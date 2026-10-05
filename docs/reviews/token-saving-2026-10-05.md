# Token and request saving on free-tier LLMs (2026-10-05)

Scope: the Investment Committee (4 structured-JSON calls: `company_profile`, `news`, `bear`, `cio`) over
RAG passages, with per-user / per-provider daily budgets, the DB response cache (`app/llm/cache.py`)
and template fallbacks. Research only; no code was changed.

Read first: `docs/analysis-committee.md` "Cost and limits", `backend/app/rag/prompt.py`,
`backend/app/llm/providers.py`, `cache.py`, `config.py` (LLM/RAG block).

## 1. Where the app stands today

| Item | Current value | Where |
|---|---|---|
| Input budget per role (estimated tokens) | profile 2500, news 2000, bear 1500, cio 2000 | `rag_role_budgets` |
| Chunks asked per role (k) | 8 / 8 / 6 / 6 | `committee_role_k` |
| Chunk size | ~300 tokens, 40 overlap | `rag_chunk_target_tokens` |
| Output cap | 1024 tokens, every role | `llm_max_output_tokens` |
| Gemini thinking | `thinkingBudget: 0` | `gemini_thinking_budget` |
| Groq JSON | `json_object` mode, schema pasted into the system prompt | `GroqProvider._send` |
| Groq reasoning | not set (gpt-oss default effort) | - |
| Cache key | role + system + prompt + schema + scope + provider + model | `input_hash` |
| Cache TTL | 7 days, news 6 hours | `llm_cache_ttl_*` |
| Tokens recorded | `totalTokenCount` / `total_tokens` only; no input/output/cached split | providers, ledger |

Rough worst case for one cold run: ~8,000 estimated input tokens + Groq schema text (a few hundred per
call) + up to 4 x 1,024 output tokens, so **~9-13k tokens and 4 requests** (5-8 with validation retries).

## 2. Current free-tier limits (verified 2026-10-05)

### Gemini (Google AI Studio)
- Google **no longer publishes** free-tier RPM/TPM/RPD numbers; they are shown only per project in AI
  Studio and "aren't guaranteed". Limits are **per project (not per key), per model**; RPD resets at
  midnight Pacific. Preview/experimental models are tighter. Rate-limits page last updated 2026-09-02.
  Source: https://ai.google.dev/gemini-api/docs/rate-limits ;
  third-party summary dated 2026-10-01: https://www.memetik.ai/guides/gemini-api-free-tier-limits
- Third-party figures disagree (2.5 Flash quoted as 10 RPM / 250k TPM / 500 RPD by some, 15 / 1M / 1,500
  by others; Flash-Lite 15-30 RPM, 1,000-1,500 RPD). **Treat our `llm_requests_per_minute=8` and
  `llm_daily_budget=900` as unverified until checked in AI Studio for the actual project.**
  https://aipromptshub.co/blog/gemini-api-free-tier-rate-limits
- Free tier: text on Flash/Flash-Lite lines is free; **context caching "Not available"** and **Batch API
  "Not available"** on the free tier; free-tier content **is used to improve Google products** (fine,
  as only `PublicFacts` and public passages are sent). Pricing page updated 2026-10-01.
  https://ai.google.dev/gemini-api/docs/pricing
- **Deprecations page (updated 2026-10-01):** no shutdown date for `gemini-2.5-flash` / `-flash-lite`,
  but "we are limiting access to the 2.5 models to users who have actively used them in the past".
  A fresh project/key may not get our default `gemini-2.5-flash`; the probe fallbacks matter.
  Recommended: 3.5 Flash-Lite / 3.6-3.8 Flash. https://ai.google.dev/gemini-api/docs/deprecations
- **Implicit caching** is on by default for 2.5+ models, minimum prefix **2,048 tokens** (2.5 Flash)
  or **4,096** (3.5-3.8 Flash); the docs do not say whether it applies on the free tier.
  https://ai.google.dev/gemini-api/docs/caching

### Groq (Free plan table, fetched 2026-10-05; the page carries no date)
| Model | RPM | RPD | TPM | TPD |
|---|---|---|---|---|
| openai/gpt-oss-20b | 30 | 1K | **8K** | 200K |
| openai/gpt-oss-120b | 30 | 1K | **8K** | 200K |
| qwen/qwen3.8-27b | 30 | 1K | 8K | 200K |

- `llama-3.3-70b-versatile` (our last Groq fallback) is **not in the free table any more**.
- **TPM is the binding limit, not RPM:** one cold committee run (~10k tokens) is bigger than one
  minute of Groq quota; TPD 200K is ~15-20 cold runs a day per model.
- **Cached prompt tokens do not count toward Groq rate limits** (gpt-oss models; cache lives 2 h).
- Response headers expose `x-ratelimit-remaining-tokens`, `-requests`, `retry-after`.
  https://console.groq.com/docs/rate-limits , https://console.groq.com/docs/prompt-caching

## 3. Ideas

Each entry: what, expected saving, quality risk, effort, source. Savings are estimates against the
current worst case and should be confirmed once real token counts are logged (idea 1).

### Top 5 (recommended order)

**1. Log input / output / thinking / cached tokens per call, and read Groq's rate-limit headers.**
- What: store `promptTokenCount`, `candidatesTokenCount`, `thoughtsTokenCount`,
  `cachedContentTokenCount` (Gemini `usageMetadata`) and Groq `usage.prompt_tokens`,
  `completion_tokens`, `prompt_tokens_details.cached_tokens` in the ledger, plus cache hits. Calibrate
  `rag_chars_per_token_*` against the real counts (the local estimator is the "token counting before
  sending" step and needs no API call). Before a Groq call, compare the estimated size with
  `x-ratelimit-remaining-tokens` from the last response and wait or fall through instead of eating a 429.
- Saving: none directly; it avoids wasted 429 calls and is the only way to know which of the ideas below
  pays. The doc itself says "input/output tokens and cache hits are not recorded".
- Quality risk: none. Effort: S.
- Sources: https://ai.google.dev/gemini-api/docs/tokens , https://console.groq.com/docs/rate-limits

**2. Skip calls when inputs did not change (cache by data version, not only by prompt text).**
- What: give each role a "data version" = symbol + sorted retrieved chunk ids/content hashes + facts
  bucket (+ upstream report hashes for Bear/CIO). Before building/sending, look up the last validated
  answer for that version; if equal, reuse it regardless of the 6 h news TTL (news with **no new
  chunks** is not stale). Run the committee only for the roles whose inputs changed (e.g. a price
  tick that changes only the score band re-runs CIO alone; new news re-runs News -> Bear -> CIO but
  not Profile). For the screener, re-run a finalist only if its deterministic score moved by more than
  a threshold or it has new passages.
- Saving: on repeat runs 50-100% of calls; screener re-runs mostly free.
- Quality risk: low (same inputs give the same answer at temperature 0.2). Risk: a bug in the
  version hash hides fresh news; keep a hard max age (e.g. 7 days).
- Effort: M. Source (Gemini recommends reusing common prefixes / identical content; general
  practice): https://ai.google.dev/gemini-api/docs/caching

**3. Smaller per-role output: tighter schemas and per-role `max_output_tokens`.**
- What: cap output per role (e.g. profile 600, news 500, bear 500, cio 500 instead of 1024); add
  `maxLength` to free-text fields, `maxItems` to lists (risks 3-5, claims 5 instead of
  `committee_max_claims=8`), short enum values, drop fields code can compute (counts, ids already
  known). Use Groq **strict `json_schema`** (supported on gpt-oss-20b/120b, qwen3.8-27b) instead of
  pasting the schema into the system prompt: guaranteed-valid output removes most validation retries
  (each retry is a full extra call) and the schema leaves the visible prompt.
- Saving: 30-50% of output tokens; retries drop from "sometimes" to near zero, i.e. up to 1-4 calls
  per run saved on Groq. JSON keys/punctuation can be ~half of a small JSON answer.
- Quality risk: low-medium; too low a cap truncates JSON -> template fallback. Size caps from measured
  outputs (idea 1), with ~30% headroom. Strict mode requires every field `required` and
  `additionalProperties: false` (schema changes).
- Effort: S-M. Sources: https://console.groq.com/docs/structured-outputs ,
  https://david-gilbertson.medium.com/llm-output-formats-why-json-costs-more-than-tsv-ebaf590bd541

**4. Fewer, shorter passages (lower k and budgets), CIO without raw passages.**
- What: profile k 8 -> 5, news 8 -> 6, bear 6 -> 4, **cio 6 -> 0** (the CIO already gets the
  `NewsReport`/`BearCase` with citations; give it reports + facts only). Drop near-duplicate chunks
  (same story from several feeds) before the budget loop; shorten the passage header (date + doc type
  only; keep the URL in our DB keyed by `c<id>`, not in the prompt). Order passages best-first and put
  the best last as well, because models use the start and end of the context best.
- Saving: ~30-45% of input tokens (CIO alone ~1,500 tokens per run). "Lost in the Middle" shows
  accuracy saturates long before retrieval recall does (20 -> 50 docs gave ~1.5%).
- Quality risk: low-medium; fewer passages can miss a risk. Check with `backend/evals/` before and
  after (recall of cited facts, Bear risk coverage).
- Effort: S (config) + S (dedupe/header). Source: https://arxiv.org/abs/2307.03172

**5. Spread roles across models (each model has its own free quota) and keep reasoning off.**
- What: Gemini and Groq limits are **per model**. Route the light roles (news summarise/classify,
  profile) to Flash-Lite (higher RPD) and keep Flash for Bear/CIO; on Groq split gpt-oss-20b
  (news/profile) and gpt-oss-120b (bear/cio), each with its own 1K RPD / 200K TPD. Keep Gemini
  thinking off (`thinkingBudget: 0` on 2.5; for 3.x use `thinkingLevel: "minimal"`/`"low"` - 3.8 Flash
  rejects "minimal" and the docs say not to send both fields). On Groq gpt-oss set
  `reasoning_effort: "low"`; for qwen3.8 use `"none"`. Thinking tokens count inside the output cap.
- Saving: roughly doubles usable daily quota; reasoning off/low cuts 20-60% of output tokens on
  reasoning models and avoids truncated JSON.
- Quality risk: medium for Bear/CIO on smaller models (keep them on the bigger one). Needs per-role
  budgets in the ledger (today budgets are per provider).
- Effort: M. Sources: https://ai.google.dev/gemini-api/docs/rate-limits ,
  https://console.groq.com/docs/reasoning , https://ai.google.dev/gemini-api/docs/generate-content/thinking ,
  https://help.apiyi.com/en/gemini-api-thinking-budget-level-error-fix-en.html

### Other ideas

**6. Merge calls: News + Bear in one call (4 -> 3 cold, 3 -> 2 when the profile is cached).**
- What: one schema `{news: NewsReport, bear: BearCase}` over the same passages. Keep CIO separate so
  the adversarial check stays independent; keep Profile separate because it is cached for weeks.
- Saving: 1 request and ~1,500 input tokens per run (passages sent once). Batch prompting work shows
  near-linear savings with comparable quality on simple tasks.
- Quality risk: medium; the Bear may anchor on the neutral news summary, and a single validation
  failure loses both outputs (template for both). Measure with the evals; make it a setting.
- Effort: M. Source: https://aclanthology.org/2023.emnlp-industry.74/

**7. Order prompts for provider-side caching (Groq now, Gemini later).**
- What: keep a byte-identical static prefix per role first (system text: role instruction, cite rule,
  untrusted rule, schema), then facts, then passages - `build_prompt` already starts with the fixed
  part; make sure nothing variable (dates, ids) sits before it. Groq gpt-oss caches automatically
  (128-1024 token minimum, 2 h) and **cached tokens do not count toward TPM**, which is our binding
  Groq limit. Gemini implicit caching needs >= 2,048-4,096 tokens, larger than our prompts, and
  explicit caching is not on the free tier - so expect little from Gemini.
- Saving: Groq: the schema+instruction prefix (~300-800 tokens per call) stops counting toward TPM
  when strict mode is not used. Gemini: ~0 at current sizes.
- Quality risk: none. Effort: S.
- Sources: https://console.groq.com/docs/prompt-caching , https://ai.google.dev/gemini-api/docs/caching ,
  https://ai.google.dev/gemini-api/docs/pricing

**8. Run Bear/CIO lazily (product decision - ask the user).**
- What: "Analyze a stock" shows the deterministic score + cached profile/news at once; Bear and CIO
  run only when the user opens "Why?" / "Committee". Screener finalists get News only until opened.
- Saving: likely 30-50% of calls in practice (many taps never open the committee view).
- Quality risk: none to quality; UX change (wait on open). Effort: M. Source: product judgement, no
  external source.

**9. Cheap extractive compression instead of LLMLingua.**
- What: LLMLingua/LongLLMLingua compress 2-5x with small loss, but need a local model (GPT-2/XLM-R
  size), which conflicts with the 512 MB image rule. Instead: strip boilerplate (bylines, "Read more",
  disclaimers, tables of contents) at chunk time, keep only sentences that contain the symbol/company
  name, a number, or a risk keyword, and collapse whitespace.
- Saving: 15-30% of passage tokens. Quality risk: low-medium (may drop context sentences; the
  grounding check still guards numbers). Effort: M.
- Sources: https://github.com/microsoft/LLMLingua ,
  https://www.llamaindex.ai/blog/longllmlingua-bye-bye-to-middle-loss-and-save-on-your-rag-costs-via-prompt-compression-54b559b9ddf7

**10. Cheaper retry.**
- What: on a validation failure, retry on the same model only for repairable errors, with the
  validation error + the bad JSON; skip the retry when strict schema mode was on (the error is then
  semantic, not syntactic) and go straight to the next provider or template.
- Saving: up to 1 full call per failed role. Quality risk: low. Effort: S.
- Source: https://console.groq.com/docs/structured-outputs

**11. Batching across symbols (screener).**
- What: Gemini Batch API is **not available on the free tier**. Prompt-level batching (several
  finalists' news in one call) conflicts with `build_prompt`'s one-symbol rule, which is a safety
  property (no cross-symbol leakage, per-symbol grounding). **Not recommended.**
- Source: https://ai.google.dev/gemini-api/docs/pricing , https://arxiv.org/abs/2503.15551 (batch
  prompting attacks/contamination)

**12. Keep the cache across provider failover where safe.**
- What: the cache key contains provider + model, so a Groq fallback answer is never reused once Gemini
  is back, and vice versa. Allow a read of any validated answer for the same data version (idea 2)
  within its TTL, preferring the primary model's answer.
- Saving: avoids re-paying runs after a failover day. Quality risk: low (answers were validated and
  grounded). Effort: S.

**13. Compact facts block.**
- What: facts are already compact JSON with rounded numbers; small further wins: drop `reasons`
  strings for Bear/CIO when `indicators` carries the same numbers, short keys.
- Saving: 50-150 tokens per Bear/CIO call. Quality risk: low. Effort: S.
- Source: https://jangwook.net/en/blog/en/llm-token-cost-data-format-experiment/

## 4. Config / risk notes found while reading

- `groq_model_fallbacks` contains `llama-3.3-70b-versatile`, which is not in Groq's free table now;
  `qwen/qwen3.8-27b` is (strict JSON supported). https://console.groq.com/docs/rate-limits
- Default `gemini_model = gemini-2.5-flash` may be refused to projects that have not used 2.5
  before. https://ai.google.dev/gemini-api/docs/deprecations
- If a 3.x Gemini fallback is chosen, `thinkingBudget: 0` is legacy; 3.8 Flash rejects
  `thinkingLevel: "minimal"`. Map per model.
- Our 8 RPM / 900 RPD per provider are not checked against the real AI Studio project limits and Groq's
  8K TPM is not modelled at all (a token bucket on tokens, not just requests, is needed for Groq).

## Sources
- https://ai.google.dev/gemini-api/docs/rate-limits (updated 2026-09-02)
- https://ai.google.dev/gemini-api/docs/pricing (updated 2026-10-01)
- https://ai.google.dev/gemini-api/docs/deprecations (updated 2026-10-01)
- https://ai.google.dev/gemini-api/docs/caching (updated 2026-09-02)
- https://ai.google.dev/gemini-api/docs/tokens
- https://ai.google.dev/gemini-api/docs/generate-content/thinking
- https://www.memetik.ai/guides/gemini-api-free-tier-limits (2026-10-01)
- https://aipromptshub.co/blog/gemini-api-free-tier-rate-limits
- https://console.groq.com/docs/rate-limits
- https://console.groq.com/docs/prompt-caching
- https://console.groq.com/docs/structured-outputs
- https://console.groq.com/docs/reasoning
- https://help.apiyi.com/en/gemini-api-thinking-budget-level-error-fix-en.html
- https://arxiv.org/abs/2307.03172 (Lost in the Middle)
- https://aclanthology.org/2023.emnlp-industry.74/ (Batch Prompting)
- https://arxiv.org/abs/2503.15551 (batch prompting attacks)
- https://github.com/microsoft/LLMLingua
- https://www.llamaindex.ai/blog/longllmlingua-bye-bye-to-middle-loss-and-save-on-your-rag-costs-via-prompt-compression-54b559b9ddf7
- https://david-gilbertson.medium.com/llm-output-formats-why-json-costs-more-than-tsv-ebaf590bd541
- https://jangwook.net/en/blog/en/llm-token-cost-data-format-experiment/
