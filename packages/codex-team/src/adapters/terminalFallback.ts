import { commandExists, runScript } from "../utils/shell.js";
import { Role } from "../types.js";

export type WorktreePathMap = Partial<Record<Role, string>>;

function shellCommandForWorktree(worktreePath: string): string {
  const escapedPath = worktreePath.replace(/\\/g, "\\\\").replace(/\"/g, '\\\"');
  return `cd \"${escapedPath}\"; codex`;
}

export function launchMacTerminalFallback(paths: WorktreePathMap, withBuilderB: boolean): void {
  if (!commandExists("osascript")) {
    throw new Error("osascript not found.");
  }

  const required = ["lead", "builder-a", "reviewer", "tester"] as const;
  for (const role of required) {
    if (!paths[role]) {
      throw new Error(`Missing worktree path for role: ${role}`);
    }
  }

  const commands = [
    shellCommandForWorktree(paths["lead"] as string),
    shellCommandForWorktree(paths["builder-a"] as string),
    shellCommandForWorktree(paths["reviewer"] as string),
    shellCommandForWorktree(paths["tester"] as string),
  ];

  if (withBuilderB && paths["builder-b"]) {
    commands.push(shellCommandForWorktree(paths["builder-b"] as string));
  }

  const scriptLines = [
    'tell application "Terminal"',
    "  activate",
    ...commands.map((cmd) => `  do script "${cmd}"`),
    "end tell",
  ];

  runScript("osascript", scriptLines.join("\n"));
}
