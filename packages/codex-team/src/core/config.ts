import path from "node:path";
import { CodexTeamConfig, WorktreeSpec } from "../types.js";
import { readTextFile, writeTextFile, exists, ensureDir } from "../utils/fs.js";

export const CONFIG_DIR = ".codex-team";
export const CONFIG_FILE = "config.json";

export function getConfigPath(repoRoot: string): string {
  return path.join(repoRoot, CONFIG_DIR, CONFIG_FILE);
}

export function defaultWorktreeSpecs(): WorktreeSpec[] {
  return [
    { role: "lead", name: "wk-lead", branch: "role/lead" },
    { role: "builder-a", name: "wk-builder-a", branch: "role/builder-a" },
    { role: "reviewer", name: "wk-review", branch: "role/review" },
    { role: "tester", name: "wk-test", branch: "role/test" },
    { role: "builder-b", name: "wk-builder-b", branch: "role/builder-b" },
  ];
}

export function saveConfig(repoRoot: string, config: CodexTeamConfig): string {
  const configDir = path.join(repoRoot, CONFIG_DIR);
  ensureDir(configDir);
  const configPath = path.join(configDir, CONFIG_FILE);
  writeTextFile(configPath, JSON.stringify(config, null, 2) + "\n");
  return configPath;
}

export function loadConfig(repoRoot: string): CodexTeamConfig {
  const configPath = getConfigPath(repoRoot);
  if (!exists(configPath)) {
    throw new Error(`Missing config: ${configPath}. Run 'codex-team init' first.`);
  }
  const parsed = JSON.parse(readTextFile(configPath)) as CodexTeamConfig;
  if (!parsed.ctxDir || !parsed.busDir || !parsed.logsDir) {
    throw new Error("Invalid .codex-team/config.json: missing required fields.");
  }
  return parsed;
}
