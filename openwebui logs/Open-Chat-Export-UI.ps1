$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$repo = Split-Path -Parent $PSScriptRoot
$exporter = Join-Path $PSScriptRoot 'Export-One-Chat.ps1'
if (-not (Test-Path -LiteralPath $exporter -PathType Leaf)) {
    throw 'Export-One-Chat.ps1 is missing.'
}

$form = New-Object System.Windows.Forms.Form
$form.Text = 'Export one Open WebUI chat'
$form.StartPosition = 'CenterScreen'
$form.Size = New-Object System.Drawing.Size(720, 320)
$form.MinimumSize = New-Object System.Drawing.Size(600, 320)
$form.MaximizeBox = $false

$label = New-Object System.Windows.Forms.Label
$label.Text = 'Paste the exact Open WebUI chat link (/c/ or /s/):'
$label.Location = New-Object System.Drawing.Point(20, 20)
$label.Size = New-Object System.Drawing.Size(650, 24)
$form.Controls.Add($label)

$linkBox = New-Object System.Windows.Forms.TextBox
$linkBox.Location = New-Object System.Drawing.Point(20, 48)
$linkBox.Size = New-Object System.Drawing.Size(660, 28)
$linkBox.Anchor = 'Top,Left,Right'
$form.Controls.Add($linkBox)

$notice = New-Object System.Windows.Forms.Label
$notice.Text = 'Saved chat messages and metadata are encrypted before upload. Attachment file contents are not included. The encrypted file goes to a public GitHub repository and remains in Git history.'
$notice.Location = New-Object System.Drawing.Point(20, 90)
$notice.Size = New-Object System.Drawing.Size(660, 64)
$notice.Anchor = 'Top,Left,Right'
$form.Controls.Add($notice)

$button = New-Object System.Windows.Forms.Button
$button.Text = 'Encrypt and push this chat'
$button.Location = New-Object System.Drawing.Point(20, 160)
$button.Size = New-Object System.Drawing.Size(205, 38)
$form.Controls.Add($button)

$status = New-Object System.Windows.Forms.TextBox
$status.Text = 'Ready. No files have been changed.'
$status.Location = New-Object System.Drawing.Point(20, 212)
$status.Size = New-Object System.Drawing.Size(660, 55)
$status.Anchor = 'Top,Left,Right'
$status.ReadOnly = $true
$status.Multiline = $true
$form.Controls.Add($status)
$form.AcceptButton = $button

$button.Add_Click({
    $link = $linkBox.Text.Trim()
    if ($link -notmatch '^https?://[^/]+/(?:c|s)/[^/?#]+/?(?:[?#].*)?$') {
        $status.Text = 'Enter a complete Open WebUI /c/ or /s/ chat link.'
        return
    }

    $button.Enabled = $false
    $artifact = $null
    try {
        $status.Text = 'Checking Git and exporting the selected chat...'
        $form.Refresh()
        [System.Windows.Forms.Application]::DoEvents()

        $origin = & git -C $repo remote get-url origin
        if ($LASTEXITCODE -ne 0) {
            throw "Could not read the Git origin from $repo."
        }
        if ($origin -notmatch '(?i)(?:[:/])rbadami-glitch/Ai-server(?:\.git)?$') {
            throw 'This checkout does not point to the expected GitHub repository.'
        }
        $branch = & git -C $repo branch --show-current
        if ($LASTEXITCODE -ne 0 -or $branch -ne 'main') {
            throw 'The checkout must be on the main branch.'
        }
        if (@(& git -C $repo status --porcelain).Count -ne 0) {
            throw 'The Git checkout has other changes. Resolve those before exporting.'
        }

        & git -C $repo pull --ff-only origin main | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Git pull failed.' }
        $artifact = & $exporter -ChatLink $link
        if (-not $artifact -or -not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
            throw 'The encrypted export was not created.'
        }
        $fullPath = [System.IO.Path]::GetFullPath($artifact)
        if ([System.IO.Path]::GetDirectoryName($fullPath) -ne $PSScriptRoot -or
            [System.IO.Path]::GetExtension($fullPath) -ne '.p7m') {
            throw 'The export path is outside the expected folder.'
        }

        $status.Text = 'Encrypted. Pushing only this file to GitHub...'
        $form.Refresh()
        [System.Windows.Forms.Application]::DoEvents()

        $relative = 'openwebui logs/' + [System.IO.Path]::GetFileName($fullPath)
        & git -C $repo add -- $relative | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Git add failed.' }
        $staged = @(& git -C $repo diff --cached --name-only)
        if ($LASTEXITCODE -ne 0 -or $staged.Count -ne 1 -or
            $staged[0] -notmatch [regex]::Escape([System.IO.Path]::GetFileName($fullPath))) {
            throw 'Unexpected staged files; stopped before committing.'
        }
        & git -C $repo commit -m 'Add encrypted Open WebUI chat export' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Git commit failed. The encrypted file remains local.' }
        & git -C $repo push origin main | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Git push failed. The encrypted commit remains local.' }

        $commit = & git -C $repo rev-parse --short HEAD
        $status.Text = 'Done. Encrypted chat pushed at commit ' + $commit + '. Tell the reviewer this commit ID.'
    } catch {
        $status.Text = 'Stopped: ' + $_.Exception.Message
        if ($artifact) {
            $status.Text += ' Encrypted file: ' + [System.IO.Path]::GetFileName($artifact)
        }
    } finally {
        $button.Enabled = $true
    }
})

[void]$form.ShowDialog()
$form.Dispose()
