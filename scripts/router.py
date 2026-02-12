#!/usr/bin/env python3
import argparse
import hashlib
import os
import re
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
    bus: Path
    state: Path
    artifacts: Path
    shared: Path
    roles: Path


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
        bus=root / "bus",
        state=root / "state",
        artifacts=root / "artifacts",
        shared=root / "shared",
        roles=root / "roles",
    )


def mkdirp(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def parse_frontmatter(md: str) -> Tuple[Dict[str, object], str]:
    """
    Minimal YAML frontmatter parser for this repo's bus/receipt formats.
    Supports:
    - key: value
    - lists with `  - "..."` lines
    """
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
    roles: List[str] = []
    if sp.roles.is_dir():
        for d in sp.roles.iterdir():
            if d.is_dir():
                roles.append(d.name)
    return [r for r in ROLE_ORDER if r in roles]


def sha256_text(s: str) -> str:
    h = hashlib.sha256()
    h.update(s.encode("utf-8"))
    return h.hexdigest()


def processed_state_dir(sp: SessionPaths) -> Path:
    return sp.state / "router" / "processed"


def processed_state_file(sp: SessionPaths, receipt_path: Path) -> Path:
    # Preserve the filename; outbox receipts are unique per message+role.
    safe = receipt_path.name
    return processed_state_dir(sp) / f"{safe}.sha256"


def ensure_dirs(sp: SessionPaths) -> None:
    mkdirp(sp.bus / "outbox")
    mkdirp(sp.bus / "inbox")
    mkdirp(sp.state / "router" / "processed")


def inbox_dir(sp: SessionPaths, role: str) -> Path:
    return sp.bus / "inbox" / role


def new_id(prefix: str) -> str:
    import secrets

    ts = time.strftime("%Y%m%d-%H%M%S")
    return f"{prefix}{ts}-{secrets.token_hex(3)}"


def atomic_write(path: Path, text: str) -> None:
    mkdirp(path.parent)
    tmp = path.parent / f".tmp.{path.name}.{os.getpid()}"
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def enqueue_bus_message(
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
    mid = mid or new_id("router-")
    out = inbox_dir(sp, to_role) / f"{mid}.md"
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


def receipt_targets(roles: List[str], receipt_front: Dict[str, object]) -> List[str]:
    out: List[str] = []
    if "lead" in roles:
        out.append("lead")
    req_from = str(receipt_front.get("request_from", "")).strip()
    if req_from and req_from in roles and req_from not in out:
        out.append(req_from)
    return out


def receipt_intent(status: str) -> str:
    if status in ("retry", "deadletter"):
        return "alert"
    return "receipt"


def parse_kv_block(s: str) -> Dict[str, str]:
    """
    Parse a directive argument block like:
      to="reviewer" intent="review" message="..."
    into a dict. Supports quoted values with \\" escaping.
    """
    out: Dict[str, str] = {}
    i = 0
    n = len(s)
    while i < n:
        while i < n and s[i].isspace():
            i += 1
        if i >= n:
            break
        # key
        k_start = i
        while i < n and (s[i].isalnum() or s[i] in "_-"):
            i += 1
        key = s[k_start:i]
        while i < n and s[i].isspace():
            i += 1
        if not key or i >= n or s[i] != "=":
            break
        i += 1
        while i < n and s[i].isspace():
            i += 1
        if i >= n:
            break
        val = ""
        if s[i] == '"':
            i += 1
            buf = []
            while i < n:
                ch = s[i]
                if ch == "\\" and i + 1 < n:
                    buf.append(s[i + 1])
                    i += 2
                    continue
                if ch == '"':
                    i += 1
                    break
                buf.append(ch)
                i += 1
            val = "".join(buf)
        else:
            v_start = i
            while i < n and not s[i].isspace():
                i += 1
            val = s[v_start:i]
        out[key] = val
    return out


def parse_bus_send_directives(text: str) -> List[Dict[str, str]]:
    """
    Parse directives from receipt/body:
      ::bus-send{to="reviewer" intent="review" risk="low" message="..."}
    """
    out: List[Dict[str, str]] = []
    for m in re.finditer(r"::bus-send\{([^}]*)\}", text):
        args = parse_kv_block(m.group(1))
        if args:
            out.append(args)
    return out


def allowed_intent(sender_role: str, intent: str) -> bool:
    """
    Guardrail to keep "team mode" coherent:
    - Lead can dispatch anything.
    - Others can dispatch follow-ups (review/test/fix/question/info), but not new "implement" tasks.
    """
    intent = (intent or "").strip().lower()
    if not intent:
        return False
    if sender_role == "lead":
        return True
    return intent in ("question", "review", "test", "fix", "info", "alert")


def valid_role(roles: List[str], r: str) -> bool:
    r = (r or "").strip()
    return bool(r) and r in roles


def dispatch_from_receipt(
    sp: SessionPaths,
    roles: List[str],
    *,
    worker_role: str,
    thread: str,
    receipt_id: str,
    directives: List[Dict[str, str]],
    dry_run: bool,
) -> None:
    for d in directives:
        to_role = (d.get("to") or "").strip()
        intent = (d.get("intent") or "").strip()
        risk = (d.get("risk") or "low").strip()
        message = (d.get("message") or "").strip()
        accept = (d.get("accept") or "").strip()

        if not valid_role(roles, to_role):
            # Invalid target: notify lead only.
            if not dry_run and "lead" in roles:
                enqueue_bus_message(
                    sp,
                    to_role="lead",
                    from_role="router",
                    intent="alert",
                    thread=thread,
                    risk="medium",
                    body=f'Invalid ::bus-send target role "{to_role}" from receipt {receipt_id} (worker={worker_role}).',
                )
            continue
        if not allowed_intent(worker_role, intent):
            if not dry_run and "lead" in roles:
                enqueue_bus_message(
                    sp,
                    to_role="lead",
                    from_role="router",
                    intent="alert",
                    thread=thread,
                    risk="medium",
                    body=f'Disallowed ::bus-send intent "{intent}" from worker {worker_role} (receipt {receipt_id}).',
                )
            continue
        if not message:
            continue

        body = "\n".join(
            [
                "Auto-dispatched by router from a worker receipt.",
                "",
                f"- receipt_id: {receipt_id}",
                f"- worker_role: {worker_role}",
                "",
                message,
                "",
            ]
        )
        if accept:
            body += "\nAcceptance:\n- " + accept + "\n"

        if not dry_run:
            enqueue_bus_message(
                sp,
                to_role=to_role,
                from_role=worker_role,
                intent=intent,
                thread=thread,
                risk=risk or "low",
                body=body,
            )


def process_receipt(sp: SessionPaths, roles: List[str], receipt_path: Path, dry_run: bool) -> bool:
    raw = read_text(receipt_path)
    cur_hash = sha256_text(raw)
    st_file = processed_state_file(sp, receipt_path)
    prev_hash = st_file.read_text(encoding="utf-8").strip() if st_file.exists() else ""
    if prev_hash == cur_hash:
        return False

    front, body = parse_frontmatter(raw)
    thread = str(front.get("thread", sp.session_root.name)).strip() or sp.session_root.name
    mid = str(front.get("id", receipt_path.stem)).strip() or receipt_path.stem
    role = str(front.get("role", "unknown")).strip()
    status = str(front.get("status", "unknown")).strip()
    codex_rc = str(front.get("codex_rc", "")).strip()
    req_from = str(front.get("request_from", "")).strip()
    req_to = str(front.get("request_to", "")).strip()
    req_intent = str(front.get("request_intent", "")).strip()

    # Avoid infinite loops:
    # - Router forwards receipts by sending bus messages from `from: router`.
    # - Those forwarded messages will themselves generate receipts when processed by workers.
    # - The router must NOT forward receipts whose originating sender is `router`.
    if req_from == "router":
        mkdirp(st_file.parent)
        atomic_write(st_file, cur_hash + "\n")
        return True

    directives = parse_bus_send_directives(raw)
    if directives:
        dispatch_from_receipt(
            sp,
            roles=roles,
            worker_role=role,
            thread=thread,
            receipt_id=mid,
            directives=directives,
            dry_run=dry_run,
        )

    intent = receipt_intent(status)
    risk = "medium" if intent == "alert" else "low"
    targets = receipt_targets(roles, front)

    # Keep the forwarded message short and stable; link to the receipt path for full details.
    forwarded = "\n".join(
        [
            f"Receipt forwarded by router.",
            "",
            f"- message_id: {mid}",
            f"- worker_role: {role}",
            f"- status: {status}",
            f"- codex_rc: {codex_rc}",
            f"- request_from: {req_from}",
            f"- request_to: {req_to}",
            f"- request_intent: {req_intent}",
            f"- receipt_file: {receipt_path}",
            "",
            "Receipt content (verbatim):",
            "```md",
            raw.rstrip(),
            "```",
            "",
            "If follow-up work is needed, dispatch it via the bus (no shared-file edits):",
            f'  ./scripts/bus-send.sh --session {thread} --from <role> --to <role> --intent <intent> --message "<...>"',
        ]
    )

    if not dry_run:
        for t in targets:
            mkdirp(inbox_dir(sp, t))
            enqueue_bus_message(
                sp,
                to_role=t,
                from_role="router",
                intent=intent,
                thread=thread,
                risk=risk,
                body=forwarded,
            )

    mkdirp(st_file.parent)
    atomic_write(st_file, cur_hash + "\n")
    return True


def loop(session: str, poll_s: float, dry_run: bool) -> int:
    start_dir = Path.cwd()
    main = git_main_worktree(start_dir)
    sp = session_paths(main, session)

    if not sp.session_root.is_dir():
        print(f"session not found: {sp.session_root}", file=sys.stderr)
        return 2

    ensure_dirs(sp)
    roles = list_roles(sp)
    for r in roles:
        mkdirp(inbox_dir(sp, r))

    outbox = sp.bus / "outbox"
    while True:
        did_any = False
        for p in sorted(outbox.glob("*.md")):
            if p.is_file():
                if process_receipt(sp, roles=roles, receipt_path=p, dry_run=dry_run):
                    did_any = True
        if not did_any:
            time.sleep(poll_s)


def run_once(session: str, dry_run: bool) -> int:
    start_dir = Path.cwd()
    main = git_main_worktree(start_dir)
    sp = session_paths(main, session)
    if not sp.session_root.is_dir():
        print(f"session not found: {sp.session_root}", file=sys.stderr)
        return 2

    ensure_dirs(sp)
    roles = list_roles(sp)
    for r in roles:
        mkdirp(inbox_dir(sp, r))

    outbox = sp.bus / "outbox"
    did_any = False
    for p in sorted(outbox.glob("*.md")):
        if p.is_file():
            if process_receipt(sp, roles=roles, receipt_path=p, dry_run=dry_run):
                did_any = True
    return 0 if did_any else 3


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("daemon", help="Route outbox receipts into inbox messages (blocking).")
    d.add_argument("--session", required=True)
    d.add_argument("--poll", type=float, default=2.0)
    d.add_argument("--dry-run", action="store_true")

    o = sub.add_parser("once", help="Process all current receipts once and exit.")
    o.add_argument("--session", required=True)
    o.add_argument("--dry-run", action="store_true")

    args = ap.parse_args()
    if args.cmd == "daemon":
        return loop(session=args.session, poll_s=args.poll, dry_run=args.dry_run)
    if args.cmd == "once":
        return run_once(session=args.session, dry_run=args.dry_run)
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
