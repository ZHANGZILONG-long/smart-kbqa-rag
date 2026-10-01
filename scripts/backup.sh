#!/usr/bin/env bash
# 备份 MySQL + media + chroma（Docker 或本机均可改环境变量）
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="${BACKUP_DIR:-$ROOT_DIR/backups}/$STAMP"
mkdir -p "$OUT_DIR"

DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-3306}"
DB_NAME="${DB_NAME:-smartkbqa}"
DB_USER="${DB_USER:-root}"
DB_PASSWORD="${DB_PASSWORD:-}"

echo "[backup] writing to $OUT_DIR"

if command -v mysqldump >/dev/null 2>&1; then
  export MYSQL_PWD="$DB_PASSWORD"
  mysqldump -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" \
    --single-transaction --routines --triggers "$DB_NAME" \
    | gzip > "$OUT_DIR/mysql_${DB_NAME}.sql.gz"
  echo "[backup] mysql dump ok"
elif command -v docker >/dev/null 2>&1 && docker compose ps db >/dev/null 2>&1; then
  docker compose exec -T db mysqldump -uroot -p"${DB_ROOT_PASSWORD:-rootpass}" \
    --single-transaction "$DB_NAME" | gzip > "$OUT_DIR/mysql_${DB_NAME}.sql.gz"
  echo "[backup] mysql dump via docker ok"
else
  echo "[backup] skip mysql: mysqldump/docker not available" >&2
fi

if [ -d "$ROOT_DIR/media" ]; then
  tar -czf "$OUT_DIR/media.tar.gz" -C "$ROOT_DIR" media
  echo "[backup] media ok"
fi

if [ -d "$ROOT_DIR/data/chroma" ]; then
  tar -czf "$OUT_DIR/chroma.tar.gz" -C "$ROOT_DIR" data/chroma
  echo "[backup] chroma ok"
fi

# 保留最近 14 份
find "${BACKUP_DIR:-$ROOT_DIR/backups}" -mindepth 1 -maxdepth 1 -type d \
  | sort -r | tail -n +15 | xargs -r rm -rf

echo "[backup] done: $OUT_DIR"
ls -lah "$OUT_DIR"
