-- AI-generated demo media (photoreal glasses POV clips, 4K stills, 3D textures). Additive only.
-- Generation is an explicit, admin-triggered, billable action; every asset is labelled AI-GENERATED.
create table public.media_assets (
  id               text primary key check (id ~ '^[a-z0-9-]{3,60}$'),
  kind             text not null check (kind in ('video','image','texture')),
  use_case         text not null,                       -- glasses_pov | evidence | twin_texture | hero
  label            text,                                -- ground truth for evaluation: glass | clear | confounder_*
  status           text not null default 'missing' check (status in ('missing','generating','ready','failed')),
  model            text,
  resolution       text,
  prompt_hash      text,
  storage_path     text,
  mime_type        text,
  bytes            bigint,
  interaction_id   text,
  cost_estimate_usd numeric(8,2),
  error            text,
  requested_by     uuid references auth.users(id),
  generated_at     timestamptz,
  updated_at       timestamptz not null default now(),
  provenance       text not null default 'AI_GENERATED' check (provenance = 'AI_GENERATED')
);
alter table public.media_assets enable row level security;   -- server-only; clients get short signed links

-- Private bucket; the browser fetches with short-lived signed URLs minted by the orchestrator.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('media', 'media', false, 52428800, array['video/mp4','image/png','image/jpeg','image/webp'])
on conflict (id) do nothing;
