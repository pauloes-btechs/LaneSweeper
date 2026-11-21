-- Local stand-in for the parts of a Supabase project that the migrations depend on.
-- Applied ONLY to local/CI databases before supabase/migrations/*. Never run against a hosted project
-- (hosted Supabase already provides all of this, and these definitions would conflict).
--
-- Provides: roles anon/authenticated/service_role, schemas auth/storage/extensions,
-- auth.uid()/auth.jwt()/auth.role() reading request.jwt.claims like PostgREST, auth.users,
-- storage.buckets/objects, and the supabase_realtime publication.

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin noinherit; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin noinherit; end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then create role service_role nologin noinherit bypassrls; end if;
end $$;

create schema if not exists extensions;
create schema if not exists auth;
create schema if not exists storage;
grant usage on schema extensions, auth, storage, public to anon, authenticated, service_role;

create extension if not exists pgcrypto with schema extensions;

create table if not exists auth.users (
  id uuid primary key default extensions.gen_random_uuid(),
  email text unique,
  raw_app_meta_data jsonb not null default '{}',
  created_at timestamptz not null default now()
);

create or replace function auth.jwt() returns jsonb language sql stable as $$
  select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb
$$;
create or replace function auth.uid() returns uuid language sql stable as $$
  select nullif(auth.jwt() ->> 'sub', '')::uuid
$$;
create or replace function auth.role() returns text language sql stable as $$
  select coalesce(auth.jwt() ->> 'role', 'anon')
$$;
grant execute on all functions in schema auth to anon, authenticated, service_role;

create table if not exists storage.buckets (
  id text primary key, name text not null, public boolean default false,
  file_size_limit bigint, allowed_mime_types text[], created_at timestamptz default now()
);
create table if not exists storage.objects (
  id uuid primary key default extensions.gen_random_uuid(),
  bucket_id text references storage.buckets(id), name text, owner uuid,
  metadata jsonb, created_at timestamptz default now()
);

do $$ begin
  if not exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    create publication supabase_realtime;
  end if;
end $$;

-- Supabase grants table privileges to these roles by default; RLS then decides row access.
alter default privileges in schema public grant select, insert, update, delete on tables to anon, authenticated, service_role;
alter default privileges in schema public grant usage, select on sequences to anon, authenticated, service_role;
alter default privileges in schema public grant execute on functions to anon, authenticated, service_role;
