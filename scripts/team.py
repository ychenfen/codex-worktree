#!/usr/bin/env python3
import argparse
import os
import re
import select
import shlex
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from task_board import add_task, ensure_task_board, format_task_brief, list_tasks, set_dispatch


ROLE_ORDER = ["lead", "builder-a", "builder-b", "reviewer", "tester"]


@dataclass
class SessionPaths:
    main_worktree: Path
    session_root: Path
    bus_inbox: Path
    bus_outbox: Path
    shared: Path
    artifacts: Path


def _run(cmd: List[str], cwd: Optional[Path] = None, timeout_s: Optional[float] = None) -> str:
    import subprocess

    p = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    if p.returncode != 0:
        raise RuntimeError(f"command failed ({p.returncode}): {' '.join(cmd)}\n{p.stderr.strip()}")
    return p.stdout


def git_main_worktree(start_dir: Path) -> Path:
    top = _run(["git", "-C", str(start_dir), "rev-parse", "--show-toplevel"]).strip()
    common = _run(["git", "-C", top, "rev-parse", "--git-common-dir"]).strip()
    common_path = Path(common)
    if not common_path.is_absolute():
        common_path = (Path(top) / common_path).resolve()
    if common_path.name == ".git":
        return common_path.parent.resolve()
    if common_path.parent.name == "worktrees" and common_path.parent.parent.name == ".git":
        return common_path.parent.parent.parent.resolve()
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
    task_id: str = "",
) -> Path:
    mid = mid or new_id("")
    out = sp.bus_inbox / to_role / f"{mid}.md"
    header = [
        "---",
        f"id: {mid}",
        f"from: {from_role}",
        f"to: {to_role}",
        f"intent: {intent}",
        f"thread: {thread}",
        f"risk: {risk}",
    ]
    if task_id.strip():
        header.append(f"task_id: {task_id.strip()}")
    header.extend(
        [
            "---",
            "",
            body.rstrip(),
            "",
        ]
    )
    text = "\n".join(header)
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


def _is_safe_shell_command(cmd: str) -> bool:
    # Allow an override for power users.
    if (os.environ.get("TEAM_SH_UNSAFE") or "").strip() in ("1", "true", "yes"):
        return True

    tokens: List[str] = []
    try:
        tokens = shlex.split(cmd)
    except Exception:
        return False

    # Skip leading VAR=... assignments.
    i = 0
    while i < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[i]):
        i += 1
    if i >= len(tokens):
        return True

    head = tokens[i]
    allowed = {
        "ls",
        "tail",
        "head",
        "cat",
        "find",
        "rg",
        "grep",
        "sed",
        "awk",
        "git",
        "pwd",
        "echo",
        "stat",
        "python3",
        "bash",
        "zsh",
    }
    if head in allowed:
        return True
    if head.startswith("./scripts/"):
        return True
    return False


def run_shell(cmd: str, cwd: Path) -> Tuple[int, str]:
    import subprocess

    p = subprocess.run(
        ["bash", "-lc", cmd],
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return p.returncode, p.stdout


def repl(session: str) -> int:
    main = git_main_worktree(Path.cwd())
    sp = session_paths(main, session)
    if not sp.session_root.is_dir():
        print(f"Session not found: {sp.session_root}", file=sys.stderr)
        return 2

    roles = list_roles(sp)
    mkdirp(sp.bus_outbox)
    ensure_task_board(sp.session_root)
    for r in roles:
        mkdirp(sp.bus_inbox / r)

    print(f"Team CLI attached: {session}")
    print(
        "Commands: /help, /task <text>, /accept <line>, /bootstrap, /tasks [status], /tadd <role> <title>, "
        "/to <role>, /send <to> <intent> <msg> [--task <id>], /outbox, /status, /sh <cmd>, /exit"
    )
    print(
        'Default: plain text is sent to lead as a chat message (Claude Code-like). '
        'Use /to <role> to change target. Use @<role> <msg> or @all <msg> for one-off. '
        'Use /task + /bootstrap for actionable work.'
    )

    accept_buf: List[str] = []
    seen: Dict[str, float] = {}
    default_to = "lead"

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
            print("  /bootstrap             send bootstrap to lead (auto-plan tasks graph from shared/task.md)")
            print("  /tasks [status]        list task-board entries (status: pending,in_progress,completed,failed)")
            print("  /tadd <role> <title>   create a pending task and dispatch implement message with task_id")
            print("  /to <role>             set default chat target for plain text (default: lead)")
            print("  @<role> <msg>          one-off message to role (or @all <msg>)")
            print("  /send <to> <intent> <msg> [--task <id>]  send a bus message (optional task binding)")
            print("  /outbox                show last 5 receipts")
            print("  /status                show basic session paths")
            print("  /sh <cmd>              run a *safe* shell command in repo root (set TEAM_SH_UNSAFE=1 to allow any)")
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

        if line.startswith("/tasks"):
            arg = line[len("/tasks") :].strip()
            statuses = [s for s in re.split(r"[,\s]+", arg) if s] if arg else []
            tasks = list_tasks(sp.session_root, statuses=statuses or None)
            if not tasks:
                print("(no tasks)")
                continue
            counts: Dict[str, int] = {}
            for t in tasks:
                st = str(t.get("status", "")).strip() or "unknown"
                counts[st] = counts.get(st, 0) + 1
            print(
                "tasks:"
                + "".join(
                    [
                        f" total={len(tasks)}",
                        f" pending={counts.get('pending', 0)}",
                        f" in_progress={counts.get('in_progress', 0)}",
                        f" completed={counts.get('completed', 0)}",
                        f" failed={counts.get('failed', 0)}",
                    ]
                )
            )
            for t in tasks[:50]:
                print(format_task_brief(t))
            if len(tasks) > 50:
                print(f"... ({len(tasks) - 50} more)")
            continue

        if line.startswith("/tadd "):
            parts = line.split(" ", 2)
            if len(parts) < 3:
                print("usage: /tadd <role> <title>")
                continue
            _, to_role, title = parts
            title = title.strip()
            if to_role not in roles:
                print(f"invalid role: {to_role}")
                continue
            if not title:
                print("usage: /tadd <role> <title>")
                continue
            task = add_task(
                sp.session_root,
                title=title,
                created_by="lead",
                owner=to_role,
                work_type="implement",
                risk="low",
                acceptance=accept_buf,
                depends_on=[],
                intent="implement",
            )
            task_id = str(task.get("id", "")).strip()
            body_lines = [f"[Task {task_id}] {title}"]
            if accept_buf:
                body_lines.append("")
                body_lines.append("Acceptance:")
                for a in accept_buf:
                    aa = a.strip()
                    if aa:
                        body_lines.append(f"- {aa}")
            p = enqueue_message(
                sp,
                to_role=to_role,
                from_role="lead",
                intent="implement",
                thread=session,
                risk="low",
                body="\n".join(body_lines),
                task_id=task_id,
            )
            set_dispatch(
                sp.session_root,
                task_id=task_id,
                from_role="lead",
                to_role=to_role,
                intent="implement",
                message_id=p.stem,
            )
            print(f"task created: {task_id}")
            print(f"enqueued -> {to_role} ({p.name})")
            continue

        if line.startswith("/to"):
            arg = line[len("/to") :].strip()
            if not arg:
                print(f"default_to: {default_to}")
                continue
            to_role = arg.strip()
            if to_role not in roles:
                print(f"invalid role: {to_role}")
                continue
            default_to = to_role
            print(f"default_to: {default_to}")
            continue

        if line.startswith("/send "):
            payload = line[len("/send ") :].strip()
            task_id = ""
            if " --task " in payload:
                payload, task_id = payload.rsplit(" --task ", 1)
                task_id = task_id.strip()
            parts = payload.split(" ", 2)
            if len(parts) < 3:
                print("usage: /send <to> <intent> <msg> [--task <id>]")
                continue
            to_role, intent, msg = parts
            if to_role != "all" and to_role not in roles:
                print(f"invalid role: {to_role}")
                continue
            targets = roles if to_role == "all" else [to_role]
            if task_id and len(targets) != 1:
                print("warn: --task is set but /send all targets multiple roles; skipping task dispatch update")
            for t in targets:
                p = enqueue_message(
                    sp,
                    to_role=t,
                    from_role="user",
                    intent=intent,
                    thread=session,
                    risk="low",
                    body=msg,
                    task_id=task_id,
                )
                if task_id and len(targets) == 1:
                    set_dispatch(
                        sp.session_root,
                        task_id=task_id,
                        from_role="user",
                        to_role=t,
                        intent=intent,
                        message_id=p.stem,
                    )
                print(f"enqueued -> {t}")
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
            print(f"task_board: {sp.session_root / 'state' / 'tasks' / 'tasks.json'}")
            continue

        if line.startswith("/sh "):
            cmd = line[len("/sh ") :].strip()
            if not cmd:
                print("usage: /sh <cmd>")
                continue
            if not _is_safe_shell_command(cmd):
                print("blocked: command not in safe allowlist. Set TEAM_SH_UNSAFE=1 to override.")
                continue
            rc, out = run_shell(cmd, cwd=sp.main_worktree)
            out_lines = out.splitlines()
            max_lines = 200
            if len(out_lines) > max_lines:
                out_lines = out_lines[:max_lines] + [f"... (truncated; {len(out.splitlines())} lines total)"]
            print("\n".join(out_lines))
            print(f"(rc={rc})")
            continue

        if line.startswith("/exit"):
            return 0

        m = re.match(r"^@([A-Za-z0-9_\\-]+)\\s+(.+)$", line.strip())
        if m:
            target = m.group(1).strip()
            msg = m.group(2).strip()
            if not msg:
                continue
            targets = roles if target == "all" else ([target] if target in roles else [])
            if not targets:
                print(f"invalid role: {target}")
                continue
            for t in targets:
                enqueue_message(
                    sp,
                    to_role=t,
                    from_role="user",
                    intent="question",
                    thread=session,
                    risk="low",
                    body=msg,
                )
                print(f"sent -> {t}")
            continue

        # Default: chat to the current target (do not overwrite shared/task.md unexpectedly).
        enqueue_message(
            sp,
            to_role=default_to,
            from_role="user",
            intent="question",
            thread=session,
            risk="low",
            body=line.strip(),
        )
        print(f"sent -> {default_to}")


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
