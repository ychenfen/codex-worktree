#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from task_board import (
    add_task,
    claim_next_task,
    claim_task,
    complete_task,
    ensure_task_board,
    format_task_brief,
    get_task,
    list_dispatchable_tasks,
    list_tasks,
    mark_task_failed,
    set_dispatch,
)


def _run(cmd: List[str], cwd: Optional[Path] = None, timeout_s: Optional[float] = None) -> str:
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


def session_root(session: str) -> Path:
    return git_main_worktree(Path.cwd()) / "sessions" / session


def _print_json(obj: object) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_init(args) -> int:
    root = session_root(args.session)
    if not root.is_dir():
        print(f"session not found: {root}", file=sys.stderr)
        return 2
    fp = ensure_task_board(root)
    print(fp)
    return 0


def cmd_list(args) -> int:
    root = session_root(args.session)
    if not root.is_dir():
        print(f"session not found: {root}", file=sys.stderr)
        return 2
    tasks = list_tasks(root, statuses=args.status or None)
    if args.json:
        _print_json(tasks)
        return 0
    if not tasks:
        print("(no tasks)")
        return 0
    for t in tasks:
        print(format_task_brief(t))
    return 0


def cmd_add(args) -> int:
    root = session_root(args.session)
    if not root.is_dir():
        print(f"session not found: {root}", file=sys.stderr)
        return 2
    t = add_task(
        root,
        title=args.title,
        created_by=args.created_by,
        owner=args.owner or "",
        work_type=args.work_type or "implement",
        risk=args.risk or "low",
        acceptance=args.accept or [],
        depends_on=args.depends_on or [],
        intent=args.intent or "implement",
        source_message_id=args.source_message_id or "",
    )
    if args.json:
        _print_json(t)
    else:
        print(format_task_brief(t))
    return 0


def cmd_show(args) -> int:
    root = session_root(args.session)
    if not root.is_dir():
        print(f"session not found: {root}", file=sys.stderr)
        return 2
    t = get_task(root, args.task)
    if not t:
        print(f"task not found: {args.task}", file=sys.stderr)
        return 3
    _print_json(t)
    return 0


def cmd_dispatchable(args) -> int:
    root = session_root(args.session)
    if not root.is_dir():
        print(f"session not found: {root}", file=sys.stderr)
        return 2
    tasks = list_dispatchable_tasks(root, owner=args.owner or "")
    if args.json:
        _print_json(tasks)
        return 0
    if not tasks:
        print("(no dispatchable tasks)")
        return 0
    for t in tasks:
        print(format_task_brief(t))
    return 0


def cmd_claim(args) -> int:
    root = session_root(args.session)
    if not root.is_dir():
        print(f"session not found: {root}", file=sys.stderr)
        return 2
    if args.task:
        ok, t, reason = claim_task(root, task_id=args.task, role=args.role, message_id=args.message_id or "")
    else:
        ok, t, reason = claim_next_task(root, role=args.role, message_id=args.message_id or "")
    if not ok:
        print(f"claim failed: {reason}", file=sys.stderr)
        if t and args.json:
            _print_json(t)
        return 4
    if t:
        if args.json:
            _print_json(t)
        else:
            print(format_task_brief(t))
    return 0


def cmd_complete(args) -> int:
    root = session_root(args.session)
    if not root.is_dir():
        print(f"session not found: {root}", file=sys.stderr)
        return 2
    ok, t, reason = complete_task(
        root,
        task_id=args.task,
        role=args.role,
        evidence=args.evidence or "",
        receipt_file=args.receipt_file or "",
    )
    if not ok:
        print(f"complete failed: {reason}", file=sys.stderr)
        if t and args.json:
            _print_json(t)
        return 5
    if t:
        if args.json:
            _print_json(t)
        else:
            print(format_task_brief(t))
    return 0


def cmd_fail(args) -> int:
    root = session_root(args.session)
    if not root.is_dir():
        print(f"session not found: {root}", file=sys.stderr)
        return 2
    ok, t, reason = mark_task_failed(
        root,
        task_id=args.task,
        role=args.role,
        error=args.error or "",
        terminal=args.terminal,
    )
    if not ok:
        print(f"fail update failed: {reason}", file=sys.stderr)
        if t and args.json:
            _print_json(t)
        return 6
    if t:
        if args.json:
            _print_json(t)
        else:
            print(format_task_brief(t))
    return 0


def cmd_dispatch(args) -> int:
    root = session_root(args.session)
    if not root.is_dir():
        print(f"session not found: {root}", file=sys.stderr)
        return 2
    ok, t, reason = set_dispatch(
        root,
        task_id=args.task,
        from_role=args.from_role,
        to_role=args.to_role,
        intent=args.intent,
        message_id=args.message_id,
    )
    if not ok:
        print(f"dispatch update failed: {reason}", file=sys.stderr)
        if t and args.json:
            _print_json(t)
        return 7
    if t:
        if args.json:
            _print_json(t)
        else:
            print(format_task_brief(t))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Task state machine for team autopilot.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init")
    p.add_argument("--session", required=True)
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("list")
    p.add_argument("--session", required=True)
    p.add_argument("--status", action="append", default=[])
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("add")
    p.add_argument("--session", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--created-by", default="lead")
    p.add_argument("--owner", default="")
    p.add_argument("--work-type", default="implement")
    p.add_argument("--risk", default="low")
    p.add_argument("--intent", default="implement")
    p.add_argument("--accept", action="append", default=[])
    p.add_argument("--depends-on", action="append", default=[])
    p.add_argument("--source-message-id", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("show")
    p.add_argument("--session", required=True)
    p.add_argument("--task", required=True)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("dispatchable")
    p.add_argument("--session", required=True)
    p.add_argument("--owner", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_dispatchable)

    p = sub.add_parser("claim")
    p.add_argument("--session", required=True)
    p.add_argument("--role", required=True)
    p.add_argument("--task", default="")
    p.add_argument("--message-id", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_claim)

    p = sub.add_parser("complete")
    p.add_argument("--session", required=True)
    p.add_argument("--role", required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--evidence", default="")
    p.add_argument("--receipt-file", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_complete)

    p = sub.add_parser("fail")
    p.add_argument("--session", required=True)
    p.add_argument("--role", required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--error", default="")
    p.add_argument("--terminal", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_fail)

    p = sub.add_parser("dispatch")
    p.add_argument("--session", required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--from-role", required=True)
    p.add_argument("--to-role", required=True)
    p.add_argument("--intent", required=True)
    p.add_argument("--message-id", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_dispatch)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
