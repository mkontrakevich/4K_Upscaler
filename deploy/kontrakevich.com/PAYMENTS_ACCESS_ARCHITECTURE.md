# Billing and external customer access architecture

Status: **billing foundation implemented; live sales disabled**.

## Model
- 2 trial credits after verified identity;
- 1 customer-started generation = 1 reserved credit;
- inspection/download existing RESULT = 0;
- no provider request or hard system failure without RESULT -> release reservation;
- user-requested regeneration -> new credit.

## RU-first topology
- kontrakevich.com — landing/pricing/legal;
- 4k.kontrakevich.com — MG 4K app;
- pay.kontrakevich.com — Russian-hosted account/billing gateway;
- Russia-hosted PostgreSQL — primary identity/payment/credit ledger;
- CloudPayments + CloudKassir/Cloud-cheki;
- Cloudflare runtime receives pseudonymous entitlement, not primary identity.

Do not use Cloudflare D1 as the primary RU personal-data database.

## International
CloudPayments foreign-card acquiring is the first international-card rail, subject to merchant/category approval. A second provider such as Stripe can be added only for a legally supported merchant entity/account.

## Crypto
Direct Russian retail crypto checkout is disabled. Architecture reserves a disabled foreign-trade adapter for qualifying nonresident contracts only after legal/accounting review under the post-01.09.2026 rules.

## Generation gate
Before public sales, POST /api/jobs must require a signed entitlement from the RU billing gateway and atomically reserve one credit. This gate is intentionally not enabled in current R9.7 production until auth, merchant onboarding, fiscalization and personal-data launch gates are complete.
