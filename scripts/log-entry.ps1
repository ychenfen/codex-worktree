[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SessionName,

    [Parameter(Mandatory = $true)]
    [ValidateSet("lead", "builder-a", "builder-b", "reviewer", "tester")]
    [string]$Role,

    [ValidateSet("worklog", "inbox", "outbox", "journal")]
    [string]$Channel = "worklog",

    [ValidateSet("todo", "doing", "done", "blocked")]
    [string]$Status = "doing",

    [Parameter(Mandatory = $true)]
    [string]$Message,

    [string]$Evidence = "N/A",

    [string]$NextAction = "N/A",

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

switch ($Channel) {
    "journal" {
        $target = Join-Path (Join-Path $sessionRoot "shared") "journal.md"
    }
    default {
        $target = Join-Path (Join-Path (Join-Path $sessionRoot "roles") $Role) "$Channel.md"
    }
}

if (-not (Test-Path $target)) {
    throw "Target file not found: $target"
}

$ts = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
$entry = @"

## [$ts] $Role/$Channel [$Status]

- Message: $Message
- Evidence: $Evidence
- Next: $NextAction
"@

Add-Content -Path $target -Value $entry -Encoding utf8
Write-Host "Appended log to: $target" -ForegroundColor Green
