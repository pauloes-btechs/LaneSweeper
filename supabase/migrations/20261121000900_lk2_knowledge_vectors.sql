-- Knowledge (procedural memory: SOPs, playbooks), semantic memory and incident embeddings.
-- Vectors are vector(768). Every row records model, dimension and version so spaces never mix.
create table public.knowledge_documents (
  id               uuid primary key default gen_random_uuid(),
  jurisdiction_id  uuid references public.jurisdictions(id),   -- null = shared platform guidance
  slug             text not null,
  title            text not null,
  kind             text not null check (kind in ('sop','playbook','equipment_limit','policy','operating_window','lane_restriction')),
  source_url       text,
  published_on     date,
  review_status    text not null default 'draft' check (review_status in ('draft','approved','retired')),
  expires_on       date,
  binding          boolean not null default false,             -- binding policy: only humans edit, never an LLM
  version          int not null default 1,
  created_by       uuid references auth.users(id),
  created_at       timestamptz not null default now(),
  unique (jurisdiction_id, slug, version)
);

create table public.knowledge_chunks (
  id               uuid primary key default gen_random_uuid(),
  document_id      uuid not null references public.knowledge_documents(id) on delete cascade,
  jurisdiction_id  uuid references public.jurisdictions(id),
  ordinal          int not null,
  heading          text,
  content          text not null,
  tsv              tsvector generated always as (to_tsvector('english', coalesce(heading,'') || ' ' || content)) stored,
  embedding        extensions.vector(768),
  embed_model      text,
  embed_dim        int check (embed_dim is null or embed_dim = 768),
  embed_version    text,
  unique (document_id, ordinal)
);
create index knowledge_chunks_tsv_idx on public.knowledge_chunks using gin (tsv);
create index knowledge_chunks_vec_idx on public.knowledge_chunks using hnsw (embedding extensions.vector_cosine_ops);

create table public.incident_embeddings (
  id               uuid primary key default gen_random_uuid(),
  jurisdiction_id  uuid not null references public.jurisdictions(id),
  report_id        uuid references public.hazard_reports(id),
  incident_id      uuid references public.hazard_incidents(id),
  modality         text not null check (modality in ('text','image','text+image')),
  embedding        extensions.vector(768) not null,
  embed_model      text not null,
  embed_dim        int not null check (embed_dim = 768),
  embed_version    text not null,
  source           text not null,                    -- what was embedded (field names, never raw PII)
  created_at       timestamptz not null default now(),
  check (report_id is not null or incident_id is not null)
);
create index incident_embeddings_vec_idx on public.incident_embeddings using hnsw (embedding extensions.vector_cosine_ops);
create index incident_embeddings_juris_idx on public.incident_embeddings (jurisdiction_id, embed_model);

create table public.memory_entries (
  id               uuid primary key default gen_random_uuid(),
  jurisdiction_id  uuid not null references public.jurisdictions(id),
  scope            text not null check (scope in ('jurisdiction','user','cell','robot')),
  scope_ref        text not null,
  kind             text not null check (kind in ('case_summary','hazard_pattern','operational_note','operator_preference')),
  content          text not null check (char_length(content) <= 2000),
  embedding        extensions.vector(768),
  embed_model      text,
  consent_basis    text not null check (consent_basis in ('operator_entered','deidentified_case','explicit_opt_in')),
  created_by       text not null,
  expires_at       timestamptz not null default now() + interval '180 days',
  deleted_at       timestamptz,
  created_at       timestamptz not null default now()
);
create index memory_entries_scope_idx on public.memory_entries (jurisdiction_id, scope, scope_ref) where deleted_at is null;

-- Hybrid SOP retrieval: weighted reciprocal-rank fusion of full-text and cosine similarity.
-- Tenant filtering happens INSIDE the function (caller passes the verified jurisdiction).
create or replace function lk.hybrid_search_knowledge(p_jurisdiction uuid, p_query text, p_embedding extensions.vector(768),
                                                      p_limit int default 5, p_w_text real default 1.0, p_w_vec real default 1.0,
                                                      p_rrf_k int default 60)
returns table (chunk_id uuid, document_id uuid, title text, heading text, content text, source_url text,
               published_on date, text_rank int, vector_rank int, text_score real, vector_score real, fused real)
language sql stable security definer set search_path = '' as $$
  with eligible as (
    select c.*, d.title, d.source_url, d.published_on
    from public.knowledge_chunks c join public.knowledge_documents d on d.id = c.document_id
    where d.review_status = 'approved'
      and (d.expires_on is null or d.expires_on >= current_date)
      and (d.jurisdiction_id is null or d.jurisdiction_id = p_jurisdiction)
  ),
  t as (select id, ts_rank_cd(tsv, websearch_to_tsquery('english', p_query))::real s,
               row_number() over (order by ts_rank_cd(tsv, websearch_to_tsquery('english', p_query)) desc)::int r
        from eligible where tsv @@ websearch_to_tsquery('english', p_query) limit 50),
  v as (select id, (1 - (embedding operator(extensions.<=>) p_embedding))::real s,
               row_number() over (order by embedding operator(extensions.<=>) p_embedding)::int r
        from eligible where embedding is not null and p_embedding is not null limit 50)
  select e.id, e.document_id, e.title, e.heading, e.content, e.source_url, e.published_on,
         t.r, v.r, t.s, v.s,
         (coalesce(p_w_text / (p_rrf_k + t.r), 0) + coalesce(p_w_vec / (p_rrf_k + v.r), 0))::real as fused
  from eligible e left join t on t.id = e.id left join v on v.id = e.id
  where t.id is not null or v.id is not null
  order by fused desc
  limit p_limit
$$;

-- Similar past incidents: vector similarity PLUS hard filters on tenant, type, time and distance.
create or replace function lk.similar_incidents(p_jurisdiction uuid, p_embedding extensions.vector(768), p_model text,
                                               p_hazard text, p_lat double precision, p_lng double precision,
                                               p_radius_m double precision default 150, p_since interval default '72 hours',
                                               p_limit int default 5)
returns table (incident_id uuid, report_id uuid, similarity real, distance_m double precision, seen_at timestamptz, cell_id text)
language sql stable security definer set search_path = '' as $$
  select coalesce(ie.incident_id, l.incident_id), ie.report_id,
         (1 - (ie.embedding operator(extensions.<=>) p_embedding))::real,
         extensions.st_distance(r.raw_location, extensions.st_setsrid(extensions.st_makepoint(p_lng, p_lat), 4326)::extensions.geography),
         r.received_at, r.cell_id
  from public.incident_embeddings ie
  join public.hazard_reports r on r.id = ie.report_id
  left join public.incident_report_links l on l.report_id = r.id
  left join public.hazard_incidents i on i.id = coalesce(ie.incident_id, l.incident_id)
  where ie.jurisdiction_id = p_jurisdiction
    and ie.embed_model = p_model
    and r.received_at >= now() - p_since
    and (p_hazard is null or r.hazard_claimed = p_hazard or i.hazard_type = p_hazard)
    and (i.id is null or i.status in ('open','monitoring'))
    and extensions.st_dwithin(r.raw_location, extensions.st_setsrid(extensions.st_makepoint(p_lng, p_lat), 4326)::extensions.geography, p_radius_m)
  order by ie.embedding operator(extensions.<=>) p_embedding
  limit p_limit
$$;
revoke all on function lk.hybrid_search_knowledge(uuid, text, extensions.vector, int, real, real, int) from public, anon, authenticated;
revoke all on function lk.similar_incidents(uuid, extensions.vector, text, text, double precision, double precision, double precision, interval, int) from public, anon, authenticated;

alter table public.knowledge_documents enable row level security;
alter table public.knowledge_chunks    enable row level security;
alter table public.incident_embeddings enable row level security;
alter table public.memory_entries      enable row level security;

create policy "staff read knowledge docs" on public.knowledge_documents for select to authenticated
  using (jurisdiction_id is null or lk.has_permission(jurisdiction_id, 'knowledge.view'));
create policy "staff read knowledge chunks" on public.knowledge_chunks for select to authenticated
  using (jurisdiction_id is null or lk.has_permission(jurisdiction_id, 'knowledge.view'));
create policy "staff read incident vectors" on public.incident_embeddings for select to authenticated
  using (lk.has_permission(jurisdiction_id, 'evidence.view'));
create policy "staff read memory" on public.memory_entries for select to authenticated
  using (deleted_at is null and expires_at > now() and lk.has_permission(jurisdiction_id, 'ticket.view')
         and (scope <> 'user' or scope_ref = auth.uid()::text));
