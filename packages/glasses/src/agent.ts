/**
 * RiderReportAgent: drafts hazard reports for the rider, and submits them only after the rider approves.
 *
 * While riding, the rider should not fill in forms. When the on-device detector reports a *stable* possible-glass
 * candidate, the agent:
 *   1. captures the best snapshots automatically (top N by detector score, kept on the device),
 *   2. records the location fix, heading and speed,
 *   3. fills in every field the report API needs (hazard, consent from the rider's saved choices, detector summary),
 *   4. optionally asks the server for a read-only preview (where it lands, whether it's already reported).
 *
 * Nothing leaves the device until the rider approves. The agent asks only when the rider is stopped; the one
 * exception is a single glasses-button press, which needs no reading. Unapproved drafts expire after 15 minutes and
 * their snapshots are deleted. The approval is sent with the report, and the server rejects agent reports without it.
 */
import type { Detection } from './detector';
import { type KV, MemoryKV, OfflineQueue } from './queue';

export type ApprovalChannel = 'phone_tap' | 'glasses_button' | 'voice';
export type DraftStatus = 'pending' | 'submitted' | 'queued' | 'declined' | 'expired' | 'failed';

export interface DraftLocation {
  lat: number; lng: number; accuracy_m?: number; heading_deg?: number; speed_mps?: number;
  source: 'device_gps' | 'phone_gps';
}

export interface DraftSnapshot {
  frameId: string;                 // fixture id in the simulator, or a local frame id
  score: number;
  capturedAt: number;
  imageB64?: string;               // real frames only (JPEG); fixtures are referenced by id
}

export interface DraftPreview {
  in_network: boolean; cell_id: string | null; place: string | null; advice: string;
  nearby_open: { ticket_number: string; hazard_type: string; report_count: number; distance_m: number } | null;
}

export interface ReportDraft {
  id: string;
  createdAt: number;
  expiresAt: number;
  hazard: 'glass';
  snapshots: DraftSnapshot[];
  location: DraftLocation | null;
  detector: { label: string; score: number; consecutive_frames: number; model: string; warning_issued: boolean };
  preview?: DraftPreview;
  status: DraftStatus;
  ticket?: string;
  error?: string;
  approvedAt?: number;
  channel?: ApprovalChannel;
}

export interface RiderPrefs { shareImage: boolean; shareLocation: boolean; notifyStatus: boolean; policyVersion: string }

export interface SubmitResult { ticket_number?: string | null }

export interface AgentOptions {
  sourceKind: 'glasses_sim' | 'glasses_device' | 'rider_app';
  prefs: () => RiderPrefs;
  submit: (body: Record<string, unknown> & { idempotency_key: string }) => Promise<SubmitResult>;
  preview?: (q: { lat: number; lng: number; accuracy_m?: number; hazard_claimed: string }) => Promise<DraftPreview>;
  kv?: KV;
  storageKey?: string;
  now?: () => number;
  ttlMs?: number;
  maxSnapshots?: number;
  mergeWindowMs?: number;
  mergeRadiusM?: number;
  stoppedBelowMps?: number;
  provenance?: 'SIMULATED' | 'OBSERVED_TEST' | 'VERIFIED_FIELD';
}

export const DRAFT_TTL_MS = 15 * 60 * 1000;     // must match the server's approval window
export const SAVED_PROMPT = 'Saved a possible-glass report — confirm when stopped';

export function distanceM(a: { lat: number; lng: number }, b: { lat: number; lng: number }): number {
  const R = 6371000, rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad, dLng = (b.lng - a.lng) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

function newId(now: number): string {
  const rnd = Math.random().toString(36).slice(2, 10).padEnd(8, '0');
  return `draft-${now.toString(36)}-${rnd}`;
}

export class RiderReportAgent {
  private kv: KV;
  private key: string;
  private now: () => number;
  private ttl: number;
  private maxSnaps: number;
  private mergeWindow: number;
  private mergeRadius: number;
  private stoppedBelow: number;
  readonly queue: OfflineQueue;

  constructor(private o: AgentOptions) {
    this.kv = o.kv ?? new MemoryKV();
    this.key = o.storageKey ?? 'lk.rider.drafts';
    this.now = o.now ?? (() => Date.now());
    this.ttl = o.ttlMs ?? DRAFT_TTL_MS;
    this.maxSnaps = o.maxSnapshots ?? 3;
    this.mergeWindow = o.mergeWindowMs ?? 60_000;
    this.mergeRadius = o.mergeRadiusM ?? 30;
    this.stoppedBelow = o.stoppedBelowMps ?? 1.0;
    this.queue = new OfflineQueue(this.kv, `${this.key}.outbox`);
  }

  // ---------- storage ----------
  all(): ReportDraft[] {
    try { return JSON.parse(this.kv.get(this.key) ?? '[]') as ReportDraft[]; } catch { return []; }
  }
  private save(ds: ReportDraft[]) { this.kv.set(this.key, JSON.stringify(ds.slice(-20))); }
  private update(id: string, f: (d: ReportDraft) => ReportDraft) { this.save(this.all().map((d) => (d.id === id ? f(d) : d))); }
  get(id: string): ReportDraft | undefined { return this.all().find((d) => d.id === id); }

  /** Pending drafts awaiting the rider, after dropping expired ones (and their snapshots). */
  pending(): ReportDraft[] {
    this.expire();
    return this.all().filter((d) => d.status === 'pending');
  }

  /** Expire drafts the rider never approved; snapshots are deleted, only a stub remains for the rider's history. */
  expire(): number {
    const t = this.now();
    let n = 0;
    this.save(this.all().map((d) => {
      if (d.status === 'pending' && t > d.expiresAt) { n++; return { ...d, status: 'expired' as const, snapshots: [] }; }
      return d;
    }));
    return n;
  }

  /** The agent prompts on the phone/HUD only when the rider is stopped. */
  canPrompt(speedMps: number | null | undefined): boolean {
    return (speedMps ?? 0) < this.stoppedBelow;
  }

  // ---------- capture ----------
  /**
   * Feed every processed frame. Returns the draft that was created or extended, or null if nothing was captured.
   * Only stable possible-glass detections are captured; uncertain frames are never stored.
   */
  observe(d: Detection, stable: boolean, consecutive: number, frame: { id: string; imageB64?: string },
          loc: DraftLocation | null, warningIssued: boolean): { draft: ReportDraft; created: boolean } | null {
    if (!stable || d.label !== 'possible_glass') return null;
    const t = this.now();
    const snap: DraftSnapshot = { frameId: frame.id, score: d.score, capturedAt: t, ...(frame.imageB64 ? { imageB64: frame.imageB64 } : {}) };
    const open = this.pending().find((x) => t - x.createdAt <= this.mergeWindow
      && (!x.location || !loc || distanceM(x.location, loc) <= this.mergeRadius));
    if (open) {
      const snaps = [...open.snapshots, snap].sort((a, b) => b.score - a.score).slice(0, this.maxSnaps);
      const merged: ReportDraft = {
        ...open, snapshots: snaps, location: open.location ?? loc,
        detector: { ...open.detector, score: Math.max(open.detector.score, d.score), consecutive_frames: Math.max(open.detector.consecutive_frames, consecutive),
                    warning_issued: open.detector.warning_issued || warningIssued },
      };
      this.update(open.id, () => merged);
      return { draft: merged, created: false };
    }
    const draft: ReportDraft = {
      id: newId(t), createdAt: t, expiresAt: t + this.ttl, hazard: 'glass', snapshots: [snap], location: loc,
      detector: { label: d.label, score: d.score, consecutive_frames: consecutive, model: d.model, warning_issued: warningIssued },
      status: 'pending',
    };
    this.save([...this.all(), draft]);
    return { draft, created: true };
  }

  /** Ask the server where the draft would land and whether it's already reported (read-only). */
  async refreshPreview(id: string): Promise<DraftPreview | null> {
    const d = this.get(id);
    if (!d || !d.location || !this.o.preview) return null;
    try {
      const p = await this.o.preview({ lat: d.location.lat, lng: d.location.lng, accuracy_m: d.location.accuracy_m, hazard_claimed: d.hazard });
      this.update(id, (x) => ({ ...x, preview: p }));
      return p;
    } catch { return null; }
  }

  // ---------- rider decision ----------
  decline(id: string): void {
    this.update(id, (d) => (d.status === 'pending' ? { ...d, status: 'declined', snapshots: [] } : d));
  }

  /** The request body the report API expects, built entirely by the agent, with the rider's approval attached. */
  buildReport(d: ReportDraft, approvedAt: number, channel: ApprovalChannel): Record<string, unknown> & { idempotency_key: string } {
    const prefs = this.o.prefs();
    const best = d.snapshots[0];
    const body: Record<string, unknown> & { idempotency_key: string } = {
      idempotency_key: `agent-${d.id}`.slice(0, 128),
      source_kind: this.o.sourceKind,
      observed_at: new Date(d.createdAt).toISOString(),
      hazard_claimed: d.hazard,
      note: `Drafted by the rider's assistant: possible glass (detector ${d.detector.model}, score ${d.detector.score.toFixed(2)}, `
        + `${d.detector.consecutive_frames} consecutive frames, ${d.snapshots.length} snapshot(s)). Approved by the rider.`,
      consent: { policy_version: prefs.policyVersion, share_image: prefs.shareImage, share_location: prefs.shareLocation,
                 notify_status: prefs.notifyStatus },
      local_detector: { label: d.detector.label, score: d.detector.score, consecutive_frames: d.detector.consecutive_frames,
                        model: d.detector.model, warning_issued: d.detector.warning_issued },
      agent_assist: { draft_id: d.id, drafted_at: new Date(d.createdAt).toISOString(), approved_at: new Date(approvedAt).toISOString(),
                      channel, auto_captured_frames: d.snapshots.length, approver: 'rider' },
      provenance: this.o.provenance ?? 'SIMULATED',
    };
    if (prefs.shareLocation && d.location) body.location = d.location;
    if (prefs.shareImage && best) {
      if (best.imageB64) { body.image_b64 = best.imageB64; body.image_mime = 'image/jpeg'; }   // single best frame only
      else body.fixture_id = best.frameId;
    }
    return body;
  }

  /**
   * The rider approves. Phone tap and voice are accepted only when stopped; a single glasses-button press is
   * accepted while moving. Submits, or queues offline when the network fails (the server de-duplicates by key).
   */
  async approve(id: string, channel: ApprovalChannel, speedMps: number | null | undefined): Promise<ReportDraft> {
    this.expire();
    const d = this.get(id);
    if (!d) throw new Error('unknown draft');
    if (d.status !== 'pending') throw new Error(`draft is ${d.status}`);
    if (channel !== 'glasses_button' && !this.canPrompt(speedMps)) throw new Error('stop before approving on the phone');
    const at = this.now();
    const body = this.buildReport(d, at, channel);
    let next: ReportDraft;
    try {
      const r = await this.o.submit(body);
      next = { ...d, status: 'submitted', approvedAt: at, channel, ticket: r.ticket_number ?? undefined, snapshots: [] };
    } catch (e) {
      this.queue.enqueue(body);
      next = { ...d, status: 'queued', approvedAt: at, channel, error: (e as Error).message, snapshots: [] };
    }
    this.update(id, () => next);
    return next;
  }

  /** Glasses button while riding: approve the newest pending draft. */
  async approveLatestByButton(speedMps: number | null | undefined): Promise<ReportDraft | null> {
    const p = this.pending().sort((a, b) => b.createdAt - a.createdAt)[0];
    return p ? this.approve(p.id, 'glasses_button', speedMps) : null;
  }

  /** Retry approved reports that were queued while offline. */
  async flushQueued(): Promise<{ sent: number; kept: number }> {
    return this.queue.flush(async (body) => { await this.o.submit(body as Record<string, unknown> & { idempotency_key: string }); return true; });
  }
}
