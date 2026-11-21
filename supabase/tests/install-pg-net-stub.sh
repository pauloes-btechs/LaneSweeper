#!/usr/bin/env bash
# Installs the no-network pg_net stub into the local Postgres extension directory (needs write access).
set -euo pipefail
DIR="$(pg_config --sharedir)/extension"
cp "$(dirname "$0")"/pg_net_stub/pg_net* "$DIR"/
echo "pg_net stub installed in $DIR"
