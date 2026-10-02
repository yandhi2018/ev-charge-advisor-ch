# Однократная подготовка PostgreSQL: роль-владелец ev_admin и база evadvisor.
# Запуск из корня репозитория:  powershell -ExecutionPolicy Bypass -File scripts\init_db.ps1
#
# PG_MODE=docker (по умолчанию): поднимает контейнер postgres:17 (docker-compose.yml);
#   пароль суперпользователя генерируется в .env, вводить ничего не нужно.
# PG_MODE=local: используется установленный PostgreSQL; пароль суперпользователя postgres
#   вводится вручную и нигде не сохраняется.
# Пароли учётных записей проекта генерируются в .env (файл не попадает в git).
# Скрипт идемпотентен: повторный запуск ничего не ломает.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root '.env'
Set-Location $root

function New-Secret {
    $chars = [char[]]'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    -join (1..24 | ForEach-Object { $chars | Get-Random })
}

function Find-Psql {
    $cmd = Get-Command psql -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @('D:\Programms1\PostgreSQL\bin\psql.exe') +
        (Get-ChildItem 'C:\Program Files\PostgreSQL\*\bin\psql.exe' -ErrorAction SilentlyContinue | ForEach-Object FullName)
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    throw 'psql.exe not found. Add PostgreSQL bin folder to PATH or use PG_MODE=docker.'
}

# 1. .env: создать из шаблона и заполнить пустые пароли
if (-not (Test-Path $envFile)) { Copy-Item (Join-Path $root '.env.example') $envFile }
$lines = Get-Content $envFile -Encoding UTF8
$mode = (($lines | Where-Object { $_ -match '^\s*PG_MODE\s*=' }) -replace '^\s*PG_MODE\s*=\s*', '').Trim()
if (-not $mode) { $mode = 'docker' }
$cfg = @{}
$out = foreach ($l in $lines) {
    if ($l -match '^\s*(PG_[A-Z_]+_PASSWORD)\s*=\s*$' -and ($Matches[1] -ne 'PG_SUPERUSER_PASSWORD' -or $mode -eq 'docker')) {
        $s = New-Secret; $cfg[$Matches[1]] = $s; "$($Matches[1])=$s"
    } else {
        if ($l -match '^\s*([A-Z_]+)\s*=\s*(.*)$') { $cfg[$Matches[1]] = $Matches[2].Trim() }
        $l
    }
}
[System.IO.File]::WriteAllLines($envFile, [string[]]$out, (New-Object System.Text.UTF8Encoding($false)))

$db = $cfg['PG_DB']; $admin = $cfg['PG_ADMIN_USER']; $adminPwd = $cfg['PG_ADMIN_PASSWORD']

if ($mode -eq 'docker') {
    docker compose up -d db
    if ($LASTEXITCODE -ne 0) { throw 'docker compose failed: is Docker Desktop running?' }
    Write-Host 'Waiting for PostgreSQL container...'
    for ($i = 0; $i -lt 60; $i++) {
        docker exec evadvisor-db pg_isready -U postgres -q
        if ($LASTEXITCODE -eq 0) { break }
        Start-Sleep 2
    }
    function Invoke-Sql([string]$database, [string]$sql) {
        $sql | docker exec -i evadvisor-db psql -U postgres -d $database -v ON_ERROR_STOP=1 -q -At
        if ($LASTEXITCODE -ne 0) { throw "SQL failed on $database" }
    }
} else {
    $psql = Find-Psql
    $sec = Read-Host 'Password of PostgreSQL superuser "postgres"' -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    function Invoke-Sql([string]$database, [string]$sql) {
        $sql | & $psql -w -h $cfg['PG_HOST'] -p $cfg['PG_PORT'] -U postgres -d $database -v ON_ERROR_STOP=1 -q -At
        if ($LASTEXITCODE -ne 0) { throw "SQL failed on $database (wrong password?)" }
    }
}

try {
    Invoke-Sql 'postgres' @"
DO `$`$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$admin') THEN
    CREATE ROLE $admin LOGIN CREATEROLE PASSWORD '$adminPwd';
  ELSE
    ALTER ROLE $admin LOGIN CREATEROLE PASSWORD '$adminPwd';
  END IF;
END
`$`$;
"@
    $exists = Invoke-Sql 'postgres' "SELECT 1 FROM pg_database WHERE datname = '$db'"
    if ($exists -ne '1') {
        Invoke-Sql 'postgres' "CREATE DATABASE $db OWNER $admin ENCODING 'UTF8' TEMPLATE template0"
    }
    # Владелец БД распоряжается схемой public; PUBLIC не получает доступ к базе по умолчанию
    Invoke-Sql $db "ALTER SCHEMA public OWNER TO $admin; REVOKE ALL ON DATABASE $db FROM PUBLIC;"
    Write-Host "OK ($mode): role $admin and database $db are ready. Next: .venv\Scripts\evadvisor db migrate"
}
finally {
    $env:PGPASSWORD = $null
}
