#!/usr/bin/env node
import os from "node:os";
import { runInit } from "./core/init.js";
import { runUp } from "./core/up.js";
import { runSend, runInbox, runDone, runWatch } from "./core/bus.js";
import { findRepoRoot } from "./utils/paths.js";

interface ParsedArgs {
  _: string[];
  flags: Record<string, string | boolean>;
}

function parseArgs(argv: string[]): ParsedArgs {
  const parsed: ParsedArgs = { _: [], flags: {} };

  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (token.startsWith("--")) {
      const key = token.slice(2);
      const next = argv[i + 1];
      if (next && !next.startsWith("--")) {
        parsed.flags[key] = next;
        i += 1;
      } else {
        parsed.flags[key] = true;
      }
    } else {
      parsed._.push(token);
    }
  }

  return parsed;
}

function requireStringFlag(parsed: ParsedArgs, key: string): string {
  const value = parsed.flags[key];
  if (typeof value !== "string" || value.trim().length === 0) {
    throw new Error(`Missing required option --${key}`);
  }
  return value;
}

function printHelp(): void {
  console.log(`codex-team <command> [options]\n\nCommands:\n  init [--ctx-dir <path>]\n  up [--layout quad] [--with-builder-b]\n  send --to <role> --type <TASK|REVIEW|VERIFY|BLOCKER|FYI> --action <text> [--context <text>] [--reply-to <filename>] [--from <name>]\n  inbox --me <role>\n  watch --me <role> [--interval <seconds>]\n  done --msg <filename> --summary <text> [--artifacts <text>] [--from <name>]\n`);
}

function normalizeLayout(value: string | boolean | undefined): "quad" {
  if (!value || value === true) {
    return "quad";
  }
  if (value !== "quad") {
    throw new Error("Only --layout quad is supported.");
  }
  return "quad";
}

function detectFrom(parsed: ParsedArgs): string {
  const from = parsed.flags.from;
  if (typeof from === "string" && from.trim().length > 0) {
    return from.trim();
  }
  return os.userInfo().username;
}

function main(): void {
  const argv = process.argv.slice(2);
  if (argv.length === 0 || argv[0] === "-h" || argv[0] === "--help") {
    printHelp();
    return;
  }

  const command = argv[0];
  const parsed = parseArgs(argv.slice(1));
  const repoRoot = findRepoRoot(process.cwd());

  switch (command) {
    case "init": {
      const ctxDir = typeof parsed.flags["ctx-dir"] === "string" ? (parsed.flags["ctx-dir"] as string) : undefined;
      runInit({ repoRoot, ctxDir });
      break;
    }
    case "up": {
      const layout = normalizeLayout(parsed.flags.layout);
      const withBuilderB = Boolean(parsed.flags["with-builder-b"]);
      runUp(repoRoot, { layout, withBuilderB });
      break;
    }
    case "send": {
      const to = requireStringFlag(parsed, "to") as any;
      const type = requireStringFlag(parsed, "type").toUpperCase() as any;
      const action = requireStringFlag(parsed, "action");
      const context = typeof parsed.flags.context === "string" ? (parsed.flags.context as string) : "-";
      const replyTo = typeof parsed.flags["reply-to"] === "string" ? (parsed.flags["reply-to"] as string) : "-";
      const from = detectFrom(parsed);
      runSend(repoRoot, { to, type, action, context, replyTo, from });
      break;
    }
    case "inbox": {
      const me = requireStringFlag(parsed, "me");
      runInbox(repoRoot, me);
      break;
    }
    case "watch": {
      const me = requireStringFlag(parsed, "me");
      const intervalRaw = parsed.flags.interval;
      const interval = typeof intervalRaw === "string" ? Number(intervalRaw) : 5;
      if (!Number.isFinite(interval) || interval < 1) {
        throw new Error("Invalid --interval, use an integer >= 1.");
      }
      runWatch(repoRoot, me, interval);
      break;
    }
    case "done": {
      const msg = requireStringFlag(parsed, "msg");
      const summary = requireStringFlag(parsed, "summary");
      const artifacts = typeof parsed.flags.artifacts === "string" ? (parsed.flags.artifacts as string) : "-";
      const from = detectFrom(parsed);
      runDone(repoRoot, msg, summary, artifacts, from);
      break;
    }
    default:
      throw new Error(`Unknown command: ${command}`);
  }
}

try {
  main();
} catch (error) {
  console.error((error as Error).message);
  process.exit(1);
}
