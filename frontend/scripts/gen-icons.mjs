// Generates simple PNG icons (no dependencies). Run: node scripts/gen-icons.mjs
import { deflateSync } from "node:zlib";
import { writeFileSync } from "node:fs";

function crc32(buf) {
  let c, crc = ~0;
  for (let n = 0; n < buf.length; n++) {
    c = (crc ^ buf[n]) & 0xff;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    crc = (crc >>> 8) ^ c;
  }
  return ~crc >>> 0;
}
function chunk(type, data) {
  const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
  const td = Buffer.concat([Buffer.from(type), data]);
  const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(td));
  return Buffer.concat([len, td, crc]);
}
function png(size, maskable) {
  const px = Buffer.alloc(size * (size * 4 + 1));
  const pad = maskable ? 0.2 : 0.1; // maskable keeps art inside the safe zone
  const inner = size * (1 - 2 * pad);
  const bars = [[0.18, 0.55], [0.42, 0.4], [0.66, 0.5]]; // x fraction, height fraction
  for (let y = 0; y < size; y++) {
    const row = y * (size * 4 + 1);
    px[row] = 0;
    for (let x = 0; x < size; x++) {
      let rgb = [29, 78, 216];
      const ux = (x - size * pad) / inner, uy = (y - size * pad) / inner;
      if (ux >= 0 && ux <= 1 && uy >= 0 && uy <= 1) {
        for (const [bx, bh] of bars) {
          if (ux >= bx && ux <= bx + 0.16 && uy >= 1 - bh && uy <= 1) rgb = [255, 255, 255];
        }
        const lineY = 0.5 - ux * 0.4;
        if (Math.abs(uy - lineY) < 0.045) rgb = [74, 222, 128];
      }
      const o = row + 1 + x * 4;
      px[o] = rgb[0]; px[o + 1] = rgb[1]; px[o + 2] = rgb[2]; px[o + 3] = 255;
    }
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0); ihdr.writeUInt32BE(size, 4); ihdr[8] = 8; ihdr[9] = 6;
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk("IHDR", ihdr), chunk("IDAT", deflateSync(px)), chunk("IEND", Buffer.alloc(0)),
  ]);
}
writeFileSync("public/icons/icon-192.png", png(192, false));
writeFileSync("public/icons/icon-512.png", png(512, false));
writeFileSync("public/icons/icon-maskable-512.png", png(512, true));
