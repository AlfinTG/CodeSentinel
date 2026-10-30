# CodeSentinel Staging Rehearsal & Production Cutover Runbook (Phases 9 & 10)

This runbook guides DevSecOps and SRE teams through executing the **Phase 9 Staging Rehearsal** and completing the **Phase 10 Production Cutover**.

---

## 1. Environment Topology & Prerequisites

### Architecture
```
[ GitHub Webhooks / Users ]
             │ (HTTPS / TLS 1.3)
             ▼
      [ Nginx Reverse Proxy ]
             │
   ┌─────────┴─────────────────────┐
   ▼                               ▼
[ Frontend (Nginx/SPA) ]    [ FastAPI Backend (Port 8000) ]
(:5173 / :80)                      │
                     ┌─────────────┼─────────────┐
                     ▼             ▼             ▼
             [ PostgreSQL 16 ]  [ Redis 7 ]   [ Prometheus ]
             (WAL + Volumes)   (Queue DB)     (:9090 / AlertManager)
                     ▲             ▲
                     └──────┬──────┘
                            ▼
                 [ Background Worker ]
               (Transactional Consumer)
```

### Server Prerequisites
- Linux OS (Ubuntu 22.04 LTS / Debian 12 recommended) with kernel cgroups v2 enabled.
- Docker Engine $\ge 26.0$ and Docker Compose $\ge 2.27$.
- Inbound ports `80` and `443` open for web traffic.

---

## 2. Phase 9: Staging Deployment & Execution

### Step 1: Clone & Configure Staging Secrets
```bash
git clone https://github.com/AlfinTG/CodeSentinel.git
cd CodeSentinel

# Copy staging environment template
cp .env.production.example .env.staging
```

Populate the required environment variables:
```bash
CODESENTINEL_ENV=staging
POSTGRES_PASSWORD=your_strong_staging_db_password
DATABASE_URL=postgresql+psycopg://codesentinel:your_strong_staging_db_password@postgres:5432/codesentinel_staging
SESSION_SECRET_KEY=at_least_32_characters_random_session_secret
GITHUB_WEBHOOK_SECRET=your_github_webhook_secret
AI_PROVIDER=nvidia
NVIDIA_API_KEY=your_nvidia_api_key
```

### Step 2: Build & Start Stack
```bash
docker compose --env-file .env.staging up --build -d
```

### Step 3: Run Database Migrations
```bash
docker compose exec api alembic upgrade head
```

### Step 4: Verify Health Probes
```bash
curl -f http://localhost:8000/api/v1/health/live
curl -f http://localhost:8000/api/v1/health/ready
```

---

## 3. Phase 9 Validation Drills

### 1. High-Throughput Webhook Load Benchmark ($\ge 100\text{ req/s}$)
Execute the automated load benchmark against the staging API:
```bash
python tests/load/load_test_benchmark.py \
  --base-url http://localhost:8000 \
  --secret "$GITHUB_WEBHOOK_SECRET" \
  --requests 500 \
  --concurrency 25 \
  --verify-targets
```
**Acceptance Criteria:**
- Throughput $\ge 100\text{ req/sec}$.
- $p99$ response latency $< 50\text{ ms}$.
- Zero connection errors or dropped requests.

### 2. Continuous 48-Hour Soak Test
Run the continuous stability harness:
```bash
python scripts/soak_test_harness.py \
  --base-url http://localhost:8000 \
  --duration-seconds 172800 \
  --interval 5.0
```
**Acceptance Criteria:**
- Zero process crashes or unexpected restarts.
- Memory slope stable (no memory leaks).
- Health readiness probe success rate $> 99.9\%$.

### 3. Adversarial Security Fuzzing
```bash
pytest backend/tests/test_phase9_fuzzing.py -v
```
**Acceptance Criteria:**
- 9 of 9 adversarial tests passing (tampered HMACs, malformed hunks, prompt injections, directory traversals).

---

## 4. Phase 10: Production Cutover Checklist

Before signing off on production traffic:

- [ ] **DNS & TLS:** Domain pointed to reverse proxy with valid Let's Encrypt SSL/TLS certificates.
- [ ] **GitHub App Live:** Webhook URL set to `https://codesentinel.yourdomain.com/api/v1/webhooks/github`.
- [ ] **Automated Backups:** Verify `codesentinel-backup` container is running and taking dumps in `/backups`.
- [ ] **Disaster Recovery Rehearsal:** Run test restore into an isolated container using `./scripts/restore_db.sh`.
- [ ] **Prometheus Alerting:** Verify `/metrics` is actively scraped and AlertManager rules are loaded.
- [ ] **Gate Sign-Off:** Confirm all 6 Mandatory Release Invariant Gates pass.

