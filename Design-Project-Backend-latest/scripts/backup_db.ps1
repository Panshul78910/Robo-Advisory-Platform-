<#
.SYNOPSIS
  Back up and restore the local Postgres database, vector store included.

.DESCRIPTION
  Re-ingesting the corpus costs a couple of minutes of CPU embedding, which is
  not something to do with an audience watching. A dump restores the same 159
  chunks -- vectors and all -- in seconds, so a reset during a demo is
  recoverable rather than a detour.

  pgvector columns dump and restore like any other type, provided the extension
  exists in the target database. It does, because the restore targets the same
  database it came from.

.EXAMPLE
  .\scripts\backup_db.ps1
  Writes backups\roboadvisor_<timestamp>.dump

.EXAMPLE
  .\scripts\backup_db.ps1 -Restore
  Restores the most recent dump in backups\.

.EXAMPLE
  .\scripts\backup_db.ps1 -Restore -File backups\roboadvisor_20261013_0214.dump
  Restores a specific dump.
#>
[CmdletBinding()]
param(
    [switch]$Restore,
    [string]$File,
    [string]$OutDir = "backups"
)

$ErrorActionPreference = "Stop"

# --- connection details, read from .env ------------------------------------
# The LAST DATABASE_URL wins, deliberately: .env currently carries two, and
# dotenv applies the last one. Taking the first would quietly target the stale
# Supabase host, which still answers and still holds an old one-document corpus
# -- so a backup would appear to succeed while saving the wrong database.
$envPath = Join-Path $PSScriptRoot "..\.env"
if (-not (Test-Path $envPath)) { throw "No .env found at $envPath" }

$dbLine = Get-Content $envPath | Where-Object { $_ -match '^\s*DATABASE_URL\s*=' } | Select-Object -Last 1
if (-not $dbLine) { throw "No DATABASE_URL in .env" }

$url = ($dbLine -split '=', 2)[1].Trim().Trim('"').Trim("'")
# postgresql+psycopg://user:password@host:port/dbname  ->  parts
if ($url -notmatch '^[a-z+]+://(?<user>[^:]+):(?<pass>[^@]*)@(?<host>[^:/]+):(?<port>\d+)/(?<db>[^?]+)') {
    throw "Could not parse DATABASE_URL (expected user:password@host:port/dbname)"
}
$dbUser = $Matches['user']; $dbPass = $Matches['pass']
$dbHost = $Matches['host']; $dbPort = $Matches['port']; $dbName = $Matches['db']

if ($dbHost -notmatch '^(127\.0\.0\.1|localhost)$') {
    Write-Warning "DATABASE_URL points at $dbHost, not localhost. Continuing, but check this is the database you meant."
}

# pg_dump and pg_restore are not on PATH by default on Windows.
function Resolve-PgTool([string]$name) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $guess = Get-ChildItem "C:\Program Files\PostgreSQL\*\bin\$name.exe" -ErrorAction SilentlyContinue |
             Sort-Object FullName -Descending | Select-Object -First 1
    if ($guess) { return $guess.FullName }
    throw "$name not found. Add PostgreSQL's bin directory to PATH, or install the client tools."
}

$env:PGPASSWORD = $dbPass    # process-scoped; gone when this script exits

if ($Restore) {
    $pgRestore = Resolve-PgTool "pg_restore"

    if (-not $File) {
        $latest = Get-ChildItem (Join-Path $PSScriptRoot "..\$OutDir\*.dump") -ErrorAction SilentlyContinue |
                  Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if (-not $latest) { throw "No .dump files in $OutDir. Run without -Restore first." }
        $File = $latest.FullName
    }
    if (-not (Test-Path $File)) { throw "No such dump: $File" }

    Write-Host "Restoring $dbName from $File" -ForegroundColor Yellow
    Write-Host "This DROPS and recreates the database objects in the dump." -ForegroundColor Yellow
    $answer = Read-Host "Type the database name to confirm"
    if ($answer -ne $dbName) { Write-Host "Cancelled."; return }

    # --clean --if-exists so a restore over a populated database replaces it
    # rather than colliding on every primary key. Stop the backend first: open
    # connections hold locks that make the DROPs fail.
    & $pgRestore --host=$dbHost --port=$dbPort --username=$dbUser --dbname=$dbName `
                 --clean --if-exists --no-owner --no-privileges $File
    if ($LASTEXITCODE -ne 0) { Write-Warning "pg_restore exited $LASTEXITCODE (per-object errors are often benign on --clean)." }
    Write-Host "Restored." -ForegroundColor Green
}
else {
    $pgDump = Resolve-PgTool "pg_dump"

    $dir = Join-Path $PSScriptRoot "..\$OutDir"
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
    $stamp = Get-Date -Format "yyyyMMdd_HHmm"
    $out = Join-Path $dir "$($dbName)_$stamp.dump"

    Write-Host "Backing up $dbName from $dbHost`:$dbPort -> $out"
    # Custom format (-Fc): compressed, and restorable selectively if you ever
    # want just the corpus tables back (pg_restore -t chunk ...).
    & $pgDump --host=$dbHost --port=$dbPort --username=$dbUser --dbname=$dbName `
              --format=custom --no-owner --no-privileges --file=$out
    if ($LASTEXITCODE -ne 0) { throw "pg_dump failed with exit code $LASTEXITCODE" }

    $size = [math]::Round((Get-Item $out).Length / 1MB, 1)
    Write-Host "Wrote $out ($size MB)" -ForegroundColor Green
}
