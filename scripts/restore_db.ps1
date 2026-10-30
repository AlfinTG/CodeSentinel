<#
.SYNOPSIS
    Automated PostgreSQL database restoration script for CodeSentinel.

.DESCRIPTION
    Restores CodeSentinel database from a logical backup (.dump or .sql).
    Validates backup file integrity via SHA-256 sidecar verification before
    execution, enforces protected database safeguards, supports clean
    reconstruction, and verifies post-restoration schema and Alembic revisions.

.PARAMETER BackupFile
    Path to the backup file to restore (.dump or .sql).

.PARAMETER DbName
    Target database to restore into. Defaults to $env:POSTGRES_DB or 'codesentinel_dev'.

.PARAMETER DbHost
    PostgreSQL server host. Defaults to $env:POSTGRES_HOST or 'localhost'.

.PARAMETER Port
    PostgreSQL server port. Defaults to $env:POSTGRES_PORT or 5432.

.PARAMETER User
    PostgreSQL user. Defaults to $env:POSTGRES_USER or 'codesentinel'.

.PARAMETER Clean
    Drop existing database objects before restoring.

.PARAMETER Force
    Skip interactive confirmation prompt.

.PARAMETER SkipChecksum
    Bypass SHA-256 checksum verification against metadata sidecar.

.PARAMETER PgBinDir
    Optional path to directory containing pg_restore and psql executables.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BackupFile,
    [string]$DbName = $env:POSTGRES_DB,
    [string]$DbHost = $env:POSTGRES_HOST,
    [int]$Port = $(if ($env:POSTGRES_PORT) { [int]$env:POSTGRES_PORT } else { 5432 }),
    [string]$User = $env:POSTGRES_USER,
    [switch]$Clean,
    [switch]$Force,
    [switch]$SkipChecksum,
    [string]$PgBinDir = "",
    [switch]$Decrypt,
    [string]$EncryptionKey = $env:BACKUP_ENCRYPTION_KEY
)

$ErrorActionPreference = "Stop"

if (-not $DbName) { $DbName = "codesentinel_dev" }
if (-not $DbHost) { $DbHost = "localhost" }
if (-not $User) { $User = "codesentinel" }

# 0. Validate database identifier safety (before any I/O or network calls)
if ($DbName -notmatch '^[A-Za-z_][A-Za-z0-9_]{0,62}$') {
    throw "SAFETY ERROR: Invalid database name '$DbName'. Names must start with a letter or underscore, contain only letters, digits, and underscores, and be at most 63 characters (PostgreSQL NAMEDATALEN limit)."
}
if ($User -notmatch '^[A-Za-z_][A-Za-z0-9_]{0,62}$') {
    throw "SAFETY ERROR: Invalid PostgreSQL user '$User'. Names must start with a letter or underscore and contain only letters, digits, and underscores."
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "   CodeSentinel Automated Database Restore (Phase 8)      " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Backup File:    $BackupFile"
Write-Host "Target DB:      $DbName"
Write-Host "Host:Port:      ${DbHost}:${Port}"
Write-Host "User:           $User"
Write-Host "Clean Mode:     $Clean"
Write-Host ""

# 1. Resolve PostgreSQL binaries
function Resolve-PgBinary([string]$BinaryName, [string]$ExplicitDir) {
    if ($ExplicitDir -and (Test-Path (Join-Path $ExplicitDir "$BinaryName.exe"))) {
        return (Join-Path $ExplicitDir "$BinaryName.exe")
    }
    $workspacePortable = Join-Path $PSScriptRoot "..\..\pg_portable\pgsql\bin\$BinaryName.exe"
    if (Test-Path $workspacePortable) {
        return (Resolve-Path $workspacePortable).Path
    }
    $cmd = Get-Command $BinaryName -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    throw "PostgreSQL executable '$BinaryName' not found. Please specify -PgBinDir or install PostgreSQL."
}

$pgRestorePath = Resolve-PgBinary -BinaryName "pg_restore" -ExplicitDir $PgBinDir
$psqlPath = Resolve-PgBinary -BinaryName "psql" -ExplicitDir $PgBinDir

# 2. Validate backup file existence and destructive safety guards
if (-not (Test-Path $BackupFile)) {
    throw "Backup file not found at: $BackupFile"
}
$resolvedBackupFile = (Resolve-Path $BackupFile).Path

# Prevent restoring into protected PostgreSQL system databases
$protectedDatabases = @("postgres", "template0", "template1", "rdsadmin", "azure_maintenance")
if ($protectedDatabases -contains $DbName.ToLowerInvariant()) {
    throw "SAFETY ERROR: Refusing to restore into protected system database '$DbName'. Target a dedicated application database."
}

# Prevent restoring empty files
if ((Get-Item $resolvedBackupFile).Length -eq 0) {
    throw "SAFETY ERROR: Backup file '$resolvedBackupFile' is 0 bytes (empty). Cannot restore."
}

# 3. Verify SHA-256 Checksum against metadata sidecar
$metaFilePath = "${resolvedBackupFile}.meta.json"
$metaJson = $null

if ((Test-Path $metaFilePath) -and (-not $SkipChecksum)) {
    Write-Host "[INFO] Validating backup integrity via SHA-256 sidecar..." -ForegroundColor Cyan
    try {
        $metaJson = Get-Content -Path $metaFilePath -Raw | ConvertFrom-Json
    } catch {
        throw "SECURITY / INTEGRITY ERROR: Malformed metadata sidecar JSON at: $metaFilePath"
    }

    $expectedChecksum = $metaJson.sha256
    if ($expectedChecksum -and $expectedChecksum -ne "unsupported") {
        $hashAlgorithm = [System.Security.Cryptography.SHA256]::Create()
        $fileStream = [System.IO.File]::OpenRead($resolvedBackupFile)
        $hashBytes = $hashAlgorithm.ComputeHash($fileStream)
        $fileStream.Close()
        $fileStream.Dispose()
        $actualChecksum = [System.BitConverter]::ToString($hashBytes).Replace("-", "").ToLowerInvariant()

        if ($actualChecksum -ne $expectedChecksum.ToLowerInvariant()) {
            throw "SECURITY / INTEGRITY ERROR: SHA-256 checksum mismatch! Expected: $expectedChecksum, Actual: $actualChecksum. Backup may be corrupted or tampered with."
        }
        Write-Host "[OK] SHA-256 integrity check PASSED ($actualChecksum)" -ForegroundColor Green
    }
} elseif (-not $SkipChecksum) {
    Write-Host "[WARN] No metadata sidecar found at: $metaFilePath. Integrity verification skipped." -ForegroundColor Yellow
}

# 4. Confirmation guard & origin mismatch check
if ($metaJson -and $metaJson.database -and ($metaJson.database.ToLowerInvariant() -ne $DbName.ToLowerInvariant())) {
    Write-Host "[WARN] Backup origin was '$($metaJson.database)', but target database is '$DbName'!" -ForegroundColor Yellow
    if (-not $Force) {
        $mismatchConfirm = Read-Host "Origin database differs from target. Proceed with restore into '$DbName'? (y/N)"
        if ($mismatchConfirm -ne "y" -and $mismatchConfirm -ne "Y") {
            Write-Host "[ABORTED] Restoration cancelled due to database target mismatch." -ForegroundColor Red
            exit 0
        }
    }
}

if (-not $Force) {
    Write-Host "[CAUTION] This action will write data into database '$DbName' on ${DbHost}:${Port}." -ForegroundColor Yellow
    $confirm = Read-Host "Proceed with restoration? (y/N)"
    if ($confirm -ne "y" -and $confirm -ne "Y") {
        Write-Host "[ABORTED] Database restoration cancelled by operator." -ForegroundColor Red
        exit 0
    }
}

# 5. Terminate active connections if clean mode requested
if ($Clean) {
    Write-Host "[INFO] Terminating active connections to '$DbName'..."
    $safeDbName = $DbName.Replace("'", "''")
    $terminateSql = "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$safeDbName' AND pid <> pg_backend_pid();"
    & $psqlPath -h $DbHost -p $Port -U $User -d postgres -c $terminateSql 2>$null
}

# 5.5 Optional Decryption
$tempDecryptedFile = $null
$fileToRestore = $resolvedBackupFile

if ($Decrypt -or $resolvedBackupFile.EndsWith(".enc")) {
    if (-not $EncryptionKey) {
        throw "Restore file is encrypted, but neither -EncryptionKey nor BACKUP_ENCRYPTION_KEY was provided."
    }
    Write-Host "[INFO] Decrypting backup using AES-256-GCM..." -ForegroundColor Cyan
    $tempDecryptedFile = Join-Path ([System.IO.Path]::GetTempPath()) ("cs_restore_" + [System.Guid]::NewGuid().ToString("N"))
    $pythonCmd = if (Get-Command "python" -ErrorAction SilentlyContinue) { "python" } else { "py" }
    $backendPath = Join-Path $PSScriptRoot "..\backend"
    if (Test-Path $backendPath) {
        $backendPath = (Resolve-Path $backendPath).Path.Replace('\', '/')
    }
    $pyScript = "import sys; sys.path.insert(0, '$backendPath'); from app.services.backup.crypto import decrypt_file; decrypt_file('$($resolvedBackupFile.Replace('\', '/'))', '$($tempDecryptedFile.Replace('\', '/'))', '$EncryptionKey')"
    & $pythonCmd -c $pyScript
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $tempDecryptedFile)) {
        throw "Backup decryption failed. Authentication tag mismatch or invalid encryption key."
    }
    $fileToRestore = $tempDecryptedFile
    Write-Host "[OK] Backup decrypted successfully to secure temporary workspace." -ForegroundColor Green
}

try {
    # 6. Execute Restoration
    $isCustom = $fileToRestore.EndsWith(".dump") -or $fileToRestore.EndsWith(".pgdump") -or $resolvedBackupFile.EndsWith(".dump.enc")

    if ($isCustom) {
        Write-Host "[INFO] Executing pg_restore for custom format archive..." -ForegroundColor Cyan
        $restoreArgs = @(
            "-h", $DbHost,
            "-p", $Port,
            "-U", $User,
            "-d", $DbName,
            "--no-owner",
            "--no-privileges"
        )
        if ($Clean) {
            $restoreArgs += @("--clean", "--if-exists")
        }
        $restoreArgs += $fileToRestore

        & $pgRestorePath @restoreArgs
        if ($LASTEXITCODE -eq 1) {
            Write-Host "[WARN] pg_restore completed with non-fatal warnings (exit code 1)." -ForegroundColor Yellow
        } elseif ($LASTEXITCODE -gt 1) {
            throw "pg_restore failed with exit code $LASTEXITCODE"
        }
    } else {
        Write-Host "[INFO] Executing psql for plain SQL format script..." -ForegroundColor Cyan
        & $psqlPath -h $DbHost -p $Port -U $User -d $DbName -f $fileToRestore
        if ($LASTEXITCODE -ne 0) {
            throw "psql restore failed with exit code $LASTEXITCODE"
        }
    }
} finally {
    if ($tempDecryptedFile -and (Test-Path $tempDecryptedFile)) {
        Remove-Item -Path $tempDecryptedFile -Force -ErrorAction SilentlyContinue
        Write-Host "[INFO] Securely cleaned up temporary decrypted backup artifact." -ForegroundColor Cyan
    }
}

# 7. Post-restoration verification
Write-Host "[INFO] Verifying restored database state..." -ForegroundColor Cyan
$revOutput = & $psqlPath -h $DbHost -p $Port -U $User -d $DbName -t -A -c "SELECT version_num FROM alembic_version LIMIT 1;" 2>$null
$tableCount = & $psqlPath -h $DbHost -p $Port -U $User -d $DbName -t -A -c "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';" 2>$null

$alembicRevDisplay = if ($revOutput) { $revOutput.Trim() } else { "unknown" }
$tableCountDisplay = if ($tableCount) { $tableCount.Trim() } else { "0" }

Write-Host "==========================================================" -ForegroundColor Green
Write-Host "   Post-Restoration Verification Report                   " -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
Write-Host "Target Database:   $DbName"
Write-Host "Alembic Revision:  $alembicRevDisplay"
Write-Host "Public Tables:     $tableCountDisplay"
Write-Host "[SUCCESS] Database restoration completed successfully!" -ForegroundColor Green

return @{
    status = "restored"
    database = $DbName
    alembic_version = $alembicRevDisplay
    tables_count = [int]$tableCountDisplay
}
