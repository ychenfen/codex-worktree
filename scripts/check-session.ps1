[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SessionName,

    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-MainWorktreeRoot {
    param(
        [Parameter(Mandatory = $true)][string]$StartDir
    )

    $resolved = (Resolve-Path $StartDir).Path
    try {
        $lines = @(git -C $resolved worktree list --porcelain 2>$null)
        foreach ($line in $lines) {
            if ($line -like "worktree *") {
                return ($line.Substring(9)).Trim()
            }
        }
    }
    catch {
        # Fall back to the current worktree root if git is unavailable.
    }

    return $resolved
}

$RepoRoot = Resolve-MainWorktreeRoot -StartDir $RepoRoot
$RepoRoot = (Resolve-Path $RepoRoot).Path

$sessionRoot = Join-Path (Join-Path $RepoRoot "sessions") $SessionName
if (-not (Test-Path $sessionRoot)) {
    throw "Session not found: $sessionRoot"
}

$errors = @()
$warnings = @()

foreach ($f in @("task.md", "decision.md", "verify.md", "pitfalls.md", "journal.md", "chat.md")) {
    $p = Join-Path (Join-Path $sessionRoot "shared") $f
    if (-not (Test-Path $p)) {
        $errors += "Missing shared file: $p"
    }
}

$chatMessagesDir = Join-Path (Join-Path (Join-Path $sessionRoot "shared") "chat") "messages"
if (-not (Test-Path $chatMessagesDir)) {
    # Backward-compatible repair for sessions created before chat/messages existed.
    New-Item -ItemType Directory -Path $chatMessagesDir -Force | Out-Null
    $warnings += "Chat messages directory was missing and has been created: $chatMessagesDir"
}

$busRoot = Join-Path $sessionRoot "bus"
if (-not (Test-Path $busRoot)) {
    New-Item -ItemType Directory -Path $busRoot -Force | Out-Null
    $warnings += "Bus root was missing and has been created: $busRoot"
}

foreach ($p in @(
    (Join-Path $busRoot "inbox"),
    (Join-Path $busRoot "outbox"),
    (Join-Path $busRoot "deadletter")
)) {
    if (-not (Test-Path $p)) {
        New-Item -ItemType Directory -Path $p -Force | Out-Null
        $warnings += "Bus directory was missing and has been created: $p"
    }
}

$stateRoot = Join-Path $sessionRoot "state"
if (-not (Test-Path $stateRoot)) {
    New-Item -ItemType Directory -Path $stateRoot -Force | Out-Null
    $warnings += "State root was missing and has been created: $stateRoot"
}
foreach ($p in @(
    (Join-Path $stateRoot "processing"),
    (Join-Path $stateRoot "done"),
    (Join-Path $stateRoot "archive"),
    (Join-Path (Join-Path $stateRoot "router") "processed")
)) {
    if (-not (Test-Path $p)) {
        New-Item -ItemType Directory -Path $p -Force | Out-Null
        $warnings += "State directory was missing and has been created: $p"
    }
}

$rolesDir = Join-Path $sessionRoot "roles"
if (-not (Test-Path $rolesDir)) {
    $errors += "Missing roles directory: $rolesDir"
}

$roles = @()
if (Test-Path $rolesDir) {
    $roles = @(Get-ChildItem -Path $rolesDir -Directory | Select-Object -ExpandProperty Name)
}

if ($roles.Count -eq 0) {
    $errors += "No role directories found under roles/."
}

foreach ($role in $roles) {
    # Ensure per-role bus/state directories exist.
    $roleInboxDir = Join-Path (Join-Path $busRoot "inbox") $role
    if (-not (Test-Path $roleInboxDir)) {
        New-Item -ItemType Directory -Path $roleInboxDir -Force | Out-Null
        $warnings += "Role bus inbox directory was missing and has been created: $roleInboxDir"
    }
    $roleDeadletterDir = Join-Path (Join-Path $busRoot "deadletter") $role
    if (-not (Test-Path $roleDeadletterDir)) {
        New-Item -ItemType Directory -Path $roleDeadletterDir -Force | Out-Null
        $warnings += "Role bus deadletter directory was missing and has been created: $roleDeadletterDir"
    }
    $roleArchiveDir = Join-Path (Join-Path $stateRoot "archive") $role
    if (-not (Test-Path $roleArchiveDir)) {
        New-Item -ItemType Directory -Path $roleArchiveDir -Force | Out-Null
        $warnings += "Role archive directory was missing and has been created: $roleArchiveDir"
    }

    foreach ($f in @("inbox.md", "outbox.md", "worklog.md", "prompt.md")) {
        $p = Join-Path (Join-Path (Join-Path $sessionRoot "roles") $role) $f
        if (-not (Test-Path $p)) {
            $errors += "Missing role file: $p"
        }
    }

    $inbox = Join-Path (Join-Path (Join-Path $sessionRoot "roles") $role) "inbox.md"
    $outbox = Join-Path (Join-Path (Join-Path $sessionRoot "roles") $role) "outbox.md"

    $hasInboxTask = $false
    if (Test-Path $inbox) {
        $inboxRaw = Get-Content -Path $inbox -Raw -Encoding utf8
        $hasInboxTask = ($inboxRaw -match "New Task From Lead")
    }

    if ($hasInboxTask -and (Test-Path $outbox)) {
        $outboxRaw = Get-Content -Path $outbox -Raw -Encoding utf8
        if ($outboxRaw -match "暂无交付") {
            $warnings += "Role $role has pending inbox task but no outbox delivery yet."
        }
    }
}

$journalPath = Join-Path (Join-Path $sessionRoot "shared") "journal.md"
if (Test-Path $journalPath) {
    $journal = Get-Content -Path $journalPath -Raw -Encoding utf8
    if (-not ($journal -match "\|\s*Task\s*\|\s*Owner\s*\|\s*Status\s*\|")) {
        $warnings += "Journal missing active task table header."
    }
}

if ($errors.Count -gt 0) {
    Write-Host "Session check: FAIL" -ForegroundColor Red
    $errors | ForEach-Object { Write-Host "- $_" -ForegroundColor Red }
    if ($warnings.Count -gt 0) {
        Write-Host "Warnings:" -ForegroundColor Yellow
        $warnings | ForEach-Object { Write-Host "- $_" -ForegroundColor Yellow }
    }
    exit 1
}

Write-Host "Session check: PASS" -ForegroundColor Green
if ($warnings.Count -gt 0) {
    Write-Host "Warnings:" -ForegroundColor Yellow
    $warnings | ForEach-Object { Write-Host "- $_" -ForegroundColor Yellow }
}
