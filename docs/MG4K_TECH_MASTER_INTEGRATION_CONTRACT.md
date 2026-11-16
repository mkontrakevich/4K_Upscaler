# MG 4K — TECH MASTER integration contract

Status: production integration contract  
Product flow: `SOURCE → TECH MASTER → MG4K → INSPECTION → FINAL`

## Product principle

MG 4K increases image quality while preserving the visual identity and factual content of SOURCE.

TECH MASTER and MG4K are one user workflow but remain two independent processing engines:

- **TECH MASTER** corrects measurable capture defects without generative reconstruction.
- **MG4K** performs source-faithful generative reconstruction only after the technical source is approved.

The engines must not be merged into one opaque operation.

## Stage 1 — SOURCE

The original upload remains immutable and SHA-256 traceable.

Historical RAW/V10 assets remain immutable evidence. Generic current intake accepts the formats supported by the existing TECH MASTER endpoint; do not silently pretend an 8-bit raster source contains RAW sensor information.

## Stage 2 — TECH MASTER

Required properties:

- no HDR;
- no virtual exposures;
- no generative image creation;
- geometry/perspective corrections only where supported by measured evidence;
- technical exposure / white balance / denoise / optical correction;
- 16-bit TIFF technical master;
- SOURCE ↔ TECH visual comparison;
- explicit human QA;
- APPROVE or REWORK decision.

Generative MG4K handoff remains blocked until human QA approval.

## Stage 3 — MG4K

The approved TECH MASTER is imported as the new MG4K SOURCE only through the server-verified Editorial/Tech Master lineage contract.

Import requirements include:

- stage = `MG4K_MASTER_IMPORTED`;
- master bit depth = 16;
- locked lineage = true;
- approved master SHA-256 is verified server-side;
- mandatory scene identity locks are applied;
- import itself does not start a paid generation.

MG4K then performs:

`AI SOURCE PROFILE → lock profile → source-faithful DONOR → SOURCE FIDELITY gates`.

## Stage 4 — INSPECTION

The existing V8.8.1 visual inspection system is mandatory and must not be removed during integration:

- SIDE;
- SPLIT;
- DIFF;
- BLINK;
- synchronized zoom/pan;
- absolute difference;
- edge difference;
- heatmap;
- defect overlay.

Automatic fidelity gates do not replace human visual QA.

## Stage 5 — FINAL

FINAL requires explicit human decision.

Actions remain:

- approve;
- reject;
- regenerate;
- save RESULT;
- open RESULT/archive.

A verified accepted DONOR may become FINAL without post-acceptance SOURCE blending.

## Integration rule for MARINS unified systems

A shell, navigation, branding layer or common project dashboard may change presentation, but it must not remove or bypass any of the following:

1. immutable SOURCE lineage;
2. TECH MASTER no-HDR technical processing;
3. TECH human QA;
4. server-verified TECH → MG4K handoff;
5. AI SOURCE PROFILE and SOURCE FIDELITY;
6. full visual INSPECTION;
7. explicit FINAL human decision.

The canonical MG4K visual-review reference remains the protected reference branch:
`reference/mg4k-upscaler-golden-r10.3`.
