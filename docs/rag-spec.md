# RAG for the app (token minimisation, free, local)

Goal: every LLM call (Company Profile, News, Bear, CIO, ask-my-portfolio, post-mortem phrasing) sees only the few passages it needs, not whole filings or histories. Free tier limits (about 8 RPM and daily token quotas) make this a quota feature as much as a cost one.

## Rules
- Public facts only for free providers (`PublicFacts`). Personal data is never embedded or retrieved for a free provider. Retrieval over the user's own holdings (ask-my-portfolio, post-mortem) uses read-only user-scoped SQL tools and templates, not an external embedding service.
- Free and local: no paid embedding API. Start with SQLite FTS5 / Postgres `tsvector` keyword search (BM25-style) and add local embeddings (a small multilingual model such as multilingual-e5-small, Hebrew included) only if keyword recall is measured too low. No vector DB service; Postgres `pgvector` only if the host supports it, else in-process cosine over a small table.
- 512 MB limit: embedding and chunking run in the GitHub Actions cron job that already feeds fundamentals/filings, never in the API process. The API only queries.
- Every retrieved chunk carries `source`, `as_of`, `symbol`, `market`, `doc_type`; the answer must cite them (feeds the typed `Explanation` sources). No chunk older than its doc-type TTL.
- TASE has little text coverage: empty retrieval means `confidence=0`, never invented.

## Design
1. Table `DocChunk` (symbol, market, doc_type: filing|news|transcript|profile, source_url, as_of, text, token_count, text_hash) plus an FTS index; expand-only migration.
2. Chunker: ~300-token paragraphs with overlap, dedupe by hash, strip boilerplate.
3. `Retriever.search(symbol, query, doc_types, k, max_tokens)` returns top k chunks under a token budget (config), with a hard cap per role.
4. Prompt builder takes only retrieved chunks plus the typed reports; budget enforced in code, with a unit test that a prompt never exceeds the role budget.
5. Response cache keyed by (role, input hash) already exists in the LLM foundation; retrieval results are included in the hash.
6. Evals: retrieval recall on fixture questions, token-per-call tracked in `LlmUsage`.

## Build order
After Analyze a stock's first version works with the template path: DocChunk + FTS + retriever + budget tests, then the committee roles use it, then ask-my-portfolio tools.
