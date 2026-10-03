import type { components } from "./api-schema";
import type { ImportDraftPending, LaunchGate, SessionInfo } from "./api-pending";
import { RASTER_TYPES } from "./api-pending";
import { apiBase } from "./config";
import { ApiError } from "./errors";
import { mockRequest } from "./mock";
import { clearClientState } from "./session";

export { ApiError };
export type { LaunchGate, SessionInfo };

/*
 * Types are DERIVED from the generated lib/api-schema.d.ts (npm run gen:api, from backend/openapi.json).
 * `Narrow` replaces fields that the OpenAPI schema types loosely (plain `string`, free-form objects) with the
 * precise union the backend actually returns. Contract items the backend has not published yet come from
 * lib/api-pending.ts (hand-written) and are intersected in.
 */
type S = components["schemas"];
type Narrow<T, N> = Omit<T, keyof N> & N;

export type MarketKey = "US" | "TASE" | "CRYPTO";
export type Horizon = NonNullable<S["HoldingOut"]["horizon"]>;
export type AssetType = "stock" | "etf" | "crypto" | "fund" | "bond" | "cash";
export type Money = S["Money"];
export type Pnl = S["Pnl"];
export type StopTpStatus = S["HoldingOut"]["stop_tp_status"];

export type Me = Narrow<S["MeOut"], { locale: "he" | "en" }>;
export type Summary = S["SummaryOut"];
export type RiskFilter = Narrow<S["RiskFilterOut"], { stop_type: "fixed" | "trailing" | "both" }>;
export type RiskPreset = { name: string } & RiskFilter;
export type Portfolio = Narrow<S["PortfolioOut"], { base_currency: "ILS" | "USD"; risk_filter: RiskFilter | null }>;
export type ScoreCardMini = S["ScoreCardMini"];
export type Holding = Narrow<S["HoldingOut"], { asset_type: AssetType; market: MarketKey }>;

export interface Explanation {
  summary: string;
  inputs: Record<string, string | number>;
  rules_applied: string[];
}
export type SignalBreakdown = Narrow<S["SignalBreakdownOut"], { explanation: Explanation }>;
export type ScoreCardDetail = Narrow<S["ScoreCardDetail"], { explanation: Explanation; signals: SignalBreakdown[] }>;

export type ExposureItem = S["ExposureItem"];
export type Breach = S["Breach"];
export type XrayRaw = Narrow<
  S["XrayOut"],
  { concentration: { symbol: string; weight_pct: number }[]; home_bias: { israel_pct?: number } }
>;
export type HeatmapItem = S["HeatmapItem"];

export type ChangeType = "buy" | "sell" | "deposit" | "withdrawal";
export type ImportFlag = NonNullable<S["ImportRowModel"]["flags"]>[number];
export type MatchCandidate = S["MatchCandidate"];
export type ImportRow = Narrow<S["ImportRowModel"], { flags: ImportFlag[]; candidates?: MatchCandidate[] }>;
export type ProposedChange = Narrow<S["ProposedChange"], { type: ChangeType }>;
export type ImportDraft = Narrow<S["ImportDraftOut"], { rows: ImportRow[]; proposed_changes: ProposedChange[] }> &
  ImportDraftPending;
export type SecurityHit = Narrow<S["SecurityHit"], { market: MarketKey }>;
export type PriceAlert = Narrow<S["AlertOut"], { op: "above" | "below" }>;
export type Notification = S["NotificationOut"];

// ---------- Client ----------
export const isMock = (): boolean => process.env.NEXT_PUBLIC_API_MOCK === "1";

let csrfToken: string | null = null;
export const setCsrfToken = (t: string | null): void => { csrfToken = t; };
export const getCsrfToken = (): string | null => csrfToken;

interface Payload { json?: unknown; image?: Blob }

async function raw<T>(method: string, path: string, payload: Payload = {}): Promise<T> {
  if (isMock()) return mockRequest(method, path, payload.image ?? payload.json) as T;
  const headers: Record<string, string> = {};
  if (method !== "GET") {
    if (!csrfToken && path !== "/auth/login" && path !== "/auth/signup") await api.me();
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
  }
  let body: BodyInit | undefined;
  if (payload.image) {
    // Raw image body, never multipart: the server must not spool the upload to a temp file.
    headers["Content-Type"] = payload.image.type;
    body = payload.image;
  } else if (payload.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(payload.json);
  }
  const res = await fetch(`${apiBase()}/api${path}`, { method, headers, body, credentials: "include" });
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : msg; } catch { /* ignore */ }
    const ra = Number(res.headers.get("Retry-After"));
    throw new ApiError(res.status, msg, Number.isFinite(ra) && ra > 0 ? ra : undefined);
  }
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") ?? "";
  return (ct.includes("json") ? await res.json() : undefined) as T;
}

const get = <T,>(p: string) => raw<T>("GET", p);
const post = <T,>(p: string, b?: unknown) => raw<T>("POST", p, { json: b ?? {} });
const patch = <T,>(p: string, b: unknown) => raw<T>("PATCH", p, { json: b });
const del = <T,>(p: string, b?: unknown) => raw<T>("DELETE", p, b === undefined ? {} : { json: b });

export const api = {
  async me(): Promise<Me> {
    const me = await get<Me>("/auth/me");
    csrfToken = me.csrf_token;
    return me;
  },
  async signup(b: { invite_code: string; email: string; password: string; accept_disclaimer: true; locale: string }) {
    const r = await post<Me | void>("/auth/signup", b);
    csrfToken = null;
    await clearClientState();
    return r;
  },
  async login(b: { email: string; password: string }) {
    const r = await post<unknown>("/auth/login", b);
    csrfToken = null;
    await clearClientState();
    return r;
  },
  async logout() {
    try { await post("/auth/logout"); } finally { csrfToken = null; await clearClientState(); }
  },
  consentOcr: () => post("/auth/consent/ocr"),
  /** Needs the account password (wrong password -> 403). */
  exportData: (password: string) => post<unknown>("/me/export", { password }),
  async deleteAccount(password: string) {
    await del("/me", { password });
    csrfToken = null;
    await clearClientState();
  },

  sessions: () => get<SessionInfo[]>("/auth/sessions"),
  revokeSession: (id: number) => del(`/auth/sessions/${id}`),
  revokeOtherSessions: () => post("/auth/sessions/revoke-all"),
  launchGate: () => get<LaunchGate>("/launch-gate"),

  portfolios: () => get<Portfolio[]>("/portfolios"),
  createPortfolio: (b: { name: string; base_currency: string }) => post<Portfolio>("/portfolios", b),
  patchPortfolio: (id: number, b: Partial<Pick<Portfolio, "name" | "risk_filter">>) =>
    patch<Portfolio>(`/portfolios/${id}`, b),
  summary: (id: number | "combined") => get<Summary>(`/portfolios/${id}/summary`),
  holdings: (id: number) => get<Holding[]>(`/portfolios/${id}/holdings`),
  patchHolding: (pid: number, hid: number, b: { horizon?: Horizon | null; quantity?: number }) =>
    patch<Holding>(`/portfolios/${pid}/holdings/${hid}`, b),
  xray: (id: number) => get<XrayRaw>(`/portfolios/${id}/xray`),
  heatmap: (id: number) => get<HeatmapItem[]>(`/portfolios/${id}/heatmap`),
  riskPresets: () => get<RiskPreset[] | Record<string, RiskFilter>>("/risk/presets"),

  /** Server-reading path: the image goes up as the raw request body (png/jpeg/webp), not multipart. */
  createImport(portfolioId: number, image: Blob) {
    if (!(RASTER_TYPES as readonly string[]).includes(image.type)) throw new ApiError(415, "Unsupported image type");
    return raw<ImportDraft>("POST", `/portfolios/${portfolioId}/imports`, { image });
  },
  /** On-device path: rows parsed in the browser; the image never leaves the device. */
  importRows: (portfolioId: number, rows: ImportRow[]) =>
    post<ImportDraft>(`/portfolios/${portfolioId}/imports/rows`, { rows }),
  getImport: (id: number) => get<ImportDraft>(`/imports/${id}`),
  patchImport: (id: number, b: { rows?: ImportRow[]; proposed_changes?: ProposedChange[] }) =>
    patch<ImportDraft>(`/imports/${id}`, b),
  confirmImport: (id: number) => post<ImportDraft>(`/imports/${id}/confirm`),
  searchSecurities: (q: string) => get<SecurityHit[]>(`/securities/search?q=${encodeURIComponent(q)}`),

  scorecard: (hid: number) => get<ScoreCardDetail>(`/holdings/${hid}/scorecard`),
  alerts: () => get<PriceAlert[]>("/alerts"),
  createAlert: (b: { symbol: string; op: "above" | "below"; price: number }) => post<PriceAlert>("/alerts", b),
  deleteAlert: (id: number) => del(`/alerts/${id}`),
  notifications: () => get<Notification[]>("/notifications"),
  readNotification: (id: number) => post(`/notifications/${id}/read`),
};

/** Normalise exposure payloads that may be arrays or {name: pct} maps. */
export function toExposure(x: ExposureItem[] | Record<string, number> | undefined): ExposureItem[] {
  if (!x) return [];
  if (Array.isArray(x)) return x;
  return Object.entries(x).map(([name, weight_pct]) => ({ name, weight_pct }));
}
export function toPresets(x: RiskPreset[] | Record<string, RiskFilter>): RiskPreset[] {
  if (Array.isArray(x)) return x;
  return Object.entries(x).map(([name, v]) => ({ ...v, name }));
}
