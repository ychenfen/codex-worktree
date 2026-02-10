import path from "node:path";
import fs from "node:fs";
import { loadConfig } from "./config.js";
import { Role, MessageType, SendOptions, MESSAGE_TYPES, ROLES } from "../types.js";
import { listMarkdownFiles, readTextFile, writeTextFile } from "../utils/fs.js";
import { messageTimestamp, isoTimestamp } from "../utils/time.js";

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

const SLEEP_ARRAY = new Int32Array(new SharedArrayBuffer(4));

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

function getPendingMessages(busDir: string, me: Role, typeFilter?: string, contextFilter?: string): BusMessage[] {
  const normalizedType = typeFilter ? typeFilter.toUpperCase() : "";

  return listBusMessages(busDir)
    .filter((msg) => msg.to === me)
    .filter((msg) => msg.status !== "DONE")
    .filter((msg) => !msg.file.toLowerCase().includes("_done"))
    .filter((msg) => (normalizedType ? msg.type.toUpperCase() === normalizedType : true))
    .filter((msg) => (contextFilter ? msg.context === contextFilter : true));
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
    console.log(`- ${item.file} | ${item.type} | ${item.action}`);
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
        console.log(`[${ts}] NEW ${msg.file} | ${msg.type} | ${msg.action}`);
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
  const replyTo = parseHeader(originalContent, "Reply-to");

  const msgBase = path.basename(msgPath, ".md");
  const ackName = replyTo && replyTo !== "-" ? replyTo : `${msgBase}_ack.md`;
  const ackPath = path.isAbsolute(ackName) ? ackName : path.join(config.busDir, ackName);

  const ackBody = [
    `From: ${from}`,
    `To: ${originalFrom}`,
    "Type: FYI",
    `Context: ${path.basename(msgPath)}`,
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
