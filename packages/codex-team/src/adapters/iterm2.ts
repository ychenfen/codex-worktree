import { commandExists, runScript } from "../utils/shell.js";
import { Role } from "../types.js";

export type WorktreePathMap = Partial<Record<Role, string>>;

function shellCommandForWorktree(worktreePath: string): string {
  const escapedPath = worktreePath.replace(/\\/g, "\\\\").replace(/\"/g, '\\\"');
  return `cd \"${escapedPath}\"; codex`;
}

export function launchIterm2(paths: WorktreePathMap, withBuilderB: boolean): void {
  if (!commandExists("osascript")) {
    throw new Error("osascript not found on macOS.");
  }

  const required = ["lead", "builder-a", "reviewer", "tester"] as const;
  for (const role of required) {
    if (!paths[role]) {
      throw new Error(`Missing worktree path for role: ${role}`);
    }
  }

  const leadCmd = shellCommandForWorktree(paths["lead"] as string);
  const builderCmd = shellCommandForWorktree(paths["builder-a"] as string);
  const reviewCmd = shellCommandForWorktree(paths["reviewer"] as string);
  const testCmd = shellCommandForWorktree(paths["tester"] as string);
  const builderBCmd = paths["builder-b"] ? shellCommandForWorktree(paths["builder-b"] as string) : "";

  const extraTab = withBuilderB && builderBCmd
    ? `
  tell createdWindow
    set extraTab to (create tab with default profile)
    tell current session of extraTab
      write text "${builderBCmd}"
    end tell
  end tell`
    : "";

  const script = `
tell application "iTerm2"
  activate
  set createdWindow to (create window with default profile)
  tell current session of createdWindow
    set leadSession to it
    write text "${leadCmd}"

    set builderSession to (split horizontally with default profile)
    tell builderSession
      write text "${builderCmd}"
    end tell

    tell leadSession
      set testerSession to (split vertically with default profile)
      tell testerSession
        write text "${testCmd}"
      end tell
    end tell

    tell builderSession
      set reviewerSession to (split vertically with default profile)
      tell reviewerSession
        write text "${reviewCmd}"
      end tell
    end tell
  end tell${extraTab}
end tell
`;

  runScript("osascript", script);
}
