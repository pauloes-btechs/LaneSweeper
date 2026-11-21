# Local Postgres 16 for `make db-docker`: pgvector's official image (Debian trixie) + PostGIS from the PGDG apt repo
# (already configured in the official postgres base) + the no-network pg_net stub.
# The previous base, postgis/postgis:16-3.4, is built on Debian bullseye, whose PGDG repo has been retired, so
# `apt-get update` failed there.
FROM pgvector/pgvector:0.8.7-pg16-trixie
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends postgresql-16-postgis-3 \
 && rm -rf /var/lib/apt/lists/*
# --chmod: checkouts on some disks (e.g. exFAT on macOS) give files owner-only modes the postgres user cannot read.
COPY --chmod=0644 supabase/tests/pg_net_stub/ /usr/share/postgresql/16/extension/
