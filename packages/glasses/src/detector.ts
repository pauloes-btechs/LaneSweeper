/**
 * LocalCandidateDetector — a free, dependency-free, on-device heuristic for "possible glass/debris ahead".
 *
 * Method (explainable, deterministic): inside the lane region of interest, find pixels much brighter than the
 * local lane surface, group them into connected components on a downsampled grid, and count SMALL, COMPACT
 * specks (glass glints/fragments). Elongated streaks (wet-road glare) and large blobs (markings, foil, bags,
 * vehicles) are counted separately and do not raise the glass score.
 *
 * Limits (stated, not hidden): it sees pixels, not materials; it has no depth, so it never estimates distance;
 * it is tuned on synthetic fixtures and MUST be re-validated on real footage. Output is a candidate for a
 * conservative warning and for optional cloud verification — never proof that a lane is clear or hazardous.
 */
import type { Frame } from './adapter';

export interface Detection {
  label: 'possible_glass' | 'possible_debris' | 'none';
  score: number;              // 0..1 heuristic confidence, NOT a probability
  specks: number;             // small compact bright components
  blobs: number;              // large components (objects, markings, foil)
  streaks: number;            // elongated components (glare)
  box: { x: number; y: number; w: number; h: number } | null;   // image-space area of interest
  model: string;
  ms: number;
}

export interface DetectorOptions {
  step?: number;              // downsample factor
  roiTop?: number;            // fraction of height where the lane ROI starts
  laneLeft?: number;          // fraction of width at the bottom edge
  laneRight?: number;
  brightK?: number;           // brightness threshold = mean + k * std
  horizon?: number;           // fraction of height where the lane edges converge (camera mounting dependent)
}

const MODEL = 'lk-local-heuristic-v1';

export function detect(frame: Frame, opts: DetectorOptions = {}): Detection {
  const t0 = typeof performance !== 'undefined' ? performance.now() : Date.now();
  const step = opts.step ?? 2;
  const roiTop = opts.roiTop ?? 0.45;
  const left = opts.laneLeft ?? 0.2;    // lane interior at the bottom edge, inside the buffer stripes
  const right = opts.laneRight ?? 0.8;
  const W = Math.floor(frame.width / step);
  const H = Math.floor(frame.height / step);
  const y0 = Math.floor(H * roiTop);
  const lum = new Float32Array(W * H);
  const inRoi = new Uint8Array(W * H);
  let sum = 0;
  let sum2 = 0;
  let n = 0;
  for (let y = y0; y < H; y++) {
    // perspective ROI: edges converge on a vanishing point at the horizon, like the lane itself
    const hz = H * (opts.horizon ?? 0.36);
    const t = Math.max(0, (y - hz) / Math.max(1, H - hz));
    const lx = Math.floor(W * (0.5 - (0.5 - left) * t));
    const rx = Math.ceil(W * (0.5 + (right - 0.5) * t));
    for (let x = lx; x < rx; x++) {
      const i = ((y * step) * frame.width + x * step) * 4;
      const l = 0.2126 * (frame.data[i] ?? 0) + 0.7152 * (frame.data[i + 1] ?? 0) + 0.0722 * (frame.data[i + 2] ?? 0);
      lum[y * W + x] = l;
      inRoi[y * W + x] = 1;
      sum += l;
      sum2 += l * l;
      n++;
    }
  }
  const mean = sum / Math.max(1, n);
  const std = Math.sqrt(Math.max(0, sum2 / Math.max(1, n) - mean * mean));
  const thr = mean + (opts.brightK ?? 2.2) * Math.max(std, 8);
  const mask = new Uint8Array(W * H);
  for (let k = 0; k < W * H; k++) if (inRoi[k] && (lum[k] ?? 0) > thr) mask[k] = 1;

  let specks = 0;
  let blobs = 0;
  let streaks = 0;
  let minX = W, minY = H, maxX = -1, maxY = -1;
  const blobBoxes: Array<[number, number, number, number]> = [];
  const speckBoxes: Array<[number, number, number, number]> = [];
  const seen = new Uint8Array(W * H);
  const stack: number[] = [];
  for (let k = 0; k < W * H; k++) {
    if (!mask[k] || seen[k]) continue;
    let area = 0, cx0 = W, cy0 = H, cx1 = -1, cy1 = -1;
    stack.push(k);
    seen[k] = 1;
    while (stack.length) {
      const p = stack.pop() as number;
      const px = p % W, py = (p - px) / W;
      area++;
      if (px < cx0) cx0 = px; if (px > cx1) cx1 = px; if (py < cy0) cy0 = py; if (py > cy1) cy1 = py;
      for (const q of [p - 1, p + 1, p - W, p + W]) {
        if (q >= 0 && q < W * H && mask[q] && !seen[q] && Math.abs((q % W) - px) <= 1) { seen[q] = 1; stack.push(q); }
      }
    }
    const bw = cx1 - cx0 + 1, bh = cy1 - cy0 + 1;
    const elong = Math.max(bw, bh) / Math.max(1, Math.min(bw, bh));
    if (area >= 120 || Math.max(bw, bh) >= 24) { blobs++; blobBoxes.push([cx0, cy0, cx1, cy1]); }
    else if (elong >= 4 && Math.max(bw, bh) >= 8) streaks++;
    else if (area >= 1 && area <= 40) speckBoxes.push([cx0, cy0, cx1, cy1]);
    else if (elong < 3) { for (let r = 0; r < 3; r++) speckBoxes.push([cx0, cy0, cx1, cy1]); }   // compact shard cluster ≈ 3 specks
  }
  // Specks on or next to a large object are that object's texture/edges (foil creases, paint), not loose glass.
  const PAD = 6;
  for (const [a0, b0, a1, b1] of speckBoxes) {
    const near = blobBoxes.some(([x0, y0, x1, y1]) => a1 >= x0 - PAD && a0 <= x1 + PAD && b1 >= y0 - PAD && b0 <= y1 + PAD);
    if (near) continue;
    specks++;
    minX = Math.min(minX, a0); minY = Math.min(minY, b0); maxX = Math.max(maxX, a1); maxY = Math.max(maxY, b1);
  }
  // Many small compact glints → glass-like; streaks (glare) reduce confidence.
  const effective = Math.max(0, specks - 2 * streaks);
  let score = 1 - Math.exp(-effective / 5);
  if (blobs > 0 && specks < 4) score = Math.min(score, 0.3);
  const label: Detection['label'] = score >= 0.55 ? 'possible_glass' : blobs > 0 && streaks === 0 ? 'possible_debris' : 'none';
  const box = maxX >= 0 ? { x: minX * step, y: minY * step, w: (maxX - minX + 1) * step, h: (maxY - minY + 1) * step } : null;
  const t1 = typeof performance !== 'undefined' ? performance.now() : Date.now();
  return { label, score: Math.round(score * 1000) / 1000, specks, blobs, streaks, box, model: MODEL, ms: Math.round((t1 - t0) * 10) / 10 };
}

export const DETECTOR_MODEL = MODEL;
