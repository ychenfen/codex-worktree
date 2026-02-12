[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SessionName,

    [Parameter(Mandatory = $true)]
    [ValidateSet("lead", "builder-a", "builder-b", "reviewer", "tester")]
    [string]$From,

    [Parameter(Mandatory = $true)]
    [ValidateSet("lead", "builder-a", "builder-b", "reviewer", "tester")]
    [string]$To,

    [string]$Intent = "message",

    [Parameter(Mandatory = $true)]
    [string]$Message,

    [ValidateSet("low", "medium", "high")]
    [string]$Risk = "low",

    [string[]]$Acceptance = @(),

    [string]$Id = "",

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

$inboxDir = Join-Path (Join-Path (Join-Path (Join-Path $RepoRoot "sessions") $SessionName) "bus") (Join-Path "inbox" $To)
New-Item -ItemType Directory -Path $inboxDir -Force | Out-Null

if (-not $Id.Trim()) {
    $ts = (Get-Date).ToString("yyyyMMdd-HHmmss")
    $rand = -join ((48..57) + (97..122) | Get-Random -Count 6 | ForEach-Object { [char]$_ })
    $Id = "$ts-$rand"
}

$tmp = Join-Path $inboxDir ".tmp.$Id.$PID"
$out = Join-Path $inboxDir "$Id.md"

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add("---")
$lines.Add("id: $Id")
$lines.Add("from: $From")
$lines.Add("to: $To")
$lines.Add("intent: $Intent")
$lines.Add("thread: $SessionName")
$lines.Add("risk: $Risk")
if ($Acceptance.Count -gt 0) {
    $lines.Add("acceptance:")
    foreach ($a in $Acceptance) {
        $aa = $a.Replace('"', "'")
        $lines.Add("  - `"$aa`"")
    }
}
$lines.Add("---")
$lines.Add($Message)
$lines.Add("")

Set-Content -Path $tmp -Value ($lines -join "`n") -Encoding utf8
Move-Item -Path $tmp -Destination $out -Force
Write-Host "Enqueued: $out" -ForegroundColor Green

