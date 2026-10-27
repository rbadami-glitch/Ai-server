<#
Run on the AI server in elevated Windows PowerShell after mail settings have
been placed in C:\ProgramData\CWAI\Ollama500Alert\config.json.
This task reads recorder metadata; it does not restart Ollama or Open WebUI.
#>
param(
    [ValidateSet('Install', 'Restart', 'Status')]
    [string]$Mode = 'Status',
    [string]$PythonPath
)

$ErrorActionPreference = 'Stop'
$taskName = 'CWAI-Ollama-500-Alert'
$scriptPath = Join-Path $PSScriptRoot 'ollama_500_alert.py'
$expectedPath = 'E:\AI\ollama\OpenWebUI-Monitoring\github\OpenWebUi Monitor\ollama_500_alert.py'
$configPath = 'C:\ProgramData\CWAI\Ollama500Alert\config.json'
$statePath = 'C:\ProgramData\CWAI\Ollama500Alert\state.json'

function Get-AlertTask {
    Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
}

if ($Mode -eq 'Status') {
    $task = Get-AlertTask
    if ($task) {
        $task | Select-Object TaskName, State
        Get-ScheduledTaskInfo -TaskName $taskName |
            Select-Object LastRunTime, LastTaskResult, NextRunTime
        $task.Actions | Select-Object Execute, Arguments, WorkingDirectory
    } else {
        Write-Output "Task $taskName is not installed."
    }
    Write-Output "Config present: $(Test-Path -LiteralPath $configPath -PathType Leaf)"
    Write-Output "State present: $(Test-Path -LiteralPath $statePath -PathType Leaf)"
    exit
}

if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Open Windows PowerShell as Administrator on the AI server.'
}
if (-not [string]::Equals($scriptPath, $expectedPath, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Run this script from the AI-server checkout: $expectedPath"
}
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "Mail configuration is missing: $configPath"
}

$task = Get-AlertTask
if ($Mode -eq 'Install') {
    if ($task) { throw "Task $taskName already exists. Use -Mode Status or -Mode Restart." }
    if (-not $PythonPath) {
        $dashboardTask = Get-ScheduledTask -TaskName 'CWAI-Feedback-Dashboard' -ErrorAction SilentlyContinue
        if ($dashboardTask -and $dashboardTask.Actions.Count -eq 1) {
            $PythonPath = $dashboardTask.Actions[0].Execute
        }
    }
    if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath -PathType Leaf) -or
        $PythonPath -match '\\WindowsApps\\') {
        throw 'Supply the full path to a real Python 3 executable with -PythonPath.'
    }
} else {
    if (-not $task) { throw "Task $taskName is not installed." }
    if ($task.Actions.Count -ne 1 -or $task.Actions[0].Arguments -notlike "*$expectedPath*") {
        throw "Task $taskName does not point to the expected monitor. Inspect it manually."
    }
    $PythonPath = $task.Actions[0].Execute
}

& $PythonPath -B $scriptPath --config $configPath --check-config
if ($LASTEXITCODE -ne 0) { throw 'Mail configuration failed validation.' }

if ($Mode -eq 'Install') {
    $action = New-ScheduledTaskAction -Execute $PythonPath `
        -Argument ('-B "{0}" --config "{1}"' -f $scriptPath, $configPath) `
        -WorkingDirectory $PSScriptRoot
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
        -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 1) `
        -MultipleInstances IgnoreNew -StartWhenAvailable
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings `
        -Description 'Email the first observed Ollama HTTP 500 per incident' | Out-Null
} else {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 2
Get-AlertTask | Select-Object TaskName, State
if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
    Write-Warning "State file not present yet: $statePath. Inspect the task and alert.log."
}
