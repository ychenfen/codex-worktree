[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SessionName,

    [Parameter(Mandatory = $true)]
    [ValidateSet("lead", "builder-a", "builder-b", "reviewer", "tester")]
    [string]$Role,

    [Parameter(Mandatory = $true)]
    [string]$Message,

    [string]$Mention = "",

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

$chatPath = Join-Path (Join-Path $sessionRoot "shared") "chat.md"
if (-not (Test-Path $chatPath)) {
    throw "Chat file not found: $chatPath"
}

$ts = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
$to = ""
if ($Mention.Trim()) {
    $to = " -> @" + $Mention.Trim()
}

$entry = @"

### [$ts] $Role$to

$Message
"@

Add-Content -Path $chatPath -Value $entry -Encoding utf8
Write-Host "Appended chat to: $chatPath" -ForegroundColor Green

