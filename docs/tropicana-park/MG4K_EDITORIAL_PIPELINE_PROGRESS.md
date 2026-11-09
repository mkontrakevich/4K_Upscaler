# MG 4K / Тропикана-парк — Editorial Photo / Tech Master → QA → MG4K
**Updated:** 2026-11-09  
**Goal:** For every uploaded photo, show truthful pipeline states, permit explicit technical QA approval and download, then copy the verified 16-bit master into the MG4K artistic workflow **with immutable scene locks and no automatic paid generation**.  
**Stage:** PR #45, **draft** (not deployed). **Estimated functional implementation: ~55%**; estimate, not production acceptance.

## Verified original defect
1. `site/editorial-photo.html` hardcoded `DSC09818` and used historical Workflow manifests.
2. `POST /api/editorial/upload` saved JPEG/PNG/WebP to an isolated R2 bucket but created no progress-tracked job or technical processing run.
3. The existing general MG4K API does not share the Editorial job state. Reusing its percent would be misleading.
4. DSC09818 V9 is HDR history; V10 is the valid non-HDR master and remains immutable. Historical jobs are separate from a new upload.

## Changes in draft PR #45
- On upload: per-photo UUID, SHA-256 source, authenticated durable R2 job record, `AWAITING_TECH_PROCESSING` without a synthetic percentage.
- Persistent read-only status API, polling UI and local authorized job token for recovering QA controls after the short MG4K session expires.
- Human `APPROVED/REJECTED` decisions, one-time R2 conditional write; no double approvals.
- QA gated download of a **real 16-bit TIFF** (not browser JPEG), protected by native R2 checksum and metadata.
- Approved TIFF streaming cross-bucket copy into MG4K R2 using a **native SHA-256 integrity check**; transfer is idempotent and no generation is launched.
- Read-only imported-master inventory inside main MG4K interface. MG4K-compatible HARD locks accompany the immutable input.
- Portable raster Tech Master script for new JPEG/PNG/WebP: 16-bit TIFF, full-res 8-bit preview and QA manifest. Explicitly records that converting an 8-bit photo to a 16-bit TIFF does NOT recover sensor precision, RAW information, or missing detail.
- CI unit and synthetic image tests plus regular regression guard.

## Data contract
`TROPICANA_EDITORIAL_R2/tropicana-park/editorial-jobs/{uuid}/`
- `job.json`: owner + source lineage, no cleartext access token.
- `technical/master16.tif`: binary output, independent R2 checksum.
- `technical/qa.json`: source SHA, master SHA/size, true 16-bit, HDR false, technical QA.
- `technical/decision.json`: explicit human QA.
- `handoff.json`: immutable release and lock contract.

`TEMP_BUFFER_R7/editorial-imports/{uuid}/approved-master16.tif`: independent MG4K R2 copy; does not overwrite originals.

## Gates
1. **Tech Master: HDR prohibited.** No virtual exposures, bracket fusion, local HDR, invented materials, or generative processing.
2. **Technical proof:** source SHA; 16-bit TIFF; native R2 checksum; geometry/material/source continuity documented.
3. **Human visual QA:** real approval or rejection by authorized operator; re-approval/change requires a new version.
4. **MG4K transfer:** byte-verified streaming copy, same SHA, immutable lock manifest.
5. **Generative stage:** requires separately implemented explicit launch and credit authorization; current transfer **does not** launch it.
6. **Postgenerative QA:** source fidelity, geometry, materials, architectural identity and output-specific locks; HOLD if uncertain.

## Outstanding / blocker for release
- **P0:** connect the new generic technical processor to the uploaded job with a durable asynchronous Cloudflare execution flow. The script exists but is **not yet wired to the public upload**.
- **P0:** technically grounded geometry correction / Perspective Lock for arbitrary uploaded images, with human quality validation.
- **P0:** MG4K generative input adapter must derive a bounded model-compatible preview from the approved master, read and enforce immutable source/lock SHA on the server, and only then permit a separately authorized paid action. The imported TIFF is not automatically sent to any model.
- **P0:** production staging E2E with a real uploaded photo and exported 16-bit file; negative test for session authorization, corrupted content and retry.
- Legacy DSC09818 V9/V10 + Editorial 01 must remain untouched; no automatic deployment/merge.

**Decision:** KEEP PR DRAFT until technical processing, controlled artistic launch, and end-to-end acceptance pass. No fabricated READY status.
