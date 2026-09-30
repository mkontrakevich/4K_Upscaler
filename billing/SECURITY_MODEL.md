# Billing security model

- Browser is untrusted: price, pack, credits and entitlement are server-authoritative.
- MG 4K must never receive/store PAN or CVC; card data stays with CloudPayments.
- CloudPayments API Secret, processor token and OpenRouter key remain server secrets.
- Verify CloudPayments POST webhook using Content-HMAC over exact raw body before parsing.
- Match InvoiceId, AccountId, amount and currency against a pending server order.
- Deduplicate Pay events by provider + TransactionId before granting credits.
- Credit ledger is append-only; refunds use compensating entries.
- Generation lifecycle: reserve -> provider call -> commit/release.
- Trial: 2 credits only after verified email, with privacy-minimized anti-abuse controls.
- Public uploads can contain personal data; enforce declared retention/deletion and cross-border processing rules before launch.
