-- Realtime: only RLS-protected staff tables (Supabase Realtime enforces RLS for postgres_changes).
-- Private evidence, consent and raw report locations are NOT published.
alter publication supabase_realtime add table public.tickets, public.ticket_events, public.agent_events,
  public.approval_requests, public.robot_status, public.mission_events, public.lane_cell_status;

-- LangGraph checkpoint internals live in their own schema, unreachable from client roles.
create schema if not exists lk_checkpoints;
revoke all on schema lk_checkpoints from public, anon, authenticated;
grant usage, create on schema lk_checkpoints to service_role;
