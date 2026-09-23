# Local V8.8.1 master baseline

Source archive supplied by user:
`MG_GENERATIVE_QUALITY_IMMUTABLE_ARCH_V8_8_1_SCENE_STABILITY_PROFILE.zip`

Archive SHA-256:
`528d7da5af538813f4ec1480f899310649fccd69e7d0123f7628561cdda2a6a8`

Archive size: 2,176,437 bytes
Entries: 62
Source files excluding __pycache__: 52

## Baseline identity
- application version: 8.8.1
- application release tag: V881_SCENE_STABILITY_PROFILE
- web console build: r3
- model: google/gemini-3-pro-image
- generator: Nano Banana Pro
- local web interface: true
- loopback-only local server: 127.0.0.1
- explicit paid generation confirmation: required

## Offline verification performed against the exact uploaded archive
All checks below passed with zero real API requests:
- Python compile: 21/21 modules
- version_guard.py
- source_queue_isolation_self_test.py
- source_folder_picker_self_test.py
- sibling_donor_recovery_self_test.py
- safe_local_upscale.py --self-test
- v8_safe_appearance.py --validator-self-test
- material_identity_self_test.py
- canvas_integrity_self_test.py
- source_sky_recovery_self_test.py
- dual_mode_self_test.py
- retry_budget_self_test.py
- web_review_server.py --self-test
- viewer_inspection_self_test.py
- web_console_bootstrap.py --self-test
- path_relocation_self_test.py
- web_bootstrap_integration_self_test.py
- faithful_donor_detail_carrier_self_test.py

## Cloud migration rule
The uploaded archive is the functional/UI source of truth.
Cloud support must be added as an adapter layer and must not change the local pipeline algorithms or the approved local review UI.

Important compatibility finding:
the archive's cloudflare_bridge.py sets MG4K_SOURCE and MG4K_LOCK_PROFILE_JSON,
while the archive's v8_safe_appearance.py still initializes SOURCE directly from config.json.
Therefore the archive itself is a valid local master but is not yet a complete cloud-processor build.
The cloud adapter must introduce environment overrides without changing local-default behavior.

## Security scan
No embedded OpenRouter/API credential value was detected in the extracted text files.
The only token-like occurrences are runtime/generated control/pairing token code paths, not hard-coded credentials.
