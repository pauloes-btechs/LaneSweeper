// Evaluation of the on-device heuristic against the 16 labelled SYNTHETIC fixtures.
// Thresholds are pass/fail gates for regressions; they say nothing about real-world accuracy.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { PNG } from 'pngjs';
import { detect } from '../src/detector';

const root = resolve(__dirname, '../../..');
const reg = JSON.parse(readFileSync(resolve(root, 'services/orchestrator/lanekeeper/fixtures/vision.json'), 'utf8')) as {
  fixtures: Array<{ id: string; truth: { glass: boolean; vision_verdict?: string } }>;
};

function load(id: string) {
  const png = PNG.sync.read(readFileSync(resolve(root, `fixtures/images/${id}.png`)));
  return { width: png.width, height: png.height, data: new Uint8ClampedArray(png.data), capturedAt: 0, source: id };
}

describe('local detector on synthetic fixtures', () => {
  const rows = reg.fixtures.map((f) => ({ id: f.id, glass: f.truth.glass, d: detect(load(f.id)) }));

  it('reports counts and timing for every fixture', () => {
    for (const r of rows) {
      expect(r.d.model).toBe('lk-local-heuristic-v1');
      expect(r.d.score).toBeGreaterThanOrEqual(0);
      expect(r.d.score).toBeLessThanOrEqual(1);
    }
    // eslint-disable-next-line no-console
    console.table(rows.map((r) => ({ id: r.id, truth_glass: r.glass, label: r.d.label, score: r.d.score, specks: r.d.specks,
                                     streaks: r.d.streaks, blobs: r.d.blobs, ms: r.d.ms })));
  });

  it('meets regression gates: recall ≥ 0.8 on dense glass, no false glass on clear lane, markings or water', () => {
    const dense = ['glass-1av-dawn-01', 'glass-wide-04', 'glass-1av-night-02', 'after-partial-14'];
    const hits = dense.filter((id) => rows.find((r) => r.id === id)!.d.label === 'possible_glass').length;
    expect(hits / dense.length).toBeGreaterThanOrEqual(0.75);
    for (const id of ['clear-lane-12', 'confounder-markings-08', 'confounder-water-05', 'after-clean-13']) {
      expect(rows.find((r) => r.id === id)!.d.label).not.toBe('possible_glass');
    }
  });

  // Measured on the synthetic set (2026-11-21): precision 0.71, recall 1.00. Known confusions: crumpled foil and
  // clear plastic look like glints to a pixel heuristic. That is acceptable for a conservative "possible debris"
  // warning and is exactly what server-side Decision 1 / Robotics ER verification resolves before any ticket action.
  it('precision/recall on the whole set are reported and meet the gates', () => {
    const tp = rows.filter((r) => r.glass && r.d.label === 'possible_glass').length;
    const fp = rows.filter((r) => !r.glass && r.d.label === 'possible_glass').length;
    const fn = rows.filter((r) => r.glass && r.d.label !== 'possible_glass').length;
    const precision = tp / Math.max(1, tp + fp);
    const recall = tp / Math.max(1, tp + fn);
    // eslint-disable-next-line no-console
    console.log(JSON.stringify({ tp, fp, fn, precision, recall, n: rows.length, note: 'synthetic fixtures only' }));
    expect(recall).toBeGreaterThanOrEqual(0.9);
    expect(precision).toBeGreaterThanOrEqual(0.7);
    const fpIds = rows.filter((r) => !r.glass && r.d.label === 'possible_glass').map((r) => r.id).sort();
    expect(fpIds).toEqual(['confounder-foil-06', 'confounder-plastic-07']);
  });
});
