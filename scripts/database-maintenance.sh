#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-${PWD}}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
BACKUP_DIR="${BACKUP_DIR:-${PROJECT_ROOT}/backups}"

APP_SERVICES="${APP_SERVICES:-backend memo}"
BACKEND_DB_SERVICE="${BACKEND_DB_SERVICE:-postgres}"
BACKEND_DB_USER="${BACKEND_DB_USER:-tradeuser}"
BACKEND_DB_NAME="${BACKEND_DB_NAME:-trading}"
MEMO_DB_SERVICE="${MEMO_DB_SERVICE:-memo-postgres}"
MEMO_DB_USER="${MEMO_DB_USER:-tradeuser}"
MEMO_DB_NAME="${MEMO_DB_NAME:-memory}"

usage() {
  cat <<'EOF'
Usage:
  scripts/database-maintenance.sh backup [backup-dir]
  scripts/database-maintenance.sh restore <backup-dir>

This script stops backend and memo during backup or restore, then starts them again.
Run it from the repository root, or set PROJECT_ROOT=/path/to/Best-AI-Trader.

Environment overrides:
  PROJECT_ROOT=/path/to/Best-AI-Trader
  COMPOSE_FILE=docker-compose.dev.yml
  BACKUP_DIR=/path/to/backups
  APP_SERVICES="backend memo"
  BACKEND_DB_SERVICE=postgres
  BACKEND_DB_USER=tradeuser
  BACKEND_DB_NAME=trading
  MEMO_DB_SERVICE=memo-postgres
  MEMO_DB_USER=tradeuser
  MEMO_DB_NAME=memory

Examples:
  scripts/database-maintenance.sh backup
  scripts/database-maintenance.sh backup backups/manual-20260613
  scripts/database-maintenance.sh restore backups/best-ai-trader-20260613-153000
  COMPOSE_FILE=docker-compose.dev.yml scripts/database-maintenance.sh backup
EOF
}

compose() {
  docker compose -f "${PROJECT_ROOT}/${COMPOSE_FILE}" "$@"
}

require_project_root() {
  if [[ ! -f "${PROJECT_ROOT}/${COMPOSE_FILE}" ]]; then
    printf 'Compose file not found: %s\n' "${PROJECT_ROOT}/${COMPOSE_FILE}" >&2
    printf 'Run from the repository root or set PROJECT_ROOT.\n' >&2
    exit 1
  fi
}

confirm_downtime() {
  local action="$1"
  printf '%s requires stopping services: %s\n' "${action}" "${APP_SERVICES}"
  printf 'Type %s to stop services and continue: ' "${action}"
  local confirmation
  read -r confirmation
  if [[ "${confirmation}" != "${action}" ]]; then
    printf '%s cancelled.\n' "${action}"
    exit 1
  fi
}

stop_app_services() {
  printf 'Stopping services: %s\n' "${APP_SERVICES}"
  compose stop ${APP_SERVICES}
}

start_app_services() {
  printf 'Starting services: %s\n' "${APP_SERVICES}"
  compose up -d ${APP_SERVICES}
}

dump_database() {
  local service="$1"
  local user="$2"
  local database="$3"
  local output_path="$4"

  printf 'Backing up %s/%s to %s\n' "${service}" "${database}" "${output_path}"
  compose exec -T "${service}" pg_dump \
    --username "${user}" \
    --dbname "${database}" \
    --format=custom \
    --no-owner \
    --no-privileges \
    > "${output_path}"
}

restore_database() {
  local service="$1"
  local user="$2"
  local database="$3"
  local input_path="$4"

  if [[ ! -f "${input_path}" ]]; then
    printf 'Backup file not found: %s\n' "${input_path}" >&2
    exit 1
  fi

  printf 'Restoring %s into %s/%s\n' "${input_path}" "${service}" "${database}"
  compose exec -T "${service}" pg_restore \
    --username "${user}" \
    --dbname "${database}" \
    --clean \
    --if-exists \
    --no-owner \
    --no-privileges \
    --exit-on-error \
    < "${input_path}"
}

backup_all() {
  local output_dir="${1:-}"
  if [[ -z "${output_dir}" ]]; then
    output_dir="${BACKUP_DIR}/best-ai-trader-$(date +%Y%m%d-%H%M%S)"
  fi

  require_project_root
  confirm_downtime "BACKUP"
  mkdir -p "${output_dir}"
  stop_app_services
  trap start_app_services EXIT

  dump_database "${BACKEND_DB_SERVICE}" "${BACKEND_DB_USER}" "${BACKEND_DB_NAME}" "${output_dir}/backend.dump"
  dump_database "${MEMO_DB_SERVICE}" "${MEMO_DB_USER}" "${MEMO_DB_NAME}" "${output_dir}/memo.dump"
  cat > "${output_dir}/manifest.txt" <<EOF
created_at=$(date -Iseconds)
compose_file=${COMPOSE_FILE}
backend_db_service=${BACKEND_DB_SERVICE}
backend_db_name=${BACKEND_DB_NAME}
memo_db_service=${MEMO_DB_SERVICE}
memo_db_name=${MEMO_DB_NAME}
EOF
  printf 'Backup completed: %s\n' "${output_dir}"
}

restore_all() {
  local input_dir="$1"

  require_project_root
  if [[ ! -d "${input_dir}" ]]; then
    printf 'Backup directory not found: %s\n' "${input_dir}" >&2
    exit 1
  fi

  confirm_downtime "RESTORE"
  stop_app_services
  trap start_app_services EXIT

  restore_database "${BACKEND_DB_SERVICE}" "${BACKEND_DB_USER}" "${BACKEND_DB_NAME}" "${input_dir}/backend.dump"
  restore_database "${MEMO_DB_SERVICE}" "${MEMO_DB_USER}" "${MEMO_DB_NAME}" "${input_dir}/memo.dump"
  printf 'Restore completed: %s\n' "${input_dir}"
}

main() {
  local command="${1:-help}"
  case "${command}" in
    backup)
      if [[ $# -gt 2 ]]; then
        usage
        exit 1
      fi
      backup_all "${2:-}"
      ;;
    restore)
      if [[ $# -ne 2 ]]; then
        usage
        exit 1
      fi
      restore_all "$2"
      ;;
    help|--help|-h)
      usage
      ;;
    *)
      usage
      exit 1
      ;;
  esac
}

main "$@"
