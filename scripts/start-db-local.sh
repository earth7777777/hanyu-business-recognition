#!/usr/bin/env bash
set -euo pipefail

MARIADB_SAFE="${MARIADB_SAFE:-/opt/homebrew/opt/mariadb/bin/mariadbd-safe}"
MARIADB_DATADIR="${MARIADB_DATADIR:-/opt/homebrew/var/mysql}"
MARIADB_SOCKET="${MARIADB_SOCKET:-/tmp/mysql.sock}"
MARIADB_PORT="${MARIADB_PORT:-3306}"

if [[ ! -x "$MARIADB_SAFE" ]]; then
  echo "Cannot find MariaDB starter at: $MARIADB_SAFE"
  echo "Ask Codex to check the local MariaDB installation path."
  exit 1
fi

if [[ ! -d "$MARIADB_DATADIR" ]]; then
  echo "Cannot find MariaDB data folder at: $MARIADB_DATADIR"
  echo "Ask Codex to check the local MariaDB data location."
  exit 1
fi

echo "Starting Mac local MariaDB on port $MARIADB_PORT"
echo "This starts the local database only. It does not affect Aliyun MariaDB."

exec "$MARIADB_SAFE" \
  --datadir="$MARIADB_DATADIR" \
  --socket="$MARIADB_SOCKET" \
  --port="$MARIADB_PORT"
