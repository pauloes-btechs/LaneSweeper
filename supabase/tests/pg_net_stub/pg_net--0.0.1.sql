-- Stub: same signatures the LaneKeeper migrations use, but requests are only queued and
-- never sent. net._http_response stays empty unless a test inserts a fake response.
create schema if not exists net;
create table net.http_request_queue (
  id bigserial primary key, method text, url text, headers jsonb, body jsonb,
  timeout_milliseconds int, created_at timestamptz default now()
);
create table net._http_response (
  id bigint primary key, status_code int, content_type text, headers jsonb,
  content text, timed_out boolean default false, error_msg text, created timestamptz default now()
);
create function net.http_post(url text, body jsonb default '{}'::jsonb, params jsonb default '{}'::jsonb,
                              headers jsonb default '{}'::jsonb, timeout_milliseconds int default 5000)
returns bigint language sql as $$
  insert into net.http_request_queue (method, url, headers, body, timeout_milliseconds)
  values ('POST', url, headers, body, timeout_milliseconds) returning id
$$;
