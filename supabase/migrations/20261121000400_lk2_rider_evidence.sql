-- Rider / glasses / operator evidence. All inserts go through the orchestrator (service role) so
-- validation, consent, rate limits and idempotency can't be skipped. The original rider_reports
-- table is untouched; the intake agent can mirror SMS reports from it.
create table public.source_devices (
  id               uuid primary key default gen_random_uuid(),
  kind             text not null check (kind in ('rider_phone','glasses_sim','glasses_device','robot_sim','robot','operator_console','sms_gateway')),
  label            text,
  owner_user_id    uuid references auth.users(id),
  key_hash         text,                              -- hash of the device link key; never the key
  firmware         text,
  capabilities     jsonb not null default '{}',
  trust_level      text not null default 'unverified' check (trust_level in ('unverified','registered','municipal')),
  revoked_at       timestamptz,
  created_at       timestamptz not null default now()
);

create table public.report_consent (
  id               uuid primary key default gen_random_uuid(),
  subject_hash     text not null,                     -- salted hash of device/user, not an identity
  policy_version   text not null,
  share_image      boolean not null,
  share_location   boolean not null,
  allow_learning   boolean not null default false,    -- may de-identified evidence train/evaluate models?
  notify_status    boolean not null default false,
  granted_at       timestamptz not null default now(),
  withdrawn_at     timestamptz
);

create table public.hazard_reports (
  id                 uuid primary key default gen_random_uuid(),
  jurisdiction_id    uuid references public.jurisdictions(id),
  source_kind        text not null check (source_kind in ('rider_app','rider_guest','glasses_sim','glasses_device',
                                                         'sms','operator','robot','robot_sim','external')),
  source_device_id   uuid references public.source_devices(id),
  reporter_user_id   uuid references auth.users(id),
  reporter_hash      text,
  idempotency_key    text not null unique check (char_length(idempotency_key) between 8 and 128),
  external_event_id  text,
  consent_id         uuid references public.report_consent(id),
  -- restricted: raw volunteered position (never exposed publicly)
  raw_location       extensions.geography(Point, 4326),
  accuracy_m         real check (accuracy_m is null or accuracy_m >= 0),
  heading_deg        real check (heading_deg is null or (heading_deg >= 0 and heading_deg < 360)),
  speed_mps          real,
  location_source    text check (location_source in ('device_gps','phone_gps','exif','pin','cross_streets','robot_odometry','none')),
  observed_at        timestamptz,
  received_at        timestamptz not null default now(),
  hazard_claimed     text check (hazard_claimed in ('glass','debris','branch','litter','pothole','water','obstruction','markings','construction','other')),
  note               text check (char_length(note) <= 500),
  local_detector     jsonb,                           -- on-device candidate output, if any
  cell_id            text references public.lane_cells(id),
  match_distance_m   real,
  match_status       text check (match_status in ('matched','ambiguous','outside_lanes','boundary_review','no_location')),
  status             text not null default 'received'
                     check (status in ('received','validated','rejected','linked','closed')),
  reject_reason      text,
  provenance         text not null default 'SIMULATED' check (provenance in ('SIMULATED','OBSERVED_TEST','VERIFIED_FIELD')),
  retention_until    timestamptz not null default now() + interval '30 days',
  unique (source_kind, external_event_id)
);
create index hazard_reports_loc_idx on public.hazard_reports using gist (raw_location);
create index hazard_reports_cell_idx on public.hazard_reports (cell_id, received_at desc);
create index hazard_reports_juris_idx on public.hazard_reports (jurisdiction_id, received_at desc);
create index hazard_reports_reporter_idx on public.hazard_reports (reporter_hash, received_at desc);

create table public.report_media (
  id               uuid primary key default gen_random_uuid(),
  report_id        uuid not null references public.hazard_reports(id) on delete cascade,
  jurisdiction_id  uuid references public.jurisdictions(id),
  storage_bucket   text not null default 'evidence',
  storage_path     text not null,
  mime_type        text not null check (mime_type in ('image/jpeg','image/png','image/webp')),
  bytes            int check (bytes > 0 and bytes <= 4194304),
  sha256           text not null,
  deidentified     boolean not null default false,
  fixture_id       text,                               -- set when the image is a labelled demo fixture
  created_at       timestamptz not null default now(),
  unique (report_id, sha256)
);

-- private evidence bucket (short signed URLs are minted server-side)
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('evidence', 'evidence', false, 4194304, array['image/jpeg','image/png','image/webp'])
on conflict (id) do nothing;

alter table public.source_devices enable row level security;
alter table public.report_consent enable row level security;
alter table public.hazard_reports enable row level security;
alter table public.report_media   enable row level security;

create policy "staff read reports" on public.hazard_reports for select to authenticated
  using (jurisdiction_id is not null and lk.has_permission(jurisdiction_id, 'evidence.view'));
create policy "reporters read own reports" on public.hazard_reports for select to authenticated
  using (reporter_user_id = auth.uid());
create policy "staff read report media" on public.report_media for select to authenticated
  using (jurisdiction_id is not null and lk.has_permission(jurisdiction_id, 'evidence.view'));
create policy "owners read own devices" on public.source_devices for select to authenticated
  using (owner_user_id = auth.uid());
-- report_consent: no client access at all; only the orchestrator reads consent.
revoke all on public.report_consent from anon, authenticated;
