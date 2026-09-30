# MG 4K / ARCH LOCK — Production Report

**Date:** 2026-09-28  
**Repository:** `mkontrakevich/4K_Upscaler`  
**Production:** `https://4k-upscaler.kontrakevich.workers.dev`

## Project / Target / Stage / Progress

- **Project:** MG 4K / ARCH LOCK
- **Target action:** stable cloud generation with ARCH LOCK and production-grade source replacement without accidental paid regeneration
- **Stage:** R11a live verification completed → R11b lifecycle hardening deployed → UI R9.3 replace-source verified in production
- **Current result:** scope completed
- **Progress:** 100%

## Executive result

The original production blocker `Maximum number of running container instances exceeded` did not recur in the successful R11a live generation path. R11a reached visual review and produced a real RESULT.

The subsequent review-release failure was isolated from generation: the fallback Container → Worker → R2 copy persisted the file but did not stamp `result_persisted_at`, so the Worker correctly refused to treat that review as durable. The fallback persistence path was aligned with the direct push path: SHA-256, persistence source and persistence timestamp are now recorded.

UI R9.3 with **«Заменить исходник»** is present in production. The replacement mutates the current queue slot, preserves the active ARCH LOCK profile, clears the old job/result state and does not create a new `/api/jobs` request or start paid generation. A new generation can start only after explicit operator action.

R11b hardens Container lifecycle with `tini -g` as PID 1, a new versioned Container instance and retirement of the previous R11a instance. Production health now exposes a unique Worker deployment marker and the expected processor build. A no-paid production preflight verifies this contract.

## Live production evidence

### R11a generation — capacity recovery verified

Workflow run: `36391575376`  
Production job: `20c9bd6c-a62e-46ab-85d8-e516321e4d39`

Observed:
- processor build: `result-persist-r11a`
- no `Maximum number of running container instances exceeded`
- reached `review / awaiting_approval`
- RESULT available: true
- RESULT bytes: `19,660,445`

The workflow then received HTTP 409 on `skip`. This was not a generation failure; it exposed missing durability metadata in the fallback R2 persistence path.

### Corrected fallback persistence — live durability verified

Production job: `a80ba1f1-898c-47a3-9f66-7c32232e9c69`

Observed:
- processor build: `result-persist-r11a`
- reached `review / awaiting_approval`
- RESULT bytes: `20,220,741`
- SHA-256: `a0b58967bbe598d6e5995510f099ef1afe58087e0a407fa75c9618b0d2df6094`
- persistence source: `container_pull`
- `result_persisted_at`: `2026-09-28T08:06:57.256Z`
- result sync warning: none
- generation error: none

This proves the corrected fallback path writes durable review metadata to R2. The workflow intentionally did not proceed to another paid generation after this evidence was obtained.

## Changes

### Durable RESULT metadata
Commit: `b22fbab5aa423d433bd151aa754b0013bdc57248`

Fallback `storeContainerResult` now:
- calculates SHA-256;
- stores `sha256` and `persist_source=container_pull` in R2 metadata;
- sets `result_checksum_sha256`;
- sets `result_persist_source`;
- sets `result_persisted_at`.

### Worker durability marker
Commit: `ddfe1823c1154f211853a99dcb796514f9682c1f`

Added an explicit Worker build marker to production health.

### R11b Container lifecycle
Commit: `a902069b1420625e2b7244b93bb7ac0477c6fc86`

- installs `tini`;
- uses `ENTRYPOINT ["/usr/bin/tini", "-g", "--"]`;
- processor build: `result-persist-r11b`;
- instance: `primary-result-persist-r11b`;
- previous R11a instance retained in the legacy retirement list;
- no visual/ARCH LOCK/generation policy was changed.

### Deployment/preflight hardening
Commit: `8287b776928c3eedb1250ee8a61b678fa892da24`

- unique production Worker marker: `pid1-tini-r11b`;
- health exposes expected processor build and instance;
- `.dockerignore` excludes workflow/report/TEREM-only files from the processor image context;
- corrected stale R10b regression assertion;
- added a no-paid production preflight;
- live paid smoke remains opt-in only through `[live-r11b]`.

## Production verification

Workflow run: `36395902666`

Results:
- **regression:** success
- **production_preflight:** success
- **live_r11b_smoke:** skipped

Production preflight required three consecutive valid responses confirming:
- `CLOUD UI R9.3`;
- `action-replace-source` is present;
- Worker build `pid1-tini-r11b`;
- expected processor build `result-persist-r11b`;
- expected processor instance `primary-result-persist-r11b`;
- lifecycle contract `tini-pid1-group-forwarding`.

No paid generation was started by this final deployment verification.

## «Заменить исходник» production contract

Verified in code regression and public production UI:

1. The current queue position is replaced, not appended.
2. The selected file becomes the new SOURCE.
3. Existing ARCH LOCK settings are preserved.
4. Old job/result/progress/error state is detached from the replacement.
5. Existing review/job state is reset through the recovery path where applicable.
6. Replacement itself does not call `/api/jobs`.
7. Replacement itself does not call `prepareJob()`.
8. Paid generation does not start automatically.
9. A new paid generation requires a separate explicit operator launch.

## Risk status

Closed:
- stale Container occupying the single instance slot;
- duplicate automatic paid redispatch;
- fallback RESULT stored without durability timestamp;
- workflow confusing a durable RESULT with a non-durable review;
- source replacement accidentally appending a new queue item;
- source replacement accidentally starting paid generation;
- PID 1 process lifecycle without an init/signal-forwarding layer.

Controlled:
- only one Container instance remains configured;
- old versioned instances are explicitly retired;
- production Worker declares the processor build it will enforce before dispatch;
- live paid smoke is opt-in, not executed on ordinary commits.

## Final state

**MG 4K / ARCH LOCK: production scope R11a/R11b + UI R9.3 is closed.**

The next real operator generation will be handled by the R11b Worker contract, which enforces the expected processor build before source dispatch. No additional paid smoke is required for the completed scope.


## Post-verification hotfix — Cloud UI R9.4 inspection viewer

After the R11b/R9.3 production baseline was closed, a real operator review exposed a remaining UI defect: the inspection dialog opened, but SOURCE and RESULT could disappear after the viewer switched from the native-image fallback to the canvas renderer.

The production hotfix changes the viewer contract as follows:

- Cloud UI version advanced to `R9.4`;
- the viewer now keeps the native SOURCE/RESULT fallback active until a canvas frame has actually completed;
- any canvas render exception leaves the native two-pane comparison visible instead of producing a blank inspection area;
- the inspection SOURCE first uses the persisted job source endpoint, with the already loaded page image and local object URL as fallback candidates;
- the inspection RESULT first reuses the already loaded page result and only then performs a fresh result request;
- the blank-viewer regression guard now verifies that fallback removal happens only after a completed paint;
- no paid generation is triggered by this hotfix.

Production verification:
- fix commit: `9be52a800a3100f47ed3655809afbe14f53ae3a8`;
- regression-guard correction: `11e5d6f0c8c47c5f3c663c162791b962e285c727`;
- workflow run: `36400176218`;
- `regression`: **success**;
- `production_preflight`: **success**;
- `live_r11b_smoke`: **skipped**.

Current production UI contract: **CORE 8.8.1 R4 · CLOUD UI R9.4** with processor contract **result-persist-r11b**.


## Inspection parity migration — Cloud UI R9.5

The operator supplied the last known-good local package `MG_GENERATIVE_QUALITY_IMMUTABLE_ARCH_V8_8_1_SCENE_STABILITY_PROFILE.zip`. Its `viewer_inspection_self_test.py` passed before migration, and the cloud inspection layer was rebuilt from the same local V8.8.1 mechanics rather than patched further.

R9.5 inspection contract:
- full-screen local inspection layout;
- independent native-dimension rendering for SOURCE and RESULT in SIDE mode;
- normalized common reference canvas for SPLIT / DIFF / BLINK;
- local FIT / 50% / 100% / 200% zoom semantics;
- synchronized or independent pan;
- draggable SPLIT divider rendered by the canvas;
- ABS / EDGE / HEATMAP diff pipeline using the local Sobel/difference implementation;
- BLINK timer behavior from the local build;
- local keyboard controls and RESET behavior;
- cloud-only adaptation is limited to acquiring SOURCE/RESULT from the current job and mapping ARCH LOCK failure information to the defect overlay.

The previous R9.4 native-image fallback renderer is intentionally removed so that every inspection mode uses one consistent rendering engine.

No Nano Banana generation is started by this migration.


## Canonical production URL correction

The canonical production endpoint is `https://4k-upscaler.kontrakevich.workers.dev/`. Earlier references to `4k-upscaler.marinsgroup.workers.dev` were stale project metadata and have been corrected across CI, reports, bridge defaults, environment examples, and Docker Compose defaults.


## RESULT download / save — Cloud UI R9.6

A dedicated **«Сохранить результат»** action was added to the review console.

Contract:
- enabled whenever a real RESULT is available, including rejected ARCH LOCK candidates, skipped results and approved FINAL;
- downloads the existing stored RESULT only; it never creates a new job and never calls Nano Banana;
- preserves the returned binary content without re-encoding;
- creates a readable filename based on SOURCE name + result state + short job id;
- logs successful saves and download errors in the process log;
- remains independent from approval/rejection/regeneration decisions.

Canonical production: `https://4k-upscaler.kontrakevich.workers.dev/`.


## Double-click image preview — Cloud UI R9.7

A dedicated full-screen image preview was added to the main SOURCE / RESULT cards.

Contract:
- double-click SOURCE opens the current source image in a full-screen native-image dialog;
- double-click RESULT opens the current generated result, including review/rejected/final states when displayed;
- double-click inside the preview toggles FIT ↔ native 100% size;
- Esc or the close button exits preview;
- preview reuses already displayed image URLs and does not create jobs, call Nano Banana, or change ARCH LOCK decisions;
- the existing local V8.8.1 inspection engine remains unchanged.

Canonical production: `https://4k-upscaler.kontrakevich.workers.dev/`.


## Final project closure — READY

**Date:** 2026-09-29  
**Project:** MG 4K / ARCH LOCK  
**State:** **DONE / PRODUCTION READY**  
**Production:** `https://4k-upscaler.kontrakevich.workers.dev/`  
**Repository:** `https://github.com/mkontrakevich/4K_Upscaler`  
**Final UI:** **CORE 8.8.1 R4 · CLOUD UI R9.7**  
**Processor:** `result-persist-r11b`

Accepted production scope:
- stable cloud generation and persisted RESULT;
- strict ARCH LOCK review workflow;
- proven local V8.8.1 inspection mechanics in cloud;
- SIDE / SPLIT / DIFF / BLINK comparison modes;
- safe RESULT saving without a new paid generation;
- SOURCE replacement while preserving LOCK settings;
- full-screen SOURCE / RESULT preview by double click;
- diagnostics, recovery controls and regression guards.

The project is now considered complete. Subsequent changes are maintenance or explicitly requested feature additions.


## Commercial extension — R9.8

**Status:** implemented foundation; live charging intentionally disabled.

Implemented:
- account/credit shell in Cloud UI R9.8;
- 2 free credits after verified account;
- one-credit reservation before a commercial generation;
- signed generation permit checked by the Worker;
- atomic reservation claim in the RU billing gateway before paid processing;
- idempotent commit/release settlement after provider usage/result state is known;
- exact OpenRouter cost telemetry from `usage.cost`;
- CloudPayments checkout + signed webhook validation foundation;
- Russia-hosted PostgreSQL schema for identities, orders, immutable credit ledger, reservations and generation costs;
- sandbox mode for end-to-end testing without real money;
- billing remains OFF by default on the current production endpoint.

Current production verification:
- R9.8 UI: passed;
- billing config on workers.dev: `mode=off`, `enabled=false`;
- regression: passed;
- production preflight: passed;
- live paid smoke: skipped.

Commercial activation still requires the RU-hosted billing server/database, CloudPayments merchant credentials, fiscalization and legal/accounting launch checks.
