# Registra la tarea programada "Bot Presupuestos GBP": arranca el bot al iniciar
# sesión el usuario actual (headful, en su escritorio) y lo deja corriendo.
# El horario de trabajo (HORA_INICIO / HORA_FIN) lo controla el propio bot.
#
#   powershell -ExecutionPolicy Bypass -File scripts\instalar_tarea.ps1
#
# Para ver / detener / borrar:
#   Get-ScheduledTask "Bot Presupuestos GBP" | Get-ScheduledTaskInfo
#   Stop-ScheduledTask "Bot Presupuestos GBP"
#   Unregister-ScheduledTask "Bot Presupuestos GBP" -Confirm:$false

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $repo ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) { throw "No existe $pythonw (crear el venv primero)" }

$usuario = "$env:USERDOMAIN\$env:USERNAME"
$accion = New-ScheduledTaskAction -Execute $pythonw -Argument "run.py" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $usuario
$trigger.Delay = "PT1M"   # 1 minuto después del login (que haya red)
$principal = New-ScheduledTaskPrincipal -UserId $usuario -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName "Bot Presupuestos GBP" -Action $accion -Trigger $trigger `
    -Principal $principal -Settings $settings -Force `
    -Description "Carga en GBP los presupuestos encolados por el CRM ($repo)" | Out-Null

Write-Host "Tarea 'Bot Presupuestos GBP' registrada para $usuario (arranca al iniciar sesión)."
