# MG 4K Cloud Processor — V8.8.1

This layer moves the existing ARCH LOCK / Nano Banana processor off a workstation and into a long-running Docker container.

## What remains unchanged

- V8.8.1 processing pipeline
- local defaults when MG4K cloud environment variables are absent
- OpenRouter/Nano Banana Pro model selection from config.json
- ARCH LOCK validation and explicit SOURCE/RESULT approval flow
- ephemeral per-job local working directories
- deletion of the local job directory after completion

## Cloud-only adapter

The container receives:
- MG4K_CLOUD_URL
- MG4K_PROCESSOR_TOKEN
- OPENROUTER_API_KEY

MG4K_PROCESSOR_TOKEN must match the Cloudflare Worker secret PROCESSOR_SHARED_SECRET.
If MG4K_PROCESSOR_TOKEN is absent, the legacy interactive pairing flow remains available outside headless production.

## No persistent image storage in the processor

/runtime and /tmp/mg4k-jobs are expected to be ephemeral.
Cloudflare R2 remains the temporary source/result buffer.
The processor deletes each local job folder in a finally block.

## Local smoke build

docker build -t mg4k-cloud-processor:8.8.1 .

No paid generation is performed during image build.
