-- Agent execution records and the action-specific approval ledger.
-- Replaces the workshop's `approved: bool` with durable, single-use, payload-bound approvals.
create table public.policy_versions (
  id               text primary key,                  -- e.g. 'lk-policy-2026.11.1'
  jurisdiction_id  uuid references public.jurisdictions(id),
  body             jsonb not null,                    -- thresholds, windows, routing rules
  body_hash        text not null,
  active           boolean not null default false,
  created_by       uuid references auth.users(id),
  created_at       timestamptz not null default now()
);
create unique index policy_one_active_idx on public.policy_versions (coalesce(jurisdiction_id, '00000000-0000-0000-0000-000000000000'::uuid)) where active;

create table public.agent_runs (
  id               uuid primary key default gen_random_uuid(),
  jurisdiction_id  uuid references public.jurisdictions(id),
  workflow         text not null check (workflow in ('ticket','fleet','glasses','copilot','verification')),
  workflow_version text not null,
  thread_id        text not null unique check (char_length(thread_id) <= 200),
  actor_id         uuid references auth.users(id),
  trace_id         text not null,
  status           text not null default 'running'
                   check (status in ('running','waiting_approval','waiting_evidence','succeeded','failed','cancelled')),
  current_node     text,
  replay_mode      boolean not null default false,
  started_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  last_checkpoint_at timestamptz,
  error            text
);
create index agent_runs_juris_idx on public.agent_runs (jurisdiction_id, started_at desc);

create table public.agent_events (
  id               bigint generated always as identity primary key,
  run_id           uuid not null references public.agent_runs(id),
  jurisdiction_id  uuid references public.jurisdictions(id),
  seq              int not null,
  at               timestamptz not null default now(),
  node             text not null,
  kind             text not null check (kind in ('enter','exit','edge','decision','tool','interrupt','resume','error','retry','model')),
  edge_to          text,
  summary          text,                              -- plain-language reasoning for operators
  detail           jsonb not null default '{}',
  unique (run_id, seq)
);

create table public.tool_invocations (
  id               uuid primary key default gen_random_uuid(),
  run_id           uuid references public.agent_runs(id),
  jurisdiction_id  uuid references public.jurisdictions(id),
  tool             text not null,
  side_effect      text not null check (side_effect in ('read','internal_write','notification','simulation','physical')),
  idempotency_key  text not null unique,
  principal_id     uuid references auth.users(id),
  approval_id      uuid,
  args_hash        text not null,
  status           text not null check (status in ('started','succeeded','failed','denied')),
  result           jsonb,
  error            text,
  started_at       timestamptz not null default now(),
  finished_at      timestamptz
);

create table public.approval_requests (
  id                 uuid primary key default gen_random_uuid(),
  operation_id       uuid not null unique default gen_random_uuid(),
  jurisdiction_id    uuid not null references public.jurisdictions(id),
  run_id             uuid references public.agent_runs(id),
  action             text not null check (action in ('dispatch_simulated_mission','cancel_simulated_mission',
                                                      'publish_rider_notice','change_safety_limits','change_membership')),
  target_kind        text not null,
  target_id          text not null,
  payload            jsonb not null,
  payload_hash       text not null check (payload_hash ~ '^[0-9a-f]{64}$'),
  simulation_mode    boolean not null,
  required_permission text not null,
  requested_by       text not null,                   -- 'agent:<run_id>' or user id
  requested_by_user  uuid references auth.users(id),
  approver_id        uuid references auth.users(id),
  status             text not null default 'pending'
                     check (status in ('pending','approved','denied','revoked','expired','consumed')),
  decision_reason    text,
  signature          text,                            -- server HMAC over (id, operation, hash, approver, expiry)
  expires_at         timestamptz not null,
  decided_at         timestamptz,
  consumed_at        timestamptz,
  consumed_by        uuid references public.tool_invocations(id),
  revoked_at         timestamptz,
  created_at         timestamptz not null default now(),
  check (action <> 'dispatch_simulated_mission' or simulation_mode),     -- the demo cannot approve real dispatch
  check (approver_id is null or requested_by_user is null or approver_id <> requested_by_user)  -- separation of duties
);
create index approvals_pending_idx on public.approval_requests (jurisdiction_id, status, expires_at);

-- Allowed transitions; terminal states are immutable.
create or replace function lk.guard_approval_transition()
returns trigger language plpgsql set search_path = '' as $$
begin
  if old.status in ('denied','revoked','expired','consumed') then
    raise exception 'approval % is %, terminal', old.id, old.status using errcode = 'P0001';
  end if;
  if new.payload_hash <> old.payload_hash or new.payload <> old.payload or new.action <> old.action
     or new.target_id <> old.target_id or new.jurisdiction_id <> old.jurisdiction_id
     or new.operation_id <> old.operation_id or new.simulation_mode <> old.simulation_mode then
    raise exception 'approval % payload is immutable', old.id using errcode = 'P0001';
  end if;
  if not ((old.status = 'pending'  and new.status in ('pending','approved','denied','revoked','expired'))
       or (old.status = 'approved' and new.status in ('approved','consumed','revoked','expired'))) then
    raise exception 'illegal approval transition % -> %', old.status, new.status using errcode = 'P0001';
  end if;
  return new;
end $$;
create trigger approval_transition_guard before update on public.approval_requests
for each row execute function lk.guard_approval_transition();

-- Does a specific user (not the session) currently hold the permission? Used at consume time.
create or replace function lk.user_has_permission(uid uuid, jid uuid, perm text)
returns boolean language sql stable security definer set search_path = '' as $$
  select exists (
    select 1 from public.municipal_memberships m
    join public.role_permissions rp on rp.role = m.role and rp.permission = perm
    where m.user_id = uid and m.jurisdiction_id = jid and m.status = 'active'
      and m.valid_from <= now() and (m.valid_until is null or m.valid_until > now()))
$$;

-- Atomically consume an approval exactly once. Every condition is rechecked inside one UPDATE,
-- so concurrent or replayed calls cannot both succeed. Returns the approval id, or raises.
create or replace function lk.consume_approval(p_approval uuid, p_operation uuid, p_hash text,
                                               p_jurisdiction uuid, p_action text, p_invocation uuid)
returns uuid language plpgsql security definer set search_path = '' as $$
declare a public.approval_requests; ok uuid;
begin
  select * into a from public.approval_requests where id = p_approval for update;
  if not found then raise exception 'approval not found' using errcode = 'P0002'; end if;
  if a.status = 'approved' and a.expires_at <= now() then
    update public.approval_requests set status = 'expired' where id = a.id;
    raise exception 'approval expired' using errcode = 'P0001';
  end if;
  if a.status <> 'approved' then raise exception 'approval is %', a.status using errcode = 'P0001'; end if;
  if a.operation_id <> p_operation then raise exception 'operation mismatch' using errcode = 'P0001'; end if;
  if a.payload_hash <> p_hash then raise exception 'payload hash mismatch' using errcode = 'P0001'; end if;
  if a.jurisdiction_id <> p_jurisdiction then raise exception 'jurisdiction mismatch' using errcode = 'P0001'; end if;
  if a.action <> p_action then raise exception 'action mismatch' using errcode = 'P0001'; end if;
  if not lk.user_has_permission(a.approver_id, a.jurisdiction_id, a.required_permission) then
    update public.approval_requests set status = 'revoked', revoked_at = now(),
           decision_reason = coalesce(decision_reason, '') || ' [approver authority no longer held]' where id = a.id;
    raise exception 'approver no longer authorized' using errcode = 'P0001';
  end if;
  update public.approval_requests set status = 'consumed', consumed_at = now(), consumed_by = p_invocation
   where id = a.id and status = 'approved' and consumed_at is null returning id into ok;
  if ok is null then raise exception 'approval already consumed' using errcode = 'P0001'; end if;
  return ok;
end $$;
revoke all on function lk.consume_approval(uuid, uuid, text, uuid, text, uuid) from public, anon, authenticated;
revoke all on function lk.user_has_permission(uuid, uuid, text) from public, anon, authenticated;

alter table public.policy_versions   enable row level security;
alter table public.agent_runs        enable row level security;
alter table public.agent_events      enable row level security;
alter table public.tool_invocations  enable row level security;
alter table public.approval_requests enable row level security;

create policy "staff read policies"   on public.policy_versions for select to authenticated
  using (jurisdiction_id is null or lk.has_permission(jurisdiction_id, 'ticket.view'));
create policy "staff read agent runs" on public.agent_runs for select to authenticated
  using (jurisdiction_id is not null and lk.has_permission(jurisdiction_id, 'ticket.view'));
create policy "staff read agent events" on public.agent_events for select to authenticated
  using (jurisdiction_id is not null and lk.has_permission(jurisdiction_id, 'ticket.view'));
create policy "staff read tool calls" on public.tool_invocations for select to authenticated
  using (jurisdiction_id is not null and lk.has_permission(jurisdiction_id, 'ticket.view'));
create policy "staff read approvals" on public.approval_requests for select to authenticated
  using (lk.has_permission(jurisdiction_id, 'ticket.view'));
-- No client writes: approve/deny goes through the orchestrator, which verifies the principal and signs.
