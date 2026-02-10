[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SessionName,

    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,

    [switch]$WithBuilderB,

    [switch]$CreateWorktrees,

    [string]$BaseBranch = "main"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

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

$RepoRoot = (Resolve-Path $RepoRoot).Path
if (-not (Test-Path (Join-Path $RepoRoot ".git"))) {
    throw "RepoRoot is not a git repository: $RepoRoot"
}

$sessionId = ($SessionName.Trim().ToLower() -replace "[^a-z0-9-]", "-").Trim("-")
if (-not $sessionId) {
    throw "SessionName contains no valid characters."
}

$sessionRoot = Join-Path $RepoRoot "sessions\$sessionId"
if (Test-Path $sessionRoot) {
    throw "Session already exists: $sessionRoot"
}

$roles = @("lead", "builder-a", "reviewer", "tester")
if ($WithBuilderB) {
    $roles += "builder-b"
}

$templateRoot = Join-Path $RepoRoot "docs\templates"
$promptRoot = Join-Path $RepoRoot "docs\prompts"

New-Item -ItemType Directory -Path $sessionRoot -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $sessionRoot "shared") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $sessionRoot "roles") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $sessionRoot "artifacts") -Force | Out-Null

$baseVars = @{
    SESSION_ID   = $sessionId
    SESSION_ROOT = $sessionRoot
    CREATED_AT   = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
}

foreach ($name in @("task", "decision", "verify", "pitfalls", "journal")) {
    Copy-TemplateFile -TemplatePath (Join-Path $templateRoot "$name.md") -OutputPath (Join-Path $sessionRoot "shared\$name.md") -Vars $baseVars
}

foreach ($role in $roles) {
    $roleRoot = Join-Path $sessionRoot "roles\$role"
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
    "- $($_): `"$sessionRoot\roles\$($_)\prompt.md`""
}
$bootLines = $roles | ForEach-Object {
    "- $($_): `"cd <worktree-for-$($_)>; codex`""
}

$sessionGuide = @"
# Session Guide - $sessionId

## Paths

- Session root: $sessionRoot
- Shared context: $sessionRoot\shared

## Role prompt files
$($promptLines -join "`n")

## Suggested terminal boot
$($bootLines -join "`n")

## Logging examples

~~~powershell
.\scripts\log-entry.ps1 -SessionName $sessionId -Role lead -Channel worklog -Status doing -Message "派工已发出" -Evidence "roles\\builder-a\\inbox.md" -NextAction "等待 builder-a outbox"
~~~

~~~powershell
.\scripts\dispatch.ps1 -SessionName $sessionId -Role builder-a -Message "实现最小改动方案" -Acceptance "pytest tests/test_xxx.py 通过"
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

    Add-Content -Path (Join-Path $sessionRoot "SESSION.md") -Value "`n## Worktree root`n- $worktreeRoot`n" -Encoding utf8
}

Write-Host "Session created: $sessionRoot" -ForegroundColor Green
Write-Host "Open: $sessionRoot\SESSION.md" -ForegroundColor Green


