MG 4K V8.8.1 - SOURCE SKY RECOVERY

PROJECT FOLDER: D:\Work\ИИ\MG
INSTALL AND START: 00_INSTALL_AND_START_V881.bat
NEXT STARTS: desktop shortcut "MG 4K Web Review" or 00_START_MG_WEB_REVIEW.bat
PREFERRED LOCAL ADDRESS: http://127.0.0.1:8742
AUTOMATIC FALLBACK: http://127.0.0.1:8743 through :8752

The launcher waits for a verified health response and only then opens the browser.
It never starts a paid generation.

STARTUP RECOVERY
- If V8.8.1 is already running, the launcher reopens that exact address.
- Select the source folder with "Обзор…" or paste its absolute Windows path and click "Открыть путь".
- For the 35-image queue, keep D:\Work\ИИ\MG\base selected. The app also checks
  Nano Banana Pro RAWs in the parent MG output folder without moving or paying for them.
- A parent RAW is accepted only with the exact source SHA-256 and a new technical
  whole-scene/camera check; the FINAL is still explicitly reviewed per image.
- When the only failures are source-sky contours and perimeter tone, V8.8.1
  restores the original sky locally, preserves the generated building and
  landscaping, and rechecks every mandatory gate before FINAL. The paid RAW
  is kept untouched. The corrected donor is the byte-identical FINAL master.
- A local correction is not guaranteed to pass on every image. Technical
  failures leave the image active and do not start a paid API request.
- If the Windows dialog is hidden, switch to it in the taskbar, or use the path field after the dialog closes (120-second timeout).
- Application folders, UI reference images, outputs and diagnostics are never admitted to the source queue.
- If port 8742 is occupied, it selects the first free port from 8743-8752.
- It never terminates an unrelated or older process that owns a port.
- The selected endpoint is written to WEB_CONSOLE_ENDPOINT.json.
- Startup stdout/stderr and a diagnostic JSON are saved automatically on failure.

WORKFLOW
1. Select SAFE or GENERATIVE.
2. Click "Запустить / продолжить". This action never authorizes a paid request.
3. Inspect SOURCE and RESULT side by side; the result appears after processing. Click either image for full-screen view.
4. Click "Утвердить и дальше" or "Отклонить".
5. The next source remains blocked until the current result is approved.
6. "Новая генерация" is available only when the current source needs a donor or
   is on technical hold. It always shows a separate paid-action confirmation.

SAFE
- Local LANCZOS upscale only.
- No generator, donor or API request.
- Output: 4K_SAFE_LOCAL_UPSCALE.

GENERATIVE
- Nano Banana Pro donor is the verified FINAL master.
- No source resize is substituted for an accepted donor.
- Camera, whole scene, background, material identity, texture and lighting
  direction gates remain mandatory.
- A frame inside the frame, invented side margins, duplicated border strips,
  altered signs or rewritten letters are forbidden and block FINAL.
- Output: 4K_GENERATIVE_FINAL.

PRIVACY AND SAFETY
- The web server listens only on 127.0.0.1. It is not available from LAN/Internet.
- Images are served from the same PC and are not uploaded by the interface.
- Control actions require a random token created anew at server start.
- Closing the browser does not erase the queue state. Run the launcher again to
  reopen the same unfinished image.
- An unapproved V8.7.3 donor is revalidated locally by the compatible V8.7.4
  image pipeline before reuse.
  This migration makes no API request.
