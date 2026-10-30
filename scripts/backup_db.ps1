<#
.SYNOPSIS
    Automated PostgreSQL logical backup script for CodeSentinel.

.DESCRIPTION
    Creates compressed logical database backups using pg_dump, generates SHA-256
    integrity checksums, records metadata sidecars (.meta.json), and enforces
    retention policies by pruning expired snapshots.

.PARAMETER DbName
    Database to back up. Defaults to $env:POSTGRES_DB or 'codesentinel_dev'.

.PARAMETER DbHost
    PostgreSQL server host. Defaults to $env:POSTGRES_HOST or 'localhost'.

.PARAMETER Port
    PostgreSQL server port. Defaults to $env:POSTGRES_PORT or 5432.

.PARAMETER User
    PostgreSQL user. Defaults to $env:POSTGRES_USER or 'codesentinel'.

.PARAMETER BackupDir
    Output directory for backups. Defaults to 'backups'.

.PARAMETER Format
    Backup format ('custom' for compressed .dump, 'plain' for .sql). Defaults to 'custom'.

.PARAMETER RetentionDays
    Number of days to keep backups. Older backups are pruned. Defaults to 7.

.PARAMETER PgBinDir
    Optional path to directory containing pg_dump and psql executables.
#>

[CmdletBinding()]
param(
    [string]$DbName = $env:POSTGRES_DB,
    [string]$DbHost = $env:POSTGRES_HOST,
    [int]$Port = $(if ($env:POSTGRES_PORT) { [int]$env:POSTGRES_PORT } else { 5432 }),
    [string]$User = $env:POSTGRES_USER,
    [string]$BackupDir = "backups",
    [ValidateSet("custom", "plain")]
    [string]$Format = "custom",
    [int]$RetentionDays = 7,
    [string]$PgBinDir = "",
    [switch]$Encrypt,
    [string]$EncryptionKey = $env:BACKUP_ENCRYPTION_KEY,
    [string]$WebhookUrl = $env:BACKUP_ALERT_WEBHOOK_URL,
    [string]$SnitchUrl = $env:BACKUP_SNITCH_URL
)

$ErrorActionPreference = "Stop"

if (-not $DbName) { $DbName = "codesentinel_dev" }
if (-not $DbHost) { $DbHost = "localhost" }
if (-not $User) { $User = "codesentinel" }

# 0. Validate database identifier safety
if ($DbName -notmatch '^[A-Za-z_][A-Za-z0-9_]{0,62}$') {
    throw "SAFETY ERROR: Invalid database name '$DbName'. Names must start with a letter or underscore, contain only letters, digits, and underscores, and be at most 63 characters."
}
if ($User -notmatch '^[A-Za-z_][A-Za-z0-9_]{0,62}$') {
    throw "SAFETY ERROR: Invalid PostgreSQL user '$User'. Names must start with a letter or underscore and contain only letters, digits, and underscores."
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "   CodeSentinel Automated Database Backup (Phase 8)       " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Database:       $DbName"
Write-Host "Host:Port:      ${DbHost}:${Port}"
Write-Host "User:           $User"
Write-Host "Backup Format:  $Format"
Write-Host "Retention:      $RetentionDays days"
Write-Host "Output Dir:     $BackupDir"
Write-Host ""

# 1. Resolve PostgreSQL binaries
function Resolve-PgBinary([string]$BinaryName, [string]$ExplicitDir) {
    if ($ExplicitDir -and (Test-Path (Join-Path $ExplicitDir "$BinaryName.exe"))) {
        return (Join-Path $ExplicitDir "$BinaryName.exe")
    }
    # Check default portable path in workspace
    $workspacePortable = Join-Path $PSScriptRoot "..\..\pg_portable\pgsql\bin\$BinaryName.exe"
    if (Test-Path $workspacePortable) {
        return (Resolve-Path $workspacePortable).Path
    }
    # Check system PATH
    $cmd = Get-Command $BinaryName -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    throw "PostgreSQL executable '$BinaryName' not found. Please specify -PgBinDir or install PostgreSQL."
}

$pgDumpPath = Resolve-PgBinary -BinaryName "pg_dump" -ExplicitDir $PgBinDir
$psqlPath = Resolve-PgBinary -BinaryName "psql" -ExplicitDir $PgBinDir
Write-Host "[OK] Using pg_dump: $pgDumpPath" -ForegroundColor Green

# 2. Ensure Backup Directory exists
if (-not (Test-Path $BackupDir)) {
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
    Write-Host "[OK] Created backup directory: $BackupDir" -ForegroundColor Green
}
$fullBackupDir = (Resolve-Path $BackupDir).Path

# 3. Query current Alembic revision from target database
$alembicRevision = "unknown"
try {
    $revOutput = & $psqlPath -h $DbHost -p $Port -U $User -d $DbName -t -A -c "SELECT version_num FROM alembic_version LIMIT 1;" 2>$null
    if ($LASTEXITCODE -eq 0 -and $revOutput) {
        $alembicRevision = $revOutput.Trim()
    }
} catch {
    Write-Host "[WARN] Could not retrieve alembic_version: $_" -ForegroundColor Yellow
}
Write-Host "[INFO] Current Alembic revision: $alembicRevision"

# 4. Generate timestamped filename
$timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd_HHmmss")
$ext = if ($Format -eq "custom") { "dump" } else { "sql" }
$backupFileName = "${DbName}_backup_${timestamp}.${ext}"
$backupFilePath = Join-Path $fullBackupDir $backupFileName
$metaFilePath = Join-Path $fullBackupDir "${backupFileName}.meta.json"

Write-Host "[INFO] Starting database dump to: $backupFilePath..."

# 5. Execute pg_dump
$pgDumpArgs = @(
    "-h", $DbHost,
    "-p", $Port,
    "-U", $User,
    "-d", $DbName,
    "-f", $backupFilePath
)

if ($Format -eq "custom") {
    $pgDumpArgs += @("-F", "c", "-Z", "6")
} else {
    $pgDumpArgs += @("-F", "p")
}

& $pgDumpPath @pgDumpArgs
if ($LASTEXITCODE -ne 0) {
    throw "pg_dump failed with exit code $LASTEXITCODE"
}

# 6. Compute SHA-256 Checksum
Write-Host "[INFO] Computing SHA-256 checksum..."
$hashAlgorithm = [System.Security.Cryptography.SHA256]::Create()
$fileStream = [System.IO.File]::OpenRead($backupFilePath)
$hashBytes = $hashAlgorithm.ComputeHash($fileStream)
$fileStream.Close()
$fileStream.Dispose()
$sha256Checksum = [System.BitConverter]::ToString($hashBytes).Replace("-", "").ToLowerInvariant()

$fileInfo = Get-Item $backupFilePath
$fileSizeBytes = $fileInfo.Length

Write-Host "[OK] Backup created successfully!" -ForegroundColor Green
Write-Host "     Size:     $fileSizeBytes bytes"
Write-Host "     SHA-256:  $sha256Checksum"

# 7. Optional Encryption (AES-256-GCM)
$isEncrypted = $false
$finalBackupPath = $backupFilePath
$finalBackupFileName = $backupFileName

if ($Encrypt) {
    if (-not $EncryptionKey) {
        throw "Encryption requested (-Encrypt) but neither -EncryptionKey nor BACKUP_ENCRYPTION_KEY env var was provided."
    }
    Write-Host "[INFO] Encrypting backup with AES-256-GCM..." -ForegroundColor Cyan
    $encFilePath = "${backupFilePath}.enc"
    
    # Locate python and backend path
    $pythonCmd = if (Get-Command "python" -ErrorAction SilentlyContinue) { "python" } else { "py" }
    $backendPath = Join-Path $PSScriptRoot "..\backend"
    if (Test-Path $backendPath) {
        $backendPath = (Resolve-Path $backendPath).Path.Replace('\', '/')
    }
    $pyScript = "import sys; sys.path.insert(0, '$backendPath'); from app.services.backup.crypto import encrypt_file; encrypt_file('$($backupFilePath.Replace('\', '/'))', '$($encFilePath.Replace('\', '/'))', '$EncryptionKey')"
    
    & $pythonCmd -c $pyScript
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $encFilePath)) {
        throw "Backup encryption failed."
    }

    # Replace unencrypted backup with encrypted file for secure at-rest storage
    Remove-Item -Path $backupFilePath -Force
    $finalBackupPath = $encFilePath
    $finalBackupFileName = "${backupFileName}.enc"
    $isEncrypted = $true
    
    # Recompute size and sha256 of encrypted artifact
    $fileInfo = Get-Item $finalBackupPath
    $fileSizeBytes = $fileInfo.Length
    $fileStream = [System.IO.File]::OpenRead($finalBackupPath)
    $hashBytes = $hashAlgorithm.ComputeHash($fileStream)
    $fileStream.Close()
    $fileStream.Dispose()
    $sha256Checksum = [System.BitConverter]::ToString($hashBytes).Replace("-", "").ToLowerInvariant()
    Write-Host "[OK] Backup encrypted: $finalBackupFileName ($fileSizeBytes bytes)" -ForegroundColor Green
}

# 8. Write metadata sidecar JSON
$metadata = @{
    version = "1.0"
    created_at = (Get-Date).ToUniversalTime().ToString("o")
    database = $DbName
    host = $DbHost
    port = $Port
    user = $User
    format = $Format
    alembic_version = $alembicRevision
    backup_file = $finalBackupFileName
    file_size_bytes = $fileSizeBytes
    sha256 = $sha256Checksum
    encrypted = $isEncrypted
    status = "completed"
} | ConvertTo-Json -Depth 4

$metaFilePath = Join-Path $fullBackupDir "${finalBackupFileName}.meta.json"
Set-Content -Path $metaFilePath -Value $metadata -Encoding UTF8
Write-Host "[OK] Metadata saved to: $metaFilePath" -ForegroundColor Green

# 9. Enforce Retention Policy
if ($RetentionDays -gt 0) {
    Write-Host "[INFO] Checking retention policy (older than $RetentionDays days)..."
    $cutoff = (Get-Date).AddDays(-$RetentionDays)
    $oldFiles = Get-ChildItem -Path $fullBackupDir -File | Where-Object {
        $_.LastWriteTime -lt $cutoff -and ($_.Name -like "*_backup_*.dump*" -or $_.Name -like "*_backup_*.sql*")
    }
    $deletedCount = 0
    foreach ($file in $oldFiles) {
        Remove-Item -Path $file.FullName -Force
        $deletedCount++
        Write-Host "     [PRUNED] Expired file removed: $($file.Name)" -ForegroundColor Yellow
    }
    if ($deletedCount -eq 0) {
        Write-Host "     No expired backups to prune."
    }
}

# 10. Failure / Success Notification Alerts
if ($WebhookUrl -or $SnitchUrl) {
    Write-Host "[INFO] Dispatching backup completion alerts..." -ForegroundColor Cyan
    try {
        $pythonCmd = if (Get-Command "python" -ErrorAction SilentlyContinue) { "python" } else { "py" }
        $alertPayload = @{
            status = "SUCCESS"
            database = $DbName
            host = $DbHost
            backup_file = $finalBackupFileName
            file_size_bytes = $fileSizeBytes
            sha256 = $sha256Checksum
            encrypted = $isEncrypted
        } | ConvertTo-Json -Compress
        
        $pyAlertScript = "import sys; sys.path.insert(0, '$backendPath'); from app.services.backup.alerting import send_backup_alert, ping_snitch; import json; d = json.loads('''$alertPayload''');"
        if ($WebhookUrl) {
            $pyAlertScript += "send_backup_alert('$WebhookUrl', d);"
        }
        if ($SnitchUrl) {
            $pyAlertScript += "ping_snitch('$SnitchUrl', 'success');"
        }
        & $pythonCmd -c $pyAlertScript
    } catch {
        Write-Host "[WARN] Alert notification failed: $_" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "[SUCCESS] Backup pipeline finished cleanly." -ForegroundColor Green
return @{
    backup_file = $finalBackupPath
    meta_file = $metaFilePath
    sha256 = $sha256Checksum
    size_bytes = $fileSizeBytes
    alembic_version = $alembicRevision
    encrypted = $isEncrypted
}
