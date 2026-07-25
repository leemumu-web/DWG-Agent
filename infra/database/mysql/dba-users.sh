#!/bin/bash
set -euo pipefail

: "${MYSQL_ROOT_PASSWORD:?MYSQL_ROOT_PASSWORD is required}"
: "${DBA_MYSQL_ADMIN_PASSWORD:?DBA_MYSQL_ADMIN_PASSWORD is required}"
: "${DBA_MYSQL_READER_PASSWORD:?DBA_MYSQL_READER_PASSWORD is required}"

sql_escape() {
  printf '%s' "$1" | sed "s/'/''/g"
}

admin_password="$(sql_escape "$DBA_MYSQL_ADMIN_PASSWORD")"
reader_password="$(sql_escape "$DBA_MYSQL_READER_PASSWORD")"

env -u MYSQL_HOST mysql \
  --protocol=socket \
  --socket=/var/lib/mysql/mysql.sock \
  -u root \
  -p"${MYSQL_ROOT_PASSWORD}" <<SQL
CREATE USER IF NOT EXISTS 'dwg_console_admin'@'%' IDENTIFIED BY '${admin_password}';
ALTER USER 'dwg_console_admin'@'%' IDENTIFIED BY '${admin_password}';
CREATE USER IF NOT EXISTS 'dwg_console_reader'@'%' IDENTIFIED BY '${reader_password}';
ALTER USER 'dwg_console_reader'@'%' IDENTIFIED BY '${reader_password}';
GRANT ALL PRIVILEGES ON dwg_agent.* TO 'dwg_console_admin'@'%';
GRANT SELECT, SHOW VIEW ON dwg_agent.* TO 'dwg_console_reader'@'%';
FLUSH PRIVILEGES;
SQL
