[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SessionName,

    [Parameter(Mandatory = $true)]
    [ValidateSet("builder-a", "builder-b", "reviewer", "tester")]
    [string]$Role,

    [Parameter(Mandatory = $true)]
    [string]$Message,

    [string]$Acceptance = "请在 outbox 中给出可执行验证证据。",

    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$sessionRoot = Join-Path $RepoRoot "sessions\$SessionName"
if (-not (Test-Path $sessionRoot)) {
    throw "Session not found: $sessionRoot"
}

$inboxPath = Join-Path $sessionRoot "roles\$Role\inbox.md"
if (-not (Test-Path $inboxPath)) {
    throw "Inbox not found: $inboxPath"
}

$ts = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
$entry = @"

## [$ts] New Task From Lead

- Message: $Message
- Acceptance: $Acceptance
- Reply channel: roles/$Role/outbox.md
"@

Add-Content -Path $inboxPath -Value $entry -Encoding utf8

$logScript = Join-Path $PSScriptRoot "log-entry.ps1"
& $logScript -SessionName $SessionName -Role "lead" -Channel "worklog" -Status "doing" -Message "Dispatched task to $Role" -Evidence "roles/$Role/inbox.md" -NextAction "Wait for outbox update." | Out-Null
& $logScript -SessionName $SessionName -Role "lead" -Channel "journal" -Status "doing" -Message "Lead dispatched task to $Role" -Evidence "roles/$Role/inbox.md" -NextAction "Review result from outbox." | Out-Null

Write-Host "Task dispatched to $Role" -ForegroundColor Green
