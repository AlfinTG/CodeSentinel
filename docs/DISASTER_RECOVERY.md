# CodeSentinel Disaster Recovery & Persistence Runbook (Phase 8)

**Document Version:** 2.0  
**Phase:** Phase 8 — Automated Backup, Encryption, Off-Site Storage, Alerting, PITR, and Rollback Automation  
**Classification:** Operational Security & Site Reliability Engineering (SRE)  
**Target RTO:** $\le 30\text{ minutes}$ (Recovery Time Objective)  
**Target RPO:** $\le 5\text{ minutes}$ (Recovery Point Objective)  

---

## 1. Overview & Operational Architecture

CodeSentinel's persistence tier stores factual execution truths, GitHub webhook deliveries, isolated check results, candidate patch evaluations, and AI diagnosis records. To guarantee continuous data availability and survive storage failures or human error, CodeSentinel implements a defense-in-depth persistence recovery strategy:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                             PostgreSQL 16 Primary                               │
│         (Durable WAL Streams + Transactions + Alembic Schema Revisions)         │
└────────────────────────┬────────────────────────────────┬───────────────────────┘
                         │                                │
            Continuous WAL Archiving         Scheduled Logical Snapshots
               (RPO <= 5 minutes)           (pg_dump Custom Format -Z 6)
                         │                                │
                         ▼                                ▼
            [ WAL Archiving Manager ]        [ AES-256-GCM Encryption ]
             (Atomic .tmp -> final)           (PBKDF2 / 256-bit Key / IV)
                         │                                │
                         ▼                                ▼
            [ Point-in-Time Recovery ]       [ S3 Off-Site Storage Adapter ]
             (recovery.signal + Auto Conf)   (SigV4 REST / WORM Object Lock)
                         │                                │
                         └────────────────┬───────────────┘
                                          │
                                          ▼
                         [ SRE Monitoring & Alerting Engine ]
                          - Slack / Discord / JSON Webhooks
                          - Dead-Man's-Snitch Heartbeat Pings
                          - Automatic Credential Redaction
```

---

## 2. Recovery Objectives

| Metric | Target SLA | Strategy & Implementation |
|---|---|---|
| **Recovery Point Objective (RPO)** | $\le 5\text{ minutes}$ | Continuous Write-Ahead Log (WAL) archiving via PostgreSQL `archive_command` with atomic writes. Maximum data loss is bounded by the WAL flush interval. |
| **Recovery Time Objective (RTO)** | $\le 30\text{ minutes}$ | Automated restoration scripts (`restore_db.ps1` / `restore_db.sh`) executing authenticated decryption, checksum verification, parallel `pg_restore`, and automated recovery signaling. |

---

## 3. Automated Logical Backup Procedures

Logical snapshots provide complete, portable database dumps that capture all database objects and data at a specific point in time.

### 3.1. Running Backups

#### Windows / PowerShell:
```powershell
# Default compressed custom archive backup with SHA-256 sidecar
.\scripts\backup_db.ps1 -DbName "codesentinel_dev" -RetentionDays 7

# Encrypted backup with AES-256-GCM and webhook alerting
.\scripts\backup_db.ps1 -DbName "codesentinel_dev" -Encrypt -EncryptionKey $env:BACKUP_ENCRYPTION_KEY -WebhookUrl $env:BACKUP_ALERT_WEBHOOK_URL
```

#### Linux / Docker / CI:
```bash
# Default custom archive backup
./scripts/backup_db.sh -d codesentinel_dev -r 7

# Encrypted backup with Slack alerting and dead-man's-snitch ping
./scripts/backup_db.sh -d codesentinel_dev -e -k "$BACKUP_ENCRYPTION_KEY" -w "$BACKUP_ALERT_WEBHOOK_URL" -s "$BACKUP_SNITCH_URL"
```

### 3.2. Backup Artifacts & Sidecar Metadata
Every backup execution produces two paired artifacts:
1. **The Binary Dump:** `${DB_NAME}_backup_${TIMESTAMP}.dump` (or `${DB_NAME}_backup_${TIMESTAMP}.dump.enc` if encrypted).
2. **The JSON Metadata Sidecar:** `${BACKUP_FILE}.meta.json` containing:
   - `created_at`: UTC ISO-8601 timestamp
   - `database`: Origin database name (used to prevent unintended restores)
   - `host`, `port`, `user`: Source topology attributes
   - `format`: Archive format (`custom` or `plain`)
   - `alembic_version`: Exact database schema revision at dump time
   - `file_size_bytes`: Byte length of the artifact
   - `sha256`: Cryptographic SHA-256 digest
   - `encrypted`: Boolean flag indicating AES-256-GCM encryption status
   - `status`: Verification status (`completed`)

---

## 4. Backup Encryption (AES-256-GCM)

All backups stored at rest or transmitted across networks should be encrypted with authenticated cryptography.

### 4.1. Cryptographic Design
- **Cipher:** AES-256-GCM (Galois/Counter Mode).
- **Authentication:** 128-bit authentication tag protecting both ciphertext and authenticated data.
- **Nonce/IV:** Random 96-bit (12-byte) initialization vector per operation. Nonces are never reused.
- **Key Derivation:** PBKDF2-HMAC-SHA256 with 600,000 iterations and random 128-bit salt when using passphrases.
- **Wire Format:** `[MAGIC: 4 bytes "CSB1"][SALT: 16 bytes][NONCE: 12 bytes][CIPHERTEXT + TAG: N bytes]`.

### 4.2. Key Management & Rotation Protocol
1. **Key Generation:**
   ```bash
   python -m app.services.backup.crypto generate-key
   ```
2. **Key Storage:** Store the key in AWS Secrets Manager, GCP Secret Manager, or HashiCorp Vault. **Never** commit encryption keys to Git or store them adjacent to backup files.
3. **Key Rotation Procedure:**
   - Generate a new key $K_2$.
   - For each active backup archive, decrypt using $K_1$ and re-encrypt using $K_2$:
     ```bash
     python -m app.services.backup.crypto decrypt backup.dump.enc backup.dump.tmp "$KEY_OLD"
     python -m app.services.backup.crypto encrypt backup.dump.tmp backup.dump.new.enc "$KEY_NEW"
     rm -f backup.dump.tmp
     ```
   - Update metadata checksum and replace original archive.

---

## 5. Off-Site S3 Storage & WORM Immutability

CodeSentinel provides a lightweight S3-compatible remote storage client (`app.services.backup.storage.S3StorageClient`) with built-in AWS Signature Version 4 (SigV4) authentication over pure HTTPS, avoiding heavy SDK dependencies.

### 5.1. Supported Providers
- Amazon Web Services (AWS S3)
- Cloudflare R2
- MinIO (Private Cloud / On-Premise)
- Wasabi Hot Cloud Storage
- Google Cloud Storage (interoperability mode)

### 5.2. Configuration Environment Variables
| Variable | Description | Example |
|---|---|---|
| `BACKUP_S3_BUCKET` | Destination S3 bucket name | `codesentinel-backups` |
| `BACKUP_S3_REGION` | AWS / Provider region | `us-east-1` |
| `BACKUP_S3_ENDPOINT` | Optional custom endpoint URL | `https://s3.wasabisys.com` |
| `BACKUP_S3_ACCESS_KEY` | IAM Access Key ID | `AKIAIOSFODNN7EXAMPLE` |
| `BACKUP_S3_SECRET_KEY` | IAM Secret Access Key | `wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY` |
| `BACKUP_S3_PREFIX` | Key prefix in bucket | `production/db/` |

### 5.3. Immutable Storage (WORM / Object Lock)
To defend against ransomware or malicious deletion:
- Enable S3 Object Lock on the destination bucket.
- Set Retention Mode to `COMPLIANCE` with a 30-day retention period.
- No IAM principal (including root) can overwrite or delete the backup file or sidecar before expiration.

---

## 6. SRE Monitoring & Alerting Pipeline

The backup subsystem integrates `app.services.backup.alerting` to guarantee complete observability into job execution.

### 6.1. Webhook Notifications
- **Slack:** Rich color-coded attachments (`#2eb886` for success, `#a30200` for failure) detailing database, host, size, and SHA-256 fingerprint.
- **Discord:** Structured embeds with field tables.
- **Generic JSON:** Standardized schema for custom SIEM / webhook consumers.

### 6.2. Dead-Man's-Snitch Integration
Scheduled backups can fail silently if the runner crashes or cron fails to trigger. CodeSentinel integrates dead-man's-snitch monitoring (Healthchecks.io / Cronitor):
- **Job Start:** Pings `${SNITCH_URL}/start`.
- **Job Success:** Pings `${SNITCH_URL}` (HTTP 200).
- **Job Failure:** Pings `${SNITCH_URL}/fail` with error diagnostic message.
- If no ping is received within the expected window, the monitoring service triggers on-call alerts.

### 6.3. Secret Masking Guarantee
The alerting service executes `sanitize_alert_payload()` before any network dispatch. All database passwords, tokens, API keys, and connection credentials matching sensitive patterns are stripped and replaced with `[REDACTED]`.

---

## 7. Point-In-Time Recovery (PITR) Setup & Replay

To achieve an RPO $\le 5\text{ minutes}$, configure PostgreSQL continuous WAL archiving:

### 7.1. PostgreSQL Primary Configuration (`postgresql.conf`)
```ini
# Enable continuous WAL archiving
wal_level = replica
archive_mode = on
archive_command = 'python -c "from app.services.backup.pitr import archive_wal_segment; import sys; res = archive_wal_segment(sys.argv[1], sys.argv[2]); sys.exit(0 if res.success else 1)" "%p" "/var/lib/postgresql/wal_archive"'
archive_timeout = 300 # Forces segment rotation every 5 minutes
```

### 7.2. Safe Atomic Archiving Guarantee
CodeSentinel's `archive_wal_segment()` writes incoming WAL segments to a process-unique temporary file (`%f.tmp.<pid>`) before atomically renaming to `%f`. This ensures PostgreSQL never reads a partial WAL segment during crash recovery.

### 7.3. Step-by-Step PITR Replay Procedure
When recovering from accidental table drops or transaction corruption at timestamp `2026-10-30 02:15:00 UTC`:

1. **Stop PostgreSQL Server:**
   ```bash
   systemctl stop postgresql
   ```
2. **Prepare Data Directory:**
   Move corrupt `PGDATA` to `PGDATA.corrupt` and restore the base snapshot:
   ```bash
   ./scripts/restore_db.sh -i backups/codesentinel_base.dump -c -f
   ```
3. **Generate Recovery Configuration:**
   Execute PITR configuration generator:
   ```python
   from app.services.backup.pitr import generate_recovery_configuration
   generate_recovery_configuration(
       pgdata_dir="/var/lib/postgresql/data",
       target_time_utc="2026-10-30 02:15:00 UTC",
       wal_archive_dir="/var/lib/postgresql/wal_archive",
       recovery_action="promote"
   )
   ```
   This generates:
   - `recovery.signal` in `PGDATA`.
   - Recovery parameters in `postgresql.auto.conf`:
     ```ini
     restore_command = 'cp "/var/lib/postgresql/wal_archive/%f" "%p"'
     recovery_target_time = '2026-10-30 02:15:00 UTC'
     recovery_target_action = 'promote'
     recovery_target_inclusive = true
     ```
4. **Start PostgreSQL:**
   ```bash
   systemctl start postgresql
   ```
5. **Verify Recovery:**
   PostgreSQL reads `recovery.signal`, replays WAL segments from `/var/lib/postgresql/wal_archive` until `2026-10-30 02:15:00 UTC`, removes `recovery.signal`, and promotes to primary. Check server logs:
   ```
   LOG: starting point-in-time recovery to 2026-10-30 02:15:00+00
   LOG: redo starts at ...
   LOG: recovery stopping at restore point: target time 2026-10-30 02:15:00+00 reached
   LOG: archive recovery complete
   LOG: database system is ready to accept connections
   ```

---

## 8. Zero-Downtime Migration Rollback Procedures

CodeSentinel maintains bidirectional Alembic migrations. Every schema migration revision must have a corresponding, non-destructive `downgrade()` implementation.

### 8.1. Emergency Rollback Command Reference

```bash
# Check current migration revision
python -m alembic current

# Downgrade to previous revision
python -m alembic downgrade -1

# Rollback to specific stable milestone
python -m alembic downgrade 0001_initial_schema

# Re-apply all migrations to head
python -m alembic upgrade head
```

### 8.2. Rollback Verification Checklist
- Run `backend/tests/test_migration_rollback.py` in CI to guarantee that any new migration script is reversible.
- Never write destructive `DROP` operations on production tables without a two-step deprecation phase (first stop writing/reading the column in application code, deploy, then drop column in a later release).

---

## 9. Automated Scheduling

### 9.1. Linux / Kubernetes CronJob
```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: codesentinel-backup
spec:
  schedule: "0 2 * * *" # Daily at 02:00 UTC
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: postgres-backup
            image: codesentinel-backend:latest
            command: ["/bin/bash", "/scripts/backup_db.sh", "-e", "-r", "14"]
            env:
            - name: POSTGRES_DB
              value: "codesentinel_production"
            - name: BACKUP_ENCRYPTION_KEY
              valueFrom:
                secretKeyRef:
                  name: backup-secrets
                  key: encryption-key
            - name: BACKUP_ALERT_WEBHOOK_URL
              valueFrom:
                secretKeyRef:
                  name: alert-secrets
                  key: slack-webhook
```

### 9.2. Windows Task Scheduler
```powershell
$action = New-ScheduledTaskAction -Execute "PowerShell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File C:\CodeSentinel\scripts\backup_db.ps1 -Encrypt -RetentionDays 14"
$trigger = New-ScheduledTaskTrigger -Daily -At "02:00"
Register-ScheduledTask -TaskName "CodeSentinel_Daily_Backup" -Action $action -Trigger $trigger -Description "Nightly CodeSentinel encrypted backup, checksumming, and retention pruning."
```


---

## 10. Production Staging Validation Procedures

Before declaring any disaster recovery component production-ready, the following validation procedures **must** be executed in an isolated staging environment. Do not substitute mocked or simulated results for live infrastructure validation.

### 10.1. WAL Archiving & Point-in-Time Recovery (PITR) Validation

**Prerequisites:**
- Isolated PostgreSQL 16 staging cluster (not production)
- Sufficient disk for WAL archives (estimate: 16 MB × WAL segments per minute × retention window)

**Procedure:**
```bash
# 1. Configure PostgreSQL for WAL archiving
psql -U postgres -c "ALTER SYSTEM SET wal_level = 'replica';"
psql -U postgres -c "ALTER SYSTEM SET archive_mode = 'on';"
psql -U postgres -c "ALTER SYSTEM SET archive_command = 'python3 -c \"import sys; sys.path.insert(0, \\\"/app/backend\\\"); from app.services.backup.pitr import archive_wal_segment; archive_wal_segment(sys.argv[1], \\\"/var/lib/postgresql/wal_archive\\\")\" %p';"
pg_ctl restart -D /var/lib/postgresql/data

# 2. Create a base backup
pg_basebackup -U postgres -D /backup/base_snapshot -Ft -z -P

# 3. Insert identifiable test data with timestamps
psql -U postgres -d codesentinel_staging -c "INSERT INTO audit_events (id, event_type, created_at) VALUES ('pitr-test-1', 'BEFORE_DISASTER', NOW());"
# Record this timestamp as the recovery target
RECOVERY_TARGET=$(date -u +"%Y-%m-%d %H:%M:%S")

# 4. Simulate data loss AFTER the target timestamp
psql -U postgres -d codesentinel_staging -c "DELETE FROM audit_events WHERE event_type = 'BEFORE_DISASTER';"
psql -U postgres -c "SELECT pg_switch_wal();"  # Force WAL archive flush

# 5. Stop PostgreSQL and restore from base backup
pg_ctl stop -D /var/lib/postgresql/data
rm -rf /var/lib/postgresql/data/*
tar -xzf /backup/base_snapshot/base.tar.gz -C /var/lib/postgresql/data/

# 6. Generate recovery configuration
python3 -c "
from app.services.backup.pitr import generate_recovery_configuration
generate_recovery_configuration('/var/lib/postgresql/data', '$RECOVERY_TARGET', '/var/lib/postgresql/wal_archive')
"

# 7. Start PostgreSQL in recovery mode and verify
pg_ctl start -D /var/lib/postgresql/data
psql -U postgres -d codesentinel_staging -c "SELECT * FROM audit_events WHERE event_type = 'BEFORE_DISASTER';"
# Expected: row exists (recovered to before deletion)
```

**Validation criteria:** The `BEFORE_DISASTER` row must be present after recovery. Mark **PASS** only if verified. If PostgreSQL configuration is unavailable, mark **BLOCKED**.

### 10.2. S3-Compatible Off-Site Backup Validation

**Prerequisites:**
- S3-compatible bucket with least-privilege IAM credentials (e.g., sandbox AWS S3, MinIO, or Cloudflare R2)
- Environment variables: `BACKUP_S3_BUCKET`, `BACKUP_S3_REGION`, `BACKUP_S3_ACCESS_KEY`, `BACKUP_S3_SECRET_KEY`, optionally `BACKUP_S3_ENDPOINT`

**Procedure:**
```bash
# 1. Create a test backup
./scripts/backup_db.sh -d codesentinel_staging -e -k "$BACKUP_ENCRYPTION_KEY"

# 2. Upload to S3
python3 -c "
from app.services.backup.storage import S3BackupStorage
s3 = S3BackupStorage(
    bucket='$BACKUP_S3_BUCKET',
    access_key_id='$BACKUP_S3_ACCESS_KEY',
    secret_access_key='$BACKUP_S3_SECRET_KEY',
    region='$BACKUP_S3_REGION',
    endpoint_url='$BACKUP_S3_ENDPOINT',  # omit for AWS
)
result = s3.upload_file('backups/latest.dump.enc', 'staging/latest.dump.enc')
print(f'Upload: success={result.success}, etag={result.etag}, sha256={result.sha256}')
assert result.success, f'Upload failed: {result.error_message}'
"

# 3. Download and verify checksum
python3 -c "
from app.services.backup.storage import S3BackupStorage
from app.services.backup.pitr import compute_sha256
s3 = S3BackupStorage(...)  # same config as above
ok = s3.download_file('staging/latest.dump.enc', '/tmp/downloaded.dump.enc')
assert ok, 'Download failed'
local_hash = compute_sha256('backups/latest.dump.enc')
remote_hash = compute_sha256('/tmp/downloaded.dump.enc')
assert local_hash == remote_hash, f'Checksum mismatch: {local_hash} != {remote_hash}'
print(f'Checksum verified: {local_hash}')
"

# 4. Restore from downloaded backup
./scripts/restore_db.sh -i /tmp/downloaded.dump.enc -d codesentinel_staging_restored -k "$BACKUP_ENCRYPTION_KEY" -f
```

**Validation criteria:** Upload succeeds with 200/201, download checksum matches original, and restored database contains expected tables and Alembic revision. Mark **PASS** only if all three verified. If S3 credentials are unavailable, mark **BLOCKED**.

### 10.3. Webhook & Heartbeat Monitoring Validation

**Prerequisites:**
- Real Slack or Discord webhook URL (create a dedicated `#backup-alerts-staging` channel)
- Healthchecks.io or Cronitor free-tier monitor URL

**Procedure:**
```bash
# 1. Test Slack webhook delivery
python3 -c "
from app.services.backup.alerting import send_backup_alert
ok = send_backup_alert(
    webhook_url='$BACKUP_ALERT_WEBHOOK_URL',
    status='SUCCESS',
    database='codesentinel_staging',
    host='staging-db-01',
    backup_file='test_validation.dump.enc',
    file_size_bytes=1048576,
    sha256='abc123...',
)
print(f'Webhook delivery: {ok}')
assert ok, 'Webhook delivery failed'
"
# Verify message appeared in Slack/Discord channel

# 2. Test failure notification
python3 -c "
from app.services.backup.alerting import send_backup_alert
ok = send_backup_alert(
    webhook_url='$BACKUP_ALERT_WEBHOOK_URL',
    status='FAILURE',
    database='codesentinel_staging',
    error='pg_dump: connection refused (simulated test failure)',
)
print(f'Failure alert: {ok}')
"

# 3. Test heartbeat/snitch ping
python3 -c "
from app.services.backup.alerting import ping_snitch
for event in ('start', 'success', 'fail'):
    ok = ping_snitch('$BACKUP_SNITCH_URL', event=event)
    print(f'Snitch {event}: {ok}')
"
# Verify pings received on Healthchecks.io dashboard
```

**Validation criteria:** Messages appear in the target Slack/Discord channel with correct formatting and colors. Snitch pings appear on the monitoring dashboard. Credentials are never exposed in messages. Mark **PASS** only if visually confirmed. If webhook URLs are unavailable, mark **BLOCKED**.

### 10.4. Current Validation Status

| Check | Unit/Mock Tests | Live Infrastructure / Rehearsal | Status |
|-------|-----------------|---------------------------------|--------|
| WAL Archiving & PITR | ✅ PASS (5/5) | ✅ PASS (Isolated physical PostgreSQL 17 cluster drill) | **PASS** |
| Cloudflare R2 / S3 Off-Site Storage | ✅ PASS (6/6) | 🔒 DEFERRED / NOT VALIDATED (No subscription or credentials configured; live tests safely disabled by default) | **DEFERRED (Live) / PASS (Mocked Unit)** |
| Webhook & Heartbeat | ✅ PASS (7/7) | 🔒 DEFERRED (Wire verified on loopback HTTP; external URL pending) | **DEFERRED (Live URL) / PASS (Wire)** |
| Encrypted Backup/Restore | ✅ PASS (6/6) | ✅ PASS (Live PostgreSQL 17 `codesentinel_dev` -> `test`) | **PASS** |
| Backup Retention | ✅ PASS (11/11) | ✅ PASS (Filesystem cutoff verified) | **PASS** |
| Migration Rollback | ✅ PASS (6/6) | ✅ PASS (SQLite + PostgreSQL schema rollback) | **PASS** |

> [!NOTE]
> **Cloudflare R2 Integration Testing Status:** Real Cloudflare R2 integration is **NOT** validated. Live R2 network operations are deferred because no Cloudflare R2 subscription or credentials were configured. The S3-compatible storage adapter and AWS SigV4 implementation have been fully verified via mocked unit tests (`test_offsite_storage.py`: 6 passed), and all live integration tests (`test_r2_live.py`: 4 tests) remain disabled by default and safely skip when credentials are unavailable.
