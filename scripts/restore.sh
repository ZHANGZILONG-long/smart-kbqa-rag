#!/usr/bin/env bash
# 从 backups/<stamp> 恢复（慎用，会覆盖数据）
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${1:-}"
if [ -z "$SRC" ] || [ ! -d "$SRC" ]; then
  echo "Usage: $0 backups/YYYYMMDD_HHMMSS"
  exit 1
fi

DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-3306}"
DB_NAME="${DB_NAME:-smartkbqa}"
DB_USER="${DB_USER:-root}"
DB_PASSWORD="${DB_PASSWORD:-}"

if [ -f "$SRC/mysql_${DB_NAME}.sql.gz" ]; then
  export MYSQL_PWD="$DB_PASSWORD"
  gunzip -c "$SRC/mysql_${DB_NAME}.sql.gz" | mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" "$DB_NAME"
  echo "[restore] mysql ok"
fi
if [ -f "$SRC/media.tar.gz" ]; then
  tar -xzf "$SRC/media.tar.gz" -C "$ROOT_DIR"
  echo "[restore] media ok"
fi
if [ -f "$SRC/chroma.tar.gz" ]; then
  tar -xzf "$SRC/chroma.tar.gz" -C "$ROOT_DIR"
  echo "[restore] chroma ok"
fi
echo "[restore] done"
