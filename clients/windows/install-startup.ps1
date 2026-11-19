param(
    [Parameter(Mandatory = $true)]
    [string]$Python
)

$ErrorActionPreference = 'Stop'
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
$pythonw = (Resolve-Path -LiteralPath (Join-Path (Split-Path $pythonPath) 'pythonw.exe')).Path
$script = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'report.py')).Path
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$taskName = "Sleepy Agent - $env:USERNAME"
$action = New-ScheduledTaskAction -Execute $pythonw -Argument ('"{0}" run' -f $script)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Output "Started scheduled task: $taskName"
