-- Public hazard map without a SECURITY DEFINER view (Supabase advisor 0010).
-- The privileged read moves into one narrow function that returns only generalized, cell-level fields;
-- the view becomes security_invoker and simply selects from it. Same columns, same public contract.
create or replace function lk.public_hazard_cells()
returns table (cell_id text, jurisdiction_id uuid, condition text, updated_day timestamptz, centroid jsonb, provenance text)
language sql stable security definer set search_path = '' as $$
  select s.cell_id, s.jurisdiction_id, s.condition,
         date_trunc('day', s.updated_at),
         extensions.st_asgeojson(extensions.st_centroid(c.geom::extensions.geometry), 4)::jsonb,
         s.provenance
  from public.lane_cell_status s join public.lane_cells c on c.id = s.cell_id
$$;
revoke all on function lk.public_hazard_cells() from public;
grant execute on function lk.public_hazard_cells() to anon, authenticated, service_role;

create or replace view public.public_hazard_cells with (security_invoker = true) as
select * from lk.public_hazard_cells();
grant select on public.public_hazard_cells to anon, authenticated;
