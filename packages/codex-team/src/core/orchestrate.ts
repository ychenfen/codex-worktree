import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { loadConfig } from "./config.js";
import { runUp } from "./up.js";
import { Role } from "../types.js";
import { getWorktreePath } from "../utils/paths.js";
import { ensureDir, writeTextFile, readTextFile, exists } from "../utils/fs.js";
import { run } from "../utils/shell.js";

interface OrchestrateStartOptions {
  context: string;
  withBuilderB: boolean;
  intervalSec: number;
  model?: string;
  fullAuto: boolean;
  includeLead: boolean;
  dangerousBypass: boolean;
}

interface WorkerRecord {
  role: Role;
  pid: number;
  worktreePath: string;
  logPath: string;
}

interface OrchestrateState {
  context: string;
  createdAt: string;
  repoRoot: string;
  workers: WorkerRecord[];
  stoppedAt?: string;
}

function sanitizeContext(context: string): string {
  return context.replace(/[^a-zA-Z0-9._-]/g, "_");
}

function getOrchestrateDir(logsDir: string): string {
  return path.join(logsDir, "orchestrate");
}

function getStatePath(logsDir: string, context: string): string {
  return path.join(getOrchestrateDir(logsDir), `${sanitizeContext(context)}.json`);
}

function getWorkerRoles(withBuilderB: boolean, includeLead: boolean): Role[] {
  const roles: Role[] = [];
  if (includeLead) {
    roles.push("lead");
  }
  roles.push("builder-a", "reviewer", "tester");
  if (withBuilderB) {
    roles.push("builder-b");
  }
  return roles;
}

function ensureRoleWorktreePath(repoRoot: string, role: Role): string {
  const nameMap: Record<Role, string> = {
    lead: "wk-lead",
    "builder-a": "wk-builder-a",
    reviewer: "wk-review",
    tester: "wk-test",
    "builder-b": "wk-builder-b",
  };
  return getWorktreePath(repoRoot, nameMap[role]);
}

function buildAutoArgs(
  distCliPath: string,
  role: Role,
  context: string,
  intervalSec: number,
  model: string | undefined,
  fullAuto: boolean,
  dangerousBypass: boolean,
): string[] {
  const args = [distCliPath, "auto", "--me", role, "--interval", String(intervalSec), "--context", context];
  if (model) {
    args.push("--model", model);
  }
  if (!fullAuto) {
    args.push("--no-full-auto");
  }
  if (dangerousBypass) {
    args.push("--dangerously-bypass-approvals-and-sandbox");
  }
  return args;
}

function isProcessRunning(pid: number): boolean {
  if (!Number.isInteger(pid) || pid <= 0) {
    return false;
  }

  if (process.platform === "win32") {
    const result = run("tasklist", ["/FI", `PID eq ${pid}`]);
    return result.status === 0 && result.stdout.includes(String(pid));
  }

  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

export function runOrchestrateStart(repoRoot: string, options: OrchestrateStartOptions): void {
  if (!options.context || options.context.trim() === "") {
    throw new Error("Missing required option --context.");
  }
  if (!Number.isFinite(options.intervalSec) || options.intervalSec < 1) {
    throw new Error("Invalid --interval, use an integer >= 1.");
  }

  runUp(repoRoot, {
    layout: "quad",
    withBuilderB: options.withBuilderB,
    launchTerminal: false,
  });

  const config = loadConfig(repoRoot);
  const orchestrateDir = getOrchestrateDir(config.logsDir);
  ensureDir(orchestrateDir);

  const statePath = getStatePath(config.logsDir, options.context);
  if (exists(statePath)) {
    const prev = JSON.parse(readTextFile(statePath)) as Partial<OrchestrateState>;
    const alive = (prev.workers ?? []).some((w) => typeof w?.pid === "number" && isProcessRunning(w.pid));
    if (alive && !prev.stoppedAt) {
      throw new Error(`Orchestrate context already running: ${statePath}. Stop it first with orchestrate --stop.`);
    }
  }

  const distCliPath = path.join(repoRoot, "packages", "codex-team", "dist", "cli.js");
  if (!fs.existsSync(distCliPath)) {
    throw new Error(`Missing CLI dist file: ${distCliPath}. Run npm run build in packages/codex-team first.`);
  }

  const roles = getWorkerRoles(options.withBuilderB, options.includeLead);
  const workers: WorkerRecord[] = [];
  const safeContext = sanitizeContext(options.context);

  for (const role of roles) {
    const worktreePath = ensureRoleWorktreePath(repoRoot, role);
    if (!fs.existsSync(worktreePath)) {
      throw new Error(`Missing worktree for role ${role}: ${worktreePath}. Run codex-team up first.`);
    }

    const logPath = path.join(orchestrateDir, `${safeContext}.${role}.log`);
    const fd = fs.openSync(logPath, "a");

    const args = buildAutoArgs(
      distCliPath,
      role,
      options.context,
      options.intervalSec,
      options.model,
      options.fullAuto,
      options.dangerousBypass,
    );

    const child = spawn(process.execPath, args, {
      cwd: repoRoot,
      detached: true,
      windowsHide: true,
      stdio: ["ignore", fd, fd],
    });
    child.unref();
    fs.closeSync(fd);

    workers.push({
      role,
      pid: child.pid ?? -1,
      worktreePath,
      logPath,
    });
  }

  const state: OrchestrateState = {
    context: options.context,
    createdAt: new Date().toISOString(),
    repoRoot,
    workers,
  };
  writeTextFile(statePath, JSON.stringify(state, null, 2) + "\n");

  const leadPath = ensureRoleWorktreePath(repoRoot, "lead");
  console.log(`orchestrate_started: ${options.context}`);
  console.log(`state_file: ${statePath}`);
  for (const worker of workers) {
    console.log(`worker: ${worker.role} pid=${worker.pid} log=${worker.logPath}`);
  }
  console.log(`lead_worktree: ${leadPath}`);
  console.log("Lead command:");
  console.log(`  cd ${leadPath} && codex`);
}

export function runOrchestrateStop(repoRoot: string, context: string): void {
  if (!context || context.trim() === "") {
    throw new Error("Missing required option --context.");
  }

  const config = loadConfig(repoRoot);
  const statePath = getStatePath(config.logsDir, context);
  if (!exists(statePath)) {
    throw new Error(`Orchestrate state not found: ${statePath}`);
  }

  const state = JSON.parse(readTextFile(statePath)) as OrchestrateState;
  const results: string[] = [];

  for (const worker of state.workers) {
    if (!Number.isInteger(worker.pid) || worker.pid <= 0) {
      results.push(`worker:${worker.role} pid=invalid skip`);
      continue;
    }

    if (process.platform === "win32") {
      const kill = run("taskkill", ["/PID", String(worker.pid), "/T", "/F"]);
      if (kill.status === 0) {
        results.push(`worker:${worker.role} pid=${worker.pid} stopped`);
      } else {
        results.push(`worker:${worker.role} pid=${worker.pid} stop_failed`);
      }
    } else {
      try {
        process.kill(worker.pid, "SIGTERM");
        results.push(`worker:${worker.role} pid=${worker.pid} stopped`);
      } catch {
        results.push(`worker:${worker.role} pid=${worker.pid} stop_failed`);
      }
    }
  }

  state.stoppedAt = new Date().toISOString();
  writeTextFile(statePath, JSON.stringify(state, null, 2) + "\n");

  console.log(`orchestrate_stopped: ${context}`);
  for (const item of results) {
    console.log(item);
  }
  console.log(`state_file: ${statePath}`);
}
