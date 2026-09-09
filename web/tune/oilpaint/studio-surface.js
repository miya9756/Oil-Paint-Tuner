// A bounded, transferable view of the actual raster buffers. The renderer's shared
// surface preparation preserves its canvas weave and cavity shading. Gradients are
// measured at the original resolution BEFORE subsampling, so stroke relief stays put.
import { AMBIENT, lightingField } from './render.js';
import { linearToSrgb } from './image.js';

export function studioSurface(cache, cfg, maxSide = 1400) {
  if (!cache.height || !cfg.impasto) return null;
  const { out, height, cover, w, h } = cache;
  const { fld, occl } = lightingField(height, cover, h, w, cfg.canvas_weave, cfg.occlusion);
  const scale = Math.min(1, maxSide / Math.max(w, h));
  const sw = Math.max(1, Math.round(w * scale)), sh = Math.max(1, Math.round(h * scale));
  const color = new Uint8Array(sw * sh * 4), surface = new Float32Array(sw * sh * 4);
  for (let y = 0; y < sh; y++) {
    const sy = Math.min(h - 1, Math.floor((y + 0.5) * h / sh));
    for (let x = 0; x < sw; x++) {
      const sx = Math.min(w - 1, Math.floor((x + 0.5) * w / sw));
      const p = sy * w + sx, d = (y * sw + x) * 4;
      const gx = (fld[sy * w + Math.min(w - 1, sx + 1)] - fld[sy * w + Math.max(0, sx - 1)]) * 0.5;
      const gy = (fld[Math.min(h - 1, sy + 1) * w + sx] - fld[Math.max(0, sy - 1) * w + sx]) * 0.5;
      const nx = -gx * cfg.impasto_depth, ny = -gy * cfg.impasto_depth;
      const norm = 1 / Math.sqrt(nx * nx + ny * ny + 1);
      surface[d] = nx * norm; surface[d + 1] = ny * norm; surface[d + 2] = norm;
      surface[d + 3] = occl ? occl[p] : 0;
      for (let c = 0; c < 3; c++) {
        const v = cfg.linear ? linearToSrgb(out[p * 3 + c]) : out[p * 3 + c];
        color[d + c] = Math.round(Math.max(0, Math.min(1, v)) * 255);
      }
      color[d + 3] = 255;
    }
  }
  return { color: color.buffer, surface: surface.buffer, w: sw, h: sh, ambient: AMBIENT };
}
