-- LaneKeeper 2.0: reports drafted by the rider's device agent must carry the rider's authorisation.
-- Additive only.
alter table public.hazard_reports
  add column agent_assisted boolean not null default false,
  add column rider_approval jsonb;

alter table public.hazard_reports
  add constraint hazard_reports_agent_needs_rider_approval
  -- coalesce: a NULL rider_approval would otherwise make the expression NULL, which a CHECK treats as passing
  check (not agent_assisted
         or coalesce(rider_approval ? 'approved_at' and rider_approval ? 'drafted_at' and rider_approval ? 'channel'
                     and rider_approval ->> 'approver' = 'rider', false));

comment on column public.hazard_reports.rider_approval is
  'Rider authorisation of an agent-drafted report: draft_id, drafted_at, approved_at, channel, auto_captured_frames, approver';
