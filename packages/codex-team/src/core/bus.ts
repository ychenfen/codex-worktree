import path from "node:path";
import fs from "node:fs";
import { loadConfig } from "./config.js";
import { Role, MessageType, SendOptions, MESSAGE_TYPES, ROLES, CodexTeamConfig } from "../types.js";
import { ensureDir, listMarkdownFiles, readTextFile, writeTextFile } from "../utils/fs.js";
import { messageTimestamp, isoTimestamp } from "../utils/time.js";
import { getWorktreePath, roleFileName } from "../utils/paths.js";
import { commandExists, run, runWithInput } from "../utils/shell.js";

interface BusMessage {
  file: string;
  fullPath: string;
  from: string;
  to: string;
  type: string;
  context: string;
  action: string;
  replyTo: string;
  status: string;
  createdAt: string;
}

interface AutoResult {
  summary: string;
  artifacts: string;
}

interface CodexLaunchConfig {
  command: string;
  prefixArgs: string[];
}

export interface AutoOptions {
  me: string;
  intervalSec: number;
  once: boolean;
  typeFilter?: string;
  contextFilter?: string;
  model?: string;
  fullAuto: boolean;
}

const SLEEP_ARRAY = new Int32Array(new SharedArrayBuffer(4));
const AUTO_SCHEMA_NAME = "codex-team-auto-schema.json";

function isRole(value: string): value is Role {
  return (ROLES as readonly string[]).includes(value);
}

function isMessageType(value: string): value is MessageType {
  return (MESSAGE_TYPES as readonly string[]).includes(value);
}

function parseHeader(content: string, key: string): string {
  const line = content
    .split(/\r?\n/)
    .find((item) => item.toLowerCase().startsWith(`${key.toLowerCase()}:`));
  if (!line) {
    return "";
  }
  return line.slice(line.indexOf(":") + 1).trim();
}

function parseBusMessage(busDir: string, file: string): BusMessage {
  const fullPath = path.join(busDir, file);
  const raw = readTextFile(fullPath);
  return {
    file,
    fullPath,
    from: parseHeader(raw, "From") || "",
    to: parseHeader(raw, "To") || "",
    type: parseHeader(raw, "Type") || "",
    context: parseHeader(raw, "Context") || "",
    action: parseHeader(raw, "Action") || "",
    replyTo: parseHeader(raw, "Reply-to") || "",
    status: (parseHeader(raw, "Status") || "NEW").toUpperCase(),
    createdAt: parseHeader(raw, "Created-at") || "",
  };
}

function listBusMessages(busDir: string): BusMessage[] {
  return listMarkdownFiles(busDir)
    .map((file) => parseBusMessage(busDir, file))
    .sort((a, b) => a.file.localeCompare(b.file));
}

function getPendingMessages(
  busDir: string,
  me: Role,
  typeFilter?: string,
  contextFilter?: string,
  mode: "all" | "new-only" = "all",
): BusMessage[] {
  const normalizedType = typeFilter ? typeFilter.toUpperCase() : "";

  return listBusMessages(busDir)
    .filter((msg) => msg.to === me)
    .filter((msg) => msg.status !== "DONE")
    .filter((msg) => !msg.file.toLowerCase().includes("_done"))
    .filter((msg) => (normalizedType ? msg.type.toUpperCase() === normalizedType : true))
    .filter((msg) => (contextFilter ? msg.context === contextFilter : true))
    .filter((msg) => (mode === "new-only" ? msg.status === "NEW" : true));
}

function setMessageStatus(messagePath: string, status: string): void {
  const raw = readTextFile(messagePath);
  const lines = raw.split(/\r?\n/);
  let replaced = false;

  const next = lines.map((line) => {
    if (line.toLowerCase().startsWith("status:")) {
      replaced = true;
      return `Status: ${status}`;
    }
    return line;
  });

  if (!replaced) {
    next.push(`Status: ${status}`);
  }

  const output = next.join("\n").replace(/\n+$/, "\n");
  writeTextFile(messagePath, output);
}

function uniqueMessagePath(busDir: string, baseName: string): string {
  const first = path.join(busDir, baseName);
  if (!fs.existsSync(first)) {
    return first;
  }

  let idx = 1;
  while (idx < 1000) {
    const candidate = path.join(busDir, baseName.replace(/\.md$/i, `-${idx}.md`));
    if (!fs.existsSync(candidate)) {
      return candidate;
    }
    idx += 1;
  }

  throw new Error("Unable to allocate unique bus message filename.");
}

function sleepMs(ms: number): void {
  Atomics.wait(SLEEP_ARRAY, 0, 0, ms);
}

function ensureCodexAvailable(): void {
  if (!commandExists("codex")) {
    throw new Error("codex CLI not found in PATH.");
  }
}

function resolveCodexLaunchConfig(): CodexLaunchConfig {
  if (process.platform !== "win32") {
    return { command: "codex", prefixArgs: [] };
  }

  const lookup = run("where", ["codex"]);
  const candidates = lookup.stdout
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);

  const direct = candidates.find((item) => /\.(exe|cmd|bat)$/i.test(item));
  if (direct) {
    return { command: direct, prefixArgs: [] };
  }

  const psLookup = run("powershell.exe", [
    "-NoProfile",
    "-Command",
    "$cmd = Get-Command codex -ErrorAction SilentlyContinue; if ($cmd) { $cmd.Source }",
  ]);
  const psSource = psLookup.stdout.trim();
  if (psLookup.status === 0 && psSource.toLowerCase().endsWith(".ps1")) {
    return {
      command: "powershell.exe",
      prefixArgs: ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", psSource],
    };
  }

  return { command: "codex", prefixArgs: [] };
}

function ensureAutoSchema(logsDir: string): string {
  ensureDir(logsDir);
  const schemaPath = path.join(logsDir, AUTO_SCHEMA_NAME);
  const schema = {
    type: "object",
    properties: {
      summary: { type: "string" },
      artifacts: { type: "string" },
    },
    required: ["summary", "artifacts"],
    additionalProperties: false,
  };

  writeTextFile(schemaPath, JSON.stringify(schema, null, 2) + "\n");
  return schemaPath;
}

function resolveRoleWorktree(repoRoot: string, config: CodexTeamConfig, me: Role): string {
  const spec = config.worktrees.find((item) => item.role === me);
  if (!spec) {
    throw new Error(`No worktree config found for role: ${me}`);
  }

  const worktreePath = getWorktreePath(repoRoot, spec.name);
  if (!fs.existsSync(worktreePath)) {
    throw new Error(`Worktree path not found for role ${me}: ${worktreePath}. Run 'codex-team up' first.`);
  }

  return worktreePath;
}

function buildAutoPrompt(rolePromptPath: string, ctxDir: string, message: BusMessage): string {
  return [
    `You are running in codex-team auto mode for role: ${message.to}`,
    "",
    "Follow this role contract file first:",
    rolePromptPath,
    "",
    "Shared context directory (outside repo):",
    ctxDir,
    "",
    "Incoming bus message file:",
    message.fullPath,
    "",
    "Message fields:",
    `From: ${message.from}`,
    `To: ${message.to}`,
    `Type: ${message.type}`,
    `Context: ${message.context}`,
    `Action: ${message.action}`,
    `Reply-to: ${message.replyTo || "-"}`,
    "",
    "Do the requested work. If code changes are needed, apply them in the current worktree.",
    `If needed, update shared files under ${ctxDir} (task.md/decision.md/verify.md/journal.md).`,
    "If sandbox permissions are read-only or edits are blocked, do not fail. Return summary with artifacts='-'.",
    "Do NOT call codex-team done yourself. The worker will mark done automatically.",
    "",
    "Return ONLY JSON with this shape:",
    '{"summary":"short completion summary", "artifacts":"comma-separated files or -"}',
  ].join("\n");
}

function parseAutoResult(outputPath: string): AutoResult {
  if (!fs.existsSync(outputPath)) {
    return { summary: "auto processed", artifacts: "-" };
  }

  const raw = readTextFile(outputPath).trim();
  if (!raw) {
    return { summary: "auto processed", artifacts: "-" };
  }

  try {
    const parsed = JSON.parse(raw) as Partial<AutoResult>;
    return {
      summary: typeof parsed.summary === "string" && parsed.summary.trim() ? parsed.summary.trim() : "auto processed",
      artifacts:
        typeof parsed.artifacts === "string" && parsed.artifacts.trim() ? parsed.artifacts.trim() : "-",
    };
  } catch {
    const summary = raw.split(/\r?\n/)[0]?.trim() || "auto processed";
    return { summary: summary.slice(0, 180), artifacts: "-" };
  }
}

function processOneAutoMessage(
  repoRoot: string,
  config: CodexTeamConfig,
  me: Role,
  message: BusMessage,
  options: AutoOptions,
): void {
  setMessageStatus(message.fullPath, "IN_PROGRESS");

  const worktreePath = resolveRoleWorktree(repoRoot, config, me);
  const rolePromptPath = path.join(repoRoot, "roles", roleFileName(me));
  if (!fs.existsSync(rolePromptPath)) {
    throw new Error(`Missing role prompt file: ${rolePromptPath}. Run 'codex-team init' first.`);
  }

  const schemaPath = ensureAutoSchema(config.logsDir);
  const autoLogDir = path.join(config.logsDir, "auto-runs");
  ensureDir(autoLogDir);

  const outputName = `${messageTimestamp()}_${me}_${message.file.replace(/[^a-zA-Z0-9._-]/g, "_")}.json`;
  const outputPath = path.join(autoLogDir, outputName);

  const prompt = buildAutoPrompt(rolePromptPath, config.ctxDir, message);
  const codexLaunch = resolveCodexLaunchConfig();

  const args: string[] = [
    ...codexLaunch.prefixArgs,
    "exec",
    "-C",
    worktreePath,
    "--add-dir",
    config.ctxDir,
    "--output-schema",
    schemaPath,
    "--output-last-message",
    outputPath,
  ];

  if (options.model) {
    args.push("--model", options.model);
  }
  if (options.fullAuto) {
    args.push("--full-auto");
  }
  args.push("-");

  const result = runWithInput(codexLaunch.command, args, prompt, worktreePath);
  if (result.status !== 0) {
    setMessageStatus(message.fullPath, "FAILED");
    const errLogPath = path.join(autoLogDir, `${outputName}.error.log`);
    writeTextFile(
      errLogPath,
      [
        `message: ${message.file}`,
        `status: ${result.status}`,
        "--- stderr ---",
        result.stderr || "",
        "--- stdout ---",
        result.stdout || "",
      ].join("\n"),
    );
    throw new Error(`codex exec failed for ${message.file}. See: ${errLogPath}`);
  }

  const autoResult = parseAutoResult(outputPath);
  runDone(repoRoot, message.file, autoResult.summary, autoResult.artifacts, `${me}-auto`);
}

export function runSend(repoRoot: string, options: SendOptions): void {
  if (!isRole(options.to)) {
    throw new Error(`Invalid role: ${options.to}`);
  }

  if (!isMessageType(options.type)) {
    throw new Error(`Invalid message type: ${options.type}`);
  }

  const config = loadConfig(repoRoot);
  const ts = messageTimestamp();
  const fileName = `${ts}_to_${options.to}_${options.type}.md`;
  const targetPath = uniqueMessagePath(config.busDir, fileName);

  const body = [
    `From: ${options.from}`,
    `To: ${options.to}`,
    `Type: ${options.type}`,
    `Context: ${options.context}`,
    `Action: ${options.action}`,
    `Reply-to: ${options.replyTo}`,
    `Created-at: ${isoTimestamp()}`,
    "Status: NEW",
    "",
  ].join("\n");

  writeTextFile(targetPath, body);
  console.log(`message_written: ${targetPath}`);
}

export function runBroadcast(
  repoRoot: string,
  toCsv: string,
  type: string,
  action: string,
  context: string,
  replyTo: string,
  from: string,
): void {
  const targetNames = Array.from(new Set(toCsv.split(",").map((item) => item.trim()).filter(Boolean)));

  if (targetNames.length === 0) {
    throw new Error("No target roles provided for broadcast.");
  }

  const targets: Role[] = [];
  for (const roleName of targetNames) {
    if (!isRole(roleName)) {
      throw new Error(`Invalid role in --to: ${roleName}`);
    }
    targets.push(roleName);
  }

  const normalizedType = type.toUpperCase();
  if (!isMessageType(normalizedType)) {
    throw new Error(`Invalid message type: ${type}`);
  }

  for (const role of targets) {
    runSend(repoRoot, {
      to: role,
      type: normalizedType,
      action,
      context,
      replyTo,
      from,
    });
  }

  console.log(`broadcast_sent: ${targets.join(",")}`);
}

export function runInbox(repoRoot: string, me: string): void {
  if (!isRole(me)) {
    throw new Error(`Invalid role: ${me}`);
  }

  const config = loadConfig(repoRoot);
  const matched = getPendingMessages(config.busDir, me);

  if (matched.length === 0) {
    console.log(`No pending messages for ${me}.`);
    return;
  }

  console.log(`Pending messages for ${me}:`);
  for (const item of matched) {
    console.log(`- ${item.file} | ${item.status} | ${item.type} | ${item.action}`);
  }
}

export function runWatch(
  repoRoot: string,
  me: string,
  intervalSec: number,
  typeFilter?: string,
  contextFilter?: string,
): void {
  if (!isRole(me)) {
    throw new Error(`Invalid role: ${me}`);
  }
  if (!Number.isFinite(intervalSec) || intervalSec < 1) {
    throw new Error("Invalid --interval, use an integer >= 1.");
  }

  const config = loadConfig(repoRoot);
  const intervalMs = Math.floor(intervalSec * 1000);
  let seen = new Set<string>();

  console.log(`Watching bus inbox for role: ${me} (interval: ${intervalSec}s)`);
  console.log(`bus_dir: ${config.busDir}`);
  if (typeFilter) {
    console.log(`filter_type: ${typeFilter.toUpperCase()}`);
  }
  if (contextFilter) {
    console.log(`filter_context: ${contextFilter}`);
  }
  console.log("Press Ctrl+C to stop.");

  while (true) {
    const current = getPendingMessages(config.busDir, me, typeFilter, contextFilter);
    const nextSeen = new Set(current.map((msg) => msg.file));

    for (const msg of current) {
      if (!seen.has(msg.file)) {
        const ts = new Date().toISOString();
        console.log(`[${ts}] NEW ${msg.file} | ${msg.status} | ${msg.type} | ${msg.action}`);
      }
    }

    seen = nextSeen;
    sleepMs(intervalMs);
  }
}

export function runThread(repoRoot: string, context: string): void {
  if (!context || context === "-") {
    throw new Error("Missing required option --context for thread view.");
  }

  const config = loadConfig(repoRoot);
  const messages = listBusMessages(config.busDir).filter((msg) => msg.context === context);

  if (messages.length === 0) {
    console.log(`No messages found for context: ${context}`);
    return;
  }

  console.log(`Thread: ${context}`);
  for (const msg of messages) {
    console.log(
      `- ${msg.file} | ${msg.status} | ${msg.from} -> ${msg.to} | ${msg.type} | ${msg.action} | reply:${msg.replyTo || "-"}`,
    );
  }
}

function resolveDoneMessage(
  repoRoot: string,
  me: string,
  order: "latest" | "oldest",
  typeFilter?: string,
  contextFilter?: string,
): string {
  if (!isRole(me)) {
    throw new Error(`Invalid role: ${me}`);
  }

  const config = loadConfig(repoRoot);
  const pending = getPendingMessages(config.busDir, me, typeFilter, contextFilter);
  if (pending.length === 0) {
    throw new Error(`No pending messages for ${me}.`);
  }

  const selected = order === "latest" ? pending[pending.length - 1] : pending[0];
  return selected.file;
}

export function runDoneByOrder(
  repoRoot: string,
  me: string,
  order: "latest" | "oldest",
  summary: string,
  artifacts: string,
  from: string,
  typeFilter?: string,
  contextFilter?: string,
): void {
  const msg = resolveDoneMessage(repoRoot, me, order, typeFilter, contextFilter);
  runDone(repoRoot, msg, summary, artifacts, from);
}

export function runDone(repoRoot: string, msg: string, summary: string, artifacts: string, from: string): void {
  const config = loadConfig(repoRoot);

  const msgPath = path.isAbsolute(msg) ? msg : path.join(config.busDir, msg);
  if (!fs.existsSync(msgPath)) {
    throw new Error(`Message file not found: ${msgPath}`);
  }

  const originalContent = readTextFile(msgPath);
  const originalFrom = parseHeader(originalContent, "From") || "unknown";
  const originalContext = parseHeader(originalContent, "Context") || "-";
  const replyTo = parseHeader(originalContent, "Reply-to");

  const msgBase = path.basename(msgPath, ".md");
  const ackName = replyTo && replyTo !== "-" ? replyTo : `${msgBase}_ack.md`;
  const ackPath = path.isAbsolute(ackName) ? ackName : path.join(config.busDir, ackName);

  const ackBody = [
    `From: ${from}`,
    `To: ${originalFrom}`,
    "Type: FYI",
    `Context: ${originalContext}`,
    `Action: ${summary}`,
    "Reply-to: -",
    `Artifacts: ${artifacts}`,
    `Created-at: ${isoTimestamp()}`,
    "Status: DONE",
    "",
  ].join("\n");

  writeTextFile(ackPath, ackBody);

  let donePath = msgPath;
  if (!msgBase.endsWith("_done")) {
    donePath = path.join(path.dirname(msgPath), `${msgBase}_done.md`);
    fs.renameSync(msgPath, donePath);
  }

  const marked = readTextFile(donePath)
    .split(/\r?\n/)
    .map((line) => (line.toLowerCase().startsWith("status:") ? "Status: DONE" : line))
    .join("\n");

  writeTextFile(donePath, marked + (marked.endsWith("\n") ? "" : "\n"));

  console.log(`ack_written: ${ackPath}`);
  console.log(`message_done: ${donePath}`);
}

export function runAuto(repoRoot: string, options: AutoOptions): void {
  if (!isRole(options.me)) {
    throw new Error(`Invalid role: ${options.me}`);
  }
  if (!Number.isFinite(options.intervalSec) || options.intervalSec < 1) {
    throw new Error("Invalid --interval, use an integer >= 1.");
  }

  ensureCodexAvailable();

  const me = options.me;
  const config = loadConfig(repoRoot);

  const processAvailable = (): number => {
    const messages = getPendingMessages(
      config.busDir,
      me,
      options.typeFilter,
      options.contextFilter,
      "new-only",
    );

    if (messages.length === 0) {
      return 0;
    }

    let processed = 0;
    for (const message of messages) {
      console.log(`auto_processing: ${message.file}`);
      try {
        processOneAutoMessage(repoRoot, config, me, message, options);
        processed += 1;
      } catch (error) {
        console.error((error as Error).message);
      }

      if (options.once) {
        break;
      }
    }

    return processed;
  };

  if (options.once) {
    const processed = processAvailable();
    if (processed === 0) {
      console.log(`No NEW pending messages for ${me}.`);
    }
    return;
  }

  const intervalMs = Math.floor(options.intervalSec * 1000);
  console.log(`Auto worker started for role: ${me}`);
  console.log(`bus_dir: ${config.busDir}`);
  console.log(`interval: ${options.intervalSec}s`);
  if (options.typeFilter) {
    console.log(`filter_type: ${options.typeFilter.toUpperCase()}`);
  }
  if (options.contextFilter) {
    console.log(`filter_context: ${options.contextFilter}`);
  }
  console.log(`full_auto: ${options.fullAuto ? "true" : "false"}`);
  console.log("Press Ctrl+C to stop.");

  while (true) {
    processAvailable();
    sleepMs(intervalMs);
  }
}
