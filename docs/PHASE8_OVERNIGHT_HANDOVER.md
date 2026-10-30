# CodeSentinel — Phase 8 Final Handover & Production Validation Runbook

**Date:** 2026-10-30  
**Branch:** `prakhar`  
**Status:** IMPLEMENTED, PARTIALLY VALIDATED  
**Scope:** Automated database backups, AES-256-GCM encryption, physical PostgreSQL Point-in-Time Recovery (PITR), Cloudflare R2 / S3-compatible off-site storage adapter, webhook alerting, and migration rollbacks.

---

## 1. Executive Summary & Verification Matrix

| Component | Logic & Unit Tests | Live Infrastructure / Rehearsal | Status | Blocker / Details |
|---|---|---|---|---|
| **Encrypted Backup & Restore** | ✅ PASS (6/6) | ✅ PASS (Live PostgreSQL 17) | **PASS** | `codesentinel_dev` backup encrypted, bit-flip tamper detected, wrong key rejected, restored cleanly into `codesentinel_test`. |
| **Point-in-Time Recovery (PITR)** | ✅ PASS (5/5) | ✅ PASS (Isolated physical PostgreSQL 17 cluster drill) | **PASS** | Base backup created via `pg_basebackup`, WAL stream archived, recovery to exact target timestamp verified (valid transaction restored, unwanted transaction excluded). |
| **Cloudflare R2 / S3 Adapter** | ✅ PASS (6/6) | 🔒 DEFERRED / NOT VALIDATED (No subscription/creds configured) | **DEFERRED (Live) / PASS (Mocked Unit)** | SigV4 signing, streaming upload, multipart assembly, abort cleanup, and object deletion fully verified with mock transport (6/6 passed). Real Cloudflare R2 integration is NOT validated; live R2 tests safely skip when credentials are unavailable (4 skipped in safe mode). |
| **Webhook Alerting & Snitch** | ✅ PASS (7/7) | 🔒 DEFERRED (External URL) / ✅ PASS (Wire) | **DEFERRED (External) / PASS (Wire)** | Credential sanitization, Slack/Discord formats, and snitch pings verified. Loopback HTTP wire test verified. External delivery deferred pending live webhook endpoint. |
| **Backup Retention Pruning** | ✅ PASS (11/11) | ✅ PASS (Filesystem) | **PASS** | Pruning strictly limited to `*_backup_*.dump*` files beyond retention window. |
| **Database Migration Rollback** | ✅ PASS (6/6) | ✅ PASS (Alembic) | **PASS** | Bidirectional schema upgrade/downgrade cycles verified on SQLite and PostgreSQL. |
| **Full Backend Regression Suite** | ✅ PASS (227 passed) | ✅ PASS | **PASS** | 227 passed, 12 skipped (4 R2 live tests disabled by default, 1 Nebius API key live test, 7 container isolation tests without local Docker), 0 failed. |

---

## 2. Cloudflare R2 Setup Guide & Live Testing

### 2.1. Obtaining Cloudflare R2 Credentials
1. Log in to the [Cloudflare Dashboard](https://dash.cloudflare.com/).
2. Navigate to **Workers & Pages > R2**.
3. Click **Create bucket** and name it (e.g., `codesentinel-backups-test`).
4. In the R2 Overview page, click **Manage R2 API Tokens > Create API Token**.
   - Permissions: **Object Read & Write** (or Admin Read & Write).
   - Scope: Apply to the dedicated test bucket.
5. Record:
   - **Access Key ID**
   - **Secret Access Key**
   - **Account ID** (found in the dashboard URL: `https://dash.cloudflare.com/<ACCOUNT_ID>/r2/overview`)

### 2.2. Configuration Environment Variables
Add to your local `.env` (which is excluded in `.gitignore`):
```bash
BACKUP_S3_BUCKET=codesentinel-backups-test
BACKUP_S3_ACCESS_KEY=<YOUR_R2_ACCESS_KEY_ID>
BACKUP_S3_SECRET_KEY=<YOUR_R2_SECRET_ACCESS_KEY>
BACKUP_S3_ENDPOINT_URL=https://<ACCOUNT_ID>.r2.cloudflarestorage.com
BACKUP_S3_REGION=auto
BACKUP_S3_TIMEOUT_SECONDS=30
RUN_R2_LIVE_TESTS=true
```

### 2.3. Executing the Live R2 Integration Test
```powershell
$env:PATH = "C:\Users\Pradyumn Joshi\Desktop\CODE SENTINEL\pg_portable\pgsql\bin;$env:PATH"
& ".\CodeSentinel\.venv\Scripts\python.exe" -m pytest CodeSentinel/backend/tests/integration/test_r2_live.py -v
```
**Safety Guarantees in `test_r2_live.py`:**
- Skips automatically unless `RUN_R2_LIVE_TESTS=true`.
- Confines all operations strictly to the `codesentinel-test/` key prefix.
- Automatically executes cleanup of created test objects in `finally` blocks.
- Tests single-part upload, download checksum validation, decryption, multipart upload, and deletion.

---

## 3. PostgreSQL Point-in-Time Recovery (PITR)

### 3.1. Rehearsal Execution & Results
A physical Point-in-Time Recovery drill was executed on 2026-10-30 using an isolated, disposable PostgreSQL 17 cluster:
- **Primary Data Directory:** `C:\Users\Public\cs_pitr_drill\primary`
- **WAL Archive Directory:** `C:\Users\Public\cs_pitr_drill\archive`
- **Port:** 5439 (completely isolated from port 5432 dev instance)
- **Step 1:** Initialized cluster with `archive_mode = on` and `archive_command`.
- **Step 2:** Inserted baseline record (`Event 1`).
- **Step 3:** Executed physical base backup with `pg_basebackup -Fp -Xs -c fast`.
- **Step 4:** Inserted pre-disaster transaction (`Event 2`) and captured target timestamp: `2026-10-30 10:51:05.606812 +05:30`.
- **Step 5:** Simulated disaster by inserting corrupting transaction (`Event 3`).
- **Step 6:** Forced WAL flush via `pg_switch_wal()` (5 archived WAL files verified).
- **Step 7:** Prepared recovery cluster from base backup, generated `recovery.signal` and `postgresql.auto.conf` (`recovery_target_time = '2026-10-30 10:51:05.606812 +05:30'`, `restore_command`).
- **Step 8:** Started recovery cluster. PostgreSQL replayed WAL segments up to target timestamp and paused before transaction 742 (`time 2026-10-30 10:51:07.993732+05:30`).
- **Verification:**
  - `Event 1` and `Event 2` present: **YES (2 rows)**
  - `Event 3` (corruption) excluded: **YES (0 rows)**
  - New timeline ID selected: `timeline 2`
  - Result: **SUCCESS**

---

## 4. Webhook Monitoring & Alerting

### 4.1. Configuration
Set the following environment variables to enable backup alerting:
```bash
BACKUP_ALERT_WEBHOOK_URL=https://hooks.slack.com/services/T00/B00/XXXXX
# Or Discord: https://discord.com/api/webhooks/XXXXX/YYYYY
BACKUP_SNITCH_URL=https://hc-ping.com/YOUR-UUID-HERE
```

### 4.2. Local Wire Verification
A real HTTP socket test verified:
- Structured JSON transmission over TCP.
- Automatic password and token redaction (e.g., `postgresql://user:[REDACTED]@...`).
- HTTP 500 error detection and non-raising failure handling.
- Dead-man's-snitch heartbeat ping delivery.

---

## 5. Verification Commands Reference

```powershell
# Set library path for PostgreSQL
$env:PATH = "C:\Users\Pradyumn Joshi\Desktop\CODE SENTINEL\pg_portable\pgsql\bin;$env:PATH"

# Run S3 Mocked Tests
& ".\CodeSentinel\.venv\Scripts\python.exe" -m pytest CodeSentinel/backend/tests/test_offsite_storage.py -v

# Run PITR Unit Tests
& ".\CodeSentinel\.venv\Scripts\python.exe" -m pytest CodeSentinel/backend/tests/test_pitr_recovery.py -v

# Run Alerting Tests
& ".\CodeSentinel\.venv\Scripts\python.exe" -m pytest CodeSentinel/backend/tests/test_backup_alerting.py -v

# Run Full Backend Suite
& ".\CodeSentinel\.venv\Scripts\python.exe" -m pytest CodeSentinel/backend -q

# Run Ruff Check on Phase 8 Python Code
& ".\CodeSentinel\.venv\Scripts\ruff.exe" check CodeSentinel/backend/app/services/backup CodeSentinel/backend/tests/integration/test_r2_live.py

# Git Diff Check
cd CodeSentinel; git diff --check
```

---

## 6. Repository Integrity & Safety
- **Branch:** `prakhar`
- **Status:** All uncommitted Phase 8 files preserved. No commits, staging, branch switches, rebases, or pushes were performed.

