import { describe, expect, it } from 'vitest';
import { type Detection, MemoryKV, RiderReportAgent, SAVED_PROMPT, SubmitRejected, distanceM } from '../src';

const glass = (score = 0.9): Detection => ({ label: 'possible_glass', score, specks: 8, blobs: 0, streaks: 0, box: null, model: 'lk-local-heuristic-v1', ms: 1 });
const loc = (dLat = 0) => ({ lat: 40.7584 + dLat, lng: -73.9621, accuracy_m: 8, heading_deg: 30, speed_mps: 5, source: 'phone_gps' as const });
const prefs = () => ({ shareImage: true, shareLocation: true, notifyStatus: true, policyVersion: 'rider-2026.11' });

function setup(over: Partial<ConstructorParameters<typeof RiderReportAgent>[0]> = {}) {
  let t = 1_700_000_000_000;
  const sent: Array<Record<string, unknown>> = [];
  const agent = new RiderReportAgent({
    sourceKind: 'glasses_sim', prefs, kv: new MemoryKV(), now: () => t,
    submit: async (b) => { sent.push(b); return { ticket_number: 'LK-201' }; }, ...over,
  });
  return { agent, sent, tick: (ms: number) => { t += ms; } };
}

describe('RiderReportAgent', () => {
  it('captures only stable possible-glass frames', () => {
    const { agent } = setup();
    expect(agent.observe(glass(), false, 1, { id: 'f1' }, loc(), false)).toBeNull();
    expect(agent.observe({ ...glass(), label: 'none' }, true, 4, { id: 'f1' }, loc(), false)).toBeNull();
    const r = agent.observe(glass(), true, 4, { id: 'glass-1av-dawn-01' }, loc(), true);
    expect(r?.created).toBe(true);
    expect(agent.pending()).toHaveLength(1);
    expect(SAVED_PROMPT).toMatch(/confirm when stopped/);
  });

  it('merges nearby frames into one draft and keeps the best snapshots', () => {
    const { agent, tick } = setup();
    for (const [i, s] of [0.6, 0.95, 0.7, 0.8].entries()) { agent.observe(glass(s), true, 3 + i, { id: `f${i}` }, loc(), false); tick(1000); }
    const [d] = agent.pending();
    expect(agent.pending()).toHaveLength(1);
    expect(d!.snapshots.map((s) => s.score)).toEqual([0.95, 0.8, 0.7]);
    expect(d!.detector.consecutive_frames).toBe(6);
  });

  it('starts a new draft for a hazard further along the lane', () => {
    const { agent } = setup();
    agent.observe(glass(), true, 4, { id: 'a' }, loc(), false);
    agent.observe(glass(), true, 4, { id: 'b' }, loc(0.002), false);     // ~220 m north
    expect(distanceM(loc(), loc(0.002))).toBeGreaterThan(200);
    expect(agent.pending()).toHaveLength(2);
  });

  it('expires unapproved drafts after 15 minutes and deletes their snapshots', () => {
    const { agent, tick } = setup();
    const { draft } = agent.observe(glass(), true, 4, { id: 'x', imageB64: 'AAAA' }, loc(), false)!;
    tick(15 * 60 * 1000 + 1);
    expect(agent.pending()).toHaveLength(0);
    expect(agent.get(draft.id)).toMatchObject({ status: 'expired', snapshots: [] });
  });

  it('refuses phone approval while riding, accepts the glasses button', async () => {
    const { agent, sent } = setup();
    const { draft } = agent.observe(glass(), true, 4, { id: 'glass-1av-dawn-01' }, loc(), true)!;
    await expect(agent.approve(draft.id, 'phone_tap', 5)).rejects.toThrow(/stop before approving/);
    expect(sent).toHaveLength(0);
    const done = await agent.approveLatestByButton(5);
    expect(done).toMatchObject({ status: 'submitted', ticket: 'LK-201', channel: 'glasses_button', snapshots: [] });
    expect(sent[0]!.agent_assist).toMatchObject({ channel: 'glasses_button', approver: 'rider', draft_id: draft.id });
  });

  it('builds the full API body from the draft with the rider approval and one best frame', async () => {
    const { agent, sent, tick } = setup();
    agent.observe(glass(0.7), true, 3, { id: 'f-low', imageB64: 'LOW' }, loc(), true);
    agent.observe(glass(0.97), true, 5, { id: 'f-high', imageB64: 'HIGH' }, loc(), true);
    tick(30_000);
    const [d] = agent.pending();
    await agent.approve(d!.id, 'phone_tap', 0);
    const b = sent[0]!;
    expect(b).toMatchObject({ source_kind: 'glasses_sim', hazard_claimed: 'glass', image_b64: 'HIGH', image_mime: 'image/jpeg',
      location: { lat: 40.7584, source: 'phone_gps' }, consent: { share_image: true, share_location: true } });
    expect(b.idempotency_key).toBe(`agent-${d!.id}`);
    const a = b.agent_assist as Record<string, string | number>;
    expect(Date.parse(a.approved_at as string) - Date.parse(a.drafted_at as string)).toBe(30_000);
    expect(a.auto_captured_frames).toBe(2);
  });

  it('honours the rider consent: no image and no location when not shared', async () => {
    const { agent, sent } = setup({ prefs: () => ({ ...prefs(), shareImage: false, shareLocation: false }) });
    const { draft } = agent.observe(glass(), true, 4, { id: 'glass-1av-dawn-01', imageB64: 'X' }, loc(), false)!;
    await agent.approve(draft.id, 'voice', 0);
    expect(sent[0]).not.toHaveProperty('image_b64');
    expect(sent[0]).not.toHaveProperty('fixture_id');
    expect(sent[0]).not.toHaveProperty('location');
  });

  it('declining deletes the snapshots and nothing is sent', () => {
    const { agent, sent } = setup();
    const { draft } = agent.observe(glass(), true, 4, { id: 'x', imageB64: 'X' }, loc(), false)!;
    agent.decline(draft.id);
    expect(agent.get(draft.id)).toMatchObject({ status: 'declined', snapshots: [] });
    expect(sent).toHaveLength(0);
  });

  it('queues an approved report when offline and flushes it later', async () => {
    let online = false;
    const sent: unknown[] = [];
    const { agent } = setup({ submit: async (b) => { if (!online) throw new Error('offline'); sent.push(b); return {}; } });
    const { draft } = agent.observe(glass(), true, 4, { id: 'glass-1av-dawn-01' }, loc(), false)!;
    expect((await agent.approve(draft.id, 'phone_tap', 0)).status).toBe('queued');
    online = true;
    expect(await agent.flushQueued()).toEqual({ sent: 1, kept: 0 });
    expect(sent).toHaveLength(1);
  });

  it('stores the preview from the server', async () => {
    const { agent } = setup({ preview: async () => ({ in_network: true, cell_id: 'MN-1AV-0137', place: '1st Ave (protected lane), Manhattan',
      advice: 'Already reported as LK-101', nearby_open: { ticket_number: 'LK-101', hazard_type: 'glass', report_count: 2, distance_m: 6 } }) });
    const { draft } = agent.observe(glass(), true, 4, { id: 'g' }, loc(), false)!;
    await agent.refreshPreview(draft.id);
    expect(agent.get(draft.id)?.preview?.nearby_open?.ticket_number).toBe('LK-101');
  });

  it('a server rejection fails the draft instead of queueing it for retry', async () => {
    const { agent } = setup({ submit: async () => { throw new SubmitRejected('agent-assisted reports must come from a signed-in rider'); } });
    const { draft } = agent.observe(glass(), true, 4, { id: 'g' }, loc(), false)!;
    const r = await agent.approve(draft.id, 'phone_tap', 0);
    expect(r).toMatchObject({ status: 'failed', snapshots: [] });
    expect(agent.queue.items()).toHaveLength(0);
  });
});
