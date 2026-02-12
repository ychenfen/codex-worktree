#!/usr/bin/env python3
import argparse
import os
import re
import select
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


ROLE_ORDER = ["lead", "builder-a", "builder-b", "reviewer", "tester"]


@dataclass
class SessionPaths:
    main_worktree: Path
    session_root: Path
    bus_inbox: Path
    bus_outbox: Path
    shared: Path
    artifacts: Path


def _run(cmd: List[str], cwd: Optional[Path] = None) -> str:
    import subprocess

    p = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if p.returncode != 0:
        raise RuntimeError(f"command failed ({p.returncode}): {' '.join(cmd)}\n{p.stderr.strip()}")
    return p.stdout


def git_main_worktree(start_dir: Path) -> Path:
    top = _run(["git", "-C", str(start_dir), "rev-parse", "--show-toplevel"]).strip()
    lines = _run(["git", "-C", top, "worktree", "list", "--porcelain"]).splitlines()
    for line in lines:
        if line.startswith("worktree "):
            return Path(line.split(" ", 1)[1]).resolve()
    return Path(top).resolve()


def session_paths(main_worktree: Path, session: str) -> SessionPaths:
    root = (main_worktree / "sessions" / session).resolve()
    return SessionPaths(
        main_worktree=main_worktree,
        session_root=root,
        bus_inbox=root / "bus" / "inbox",
        bus_outbox=root / "bus" / "outbox",
        shared=root / "shared",
        artifacts=root / "artifacts",
    )


def mkdirp(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def atomic_write(path: Path, text: str) -> None:
    mkdirp(path.parent)
    tmp = path.parent / f".tmp.{path.name}.{os.getpid()}"
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def parse_frontmatter(md: str) -> Tuple[Dict[str, object], str]:
    lines = md.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        return {}, md
    fm: Dict[str, object] = {}
    i = 1
    current_key = None
    while i < len(lines):
        line = lines[i]
        if line.strip() == "---":
            body = "\n".join(lines[i + 1 :]).lstrip("\n")
            return fm, body
        if line.startswith("  - ") and current_key:
            val = line[4:].strip()
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            if isinstance(fm.get(current_key), list):
                fm[current_key].append(val)
            else:
                fm[current_key] = [val]
            i += 1
            continue
        m = re.match(r"^([A-Za-z0-9_\-]+):\s*(.*)$", line)
        if m:
            k = m.group(1)
            v = m.group(2).strip()
            current_key = k
            if v.startswith('"') and v.endswith('"'):
                v = v[1:-1]
            fm[k] = v
        i += 1
    return {}, md


def list_roles(sp: SessionPaths) -> List[str]:
    roles_dir = sp.session_root / "roles"
    roles: List[str] = []
    if roles_dir.is_dir():
        for d in roles_dir.iterdir():
            if d.is_dir():
                roles.append(d.name)
    return [r for r in ROLE_ORDER if r in roles]


def new_id(prefix: str = "") -> str:
    import secrets

    ts = time.strftime("%Y%m%d-%H%M%S")
    return f"{prefix}{ts}-{secrets.token_hex(3)}"


def enqueue_message(
    sp: SessionPaths,
    *,
    to_role: str,
    from_role: str,
    intent: str,
    thread: str,
    risk: str,
    body: str,
    mid: Optional[str] = None,
) -> Path:
    mid = mid or new_id("")
    out = sp.bus_inbox / to_role / f"{mid}.md"
    text = "\n".join(
        [
            "---",
            f"id: {mid}",
            f"from: {from_role}",
            f"to: {to_role}",
            f"intent: {intent}",
            f"thread: {thread}",
            f"risk: {risk}",
            "---",
            "",
            body.rstrip(),
            "",
        ]
    )
    atomic_write(out, text)
    return out


def write_task(sp: SessionPaths, task_text: str, acceptance_lines: List[str]) -> None:
    lines = ["# Task", task_text.strip(), "", "## Acceptance"]
    if acceptance_lines:
        for a in acceptance_lines:
            a = a.strip()
            if not a:
                continue
            if a.startswith("- "):
                lines.append(a)
            else:
                lines.append(f"- {a}")
    else:
        lines.append("- (fill)")
    lines.append("")
    atomic_write(sp.shared / "task.md", "\n".join(lines))


def format_receipt(receipt_path: Path) -> str:
    raw = read_text(receipt_path)
    fm, body = parse_frontmatter(raw)
    rid = str(fm.get("id", receipt_path.stem)).strip() or receipt_path.stem
    role = str(fm.get("role", "")).strip()
    status = str(fm.get("status", "")).strip()
    rc = str(fm.get("codex_rc", "")).strip()
    finished = str(fm.get("finished_at", "")).strip()

    head = f"[receipt] role={role or '?'} status={status or '?'} rc={rc or '?'} id={rid}"
    if finished:
        head = f"{head} at {finished}"
    snippet = "\n".join([ln for ln in body.strip().splitlines()[:6] if ln.strip()][:6])
    if snippet:
        return head + "\n" + snippet
    return head


def repl(session: str) -> int:
    main = git_main_worktree(Path.cwd())
    sp = session_paths(main, session)
    if not sp.session_root.is_dir():
        print(f"Session not found: {sp.session_root}", file=sys.stderr)
        return 2

    roles = list_roles(sp)
    mkdirp(sp.bus_outbox)
    for r in roles:
        mkdirp(sp.bus_inbox / r)

    print(f"Team CLI attached: {session}")
    print('Commands: /help, /task <text>, /accept <line>, /bootstrap, /send <to> <intent> <msg>, /outbox, /status, /exit')
    print('Default: plain text is sent to lead as a chat message (Claude Code-like). Use /task + /bootstrap for actionable work.')

    accept_buf: List[str] = []
    seen: Dict[str, float] = {}

    # Default behavior: show only NEW receipts after attaching (Claude Code-like).
    # Historical receipts can still be viewed with /outbox.
    if sp.bus_outbox.is_dir():
        for p in sp.bus_outbox.glob("*.md"):
            try:
                seen[str(p)] = float(p.stat().st_mtime)
            except Exception:
                pass

    def poll_outbox() -> None:
        if not sp.bus_outbox.is_dir():
            return
        for p in sorted(sp.bus_outbox.glob("*.md"), key=lambda x: x.stat().st_mtime):
            try:
                mt = float(p.stat().st_mtime)
            except Exception:
                continue
            key = str(p)
            if seen.get(key, 0.0) >= mt:
                continue
            seen[key] = mt
            print("")
            print(format_receipt(p))
            print("")

    while True:
        poll_outbox()

        sys.stdout.write("> ")
        sys.stdout.flush()
        r, _, _ = select.select([sys.stdin], [], [], 1.0)
        if not r:
            # No input; continue polling outbox.
            sys.stdout.write("\r")
            sys.stdout.flush()
            continue

        line = sys.stdin.readline()
        if not line:
            return 0
        line = line.rstrip("\n")
        if not line.strip():
            continue

        if line.startswith("/help"):
            print("  /task <text>           write shared/task.md (keeps /accept buffer)")
            print("  /accept <line>         add an acceptance line (repeatable)")
            print("  /bootstrap             send bootstrap to lead (read shared/task.md and dispatch)")
            print("  /send <to> <intent> <msg>  send a bus message")
            print("  /outbox                show last 5 receipts")
            print("  /status                show basic session paths")
            print("  /exit                  quit (does not stop daemons)")
            continue

        if line.startswith("/accept "):
            accept_buf.append(line[len("/accept ") :].strip())
            print(f"acceptance buffered: {len(accept_buf)}")
            continue

        if line.startswith("/task "):
            t = line[len("/task ") :].strip()
            write_task(sp, t, accept_buf)
            print(f"wrote: {sp.shared / 'task.md'}")
            continue

        if line.startswith("/bootstrap"):
            enqueue_message(
                sp,
                to_role="lead",
                from_role="user",
                intent="bootstrap",
                thread=session,
                risk="low",
                body="Please read shared/task.md and dispatch to roles via bus (team-mode).",
            )
            print("enqueued bootstrap -> lead")
            continue

        if line.startswith("/send "):
            parts = line.split(" ", 3)
            if len(parts) < 4:
                print("usage: /send <to> <intent> <msg>")
                continue
            _, to_role, intent, msg = parts
            if to_role not in roles:
                print(f"invalid role: {to_role}")
                continue
            enqueue_message(
                sp,
                to_role=to_role,
                from_role="user",
                intent=intent,
                thread=session,
                risk="low",
                body=msg,
            )
            print(f"enqueued -> {to_role}")
            continue

        if line.startswith("/outbox"):
            if not sp.bus_outbox.is_dir():
                print("(no outbox dir)")
                continue
            files = sorted(sp.bus_outbox.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]
            for p in files:
                print(format_receipt(p))
                print("")
            continue

        if line.startswith("/status"):
            print(f"session_root: {sp.session_root}")
            print(f"outbox: {sp.bus_outbox}")
            print(f"inbox: {sp.bus_inbox}")
            print(f"shared: {sp.shared}")
            continue

        if line.startswith("/exit"):
            return 0

        # Default: chat to lead (do not overwrite shared/task.md unexpectedly).
        enqueue_message(
            sp,
            to_role="lead",
            from_role="user",
            intent="question",
            thread=session,
            risk="low",
            body=line.strip(),
        )
        print("sent -> lead")


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("repl", help="Interactive team terminal (Claude Code-like).")
    r.add_argument("--session", required=True)

    args = ap.parse_args()
    if args.cmd == "repl":
        return repl(session=args.session)
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
