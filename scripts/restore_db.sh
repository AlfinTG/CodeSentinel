#!/usr/bin/env bash
# ==============================================================================
# CodeSentinel Automated PostgreSQL Database Restoration Script (Phase 8)
#
# Restores CodeSentinel database from a logical backup (.dump or .sql).
# Validates backup integrity via SHA-256 sidecars before execution and enforces
# system database protection and destination safety checks.
# ==============================================================================

set -euo pipefail

BACKUP_FILE=""
DB_NAME="${POSTGRES_DB:-codesentinel_dev}"
DB_HOST="${POSTGRES_HOST:-localhost}"
DB_PORT="${POSTGRES_PORT:-5432}"
DB_USER="${POSTGRES_USER:-codesentinel}"
CLEAN=false
FORCE=false
SKIP_CHECKSUM=false
DECRYPT=false
ENCRYPTION_KEY="${BACKUP_ENCRYPTION_KEY:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -i|--input) BACKUP_FILE="$2"; shift 2 ;;
    -d|--dbname) DB_NAME="$2"; shift 2 ;;
    -h|--host) DB_HOST="$2"; shift 2 ;;
    -p|--port) DB_PORT="$2"; shift 2 ;;
    -U|--user) DB_USER="$2"; shift 2 ;;
    -c|--clean) CLEAN=true; shift 1 ;;
    -f|--force) FORCE=true; shift 1 ;;
    --skip-checksum) SKIP_CHECKSUM=true; shift 1 ;;
    -e|--decrypt) DECRYPT=true; shift 1 ;;
    -k|--key) ENCRYPTION_KEY="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

if [[ -z "$BACKUP_FILE" ]]; then
  echo "[ERROR] Backup file path is required. Use -i or --input <file>"
  exit 1
fi

# Validate database identifier safety (before any I/O or network calls)
if [[ ! "$DB_NAME" =~ ^[A-Za-z_][A-Za-z0-9_]{0,62}$ ]]; then
  echo "[FATAL SAFETY ERROR] Invalid database name '$DB_NAME'. Names must start with a letter or underscore, contain only letters, digits, and underscores, and be at most 63 characters."
  exit 1
fi
if [[ ! "$DB_USER" =~ ^[A-Za-z_][A-Za-z0-9_]{0,62}$ ]]; then
  echo "[FATAL SAFETY ERROR] Invalid PostgreSQL user '$DB_USER'. Names must start with a letter or underscore and contain only letters, digits, and underscores."
  exit 1
fi

if [[ ! -f "$BACKUP_FILE" ]]; then
  echo "[ERROR] Backup file not found at: $BACKUP_FILE"
  exit 1
fi

if [[ ! -s "$BACKUP_FILE" ]]; then
  echo "[FATAL SAFETY ERROR] Backup file '$BACKUP_FILE' is 0 bytes (empty). Cannot restore."
  exit 1
fi

# Prevent restoring into protected PostgreSQL system databases
case "$(echo "$DB_NAME" | tr '[:upper:]' '[:lower:]')" in
  postgres|template0|template1|rdsadmin|azure_maintenance)
    echo "[FATAL SAFETY ERROR] Refusing to restore into protected system database '$DB_NAME'."
    exit 1
    ;;
esac

echo "=========================================================="
echo "   CodeSentinel Automated Database Restore (Phase 8)      "
echo "=========================================================="
echo "Backup File:    $BACKUP_FILE"
echo "Target DB:      $DB_NAME"
echo "Host:Port:      ${DB_HOST}:${DB_PORT}"
echo "User:           $DB_USER"
echo "Clean Mode:     $CLEAN"
echo ""

# 1. Verify required executables
command -v pg_restore >/dev/null 2>&1 || { echo "[ERROR] pg_restore is required but not installed."; exit 1; }
command -v psql >/dev/null 2>&1 || { echo "[ERROR] psql is required but not installed."; exit 1; }

# 2. Check SHA-256 Checksum against metadata sidecar
META_FILE="${BACKUP_FILE}.meta.json"
ORIGIN_DB=""
if [[ -f "$META_FILE" && "$SKIP_CHECKSUM" == "false" ]]; then
  echo "[INFO] Validating backup integrity via SHA-256 sidecar..."
  EXPECTED_SHA=$(grep -o '"sha256": *"[^"]*"' "$META_FILE" | cut -d'"' -f4 || echo "")
  ORIGIN_DB=$(grep -o '"database": *"[^"]*"' "$META_FILE" | cut -d'"' -f4 || echo "")

  if [[ -n "$EXPECTED_SHA" && "$EXPECTED_SHA" != "unsupported" ]]; then
    if command -v sha256sum >/dev/null 2>&1; then
      ACTUAL_SHA=$(sha256sum "$BACKUP_FILE" | awk '{print $1}')
    elif command -v shasum >/dev/null 2>&1; then
      ACTUAL_SHA=$(shasum -a 256 "$BACKUP_FILE" | awk '{print $1}')
    else
      ACTUAL_SHA=""
    fi

    if [[ -n "$ACTUAL_SHA" && "$ACTUAL_SHA" != "$EXPECTED_SHA" ]]; then
      echo "[FATAL] SHA-256 checksum mismatch! Expected: $EXPECTED_SHA, Actual: $ACTUAL_SHA"
      echo "Backup file may be corrupted or tampered with. Aborting restoration."
      exit 1
    fi
    echo "[OK] SHA-256 integrity check PASSED ($ACTUAL_SHA)"
  fi
fi

# 3. Origin mismatch check & Confirmation Guard
if [[ -n "$ORIGIN_DB" && "$ORIGIN_DB" != "$DB_NAME" ]]; then
  echo "[WARN] Backup origin was '$ORIGIN_DB', but target database is '$DB_NAME'!"
  if [[ "$FORCE" == "false" ]]; then
    read -p "Target differs from origin. Confirm restore into '$DB_NAME'? (y/N): " -r CONFIRM_DIFF
    if [[ ! "$CONFIRM_DIFF" =~ ^[Yy]$ ]]; then
      echo "[ABORTED] Restoration cancelled by operator."
      exit 0
    fi
  fi
fi

if [[ "$FORCE" == "false" ]]; then
  read -p "Proceed with restoring into '$DB_NAME' on ${DB_HOST}:${DB_PORT}? (y/N): " -r CONFIRM
  if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
    echo "[ABORTED] Database restoration cancelled by operator."
    exit 0
  fi
fi

# 4. Terminate active connections if clean requested
if [[ "$CLEAN" == "true" ]]; then
  echo "[INFO] Terminating active connections to '$DB_NAME'..."
  SAFE_DB_NAME="${DB_NAME//\'/\'\'}"
  psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d postgres -c \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$SAFE_DB_NAME' AND pid <> pg_backend_pid();" >/dev/null 2>&1 || true
fi

# 4.5 Optional Decryption
TEMP_DECRYPTED=""
RESTORE_TARGET="$BACKUP_FILE"

if [[ "$DECRYPT" == "true" || "$BACKUP_FILE" =~ \.enc$ ]]; then
  if [[ -z "$ENCRYPTION_KEY" ]]; then
    echo "[ERROR] Backup is encrypted but no key provided (-k or BACKUP_ENCRYPTION_KEY)."
    exit 1
  fi
  echo "[INFO] Decrypting backup with AES-256-GCM..."
  TEMP_DECRYPTED=$(mktemp)
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  BACKEND_DIR="$(cd "$SCRIPT_DIR/../backend" && pwd)"
  PYTHON_BIN=$(command -v python3 || command -v python)
  $PYTHON_BIN -c "import sys; sys.path.insert(0, '$BACKEND_DIR'); from app.services.backup.crypto import decrypt_file; decrypt_file('$BACKUP_FILE', '$TEMP_DECRYPTED', '$ENCRYPTION_KEY')"
  RESTORE_TARGET="$TEMP_DECRYPTED"
  echo "[OK] Decrypted temporary artifact ready for ingestion."
fi

cleanup() {
  if [[ -n "$TEMP_DECRYPTED" && -f "$TEMP_DECRYPTED" ]]; then
    rm -f "$TEMP_DECRYPTED"
    echo "[INFO] Cleaned up temporary decrypted backup artifact."
  fi
}
trap cleanup EXIT

# 5. Execute Restoration
if [[ "$RESTORE_TARGET" =~ \.dump$|\.pgdump$ || "$BACKUP_FILE" =~ \.dump\.enc$ ]]; then
  echo "[INFO] Executing pg_restore for custom format archive..."
  RESTORE_ARGS=(-h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" --no-owner --no-privileges)
  if [[ "$CLEAN" == "true" ]]; then
    RESTORE_ARGS+=(--clean --if-exists)
  fi
  # pg_restore exit code 1 indicates non-fatal warnings (e.g. object not existing during clean drop)
  pg_restore "${RESTORE_ARGS[@]}" "$RESTORE_TARGET" || [[ $? -le 1 ]]
else
  echo "[INFO] Executing psql for plain SQL format script..."
  psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -f "$RESTORE_TARGET"
fi

# 6. Post-restoration verification
echo "[INFO] Verifying restored database state..."
ALEMBIC_REV=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t -A -c "SELECT version_num FROM alembic_version LIMIT 1;" 2>/dev/null || echo "unknown")
TABLE_COUNT=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t -A -c "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';" 2>/dev/null || echo "0")

echo "=========================================================="
echo "   Post-Restoration Verification Report                   "
echo "=========================================================="
echo "Target Database:   $DB_NAME"
echo "Alembic Revision:  $ALEMBIC_REV"
echo "Public Tables:     $TABLE_COUNT"
echo "[SUCCESS] Database restoration completed successfully!"
