# V8.8.0 — Web Review Console / Source Sky Recovery

V8.8.0 retains the V874 image-pipeline tag to preserve the user queue and
historic RAW checkpoints. A previously paid exact-source donor that differs
only in source sky may be corrected locally; the unchanged donor remains in
its historical folder. Every correction is revalidated and the corrected
donor is copied byte-for-byte into FINAL after the camera and scene gates pass.
The correction does not approve FINAL or start a paid request. Its full-size
outcome is determined on the workstation from the actual 5504-pixel RAW.

The release retains the V8.7.4 image pipeline and adds a supervised local-server bootstrap. Queue state, donor identity and all canvas-integrity rules remain compatible.

## Control boundary

- `web_console_bootstrap.py` prefers `127.0.0.1:8742` and may select `8743-8752` when the preferred port is occupied.
- `web_review_server.py` always binds to loopback only; no selected port is exposed to LAN or Internet.
- The browser opens only after `/health` confirms V8.7.9 is ready.
- A current V8.7.9 process is reused; foreign and older port owners are never terminated.
- Static HTML/CSS/JavaScript is bundled locally; no CDN or remote frontend is used.
- Every state-changing request requires a per-process random control token.
- Content Security Policy allows only same-origin scripts, styles, images and connections.
- The interface never exposes an arbitrary filesystem-path endpoint.

## Queue contract

| UI action | Command | Paid request possible |
|---|---|---:|
| Start / continue | current pipeline with `--limit 1` | No |
| Approve | explicit queue approval | No |
| Reject | explicit queue rejection | No |
| New generation | `--force-generation --donor-only --auto-generate-current` | Yes, after confirmation |
| Pause | terminates current subprocess; persistent state remains | No |

Approval automatically launches the ordinary one-image processing command for the next source. That follow-up command cannot authorize generation. If the next source needs a donor, the queue stops at `AWAITING_EXPLICIT_DONOR_GENERATION` and waits for the yellow button.

## Existing work migration

V8.7.0–V8.7.3 state is accepted and relabelled to `V874_CANVAS_INTEGRITY_GATE`. Existing approvals stay intact. A current unapproved donor is returned to `AWAITING_RAW_REVIEW`, where the new checks run locally without a paid request.

V8.7.6 deliberately keeps `V874_CANVAS_INTEGRITY_GATE` as the image-pipeline tag. The application version changes, but valid donor and queue state are not invalidated. Source admission is now shared by the web console, SAFE and GENERATIVE modes. Application directories, UI references, outputs and diagnostics cannot enter the user-image queue.

## Startup state machine

1. Scan ports 8742-8752 for an already healthy V8.7.9 server.
2. Skip any port owned by another service or older release without terminating it.
3. Start the server on the first available loopback port and capture its log.
4. Poll `/health` for up to 20 seconds.
5. Persist the verified endpoint and open the browser only after success.
6. On failure, preserve the queue and write a diagnostic package without API secrets.

## Image authority

- SAFE stays source-only and deterministic.
- GENERATIVE keeps the strict V8.7.2 contract: a technically accepted Nano Banana Pro donor is copied byte-for-byte to FINAL.
- The original source remains the camera, composition, background, material, texture and lighting-direction authority used by the technical gates.

## Canvas integrity contract

- The source photograph must fill the complete output edge to edge.
- Picture-in-picture, inset plates, inner frames, reflected margins, duplicated strips and side wedges are blocked.
- Low-frequency continuity is checked around the entire perimeter.
- Long donor-only seam lines near the canvas boundary are blocked.
- Existing source contours receive a strict and a high-density critical-region preservation check.
- Existing signs, logos, letters and numerals must retain their source shapes; unreadable glyphs may not be guessed.
