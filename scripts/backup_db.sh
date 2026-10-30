#!/usr/bin/env bash
# ==============================================================================
# CodeSentinel Automated PostgreSQL Logical Backup Script (Phase 8)
#
# Generates compressed logical database backups via pg_dump, generates SHA-256
# checksums, records metadata sidecars (.meta.json), and prunes expired snapshots.
# ==============================================================================

set -euo pipefail

DB_NAME="${POSTGRES_DB:-codesentinel_dev}"
DB_HOST="${POSTGRES_HOST:-localhost}"
DB_PORT="${POSTGRES_PORT:-5432}"
DB_USER="${POSTGRES_USER:-codesentinel}"
BACKUP_DIR="${BACKUP_DIR:-backups}"
FORMAT="${FORMAT:-custom}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
ENCRYPT=false
ENCRYPTION_KEY="${BACKUP_ENCRYPTION_KEY:-}"
WEBHOOK_URL="${BACKUP_ALERT_WEBHOOK_URL:-}"
SNITCH_URL="${BACKUP_SNITCH_URL:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -d|--dbname) DB_NAME="$2"; shift 2 ;;
    -h|--host) DB_HOST="$2"; shift 2 ;;
    -p|--port) DB_PORT="$2"; shift 2 ;;
    -U|--user) DB_USER="$2"; shift 2 ;;
    -o|--outdir) BACKUP_DIR="$2"; shift 2 ;;
    -f|--format) FORMAT="$2"; shift 2 ;;
    -r|--retention) RETENTION_DAYS="$2"; shift 2 ;;
    -e|--encrypt) ENCRYPT=true; shift 1 ;;
    -k|--key) ENCRYPTION_KEY="$2"; shift 2 ;;
    -w|--webhook-url) WEBHOOK_URL="$2"; shift 2 ;;
    -s|--snitch-url) SNITCH_URL="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# Validate database identifier safety
if [[ ! "$DB_NAME" =~ ^[A-Za-z_][A-Za-z0-9_]{0,62}$ ]]; then
  echo "[FATAL SAFETY ERROR] Invalid database name '$DB_NAME'. Names must start with a letter or underscore, contain only letters, digits, and underscores, and be at most 63 characters."
  exit 1
fi
if [[ ! "$DB_USER" =~ ^[A-Za-z_][A-Za-z0-9_]{0,62}$ ]]; then
  echo "[FATAL SAFETY ERROR] Invalid PostgreSQL user '$DB_USER'. Names must start with a letter or underscore and contain only letters, digits, and underscores."
  exit 1
fi

echo "=========================================================="
echo "   CodeSentinel Automated Database Backup (Phase 8)       "
echo "=========================================================="
echo "Database:       $DB_NAME"
echo "Host:Port:      ${DB_HOST}:${DB_PORT}"
echo "User:           $DB_USER"
echo "Backup Format:  $FORMAT"
echo "Retention:      $RETENTION_DAYS days"
echo "Output Dir:     $BACKUP_DIR"
echo ""

# 1. Verify required executables
command -v pg_dump >/dev/null 2>&1 || { echo "[ERROR] pg_dump is required but not installed."; exit 1; }
command -v psql >/dev/null 2>&1 || { echo "[ERROR] psql is required but not installed."; exit 1; }

# 2. Ensure Backup Directory exists
mkdir -p "$BACKUP_DIR"

# 3. Retrieve current Alembic revision
ALEMBIC_REV="unknown"
if REV_OUT=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t -A -c "SELECT version_num FROM alembic_version LIMIT 1;" 2>/dev/null); then
  if [[ -n "$REV_OUT" ]]; then
    ALEMBIC_REV=$(echo "$REV_OUT" | tr -d '[:space:]')
  fi
fi
echo "[INFO] Current Alembic revision: $ALEMBIC_REV"

# 4. Generate timestamped filename
TIMESTAMP=$(date -u +"%Y%m%d_%H%M%S")
if [[ "$FORMAT" == "custom" ]]; then
  EXT="dump"
  DUMP_FLAGS=(-F c -Z 6)
else
  EXT="sql"
  DUMP_FLAGS=(-F p)
fi

BACKUP_FILE="${BACKUP_DIR}/${DB_NAME}_backup_${TIMESTAMP}.${EXT}"
META_FILE="${BACKUP_FILE}.meta.json"

# 5. Run pg_dump
echo "[INFO] Executing pg_dump into: $BACKUP_FILE..."
pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" "${DUMP_FLAGS[@]}" -f "$BACKUP_FILE"

# 6. Compute SHA-256 Checksum
if command -v sha256sum >/dev/null 2>&1; then
  CHECKSUM=$(sha256sum "$BACKUP_FILE" | awk '{print $1}')
elif command -v shasum >/dev/null 2>&1; then
  CHECKSUM=$(shasum -a 256 "$BACKUP_FILE" | awk '{print $1}')
else
  CHECKSUM="unsupported"
fi

FILE_SIZE=$(wc -c < "$BACKUP_FILE" | tr -d '[:space:]')
CREATED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

echo "[OK] Backup created successfully!"
echo "     Size:     $FILE_SIZE bytes"
echo "     SHA-256:  $CHECKSUM"

# 7. Optional Encryption (AES-256-GCM)
IS_ENCRYPTED=false
FINAL_BACKUP_FILE="$BACKUP_FILE"

if [[ "$ENCRYPT" == "true" ]]; then
  if [[ -z "$ENCRYPTION_KEY" ]]; then
    echo "[ERROR] Encryption requested (-e/--encrypt) but no key provided (-k or BACKUP_ENCRYPTION_KEY)."
    exit 1
  fi
  echo "[INFO] Encrypting backup with AES-256-GCM..."
  ENC_FILE="${BACKUP_FILE}.enc"
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  BACKEND_DIR="$(cd "$SCRIPT_DIR/../backend" && pwd)"
  PYTHON_BIN=$(command -v python3 || command -v python)
  $PYTHON_BIN -c "import sys; sys.path.insert(0, '$BACKEND_DIR'); from app.services.backup.crypto import encrypt_file; encrypt_file('$BACKUP_FILE', '$ENC_FILE', '$ENCRYPTION_KEY')"
  rm -f "$BACKUP_FILE"
  FINAL_BACKUP_FILE="$ENC_FILE"
  IS_ENCRYPTED=true
  FILE_SIZE=$(wc -c < "$FINAL_BACKUP_FILE" | tr -d '[:space:]')
  if command -v sha256sum >/dev/null 2>&1; then
    CHECKSUM=$(sha256sum "$FINAL_BACKUP_FILE" | awk '{print $1}')
  elif command -v shasum >/dev/null 2>&1; then
    CHECKSUM=$(shasum -a 256 "$FINAL_BACKUP_FILE" | awk '{print $1}')
  fi
  echo "[OK] Backup encrypted successfully: $(basename "$FINAL_BACKUP_FILE")"
fi

META_FILE="${FINAL_BACKUP_FILE}.meta.json"

# 8. Write metadata sidecar JSON
cat <<EOF > "$META_FILE"
{
  "version": "1.0",
  "created_at": "$CREATED_AT",
  "database": "$DB_NAME",
  "host": "$DB_HOST",
  "port": $DB_PORT,
  "user": "$DB_USER",
  "format": "$FORMAT",
  "alembic_version": "$ALEMBIC_REV",
  "backup_file": "$(basename "$FINAL_BACKUP_FILE")",
  "file_size_bytes": $FILE_SIZE,
  "sha256": "$CHECKSUM",
  "encrypted": $IS_ENCRYPTED,
  "status": "completed"
}
EOF
echo "[OK] Metadata saved to: $META_FILE"

# 9. Enforce Retention Policy
if [[ "$RETENTION_DAYS" -gt 0 ]]; then
  echo "[INFO] Checking retention policy (older than $RETENTION_DAYS days)..."
  find "$BACKUP_DIR" -type f \( -name "*_backup_*.dump*" -o -name "*_backup_*.sql*" \) -mtime +"$RETENTION_DAYS" -delete || true
fi

# 10. Notifications and Alerts
if [[ -n "$WEBHOOK_URL" || -n "$SNITCH_URL" ]]; then
  echo "[INFO] Dispatching backup completion alerts..."
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  BACKEND_DIR="$(cd "$SCRIPT_DIR/../backend" && pwd)"
  PYTHON_BIN=$(command -v python3 || command -v python)
  $PYTHON_BIN -c "
import sys
sys.path.insert(0, '$BACKEND_DIR')
from app.services.backup.alerting import send_backup_alert, ping_snitch
payload = {
    'status': 'SUCCESS',
    'database': '$DB_NAME',
    'host': '$DB_HOST',
    'backup_file': '$(basename "$FINAL_BACKUP_FILE")',
    'file_size_bytes': $FILE_SIZE,
    'sha256': '$CHECKSUM',
    'encrypted': $IS_ENCRYPTED
}
if '$WEBHOOK_URL':
    send_backup_alert('$WEBHOOK_URL', payload)
if '$SNITCH_URL':
    ping_snitch('$SNITCH_URL', 'success')
" || echo "[WARN] Alert dispatch encountered an error."
fi

echo ""
echo "[SUCCESS] Backup pipeline finished cleanly."

