import { spawnSync } from "node:child_process";

export interface ShellResult {
  stdout: string;
  stderr: string;
  status: number;
}

export function run(command: string, args: string[], cwd?: string): ShellResult {
  const result = spawnSync(command, args, {
    cwd,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });

  return {
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    status: result.status ?? 1,
  };
}

export function runWithInput(command: string, args: string[], input: string, cwd?: string): ShellResult {
  const needsShell = process.platform === "win32" && /\.cmd$/i.test(command);
  const result = spawnSync(command, args, {
    cwd,
    input,
    encoding: "utf8",
    stdio: ["pipe", "pipe", "pipe"],
    shell: needsShell,
  });

  const spawnError = result.error ? String(result.error.message ?? result.error) : "";
  return {
    stdout: result.stdout ?? "",
    stderr: (result.stderr ?? "") + (spawnError ? `\n${spawnError}` : ""),
    status: result.status ?? 1,
  };
}

export function runChecked(command: string, args: string[], cwd?: string): string {
  const result = run(command, args, cwd);
  if (result.status !== 0) {
    throw new Error(`Command failed: ${command} ${args.join(" ")}\n${result.stderr || result.stdout}`);
  }
  return result.stdout.trim();
}

export function runInherit(command: string, args: string[], cwd?: string): void {
  const result = spawnSync(command, args, {
    cwd,
    encoding: "utf8",
    stdio: "inherit",
  });

  if ((result.status ?? 1) !== 0) {
    throw new Error(`Command failed: ${command} ${args.join(" ")}`);
  }
}

export function commandExists(command: string): boolean {
  const checker = process.platform === "win32" ? "where" : "which";
  const result = spawnSync(checker, [command], {
    encoding: "utf8",
    stdio: ["ignore", "ignore", "ignore"],
  });
  return (result.status ?? 1) === 0;
}

export function runScript(command: string, scriptContent: string): void {
  const result = spawnSync(command, ["-"], {
    input: scriptContent,
    encoding: "utf8",
    stdio: ["pipe", "inherit", "pipe"],
  });
  if ((result.status ?? 1) !== 0) {
    throw new Error(`Script execution failed: ${command}\n${result.stderr ?? ""}`);
  }
}
