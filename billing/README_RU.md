# MG 4K — billing foundation (RU-first)

Status: **implemented foundation / live charging disabled**.

## Commercial model
- verified new account: **2 free generation credits**;
- 1 customer-started generation reserves 1 credit;
- inspection and download of an existing RESULT cost 0 credits;
- user-requested regeneration needs another credit;
- no provider request -> release reservation;
- hard system failure with no RESULT -> release reservation.

Scenario prices in config are provisional and disabled until real cost telemetry is measured.

## Payment rails
**Russia:** CloudPayments (cards, SBP, pay methods) + CloudKassir/Cloud-cheki.
**International cards:** CloudPayments foreign cards after merchant/category approval.
**Crypto:** Russian retail checkout is disabled. A nonresident foreign-trade path may be added only after legal/accounting review under 282-FZ.
**Digital ruble:** separate lawful RUB rail; not cryptocurrency.

## RU data topology
- kontrakevich.com — landing/pricing/legal;
- 4k.kontrakevich.com — image UI;
- pay.kontrakevich.com — billing/account gateway on Russian-hosted infrastructure;
- Russia-hosted PostgreSQL — primary customer identity/payment/credit ledger;
- image runtime receives only pseudonymous account ID + signed entitlement.

The current Cloudflare/OpenRouter image path must not be treated as automatically compliant for customer images containing personal data. Close the 152-FZ localization/cross-border-processing gates before public RU launch.

## Activation gates
1. Merchant form/tax regime confirmed with accountant.
2. CloudPayments merchant approved; request foreign-card acquiring.
3. CloudKassir/Cloud-cheki fiscalization approved for prepaid credits.
4. Russia-hosted PostgreSQL and billing gateway deployed.
5. Roskomnadzor processing/cross-border notifications handled as applicable.
6. Offer, privacy policy, PD consent, refund rules published.
7. Customer auth deployed.
8. Generation-credit reservation gate wired to POST /api/jobs.
9. Sandbox webhook/replay/refund tests pass.
10. Only then enable sales/prices.

No live payment is accepted by this commit.
