import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export function findRepoRoot(startDir = process.cwd()): string {
  let current = path.resolve(startDir);

  while (true) {
    if (fs.existsSync(path.join(current, ".git"))) {
      return current;
    }

    const parent = path.dirname(current);
    if (parent === current) {
      throw new Error("Current directory is not inside a git repository. Run from repo root.");
    }
    current = parent;
  }
}

export function getDefaultCtxDir(): string {
  const home = os.homedir();
  return path.join(home, "codex-ctx");
}

export function getWorktreePath(repoRoot: string, worktreeName: string): string {
  return path.resolve(repoRoot, "..", worktreeName);
}

export function roleFileName(role: string): string {
  return `ROLE_${role.toUpperCase().replace(/-/g, "_")}.md`;
}
