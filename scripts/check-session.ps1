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

foreach ($f in @("task.md", "decision.md", "verify.md", "pitfalls.md", "journal.md")) {
    $p = Join-Path (Join-Path $sessionRoot "shared") $f
    if (-not (Test-Path $p)) {
        $errors += "Missing shared file: $p"
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
