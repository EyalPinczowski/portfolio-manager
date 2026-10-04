/**
 * Canvas handling for screenshots. The image only ever lives in memory: no localStorage, no IndexedDB, no
 * long-lived blob URL (a temporary object URL, if the browser needs one, is revoked in `finally`).
 */
import { OCR_MAX_SIDE_PX, OCR_TOP_MASK_FRACTION } from "../config";

export const topMaskHeight = (height: number, fraction: number = OCR_TOP_MASK_FRACTION): number =>
  Math.min(height, Math.max(0, Math.round(height * fraction)));

export function fitSize(w: number, h: number, maxSide: number = OCR_MAX_SIDE_PX): { width: number; height: number } {
  const k = Math.min(1, maxSide / Math.max(w, h));
  return { width: Math.max(1, Math.round(w * k)), height: Math.max(1, Math.round(h * k)) };
}

async function decode(file: Blob): Promise<{ source: CanvasImageSource; width: number; height: number; close: () => void }> {
  if (typeof createImageBitmap === "function") {
    try {
      const bmp = await createImageBitmap(file);
      return { source: bmp, width: bmp.width, height: bmp.height, close: () => bmp.close() };
    } catch { /* fall through to <img> */ }
  }
  const url = URL.createObjectURL(file);
  try {
    const img = new Image();
    img.decoding = "async";
    img.src = url;
    await img.decode();
    return { source: img, width: img.naturalWidth, height: img.naturalHeight, close: () => undefined };
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** Decode into a canvas (downscaled to OCR_MAX_SIDE_PX) with the top `fraction` (per layout, see OCR_LAYOUTS) painted over. */
export async function toMaskedCanvas(file: Blob, fraction: number = OCR_TOP_MASK_FRACTION): Promise<HTMLCanvasElement> {
  const img = await decode(file);
  try {
    const { width, height } = fitSize(img.width, img.height);
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("canvas unavailable");
    ctx.drawImage(img.source, 0, 0, width, height);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, width, topMaskHeight(height, fraction));
    return canvas;
  } finally {
    img.close();
  }
}

/** Re-encode the (masked) canvas as PNG. This also drops EXIF/GPS metadata from the original file. */
export function canvasToPng(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("encode failed"))), "image/png");
  });
}

/** Release the pixel buffer. */
export function disposeCanvas(canvas: HTMLCanvasElement): void {
  canvas.width = 0;
  canvas.height = 0;
}
