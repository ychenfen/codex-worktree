import fs from "node:fs";
import path from "node:path";
import { loadConfig } from "./config.js";
import { UpOptions, WorktreeSpec, Role } from "../types.js";
import { getWorktreePath, roleFileName } from "../utils/paths.js";
import { runChecked, runInherit, commandExists } from "../utils/shell.js";
import { launchWindowsTerminal } from "../adapters/windowsTerminal.js";
import { launchIterm2 } from "../adapters/iterm2.js";
import { launchMacTerminalFallback } from "../adapters/terminalFallback.js";

function branchExists(repoRoot: string, branch: string): boolean {
  const output = runChecked("git", ["-C", repoRoot, "branch", "--list", branch]);
  return output.length > 0;
}

function ensureMainBranch(repoRoot: string): void {
  if (branchExists(repoRoot, "main")) {
    return;
  }

  const current = runChecked("git", ["-C", repoRoot, "branch", "--show-current"]);
  if (current === "master") {
    runInherit("git", ["-C", repoRoot, "branch", "-m", "master", "main"]);
    return;
  }

  if (current.length > 0) {
    runInherit("git", ["-C", repoRoot, "branch", "main", current]);
    return;
  }

  runInherit("git", ["-C", repoRoot, "branch", "main"]);
}

function ensureWorktree(repoRoot: string, spec: WorktreeSpec): string {
  const worktreePath = getWorktreePath(repoRoot, spec.name);
  if (fs.existsSync(worktreePath)) {
    return worktreePath;
  }

  if (branchExists(repoRoot, spec.branch)) {
    runInherit("git", ["-C", repoRoot, "worktree", "add", worktreePath, spec.branch]);
  } else {
    runInherit("git", ["-C", repoRoot, "worktree", "add", "-b", spec.branch, worktreePath, "main"]);
  }

  return worktreePath;
}

function launchTerminal(paths: Partial<Record<Role, string>>, withBuilderB: boolean): void {
  if (process.platform === "win32") {
    launchWindowsTerminal(paths, withBuilderB);
    return;
  }

  if (process.platform === "darwin") {
    try {
      launchIterm2(paths, withBuilderB);
    } catch (error) {
      console.warn(`[warn] iTerm2 launch failed: ${(error as Error).message}`);
      console.warn("[warn] Fallback to macOS Terminal multi-window mode.");
      launchMacTerminalFallback(paths, withBuilderB);
    }
    return;
  }

  throw new Error("Unsupported OS for terminal auto-layout. Supported: Windows, macOS.");
}

export function runUp(repoRoot: string, options: UpOptions): void {
  if (!fs.existsSync(path.join(repoRoot, ".git"))) {
    throw new Error("Current directory is not a git repository. Run 'git init' first.");
  }

  if (options.layout !== "quad") {
    throw new Error("Only --layout quad is currently supported.");
  }

  if (!commandExists("git")) {
    throw new Error("git not found in PATH.");
  }

  const config = loadConfig(repoRoot);
  ensureMainBranch(repoRoot);

  const worktreeSpecs = config.worktrees.filter((spec) => options.withBuilderB || spec.role !== "builder-b");
  const roleToPath: Partial<Record<Role, string>> = {};

  for (const spec of worktreeSpecs) {
    roleToPath[spec.role] = ensureWorktree(repoRoot, spec);
  }

  launchTerminal(roleToPath, options.withBuilderB);

  console.log("codex-team up completed");
  console.log(`ctx_dir: ${config.ctxDir}`);
  console.log(`bus_dir: ${config.busDir}`);

  const roleFiles = ["lead", "builder-a", "reviewer", "tester"] as const;
  for (const role of roleFiles) {
    console.log(`${role}_prompt: ${path.join(repoRoot, "roles", roleFileName(role))}`);
  }
  if (options.withBuilderB) {
    console.log(`builder-b_prompt: ${path.join(repoRoot, "roles", roleFileName("builder-b"))}`);
  }

  console.log("If model is unavailable, switch in Codex with /model.");
}
