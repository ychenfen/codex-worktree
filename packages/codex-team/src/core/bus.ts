import path from "node:path";
import fs from "node:fs";
import { loadConfig } from "./config.js";
import { Role, MessageType, SendOptions, MESSAGE_TYPES, ROLES } from "../types.js";
import { listMarkdownFiles, readTextFile, writeTextFile } from "../utils/fs.js";
import { messageTimestamp, isoTimestamp } from "../utils/time.js";

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

export function runInbox(repoRoot: string, me: string): void {
  if (!isRole(me)) {
    throw new Error(`Invalid role: ${me}`);
  }

  const config = loadConfig(repoRoot);
  const files = listMarkdownFiles(config.busDir);

  const matched = files
    .filter((file) => !file.toLowerCase().includes("_done"))
    .filter((file) => {
      const content = readTextFile(path.join(config.busDir, file));
      const to = parseHeader(content, "To");
      const status = parseHeader(content, "Status").toUpperCase();
      return to === me && status !== "DONE";
    });

  if (matched.length === 0) {
    console.log(`No pending messages for ${me}.`);
    return;
  }

  console.log(`Pending messages for ${me}:`);
  for (const file of matched) {
    const content = readTextFile(path.join(config.busDir, file));
    const msgType = parseHeader(content, "Type") || "N/A";
    const action = parseHeader(content, "Action") || "";
    console.log(`- ${file} | ${msgType} | ${action}`);
  }
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
