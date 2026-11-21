-- Light web variant (2K WebP) next to each 4K still/texture, so pages don't download 15 MB images. Additive.
alter table public.media_assets add column web_path text;
alter table public.media_assets add column web_bytes bigint;
