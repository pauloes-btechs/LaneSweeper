FROM postgis/postgis:16-3.4
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends postgresql-16-pgvector && rm -rf /var/lib/apt/lists/*
COPY supabase/tests/pg_net_stub/ /usr/share/postgresql/16/extension/
