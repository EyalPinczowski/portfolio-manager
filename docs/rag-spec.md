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


## Built (2026-10-04, backend only, not committed)
Code in `backend/app/rag/`; tests `backend/tests/test_rag.py` (+ migration test in `test_migrations_data.py`), fixtures `backend/tests/fixtures/rag/` (English, Hebrew, JSON) and `rag_eval_questions.json`.
- **Table** `doc_chunk` (migration `0015_doc_chunk`, expand-only): symbol, market, doc_type, source_url, as_of, text, token_count, text_hash, created_at; unique (symbol, text_hash). No user or portfolio column anywhere in the RAG tables or API.
- **Keyword index** behind `ChunkIndex` (`rag/index.py`): SQLite FTS5 table `doc_chunk_fts` (unicode61, `bm25()`), Postgres GIN expression index on `to_tsvector('simple', text)` ranked with `ts_rank_cd`. The index is NOT in the migration: it is not in the SQLModel metadata, so `ChunkIndex.ensure()` (run by the index job, idempotent) creates it and the schema-equals-metadata test stays exact. Before `ensure()`, SQLite search returns nothing. Hebrew has no stemmer: query words are expanded with one-letter prefixes (ב ה ל מ ו ש כ) and prefix matching.
- **Embeddings hook (not built)**: a second `ChunkIndex.search` implementation over a `doc_chunk_vec` table filled by the index job; config flag `rag_embeddings_enabled` (default false, unread). No model, no new dependency.
- **Chunker** (`rag/chunker.py`, `rag/tokens.py`): about 300-token paragraph chunks with about 40-token overlap, boilerplate lines stripped (English and Hebrew), long paragraphs split on sentences then words, dedupe by normalised hash, estimator = characters per token by script (Latin 4.0, Hebrew 2.5). All numbers in config (`rag_*`).
- **Retriever** (`rag/retriever.py`): `Retriever(db).search(symbol, query, doc_types, k, max_tokens, role=None, now=None)`; k capped by `rag_max_k`, tokens by `max_tokens` and (with `role`) the role budget; stale chunks dropped per doc-type TTL (`rag_doc_ttl_days`: filing 400, news 14, transcript 120, profile 365); result `status` is `ok` or `no_coverage` (callers set `confidence=0`). Each search adds one request and its tokens-in to `llm_usage` under provider `rag`, model = role.
- **Prompt builder** (`rag/prompt.py`): `build_prompt(role, PublicFacts, hits)`; exact `PublicFacts` class required, chunks of the same symbol only, no free-text parameter (the instruction per role is a table in code), chunk text fenced as untrusted, chunks cited as `[c<id>]`, whole prompt capped by `rag_role_budgets` (company_profile 2500, news 2000, bear 1500, cio 2000, ask_portfolio 2000).
- **Index job** `python -m app.cli rag-index --from-dir <dir> [--symbols-from universe | --symbols A,B]` (`rag/indexjob.py`): local `<SYMBOL>/<doc_type>/<YYYY-MM-DD>_name.txt` files and JSON lists; idempotent; purges expired chunks at the end. No fetchers, no network.
- **Eval**: hit@3 on 8 fixture questions (English and Hebrew), asserted at 0.85 or more.
- No API route was added. Not built yet: the committee roles calling it, ask-my-portfolio tools, embeddings.
- Needs the user's decision (proposed defaults): chunk 300/40 tokens, role budgets above, TTLs above, `rag_max_k` 12.
