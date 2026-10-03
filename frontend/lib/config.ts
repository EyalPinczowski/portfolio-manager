/** Frontend constants. Thresholds live here, never inline (CLAUDE.md conventions). */

/** quantity x price must match value within this fraction (mirrors backend `import_value_tolerance`). */
export const IMPORT_VALUE_TOLERANCE = 0.02;

/** On-device OCR: share of the image height (from the top) that is blanked before reading (account header). */
export const OCR_TOP_MASK_FRACTION = 0.12;

/**
 * Digit runs at least this long are identifiers. Runs of 9+ digits (accounts, phones, IDs) are always dropped from
 * the OCR text. 6-8 digit runs are TASE security numbers (the backend matches on them), so they are only kept as
 * `tase_number`, never in a row's name.
 */
export const OCR_DROP_DIGITS_MIN = 9;
export const OCR_ID_DIGITS_MIN = 6;

/** Longest side (px) of the canvas used for OCR and for the server-upload fallback. */
export const OCR_MAX_SIDE_PX = 2600;

/** Largest screenshot the UI accepts (the API enforces its own limit and answers 413). */
export const MAX_UPLOAD_BYTES = 8 * 1024 * 1024;

/** Base URL of the API. Empty = same origin (Pages Function / reverse proxy / dev rewrite). No trailing slash. */
export const apiBase = (): string => (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/+$/, "");
