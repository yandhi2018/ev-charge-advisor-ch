# Ежедневная догрузка источников по расписанию (Планировщик Windows).
# Запуск: powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1
# Задача evadvisor-daily выполняет `evadvisor run-all` в 03:30. Если ПК был выключен,
# запуск выполнится при следующем включении (StartWhenAvailable): загрузчики инкрементальны
# и догрузят пропущенное. Удаление: Unregister-ScheduledTask -TaskName evadvisor-daily

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $root '.venv\Scripts\evadvisor.exe'
if (-not (Test-Path $exe)) { throw "Not found: $exe. Install the project first (see README)." }
$log = Join-Path $root 'data\logs'
New-Item -ItemType Directory -Force $log | Out-Null

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -WorkingDirectory $root -Argument (
    "-NoProfile -WindowStyle Hidden -Command `"& '$exe' run-all *>> '$log\daily.log'`"")
$trigger = New-ScheduledTaskTrigger -Daily -At 03:30
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
    -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'evadvisor-daily' -Action $action -Trigger $trigger -Settings $settings `
    -Description 'EV Charge Advisor CH: incremental load, transforms, data quality' -Force | Out-Null
Write-Host "Registered task evadvisor-daily (03:30 daily). Check: Get-ScheduledTask -TaskName evadvisor-daily"
