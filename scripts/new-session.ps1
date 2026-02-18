[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SessionName,

    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,

    [switch]$WithBuilderB,

    [switch]$CreateWorktrees,

    [switch]$BootstrapBus,

    [string]$BaseBranch = "main"
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

function Copy-TemplateFile {
    param(
        [Parameter(Mandatory = $true)][string]$TemplatePath,
        [Parameter(Mandatory = $true)][string]$OutputPath,
        [Parameter(Mandatory = $true)][hashtable]$Vars
    )

    if (-not (Test-Path $TemplatePath)) {
        throw "Template not found: $TemplatePath"
    }

    $content = Get-Content -Path $TemplatePath -Raw -Encoding utf8
    foreach ($k in $Vars.Keys) {
        $token = "{{${k}}}"
        $content = $content.Replace($token, [string]$Vars[$k])
    }

    $parent = Split-Path -Parent $OutputPath
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }

    Set-Content -Path $OutputPath -Value $content -Encoding utf8
}

function Resolve-BaseBranchName {
    param(
        [Parameter(Mandatory = $true)][string]$Repo,
        [Parameter(Mandatory = $true)][string]$Preferred
    )

    $heads = @(git -C $Repo for-each-ref --format='%(refname:short)' refs/heads)
    if ($heads.Count -eq 0) {
        throw "No local branches found in $Repo"
    }

    if ($heads -contains $Preferred) {
        return $Preferred
    }

    $current = (git -C $Repo branch --show-current).Trim()
    if ($current) {
        return $current
    }

    if ($heads -contains "master") {
        return "master"
    }

    return $heads[0]
}

function Add-RoleWorktree {
    param(
        [Parameter(Mandatory = $true)][string]$Repo,
        [Parameter(Mandatory = $true)][string]$WorktreeRoot,
        [Parameter(Mandatory = $true)][string]$SessionId,
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][string]$Base
    )

    $targetPath = Join-Path $WorktreeRoot $Role
    if (Test-Path $targetPath) {
        Write-Host "[skip] worktree exists: $targetPath"
        return
    }

    $branch = "session/$SessionId/$Role"
    $branchExists = @(git -C $Repo branch --list $branch).Count -gt 0

    if ($branchExists) {
        git -C $Repo worktree add $targetPath $branch | Out-Host
    }
    else {
        git -C $Repo worktree add -b $branch $targetPath $Base | Out-Host
    }
}

$RepoRoot = Resolve-MainWorktreeRoot -StartDir $RepoRoot
$RepoRoot = (Resolve-Path $RepoRoot).Path
if (-not (Test-Path (Join-Path $RepoRoot ".git"))) {
    throw "RepoRoot is not a git repository: $RepoRoot"
}

$sessionId = ($SessionName.Trim().ToLower() -replace "[^a-z0-9-]", "-").Trim("-")
if (-not $sessionId) {
    throw "SessionName contains no valid characters."
}

$sessionRoot = Join-Path (Join-Path $RepoRoot "sessions") $sessionId
if (Test-Path $sessionRoot) {
    throw "Session already exists: $sessionRoot"
}

$roles = @("lead", "builder-a", "reviewer", "tester")
if ($WithBuilderB) {
    $roles += "builder-b"
}

$templateRoot = Join-Path (Join-Path $RepoRoot "docs") "templates"
$promptRoot = Join-Path (Join-Path $RepoRoot "docs") "prompts"

New-Item -ItemType Directory -Path $sessionRoot -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $sessionRoot "shared") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $sessionRoot "roles") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $sessionRoot "artifacts") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path (Join-Path $sessionRoot "shared") "chat") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path (Join-Path (Join-Path $sessionRoot "shared") "chat") "messages") -Force | Out-Null

# Message bus + state (for unattended multi-role execution)
$busRoot = Join-Path $sessionRoot "bus"
New-Item -ItemType Directory -Path $busRoot -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $busRoot "outbox") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $busRoot "inbox") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $busRoot "deadletter") -Force | Out-Null

foreach ($r in $roles) {
    New-Item -ItemType Directory -Path (Join-Path (Join-Path $busRoot "inbox") $r) -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path (Join-Path $busRoot "deadletter") $r) -Force | Out-Null
}

$stateRoot = Join-Path $sessionRoot "state"
New-Item -ItemType Directory -Path $stateRoot -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $stateRoot "processing") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $stateRoot "done") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $stateRoot "archive") -Force | Out-Null
foreach ($r in $roles) {
    New-Item -ItemType Directory -Path (Join-Path (Join-Path $stateRoot "archive") $r) -Force | Out-Null
}

if ($BootstrapBus) {
    $ts = (Get-Date).ToString("yyyyMMdd-HHmmss")
    $bootId = "$ts-bootstrap"
    $leadInboxDir = Join-Path (Join-Path $busRoot "inbox") "lead"
    $bootMsgPath = Join-Path $leadInboxDir "$bootId.md"
    $bootMsg = @"
---
id: $bootId
from: system
to: lead
intent: bootstrap
thread: $sessionId
risk: low
acceptance:
  - "If shared/task.md is empty, ask for missing info (do not guess)."
  - "If shared/task.md is filled, break down and dispatch to roles via bus-send.sh."
---
Bootstrap autopilot for session $sessionId.

Read:
- shared/task.md
- docs/team-mode.md
- docs/bus.md

Then:
- If task is actionable: dispatch messages to bus/inbox/<role>/ using ./scripts/bus-send.sh.
- Otherwise: write what is missing and ask for clarification.
"@
    Set-Content -Path $bootMsgPath -Value $bootMsg -Encoding utf8
}

$baseVars = @{
    SESSION_ID   = $sessionId
    SESSION_ROOT = $sessionRoot
    CREATED_AT   = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
}

	foreach ($name in @("task", "decision", "verify", "pitfalls", "journal", "chat")) {
	    $sharedDir = Join-Path $sessionRoot "shared"
	    Copy-TemplateFile -TemplatePath (Join-Path $templateRoot "$name.md") -OutputPath (Join-Path $sharedDir "$name.md") -Vars $baseVars
	}

foreach ($role in $roles) {
    $roleRoot = Join-Path (Join-Path $sessionRoot "roles") $role
    New-Item -ItemType Directory -Path $roleRoot -Force | Out-Null

    $roleVars = @{}
    foreach ($item in $baseVars.GetEnumerator()) {
        $roleVars[$item.Key] = $item.Value
    }
    $roleVars["ROLE"] = $role

    foreach ($file in @("inbox", "outbox", "worklog")) {
        Copy-TemplateFile -TemplatePath (Join-Path $templateRoot "$file.md") -OutputPath (Join-Path $roleRoot "$file.md") -Vars $roleVars
    }

    $promptTemplate = Join-Path $promptRoot "$role.md"
    if (Test-Path $promptTemplate) {
        Copy-TemplateFile -TemplatePath $promptTemplate -OutputPath (Join-Path $roleRoot "prompt.md") -Vars $roleVars
    }
}

$promptLines = $roles | ForEach-Object {
    $p = Join-Path (Join-Path (Join-Path $sessionRoot "roles") $_) "prompt.md"
    "- $($_): `"$p`""
}
$bootLines = $roles | ForEach-Object {
    "- $($_): `"cd <worktree-for-$($_)>; codex`""
}

$sessionGuide = @"
# Session Guide - $sessionId

## Paths

- Session root: $sessionRoot
- Shared context: $(Join-Path $sessionRoot "shared")

## Role prompt files
$($promptLines -join "`n")

## Suggested terminal boot
$($bootLines -join "`n")

## Logging examples

~~~powershell
pwsh ./scripts/log-entry.ps1 -SessionName $sessionId -Role lead -Channel worklog -Status doing -Message "派工已发出" -Evidence "roles/builder-a/inbox.md" -NextAction "等待 builder-a outbox"
~~~

~~~powershell
pwsh ./scripts/dispatch.ps1 -SessionName $sessionId -Role builder-a -Message "实现最小改动方案" -Acceptance "pytest tests/test_xxx.py 通过"
~~~
"@

Set-Content -Path (Join-Path $sessionRoot "SESSION.md") -Value $sessionGuide -Encoding utf8

if ($CreateWorktrees) {
    $base = Resolve-BaseBranchName -Repo $RepoRoot -Preferred $BaseBranch
    $worktreeRoot = Join-Path (Split-Path -Parent $RepoRoot) "wk-$sessionId"
    New-Item -ItemType Directory -Path $worktreeRoot -Force | Out-Null

    foreach ($role in $roles) {
        Add-RoleWorktree -Repo $RepoRoot -WorktreeRoot $worktreeRoot -SessionId $sessionId -Role $role -Base $base
    }

    $worktreeLines = $roles | ForEach-Object {
        $p = Join-Path $worktreeRoot $_
        "- $($_): $p"
    }

    $worktreeSection = @"

## Worktree root
- $worktreeRoot

## Role worktrees
$($worktreeLines -join "`n")
"@

    Add-Content -Path (Join-Path $sessionRoot "SESSION.md") -Value $worktreeSection -Encoding utf8
}

Write-Host "Session created: $sessionRoot" -ForegroundColor Green
Write-Host ("Open: " + (Join-Path $sessionRoot "SESSION.md")) -ForegroundColor Green
