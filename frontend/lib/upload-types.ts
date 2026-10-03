/** Raster image types accepted for server-side reading (the OpenAPI schema cannot express a raw body type). */
export const RASTER_TYPES = ["image/png", "image/jpeg", "image/webp"] as const;
export type RasterType = (typeof RASTER_TYPES)[number];
