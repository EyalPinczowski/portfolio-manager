/**
 * HAND-WRITTEN types for contract items that are in docs/phase-1.5-spec.md ("Contract changes") but are NOT yet
 * in backend/openapi.json. Everything else in lib/api.ts is derived from the generated lib/api-schema.d.ts
 * (`npm run gen:api`).
 *
 * When the backend lands one of these and openapi.json contains it, delete it here and use the generated type.
 * Types in lib/api.ts intersect with these, so a field that appears in both places never conflicts.
 * Pending list (spec table row -> item):
 * Already in openapi.json (so generated, no longer pending): Summary.week_start, Summary.fx_stale,
 * ImportRow.flags enum (incl. currency_changed, low_confidence_match) and ImportRow.candidates.
 * Still pending:
 *   Upload (device) POST /portfolios/{id}/imports/rows  { rows: ImportRow[] }  -> ImportDraft
 *   Upload (server) POST /portfolios/{id}/imports       raw image body (not multipart) -> ImportDraft
 *   Draft expiry    ImportDraft.expires_at
 *   Import flags    ImportRow.flags may include currency_changed | low_confidence_match
 *   Delete/export   DELETE /me {password}; POST /me/export {password}; wrong password -> 403
 *   Sessions        GET /auth/sessions, DELETE /auth/sessions/{id}, POST /auth/sessions/revoke-all
 *   Rate limits     429 + Retry-After on login/signup/upload
 *   Launch gate     GET /launch-gate
 */
export interface ImportDraftPending {
  /** ISO datetime; the server deletes an unconfirmed draft then (24 h after creation). */
  expires_at: string;
}

export interface ImportRowsBody {
  rows: import("./api").ImportRow[];
}

export interface PasswordBody { password: string }

export interface SessionInfo {
  id: number;
  created_at: string;
  last_seen_at: string;
  current: boolean;
}

export interface LaunchGate {
  open: boolean;
  reasons: string[];
}

export const RASTER_TYPES = ["image/png", "image/jpeg", "image/webp"] as const;
export type RasterType = (typeof RASTER_TYPES)[number];
