# Billing and external customer access architecture

Status: design only. No payment provider is enabled by default.

## Product model

Do not sell unlimited generation access initially. Each generated image has a real provider cost, so the safer commercial model is:

- account + authenticated session;
- subscription or prepaid package;
- included generation credits;
- one credit reservation before a paid generation starts;
- refund the reserved credit automatically if no paid generation is actually consumed;
- separate free operations such as SOURCE upload, inspection and previously generated RESULT download.

This keeps billing predictable and prevents an "unlimited" plan from becoming loss-making.

## Recommended topology

- `kontrakevich.com` — landing, pricing, terms, account entry.
- `4k.kontrakevich.com` — MG 4K application.
- Hosted checkout from the selected payment provider.
- Webhook endpoint in the Worker:
  - `POST /api/billing/webhook/<provider>`
- Customer session/account endpoints:
  - `POST /api/auth/start`
  - `POST /api/auth/verify`
  - `GET /api/account`
  - `POST /api/billing/checkout`
  - `POST /api/billing/portal`
- Generation gate:
  - protect `POST /api/jobs`;
  - validate account;
  - validate active entitlement;
  - atomically reserve one or more credits;
  - only then enqueue the generation.

## Data model

Use a dedicated persistent billing store, preferably Cloudflare D1, separate from temporary image storage.

Suggested tables:

```
users(
  id, email, created_at, status
)

billing_customers(
  user_id, provider, provider_customer_id, created_at
)

subscriptions(
  user_id, provider, provider_subscription_id,
  plan_code, status, current_period_end, updated_at
)

credit_ledger(
  id, user_id, job_id, delta, reason, created_at
)

payment_events(
  provider, event_id, event_type, payload_hash,
  processed_at, status
)
```

The credit ledger should be append-only.

## Webhook rules

- verify the provider signature before reading the event;
- make every event idempotent using `provider + event_id`;
- never trust plan or price values supplied by the browser;
- map provider price IDs to internal plan codes on the server;
- subscription cancellation removes future access only according to the paid-through date;
- chargeback/refund immediately adjusts entitlement according to your policy.

## Authentication

For public customers, use email magic-link authentication or an external identity provider.

Cloudflare Access is useful for owner/admin access but is not a replacement for public SaaS customer billing.

## Provider adapter

Keep payment logic behind a small provider interface:

```
createCheckout(user, plan)
verifyWebhook(request)
normalizeEvent(event)
createCustomerPortal(user)
```

That lets the service switch between Stripe, Paddle, Lemon Squeezy, CloudPayments or another processor without rewriting generation logic.

## Initial plans

Start with credits, not unlimited use.

Example structure:
- Trial — 1 generation after verified email.
- Starter — small monthly credit pack.
- Pro — larger monthly pack + priority queue.
- Business — larger pack, team/account limits and invoicing.

Do not set final prices until the average real generation cost, failed-generation rate, payment fees and target gross margin are measured.

## Production gates before enabling sales

1. Terms of Service.
2. Privacy Policy.
3. Refund/cancellation policy.
4. Clear description of AI processing and retention of uploaded images.
5. Account deletion.
6. Billing webhook test suite.
7. Replay/idempotency test.
8. Credit reservation/refund test.
9. Rate limiting and abuse protection.
10. Usage dashboard for customer and owner.
