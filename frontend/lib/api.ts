import type { components } from "./api-schema";
import { RASTER_TYPES } from "./upload-types";
import { apiBase } from "./config";
import { ApiError } from "./errors";
import { mockRequest } from "./mock";
import { clearClientState } from "./session";
import { sanitizeRows } from "./import-rows";

export { ApiError };

/*
 * Types are DERIVED from the generated lib/api-schema.d.ts (npm run gen:api, from backend/openapi.json).
 * `Narrow` replaces fields that the OpenAPI schema types loosely (plain `string`, free-form objects) with the
 * precise union the backend actually returns.
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
export type HoldingCreate = S["HoldingCreate"];
export type Holding = Narrow<S["HoldingOut"], { asset_type: AssetType; market: MarketKey }>;

/** The typed "Why?" (every field after `summary` is optional: score cards cached before v1 lack them). */
export type Explanation = S["Explanation"];
export type SignalContribution = S["SignalContribution"];
export type ChartAnnotation = S["ChartAnnotation"];
export type ExplanationSource = S["ExplanationSource"];
export type SignalBreakdown = S["SignalBreakdownOut"];
export type ScoreCardDetail = Narrow<S["ScoreCardDetail"], { signals: SignalBreakdown[] }>;
export type ExitLevel = S["ExitLevel"];
export type ExitLevelsResult = S["ExitLevelsResult"];
export type ExitStatus = ExitLevelsResult["status"];
export type ExitReasonCode = NonNullable<ExitLevelsResult["reason_code"]>;
export type ScaleOutStep = S["ScaleOutStep"];
export type ScaleOutPlan = S["ScaleOutPlan"];
export type SizeGuidance = S["SizeGuidance"];
export type StopCandidate = S["StopCandidate"];
export type ReviewRow = S["ReviewRow"];
export type ReviewTotals = S["ReviewTotals"];
export type ExitReviewOut = S["ExitReviewOut"];
export type ExitReviewIn = S["ExitReviewIn"];
export type RiskPresetName = NonNullable<ExitReviewIn["risk"]>;
export type Postmortem = S["PostmortemOut"];
export type PostmortemStatus = Postmortem["status"];
export type PostmortemFinding = S["Finding"];
export type FindingKind = PostmortemFinding["kind"];
export type FindingStatus = PostmortemFinding["status"];
export type EvidenceRow = S["EvidenceRow"];
export type GapBreakdown = S["GapBreakdown"];
export type GapItem = S["GapItem"];
export type HoldingResult = S["HoldingResult"];
export type ExpectationPatch = Pick<S["PortfolioPatch"], "expected_return_pct" | "expected_return_horizon_months">;
export type Health = S["HealthOut"];
export type ChallengeRequired = S["ChallengeRequiredOut"];

export type ExposureItem = S["ExposureItem"];
export type Breach = S["Breach"];
export type XrayRaw = Narrow<
  S["XrayOut"],
  { concentration: { symbol: string; weight_pct: number }[]; home_bias: { israel_pct?: number } }
>;
export type HeatmapItem = S["HeatmapItem"];
export type XrayRuleResult = S["XrayRuleResult"];
export type XrayRuleName = XrayRuleResult["rule"];
export type XrayRuleOut = S["XrayRuleOut"];
export type XrayRulesPatch = S["XrayRulesPatch"];
export type TrackRecord = S["TrackRecordOut"];
export type TrackRow = S["TrackCallRow"];
export type TrackOutcome = TrackRow["outcome"];
export type BenchmarkAggregate = S["BenchmarkAggregate"];
export type GateProgress = S["GateProgress"];

/** What the user can pick for a row whose quantity changed. */
export type ChangeType = "buy" | "sell" | "deposit" | "withdrawal";
/** The server also uses `keep` for a holding that is not in the screenshots (full scope): no action. */
export type ProposedChangeType = S["ProposedChange"]["type"];
export type ImportScope = NonNullable<S["ImportRowsBody"]["scope"]>;
export type ImportFlag = NonNullable<S["ImportRowModel"]["flags"]>[number];
export type MatchCandidate = S["MatchCandidate"];
export type ImportRow = Narrow<S["ImportRowModel"], { flags: ImportFlag[]; candidates?: MatchCandidate[] }>;
export type ProposedChange = Narrow<S["ProposedChange"], { type: ProposedChangeType }>;
export type ImportDraft = Narrow<S["ImportDraftOut"], { rows: ImportRow[]; proposed_changes: ProposedChange[] }>;
export type LaunchGate = S["LaunchGateOut"];
export type SessionInfo = S["SessionOut"];
export type ImportRowsBody = Narrow<S["ImportRowsBody"], { rows: ImportRow[] }>;
export type PasswordBody = S["PasswordBody"];
export type SecurityHit = Narrow<S["SecurityHit"], { market: MarketKey }>;
export type PriceAlert = Narrow<S["AlertOut"], { op: "above" | "below" }>;
export type Notification = S["NotificationOut"];

export type AnalyzeOut = S["AnalyzeOut"];
export type PortfolioFit = S["PortfolioFit"];
export type FitStatus = PortfolioFit["status"];
export type NeedsInput = AnalyzeOut["needs_input"] extends (infer T)[] | undefined ? T : never;
export type ExposureCheck = S["ExposureCheck"];
export type ScoutReport = S["ScoutReport"];
export type ChartReport = S["ChartReport"];
export type SignalLine = S["SignalLine"];
export type MissingInput = S["MissingInput"];
export type SizeOut = S["SizeOut"];
export type AskOut = S["AskOut"];
export type PortfolioAskOut = S["PortfolioAskOut"];
export type NeedsHorizon = S["NeedsHorizon"];
export type AskMessage = S["MessageOut"];
export type AskConversation = S["ConversationOut"];
export type AskConversationDetail = S["ConversationDetailOut"];
export type CommitteeOut = S["CommitteeOut"];
export type LlmUsage = S["LlmUsageOut"];
export type CommitteeStance = S["RiskResponse"]["stance"];
export type SearchHistoryItem = S["SearchHistoryOut"];
export type WatchlistItem = S["WatchlistOut"];
/** Inputs of the analysis. No defaults anywhere: what is missing comes back in `needs_input`. */
export interface AnalyzeQuery { portfolioId?: number | null; amount?: number | null; currency?: "ILS" | "USD" | null; horizon?: Horizon | null; risk?: RiskPresetName | null }

export type FundSearchOut = S["FundSearchOut"];
export type FundSearchItem = S["FundSearchItem"];
export type FundDataStatus = FundSearchOut["data_status"];
export type FundDetail = S["FundDetailOut"];
export type FundReturn = S["FundReturn"];
export type FundMonth = S["FundMonth"];
export type FundHolding = S["FundHoldingOut"];
export type FundValueBasis = FundHolding["value_basis"];
export type Dividends = S["DividendsOut"];
export type UpcomingDividend = S["UpcomingDividend"];
export type SymbolDividendStatus = S["SymbolDividendStatus"];
export type DividendStatus = SymbolDividendStatus["data_status"];
export type IncomeEstimate = S["IncomeEstimate"];
export type IncomeLine = S["IncomeLine"];
/** Fund holdings use the symbol `GEMEL-<fund number>`. */
export const FUND_SYMBOL_PREFIX = "GEMEL-";
export const isFundHolding = (h: { symbol: string; fund?: unknown }): boolean => !!h.fund || h.symbol.startsWith(FUND_SYMBOL_PREFIX);

export type Settings = Narrow<S["SettingsOut"], { language: "he" | "en" }>;
export type SettingsPatch = S["SettingsPatch"];
export type QuietHours = S["QuietHours"];
export type IdeaAlertsFilter = S["IdeaAlertsFilter"];
export type IdeaAlerts = S["IdeaAlertsOut"];
export type WeeklyReview = S["WeeklyReviewOut"];
export type Weekday = WeeklyReview["day"];
export type TelegramStatus = S["TelegramStatusOut"];
export type LinkCode = S["LinkCodeOut"];
export type AdminUser = S["AdminUserOut"];
export type Invite = S["InviteOut"];
export type BuyIdeasIn = S["BuyIdeasIn"];
export type BuyIdeasOut = S["CandidatesOut"];
export type BuyCandidate = S["Candidate"];
export type SkippedItem = S["SkippedItem"];
export type SkipCode = SkippedItem["code"];

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
    let body: unknown;
    try { body = await res.json(); const d = (body as { detail?: unknown }).detail; msg = typeof d === "string" ? d : msg; } catch { /* ignore */ }
    const ra = Number(res.headers.get("Retry-After"));
    throw new ApiError(res.status, msg, Number.isFinite(ra) && ra > 0 ? ra : undefined, body);
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
  async login(b: { email: string; password: string; turnstile_token?: string }) {
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
  exportData: (password: string) => post<unknown>("/me/export", { password } satisfies PasswordBody),
  async deleteAccount(password: string) {
    await del("/me", { password } satisfies PasswordBody);
    csrfToken = null;
    await clearClientState();
  },

  sessions: () => get<SessionInfo[]>("/auth/sessions"),
  revokeSession: (id: number) => del(`/auth/sessions/${id}`),
  revokeOtherSessions: () => post("/auth/sessions/revoke-all"),
  launchGate: () => get<LaunchGate>("/launch-gate"),
  health: () => get<Health>("/health"),

  portfolios: () => get<Portfolio[]>("/portfolios"),
  createPortfolio: (b: { name: string; base_currency: string }) => post<Portfolio>("/portfolios", b),
  patchPortfolio: (id: number, b: Partial<Pick<Portfolio, "name" | "risk_filter">> & ExpectationPatch) =>
    patch<Portfolio>(`/portfolios/${id}`, b),
  /** Descriptive "why not my expected return" report. No dates = since the user started tracking. */
  postmortem: (id: number, q: { start?: string | null; end?: string | null } = {}) => {
    const qs = new URLSearchParams();
    if (q.start) qs.set("start", q.start);
    if (q.end) qs.set("end", q.end);
    const str = qs.toString();
    return get<Postmortem>(`/portfolios/${id}/post-mortem${str ? `?${str}` : ""}`);
  },
  summary: (id: number | "combined") => get<Summary>(`/portfolios/${id}/summary`),
  holdings: (id: number) => get<Holding[]>(`/portfolios/${id}/holdings`),
  /** Manual entry (no screenshot). 409 = already in the portfolio, 422 = invalid symbol or number, 429 = rate limit. */
  addHolding: (pid: number, b: HoldingCreate) => post<Holding>(`/portfolios/${pid}/holdings`, b),
  patchHolding: (pid: number, hid: number, b: Pick<S["HoldingPatch"], "horizon" | "quantity" | "manual_value_ils" | "manual_value_as_of" | "fund_name" | "track">) =>
    patch<Holding>(`/portfolios/${pid}/holdings/${hid}`, b),
  /** Upcoming ex/pay dates, per-symbol statuses and a 12-month income estimate (an estimate, never a promise). */
  dividends: (pid: number) => get<Dividends>(`/portfolios/${pid}/dividends`),
  /** Israeli funds (GemelNet). `data_status` says why a list is empty: no_data / unavailable / rate_limited. */
  searchFunds: (q: string) => get<FundSearchOut>(`/funds/search?q=${encodeURIComponent(q)}`),
  /** 404 = unknown fund number. */
  fund: (fundId: string) => get<FundDetail>(`/funds/${encodeURIComponent(fundId)}`),
  xray: (id: number) => get<XrayRaw>(`/portfolios/${id}/xray`),
  /** Toggle and threshold settings of the informational X-ray rules. `null` clears an override; 422 `threshold_out_of_bounds` carries min_pct/max_pct. */
  xrayRules: (id: number) => get<{ rules: XrayRuleOut[] }>(`/portfolios/${id}/xray-rules`),
  patchXrayRules: (id: number, b: XrayRulesPatch) => patch<{ rules: XrayRuleOut[] }>(`/portfolios/${id}/xray-rules`, b),
  /** Members-only record of the app's own ended calls (never a member's portfolio). */
  trackRecord: () => get<TrackRecord>("/track-record"),
  heatmap: (id: number) => get<HeatmapItem[]>(`/portfolios/${id}/heatmap`),
  riskPresets: () => get<RiskPreset[] | Record<string, RiskFilter>>("/risk/presets"),

  /** Server-reading path: the image goes up as the raw request body (png/jpeg/webp), not multipart. */
  createImport(portfolioId: number, image: Blob) {
    if (!(RASTER_TYPES as readonly string[]).includes(image.type)) throw new ApiError(415, "Unsupported image type");
    return raw<ImportDraft>("POST", `/portfolios/${portfolioId}/imports`, { image });
  },
  /** On-device path: rows parsed in the browser; the image never leaves the device. */
  importRows: (portfolioId: number, rows: ImportRow[], scope: ImportScope = "partial") =>
    post<ImportDraft>(`/portfolios/${portfolioId}/imports/rows`, { rows: sanitizeRows(rows), scope } satisfies ImportRowsBody),
  getImport: (id: number) => get<ImportDraft>(`/imports/${id}`),
  patchImport: (id: number, b: { rows?: ImportRow[]; proposed_changes?: ProposedChange[]; scope?: ImportScope }) =>
    patch<ImportDraft>(`/imports/${id}`, b.rows ? { ...b, rows: sanitizeRows(b.rows) } : b),
  confirmImport: (id: number) => post<ImportDraft>(`/imports/${id}/confirm`),
  searchSecurities: (q: string) => get<SecurityHit[]>(`/securities/search?q=${encodeURIComponent(q)}`),

  scorecard: (hid: number) => get<ScoreCardDetail>(`/holdings/${hid}/scorecard`),
  /** `horizon` overrides the holding's own as a what-if (not saved). No horizon at all -> status `needs_horizon`. */
  exitLevels: (hid: number, q: { horizon?: Horizon | null; risk?: RiskPresetName | null } = {}) => {
    const qs = new URLSearchParams();
    if (q.horizon) qs.set("horizon", q.horizon);
    if (q.risk) qs.set("risk", q.risk);
    const str = qs.toString();
    return get<ExitLevelsResult>(`/holdings/${hid}/exit-levels${str ? `?${str}` : ""}`);
  },
  /** A what-if review: `horizon` / `risk` override every holding's own for this call only. */
  exitReview: (pid: number, b: ExitReviewIn = {}) => post<ExitReviewOut>(`/portfolios/${pid}/exit-review`, b),
  /** 404 = unknown symbol. Nothing about the result is saved anywhere on the client. */
  analyze: (symbol: string, q: AnalyzeQuery = {}) => {
    const qs = new URLSearchParams();
    if (q.portfolioId) qs.set("portfolio_id", String(q.portfolioId));
    if (q.amount !== null && q.amount !== undefined) qs.set("amount", String(q.amount));
    if (q.currency) qs.set("currency", q.currency);
    if (q.horizon) qs.set("horizon", q.horizon);
    if (q.risk) qs.set("risk", q.risk);
    const str = qs.toString();
    return get<AnalyzeOut>(`/analyze/${encodeURIComponent(symbol)}${str ? `?${str}` : ""}`);
  },
  askAboutStock: (symbol: string, b: { question: string; notes?: string | null }) =>
    post<AskOut>(`/analyze/${encodeURIComponent(symbol)}/ask`, b),
  /** Investment Committee for any ticker: public data only, no verdict. 429 = per-user hourly or daily limit. */
  committee: (symbol: string) => post<CommitteeOut>(`/analyze/${encodeURIComponent(symbol)}/committee`),
  /** Ask my portfolio: read-only tools over the caller's own data. 429 = per-user hourly limit, 404 = unknown conversation or portfolio. */
  askPortfolio: (b: { question: string; conversation_id?: number | null; portfolio_id?: number | null }) => post<PortfolioAskOut>("/ask", b),
  askConversations: () => get<AskConversation[]>("/ask/conversations"),
  askConversation: (id: number) => get<AskConversationDetail>(`/ask/conversations/${id}`),
  deleteAskConversation: (id: number) => del(`/ask/conversations/${id}`),
  searchHistory: () => get<SearchHistoryItem[]>("/search-history"),
  removeSearch: (symbol: string) => del(`/search-history/${encodeURIComponent(symbol)}`),
  clearSearchHistory: () => del("/search-history"),
  watchlist: () => get<WatchlistItem[]>("/watchlist"),
  addToWatchlist: (symbol: string) => post<WatchlistItem>("/watchlist", { symbol }),
  removeFromWatchlist: (symbol: string) => del(`/watchlist/${encodeURIComponent(symbol)}`),
  settings: () => get<Settings>("/settings"),
  /** Only the fields sent change. `idea_alerts: null` clears the Buy-alerts filter, `quiet_hours: null` clears quiet hours. */
  patchSettings: (b: SettingsPatch) => patch<Settings>("/settings", b),
  telegramStatus: () => get<TelegramStatus>("/telegram/status"),
  /** One-time code, valid for `ttl_minutes`. 503 = the server has no bot configured, 429 = too many codes. */
  telegramLinkCode: () => post<LinkCode>("/telegram/link-code"),
  telegramUnlink: () => del("/telegram/link"),
  /** Admin only: everything under /admin answers 403 to a normal member. */
  adminUsers: () => get<AdminUser[]>("/admin/users"),
  adminDisableUser: (id: number) => post<AdminUser>(`/admin/users/${id}/disable`),
  adminEnableUser: (id: number) => post<AdminUser>(`/admin/users/${id}/enable`),
  adminLlmUsage: () => get<LlmUsage>("/admin/llm-usage"),
  adminInvites: () => get<Invite[]>("/admin/invites"),
  adminCreateInvite: (days?: number | null) => post<Invite>("/admin/invites", { days: days ?? null }),
  adminRevokeInvite: (code: string) => post("/admin/invites/revoke", { code }),
  /** Neutral candidates for new money from cached scores. Every input is required: no defaults. */
  buyIdeas: (pid: number, b: BuyIdeasIn) => post<BuyIdeasOut>(`/portfolios/${pid}/buy-ideas`, b),
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
