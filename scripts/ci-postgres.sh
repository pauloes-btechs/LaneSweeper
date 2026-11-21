#!/usr/bin/env bash
# CI only: install and start Postgres 16 + PostGIS + pgvector on an Ubuntu 24.04 runner.
set -euo pipefail
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql-16 postgresql-16-postgis-3 postgresql-16-pgvector >/dev/null
sudo PATH=/usr/lib/postgresql/16/bin:$PATH "$(dirname "$0")/../supabase/tests/install-pg-net-stub.sh"
sudo systemctl start postgresql || sudo service postgresql start
sudo -u postgres psql -qc "alter user postgres password 'postgres';"
