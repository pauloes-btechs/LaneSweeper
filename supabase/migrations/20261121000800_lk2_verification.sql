-- Before/after evidence, remediation verdicts and public run reports.
create table public.inspection_evidence (
  id               uuid primary key default gen_random_uuid(),
  jurisdiction_id  uuid not null references public.jurisdictions(id),
  mission_id       uuid references public.missions(id),
  ticket_id        uuid references public.tickets(id),
  cell_id          text not null references public.lane_cells(id),
  phase            text not null check (phase in ('before','after','reinspection')),
  storage_path     text,
  fixture_id       text,
  captured_at      timestamptz not null default now(),
  analysis         jsonb,                             -- model/detector output incl. provenance
  provenance       text not null default 'SIMULATED' check (provenance in ('SIMULATED','OBSERVED_TEST','VERIFIED_FIELD'))
);
create index inspection_evidence_idx on public.inspection_evidence (mission_id, cell_id, phase);

create table public.remediation_verdicts (
  id               uuid primary key default gen_random_uuid(),
  jurisdiction_id  uuid not null references public.jurisdictions(id),
  ticket_id        uuid not null references public.tickets(id),
  mission_id       uuid references public.missions(id),
  cell_id          text not null references public.lane_cells(id),
  verdict          text not null check (verdict in ('cleared','partially_cleared','not_cleared','unverified')),
  after_evidence_id uuid references public.inspection_evidence(id),
  method           text not null check (method in ('model','detector','human','model+human')),
  confidence       real,
  model_provenance jsonb,
  decided_by       text not null,
  human_reviewed   boolean not null default false,
  reason           text not null,
  created_at       timestamptz not null default now(),
  check (verdict <> 'cleared' or after_evidence_id is not null)   -- "cleared" always needs after-evidence
);
create index verdicts_ticket_idx on public.remediation_verdicts (ticket_id, created_at desc);

-- A ticket can only become verified_closed when its latest verdict is 'cleared'.
create or replace function lk.guard_ticket_close()
returns trigger language plpgsql set search_path = '' as $$
declare v text;
begin
  if new.status = 'verified_closed' and old.status is distinct from 'verified_closed' then
    select verdict into v from public.remediation_verdicts where ticket_id = new.id order by created_at desc limit 1;
    if v is distinct from 'cleared' then
      raise exception 'ticket % cannot be verified_closed without a cleared verdict (latest: %)', new.number, coalesce(v, 'none')
        using errcode = 'P0001';
    end if;
  end if;
  new.updated_at := now();
  return new;
end $$;
create trigger ticket_close_guard before update on public.tickets
for each row execute function lk.guard_ticket_close();

create table public.public_run_reports (
  slug             text primary key check (slug ~ '^[a-z0-9-]{6,80}$'),
  jurisdiction_id  uuid not null references public.jurisdictions(id),
  mission_id       uuid not null unique references public.missions(id),
  service_date     date not null,
  summary          jsonb not null,                    -- aggregate only: cells, distance, verified vs unverified
  provenance       text not null check (provenance in ('SIMULATED','OBSERVED_TEST','VERIFIED_FIELD')),
  generated_at     timestamptz not null default now()
);

alter table public.inspection_evidence  enable row level security;
alter table public.remediation_verdicts enable row level security;
alter table public.public_run_reports   enable row level security;

create policy "staff read inspection evidence" on public.inspection_evidence for select to authenticated
  using (lk.has_permission(jurisdiction_id, 'evidence.view'));
create policy "staff read verdicts" on public.remediation_verdicts for select to authenticated
  using (lk.has_permission(jurisdiction_id, 'ticket.view'));
create policy "public reads run reports v2" on public.public_run_reports for select to anon, authenticated using (true);
