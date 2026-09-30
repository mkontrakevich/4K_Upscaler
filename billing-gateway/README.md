# MG 4K Billing Gateway

Russia-first account, credit and payment service for MG 4K.

## Modes

- `sandbox` — real PostgreSQL ledger and full credit lifecycle, but no real money. Magic-link token is returned to the UI and payment completion is simulated.
- `live` — email delivery webhook + CloudPayments Widget/Pay webhook.

The image Worker remains unchanged when `BILLING_MODE=off`.

## Required environment

```
DATABASE_URL=postgres://...
BILLING_MODE=sandbox
PUBLIC_BASE_URL=https://pay.kontrakevich.com
APP_ORIGIN=https://4k.kontrakevich.com
CORS_ORIGIN=https://4k.kontrakevich.com
BILLING_SESSION_HMAC_SECRET=...
BILLING_PERMIT_HMAC_SECRET=...
BILLING_WORKER_SHARED_SECRET=...
CLOUDPAYMENTS_PUBLIC_TERMINAL_ID=...
CLOUDPAYMENTS_API_SECRET=...
EMAIL_DELIVERY_WEBHOOK_URL=...
```

For a public RU launch, deploy this gateway and PostgreSQL on Russian-hosted infrastructure. Do not deploy the primary identity/payment database outside Russia.

Apply `billing/schema.postgresql.sql` before starting the gateway.
