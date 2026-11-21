export interface Membership { id: string; slug: string; name: string; role: string }
export interface Me { user_id: string; email: string; memberships: Membership[] }
export interface Ticket {
  id: string; number: string; hazard_type: string; severity: string; priority: number; status: string; remediation_path: string;
  summary: string | null; created_at: string; cell_id: string | null; report_count: number | null; department: string | null; provenance: string;
}
export interface Approval {
  id: string; operation_id: string; action: string; target_id: string; payload: Record<string, unknown>; payload_hash: string; status: string;
  expires_at: string; requested_by: string; created_at: string; thread_id: string | null; workflow: string | null; robot_id: string | null;
  estimates: Record<string, string | number> | null; constraints: Array<{ check: string; ok: boolean; detail: string }> | null;
  rationale: string | null; decision_reason: string | null;
}
export interface TraceEvent { seq: number; at: string; node: string; kind: string; edge_to: string | null; summary: string; detail: Record<string, unknown> }
export interface Robot { id: string; state: string; battery_pct: number; hopper_pct: number; connectivity: string; cell_id: string | null; lat: number; lng: number }
export interface Overview {
  tickets_by_status: Array<{ status: string; n: number }>; tickets_by_priority: Array<{ priority: number; n: number }>;
  active_hazards: number; pending_approvals: number; running_workflows: number; robots: Robot[];
  median_minutes_to_respond_estimate: number | null; provenance: string;
}
