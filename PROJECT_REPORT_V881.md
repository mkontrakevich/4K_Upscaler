# MG 4K Web Review Console — V8.8.1

## Project status
- Production: https://4k-upscaler.marinsgroup.workers.dev
- Target action: continue the existing V8.8.0 pipeline with the canonical Scene Stability Profile embedded into generation and web review.
- Stage: V8.8.1 Scene Stability Profile integration.
- Progress: 100%; Cloud UI R9 deployed and regression-verified.

## Added
- Canonical `scene_stability_profile.json`.
- HARD_LOCK: CAMERA, GEOMETRY, ARCHITECTURE, TEXTURES, VEGETATION, PEOPLE / VEHICLES / SMALL_OBJECTS.
- SOFT_LOCK: LIGHTING, SKY_CLOUDS, WATER / REFLECTIONS / SHADOWS.
- Generation prompt now follows the same policy used for review.
- Web Review Console displays the full policy under SOURCE/RESULT.

## Preserved from V8.8.0
- Existing source-folder picker and persisted folder selection.
- SOURCE and RESULT previews.
- sibling exact-SHA donor recovery from `02_NANO_BANANA_PRO_RAW`.
- explicit paid-generation consent only.
- canvas-integrity, material identity and camera gates.
- local FastAPI server, diagnostics and resumable queue.

## Safety
No API request is made by installation, startup, folder selection or offline tests.


## Web inspection layer — build r3 (2026-09-22)

- Public application version remains 8.8.1; internal Web Console BUILD_ID is r3 so bootstrap will not reuse an older r2 process.
- Rejected candidates remain visible and now expose human-readable failure criteria, group, lock scope, severity, impact and recommended operator action.
- Full-screen inspection viewer adds FIT / 50% / 100% / 200% zoom, mouse-wheel zoom, drag pan and synchronized navigation.
- Comparison modes: SIDE, SPLIT with draggable divider, DIFF (absolute / edge / heatmap preview up to 2048 px) and BLINK.
- Known validator failures can expose defect overlays; perimeter and border-seam checks highlight the frame boundary. Unknown checks fall back to a safe generic explanation instead of breaking UI.
- Viewer remains read-only with respect to image processing: quality-gate decisions, one-image queue lock and explicit paid-generation consent are unchanged.
- Manual override continues to preserve automatic rejection reasons in the report.
- Offline viewer/server/bootstrap tests make 0 real API requests.
