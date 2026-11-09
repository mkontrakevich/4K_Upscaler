# Tropicana Park / DSC09818 — Technical RAW Master V10 (NO HDR)

**Решение владельца:** технический RAW мастер не должен содержать HDR, виртуальных экспозиций или генеративной дорисовки. После утверждения технического QA начинается отдельный генеративный этап.

## Назначение и текущий этап

- **Целевое действие:** подготовить технически корректный, достоверный RAW-мастер и передать его в генеративное улучшение только после визуальной приёмки.
- **Стадия:** Technical V10 / QA. Автоматическая обработка PASS; человеческая приёмка PENDING.
- **Оценка прогресса:** техническая сборка 100% по проверяемым автоматическим тестам; художественная готовность не подтверждена.
- **Контур данных:** MARINS OS, отдельный Cloudflare R2 `marins-tropicana-editorial-photo`, префикс `tropicana-park/DSC09818/technical-v10/`.

## Происхождение

- Оригинал: `marins-tropicana-photo-originals/originals/DSC09818.ARW`.
- Sony ILCE-7M4, FE 16–35mm F2.8 GM, 16 mm, 1/160 s, ISO 3200.
- Original RAW SHA-256: `1b4344f4b8ca629830254850fbcbaf2931c83c00a158ab87d84e5ee7722046d5`.
- Linear 16-bit RAW-derived TIFF, optics/PTLens at 16 mm + per-frame measured PerspectiveLock in **one** resampling stage.
- One fixed base neutral display exposure (not HDR), camera white balance, restrained chroma/luma noise reduction.
- Prohibited in TECH: HDR tone fusion, virtual exposure brackets, local HDR, generative additions, fabricated sky/city, editorial color remapping.

## Проверенные артефакты

| Artifact | Value |
| --- | --- |
| `DSC09818_TECH_LINEAR16.tif` | 6536 × 4360, uint16 RGB |
| `DSC09818_TECH_CLEAN16.tif` | 6536 × 4360, uint16 RGB, 165,561,703 bytes |
| V10 clean master SHA-256 | `de64ae82d1ef379698a9b412868e7fb3f3fe4c58c26fa8af9ccb83a17873d689` |
| `DSC09818_TECH_NEUTRAL.jpg` | RAW neutral preview, geometry-corrected |
| `DSC09818_TECH_CLEAN.jpg` | Noise-managed preview, geometry-corrected |
| Native browser display proxy | `fullres-review-de64ae82d1ef3796.jpg`, 6536 × 4360, 8bit display from TIFF |
| Full-res preview SHA-256 | `f675cf3ce8a03e9c6f382d3af73efac90af51fd32eeefd53f3876a3d3070fe92` |
| Technical QA | `DSC09818_TECH_QA.json` |

## Автоматические QA-гейты

- Original RAW/EXIF: **PASS**
- Geometric remapping/source valid coverage: **PASS**. This does not certify 100% residual straightness.
- Native 16-bit TIFF 6536 × 4360 and SHA: **PASS**
- HDR_ABSENT: **PASS** (`HDR_applied=false`, `virtual_exposures=false`, `local_HDR=false`, `adaptive_tone_recovery=false`)
- Non-generative cleanup: **PASS**
- Human visual QA: **PENDING**, without implicit approval.

## Принцип приёмки

1. Open the full-screen MG4K Split viewer with `ORIGINAL RAW / TECH V10`: original 7028 × 4688 sensor demosaic without geometry correction versus TECH V10 6536 × 4360. Original is only translated to a matching central viewport (no source geometry warp).
2. Review rails and frames, curvature, convergence, 100% details, brick/metal/glass, color noise, city lights.
3. **Only after explicit user QA approval**, create a separate `technical-v10/qa-approval.json` linked to exact RAW/master/model-input SHA and a single UUID.
4. The subsequent OpenRouter Workflow refuses any new V9 (HDR) input, unapproved V10, missing RAW source provenance, or repeated job/candidate. Candidate is always `EDITORIAL_REVIEW` not `FINAL`.
5. After generation inspect output against the exact V10 (NOT archived V9) with source-fidelity and independent geometry QA.

## Неустранимые ограничения без повторной съемки

Shadow SNR at ISO 3200 and 1/160 s. Third-party lens radial calibration may have residual moustache/decentering/TCA; manual straight-edge inspection needed. No real bracket HDR and no lost-photon recovery. The archived V9 and its historic OpenRouter 01 remain as evidence only.

**Status:** `TECH_V10_AUTOMATED_QA_PASS / HUMAN_VISUAL_QA_PENDING / GENERATIVE_BLOCKED`.
