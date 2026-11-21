-- Geography: tenant scoping on the original lane tables (additive nullable columns) plus
-- operational geography. Rollback: drop the new tables/function; drop column jurisdiction_id.
alter table public.lane_segments add column if not exists jurisdiction_id uuid references public.jurisdictions(id);
alter table public.lane_cells    add column if not exists jurisdiction_id uuid references public.jurisdictions(id);
alter table public.lane_cells    add column if not exists district_id text references public.district_boundaries(id);
create index if not exists lane_cells_jurisdiction_idx on public.lane_cells (jurisdiction_id);

-- Current operational condition of a cell. Public reads see only the generalized view (below).
create table public.lane_cell_status (
  cell_id          text primary key references public.lane_cells(id),
  jurisdiction_id  uuid not null references public.jurisdictions(id),
  condition        text not null default 'unknown'
                   check (condition in ('unknown','hazard_reported','hazard_confirmed','scheduled',
                                        'cleaning','cleared_unverified','partially_cleared','cleared_verified')),
  last_verified_at timestamptz,
  updated_at       timestamptz not null default now(),
  provenance       text not null default 'SIMULATED' check (provenance in ('SIMULATED','OBSERVED_TEST','VERIFIED_FIELD'))
);

create table public.depots (
  id               text primary key,
  jurisdiction_id  uuid not null references public.jurisdictions(id),
  name             text not null,
  location         extensions.geography(Point, 4326) not null,
  charging_ports   int not null default 1 check (charging_ports >= 0)
);

create table public.closures (
  id               uuid primary key default gen_random_uuid(),
  jurisdiction_id  uuid not null references public.jurisdictions(id),
  cell_ids         text[] not null,
  reason           text not null,
  starts_at        timestamptz not null,
  ends_at          timestamptz not null check (ends_at > starts_at),
  source           text not null default 'operator'
);
create index closures_window_idx on public.closures (jurisdiction_id, starts_at, ends_at);

create table public.restricted_zones (
  id               uuid primary key default gen_random_uuid(),
  jurisdiction_id  uuid not null references public.jurisdictions(id),
  name             text not null,
  kind             text not null check (kind in ('school_zone','event','construction','no_robot','private')),
  geom             extensions.geography(Polygon, 4326) not null,
  active           boolean not null default true
);
create index restricted_zones_geom_idx on public.restricted_zones using gist (geom);

-- Candidate lane cells for a reported point. Searches within max(accuracy, base radius), capped,
-- and reports whether the match is unambiguous. No snapping beyond the configured threshold.
create or replace function lk.match_lane_cells(lat double precision, lng double precision,
                                               accuracy_m double precision default null,
                                               jid uuid default null,
                                               base_radius_m double precision default 30,
                                               max_radius_m double precision default 75)
returns table (cell_id text, jurisdiction_id uuid, district_id text, distance_m double precision,
               search_radius_m double precision, rank int)
language sql stable security definer set search_path = '' as $$
  with p as (select extensions.st_setsrid(extensions.st_makepoint(lng, lat), 4326)::extensions.geography g,
                    least(greatest(coalesce(accuracy_m, 0), base_radius_m), max_radius_m) r)
  select c.id, c.jurisdiction_id, c.district_id, extensions.st_distance(c.geom, p.g), p.r,
         (row_number() over (order by extensions.st_distance(c.geom, p.g)))::int
  from public.lane_cells c, p
  where extensions.st_dwithin(c.geom, p.g, p.r)
    and (jid is null or c.jurisdiction_id = jid)
  order by extensions.st_distance(c.geom, p.g)
  limit 5
$$;
grant execute on function lk.match_lane_cells(double precision, double precision, double precision, uuid, double precision, double precision) to service_role;

alter table public.lane_cell_status  enable row level security;
alter table public.depots            enable row level security;
alter table public.closures          enable row level security;
alter table public.restricted_zones  enable row level security;

create policy "staff read cell status" on public.lane_cell_status for select to authenticated
  using (lk.has_permission(jurisdiction_id, 'ticket.view'));
create policy "staff read depots" on public.depots for select to authenticated
  using (lk.has_permission(jurisdiction_id, 'fleet.view'));
create policy "staff read closures" on public.closures for select to authenticated
  using (lk.has_permission(jurisdiction_id, 'ticket.view'));
create policy "staff read restricted zones" on public.restricted_zones for select to authenticated
  using (lk.has_permission(jurisdiction_id, 'fleet.view'));

-- Public, generalized hazard map: cell-level condition only, no reports, riders or timestamps finer than a day.
create view public.public_hazard_cells with (security_invoker = false) as
select s.cell_id, s.jurisdiction_id, s.condition,
       date_trunc('day', s.updated_at) as updated_day,
       extensions.st_asgeojson(extensions.st_centroid(c.geom::extensions.geometry), 4)::jsonb as centroid,
       s.provenance
from public.lane_cell_status s join public.lane_cells c on c.id = s.cell_id;
grant select on public.public_hazard_cells to anon, authenticated;
