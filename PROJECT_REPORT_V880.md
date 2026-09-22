# MG 4K Web Review Console — V8.8.0

**Target:** continue the current `base/mg (2).jpeg` queue with existing Nano Banana Pro quality, its original sky and explicit approval of every FINAL.

**Stage:** source-sky restoration from an exact-SHA historical donor; pending verification against the full 5504 × 3072 RAW on the Windows workstation.

## Evidence and defect

- The diagnostic found eight filenames for the active source, but all eight have identical RAW SHA-256 `61bfe13a827f8ed33ae76134814fd91507bac748279b424df5d9c1f31d3d98c4`. They are one 5504 × 3072 image, 22,233,845 bytes, not independent alternatives.
- The V8.7.9 donor passes camera lock (100), source edge preservation (0.980930), hardscape preservation (0.986580) and material identity (0 substituted cells out of 20).
- It fails perimeter luminance continuity (p95 41.302254 > 32; excess fraction 0.230376 > 0.08) and one sky grid tile. The source clouds and darker sky are part of the locked scene.
- A chat image attachment was transformed to 2048 × 1143 and is not byte-identical to the original RAW. Offline verification on this transformed preview found three `structure_loss` sky cells and the same perimeter failure. Restoring the source sky alone resolved those failures, while keeping generated image content outside sky. The preview then passed the full gate after correction of a false line caused by the outermost pixel of subpixel registration. This result is **not** a claim that the full-resolution workstation RAW passes yet.

## Implementation

1. Search the same source-SHA donor folders, hash files and evaluate identical byte copies only once. Valid existing donors are preferred unchanged.
2. Attempt local sky recovery only when the complete list of failed checks is perimeter continuity plus grid corruption, every corrupt tile has only `structure_loss`, every affected tile is at least 80% source sky and the camera passes.
3. Derive the sky mask from the immutable source, including its original cloud contours. Preserve donor pixels and native generated dimensions outside sky; retain the original paid RAW unchanged.
4. Recheck the recovered candidate against **all** hard gates. Only a fully passing candidate enters the current-source canonical donor checkpoint. FINAL is copied byte-for-byte from this **corrected** donor checkpoint, and the next image remains blocked until visual approval.
5. Ignore only the outermost two pixels when searching for unsupported long border lines after subpixel camera registration. The original frame-within-frame, margin and paired-side seam regressions remain blocking.
6. A failure reports the actual rejected donor and failed checks without silently issuing an API request. No validator threshold has been loosened.

## Verification boundary

- All bundled offline tests and source-folder/queue gates run at installation, with zero paid API requests.
- The donor imported into ChatGPT was reduced to 2048 pixels by attachment handling; the original 5504-pixel RAW remains on the user's Windows PC. A real full-resolution pass occurs there when the current image is processed.
- Installation and server start do not publish a FINAL or call the generator. The current image must be started in the web interface, reviewed and explicitly approved before the queue advances.
- The original 5504-pixel donor is never overwritten or silently marked approved. If the local repair fails any gate at full resolution, FINAL remains held and the diagnostic records the reason.
