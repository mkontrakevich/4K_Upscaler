# MG 4K / ARCH LOCK — deployment on kontrakevich.com

Target hostname: **4k.kontrakevich.com**

The root domain `kontrakevich.com` is intentionally kept free for a product/marketing page and billing entry point. The application itself runs on the subdomain `4k.kontrakevich.com`.

## Fast path — same Cloudflare account

This repository already contains the working Worker, Durable Object container, KV and R2 bindings used by production.

1. Make sure the DNS zone `kontrakevich.com` is active in the same Cloudflare account as the Worker.
2. Copy `wrangler.kontrakevich.com.jsonc` to a temporary local config if you do not want to replace the repository default.
3. Configure Worker secrets:
   - `PROCESSOR_SHARED_SECRET`
   - `OPENROUTER_API_KEY`
4. Deploy:
   ```
   npx wrangler deploy --config deploy/kontrakevich.com/wrangler.kontrakevich.com.jsonc
   ```
5. Set the external processor environment using `cloud-processor.env.example`.
6. Start the processor:
   ```
   docker compose --env-file deploy/kontrakevich.com/cloud-processor.env -f docker-compose.cloud.yml up -d --build
   ```
7. Verify:
   - `https://4k.kontrakevich.com/`
   - `https://4k.kontrakevich.com/api/health`

Cloudflare Custom Domains create the Worker DNS record and certificate automatically when the domain is inside an active Cloudflare zone.

## Isolated/fresh Cloudflare account

Before using the supplied Wrangler config, create:
- a KV namespace for `SESSION_STATE_R7`;
- an R2 bucket for `TEMP_BUFFER_R7`;
- the Worker secrets listed above.

Then replace the KV namespace id and R2 bucket name in `wrangler.kontrakevich.com.jsonc`.

The Durable Object/container migration is declared in Wrangler and will be provisioned during deploy.

## Security

Never place the real OpenRouter key or processor shared secret in this archive, Git, Docker Compose, screenshots or logs.

The archive contains only examples/placeholders.

## Billing

Customer billing is not enabled in this package yet. The recommended provider-independent architecture is documented in `PAYMENTS_ACCESS_ARCHITECTURE.md`.
