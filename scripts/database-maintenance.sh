#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
DB_SERVICE="${DB_SERVICE:-postgres}"
DB_USER="${DB_USER:-tradeuser}"
DB_NAME="${DB_NAME:-trading}"
BACKUP_DIR="${BACKUP_DIR:-${PROJECT_ROOT}/backups}"

usage() {
  cat <<'EOF'
Usage:
  scripts/database-maintenance.sh backup [output.dump]
  scripts/database-maintenance.sh restore <backup.dump>

Environment overrides:
  COMPOSE_FILE=docker-compose.dev.yml
  DB_SERVICE=postgres
  DB_USER=tradeuser
  DB_NAME=trading
  BACKUP_DIR=/path/to/backups

Examples:
  scripts/database-maintenance.sh backup
  scripts/database-maintenance.sh backup backups/manual.dump
  scripts/database-maintenance.sh restore backups/best-ai-trader-trading-20260613-153000.dump
  COMPOSE_FILE=docker-compose.dev.yml scripts/database-maintenance.sh backup
EOF
}

compose() {
  docker compose -f "${PROJECT_ROOT}/${COMPOSE_FILE}" "$@"
}

require_file() {
  local path="$1"
  if [[ ! -f "${path}" ]]; then
    printf 'Backup file not found: %s\n' "${path}" >&2
    exit 1
  fi
}

backup_database() {
  local output_path="${1:-}"
  if [[ -z "${output_path}" ]]; then
    mkdir -p "${BACKUP_DIR}"
    output_path="${BACKUP_DIR}/best-ai-trader-${DB_NAME}-$(date +%Y%m%d-%H%M%S).dump"
  fi

  mkdir -p "$(dirname "${output_path}")"
  printf 'Backing up %s/%s to %s\n' "${DB_SERVICE}" "${DB_NAME}" "${output_path}"
  compose exec -T "${DB_SERVICE}" pg_dump \
    --username "${DB_USER}" \
    --dbname "${DB_NAME}" \
    --format=custom \
    --no-owner \
    --no-privileges \
    > "${output_path}"
  printf 'Backup completed: %s\n' "${output_path}"
}

restore_database() {
  local input_path="$1"
  require_file "${input_path}"

  printf 'Restoring %s into %s/%s\n' "${input_path}" "${DB_SERVICE}" "${DB_NAME}"
  printf 'This will clean and replace database objects in %s. Type RESTORE to continue: ' "${DB_NAME}"
  local confirmation
  read -r confirmation
  if [[ "${confirmation}" != "RESTORE" ]]; then
    printf 'Restore cancelled.\n'
    exit 1
  fi

  compose exec -T "${DB_SERVICE}" pg_restore \
    --username "${DB_USER}" \
    --dbname "${DB_NAME}" \
    --clean \
    --if-exists \
    --no-owner \
    --no-privileges \
    --exit-on-error \
    < "${input_path}"
  printf 'Restore completed: %s\n' "${input_path}"
}

main() {
  local command="${1:-help}"
  case "${command}" in
    backup)
      backup_database "${2:-}"
      ;;
    restore)
      if [[ $# -ne 2 ]]; then
        usage
        exit 1
      fi
      restore_database "$2"
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
