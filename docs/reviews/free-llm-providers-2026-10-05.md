# Free LLM providers to replace Groq (second slot after Gemini), 2026-10-05

Research only, no code changed. Requirements: free with **no credit card**, JSON output (`json_object` or `json_schema`), OpenAI-compatible `POST /chat/completions` (our adapter in `backend/app/llm/providers.py` is a plain httpx POST), works from a cloud server IP, and acceptable data terms. We only send `PublicFacts`, so training on inputs is acceptable, but it is noted below.

## Recommendation

**Mistral La Plateforme / Studio, Free mode**, base URL `https://api.mistral.ai/v1`, default model **`mistral-small-latest`** (use `mistral-large-latest` only for the rare long "committee" style calls).

Why:
- The official docs say "API access is enabled by default with no credit card required" ([Mistral docs](https://docs.mistral.ai/getting-started/quickstarts/studio/activate-and-generate-api-key)).
- It is OpenAI-shaped: `POST /v1/chat/completions` with a Bearer key, and `response_format` accepts `json_object` **and** `json_schema` (schema-enforced) ([API ref](https://docs.mistral.ai/api), [JSON mode](https://docs.mistral.ai/capabilities/structured_output/json_mode), [custom structured outputs](https://docs.mistral.ai/studio-api/conversations/structured-output/custom)). The Groq adapter's body (`model`, `messages`, `response_format`) should port almost unchanged. Drop Groq-only fields such as `reasoning_effort`.
- It has a large token budget compared with the other no-card options (see the limits row).
- It is an EU company with no region lock on the API that we know of. The API is designed for server use, so it works from a cloud IP.

Things to watch:
- Mistral does **not** publish the numbers for Free mode. The docs say only that Free mode "has the lowest limits, intended for evaluation and prototyping" and point to the console Limits page ([usage & limits](https://docs.mistral.ai/admin/billing-usage/usage-limits), [rate-limit help](https://help.mistral.ai/en/articles/698531-why-am-i-hitting-api-rate-limits-and-how-do-i-increase-them)). Third-party trackers report **about 1 request/s, 500K tokens/min and 1B tokens/month per model** ([free-llm.com](https://free-llm.com/provider/mistral-ai), [freellm.net](https://freellm.net/providers/mistral-ai)). **Not verified officially.** After signup, read the real values in the Admin → Limits page and put them in config.
- Signup reportedly needs **SMS phone verification** (third-party reports only, e.g. [free-llm.com](https://free-llm.com/provider/mistral-ai)). Israeli numbers are not reported as blocked.
- Training: on free plans Mistral "may use your data (input and output) to train" its models, with opt-out available ([help 347617](https://help.mistral.ai/en/articles/347617-do-you-use-my-user-data-to-train-your-artificial-intelligence-models)). For the API, opt out in Admin → Privacy → "Anonymous improvement data" ([help 455207](https://help.mistral.ai/en/articles/455207-can-i-opt-out-of-my-input-or-output-data-being-used-for-training)). This is acceptable for PublicFacts, but opting out costs nothing.
- Rate handling: about 1 RPS means requests must be serialised. The current Groq token-bucket precheck can be reused with an RPS gate. Mistral returns 429 when the limit is hit.

## Top 3 ranked

| # | Provider | Free limits (source) | JSON support | Data/terms | Signup friction | Base URL / default model |
|---|---|---|---|---|---|---|
| 1 | **Mistral Free mode** | Not published officially (console only) ([docs](https://docs.mistral.ai/admin/billing-usage/usage-limits)). Third parties report about 1 RPS, 500K TPM, 1B tokens/month per model ([free-llm.com](https://free-llm.com/provider/mistral-ai)) | `json_object` + `json_schema` ([docs](https://docs.mistral.ai/capabilities/structured_output/json_mode)) | Free-plan data may be used for training; opt-out toggle ([help](https://help.mistral.ai/en/articles/455207-can-i-opt-out-of-my-input-or-output-data-being-used-for-training)) | No card ([docs](https://docs.mistral.ai/getting-started/quickstarts/studio/activate-and-generate-api-key)); phone SMS reportedly required | `https://api.mistral.ai/v1` / `mistral-small-latest` |
| 2 | **Cloudflare Workers AI (free plan)** | 10,000 Neurons/day, reset 00:00 UTC ([pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/)); 300 req/min for text generation ([limits](https://developers.cloudflare.com/workers-ai/platform/limits/)). Llama-3.3-70B-fp8-fast costs 26,668 neurons per 1M input and 204,805 per 1M output, so about **80 calls/day** at 1.5K in / 0.4K out. Llama-3.1-8B gives about 5x more calls | `response_format` with JSON Schema, OpenAI style, but "can't guarantee" schema adherence; only 6 models (Llama 3.3-70B, 3.1-8B, ...) ([JSON mode](https://developers.cloudflare.com/workers-ai/features/json-mode/)) | Cloudflare states it does not train on customer inputs (standard Workers AI terms; not re-verified today) | Cloudflare free account, no card for the free allocation (pricing page does not ask for one; "some models require a paid billing method") | `https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1` ([OpenAI compat](https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/)) / `@cf/meta/llama-3.3-70b-instruct-fp8-fast`. Needs an account id in the URL plus an API token |
| 3 | **OpenRouter `:free` models** | 20 RPM; **50 requests/day** unless you have bought at least 10 credits in total, then 1000/day ([limits](https://openrouter.ai/docs/api-reference/limits)) | `json_schema` on select models/endpoints only; set `provider.require_parameters: true` ([structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs)) | Free models often route to providers that log or train. A separate account setting for free models has to allow them ([privacy](https://openrouter.ai/docs/guides/privacy/provider-logging)) | No card for 50/day; email signup | `https://openrouter.ai/api/v1`. The model id changes often: today the free models with structured outputs are small or niche (e.g. `inclusionai/ling-3.1-flash`) ([models](https://openrouter.ai/models?max_price=0&supported_parameters=structured_outputs)) |

## Excluded (checked today)

| Provider | Why excluded | Source |
|---|---|---|
| Cerebras Inference | Free trial now **requires a verified payment method**: $5 credits, expire after 30 days, 5 RPM / 1M TPD | [rate limits](https://inference-docs.cerebras.ai/support/rate-limits) |
| SambaNova Cloud | "Add a payment method and purchase credits to run your first requests" | [plans](https://cloud.sambanova.ai/plans) |
| GitHub Models | **Fully retired 2026-07-30**, inference API gone | [GitHub docs](https://docs.github.com/en/github-models/use-github-models/prototyping-with-ai-models) |
| Hugging Face Inference Providers | Free users get only **$0.10/month** in credits | [pricing](https://huggingface.co/docs/inference-providers/pricing) |
| NVIDIA NIM (build.nvidia.com) | No card, about 40 RPM, but it is a trial or prototyping tier with roughly 1,000 credits. NVIDIA moderators say production use needs a paid deployment and there is "no official way" to raise limits | [NVIDIA forum](https://forums.developer.nvidia.com/t/request-for-nvidia-build-api-rate-limit-increase-40-rpm-200-rpm/377605) |
| Google AI Studio | Already provider #1. Free-tier numbers are no longer published in docs, only in the AI Studio dashboard | [rate limits](https://ai.google.dev/gemini-api/docs/rate-limits) |

## Suggested order

`llm_provider_order = ["gemini", "mistral", "cloudflare"]`, then templates. Cloudflare is optional as a third free fallback. Keep OpenRouter out: 50 requests/day and an unstable free-model list are not worth an adapter. Like any provider change, this needs the user's approval. All three options above are free, so `llm_paid_enabled` is not affected.
