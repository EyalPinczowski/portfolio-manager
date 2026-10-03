/**
 * Security headers for the web app, shared by next.config.ts (dev server) and checked against
 * public/_headers (Cloudflare Pages, production) by tests/security-headers.test.ts.
 *
 * CSP notes
 * - script-src needs 'unsafe-inline' because a static export has no per-request nonce and Next.js
 *   emits inline bootstrap scripts. Mitigations: object-src 'none', base-uri 'self', no remote scripts,
 *   React escapes all output, and no user HTML is ever rendered.
 * - 'wasm-unsafe-eval' lets tesseract.js compile its WebAssembly core. It does NOT allow eval().
 * - worker-src 'self': tesseract.js runs with workerBlobURL:false, so the worker is /tesseract/worker.min.js
 *   (same origin) and no blob: workers are needed.
 * - connect-src 'self': the API is same-origin (Pages Function proxy) and OCR assets are self-hosted under
 *   /tesseract/, so no third-party host is contacted. If NEXT_PUBLIC_API_URL points elsewhere, add it here.
 */
export function buildCsp(opts: { dev?: boolean; apiOrigin?: string } = {}): string {
  const dev = opts.dev ?? false;
  const connect = ["'self'", ...(opts.apiOrigin ? [opts.apiOrigin] : []), ...(dev ? ["ws:", "http://localhost:*"] : [])];
  const directives: Record<string, string[]> = {
    "default-src": ["'self'"],
    "script-src": ["'self'", "'unsafe-inline'", "'wasm-unsafe-eval'", ...(dev ? ["'unsafe-eval'"] : [])],
    "style-src": ["'self'", "'unsafe-inline'"],
    "img-src": ["'self'", "data:"],
    "font-src": ["'self'"],
    "connect-src": connect,
    "worker-src": ["'self'"],
    "manifest-src": ["'self'"],
    "object-src": ["'none'"],
    "base-uri": ["'self'"],
    "form-action": ["'self'"],
    "frame-ancestors": ["'none'"],
  };
  return Object.entries(directives).map(([k, v]) => `${k} ${v.join(" ")}`).join("; ");
}

/** Headers other than CSP (static; identical in dev and production). */
export const BASE_SECURITY_HEADERS: Record<string, string> = {
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "X-Frame-Options": "DENY",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
  "Cross-Origin-Opener-Policy": "same-origin",
  "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
};
