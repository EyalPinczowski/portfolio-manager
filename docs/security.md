# Security requirements

Status: living document. Started 2026-10-03 from a user request: **"when getting a screenshot, copy only the stock information and then delete the screenshot; look for more security problems."**

## 1. Screenshot handling: keep only the stock data

**Rule: the screenshot is never kept. Only the stock rows survive.** Allowed to be stored: name, symbol, quantity, price, value, average cost, currency/unit. Never stored: the image, the raw OCR text, account numbers, owner names, addresses, or any other text from the screenshot.

Findings on the Phase 1 code (checked 2026-10-03):
- ✅ Draft and holding rows contain only stock fields (`ParsedRow`). No raw OCR text is stored.
- ✅ The image is re-encoded to PNG, which drops EXIF/GPS metadata, and the header and long digit runs are blurred before OCR.
- ✅ No logging of OCR text or image bytes was found.
- ❌ **The claim "never written to disk" is false for typical phone screenshots.** The upload endpoint uses a multipart `UploadFile`, and Starlette spools any upload over 1 MB into a temporary file in `/tmp`. That file stays on disk until the request is closed and is not securely wiped.
- ❌ Redaction of long digit runs works only if Tesseract is installed. Without it (as on a 512 MB host), only the header is blurred, and the rest of the image can go to Google (Gemini free tier) with account numbers or names visible.
- ❌ Unconfirmed drafts are never deleted. Confirmed drafts keep a second copy of the rows.

Required fixes:
1. **Best option: read the screenshot on the user's device** (in-browser `tesseract.js`, Hebrew + English), then send only the parsed rows to the server. The image never leaves the phone and the server never sees it. Gemini stays an optional, explicitly consented upgrade for accuracy. *(Option A in `docs/deployment.md` already needs this.)*
2. **If an image is uploaded to the server** (Gemini path or Tesseract server fallback):
   - Take the image as a **raw request body** (`Content-Type: image/png|jpeg|webp`), read from the stream with the size limit enforced while reading, **not** as a multipart file. That avoids the temp file entirely.
   - Check the file type from its first bytes, not from the header, and cap the pixel dimensions (decompression-bomb guard: `Image.MAX_IMAGE_PIXELS`).
   - Hold the bytes in memory only. Overwrite and release them in a `finally` block.
   - Before sending to Gemini, **crop to the table** and blur everything outside it, not only the header.
   - If word-box redaction is unavailable, **refuse to send to a third party** (use on-device reading or ask the user to crop) instead of sending a half-redacted image.
3. **Retention:** delete unconfirmed drafts after **24 hours**, and after a draft is confirmed, clear its `rows` (keep only the `HoldingsSnapshot`). Add a scheduled purge job and a test.
4. **A "what we keep" notice** on the import screen: "We keep only the stock list. The screenshot is deleted right away."
5. **Tests:** (a) a large (>1 MB) upload leaves no new files in the temp directory, (b) a draft contains no keys other than the stock fields, (c) a log capture during import contains no OCR text.

## 2. Findings from the spot-check of other areas (2026-10-03)
- ⚠️ `cookie_secure` defaults to **False**. In production it must be True. Fix: default True, with an explicit `ENV=dev` override, and refuse to start in production with it off.
- ⚠️ Cross-site hosting (Option A: Cloudflare Pages site + Render API on different domains) conflicts with `SameSite=Lax` cookies and the CSRF design. Needs a deliberate design (a same-origin proxy or a shared parent domain) before deploying.
- ✅ The service worker never caches `/api/` responses, so account data is not stored in the browser cache.
- ✅ The session cookie is httpOnly, signed, and only a hash of the token is stored on the server.
- ✅ The upload size limit is enforced (but see 1.2: it is applied after the multipart parse).

## 3. Open review items
The Opus Phase 1 review is auditing: owner scoping on every endpoint, CSRF, rate limiting, signup/invite abuse, account export/delete, secrets, injection, and the frontend/backend contract. Its findings are added to `docs/reviews/` and fixed in Phase 2.

## 4. Always-on rules
- Never log request bodies, OCR text, tokens, passwords, emails or API keys.
- Secrets only in env vars. `.env` is never committed.
- Every query is scoped to the logged-in user. Tests prove cross-user access returns 404.
- Rate-limit login, signup, and upload endpoints per IP and per account.
- Encrypt backups. Offer data export and deletion.
- Free-tier third-party APIs (Gemini, Groq) may use the data they receive. Send them only what the user has consented to, and nothing that identifies the account holder.
