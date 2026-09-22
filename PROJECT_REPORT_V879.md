# MG 4K — Project Report V8.7.9

## Project status

- **Target action:** operate SAFE and GENERATIVE queues through a local web interface.
- **Implementation stage:** V8.7.9 sibling donor recovery and one-image visual review.
- **Primary goal:** inspect every result before the next image while preserving resumability and explicit paid-generation consent.
- **Project directory:** `D:\Work\ИИ\MG`.

## V8.7.9 sibling donor recovery (21 September 2026)

A new screenshot shows two distinct queues: selecting `D:\Work\ИИ\MG\base` finds 35 source images but no RAW for the active `mg (2).jpeg`; selecting `D:\Work\ИИ\MG` finds 71 images and returns `QUEUE COMPLETE`. The version 8.7.8 lookup only searched the output under the selected source root, so its base queue could not discover historical RAWs in the project-root output.

V8.7.9 keeps the base queue and its approval ledger, but also checks the one known parent output root `D:\Work\ИИ\MG\GEN_QUALITY_ARCH_LOCK_V8_4_WHOLE_SCENE\02_NANO_BANANA_PRO_RAW`. It reads only eligible Nano Banana Pro RAW checkpoints; a donor must bear the exact 16-character SHA-256 token of the current source and pass the current whole-scene/camera gate. The accepted donor is locally copied into the base output checkpoint and remains subject to explicit visual approval. No paid API request is made by installation, lookup, or normal processing.

The V8.7.9 installer reads the selected folder from a prior V8.7.8 config. If that selection was switched to the completed project root but an active base queue exists, it recovers `base` as the active source root. It does not alter the root approval ledger or original source bytes. Users should confirm the active file is `mg (2).jpeg` before starting processing. The donor's exact match on the user's Windows PC has not yet been established because the actual donor files were not provided.

## V8.7.8 preview visibility and folder recovery (21 September 2026)

The source/result preview panels were overlaid by `.empty { display:grid }` after JavaScript set the HTML `hidden` attribute. Author CSS overrode the browser's default hidden style. `.empty[hidden] { display:none }` now removes the overlay. The image load/error handlers show either the picture or a specific loading failure. The result panel clearly says when the result has not been created yet.

The previous Windows Shell folder chooser ran in a blocking HTTP request with owner handle 0. On the affected PC, the chooser remained open or hidden and the next request reported “Окно выбора папки уже открыто”. V8.7.8 gives the chooser an owned topmost Windows Forms taskbar window and a 120-second timeout. The browser disables repeat clicks during selection. A direct absolute path input and `Открыть путь` action use the same source queue and require no Windows dialog. They do not process an image or authorize generation.

The actual pipeline remains: select source folder → choose SAFE or GENERATIVE → process one image → compare source/result when a candidate exists → approve or reject → continue on the next image. SAFE is local enlargement. GENERATIVE checks and reuses existing Nano Banana Pro donor; a new paid generation requires its own explicit confirmation. A technical rejection holds the current image. No operation in this release changes the image pipeline, donor checks, approval state or source bytes.

Verification: syntax checks for JavaScript/Python; Python version guard; web/bootstrap self-tests; a valid source/result image fixture providing two image URLs and distinct dimensions; CSS hidden check; rebind to another source folder by entering the path, with zero generation requests. The Windows chooser cannot be exercised from this Linux build environment and still needs confirmation on the user's Windows PC.

## V8.7.5 startup incident analysis

On 17 September 2026 the installed console opened `127.0.0.1:8742` as an unavailable page and displayed a Windows error dialog pointing to `WEB_DIAGNOSTIC_20260917_134606_217996.json`. The screenshot proves that the web process did not become healthy; it does not indicate an image-generation failure, and no paid generation was authorized.

The V8.7.4 launcher had two architectural weaknesses: it depended on one fixed port and opened the browser on a timer instead of waiting for a successful health response. Therefore a port collision, delayed dependency initialization or early server exit could expose a dead page even though the image queue remained valid.

## V8.7.5 corrective action

V8.7.5 introduces a standard-library bootstrap in front of the FastAPI process. It scans `8742-8752`, reuses an already healthy V8.7.5 instance, skips occupied ports without killing their owners, launches the server on the first safe port, captures stdout/stderr, and polls `/health` for up to 20 seconds. The browser opens only after the reported application version and loopback-only status are verified.

The selected address is recorded in `WEB_CONSOLE_ENDPOINT.json`. Any failure creates a `WEB_BOOTSTRAP_DIAGNOSTIC_*.json` plus the corresponding startup log. Neither file includes API secrets, and the bootstrap command contains no generation flags.

The image pipeline intentionally remains tagged `V874_CANVAS_INTEGRITY_GATE`; changing the Windows launcher must not invalidate a valid donor, approval ledger or unfinished queue position.

## V8.7.4 incident analysis

The reviewed output was unacceptable: the generator returned a visually incoherent canvas with frame-like edge discontinuities and altered signage, yet the V8.7.3 average whole-scene metrics permitted the RAW to reach FINAL. Byte-identical RAW-to-FINAL publication worked as designed; the failure was earlier, in donor admission.

The previous seam metric measured a global illumination derivative and was too weak against narrow but long defects near the perimeter. Global edge averages also allowed a locally damaged border or sign to be diluted by the large correct central area.

## Corrective action

V8.7.4 adds a separate mandatory canvas-integrity group. It measures strict source-edge preservation, critical dense-structure preservation, low-frequency perimeter continuity, long unsupported border seams and paired inset side seams. Any failed item produces `REJECTED_CANVAS_INTEGRITY`; RAW cannot enter FINAL.

The generator instruction now explicitly forbids treating the source as a layer, framed plate, picture-in-picture image or outpainted composition. The former cinematic wording was removed. Exact existing text and signage shapes are locked; the model is told to preserve unresolved glyph pixels rather than guess letters.

The V8.7.3 active unapproved item is invalidated for free local revalidation. Approved historical work remains untouched, and no paid generation is triggered by installation or migration.

## Previous interface analysis

The V8.7.2 processing behavior was working, but the Tkinter desktop shell constrained layout and made browser-based access impossible. The replacement must not weaken the safeguards that were added during earlier donor/final corrections. In particular, a visually convenient UI must not silently regenerate images, bypass the one-image gate, or replace a verified donor with a resized source.

## Implemented solution

V8.7.5 retains the FastAPI/uvicorn server and responsive dark review panel introduced in V8.7.3, including local image previews, full-screen inspection, current-file progress, filtered logs and explicit confirmation dialogs. The V8.7.4 canvas-integrity admission gate remains mandatory before any donor can become FINAL.

The server is a controller around the existing scripts; it does not duplicate image-processing logic. SAFE and GENERATIVE continue using separate output folders and state ledgers. Each subprocess is limited to one image. Approval may automatically prepare the next image, but paid generation can begin only through the dedicated action with `paid_confirmed=true`.

## Resumption behavior

Queue state remains on disk under the existing `_diagnostics` folder. Closing the browser or restarting Windows does not approve, skip or discard the active image. Starting the launcher again opens the current state. An incomplete image remains ahead of every later image.

## Security and privacy

- Loopback binding: `127.0.0.1`, not `0.0.0.0`.
- No cloud frontend and no image upload from the web console.
- Random control token for all API actions and image URLs.
- No CORS exposure; restrictive CSP, frame denial and no-store caching.
- Diagnostics omit API secrets.

## Compatibility

The installer targets `D:\Work\ИИ`, verifies that `D:\Work\ИИ\MG` exists, installs into a versioned sibling folder, creates a desktop shortcut and starts the supervised bootstrap. Paths containing Cyrillic characters are formed safely inside PowerShell; the BAT files contain only ASCII control text to avoid the earlier command-parsing failure.

## Quality contract retained

- Nano Banana Pro: `google/gemini-3-pro-image`.
- Accepted GENERATIVE RAW is the FINAL master; no post-gate resize, blend or re-encode.
- Source composition, background, materials, texture class and lighting direction remain mandatory validators.
- Every result requires explicit human approval before later images proceed.
- Installer and ordinary start make zero paid API requests.

## Verification result

- Python source and JSON identity checks: passed.
- SAFE and GENERATIVE isolation: passed.
- Canvas-integrity and material-identity regression: passed.
- Retry/resumption and `D:\Work\ИИ\MG` relocation: passed.
- Dynamic port selection and occupied-port detection: passed.
- Live loopback HTTP health and control-token checks: passed.
- Real API requests during verification: `0`.

## V8.7.6 source-queue incident analysis — 18 September 2026

The console selected `D:\Work\ИИ\MG\MG_GENERATIVE_QUALITY_IMMUTABLE_ARCH_V8_7_3_LOCAL_WEB_REVIEW_CONSOLE\UI_REFERENCE_APPROVED_V873.png` as the current image. This was an internal interface reference stored inside an old unpacked application directory, not a user source. The earlier recursive scanner excluded known output folders but did not exclude versioned application directories or UI-reference filenames. The process stopped before generation with exit code 5, so no paid API request was made.

V8.7.6 introduces one shared source-admission policy for the web console, SAFE mode and GENERATIVE mode. It rejects every directory beginning with `MG_GENERATIVE_QUALITY_IMMUTABLE_ARCH_`, every `UI_REFERENCE_*` image, all output trees, virtual environments and diagnostic directories. Normal nested user folders such as `base` remain supported.

If the persisted queue currently points to an excluded service image, the state is repaired automatically: the rejected service record is preserved in an audit list, the false active item is cleared, and processing resumes from the first real unfinished user image. Existing approved user images, valid donors and the V8.7.4 image pipeline are not invalidated. This recovery performs zero API requests.

## V8.7.8 web source-folder selection — 18 September 2026

The web interface now contains a permanent `ПАПКА ИСХОДНИКОВ` panel and a `Выбрать папку` action. The action opens the native Windows folder dialog on the workstation running the local server. The chosen absolute path is validated, displayed in the interface and persisted to `config.json`; manual configuration editing is no longer required.

Folder selection is a control-plane action only. It does not start SAFE processing, Nano Banana Pro, a paid API request or automatic queue advancement. Switching is blocked while an image process is running. Empty folders are rejected with a visible error and a diagnostic package. Cyrillic names and spaces are supported.

Each selected folder owns its queue state inside its own output tree. Switching to another folder does not approve, reject or delete the unfinished state of the previous folder. Returning to that folder resumes its own first unfinished image. The shared V8.7.6 source-admission policy continues to exclude application builds, UI references, outputs and diagnostics from every selected folder.
