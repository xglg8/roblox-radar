param([string]$At = '10:00', [string]$TaskName = 'RobloxRadar-Daily', [switch]$Daily)
$ErrorActionPreference = 'Stop'
$pythonExe = (Get-Command python -ErrorAction Stop).Source
$scriptPath = Join-Path $PSScriptRoot 'radar.py'
$command = if ($Daily) { 'daily' } else { 'hourly' }
$action = New-ScheduledTaskAction -Execute $pythonExe -Argument ('"{0}" {1}' -f $scriptPath, $command) -WorkingDirectory $PSScriptRoot
$trigger = if ($Daily) { New-ScheduledTaskTrigger -Daily -At $At } else {
    0..23 | ForEach-Object { New-ScheduledTaskTrigger -Daily -At ((Get-Date).Date.AddHours($_)) }
}
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 45) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 15)
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
# Fail rather than overwrite a pre-existing task of the same name.
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    throw "Task already exists: $TaskName. Choose another -TaskName or remove the old task explicitly."
}
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Roblox Top 500 CCU collection'
Get-ScheduledTaskInfo -TaskName $TaskName
