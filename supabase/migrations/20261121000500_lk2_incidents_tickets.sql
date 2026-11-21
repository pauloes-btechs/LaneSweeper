-- Incidents (a physical hazard, possibly seen many times) and tickets (the municipal case).
-- A report can create a ticket without implying robot dispatch.
create table public.sla_policies (
  id               uuid primary key default gen_random_uuid(),
  jurisdiction_id  uuid not null references public.jurisdictions(id),
  hazard_type      text not null,
  severity         text not null check (severity in ('low','medium','high','critical')),
  respond_within   interval not null,
  resolve_within   interval not null,
  unique (jurisdiction_id, hazard_type, severity)
);

create table public.hazard_incidents (
  id               uuid primary key default gen_random_uuid(),
  jurisdiction_id  uuid not null references public.jurisdictions(id),
  cell_id          text references public.lane_cells(id),
  hazard_type      text not null check (hazard_type in ('glass','debris','branch','litter','pothole','water','obstruction','markings','construction','other')),
  first_seen_at    timestamptz not null,
  last_seen_at     timestamptz not null,
  report_count     int not null default 1 check (report_count >= 1),
  status           text not null default 'open' check (status in ('open','monitoring','resolved','dismissed')),
  provenance       text not null default 'SIMULATED' check (provenance in ('SIMULATED','OBSERVED_TEST','VERIFIED_FIELD')),
  created_at       timestamptz not null default now()
);
create index incidents_open_idx on public.hazard_incidents (jurisdiction_id, cell_id, hazard_type) where status in ('open','monitoring');

-- Every original report stays its own row; links record how and why it was grouped.
create table public.incident_report_links (
  incident_id      uuid not null references public.hazard_incidents(id),
  report_id        uuid not null references public.hazard_reports(id),
  link_kind        text not null check (link_kind in ('origin','auto_duplicate','suggested_duplicate','operator_linked')),
  rule_score       real,            -- time/distance/type rules, 0..1
  vector_score     real,            -- embedding similarity, 0..1
  decided_by       text not null,   -- 'policy:<version>' or a user id
  feedback         text check (feedback in ('accepted','rejected')),
  created_at       timestamptz not null default now(),
  primary key (incident_id, report_id)
);

create sequence public.ticket_number_seq start 101;
create table public.tickets (
  id               uuid primary key default gen_random_uuid(),
  number           text unique not null default 'LK-' || nextval('public.ticket_number_seq'),
  jurisdiction_id  uuid not null references public.jurisdictions(id),
  incident_id      uuid references public.hazard_incidents(id),
  department_id    uuid references public.departments(id),
  hazard_type      text not null,
  severity         text not null check (severity in ('low','medium','high','critical')),
  priority         int not null check (priority between 1 and 5),   -- 1 = most urgent
  priority_basis   jsonb not null default '{}',                     -- rubric inputs, shown to operators
  status           text not null default 'new'
                   check (status in ('new','needs_evidence','triaged','assigned','scheduled','in_progress',
                                     'remediated_unverified','verified_closed','reopened','escalated','dismissed')),
  remediation_path text not null default 'undecided'
                   check (remediation_path in ('undecided','robot_sweep','crew_sweep','road_repair','drainage','markings','external_agency','none')),
  summary          text,
  respond_by       timestamptz,
  resolve_by       timestamptz,
  created_by       text not null,          -- 'agent:<run>' or user id
  idempotency_key  text unique,
  provenance       text not null default 'SIMULATED' check (provenance in ('SIMULATED','OBSERVED_TEST','VERIFIED_FIELD')),
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);
create index tickets_queue_idx on public.tickets (jurisdiction_id, status, priority);
create index tickets_incident_idx on public.tickets (incident_id);

create table public.ticket_events (
  id               bigint generated always as identity primary key,
  ticket_id        uuid not null references public.tickets(id),
  jurisdiction_id  uuid not null references public.jurisdictions(id),
  at               timestamptz not null default now(),
  actor            text not null,
  kind             text not null,
  from_status      text,
  to_status        text,
  detail           jsonb not null default '{}'
);
create index ticket_events_ticket_idx on public.ticket_events (ticket_id, at);

create table public.assignments (
  id               uuid primary key default gen_random_uuid(),
  ticket_id        uuid not null references public.tickets(id),
  jurisdiction_id  uuid not null references public.jurisdictions(id),
  assignee_kind    text not null check (assignee_kind in ('crew','department','robot_sim','external')),
  assignee_ref     text not null,
  assigned_by      uuid references auth.users(id),
  reason           text not null,
  active           boolean not null default true,
  created_at       timestamptz not null default now()
);

alter table public.sla_policies          enable row level security;
alter table public.hazard_incidents      enable row level security;
alter table public.incident_report_links enable row level security;
alter table public.tickets               enable row level security;
alter table public.ticket_events         enable row level security;
alter table public.assignments           enable row level security;

create policy "staff read sla"         on public.sla_policies     for select to authenticated using (lk.has_permission(jurisdiction_id, 'ticket.view'));
create policy "staff read incidents"   on public.hazard_incidents for select to authenticated using (lk.has_permission(jurisdiction_id, 'ticket.view'));
create policy "staff read links"       on public.incident_report_links for select to authenticated
  using (exists (select 1 from public.hazard_incidents i where i.id = incident_id and lk.has_permission(i.jurisdiction_id, 'evidence.view')));
create policy "staff read tickets"     on public.tickets          for select to authenticated using (lk.has_permission(jurisdiction_id, 'ticket.view'));
create policy "staff read ticket log"  on public.ticket_events    for select to authenticated using (lk.has_permission(jurisdiction_id, 'ticket.view'));
create policy "staff read assignments" on public.assignments      for select to authenticated using (lk.has_permission(jurisdiction_id, 'ticket.view'));
