-- LaneKeeper 2.0 — tenancy and authority. Additive: creates new objects only.
-- Rollback: drop schema lk cascade; drop table public.role_permissions, public.municipal_memberships,
--           public.district_boundaries, public.departments, public.jurisdictions cascade;
create extension if not exists vector with schema extensions;
create schema if not exists lk;          -- helper functions used by RLS (no tables, no public API)
grant usage on schema lk to anon, authenticated, service_role;

create table public.jurisdictions (
  id          uuid primary key default gen_random_uuid(),
  slug        text unique not null check (slug ~ '^[a-z0-9-]{2,40}$'),
  name        text not null,
  kind        text not null default 'city' check (kind in ('city','county','campus','bid','private_row')),
  timezone    text not null default 'America/New_York',
  boundary    extensions.geography(MultiPolygon, 4326),
  synthetic   boolean not null default false,
  created_at  timestamptz not null default now()
);

create table public.departments (
  id               uuid primary key default gen_random_uuid(),
  jurisdiction_id  uuid not null references public.jurisdictions(id),
  code             text not null check (code ~ '^[A-Z0-9_]{2,16}$'),
  name             text not null,
  handles          text[] not null default '{}',  -- hazard categories this department owns
  unique (jurisdiction_id, code)
);

create table public.district_boundaries (
  id               text primary key,
  jurisdiction_id  uuid not null references public.jurisdictions(id),
  name             text not null,
  geom             extensions.geography(MultiPolygon, 4326) not null
);
create index district_boundaries_geom_idx on public.district_boundaries using gist (geom);

create table public.role_permissions (
  role        text not null check (role in ('public_rider','authorized_reporter','city_viewer','city_operator',
                'city_dispatcher','city_supervisor','fleet_technician','city_admin','platform_admin')),
  permission  text not null check (permission ~ '^[a-z_]+\.[a-z_]+$'),
  primary key (role, permission)
);

create table public.municipal_memberships (
  id               uuid primary key default gen_random_uuid(),
  user_id          uuid not null references auth.users(id),
  jurisdiction_id  uuid not null references public.jurisdictions(id),
  department_id    uuid references public.departments(id),
  role             text not null,
  district_ids     text[] not null default '{}',   -- empty = whole jurisdiction
  status           text not null default 'active' check (status in ('active','suspended','revoked')),
  valid_from       timestamptz not null default now(),
  valid_until      timestamptz,
  granted_by       uuid references auth.users(id),
  revoked_at       timestamptz,
  revoked_reason   text,
  created_at       timestamptz not null default now(),
  unique (user_id, jurisdiction_id, role)
);
create index memberships_user_idx on public.municipal_memberships (user_id) where status = 'active';

-- Is the current authenticated user an active member of the jurisdiction with this permission?
-- Reads server-side membership; never trusts role/tenant claims supplied by a client.
create or replace function lk.has_permission(jid uuid, perm text)
returns boolean language sql stable security definer set search_path = '' as $$
  select exists (
    select 1
    from public.municipal_memberships m
    join public.role_permissions rp on rp.role = m.role and rp.permission = perm
    where m.user_id = auth.uid()
      and m.jurisdiction_id = jid
      and m.status = 'active'
      and m.valid_from <= now()
      and (m.valid_until is null or m.valid_until > now())
  )
$$;

create or replace function lk.member_jurisdictions(perm text)
returns setof uuid language sql stable security definer set search_path = '' as $$
  select m.jurisdiction_id
  from public.municipal_memberships m
  join public.role_permissions rp on rp.role = m.role and rp.permission = perm
  where m.user_id = auth.uid() and m.status = 'active'
    and m.valid_from <= now() and (m.valid_until is null or m.valid_until > now())
$$;
revoke all on function lk.has_permission(uuid, text) from public;
revoke all on function lk.member_jurisdictions(text) from public;
grant execute on function lk.has_permission(uuid, text) to authenticated, service_role;
grant execute on function lk.member_jurisdictions(text) to authenticated, service_role;

alter table public.jurisdictions         enable row level security;
alter table public.departments           enable row level security;
alter table public.district_boundaries   enable row level security;
alter table public.role_permissions      enable row level security;
alter table public.municipal_memberships enable row level security;

create policy "public reads jurisdictions" on public.jurisdictions for select to anon, authenticated using (true);
create policy "public reads departments"   on public.departments   for select to anon, authenticated using (true);
create policy "public reads districts"     on public.district_boundaries for select to anon, authenticated using (true);
create policy "members read permission matrix" on public.role_permissions for select to authenticated using (true);
create policy "users read own memberships" on public.municipal_memberships for select to authenticated
  using (user_id = auth.uid() or lk.has_permission(jurisdiction_id, 'admin.membership'));
-- membership writes go through the orchestrator (service role) so every change is audited
