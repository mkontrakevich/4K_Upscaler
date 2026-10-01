# MG 4K / ARCH LOCK

Status: **READY / PRODUCTION**

CORE **8.8.1 R4** · CLOUD UI **R10.2** · processor **result-persist-r11b**

Production: https://4k-upscaler.kontrakevich.workers.dev/

Repository: https://github.com/mkontrakevich/4K_Upscaler

Ready scope:
- generative 4K enhancement with ARCH LOCK;
- SOURCE ↔ RESULT inspection using the proven local V8.8.1 viewer mechanics;
- SIDE / SPLIT / DIFF / BLINK;
- RESULT persistence and safe download;
- rejected-candidate review;
- source replacement without paid regeneration;
- double-click full-screen SOURCE / RESULT preview;
- editable exact generator prompt shown before launch;
- LOCK buttons rebuild the prompt in sync mode;
- user-created prompt presets can be saved, applied and deleted from the control panel;
- user-created additive LOCK buttons can be built directly from prompt fragments, toggled on/off, edited, overwritten and deleted;
- AI scene analysis inspects each newly loaded SOURCE and proposes only the relevant LOCK controls with conservative HARD/SOFT/FREE defaults;
- the full universal LOCK library remains available behind «Все настройки», but hidden irrelevant controls do not enter FINAL PROMPT unless the operator changes them;
- **Prompt AI Assistant** accepts the operator's natural-language goal, uses current scene analysis + LOCK + ADDITIONS + FINAL PROMPT, and returns precise prompt wording plus recommended LOCK changes;
- architecture quick commands include vertical/horizontal straightening, facade preservation, signage preservation, people, sky, evening light and premium-hotel styling;
- ADDITIONS are merged into the visible FINAL PROMPT, while the parameter LOCK profile remains higher priority on conflicts;
- process diagnostics and regression protection.

Project state: **core product done / production ready**.

Commercial extension:
- account and credit UI is implemented in R9.8;
- RU billing gateway + PostgreSQL ledger are included;
- 2-credit trial, credit reservation/settlement and CloudPayments checkout/webhook foundation are implemented;
- current workers.dev production keeps billing safely OFF until the RU-hosted billing gateway, merchant account and fiscalization are configured.
