# Однократная подготовка PostgreSQL: роль-владелец ev_admin и база evadvisor.
# Запуск из корня репозитория:  powershell -ExecutionPolicy Bypass -File scripts\init_db.ps1
# Пароль суперпользователя postgres вводится вручную и нигде не сохраняется.
# Пароли учётных записей проекта генерируются в .env (файл не попадает в git).
# Скрипт идемпотентен: повторный запуск ничего не ломает.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root '.env'

function Find-Psql {
    $cmd = Get-Command psql -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @('D:\Programms1\PostgreSQL\bin\psql.exe') +
        (Get-ChildItem 'C:\Program Files\PostgreSQL\*\bin\psql.exe' -ErrorAction SilentlyContinue | ForEach-Object FullName)
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    throw 'psql.exe not found. Add PostgreSQL bin folder to PATH.'
}

function New-Secret {
    $chars = [char[]]'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    -join (1..24 | ForEach-Object { $chars | Get-Random })
}

# 1. .env: создать из шаблона и заполнить пустые пароли
if (-not (Test-Path $envFile)) { Copy-Item (Join-Path $root '.env.example') $envFile }
$lines = Get-Content $envFile -Encoding UTF8
$cfg = @{}
$out = foreach ($l in $lines) {
    if ($l -match '^\s*(PG_[A-Z_]+_PASSWORD)\s*=\s*$') { $s = New-Secret; $cfg[$Matches[1]] = $s; "$($Matches[1])=$s" }
    else {
        if ($l -match '^\s*([A-Z_]+)\s*=\s*(.*)$') { $cfg[$Matches[1]] = $Matches[2].Trim() }
        $l
    }
}
[System.IO.File]::WriteAllLines($envFile, [string[]]$out, (New-Object System.Text.UTF8Encoding($false)))

$psql = Find-Psql
$pgHost = $cfg['PG_HOST']; $port = $cfg['PG_PORT']; $db = $cfg['PG_DB']
$admin = $cfg['PG_ADMIN_USER']; $adminPwd = $cfg['PG_ADMIN_PASSWORD']

# 2. Пароль суперпользователя: только в памяти процесса
$sec = Read-Host 'Password of PostgreSQL superuser "postgres"' -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
$env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)

try {
    $sqlRole = @"
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
    & $psql -w -h $pgHost -p $port -U postgres -d postgres -v ON_ERROR_STOP=1 -q -c $sqlRole
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create role (wrong password?)' }

    $exists = & $psql -w -h $pgHost -p $port -U postgres -d postgres -At -c "SELECT 1 FROM pg_database WHERE datname = '$db'"
    if ($exists -ne '1') {
        & $psql -w -h $pgHost -p $port -U postgres -d postgres -v ON_ERROR_STOP=1 -q -c "CREATE DATABASE $db OWNER $admin ENCODING 'UTF8' TEMPLATE template0"
        if ($LASTEXITCODE -ne 0) { throw 'Failed to create database' }
    }
    # Владелец БД должен уметь выдавать права на схему public и создавать расширения
    & $psql -w -h $pgHost -p $port -U postgres -d $db -v ON_ERROR_STOP=1 -q -c "ALTER SCHEMA public OWNER TO $admin; REVOKE ALL ON DATABASE $db FROM PUBLIC;"
    Write-Host "OK: role $admin and database $db are ready. Next: .venv\Scripts\evadvisor db migrate"
}
finally {
    $env:PGPASSWORD = $null
}
