# MG 4K unit economics — baseline 2026-09-30

Production requests google/gemini-3-pro-image through OpenRouter Image API at 4K with one reference image.

Published Google paid rates:
- 4K image output: USD 0.24;
- reference-image input: about USD 0.0011;
- text/thinking can add variable cost.

OpenRouter Standard platform fee is 5.5% when buying credits, not a per-request inference markup.

At USD/RUB 84.1015:
- simple provider base: USD 0.2411;
- with 5.5% credit-purchase fee: about USD 0.25436;
- about **RUB 21.39 per one 4K API call**, before prompt/thinking/infrastructure/payment/tax.

If the pipeline consumes more than one provider call:
- 1.3 calls: ~RUB 27.81;
- 1.5 calls: ~RUB 32.09;
- 2.0 calls: ~RUB 42.78.

Exact cost telemetry is now recorded per job from OpenRouter response usage.cost:
provider_cost_usd, provider_request_count, provider_cost_complete, provider_cost_provider.
Missing usage.cost stays unknown, never zero.

Trial recommendation: 2 free credits after verified email.
Two free credits cost roughly RUB 42.78 at one provider call each, or ~RUB 85.57 at two calls each, before overhead.

Disabled provisional price scenarios:
- 10 credits: RUB 790
- 30 credits: RUB 1,990
- 100 credits: RUB 5,990

Reprice after at least ~30 customer-style generations using mean/p95 provider cost, calls/job, rejection/refund rate, acquiring/fiscalization and tax.

Sources:
- https://ai.google.dev/gemini-api/docs/pricing
- https://openrouter.ai/docs/guides/overview/multimodal/image-generation
- https://openrouter.ai/pricing/
