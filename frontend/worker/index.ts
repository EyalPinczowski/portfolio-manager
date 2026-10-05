/**
 * Cloudflare Worker entry (static assets + same-origin API proxy), used when the site is deployed as a Worker
 * (wrangler.jsonc) instead of a Pages project. `/api/*` runs here first (run_worker_first); every other path is
 * served from the static export by the ASSETS binding.
 *
 * Settings -> Variables and Secrets: API_ORIGIN (e.g. https://your-api.onrender.com) and PROXY_SHARED_SECRET
 * (the same value as on the API).
 */
import { proxyApi, type ProxyEnv } from "../lib/proxy-handler";

interface Env extends ProxyEnv { ASSETS: { fetch(request: Request): Promise<Response> } }

const worker = {
  async fetch(request: Request, env: Env): Promise<Response> {
    const { pathname } = new URL(request.url);
    if (pathname === "/api" || pathname.startsWith("/api/")) return proxyApi(request, env);
    return env.ASSETS.fetch(request);
  },
};

export default worker;
