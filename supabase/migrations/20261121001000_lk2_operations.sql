-- Operations: durable jobs with leases/retries/dead-letter, outbox, audit log, usage, flags, model calls.
create table public.jobs (
  id               uuid primary key default gen_random_uuid(),
  jurisdiction_id  uuid references public.jurisdictions(id),
  kind             text not null,
  payload          jsonb not null default '{}',
  dedupe_key       text unique,                       -- business-event idempotency
  status           text not null default 'pending'
                   check (status in ('pending','leased','running','waiting_approval','retry_wait','succeeded','failed','dead_letter')),
  priority         int not null default 5,
  attempts         int not null default 0,
  max_attempts     int not null default 5,
  run_after        timestamptz not null default now(),
  leased_by        text,
  lease_expires_at timestamptz,
  last_error       text,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);
create index jobs_ready_idx on public.jobs (status, run_after, priority) where status in ('pending','retry_wait','leased');

create table public.job_attempts (
  id               bigint generated always as identity primary key,
  job_id           uuid not null references public.jobs(id),
  attempt          int not null,
  worker           text not null,
  started_at       timestamptz not null default now(),
  finished_at      timestamptz,
  outcome          text check (outcome in ('succeeded','failed','lease_expired')),
  error            text
);

-- Lease up to n ready jobs. Expired leases are reclaimed (the job is not lost).
create or replace function lk.lease_jobs(p_worker text, p_n int default 5, p_lease_s int default 60, p_kinds text[] default null)
returns setof public.jobs language plpgsql security definer set search_path = '' as $$
begin
  update public.job_attempts a set outcome = 'lease_expired', finished_at = now()
   from public.jobs j
   where a.job_id = j.id and a.finished_at is null and j.status in ('leased','running') and j.lease_expires_at < now();
  return query
  update public.jobs j set status = 'leased', leased_by = p_worker, attempts = j.attempts + 1,
         lease_expires_at = now() + make_interval(secs => p_lease_s), updated_at = now()
  where j.id in (
    select id from public.jobs
    where ((status in ('pending','retry_wait') and run_after <= now())
        or (status in ('leased','running') and lease_expires_at < now()))
      and (p_kinds is null or kind = any(p_kinds))
    order by priority, run_after
    limit p_n
    for update skip locked)
  returning j.*;
end $$;

create or replace function lk.finish_job(p_job uuid, p_worker text, p_ok boolean, p_error text default null,
                                         p_waiting_approval boolean default false)
returns text language plpgsql security definer set search_path = '' as $$
declare j public.jobs; st text;
begin
  select * into j from public.jobs where id = p_job for update;
  if j.leased_by is distinct from p_worker then raise exception 'job % not leased by %', p_job, p_worker; end if;
  if p_ok then st := case when p_waiting_approval then 'waiting_approval' else 'succeeded' end;
  elsif j.attempts >= j.max_attempts then st := 'dead_letter';
  else st := 'retry_wait'; end if;
  update public.jobs set status = st, last_error = p_error, lease_expires_at = null, updated_at = now(),
         run_after = case when st = 'retry_wait' then now() + make_interval(secs => least(3600, 2 ^ j.attempts * 5)) else run_after end
   where id = p_job;
  update public.job_attempts set finished_at = now(), outcome = case when p_ok then 'succeeded' else 'failed' end, error = p_error
   where job_id = p_job and attempt = j.attempts and finished_at is null;
  return st;
end $$;
revoke all on function lk.lease_jobs(text, int, int, text[]) from public, anon, authenticated;
revoke all on function lk.finish_job(uuid, text, boolean, text, boolean) from public, anon, authenticated;

create table public.notification_outbox (
  id               uuid primary key default gen_random_uuid(),
  jurisdiction_id  uuid references public.jurisdictions(id),
  channel          text not null check (channel in ('rider_push','glasses_hud','email','sms','whatsapp','console')),
  recipient_ref    text not null,                     -- opaque subscription id, never a raw phone/email
  template         text not null,
  body             jsonb not null,
  dedupe_key       text not null unique,
  status           text not null default 'pending' check (status in ('pending','sent','suppressed','failed')),
  adapter          text not null default 'mock',      -- paid channels use mock adapters unless enabled
  created_at       timestamptz not null default now(),
  sent_at          timestamptz
);

create table public.audit_log (
  id               bigint generated always as identity primary key,
  at               timestamptz not null default now(),
  jurisdiction_id  uuid references public.jurisdictions(id),
  actor            text not null,
  action           text not null,
  target           text,
  outcome          text not null check (outcome in ('allowed','denied','error')),
  reason           text,
  detail           jsonb not null default '{}',
  trace_id         text
);
create index audit_log_juris_idx on public.audit_log (jurisdiction_id, at desc);

create or replace function lk.audit_append_only()
returns trigger language plpgsql set search_path = '' as $$
begin raise exception 'audit_log is append-only' using errcode = 'P0001'; end $$;
create trigger audit_log_immutable before update or delete on public.audit_log
for each row execute function lk.audit_append_only();

create table public.usage_counters (
  bucket           text not null,                     -- e.g. 'model:gemini-decision-1'
  window_start     timestamptz not null,
  window_kind      text not null check (window_kind in ('minute','day')),
  count            int not null default 0,
  tokens           bigint not null default 0,
  primary key (bucket, window_kind, window_start)
);

create table public.feature_flags (
  key              text primary key,
  enabled          boolean not null,
  description      text not null,
  updated_by       uuid references auth.users(id),
  updated_at       timestamptz not null default now()
);
insert into public.feature_flags (key, enabled, description) values
  ('billable_calls', true,  'Master switch for every externally billable call (models, SMS, video). Off = offline fixtures only.'),
  ('hardware_gateway', false, 'Real robot hardware gateway. Not implemented; must remain false.'),
  ('sms_live', false, 'Send real SMS/WhatsApp via Twilio instead of the mock adapter.'),
  ('auto_link_duplicates', true, 'Auto-link duplicates that pass the strict rule threshold; otherwise suggest.')
on conflict (key) do nothing;

create or replace function lk.guard_hardware_flag()
returns trigger language plpgsql set search_path = '' as $$
begin
  if new.key = 'hardware_gateway' and new.enabled then
    raise exception 'hardware_gateway cannot be enabled in this build' using errcode = 'P0001';
  end if;
  return new;
end $$;
create trigger hardware_flag_guard before insert or update on public.feature_flags
for each row execute function lk.guard_hardware_flag();

create table public.model_calls (
  id               bigint generated always as identity primary key,
  at               timestamptz not null default now(),
  jurisdiction_id  uuid references public.jurisdictions(id),
  route            text not null,
  model            text not null,
  api              text not null,
  mode             text not null check (mode in ('live','offline','cached')),
  purpose          text not null,
  input_provenance text not null,
  latency_ms       int,
  input_tokens     int,
  output_tokens    int,
  outcome          text not null check (outcome in ('ok','fallback','error','quota','malformed','blocked','disabled')),
  error_class      text,
  run_id           uuid,
  trace_id         text
);
create index model_calls_at_idx on public.model_calls (at desc);

alter table public.jobs                enable row level security;
alter table public.job_attempts        enable row level security;
alter table public.notification_outbox enable row level security;
alter table public.audit_log           enable row level security;
alter table public.usage_counters      enable row level security;
alter table public.feature_flags       enable row level security;
alter table public.model_calls         enable row level security;

create policy "staff read jobs" on public.jobs for select to authenticated
  using (jurisdiction_id is not null and lk.has_permission(jurisdiction_id, 'ticket.view'));
create policy "admins read audit" on public.audit_log for select to authenticated
  using (jurisdiction_id is not null and lk.has_permission(jurisdiction_id, 'admin.policy'));
create policy "staff read model calls" on public.model_calls for select to authenticated
  using (jurisdiction_id is not null and lk.has_permission(jurisdiction_id, 'analytics.view'));
create policy "anyone reads flags" on public.feature_flags for select to anon, authenticated using (true);
