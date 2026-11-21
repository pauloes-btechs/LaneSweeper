-- Public bucket for the official demo reel only (AI-generated / simulated footage of a fictional tenant).
-- Nothing else is written here; evidence and media stay in private buckets. Additive.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('demo', 'demo', true, 52428800, array['video/mp4','image/jpeg','image/png'])
on conflict (id) do nothing;
