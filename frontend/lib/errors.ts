/** One 422 detail item. The server sends only `type`, `loc` and `msg` (never the input). */
export interface ValidationDetail { type?: string; loc: (string | number)[]; msg: string }

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public retryAfter?: number,
    /** Parsed JSON error body, when there was one (e.g. `{code: "turnstile_required", site_key}` or 422 `detail[]`). */
    public body?: unknown,
  ) {
    super(message);
  }

  /** The `code` field of the error body, if any. */
  get code(): string | undefined {
    const c = (this.body as { code?: unknown } | undefined)?.code;
    return typeof c === "string" ? c : undefined;
  }

  /** Turnstile challenge demanded by POST /auth/login (403). */
  get challenge(): { siteKey: string } | null {
    if (this.status !== 403 || this.code !== "turnstile_required") return null;
    const k = (this.body as { site_key?: unknown }).site_key;
    return typeof k === "string" && k !== "" ? { siteKey: k } : null;
  }

  /** Items of a 422 `detail` array. */
  get validation(): ValidationDetail[] {
    const d = (this.body as { detail?: unknown } | undefined)?.detail;
    if (this.status !== 422 || !Array.isArray(d)) return [];
    return d.filter((x): x is ValidationDetail => !!x && Array.isArray((x as ValidationDetail).loc))
      .map((x) => ({ type: x.type, loc: x.loc, msg: String(x.msg) }));
  }
}
