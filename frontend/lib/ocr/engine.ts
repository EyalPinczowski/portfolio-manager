/**
 * On-device OCR (Hebrew + English) with tesseract.js, loaded lazily so it is not part of the main bundle.
 * Worker, WASM core and language data are self-hosted under /tesseract/ (scripts/copy-ocr-assets.mjs), so no
 * third-party host is contacted. workerBlobURL:false keeps the worker same-origin (CSP worker-src 'self');
 * cacheMethod:"none" keeps tesseract from persisting anything in IndexedDB (the browser HTTP cache holds the
 * language files instead).
 */
import { canvasToPng, disposeCanvas, toMaskedCanvas } from "./image";
import { parseOcrText, scrubIdentifiers } from "./parse";
import type { ImportRow } from "../api";

export interface OcrProgress { status: string; progress: number }

export const OCR_ASSET_BASE = "/tesseract";

async function recognize(canvas: HTMLCanvasElement, onProgress?: (p: OcrProgress) => void): Promise<string> {
  const { createWorker, OEM } = await import("tesseract.js");
  const worker = await createWorker(["heb", "eng"], OEM.LSTM_ONLY, {
    workerPath: `${OCR_ASSET_BASE}/worker.min.js`,
    corePath: `${OCR_ASSET_BASE}/core`,
    langPath: `${OCR_ASSET_BASE}/lang`,
    workerBlobURL: false,
    cacheMethod: "none",
    gzip: true,
    logger: (m) => onProgress?.({ status: m.status, progress: m.progress }),
  });
  try {
    await worker.setParameters({ preserve_interword_spaces: "1" });
    const { data } = await worker.recognize(canvas);
    return data.text;
  } finally {
    await worker.terminate();
  }
}

/**
 * Read a screenshot entirely in the browser and return parsed rows. The image is decoded to a canvas, its top
 * is blanked, OCR runs, the canvas is zeroed, and only the parsed stock rows are returned. The raw OCR text is
 * scrubbed of identifier digit runs and never leaves this function.
 */
export async function readScreenshotOnDevice(file: Blob, onProgress?: (p: OcrProgress) => void): Promise<ImportRow[]> {
  const canvas = await toMaskedCanvas(file);
  try {
    const text = await recognize(canvas, onProgress);
    return parseOcrText(scrubIdentifiers(text));
  } finally {
    disposeCanvas(canvas);
  }
}

/**
 * For the explicit "server reading" fallback: a PNG re-encoded from the masked canvas (no EXIF, header blanked,
 * oversized images downscaled). Sent as the raw request body.
 */
export async function prepareForServer(file: Blob): Promise<Blob> {
  const canvas = await toMaskedCanvas(file);
  try {
    return await canvasToPng(canvas);
  } finally {
    disposeCanvas(canvas);
  }
}
