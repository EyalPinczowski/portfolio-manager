/**
 * On-device OCR (Hebrew + English) with tesseract.js, loaded lazily so it is not part of the main bundle.
 * Worker, WASM core and language data are self-hosted under /tesseract/ (scripts/copy-ocr-assets.mjs), so no
 * third-party host is contacted. workerBlobURL:false keeps the worker same-origin (CSP worker-src 'self');
 * cacheMethod:"none" keeps tesseract from persisting anything in IndexedDB (the browser HTTP cache holds the
 * language files instead).
 */
import { canvasToPng, disposeCanvas, toMaskedCanvas } from "./image";
import { headerFractionFor, mergeScreenshots, parseScreenshotText, type LayoutChoice } from "./layouts";
import { preprocessCanvas } from "./preprocess";
import { scrubIdentifiers } from "./parse";
import type { ParsedRows } from "./types";

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
 * Read screenshots entirely in the browser and return parsed rows. Each image is decoded to a canvas, its top
 * (the layout's header fraction) is blanked, OCR runs, the canvas is zeroed, and only the parsed stock rows are
 * kept. The raw OCR text is scrubbed of identifier digit runs and never leaves this function. Several images of
 * one list are merged: a card in two screenshots is counted once (see mergeScreenshots).
 * With `auto` the generic header fraction is used for the pixels (the layout is only known after OCR); pick the
 * layout explicitly to get its own fraction.
 */
export async function readScreenshotsOnDevice(
  files: Blob[], opts: { layout?: LayoutChoice; onProgress?: (p: OcrProgress) => void } = {},
): Promise<ParsedRows> {
  const layout = opts.layout ?? "auto";
  const parts: ParsedRows[] = [];
  for (const file of files) {
    let canvas = await toMaskedCanvas(file, headerFractionFor(layout));
    try {
      canvas = preprocessCanvas(canvas);
      const text = await recognize(canvas, opts.onProgress);
      parts.push(parseScreenshotText(scrubIdentifiers(text), layout));
    } finally {
      disposeCanvas(canvas);
    }
  }
  return mergeScreenshots(parts);
}

/**
 * For the explicit "server reading" fallback: a PNG re-encoded from the masked canvas (no EXIF, header blanked,
 * oversized images downscaled). Sent as the raw request body.
 */
export async function prepareForServer(file: Blob, layout: LayoutChoice = "auto"): Promise<Blob> {
  const canvas = await toMaskedCanvas(file, headerFractionFor(layout));
  try {
    return await canvasToPng(canvas);
  } finally {
    disposeCanvas(canvas);
  }
}
