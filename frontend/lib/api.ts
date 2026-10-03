import { mockRequest } from "./mock";

// ---------- Types (from docs/phase-1-spec.md API contract) ----------
export interface Me {
  id: number;
  email: string;
  locale: "he" | "en";
  disclaimer_accepted: boolean;
  ocr_consent: boolean;
  csrf_token: string;
}
export interface Money { ils: number; usd: number }
export interface Pnl { ils: number; usd: number; pct: number }
export type MarketKey = "US" | "TASE" | "CRYPTO";
export type Horizon = "1w" | "1m" | "3m" | "6m" | "1y";
export type AssetType = "stock" | "etf" | "crypto" | "fund" | "bond" | "cash";

export interface Summary {
  value: Money;
  day_pnl: Pnl;
  week_pnl: Pnl;
  month_pnl: Pnl;
  since_start_pnl: Pnl;
  /** ISO date the user first started using the app */
  since_start_date: string;
  weekly_bars: { week_start: string; pnl_ils: number; pct: number }[];
  monthly_bars: { month: string; pnl_ils: number; pct: number }[];
  since_start_series: { date: string; pct: number; sp500_pct: number; ta125_pct: number }[];
  as_of: string;
  markets: Record<MarketKey, { open: boolean }>;
}

export interface RiskFilter {
  preset?: string;
  max_position_pct: number;
  max_sector_pct: number;
  max_country_pct: number;
  max_loss_per_position_pct: number;
  max_portfolio_risk_per_trade_pct: number;
  max_total_portfolio_risk_pct: number;
  min_rr: number;
  stop_type: "fixed" | "trailing" | "both";
  drawdown_defensive_pct: number;
}
export type RiskPreset = { name: string } & RiskFilter;

export interface Portfolio {
  id: number;
  name: string;
  base_currency: "ILS" | "USD";
  risk_filter: RiskFilter | null;
  created_at: string;
}

export type StopTpStatus = "missing" | "needs_horizon";
export interface ScoreCardMini { total: number; technical: number; patterns: number; confidence: number }
export interface Holding {
  id: number;
  symbol: string;
  name_en: string;
  name_he: string;
  asset_type: AssetType;
  market: MarketKey;
  quantity: number;
  price: number;
  currency: string;
  day_change_pct: number;
  value_ils: number;
  pnl: Pnl;
  weight_pct: number;
  horizon: Horizon | null;
  stop_tp_status: StopTpStatus;
  score_card: ScoreCardMini;
}

export interface Explanation {
  summary: string;
  inputs: Record<string, string | number>;
  rules_applied: string[];
}
export interface SignalBreakdown {
  name: string;
  score: number;
  confidence: number;
  weight: number;
  reasons: string[];
  data_as_of: string;
  explanation: Explanation;
}
/** Assumption: the contract only says "score card with signal breakdown + explanation (no verdict)". */
export interface ScoreCardDetail {
  holding_id: number;
  portfolio_id: number;
  symbol: string;
  name_en: string;
  name_he: string;
  horizon: Horizon | null;
  total: number;
  confidence: number;
  validated: boolean;
  signals: SignalBreakdown[];
  explanation: Explanation;
}

export interface ExposureItem { name: string; weight_pct: number }
export interface Breach { rule: string; value: number; limit: number; why: string }
export interface XrayRaw {
  concentration: { symbol: string; weight_pct: number }[];
  currency_exposure: ExposureItem[] | Record<string, number>;
  country_exposure: ExposureItem[] | Record<string, number>;
  sector_exposure: ExposureItem[] | Record<string, number>;
  home_bias: number | { israel_pct?: number; [k: string]: unknown };
  breaches: Breach[];
}
export interface HeatmapItem { symbol: string; sector: string; weight_pct: number; day_change_pct: number }

export type ChangeType = "buy" | "sell" | "deposit" | "withdrawal";
export interface ImportRow {
  index: number;
  name: string;
  symbol: string | null;
  quantity: number | null;
  price: number | null;
  value: number | null;
  cost: number | null;
  currency: string;
  unit: "ILS" | "agorot" | "USD";
  matched_name?: string | null;
  flags: string[];
}
export interface ProposedChange {
  row_index: number;
  symbol: string | null;
  type: ChangeType;
  quantity: number | null;
  amount: number | null;
  currency: string;
}
export interface ImportDraft {
  id: number;
  portfolio_id: number;
  status: "draft" | "confirmed" | "discarded";
  rows: ImportRow[];
  proposed_changes: ProposedChange[];
}
export interface SecurityHit { symbol: string; name_en: string; name_he: string; market: MarketKey }

export interface PriceAlert {
  id: number;
  symbol: string;
  op: "above" | "below";
  price: number;
  active: boolean;
  triggered_at: string | null;
}
export interface Notification {
  id: number; kind: string; title: string; body: string; created_at: string; read: boolean;
}

// ---------- Client ----------
export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export const isMock = (): boolean => process.env.NEXT_PUBLIC_API_MOCK === "1";

let csrfToken: string | null = null;
export const setCsrfToken = (t: string | null): void => { csrfToken = t; };
export const getCsrfToken = (): string | null => csrfToken;

async function raw<T>(method: string, path: string, body?: unknown, form?: FormData): Promise<T> {
  if (isMock()) return mockRequest(method, path, body) as T;
  const headers: Record<string, string> = {};
  if (method !== "GET") {
    if (!csrfToken && path !== "/auth/login" && path !== "/auth/signup") await api.me();
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
  }
  let payload: BodyInit | undefined;
  if (form) payload = form;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const res = await fetch(`/api${path}`, { method, headers, body: payload, credentials: "include" });
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : msg; } catch { /* ignore */ }
    throw new ApiError(res.status, msg);
  }
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") ?? "";
  return (ct.includes("json") ? await res.json() : undefined) as T;
}

const get = <T,>(p: string) => raw<T>("GET", p);
const post = <T,>(p: string, b?: unknown) => raw<T>("POST", p, b ?? {});
const patch = <T,>(p: string, b: unknown) => raw<T>("PATCH", p, b);
const del = <T,>(p: string) => raw<T>("DELETE", p);

export const api = {
  async me(): Promise<Me> {
    const me = await get<Me>("/auth/me");
    csrfToken = me.csrf_token;
    return me;
  },
  signup: (b: { invite_code: string; email: string; password: string; accept_disclaimer: true; locale: string }) =>
    post<Me | void>("/auth/signup", b),
  async login(b: { email: string; password: string }) {
    const r = await post<unknown>("/auth/login", b);
    csrfToken = null;
    return r;
  },
  async logout() { await post("/auth/logout"); csrfToken = null; },
  consentOcr: () => post("/auth/consent/ocr"),
  exportData: () => get<unknown>("/me/export"),
  deleteAccount: () => del("/me"),

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

  createImport(portfolioId: number, file: File) {
    const fd = new FormData();
    fd.append("file", file);
    return raw<ImportDraft>("POST", `/portfolios/${portfolioId}/imports`, undefined, fd);
  },
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
