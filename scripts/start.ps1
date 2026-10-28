# Phase 7 Deployment Startup Script (Windows PowerShell)
# ─────────────────────────────────────────────────────────
# Prerequisites: Docker Desktop running, .env.production file populated.
# Run from the CodeSentinel project root.

param(
    [switch]$DevMode   # Use dev DB/Redis only (no backend/frontend containers)
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot

function Check-EnvFile {
    if (-not (Test-Path "$Root\.env.production")) {
        Write-Host "[WARN] .env.production not found. Copying from example template..." -ForegroundColor Yellow
        Copy-Item "$Root\.env.production.example" "$Root\.env.production"
        Write-Host "[ACTION REQUIRED] Edit .env.production and replace all REPLACE_WITH_* placeholders." -ForegroundColor Red
        exit 1
    }
    # Check for unset placeholders
    $envContent = Get-Content "$Root\.env.production" -Raw
    if ($envContent -match "REPLACE_WITH_") {
        Write-Host "[WARN] .env.production still contains unreplaced placeholder values:" -ForegroundColor Yellow
        Select-String -Path "$Root\.env.production" -Pattern "REPLACE_WITH_" | ForEach-Object { Write-Host "  $_" }
        Write-Host "[CAUTION] Proceeding with placeholder values (NOT suitable for production)." -ForegroundColor Red
    }
}

function Run-Migration {
    Write-Host "[STEP] Running Alembic migrations against PostgreSQL..." -ForegroundColor Cyan
    & docker compose exec backend alembic upgrade head
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Migration failed. Check backend logs." -ForegroundColor Red
        exit 1
    }
    Write-Host "[OK] Migrations applied." -ForegroundColor Green
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

function Seed-Demo {
    Write-Host "[STEP] Seeding demo project via API..." -ForegroundColor Cyan
    $resp = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/projects/seed-demo" -Method POST -ContentType "application/json"
    Write-Host "[OK] Demo project seeded: $($resp.repository)" -ForegroundColor Green
}

# ─── Main ───────────────────────────────────────────────────────────────────

Check-EnvFile

if ($DevMode) {
    Write-Host "[DEV MODE] Starting only Postgres + Redis..." -ForegroundColor Cyan
    docker compose up postgres redis -d
} else {
    Write-Host "[STEP] Building and starting all services..." -ForegroundColor Cyan
    docker compose up --build -d
    Wait-ForHealth "http://localhost:8000/api/v1/health/live" "Backend API"
    Run-Migration
    Seed-Demo
    Write-Host ""
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green
    Write-Host " CodeSentinel is running!" -ForegroundColor Green
    Write-Host "  Frontend :  http://localhost:5173" -ForegroundColor Cyan
    Write-Host "  Backend  :  http://localhost:8000/api/v1/health/live" -ForegroundColor Cyan
    Write-Host "  API Docs :  http://localhost:8000/docs" -ForegroundColor Cyan
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green
}

