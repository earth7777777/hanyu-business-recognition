#!/usr/bin/env bash
set -euo pipefail

MARIADB_ADMIN="${MARIADB_ADMIN:-/opt/homebrew/opt/mariadb/bin/mariadb-admin}"
MARIADB_SOCKET="${MARIADB_SOCKET:-/tmp/mysql.sock}"

if [[ ! -x "$MARIADB_ADMIN" ]]; then
  echo "Cannot find MariaDB admin tool at: $MARIADB_ADMIN"
  echo "Ask Codex to check the local MariaDB installation path."
  exit 1
fi

echo "Stopping Mac local MariaDB"
echo "This stops the local database only. It does not affect Aliyun MariaDB."

exec "$MARIADB_ADMIN" --socket="$MARIADB_SOCKET" shutdown
