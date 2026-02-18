#!/usr/bin/env python3
import argparse
import hashlib
import logging
import os
import re
import signal
import shutil
import stat
import subprocess
import sys
import time
import traceback
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


ROLE_ORDER = ["lead", "builder-a", "builder-b", "reviewer", "tester"]
GLOBAL_LOCK_PID_SUFFIX = "autopilot.global.lockdir/pid"
OP_TRACE_MAX = 10
_RECENT_OPS: "deque[Dict[str, str]]" = deque(maxlen=OP_TRACE_MAX)
_PID_RE = re.compile(r"^[0-9]+$")
HEARTBEAT_SECONDS = 30.0
LOG = logging.getLogger("router")
USE_KQUEUE = (sys.platform == "darwin") and (os.environ.get("ROUTER_USE_KQUEUE", "1") != "0")


@dataclass
class SessionPaths:
    main_worktree: Path
    session_root: Path
    bus: Path
    state: Path
    artifacts: Path
    shared: Path
    roles: Path


def init_logging() -> None:
    if LOG.handlers:
        return
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [router] %(message)s"))
    LOG.addHandler(h)
    LOG.setLevel(logging.INFO)
    LOG.propagate = False


def _flush_log_handlers() -> None:
    for h in LOG.handlers:
        try:
            h.flush()
        except Exception:
            pass


def _signal_process_context(*, session: str, role: str) -> str:
    pid = os.getpid()
    ppid = os.getppid()
    try:
        pgid = os.getpgid(pid)
    except Exception as e:
        pgid = f"err:{e}"
    try:
        sid = os.getsid(pid)
    except Exception as e:
        sid = f"err:{e}"
    try:
        cwd = str(Path.cwd())
    except Exception as e:
        cwd = f"<cwd-error:{e}>"

    ps_line = "<ps-unavailable>"
    try:
        ps_attempts = [
            ["ps", "-o", "pid,ppid,pgid,sid,tty,stat,etime,command", "-p", str(pid)],
            ["ps", "-o", "pid,ppid,pgid,sess,tty,stat,etime,command", "-p", str(pid)],
        ]
        last_err = ""
        for ps_cmd in ps_attempts:
            p = subprocess.run(
                ps_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            if p.returncode == 0 and p.stdout.strip():
                ps_line = " | ".join([ln.strip() for ln in p.stdout.splitlines() if ln.strip()])
                break
            if p.stderr.strip():
                last_err = f"<ps-rc={p.returncode} err={p.stderr.strip()}>"
            else:
                last_err = f"<ps-rc={p.returncode}>"
        if ps_line == "<ps-unavailable>" and last_err:
            ps_line = last_err
    except Exception as e:
        ps_line = f"<ps-exception:{e}>"

    return (
        f"session={session} role={role} pid={pid} ppid={ppid} pgid={pgid} sid={sid} "
        f"cwd={cwd} ps=\"{ps_line}\""
    )


def _wait_for_dir_change(path: Path, timeout_s: float) -> None:
    if timeout_s <= 0:
        return
    if not USE_KQUEUE or not path.is_dir():
        time.sleep(timeout_s)
        return
    try:
        import select  # local import: only supported on some platforms

        oflag = getattr(os, "O_EVTONLY", os.O_RDONLY)
        fd = os.open(str(path), oflag)
        try:
            kq = select.kqueue()
            try:
                ev = select.kevent(
                    fd,
                    filter=select.KQ_FILTER_VNODE,
                    flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                    fflags=(
                        select.KQ_NOTE_WRITE
                        | select.KQ_NOTE_EXTEND
                        | select.KQ_NOTE_ATTRIB
                        | select.KQ_NOTE_RENAME
                        | select.KQ_NOTE_DELETE
                    ),
                )
                kq.control([ev], 0, 0)
                kq.control(None, 1, timeout_s)
            finally:
                try:
                    kq.close()
                except Exception:
                    pass
        finally:
            try:
                os.close(fd)
            except Exception:
                pass
    except Exception:
        time.sleep(timeout_s)


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
    # Main worktree:
    # - main repo: .../.git
    # - linked worktree: .../.git/worktrees/<name>
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
    mkdirp(sp.state / "router" / "bad-receipts")
    mkdirp(sp.state / "router" / "bad-locks")


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


def _count_md_files(path: Path) -> int:
    try:
        return sum(1 for p in path.glob("*.md") if p.is_file())
    except Exception:
        return 0


def _count_inbox_files(sp: SessionPaths) -> int:
    total = 0
    try:
        for role_dir in (sp.bus / "inbox").glob("*"):
            if role_dir.is_dir():
                total += _count_md_files(role_dir)
    except Exception:
        return 0
    return total


def log_heartbeat(sp: SessionPaths, session: str, poll_s: float) -> None:
    inbox_count = _count_inbox_files(sp)
    outbox_count = _count_md_files(sp.bus / "outbox")
    LOG.info(
        "heartbeat session=%s role=router pid=%s poll_s=%s inbox_count=%s outbox_count=%s current_task_id=%s",
        session,
        os.getpid(),
        poll_s,
        inbox_count,
        outbox_count,
        "-",
    )


def record_op(name: str, path: Path, extra: str = "") -> None:
    rec = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "name": name,
        "path": str(path),
        "extra": extra,
    }
    _RECENT_OPS.append(rec)
    LOG.info("[op] name=%s path=%s extra=%s", name, path, extra)


def recent_ops_lines() -> List[str]:
    lines: List[str] = []
    for i, rec in enumerate(_RECENT_OPS, start=1):
        lines.append(
            f"{i}. ts={rec.get('ts','')} name={rec.get('name','')} "
            f"path={rec.get('path','')} extra={rec.get('extra','')}"
        )
    return lines


def safe_read_text(path: Path) -> str:
    record_op("read", path)
    return read_text(path)


def safe_atomic_write(path: Path, text: str, extra: str = "") -> None:
    record_op("write", path, extra=extra)
    atomic_write(path, text)


def safe_rename(src: Path, dst: Path) -> None:
    record_op("rename", src, extra=f"src={src} dst={dst}")
    os.rename(src, dst)


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
    safe_atomic_write(
        out,
        text,
        extra=f"phase=inbox-write from={from_role} to={to_role} intent={intent}",
    )
    return out


def quarantine_receipt(sp: SessionPaths, receipt_path: Path, reason: str) -> None:
    """
    Move unreadable/broken receipts out of the hot path so the router loop keeps running.
    """
    bad_dir = sp.state / "router" / "bad-receipts"
    mkdirp(bad_dir)
    ts = time.strftime("%Y%m%d-%H%M%S")
    target = bad_dir / f"{receipt_path.name}.{ts}.bad"
    note = bad_dir / f"{receipt_path.name}.{ts}.error.txt"
    moved_to = receipt_path
    try:
        safe_rename(receipt_path, target)
        moved_to = target
    except Exception:
        pass
    try:
        note.write_text(
            "\n".join(
                [
                    f"time: {time.strftime('%Y-%m-%d %H:%M:%S')}",
                    f"receipt: {receipt_path}",
                    f"moved_to: {moved_to}",
                    f"reason: {reason}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    except Exception:
        pass
    LOG.warning("quarantined_receipt receipt=%s moved_to=%s reason=%s", receipt_path, moved_to, reason)


def write_router_lock_receipt(sp: SessionPaths, status: str, body: str) -> Path:
    rid = new_id("router-lock-")
    out = sp.bus / "outbox" / f"{rid}.router.md"
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    text = "\n".join(
        [
            "---",
            f"id: {rid}",
            "role: router",
            f'thread: "{sp.session_root.name}"',
            'request_from: "router"',
            'request_to: "lead"',
            'request_intent: "warn"',
            f"status: {status}",
            "codex_rc: 0",
            f'finished_at: "{ts}"',
            "---",
            "",
            body.rstrip(),
            "",
        ]
    )
    safe_atomic_write(out, text, extra="phase=router-lock-receipt")
    return out


def _extract_errno22_path(err: OSError) -> Optional[Path]:
    cands: List[str] = []
    fn = getattr(err, "filename", None)
    if isinstance(fn, str) and fn:
        cands.append(fn)
    fn2 = getattr(err, "filename2", None)
    if isinstance(fn2, str) and fn2:
        cands.append(fn2)

    text = str(err)
    m = re.search(r"([/][^'\"]*autopilot\.global\.lockdir/pid)", text)
    if m:
        cands.append(m.group(1))

    for c in cands:
        if GLOBAL_LOCK_PID_SUFFIX in c:
            return Path(c)
    return None


def _is_target_errno22(err: BaseException) -> Tuple[bool, Optional[Path]]:
    if not isinstance(err, OSError):
        return False, None
    if int(getattr(err, "errno", 0) or 0) != 22:
        return False, None
    p = _extract_errno22_path(err)
    return (p is not None), p


def _lstat_debug(path: Optional[Path]) -> str:
    if path is None:
        return "lstat: <no path>"
    try:
        st = os.lstat(path)
        return (
            f"lstat: path={path} mode={stat.filemode(st.st_mode)} "
            f"size={st.st_size} ino={st.st_ino} mtime={int(st.st_mtime)}"
        )
    except Exception as e:
        return f"lstat: path={path} error={e}"


def confirm_global_lock_pid_broken(sp: SessionPaths, target_path: Optional[Path]) -> Tuple[bool, str]:
    _ = sp
    if target_path is None:
        return True, "probe: no target_path (confirmed broken)"
    try:
        st = os.lstat(target_path)
    except Exception as e:
        return True, f"probe: lstat failed path={target_path} err={e!r} (confirmed broken)"

    mode = stat.filemode(st.st_mode)
    size = st.st_size
    if not stat.S_ISREG(st.st_mode):
        return True, (
            f"probe: non-regular path={target_path} mode={mode} size={size} "
            "(confirmed broken)"
        )
    try:
        data = target_path.read_bytes()[:64]
        preview = data.decode("utf-8", errors="replace").strip()
    except Exception as e:
        return True, (
            f"probe: read failed path={target_path} mode={mode} size={size} "
            f"err={e!r} (confirmed broken)"
        )
    if not (1 <= len(preview) <= 20):
        return True, (
            f"probe: pid length invalid path={target_path} mode={mode} size={size} "
            f"preview={preview!r} (confirmed broken)"
        )
    if not _PID_RE.match(preview):
        return True, (
            f"probe: pid content invalid path={target_path} mode={mode} size={size} "
            f"preview={preview!r} (confirmed broken)"
        )
    try:
        int(preview)
    except Exception as e:
        return True, (
            f"probe: pid int parse failed path={target_path} mode={mode} size={size} "
            f"preview={preview!r} err={e!r} (confirmed broken)"
        )
    return False, (
        f"probe: pid looks OK path={target_path} mode={mode} size={size} "
        f"pid={preview} (NOT confirmed)"
    )


def quarantine_global_lockdir(sp: SessionPaths) -> Tuple[bool, str]:
    src = sp.artifacts / "locks" / "autopilot.global.lockdir"
    if not src.exists():
        return False, f"lockdir missing: {src}"

    bad_root = sp.state / "router" / "bad-locks"
    mkdirp(bad_root)
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = bad_root / f"{ts}-autopilot.global.lockdir"

    try:
        # Same-filesystem rename is atomic.
        os.rename(src, dst)
        return True, f"renamed lockdir to {dst}"
    except Exception as rename_err:
        try:
            if dst.exists():
                shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(src, dst, symlinks=True)
            shutil.rmtree(src, ignore_errors=True)
            return True, f"copy+remove lockdir to {dst} (rename failed: {rename_err})"
        except Exception as copy_err:
            return False, f"failed to isolate lockdir (rename: {rename_err}; copy+remove: {copy_err})"


def handle_errno22_invalid_lock_pid(
    sp: SessionPaths,
    *,
    current_item: Path,
    action: str,
    err: OSError,
    target_path: Optional[Path],
    confirmed_lock_action: bool,
    probe_msg: str,
) -> None:
    LOG.warning(
        "errno22_detected action=%s current_item=%s errno=%s path=%s confirm=%s reason=%s lstat=%s",
        action,
        current_item,
        getattr(err, "errno", ""),
        target_path,
        confirmed_lock_action,
        probe_msg,
        _lstat_debug(target_path),
    )
    LOG.warning("errno22_next_action=quarantine_lockdir_if_confirmed")
    ops = recent_ops_lines()
    if ops:
        LOG.warning("recent_ops(last10):")
        for line in ops:
            LOG.warning("recent_op %s", line)
    else:
        LOG.warning("recent_ops(last10): <empty>")
    LOG.error("traceback=%s", traceback.format_exc().rstrip())

    if not confirmed_lock_action:
        LOG.warning("lockdir_quarantine_skipped probe_confirmed=false; quarantining_current_receipt_only")
        quarantine_receipt(
            sp,
            current_item,
            f"errno22 target={target_path} but probe says pid ok; lockdir untouched",
        )
        return

    ok, move_msg = quarantine_global_lockdir(sp)
    receipt_status = "done" if ok else "warn"
    receipt_body = "\n".join(
        [
            "Router isolated invalid global lock PID path after OSError(errno=22).",
            f"- current_item: {current_item}",
            f"- action: {action}",
            f"- error: {err}",
            f"- lock_pid_path: {target_path or '<unknown>'}",
            f"- lstat: {_lstat_debug(target_path)}",
            f"- probe: {probe_msg}",
            f"- quarantine_result: {move_msg}",
        ]
    )
    out = write_router_lock_receipt(sp, receipt_status, receipt_body)
    LOG.warning("wrote_isolation_receipt receipt=%s status=%s", out, receipt_status)


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
      ::bus-send{to="all" intent="info" risk="low" message="..."}
      ::bus-send{to="reviewer,tester" intent="test" risk="low" message="..."}
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


def parse_target_roles(roles: List[str], to_expr: str, *, sender_role: str) -> List[str]:
    """
    Resolve a directive target expression into concrete roles.

    Supported:
    - to="lead" (single)
    - to="reviewer,tester" (multi)
    - to="all" (broadcast to all roles except sender_role)
    """
    to_expr = (to_expr or "").strip()
    if not to_expr:
        return []

    out: List[str] = []
    parts = [p for p in re.split(r"[,\s]+", to_expr) if p]
    for p in parts:
        if p.lower() == "all":
            for r in roles:
                if r != sender_role and r not in out:
                    out.append(r)
            continue
        if valid_role(roles, p) and p not in out:
            out.append(p)
    return out


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
        to_expr = (d.get("to") or "").strip()
        intent = (d.get("intent") or "").strip()
        risk = (d.get("risk") or "low").strip()
        message = (d.get("message") or "").strip()
        accept = (d.get("accept") or "").strip()

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

        targets = parse_target_roles(roles, to_expr, sender_role=worker_role)
        if not targets:
            # Invalid/no targets: notify lead only.
            if not dry_run and "lead" in roles:
                enqueue_bus_message(
                    sp,
                    to_role="lead",
                    from_role="router",
                    intent="alert",
                    thread=thread,
                    risk="medium",
                    body=f'Invalid ::bus-send target "{to_expr}" from receipt {receipt_id} (worker={worker_role}).',
                )
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
            for to_role in targets:
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
    try:
        raw = safe_read_text(receipt_path)
    except Exception as e:
        quarantine_receipt(sp, receipt_path, f"read failed: {e}")
        return True

    cur_hash = sha256_text(raw)
    st_file = processed_state_file(sp, receipt_path)
    try:
        if st_file.exists():
            record_op("read", st_file, extra="phase=processed-state-read")
            prev_hash = st_file.read_text(encoding="utf-8").strip()
        else:
            prev_hash = ""
    except Exception:
        prev_hash = ""
    if prev_hash == cur_hash:
        return False

    front, body = parse_frontmatter(raw)
    thread = str(front.get("thread", sp.session_root.name)).strip() or sp.session_root.name
    mid = str(front.get("id", receipt_path.stem)).strip() or receipt_path.stem
    role = str(front.get("role", "unknown")).strip()
    status = str(front.get("status", "unknown")).strip()
    codex_rc = str(front.get("codex_rc", "")).strip()
    task_id = str(front.get("task_id", "")).strip()
    req_from = str(front.get("request_from", "")).strip()
    req_to = str(front.get("request_to", "")).strip()
    req_intent = str(front.get("request_intent", "")).strip()

    # Avoid infinite loops:
    # - Router forwards receipts by sending bus messages from `from: router`.
    # - Those forwarded messages will themselves generate receipts when processed by workers.
    # - The router must NOT forward receipts whose originating sender is `router`.
    if req_from == "router":
        try:
            mkdirp(st_file.parent)
            safe_atomic_write(st_file, cur_hash + "\n", extra="phase=mark-processed")
        except Exception as e:
            LOG.warning("mark_processed_failed file=%s err=%s", st_file, e)
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
            f"- task_id: {task_id or '-'}",
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

    try:
        mkdirp(st_file.parent)
        safe_atomic_write(st_file, cur_hash + "\n", extra="phase=mark-processed")
    except Exception as e:
        LOG.warning("mark_processed_failed file=%s err=%s", st_file, e)
    return True


def loop(session: str, poll_s: float, dry_run: bool) -> int:
    init_logging()
    rc = 0
    runtime_state: Dict[str, str] = {
        "last_receipt_path": "-",
        "last_receipt_id": "-",
    }
    signal_seen = {"signum": 0}
    prev_sigterm = None
    prev_sigint = None

    def _signal_handler(signum: int, _frame: object) -> None:
        signal_seen["signum"] = signum
        LOG.error(
            "SIGNAL received session=%s role=router pid=%s signum=%s last_receipt_id=%s last_receipt_path=%s",
            session,
            os.getpid(),
            signum,
            runtime_state.get("last_receipt_id", "-"),
            runtime_state.get("last_receipt_path", "-"),
        )
        LOG.error("SIGNAL process_context %s", _signal_process_context(session=session, role="router"))
        _flush_log_handlers()
        raise SystemExit(128 + int(signum))

    try:
        prev_sigterm = signal.signal(signal.SIGTERM, _signal_handler)
        prev_sigint = signal.signal(signal.SIGINT, _signal_handler)

        start_dir = Path.cwd()
        main = git_main_worktree(start_dir)
        sp = session_paths(main, session)

        if not sp.session_root.is_dir():
            LOG.error("session_not_found session=%s path=%s", session, sp.session_root)
            rc = 2
            return rc

        ensure_dirs(sp)
        roles = list_roles(sp)
        for r in roles:
            mkdirp(inbox_dir(sp, r))

        LOG.info(
            "daemon_start session=%s role=router pid=%s poll_s=%s dry_run=%s",
            session,
            os.getpid(),
            poll_s,
            dry_run,
        )
        outbox = sp.bus / "outbox"
        next_hb = 0.0
        while True:
            now = time.monotonic()
            if now >= next_hb:
                log_heartbeat(sp, session=session, poll_s=poll_s)
                next_hb = now + HEARTBEAT_SECONDS
            did_any = False
            outbox_files = [p for p in sorted(outbox.glob("*.md")) if p.is_file()]
            LOG.info("scan_outbox session=%s role=router outbox_count=%s", session, len(outbox_files))
            for p in outbox_files:
                runtime_state["last_receipt_path"] = str(p)
                runtime_state["last_receipt_id"] = p.stem
                try:
                    if process_receipt(sp, roles=roles, receipt_path=p, dry_run=dry_run):
                        did_any = True
                except OSError as e:
                    LOG.exception("process_receipt failed receipt_path=%s", p)
                    matched, target = _is_target_errno22(e)
                    if matched:
                        confirmed, probe_msg = confirm_global_lock_pid_broken(sp, target)
                        handle_errno22_invalid_lock_pid(
                            sp,
                            current_item=p,
                            action="process_receipt(open/read/mark-processed)",
                            err=e,
                            target_path=target,
                            confirmed_lock_action=confirmed,
                            probe_msg=probe_msg,
                        )
                        did_any = True
                        continue
                    quarantine_receipt(sp, p, f"unhandled processing os error: {e}")
                    did_any = True
                except Exception as e:
                    LOG.exception("process_receipt failed receipt_path=%s", p)
                    quarantine_receipt(sp, p, f"unhandled processing error: {e}")
                    did_any = True
            if not did_any:
                _wait_for_dir_change(outbox, poll_s)
    except KeyboardInterrupt:
        rc = 130
        LOG.error(
            "EXIT by KeyboardInterrupt session=%s role=router pid=%s last_receipt_id=%s last_receipt_path=%s",
            session,
            os.getpid(),
            runtime_state.get("last_receipt_id", "-"),
            runtime_state.get("last_receipt_path", "-"),
        )
    except SystemExit as e:
        rc = int(e.code) if isinstance(e.code, int) else 1
        LOG.error(
            "EXIT by SystemExit session=%s role=router pid=%s rc=%s signal=%s last_receipt_id=%s last_receipt_path=%s",
            session,
            os.getpid(),
            rc,
            signal_seen.get("signum", 0),
            runtime_state.get("last_receipt_id", "-"),
            runtime_state.get("last_receipt_path", "-"),
        )
    except Exception:
        rc = 2
        LOG.exception(
            "FATAL: daemon crashed session=%s role=router pid=%s last_receipt_id=%s last_receipt_path=%s",
            session,
            os.getpid(),
            runtime_state.get("last_receipt_id", "-"),
            runtime_state.get("last_receipt_path", "-"),
        )
    finally:
        if prev_sigterm is not None:
            signal.signal(signal.SIGTERM, prev_sigterm)
        if prev_sigint is not None:
            signal.signal(signal.SIGINT, prev_sigint)
        LOG.error(
            "EXIT rc=%s session=%s role=router pid=%s signal=%s last_receipt_id=%s last_receipt_path=%s",
            rc,
            session,
            os.getpid(),
            signal_seen.get("signum", 0),
            runtime_state.get("last_receipt_id", "-"),
            runtime_state.get("last_receipt_path", "-"),
        )
        _flush_log_handlers()
    return rc


def run_once(session: str, dry_run: bool) -> int:
    init_logging()
    start_dir = Path.cwd()
    main = git_main_worktree(start_dir)
    sp = session_paths(main, session)
    if not sp.session_root.is_dir():
        LOG.error("session_not_found session=%s path=%s", session, sp.session_root)
        return 2

    ensure_dirs(sp)
    roles = list_roles(sp)
    for r in roles:
        mkdirp(inbox_dir(sp, r))

    outbox = sp.bus / "outbox"
    LOG.info("run_once_start session=%s role=router pid=%s dry_run=%s", session, os.getpid(), dry_run)
    log_heartbeat(sp, session=session, poll_s=0.0)
    did_any = False
    outbox_files = [p for p in sorted(outbox.glob("*.md")) if p.is_file()]
    LOG.info("scan_outbox session=%s role=router outbox_count=%s", session, len(outbox_files))
    for p in outbox_files:
        try:
            if process_receipt(sp, roles=roles, receipt_path=p, dry_run=dry_run):
                did_any = True
        except OSError as e:
            LOG.exception("process_receipt failed receipt_path=%s", p)
            matched, target = _is_target_errno22(e)
            if matched:
                confirmed, probe_msg = confirm_global_lock_pid_broken(sp, target)
                handle_errno22_invalid_lock_pid(
                    sp,
                    current_item=p,
                    action="process_receipt(open/read/mark-processed)",
                    err=e,
                    target_path=target,
                    confirmed_lock_action=confirmed,
                    probe_msg=probe_msg,
                )
                did_any = True
                continue
            quarantine_receipt(sp, p, f"unhandled processing os error: {e}")
            did_any = True
        except Exception as e:
            LOG.exception("process_receipt failed receipt_path=%s", p)
            quarantine_receipt(sp, p, f"unhandled processing error: {e}")
            did_any = True
    LOG.info("run_once_finish session=%s role=router processed=%s", session, did_any)
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
