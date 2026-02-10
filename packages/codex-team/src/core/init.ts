import path from "node:path";
import os from "node:os";
import { defaultWorktreeSpecs, saveConfig } from "./config.js";
import { writeRolePrompts, writeRepoTemplates } from "./templates.js";
import { ensureDir, ensureTextFile } from "../utils/fs.js";
import { getDefaultCtxDir } from "../utils/paths.js";
import { isoTimestamp } from "../utils/time.js";
import { CodexTeamConfig } from "../types.js";

export interface InitOptions {
  repoRoot: string;
  ctxDir?: string;
}

function defaultSharedFile(name: string): string {
  const title = name.replace(/\.md$/, "");
  return `# ${title}\n\n`;
}

export function runInit(options: InitOptions): void {
  const repoRoot = options.repoRoot;
  const ctxDir = path.resolve(options.ctxDir ?? getDefaultCtxDir());
  const busDir = path.join(ctxDir, "bus");
  const logsDir = path.join(ctxDir, "logs");

  ensureDir(ctxDir);
  ensureDir(busDir);
  ensureDir(logsDir);

  for (const file of ["task.md", "decision.md", "verify.md", "pitfalls.md", "journal.md"]) {
    ensureTextFile(path.join(ctxDir, file), defaultSharedFile(file));
  }

  const roleFiles = writeRolePrompts(repoRoot, ctxDir);
  const templateFiles = writeRepoTemplates(repoRoot);

  const config: CodexTeamConfig = {
    version: 1,
    createdAt: isoTimestamp(),
    repoRoot,
    ctxDir,
    busDir,
    logsDir,
    defaultLayout: "quad",
    defaultModel: "codex",
    worktrees: defaultWorktreeSpecs(),
  };

  const configPath = saveConfig(repoRoot, config);

  console.log("codex-team init completed");
  console.log(`repo_root: ${repoRoot}`);
  console.log(`ctx_dir: ${ctxDir}`);
  console.log(`bus_dir: ${busDir}`);
  console.log(`logs_dir: ${logsDir}`);
  console.log(`config: ${configPath}`);
  console.log(`roles: ${roleFiles.length} files`);
  console.log(`templates: ${templateFiles.length} files`);
  console.log(`user: ${os.userInfo().username}`);
}
