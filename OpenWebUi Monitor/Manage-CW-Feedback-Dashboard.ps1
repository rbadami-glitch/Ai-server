<#
Run on the AI server in elevated Windows PowerShell.
Install: keep the feedback dashboard running at startup, independently of login.
Restart: reload reviewed source while preserving its private access link.
Status: inspect task and local HTTP availability.
Open: open the private local link in the interactive browser.
#>
param(
    [ValidateSet('Install', 'Restart', 'Status', 'Open')]
    [string]$Mode = 'Status',
    [string]$PythonPath
)

$ErrorActionPreference = 'Stop'
$taskName = 'CWAI-Feedback-Dashboard'
$runnerPath = Join-Path $PSScriptRoot 'Run-CW-Feedback-Dashboard.py'
$expectedPath = 'E:\AI\ollama\OpenWebUI-Monitoring\github\OpenWebUi Monitor\Run-CW-Feedback-Dashboard.py'
$tokenDir = 'C:\ProgramData\CWAI\FeedbackDashboard'
$tokenPath = Join-Path $tokenDir 'access-token.txt'
$url = 'http://127.0.0.1:8789/'

function Get-DashboardTask {
    Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
}

function Read-DashboardToken {
    if (-not (Test-Path -LiteralPath $tokenPath -PathType Leaf)) {
        throw "Access-token file is missing: $tokenPath"
    }
    $token = [System.IO.File]::ReadAllText($tokenPath).Trim()
    if ($token -cnotmatch '^[0-9a-f]{64}$') {
        throw 'Access-token file has an invalid format.'
    }
    return $token
}

function Test-DashboardHttp {
    try {
        $token = Read-DashboardToken
        $response = Invoke-WebRequest -Uri ($url + '?key=' + $token) -UseBasicParsing -TimeoutSec 3
        return ($response.StatusCode -eq 200)
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

function Wait-PortClosed {
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if (-not (Get-NetTCPConnection -LocalPort 8789 -State Listen -ErrorAction SilentlyContinue)) {
            return $true
        }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Assert-ExpectedTask($task) {
    if (-not $task) { throw "Task $taskName is not installed." }
    if ($task.Actions.Count -ne 1 -or $task.Actions[0].Arguments -notlike "*$expectedPath*") {
        throw "Task $taskName does not point to the expected launcher. Inspect it manually."
    }
}

function Initialize-TokenFile {
    if (Test-Path -LiteralPath $tokenPath -PathType Leaf) {
        throw "Access-token file already exists without the task. Inspect it before installing: $tokenPath"
    }
    New-Item -ItemType Directory -Path $tokenDir -Force | Out-Null
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $inherit = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
               [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
    foreach ($sidValue in @('S-1-5-18', 'S-1-5-32-544')) {
        $sid = New-Object System.Security.Principal.SecurityIdentifier($sidValue)
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $sid, [System.Security.AccessControl.FileSystemRights]::FullControl,
            $inherit, [System.Security.AccessControl.PropagationFlags]::None,
            [System.Security.AccessControl.AccessControlType]::Allow
        )
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $tokenDir -AclObject $acl
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $token = [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
    [System.IO.File]::WriteAllText($tokenPath, $token, (New-Object System.Text.UTF8Encoding($false)))
    $null = Read-DashboardToken
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
    Write-Output "Feedback dashboard available at $url`: $(Test-DashboardHttp)"
    exit
}

if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Open Windows PowerShell as Administrator on the AI server.'
}

if ($Mode -eq 'Open') {
    Assert-ExpectedTask (Get-DashboardTask)
    if (-not (Test-DashboardHttp)) { throw 'Feedback dashboard is not available. Run -Mode Status.' }
    $token = Read-DashboardToken
    Start-Process ($url + '?key=' + $token)
    Write-Output 'Opened the private feedback dashboard in the browser. Keep its URL private.'
    exit
}

if (-not [string]::Equals($runnerPath, $expectedPath, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Run this script from the AI-server checkout: $expectedPath"
}
if (-not (Test-Path -LiteralPath $runnerPath -PathType Leaf)) {
    throw "Feedback dashboard launcher not found: $runnerPath"
}

if ($Mode -eq 'Install') {
    if (Get-DashboardTask) { throw "Task $taskName already exists. Use -Mode Status or -Mode Restart." }
    if (Get-NetTCPConnection -LocalPort 8789 -State Listen -ErrorAction SilentlyContinue) {
        throw 'Port 8789 is already in use. Stop the manually started feedback dashboard and retry.'
    }
    if (-not $PythonPath) {
        $gpuTask = Get-ScheduledTask -TaskName 'CWAI-GPU-Dashboard' -ErrorAction SilentlyContinue
        if ($gpuTask -and $gpuTask.Actions.Count -eq 1) {
            $PythonPath = $gpuTask.Actions[0].Execute
        }
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
    Initialize-TokenFile
    $action = New-ScheduledTaskAction -Execute $PythonPath `
        -Argument ('-B "{0}" --token-file "{1}"' -f $runnerPath, $tokenPath) `
        -WorkingDirectory $PSScriptRoot
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
        -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 1) `
        -MultipleInstances IgnoreNew -StartWhenAvailable
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Description 'Local Open WebUI feedback dashboard on 127.0.0.1:8789' | Out-Null
    Start-ScheduledTask -TaskName $taskName
} elseif ($Mode -eq 'Restart') {
    $task = Get-DashboardTask
    Assert-ExpectedTask $task
    $null = Read-DashboardToken
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if (-not (Wait-PortClosed)) {
        throw 'Port 8789 did not close. Inspect its owning process before starting the task again.'
    }
    Start-ScheduledTask -TaskName $taskName
}

if (-not (Wait-DashboardHttp $true)) {
    throw "Feedback dashboard did not start at $url. Check Get-ScheduledTaskInfo -TaskName '$taskName' and database permissions for SYSTEM."
}
Write-Output "Feedback dashboard running at $url"
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
Write-Output "Run -Mode Open from an elevated interactive PowerShell to open its private link."
