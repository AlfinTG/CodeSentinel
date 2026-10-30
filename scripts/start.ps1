# Phase 7 Deployment Startup Script (Windows PowerShell)
# ─────────────────────────────────────────────────────────
# Prerequisites: Docker Desktop running, environment configuration populated.
# Run from the CodeSentinel project root.

param(
    [switch]$DevMode   # Use dev DB/Redis only (no API/worker/frontend containers)
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot

function Check-Docker {
    Write-Host "[STEP] Checking Docker availability..." -ForegroundColor Cyan
    try {
        $dockerCheck = & docker info 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[ERROR] Docker daemon is not running or not accessible. Please start Docker Desktop." -ForegroundColor Red
            exit 1
        }
    } catch {
        Write-Host "[ERROR] Docker command not found. Please ensure Docker Desktop is installed and in PATH." -ForegroundColor Red
        exit 1
    }

    try {
        $composeCheck = & docker compose version 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[ERROR] Docker Compose is not available. Please verify your Docker installation." -ForegroundColor Red
            exit 1
        }
    } catch {
        Write-Host "[ERROR] Docker Compose command not found." -ForegroundColor Red
        exit 1
    }
    Write-Host "[OK] Docker and Docker Compose are available." -ForegroundColor Green
}

function Check-EnvFile {
    $hasProdEnv = Test-Path "$Root\.env.production"
    $hasDevEnv = Test-Path "$Root\.env"

    if (-not $hasProdEnv -and -not $hasDevEnv) {
        Write-Host "[WARN] No environment configuration file found (.env.production or .env)." -ForegroundColor Yellow
        Write-Host "[STEP] Copying .env.production.example to .env.production..." -ForegroundColor Cyan
        Copy-Item "$Root\.env.production.example" "$Root\.env.production"
        Write-Host "[ACTION REQUIRED] Edit .env.production and replace all REPLACE_WITH_* placeholders before running again." -ForegroundColor Red
        exit 1
    }

    $activeEnv = if ($hasProdEnv) { "$Root\.env.production" } else { "$Root\.env" }
    $envName = [System.IO.Path]::GetFileName($activeEnv)
    Write-Host "[INFO] Using environment file: $envName" -ForegroundColor Cyan

    $envContent = Get-Content $activeEnv -Raw
    if ($envContent -match "REPLACE_WITH_") {
        Write-Host "[WARN] $envName still contains unreplaced placeholder values:" -ForegroundColor Yellow
        Select-String -Path $activeEnv -Pattern "REPLACE_WITH_" | ForEach-Object { Write-Host "  $_" }
        Write-Host "[CAUTION] Proceeding with placeholder values (NOT suitable for production)." -ForegroundColor Red
    }

    return $activeEnv
}

function Wait-ForPostgres {
    param([string]$EnvFile, [int]$MaxWaitSec = 45)
    Write-Host "[STEP] Waiting for PostgreSQL container to become ready..." -ForegroundColor Cyan
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $MaxWaitSec) {
        try {
            $status = (& docker compose --env-file $EnvFile ps postgres --format "{{.Health}}" 2>&1).Trim()
            if ($status -eq "healthy") {
                Write-Host "[OK] PostgreSQL is ready and healthy." -ForegroundColor Green
                return
            }
        } catch {}
        Start-Sleep -Seconds 1
    }
    Write-Host "[ERROR] PostgreSQL did not become healthy within ${MaxWaitSec}s." -ForegroundColor Red
    exit 1
}

function Wait-ForHealth {
    param([string]$ServiceUrl, [string]$ServiceName, [int]$MaxWaitSec = 60)
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    Write-Host "[STEP] Waiting for $ServiceName to be healthy..." -ForegroundColor Cyan
    while ($sw.Elapsed.TotalSeconds -lt $MaxWaitSec) {
        try {
            $resp = Invoke-WebRequest -Uri $ServiceUrl -TimeoutSec 3 -UseBasicParsing
            if ($resp.StatusCode -eq 200) {
                Write-Host "[OK] $ServiceName is healthy." -ForegroundColor Green
                return
            }
        } catch {}
        Start-Sleep -Seconds 2
    }
    Write-Host "[ERROR] $ServiceName did not become healthy within ${MaxWaitSec}s." -ForegroundColor Red
    exit 1
}

function Run-Migration {
    param([string]$EnvFile)
    Write-Host "[STEP] Verifying and applying Alembic migrations on API container..." -ForegroundColor Cyan
    & docker compose --env-file $EnvFile exec api alembic upgrade head
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Database migration failed. Check API container logs via: docker compose logs api" -ForegroundColor Red
        exit 1
    }
    Write-Host "[OK] Database schema verified and at migration head." -ForegroundColor Green
}

function Seed-Demo {
    Write-Host "[STEP] Seeding demo project via API..." -ForegroundColor Cyan
    try {
        $resp = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/projects/seed-demo" -Method POST -ContentType "application/json"
        Write-Host "[OK] Demo project seeded: $($resp.repository)" -ForegroundColor Green
    } catch {
        Write-Host "[WARN] Demo project seeding skipped or returned an error: $_" -ForegroundColor Yellow
    }
}

# ─── Main ───────────────────────────────────────────────────────────────────

Check-Docker
$ActiveEnvFile = Check-EnvFile

if ($DevMode) {
    Write-Host "[DEV MODE] Starting only Postgres and Redis services..." -ForegroundColor Cyan
    & docker compose --env-file $ActiveEnvFile up postgres redis -d
    Wait-ForPostgres -EnvFile $ActiveEnvFile
    Write-Host "[OK] Development backing services are ready." -ForegroundColor Green
} else {
    Write-Host "[STEP] Building and starting all CodeSentinel services..." -ForegroundColor Cyan
    & docker compose --env-file $ActiveEnvFile up --build -d
    Wait-ForPostgres -EnvFile $ActiveEnvFile
    Wait-ForHealth "http://localhost:8000/api/v1/health/live" "Backend API"
    Run-Migration -EnvFile $ActiveEnvFile
    Seed-Demo
    Write-Host ""
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green
    Write-Host " CodeSentinel is running!" -ForegroundColor Green
    Write-Host "  Frontend :  http://localhost:5173" -ForegroundColor Cyan
    Write-Host "  Backend  :  http://localhost:8000/api/v1/health/live" -ForegroundColor Cyan
    Write-Host "  API Docs :  http://localhost:8000/docs" -ForegroundColor Cyan
    Write-Host "  Metrics  :  http://localhost:9090" -ForegroundColor Cyan
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green
}
