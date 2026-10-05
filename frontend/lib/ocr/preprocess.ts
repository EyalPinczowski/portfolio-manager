/**
 * Cheap canvas clean-up before on-device OCR (mirror of backend/app/importer/preprocess.py): invert dark-mode
 * screens, upscale narrow images 2x, binarise with Otsu only when the histogram is clearly two-toned.
 * The numeric-only second pass (digit whitelist) is not done: the on-device layouts work on text, not columns.
 */
export const DARK_MEAN_LUMINANCE = 110;
export const UPSCALE_BELOW_WIDTH = 1000;
export const UPSCALE_FACTOR = 2;
export const MAX_SIDE_PX = 3000;
export const OTSU_MIN_SEPARATION = 0.5;
export const NUMERIC_WHITELIST = "0123456789.,-%₪$";

/** 256-bin luminance histogram of RGBA pixel data. */
export function lumaHistogram(rgba: ArrayLike<number>): number[] {
  const hist = new Array<number>(256).fill(0);
  for (let i = 0; i + 3 < rgba.length; i += 4) {
    hist[Math.round(0.299 * rgba[i] + 0.587 * rgba[i + 1] + 0.114 * rgba[i + 2])]++;
  }
  return hist;
}

export function meanOfHistogram(hist: number[]): number {
  const total = hist.reduce((a, b) => a + b, 0);
  return total === 0 ? 255 : hist.reduce((a, h, i) => a + i * h, 0) / total;
}

export function otsu(hist: number[]): { threshold: number; separation: number } {
  const total = hist.reduce((a, b) => a + b, 0);
  if (total === 0) return { threshold: 128, separation: 0 };
  const sumAll = hist.reduce((a, h, i) => a + i * h, 0);
  const meanAll = sumAll / total;
  const varTotal = hist.reduce((a, h, i) => a + h * (i - meanAll) ** 2, 0) / total;
  let best = -1, bestT = 128, w0 = 0, sum0 = 0;
  for (let t = 0; t < 256; t++) {
    w0 += hist[t];
    if (w0 === 0) continue;
    const w1 = total - w0;
    if (w1 === 0) break;
    sum0 += t * hist[t];
    const between = (w0 / total) * (w1 / total) * (sum0 / w0 - (sumAll - sum0) / w1) ** 2;
    if (between > best) { best = between; bestT = t; }
  }
  return { threshold: bestT, separation: varTotal > 0 ? Math.max(0, Math.min(1, best / varTotal)) : 0 };
}

export const shouldInvert = (hist: number[]): boolean => meanOfHistogram(hist) < DARK_MEAN_LUMINANCE;
export const shouldUpscale = (w: number, h: number): boolean => w < UPSCALE_BELOW_WIDTH && Math.max(w, h) * UPSCALE_FACTOR <= MAX_SIDE_PX;

/** Returns a (possibly new) canvas ready for OCR; when a new one is returned the input is zeroed. */
export function preprocessCanvas(canvas: HTMLCanvasElement): HTMLCanvasElement {
  const ctx = canvas.getContext("2d");
  if (!ctx || canvas.width === 0 || canvas.height === 0) return canvas;
  let out = canvas;
  if (shouldUpscale(canvas.width, canvas.height)) {
    out = document.createElement("canvas");
    out.width = canvas.width * UPSCALE_FACTOR;
    out.height = canvas.height * UPSCALE_FACTOR;
    const octx = out.getContext("2d");
    if (!octx) return canvas;
    octx.imageSmoothingEnabled = true;
    octx.drawImage(canvas, 0, 0, out.width, out.height);
    canvas.width = 0;
    canvas.height = 0;
  }
  const c = out.getContext("2d");
  if (!c) return out;
  const img = c.getImageData(0, 0, out.width, out.height);
  const d = img.data;
  let hist = lumaHistogram(d);
  const invert = shouldInvert(hist);
  const { threshold, separation } = otsu(invert ? hist.slice().reverse() : hist);
  const binarise = separation >= OTSU_MIN_SEPARATION;
  for (let i = 0; i + 3 < d.length; i += 4) {
    let g = Math.round(0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2]);
    if (invert) g = 255 - g;
    if (binarise) g = g > threshold ? 255 : 0;
    d[i] = d[i + 1] = d[i + 2] = g;
    d[i + 3] = 255;
  }
  hist = [];
  c.putImageData(img, 0, 0);
  return out;
}
