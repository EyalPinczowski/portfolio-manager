/* Mock settings, Telegram and admin responses (NEXT_PUBLIC_API_MOCK=1). Shapes follow backend/openapi.json. */
import type { AdminUser, IdeaAlertsFilter, Invite, LinkCode, Settings, TelegramStatus } from "./api";
import { ApiError } from "./errors";

const AS_OF = "2026-10-03T08:55:00Z";
const BOT = "PortfolioManagerBot";

const fresh = (): Settings => ({
  language: "he", theme: "system", main_currency: "ILS", number_format: "full", week_start_day: "sunday",
  price_alerts_enabled: true,
  weekly_review: { enabled: true, day: "sunday", time: "20:00", timezone: "Asia/Jerusalem" },
  quiet_hours: null, idea_alerts: { state: "not_set", filter: null }, telegram_linked: false, updated_at: null,
});
let settings: Settings = fresh();
let linked = false;
let users: AdminUser[] = [
  { id: 1, email: "demo@example.com", created_at: "2026-09-01T09:00:00Z", last_seen_at: AS_OF, is_admin: true, active: true, telegram_linked: false },
  { id: 2, email: "dana@example.com", created_at: "2026-09-10T09:00:00Z", last_seen_at: "2026-10-01T18:30:00Z", is_admin: false, active: true, telegram_linked: true },
  { id: 3, email: "old@example.com", created_at: "2026-09-12T09:00:00Z", last_seen_at: null, is_admin: false, active: false, telegram_linked: false },
];
let invites: Invite[] = [
  { code: "INV-AAAA1111", status: "unused", created_at: "2026-10-01T09:00:00Z", expires_at: "2026-10-15T09:00:00Z", created_by_me: true },
  { code: "INV-BBBB2222", status: "used", created_at: "2026-09-20T09:00:00Z", expires_at: "2026-10-04T09:00:00Z", created_by_me: true },
  { code: "INV-CCCC3333", status: "expired", created_at: "2026-08-01T09:00:00Z", expires_at: "2026-08-15T09:00:00Z", created_by_me: false },
];
let nextInvite = 4;

/** Test helper: back to the first-run state. */
export function resetMockSettings(): void {
  settings = fresh(); linked = false;
  users = users.map((u) => (u.id === 3 ? u : { ...u, active: true }));
}

const unprocessable = (field: string, msg: string) =>
  new ApiError(422, "Validation error", undefined, { detail: [{ type: "value_error", loc: ["body", field], msg }] });
const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;
const ENUMS: Record<string, readonly string[]> = {
  language: ["he", "en"], theme: ["system", "light", "dark"], main_currency: ["ILS", "USD"], number_format: ["full", "compact"], week_start_day: ["sunday", "monday"],
};
const DAYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"];
const PRESETS = ["very_conservative", "conservative", "balanced", "balanced_aggressive", "aggressive", "very_aggressive"];

function validQuiet(q: unknown, field: string): void {
  const x = q as { start?: unknown; end?: unknown };
  if (!x || typeof x.start !== "string" || typeof x.end !== "string" || !HHMM.test(x.start) || !HHMM.test(x.end) || x.start === x.end) throw unprocessable(field, "Invalid quiet hours");
}

function validIdea(f: unknown): IdeaAlertsFilter {
  const x = f as Partial<IdeaAlertsFilter>;
  const bad = (k: string) => { throw unprocessable(`idea_alerts.${k}`, "Invalid value"); };
  if (typeof x.min_confidence !== "number" || x.min_confidence < 0 || x.min_confidence > 1) bad("min_confidence");
  if (!["1w", "1m", "3m", "6m", "1y"].includes(String(x.horizon))) bad("horizon");
  if (!PRESETS.includes(String(x.risk_preset))) bad("risk_preset");
  if (!Array.isArray(x.markets) || x.markets.length < 1 || x.markets.some((m) => !["US", "TASE", "CRYPTO"].includes(m))) bad("markets");
  if (!Array.isArray(x.asset_types) || x.asset_types.length < 1) bad("asset_types");
  if (!Number.isInteger(x.max_per_day) || (x.max_per_day as number) < 1 || (x.max_per_day as number) > 20) bad("max_per_day");
  if (!("quiet_hours" in x)) bad("quiet_hours");
  if (x.quiet_hours !== null) validQuiet(x.quiet_hours, "idea_alerts.quiet_hours");
  return x as IdeaAlertsFilter;
}

function patchSettings(b: Record<string, unknown>): Settings {
  const next: Settings = { ...settings };
  for (const [k, v] of Object.entries(b)) {
    if (k in ENUMS) {
      if (v === null || !ENUMS[k].includes(String(v))) throw unprocessable(k, "Invalid value");
      (next as unknown as Record<string, unknown>)[k] = v;
    } else if (k === "price_alerts_enabled") {
      if (typeof v !== "boolean") throw unprocessable(k, "Invalid value");
      next.price_alerts_enabled = v;
    } else if (k === "weekly_review") {
      const w = (v ?? null) as Record<string, unknown> | null;
      if (w === null) throw unprocessable(k, "Invalid value");
      if ("enabled" in w && typeof w.enabled !== "boolean") throw unprocessable(k, "Invalid value");
      if ("day" in w && !DAYS.includes(String(w.day))) throw unprocessable(k, "Invalid value");
      if ("time" in w && !HHMM.test(String(w.time))) throw unprocessable(k, "Invalid value");
      next.weekly_review = { ...next.weekly_review, ...(w as object) };
    } else if (k === "quiet_hours") {
      if (v !== null) validQuiet(v, k);
      next.quiet_hours = v as Settings["quiet_hours"];
    } else if (k === "idea_alerts") {
      next.idea_alerts = v === null ? { state: "not_set", filter: null } : { state: "set", filter: validIdea(v) };
    } else throw unprocessable(k, "Extra inputs are not permitted");
  }
  settings = { ...next, telegram_linked: linked, updated_at: AS_OF };
  return settings;
}

const NOT_HANDLED = Symbol("not handled");
export { NOT_HANDLED };

/** `scenario` is the dev/test switch from localStorage "pm.mock": "member" = not an admin, "no-bot" = no Telegram bot configured. */
export function mockSettingsRequest(method: string, p: string, b: Record<string, unknown>, scenario: string | null): unknown {
  let m: RegExpMatchArray | null;
  if (p === "/settings") return method === "PATCH" ? patchSettings(b) : { ...settings, telegram_linked: linked };
  if (p === "/telegram/status") {
    const s: TelegramStatus = { configured: scenario !== "no-bot", webhook_ready: scenario !== "no-bot", linked, bot_username: scenario === "no-bot" ? null : BOT };
    return s;
  }
  if (p === "/telegram/link-code" && method === "POST") {
    if (scenario === "no-bot") throw new ApiError(503, "Telegram is not set up on this server.");
    const code: LinkCode = {
      code: "K7Q2M9XP", command: "/start K7Q2M9XP", ttl_minutes: 15,
      expires_at: new Date(Date.now() + 15 * 60_000).toISOString(), deep_link: `https://t.me/${BOT}?start=K7Q2M9XP`,
    };
    return code;
  }
  if (p === "/telegram/link" && method === "DELETE") { linked = false; settings = { ...settings, telegram_linked: false }; return undefined; }
  if (p.startsWith("/admin/")) {
    if (scenario === "member") throw new ApiError(403, "Forbidden");
    if (p === "/admin/users") return users;
    if ((m = p.match(/^\/admin\/users\/(\d+)\/(disable|enable)$/)) && method === "POST") {
      const u = users.find((x) => x.id === Number(m![1]));
      if (!u) throw new ApiError(404, "Not found");
      if (u.is_admin && m[2] === "disable") throw new ApiError(400, "You cannot deactivate an admin");
      const out = { ...u, active: m[2] === "enable" };
      users = users.map((x) => (x.id === u.id ? out : x));
      return out;
    }
    if (p === "/admin/invites" && method === "POST") {
      const days = typeof b.days === "number" ? b.days : 14;
      const inv: Invite = { code: `INV-NEW${String(nextInvite++).padStart(5, "0")}`, status: "unused", created_at: AS_OF, expires_at: new Date(Date.parse(AS_OF) + days * 86_400_000).toISOString(), created_by_me: true };
      invites = [inv, ...invites];
      return inv;
    }
    if (p === "/admin/invites") return invites;
    if (p === "/admin/invites/revoke" && method === "POST") {
      const i = invites.find((x) => x.code === b.code);
      if (!i) throw new ApiError(404, "Not found");
      invites = invites.filter((x) => x.code !== b.code);
      return undefined;
    }
  }
  return NOT_HANDLED;
}
