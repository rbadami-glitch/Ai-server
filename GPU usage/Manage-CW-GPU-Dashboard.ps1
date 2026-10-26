<#
Run on the AI server from its Git checkout, in an elevated Windows PowerShell.
Install: register and start the dashboard at Windows startup.
Restart: reload the dashboard after a reviewed Git fast-forward update.
Status: show the task and local HTTP status.
#>
param(
    [ValidateSet('Install', 'Restart', 'Status')]
    [string]$Mode = 'Status',
    [string]$PythonPath
)

$ErrorActionPreference = 'Stop'
$taskName = 'CWAI-GPU-Dashboard'
$scriptPath = Join-Path $PSScriptRoot 'CW-GPU-Dashboard.py'
$expectedPath = 'E:\AI\ollama\OpenWebUI-Monitoring\github\GPU usage\CW-GPU-Dashboard.py'
$url = 'http://127.0.0.1:8791/'
$historyDir = 'E:\AI\ollama\OpenWebUI-Monitoring\GPU-RCA-Checks\CW-GPU-Dashboard-History'

function Get-DashboardTask {
    Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
}

function Test-DashboardHttp {
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3
        return ($response.StatusCode -eq 200 -and $response.Content -match 'GPU')
    } catch {
        return $false
    }
}

function Wait-DashboardHttp([bool]$WantAvailable) {
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if ((Test-DashboardHttp) -eq $WantAvailable) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Assert-ExpectedTask($task) {
    if (-not $task) { throw "Task $taskName is not installed." }
    if ($task.Actions.Count -ne 1 -or $task.Actions[0].Arguments -notlike "*$expectedPath*") {
        throw "Task $taskName does not point to the expected dashboard script. Inspect it manually."
    }
}

if ($Mode -eq 'Status') {
    $task = Get-DashboardTask
    if ($task) {
        $task | Select-Object TaskName, State
        Get-ScheduledTaskInfo -TaskName $taskName |
            Select-Object LastRunTime, LastTaskResult, NextRunTime
        $task.Actions | Select-Object Execute, Arguments, WorkingDirectory
    } else {
        Write-Output "Task $taskName is not installed."
    }
    Write-Output "Dashboard available at $url`: $(Test-DashboardHttp)"
    exit
}

if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Open Windows PowerShell as Administrator on the AI server.'
}
if (-not [string]::Equals($scriptPath, $expectedPath, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Run this script from the AI-server checkout: $expectedPath"
}
if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
    throw "Dashboard script not found: $scriptPath"
}

if ($Mode -eq 'Install') {
    if (Get-DashboardTask) { throw "Task $taskName already exists. Use -Mode Status or -Mode Restart." }
    if (Get-NetTCPConnection -LocalPort 8791 -State Listen -ErrorAction SilentlyContinue) {
        throw 'Port 8791 is already in use. Stop the old dashboard with Ctrl+C and retry.'
    }
    if (-not $PythonPath) {
        $python = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($python) { $PythonPath = $python.Source }
    }
    if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath -PathType Leaf) -or
        $PythonPath -match '\\WindowsApps\\') {
        throw 'Supply the full path to a real Python 3 executable with -PythonPath.'
    }
    & $PythonPath --version
    if ($LASTEXITCODE -ne 0) { throw "Python failed: $PythonPath" }

    $action = New-ScheduledTaskAction -Execute $PythonPath `
        -Argument ('-B "{0}" --no-browser' -f $scriptPath) `
        -WorkingDirectory $PSScriptRoot
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
        -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 1) `
        -MultipleInstances IgnoreNew -StartWhenAvailable
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Description 'Local GPU and Ollama dashboard on 127.0.0.1:8791' | Out-Null
    Start-ScheduledTask -TaskName $taskName
} elseif ($Mode -eq 'Restart') {
    $task = Get-DashboardTask
    Assert-ExpectedTask $task
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if (-not (Wait-DashboardHttp $false)) {
        throw 'Port 8791 did not close. Inspect its owning process before starting the task again.'
    }
    Start-ScheduledTask -TaskName $taskName
}

if (-not (Wait-DashboardHttp $true)) {
    throw "Dashboard did not start at $url. Check Get-ScheduledTaskInfo -TaskName '$taskName' and the Python path."
}
if (-not (Test-Path -LiteralPath $historyDir -PathType Container)) {
    throw "Dashboard responds, but the history directory is missing: $historyDir"
}
$snapshot = Invoke-RestMethod -Uri 'http://127.0.0.1:8791/api/snapshot' -TimeoutSec 15
if (-not $snapshot.gpus -or $snapshot.gpus.Count -lt 1) {
    throw 'Dashboard responds but cannot read GPU data. Check nvidia-smi in the task account.'
}
$todayFile = Join-Path $historyDir ((Get-Date).ToString('yyyy-MM-dd') + '.json')
if (-not (Test-Path -LiteralPath $todayFile -PathType Leaf)) {
    throw "Dashboard responds but today's history file is missing: $todayFile"
}
Write-Output "Dashboard running at $url"
Write-Output "History directory: $historyDir"
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
