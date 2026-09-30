# RU legal launch checklist — 2026-09-30

Engineering checklist, not a substitute for Russian legal/accounting advice.

## Merchant/tax
Use a registered merchant suitable for scale (typically IP/OOO). NPD is only suitable while all statutory conditions/caps are met. Configure taxes/VAT from accountant-approved merchant settings; do not hard-code tax assumptions.

## 54-FZ
Connect CloudKassir or Cloud-cheki where required. Credits are prepayment for future service; confirm with accountant/cash-register provider the correct receipt flow. A common pattern is 100% prepayment at purchase and a closing/full-settlement receipt with prepayment offset when service is consumed.

## 152-FZ
Before collection, handle operator notification unless an exception applies. Primary collection/storage of Russian citizens' personal data must satisfy Russian localization requirements; assess separate cross-border notification/requirements before transfer to foreign processors. Images can themselves contain personal data.

## Crypto
Federal Law 282-FZ effective 01.09.2026 generally prohibits accepting digital currency/digital rights as payment for goods/services in Russia, with statutory exceptions including qualifying foreign-trade contracts. Therefore Russian retail crypto is disabled; any nonresident foreign-trade crypto flow needs separate legal/compliance approval. Digital ruble is separate.

Sources checked:
- https://developers.cloudpayments.ru/
- https://cloudpayments.ru/payments/foreign-cards/
- https://cloudpayments.ru/cloudkassir/
- https://www.nalog.gov.ru/new2026/
- https://www.consultant.ru/document/cons_doc_LAW_61801/
- https://base.garant.ru/414700319/
- https://cbr.ru/PSystem/dr/dr_for_business/accepting_payments_dr/
