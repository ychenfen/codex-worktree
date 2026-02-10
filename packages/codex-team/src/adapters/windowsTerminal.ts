import { commandExists, runInherit } from "../utils/shell.js";
import { Role } from "../types.js";

export type WorktreePathMap = Partial<Record<Role, string>>;

export function launchWindowsTerminal(paths: WorktreePathMap, withBuilderB: boolean): void {
  if (!commandExists("wt")) {
    throw new Error("Windows Terminal (wt) not found. Install from Microsoft Store.");
  }

  const required = ["lead", "builder-a", "reviewer", "tester"] as const;
  for (const role of required) {
    if (!paths[role]) {
      throw new Error(`Missing worktree path for role: ${role}`);
    }
  }

  const args: string[] = [
    "new-tab",
    "-d",
    paths["lead"] as string,
    "powershell",
    "-NoExit",
    "-Command",
    "codex",
    ";",
    "split-pane",
    "-H",
    "-d",
    paths["builder-a"] as string,
    "powershell",
    "-NoExit",
    "-Command",
    "codex",
    ";",
    "split-pane",
    "-V",
    "-d",
    paths["reviewer"] as string,
    "powershell",
    "-NoExit",
    "-Command",
    "codex",
    ";",
    "move-focus",
    "left",
    ";",
    "split-pane",
    "-V",
    "-d",
    paths["tester"] as string,
    "powershell",
    "-NoExit",
    "-Command",
    "codex",
  ];

  if (withBuilderB && paths["builder-b"]) {
    args.push(
      ";",
      "new-tab",
      "-d",
      paths["builder-b"] as string,
      "powershell",
      "-NoExit",
      "-Command",
      "codex",
    );
  }

  runInherit("wt", args);
}
