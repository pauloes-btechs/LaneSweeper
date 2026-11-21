-- Fleet and missions. Simulated and real robots are never conflated: missions require
-- simulation_mode = true and a simulated robot, enforced here as well as in the orchestrator.
create table public.robots (
  id               text primary key check (id ~ '^[A-Z0-9-]{3,24}$'),
  jurisdiction_id  uuid not null references public.jurisdictions(id),
  name             text not null,
  model            text not null default 'LK-01',
  mode             text not null default 'simulated' check (mode in ('simulated','real')),
  home_depot_id    text references public.depots(id),
  legacy_sweeper_id text references public.sweepers(id),
  created_at       timestamptz not null default now()
);

create table public.robot_capabilities (
  robot_id         text primary key references public.robots(id),
  hazard_types     text[] not null default '{glass,debris,litter}',
  sweep_width_mm   int not null default 900,
  body_mm          int[] not null default '{1300,800,550}',
  max_speed_mps    real not null default 1.2,        -- design target, simulation only
  battery_wh       real not null default 1200,
  wh_per_km        real not null default 95,
  hopper_l         real not null default 40,
  min_lane_width_mm int not null default 1500
);

create table public.robot_status (
  robot_id         text primary key references public.robots(id),
  jurisdiction_id  uuid not null references public.jurisdictions(id),
  state            text not null default 'docked'
                   check (state in ('docked','charging','idle','en_route','sweeping','paused_safety','returning','fault','offline')),
  battery_pct      real not null default 100 check (battery_pct between 0 and 100),
  hopper_pct       real not null default 0 check (hopper_pct between 0 and 100),
  cell_id          text references public.lane_cells(id),
  position         extensions.geography(Point, 4326),
  connectivity     text not null default 'online' check (connectivity in ('online','degraded','lost')),
  mission_id       uuid,
  updated_at       timestamptz not null default now()
);

create table public.robot_telemetry (
  id               bigint generated always as identity primary key,
  robot_id         text not null references public.robots(id),
  jurisdiction_id  uuid not null references public.jurisdictions(id),
  mission_id       uuid,
  event_id         text not null unique,              -- deterministic in replays
  at               timestamptz not null,
  sim_time_s       real,
  position         extensions.geography(Point, 4326),
  cell_id          text,
  speed_mps        real,
  battery_pct      real,
  hopper_pct       real,
  state            text,
  provenance       text not null default 'SIMULATED' check (provenance in ('SIMULATED','OBSERVED_TEST','VERIFIED_FIELD'))
);
create index telemetry_robot_idx on public.robot_telemetry (robot_id, at desc);
create index telemetry_mission_idx on public.robot_telemetry (mission_id, at);

create table public.robot_faults (
  id               uuid primary key default gen_random_uuid(),
  robot_id         text not null references public.robots(id),
  jurisdiction_id  uuid not null references public.jurisdictions(id),
  mission_id       uuid,
  code             text not null check (code in ('obstacle_cyclist','obstacle_pedestrian','vehicle_blocking','wheel_jam',
                                                 'low_battery','hopper_full','connection_lost','gps_drift','optics_degraded',
                                                 'operator_stop','geofence')),
  severity         text not null check (severity in ('info','warning','critical')),
  safety_action    text not null check (safety_action in ('none','yield','stop','return_to_depot','abort')),
  detail           jsonb not null default '{}',
  opened_at        timestamptz not null default now(),
  cleared_at       timestamptz,
  cleared_by       text
);

create table public.work_orders (
  id               uuid primary key default gen_random_uuid(),
  jurisdiction_id  uuid not null references public.jurisdictions(id),
  ticket_id        uuid not null references public.tickets(id),
  kind             text not null check (kind in ('robot_sweep','crew_sweep','road_repair','drainage','markings','inspection')),
  department_id    uuid references public.departments(id),
  status           text not null default 'open' check (status in ('open','planned','in_progress','done','cancelled')),
  priority         int not null check (priority between 1 and 5),
  cell_id          text references public.lane_cells(id),
  mission_id       uuid,
  created_by       text not null,
  created_at       timestamptz not null default now(),
  unique (ticket_id, kind)
);

-- Immutable proposal versions: any change produces a new row and a new payload hash.
create table public.mission_proposals (
  id               uuid primary key default gen_random_uuid(),
  jurisdiction_id  uuid not null references public.jurisdictions(id),
  run_id           uuid references public.agent_runs(id),
  version          int not null default 1,
  supersedes       uuid references public.mission_proposals(id),
  robot_id         text not null references public.robots(id),
  work_order_ids   uuid[] not null,
  route_cell_ids   text[] not null check (cardinality(route_cell_ids) between 1 and 400),
  window_start     timestamptz not null,
  window_end       timestamptz not null check (window_end > window_start),
  estimates        jsonb not null,                    -- labelled estimates: distance, duration, battery
  constraints      jsonb not null,                    -- which constraints were checked and their results
  rationale        text not null,
  simulation_mode  boolean not null default true check (simulation_mode),
  payload_hash     text not null,
  status           text not null default 'proposed' check (status in ('proposed','awaiting_approval','approved','rejected','superseded','expired')),
  created_at       timestamptz not null default now()
);

create table public.missions (
  id               uuid primary key default gen_random_uuid(),
  jurisdiction_id  uuid not null references public.jurisdictions(id),
  proposal_id      uuid not null unique references public.mission_proposals(id),
  approval_id      uuid not null unique references public.approval_requests(id),
  operation_id     uuid not null unique,
  robot_id         text not null references public.robots(id),
  simulation_mode  boolean not null default true check (simulation_mode),
  status           text not null default 'dispatched'
                   check (status in ('dispatched','running','paused_safety','returning','completed','aborted','failed')),
  seed             bigint not null default 0,         -- deterministic simulation seed
  scenario         text,
  dispatched_at    timestamptz not null default now(),
  started_at       timestamptz,
  ended_at         timestamptz,
  abort_reason     text
);

create or replace function lk.guard_simulated_mission()
returns trigger language plpgsql set search_path = '' as $$
declare m text; st text;
begin
  select mode into m from public.robots where id = new.robot_id;
  if m is distinct from 'simulated' then
    raise exception 'robot % is not simulated; real robot missions are unavailable', new.robot_id using errcode = 'P0001';
  end if;
  select status into st from public.approval_requests where id = new.approval_id;
  if st is distinct from 'consumed' then
    raise exception 'mission requires a consumed approval (got %)', st using errcode = 'P0001';
  end if;
  return new;
end $$;
create trigger mission_simulated_guard before insert on public.missions
for each row execute function lk.guard_simulated_mission();

create table public.mission_stops (
  mission_id       uuid not null references public.missions(id),
  seq              int not null,
  cell_id          text not null references public.lane_cells(id),
  planned_eta_s    real not null,
  status           text not null default 'pending' check (status in ('pending','arrived','swept','skipped','blocked')),
  swept_at         timestamptz,
  primary key (mission_id, seq)
);

create table public.mission_events (
  id               bigint generated always as identity primary key,
  mission_id       uuid not null references public.missions(id),
  jurisdiction_id  uuid not null references public.jurisdictions(id),
  event_id         text not null unique,
  at               timestamptz not null default now(),
  sim_time_s       real,
  kind             text not null,
  actor            text not null,
  detail           jsonb not null default '{}',
  provenance       text not null default 'SIMULATED' check (provenance in ('SIMULATED','OBSERVED_TEST','VERIFIED_FIELD'))
);
create index mission_events_idx on public.mission_events (mission_id, id);

alter table public.robots             enable row level security;
alter table public.robot_capabilities enable row level security;
alter table public.robot_status       enable row level security;
alter table public.robot_telemetry    enable row level security;
alter table public.robot_faults       enable row level security;
alter table public.work_orders        enable row level security;
alter table public.mission_proposals  enable row level security;
alter table public.missions           enable row level security;
alter table public.mission_stops      enable row level security;
alter table public.mission_events     enable row level security;

create policy "staff read robots"     on public.robots       for select to authenticated using (lk.has_permission(jurisdiction_id, 'fleet.view'));
create policy "staff read robot caps" on public.robot_capabilities for select to authenticated
  using (exists (select 1 from public.robots r where r.id = robot_id and lk.has_permission(r.jurisdiction_id, 'fleet.view')));
create policy "staff read robot status" on public.robot_status for select to authenticated using (lk.has_permission(jurisdiction_id, 'fleet.view'));
create policy "staff read telemetry"  on public.robot_telemetry for select to authenticated using (lk.has_permission(jurisdiction_id, 'telemetry.view'));
create policy "staff read faults"     on public.robot_faults for select to authenticated using (lk.has_permission(jurisdiction_id, 'fleet.view'));
create policy "staff read work orders" on public.work_orders for select to authenticated using (lk.has_permission(jurisdiction_id, 'ticket.view'));
create policy "staff read proposals"  on public.mission_proposals for select to authenticated using (lk.has_permission(jurisdiction_id, 'fleet.view'));
create policy "staff read missions"   on public.missions     for select to authenticated using (lk.has_permission(jurisdiction_id, 'fleet.view'));
create policy "staff read stops"      on public.mission_stops for select to authenticated
  using (exists (select 1 from public.missions m where m.id = mission_id and lk.has_permission(m.jurisdiction_id, 'fleet.view')));
create policy "staff read mission events" on public.mission_events for select to authenticated using (lk.has_permission(jurisdiction_id, 'fleet.view'));
