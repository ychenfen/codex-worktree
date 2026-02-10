#!/usr/bin/env node
import os from "node:os";
import { runInit } from "./core/init.js";
import { runUp } from "./core/up.js";
import { runSend, runInbox, runDone, runWatch, runBroadcast, runThread, runDoneByOrder, runAuto } from "./core/bus.js";
import { runOrchestrateStart, runOrchestrateStop } from "./core/orchestrate.js";
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

function getOptionalStringFlag(parsed: ParsedArgs, key: string): string | undefined {
  const value = parsed.flags[key];
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : undefined;
}

function printHelp(): void {
  console.log(`codex-team <command> [options]\n\nCommands:\n  init [--ctx-dir <path>]\n  up [--layout quad] [--with-builder-b]\n  send --to <role> --type <TASK|REVIEW|VERIFY|BLOCKER|FYI|PROPOSE|COMPARE> --action <text> [--context <text>] [--reply-to <filename>] [--from <name>]\n  broadcast --to <role1,role2,...> --type <TASK|REVIEW|VERIFY|BLOCKER|FYI|PROPOSE|COMPARE> --action <text> [--context <text>] [--reply-to <filename>] [--from <name>]\n  inbox --me <role>\n  watch --me <role> [--interval <seconds>] [--type <TYPE>] [--context <id>]\n  thread --context <id>\n  done --msg <filename> --summary <text> [--artifacts <text>] [--from <name>]\n  done --latest|--oldest --me <role> --summary <text> [--type <TYPE>] [--context <id>] [--artifacts <text>] [--from <name>]\n  auto --me <role> [--interval <seconds>] [--once] [--type <TYPE>] [--context <id>] [--model <name>] [--no-full-auto]\n  orchestrate --context <id> [--with-builder-b] [--interval <seconds>] [--model <name>] [--no-full-auto] [--lead]\n  orchestrate --stop --context <id>\n`);
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
      const ctxDir = getOptionalStringFlag(parsed, "ctx-dir");
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
      const context = getOptionalStringFlag(parsed, "context") ?? "-";
      const replyTo = getOptionalStringFlag(parsed, "reply-to") ?? "-";
      const from = detectFrom(parsed);
      runSend(repoRoot, { to, type, action, context, replyTo, from });
      break;
    }
    case "broadcast": {
      const toCsv = requireStringFlag(parsed, "to");
      const type = requireStringFlag(parsed, "type").toUpperCase();
      const action = requireStringFlag(parsed, "action");
      const context = getOptionalStringFlag(parsed, "context") ?? "-";
      const replyTo = getOptionalStringFlag(parsed, "reply-to") ?? "-";
      const from = detectFrom(parsed);
      runBroadcast(repoRoot, toCsv, type, action, context, replyTo, from);
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
      const typeFilter = getOptionalStringFlag(parsed, "type")?.toUpperCase();
      const contextFilter = getOptionalStringFlag(parsed, "context");
      runWatch(repoRoot, me, interval, typeFilter, contextFilter);
      break;
    }
    case "thread": {
      const context = requireStringFlag(parsed, "context");
      runThread(repoRoot, context);
      break;
    }
    case "done": {
      const summary = requireStringFlag(parsed, "summary");
      const artifacts = getOptionalStringFlag(parsed, "artifacts") ?? "-";
      const from = detectFrom(parsed);

      const directMsg = getOptionalStringFlag(parsed, "msg");
      if (directMsg) {
        runDone(repoRoot, directMsg, summary, artifacts, from);
        break;
      }

      const latest = Boolean(parsed.flags.latest);
      const oldest = Boolean(parsed.flags.oldest);
      if (latest && oldest) {
        throw new Error("Use only one of --latest or --oldest.");
      }
      if (!latest && !oldest) {
        throw new Error("Missing required option --msg, or use --latest/--oldest with --me.");
      }

      const me = requireStringFlag(parsed, "me");
      const typeFilter = getOptionalStringFlag(parsed, "type")?.toUpperCase();
      const contextFilter = getOptionalStringFlag(parsed, "context");
      const order = latest ? "latest" : "oldest";
      runDoneByOrder(repoRoot, me, order, summary, artifacts, from, typeFilter, contextFilter);
      break;
    }
    case "auto": {
      const me = requireStringFlag(parsed, "me");
      const intervalRaw = parsed.flags.interval;
      const interval = typeof intervalRaw === "string" ? Number(intervalRaw) : 5;
      if (!Number.isFinite(interval) || interval < 1) {
        throw new Error("Invalid --interval, use an integer >= 1.");
      }

      const once = Boolean(parsed.flags.once);
      const typeFilter = getOptionalStringFlag(parsed, "type")?.toUpperCase();
      const contextFilter = getOptionalStringFlag(parsed, "context");
      const model = getOptionalStringFlag(parsed, "model");
      const noFullAuto = Boolean(parsed.flags["no-full-auto"]);

      runAuto(repoRoot, {
        me,
        intervalSec: interval,
        once,
        typeFilter,
        contextFilter,
        model,
        fullAuto: !noFullAuto,
      });
      break;
    }
    case "orchestrate": {
      const context = requireStringFlag(parsed, "context");
      const stop = Boolean(parsed.flags.stop);
      if (stop) {
        runOrchestrateStop(repoRoot, context);
        break;
      }

      const withBuilderB = Boolean(parsed.flags["with-builder-b"]);
      const intervalRaw = parsed.flags.interval;
      const interval = typeof intervalRaw === "string" ? Number(intervalRaw) : 5;
      if (!Number.isFinite(interval) || interval < 1) {
        throw new Error("Invalid --interval, use an integer >= 1.");
      }

      const model = getOptionalStringFlag(parsed, "model");
      const noFullAuto = Boolean(parsed.flags["no-full-auto"]);
      const lead = Boolean(parsed.flags.lead);

      runOrchestrateStart(repoRoot, {
        context,
        withBuilderB,
        intervalSec: interval,
        model,
        fullAuto: !noFullAuto,
        includeLead: lead,
      });
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
