#!/usr/bin/env python3
import argparse
import json
import logging
import os
import re
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from task_board import (
    add_task,
    claim_task,
    complete_task,
    ensure_task_board,
    get_task,
    list_dispatchable_tasks,
    list_tasks,
    mark_task_failed,
    set_dispatch,
)


ROLE_ORDER = ["lead", "builder-a", "builder-b", "reviewer", "tester"]
DEFAULT_FALLBACK_MODELS = ["gpt-5.2-codex", "gpt-5.2", "gpt-5.1-codex-max"]
LOCK_STALE_SECONDS = int(os.environ.get("AUTOPILOT_LOCK_STALE_SECONDS", "21600"))
HEARTBEAT_SECONDS = 30.0
DISPATCH_SCAN_SECONDS = float(os.environ.get("AUTOPILOT_DISPATCH_SCAN_SECONDS", "5"))
DISPATCH_MAX_PER_SCAN = int(os.environ.get("AUTOPILOT_DISPATCH_MAX_PER_SCAN", "3"))
ROLE_BOUNDARY_MODE = os.environ.get("AUTOPILOT_ROLE_BOUNDARY_MODE", "enforce").strip().lower()
LOG = logging.getLogger("autopilot")
USE_KQUEUE = (sys.platform == "darwin") and (os.environ.get("AUTOPILOT_USE_KQUEUE", "1") != "0")


@dataclass
class SessionPaths:
    main_worktree: Path
    session_root: Path
    shared: Path
    roles: Path
    artifacts: Path
    bus: Path
    state: Path


def init_logging() -> None:
    if LOG.handlers:
        return
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [autopilot] %(message)s"))
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
        shared=root / "shared",
        roles=root / "roles",
        artifacts=root / "artifacts",
        bus=root / "bus",
        state=root / "state",
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


def new_id(prefix: str = "") -> str:
    import secrets

    ts = time.strftime("%Y%m%d-%H%M%S")
    return f"{prefix}{ts}-{secrets.token_hex(3)}"


def _normalize_list(items: object) -> List[str]:
    out: List[str] = []
    if not isinstance(items, list):
        return out
    for x in items:
        s = str(x).strip()
        if s:
            out.append(s)
    return out


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
    task_id: str = "",
    acceptance: Optional[List[str]] = None,
) -> Path:
    mid = mid or new_id("")
    out = sp.bus / "inbox" / to_role / f"{mid}.md"
    lines = [
        "---",
        f"id: {mid}",
        f"from: {from_role}",
        f"to: {to_role}",
        f"intent: {intent}",
        f"thread: {thread}",
        f"risk: {risk}",
    ]
    if task_id.strip():
        lines.append(f"task_id: {task_id.strip()}")
    acc = _normalize_list(acceptance)
    if acc:
        lines.append("acceptance:")
        for a in acc:
            aa = a.replace('"', "'")
            lines.append(f'  - "{aa}"')
    lines.extend(["---", "", body.rstrip(), ""])
    atomic_write(out, "\n".join(lines))
    return out


def list_roles(sp: SessionPaths) -> List[str]:
    roles: List[str] = []
    if sp.roles.is_dir():
        for d in sp.roles.iterdir():
            if d.is_dir():
                roles.append(d.name)
    return [r for r in ROLE_ORDER if r in roles]


def parse_role_worktrees(session_md: Path) -> Dict[str, Path]:
    if not session_md.exists():
        return {}
    text = read_text(session_md)
    m = re.search(r"^## Role worktrees\s*$", text, flags=re.M)
    if not m:
        return {}
    start = m.end()
    section = text[start:].split("\n## ", 1)[0]
    out: Dict[str, Path] = {}
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        line = line[2:]
        if ":" not in line:
            continue
        role, path = line.split(":", 1)
        role = role.strip()
        path = path.strip()
        if role and path:
            out[role] = Path(path).resolve()
    return out


class DirLock:
    def __init__(self, lock_dir: Path, poll_s: float = 0.2, timeout_s: float = 60.0):
        self.lock_dir = lock_dir
        self.poll_s = poll_s
        self.timeout_s = timeout_s

    def __enter__(self):
        mkdirp(self.lock_dir.parent)
        start = time.time()
        while True:
            try:
                os.mkdir(self.lock_dir)
                (self.lock_dir / "pid").write_text(str(os.getpid()), encoding="utf-8")
                return self
            except FileExistsError:
                if time.time() - start > self.timeout_s:
                    raise TimeoutError(f"lock timeout: {self.lock_dir}")
                time.sleep(self.poll_s)

    def __exit__(self, exc_type, exc, tb):
        try:
            for p in self.lock_dir.glob("*"):
                try:
                    p.unlink()
                except Exception:
                    pass
            os.rmdir(self.lock_dir)
        except Exception:
            pass


def ensure_session_dirs(sp: SessionPaths, roles: List[str]) -> None:
    # Best-effort mkdir to keep workers robust.
    mkdirp(sp.artifacts / "locks")
    mkdirp(sp.artifacts / "autopilot")
    mkdirp(sp.bus / "inbox")
    mkdirp(sp.bus / "outbox")
    mkdirp(sp.bus / "deadletter")
    mkdirp(sp.state / "processing")
    mkdirp(sp.state / "done")
    mkdirp(sp.state / "archive")
    mkdirp(sp.state / "tasks")
    mkdirp(sp.state / "memory")
    ensure_task_board(sp.session_root)
    for r in roles:
        mkdirp(sp.bus / "inbox" / r)
        mkdirp(sp.bus / "deadletter" / r)
        mkdirp(sp.state / "archive" / r)


def _count_md_files(path: Path) -> int:
    try:
        return sum(1 for p in path.glob("*.md") if p.is_file())
    except Exception:
        return 0


def _current_task_id(sp: SessionPaths, role: str) -> str:
    try:
        for task in list_tasks(sp.session_root):
            if str(task.get("owner", "")).strip() != role:
                continue
            if str(task.get("status", "")).strip() != "in_progress":
                continue
            tid = str(task.get("id", "")).strip()
            if tid:
                return tid
    except Exception:
        return ""
    return ""


def _use_global_lock() -> bool:
    """
    Claude-style team mode favors real parallelism. The original implementation
    defaulted to a global lock for safety; keep it opt-in via env to preserve
    both modes.
    """
    raw = (os.environ.get("AUTOPILOT_GLOBAL_LOCK") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    # Default: no global serialization.
    return False


ROLE_MEMORY_MAX_BYTES = int(os.environ.get("AUTOPILOT_ROLE_MEMORY_MAX_BYTES", "65536"))
ROLE_MEMORY_PROMPT_LINES = int(os.environ.get("AUTOPILOT_ROLE_MEMORY_PROMPT_LINES", "40"))


def _role_memory_path(sp: SessionPaths, role: str) -> Path:
    return sp.state / "memory" / f"{role}.md"


def read_recent_role_memory(sp: SessionPaths, role: str) -> str:
    """
    Return a tail slice of role-local memory for prompt continuity.
    Stored under sessions/<sid>/state/memory/<role>.md (append-only-ish).
    """
    path = _role_memory_path(sp, role)
    try:
        if not path.exists():
            return ""
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = lines[-ROLE_MEMORY_PROMPT_LINES:] if len(lines) > ROLE_MEMORY_PROMPT_LINES else lines
        return "\n".join(tail).strip()
    except Exception:
        return ""


def append_role_memory(
    sp: SessionPaths,
    *,
    session: str,
    role: str,
    mid: str,
    task_id: str,
    intent: str,
    status: str,
    codex_rc: int,
    summary: str,
) -> None:
    """
    Append a compact line-oriented memory record and keep the file bounded.
    """
    path = _role_memory_path(sp, role)
    mkdirp(path.parent)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    tid = task_id.strip() or "-"
    it = intent.strip() or "-"
    one = (summary.strip().splitlines() or [""])[0].strip()
    if len(one) > 160:
        one = one[:160] + "..."
    rec = f"- {ts} session={session} mid={mid} task_id={tid} intent={it} status={status} rc={codex_rc} :: {one}\n"
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(rec)
    except Exception:
        return
    try:
        if path.stat().st_size <= ROLE_MEMORY_MAX_BYTES:
            return
        # Trim to the last ~ROLE_MEMORY_PROMPT_LINES*2 lines when oversized.
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        keep = max(ROLE_MEMORY_PROMPT_LINES * 2, 80)
        tail = lines[-keep:] if len(lines) > keep else lines
        path.write_text("\n".join(tail).strip() + "\n", encoding="utf-8")
    except Exception:
        return


def log_heartbeat(sp: SessionPaths, session: str, role: str, poll_s: float) -> None:
    inbox_count = _count_md_files(sp.bus / "inbox" / role)
    outbox_count = _count_md_files(sp.bus / "outbox")
    cur_task = _current_task_id(sp, role) or "-"
    global_lock = "1" if _use_global_lock() else "0"
    LOG.info(
        "heartbeat session=%s role=%s pid=%s poll_s=%s inbox_count=%s outbox_count=%s current_task_id=%s global_lock=%s",
        session,
        role,
        os.getpid(),
        poll_s,
        inbox_count,
        outbox_count,
        cur_task,
        global_lock,
    )


def parse_frontmatter(md: str) -> Tuple[Dict[str, object], str]:
    """
    Minimal YAML frontmatter parser for this repo's message format.
    Supports:
    - key: value
    - acceptance: list with `  - "..."` lines
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


def _read_codex_config_model() -> str:
    cfg = Path.home() / ".codex" / "config.toml"
    if not cfg.exists():
        return ""
    try:
        text = cfg.read_text(encoding="utf-8")
    except Exception:
        return ""
    m = re.search(r'^\s*model\s*=\s*"([^"]+)"\s*$', text, flags=re.M)
    return m.group(1).strip() if m else ""


def _read_codex_config_model_provider() -> str:
    cfg = Path.home() / ".codex" / "config.toml"
    if not cfg.exists():
        return ""
    try:
        text = cfg.read_text(encoding="utf-8")
    except Exception:
        return ""
    m = re.search(r'^\s*model_provider\s*=\s*"([^"]+)"\s*$', text, flags=re.M)
    return m.group(1).strip() if m else ""


def _read_codex_models_cache() -> List[Dict[str, object]]:
    path = Path.home() / ".codex" / "models_cache.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    models = data.get("models")
    return models if isinstance(models, list) else []


def choose_model(cli_model: str = "") -> str:
    """
    Pick a model that exists for this machine/account.
    - Prefer explicit CLI arg.
    - Else respect env overrides.
    - Else validate ~/.codex/config.toml's model against ~/.codex/models_cache.json.
    - Else fall back to a known-good codex model if present in cache.
    """
    if cli_model:
        return cli_model

    env_model = (os.environ.get("CODEX_AUTOPILOT_MODEL") or os.environ.get("CODEX_MODEL") or "").strip()
    if env_model:
        return env_model

    cfg_model = _read_codex_config_model()
    cfg_provider = _read_codex_config_model_provider()
    cache = _read_codex_models_cache()
    slugs = {str(m.get("slug", "")).strip(): m for m in cache if isinstance(m, dict)}

    # If user configured a custom provider, allow unlisted models (often not in OpenAI model cache).
    # This enables setups like Azure deployments or OpenAI-compatible chat endpoints.
    if cfg_model and cfg_provider and cfg_provider != "openai":
        return cfg_model

    # Allow unlisted GLM-style model names even under openai provider, as long as the user
    # intentionally set it in ~/.codex/config.toml.
    if cfg_model and cfg_model.lower().startswith("glm"):
        return cfg_model

    if cfg_model and cfg_model in slugs:
        return cfg_model

    # Prefer a stable codex model from cache.
    for s in DEFAULT_FALLBACK_MODELS:
        if s in slugs:
            return s

    # Otherwise pick the highest-priority listed codex-ish model.
    best = ""
    best_pri = -10**9
    for s, meta in slugs.items():
        if "codex" not in s:
            continue
        try:
            pri = int(meta.get("priority", 0))
        except Exception:
            pri = 0
        if pri > best_pri:
            best_pri = pri
            best = s
    if best:
        return best

    # Last resort: return config even if invalid; codex will error clearly.
    return cfg_model


def inbox_dir(sp: SessionPaths, role: str) -> Path:
    return sp.bus / "inbox" / role


def message_files(sp: SessionPaths, role: str) -> List[Path]:
    d = inbox_dir(sp, role)
    if not d.is_dir():
        return []
    return sorted([p for p in d.glob("*.md") if p.is_file()], key=lambda p: p.name)


def message_id(msg_path: Path, front: Dict[str, object]) -> str:
    mid = str(front.get("id", "")).strip()
    if mid:
        return mid
    return msg_path.stem


def done_sentinel(sp: SessionPaths, mid: str, role: str) -> Path:
    return sp.state / "done" / f"{mid}.{role}.ok"


def processing_lock(sp: SessionPaths, mid: str, role: str) -> Path:
    return sp.state / "processing" / f"{mid}.{role}.lockdir"


def archive_path(sp: SessionPaths, role: str, msg_path: Path) -> Path:
    return sp.state / "archive" / role / msg_path.name


def deadletter_path(sp: SessionPaths, role: str, msg_path: Path) -> Path:
    return sp.bus / "deadletter" / role / msg_path.name


def load_json(path: Path) -> Dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_json(path: Path, data: Dict) -> None:
    mkdirp(path.parent)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Treat as alive; we can't inspect it but we also shouldn't steal its lock.
        return True
    except Exception:
        return False


def _cleanup_lockdir(lock_dir: Path) -> None:
    if not lock_dir.exists():
        return
    pid_file = lock_dir / "pid"
    try:
        st = os.lstat(pid_file)
        if stat.S_ISREG(st.st_mode):
            pid_file.unlink(missing_ok=True)
    except Exception:
        pass
    try:
        os.rmdir(lock_dir)
        return
    except Exception:
        pass
    # Avoid traversing potentially corrupt lock dirs; quarantine with atomic rename.
    try:
        stale_root = lock_dir.parent / "_stale_lockdirs"
        mkdirp(stale_root)
        target = stale_root / f"{lock_dir.name}.{int(time.time())}.{os.getpid()}"
        os.rename(lock_dir, target)
        return
    except Exception:
        pass
    try:
        subprocess.run(
            ["/bin/rm", "-rf", str(lock_dir)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
    except Exception:
        pass


def _read_lock_pid(lock_dir: Path) -> int:
    pid_file = lock_dir / "pid"
    try:
        st = os.lstat(pid_file)
        if not stat.S_ISREG(st.st_mode):
            return 0
        raw = pid_file.read_text(encoding="utf-8").strip()
        return int(raw) if raw else 0
    except Exception:
        return 0


def _lock_age_seconds(lock_dir: Path) -> float:
    try:
        return max(0.0, time.time() - lock_dir.stat().st_mtime)
    except Exception:
        return float("inf")


def _is_placeholder_text(s: str) -> bool:
    t = s.strip().lower()
    if not t:
        return True
    placeholders = {
        "(fill)",
        "fill",
        "todo",
        "tbd",
        "待补充",
        "待填写",
        "未填写",
        "n/a",
    }
    return t in placeholders


def _extract_task_objective(task_md: str) -> str:
    lines = task_md.splitlines()

    # 1) Prefer explicit objective fields.
    for raw in lines:
        s = raw.strip()
        m = re.match(r"^[-*]?\s*(目标|objective)[^:：]*[:：]\s*(.+)$", s, flags=re.I)
        if m:
            v = m.group(2).strip()
            if v and not _is_placeholder_text(v):
                return v

    # 2) Fallback to # Task section first meaningful line.
    in_task = False
    for raw in lines:
        s = raw.strip()
        if re.match(r"^#\s*task\b", s, flags=re.I):
            in_task = True
            continue
        if in_task and re.match(r"^##\s+", s):
            break
        if not in_task:
            continue
        if not s:
            continue
        if s.startswith("|"):
            continue
        if re.match(r"^[-*]\s+[^:：]+[:：]\s*$", s):
            # Template placeholder lines like "- 目标（Objective）："
            continue
        if _is_placeholder_text(s):
            continue
        return s
    return ""


def _extract_acceptance_lines(task_md: str) -> List[str]:
    lines = task_md.splitlines()
    in_sec = False
    out: List[str] = []
    for raw in lines:
        s = raw.strip()
        if re.match(r"^##\s*(acceptance|验收标准)\b", s, flags=re.I):
            in_sec = True
            continue
        if in_sec and re.match(r"^##\s+", s):
            break
        if not in_sec:
            continue
        m = re.match(r"^[-*]\s+(.+)$", s)
        if not m:
            m = re.match(r"^\d+[.)]\s+(.+)$", s)
        if not m:
            continue
        v = m.group(1).strip()
        if not v or _is_placeholder_text(v):
            continue
        out.append(v)
    return out


def _infer_work_type(task_text: str) -> str:
    t = task_text.lower()
    if "重构" in task_text or "refactor" in t:
        return "refactor"
    if "修复" in task_text or "fix" in t or "bug" in t:
        return "fix"
    if "文档" in task_text or "readme" in t or "docs" in t:
        return "docs"
    if "测试" in task_text or "test" in t:
        return "test"
    return "implement"


def _infer_risk(task_text: str) -> str:
    t = task_text.lower()
    if "high" in t or "高" in task_text:
        return "high"
    if "medium" in t or "中" in task_text:
        return "medium"
    return "low"


def _format_task_message(task: Dict[str, object]) -> str:
    tid = str(task.get("id", "")).strip()
    title = str(task.get("title", "")).strip() or "(untitled)"
    acc = _normalize_list(task.get("acceptance"))
    lines = [f"[Task {tid}] {title}"]
    if acc:
        lines.append("")
        lines.append("Acceptance:")
        for a in acc:
            lines.append(f"- {a}")
    return "\n".join(lines)


def _format_task_context(sp: SessionPaths, task_id: str, *, max_receipts: int = 3) -> str:
    tid = str(task_id or "").strip()
    if not tid:
        return ""

    t = get_task(sp.session_root, tid)
    if not isinstance(t, dict):
        return f"(task not found in task board: {tid})"

    core = {
        "id": str(t.get("id", "")).strip(),
        "title": str(t.get("title", "")).strip(),
        "status": str(t.get("status", "")).strip(),
        "owner": str(t.get("owner", "")).strip(),
        "claimed_by": str(t.get("claimed_by", "")).strip(),
        "depends_on": _normalize_list(t.get("depends_on")),
        "acceptance": _normalize_list(t.get("acceptance")),
        "dispatch": t.get("dispatch") if isinstance(t.get("dispatch"), dict) else {},
    }

    # Attach minimal recent receipts for this task id (improves cross-message coherence).
    receipts: List[Dict[str, str]] = []
    outbox = sp.bus / "outbox"
    if outbox.is_dir():
        files = sorted([p for p in outbox.glob("*.md") if p.is_file()], key=lambda p: p.stat().st_mtime, reverse=True)
        for p in files:
            if len(receipts) >= max_receipts:
                break
            try:
                raw = read_text(p)
            except Exception:
                continue
            fm, _ = parse_frontmatter(raw)
            rid = str(fm.get("id", "")).strip() or p.stem
            rtid = str(fm.get("task_id", "")).strip()
            if rtid != tid:
                continue
            receipts.append(
                {
                    "receipt_id": rid,
                    "role": str(fm.get("role", "")).strip(),
                    "status": str(fm.get("status", "")).strip(),
                    "codex_rc": str(fm.get("codex_rc", "")).strip(),
                    "finished_at": str(fm.get("finished_at", "")).strip(),
                    "file": str(p),
                }
            )

    parts = [
        "Task context (task-board + recent receipts):",
        "",
        "Task (summary):",
        "```json",
        json.dumps(core, ensure_ascii=False, indent=2),
        "```",
    ]
    if receipts:
        parts.append("")
        parts.append(f"Recent receipts for task {tid} (newest first):")
        for r in receipts:
            parts.append(
                f"- {r.get('receipt_id','?')} role={r.get('role','?')} status={r.get('status','?')} "
                f"rc={r.get('codex_rc','?')} at={r.get('finished_at','?')} file={r.get('file','?')}"
            )
    return "\n".join(parts).strip()


def dispatch_ready_tasks(
    sp: SessionPaths,
    *,
    session: str,
    roles: List[str],
    from_role: str = "system",
    owner: str = "",
) -> List[str]:
    sent: List[str] = []
    role_set = set(roles)
    for task in list_dispatchable_tasks(sp.session_root, owner=owner):
        if DISPATCH_MAX_PER_SCAN > 0 and len(sent) >= DISPATCH_MAX_PER_SCAN:
            break
        tid = str(task.get("id", "")).strip()
        to_role = str(task.get("owner", "")).strip()
        if not tid or not to_role or to_role not in role_set:
            continue
        mid = new_id("")
        msg_path = enqueue_bus_message(
            sp,
            to_role=to_role,
            from_role=from_role,
            intent=str(task.get("intent", "")).strip() or "implement",
            thread=session,
            risk=str(task.get("risk", "")).strip() or "low",
            body=_format_task_message(task),
            mid=mid,
            task_id=tid,
            acceptance=_normalize_list(task.get("acceptance")),
        )
        ok, _, reason = set_dispatch(
            sp.session_root,
            task_id=tid,
            from_role=from_role,
            to_role=to_role,
            intent=str(task.get("intent", "")).strip() or "implement",
            message_id=mid,
        )
        if not ok:
            try:
                msg_path.unlink(missing_ok=True)
            except Exception:
                pass
            if reason in ("already_dispatched", "already_dispatched_same"):
                continue
            continue
        sent.append(f"{tid}->{to_role}({mid})")
    return sent


def run_lead_bootstrap(
    sp: SessionPaths,
    *,
    session: str,
    roles: List[str],
    source_message_id: str,
) -> str:
    task_md = sp.shared / "task.md"
    if not task_md.exists():
        return "Bootstrap blocked: shared/task.md not found."

    task_text = read_text(task_md).strip()
    objective = _extract_task_objective(task_text)
    acceptance = _extract_acceptance_lines(task_text)
    if not objective:
        return "Bootstrap blocked: shared/task.md has no actionable objective."
    if not acceptance:
        acceptance = ["Provide reproducible verification evidence in outbox/verify."]

    existing = [
        t
        for t in list_tasks(sp.session_root)
        if str(t.get("source_message_id", "")).strip() == source_message_id
    ]
    created_ids: List[str] = []
    if not existing:
        builder_role = "builder-a" if "builder-a" in roles else ("builder-b" if "builder-b" in roles else "")
        if not builder_role and roles:
            builder_role = roles[0]
        work_type = _infer_work_type(task_text)
        risk = _infer_risk(task_text)
        impl = add_task(
            sp.session_root,
            title=objective,
            created_by="lead",
            owner=builder_role,
            work_type=work_type,
            risk=risk,
            acceptance=acceptance,
            depends_on=[],
            intent="implement",
            source_message_id=source_message_id,
        )
        created_ids.append(str(impl.get("id", "")).strip())

        impl_id = str(impl.get("id", "")).strip()
        if "reviewer" in roles:
            rv = add_task(
                sp.session_root,
                title=f"Review: {objective}",
                created_by="lead",
                owner="reviewer",
                work_type="review",
                risk=risk,
                acceptance=["Write merge recommendation and risk notes in shared/decision.md."],
                depends_on=[impl_id],
                intent="review",
                source_message_id=source_message_id,
            )
            created_ids.append(str(rv.get("id", "")).strip())
        if "tester" in roles:
            tv = add_task(
                sp.session_root,
                title=f"Test: {objective}",
                created_by="lead",
                owner="tester",
                work_type="test",
                risk=risk,
                acceptance=acceptance,
                depends_on=[impl_id],
                intent="test",
                source_message_id=source_message_id,
            )
            created_ids.append(str(tv.get("id", "")).strip())

    sent = dispatch_ready_tasks(sp, session=session, roles=roles, from_role="lead")
    lines = ["Bootstrap planner done."]
    if created_ids:
        lines.append("Created tasks: " + ", ".join(created_ids))
    else:
        lines.append("Tasks already existed for this bootstrap message.")
    if sent:
        lines.append("Dispatched: " + ", ".join(sent))
    else:
        lines.append("No dispatchable tasks at this moment.")
    return "\n".join(lines)


def build_role_prompt(sp: SessionPaths, session: str, role: str, msg_path: Path, msg_body: str, *, task_id: str = "") -> str:
    role_prompt_path = sp.roles / role / "prompt.md"
    base = read_text(role_prompt_path).strip() if role_prompt_path.exists() else f"You are {role}."
    mem = read_recent_role_memory(sp, role=role)
    mem_block = ""
    if mem:
        mem_block = f"""

Recent role memory (tail; do not treat as authoritative requirements):
```md
{mem}
```
"""
    task_block = ""
    if task_id.strip():
        task_ctx = _format_task_context(sp, task_id.strip(), max_receipts=3)
        if task_ctx:
            task_block = f"""

{task_ctx}
"""

    extra = f"""

You are running under Autopilot (message bus mode) on macOS.

Session root (shared truth): {sp.session_root}
Message file to process: {msg_path}

Rules:
- Do not ask the human for input.
- If you need clarification or want to hand off work, either:
  - emit a directive in your final message (router will execute it):
    ::bus-send{{to="lead" intent="question" risk="low" message="..." }}
  - or (fallback) send a bus message manually:
    ./scripts/bus-send.sh --session {session} --from {role} --to <role> --intent question --message "<...>"
- Do not process messages outside your role.
- Prefer writing results to your role outbox/worklog. Only write shared files if your role is the owner per prompt.
 - If you want Reviewer/Tester to act next, include ::bus-send directives (router will deliver them).
{mem_block}
{task_block}

Task:
Read the message file content below and execute it end-to-end (code changes + verification + writeback).

Message content:
```md
{msg_body.strip()}
```
"""
    return base + "\n" + extra.strip() + "\n"


def _utf8_safe_env() -> Tuple[Dict[str, str], List[str]]:
    """
    Build an env dict safe for Rust CLIs that call std::env::vars().
    Some local shells can carry non-UTF8 bytes (decoded by Python with surrogates),
    which can panic downstream Rust code when inherited.
    """
    env: Dict[str, str] = {}
    dropped: List[str] = []
    for k, v in os.environ.items():
        try:
            k.encode("utf-8")
            v.encode("utf-8")
        except UnicodeEncodeError:
            dropped.append(k)
            continue
        env[k] = v
    return env, dropped


def _git_changed_paths(repo_dir: Path) -> List[str]:
    """
    Return a stable list of paths that are changed/untracked in the given git worktree.
    Best-effort: on failure returns [] (does not raise).
    """
    try:
        p = subprocess.run(
            ["git", "-C", str(repo_dir), "status", "--porcelain=v1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=10,
        )
        if p.returncode != 0:
            return []
        out: List[str] = []
        for ln in p.stdout.splitlines():
            ln = ln.rstrip("\n")
            if not ln:
                continue
            # Format: XY<space>path (or "R  old -> new").
            if len(ln) < 4:
                continue
            rest = ln[3:].strip()
            if " -> " in rest:
                rest = rest.split(" -> ", 1)[1].strip()
            if rest:
                out.append(rest)
        return sorted(set(out))
    except Exception:
        return []


def _role_allows_repo_writes(role: str) -> bool:
    return role in ("builder-a", "builder-b")


def _enforce_role_boundary(role: str, *, baseline: List[str], after: List[str]) -> Tuple[bool, str]:
    """
    Enforce hard role boundaries at the worktree level (not session root).
    For non-builder roles: disallow NEW repo changes during this message handling.
    """
    mode = ROLE_BOUNDARY_MODE
    if mode in ("0", "off", "false", "disabled"):
        return True, "boundary_mode=off"
    if _role_allows_repo_writes(role):
        return True, "boundary_ok(builder)"

    before_set = set(baseline or [])
    after_set = set(after or [])
    new_paths = sorted(after_set - before_set)
    if not new_paths:
        return True, "boundary_ok(no_new_repo_changes)"

    msg = f"role_boundary_violation role={role} new_paths={new_paths[:50]}"
    if len(new_paths) > 50:
        msg += f" (+{len(new_paths) - 50} more)"

    if mode in ("1", "enforce", "strict"):
        return False, msg
    # warn/default: do not fail the run, but record evidence.
    LOG.error("BOUNDARY warn %s", msg)
    return True, "boundary_warn"


class RoleBoundaryError(RuntimeError):
    pass


def codex_exec(role_cwd: Path, prompt: str, out_last: Path, *, model: str, add_dirs: List[Path]) -> int:
    mkdirp(out_last.parent)
    env, dropped = _utf8_safe_env()
    if dropped:
        LOG.warning(
            "dropped_non_utf8_env_vars vars=%s",
            ",".join(sorted(dropped)),
        )
    env["PWD"] = str(role_cwd)
    cmd = [
        "codex",
        "-a",
        "never",
        "exec",
        "-s",
        "workspace-write",
        "-m",
        model,
    ]
    for d in add_dirs:
        cmd.extend(["--add-dir", str(d)])
    cmd += [
        "--cd",
        str(role_cwd),
        "--output-last-message",
        str(out_last),
        "-",
    ]
    p = subprocess.run(cmd, input=prompt, text=True, env=env)
    return p.returncode


def write_receipt(
    sp: SessionPaths,
    mid: str,
    role: str,
    status: str,
    codex_rc: int,
    last_msg: str,
    *,
    thread: str,
    request_from: str,
    request_to: str,
    request_intent: str,
    task_id: str = "",
) -> None:
    out = sp.bus / "outbox" / f"{mid}.{role}.md"
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    header = [
        "---",
        f"id: {mid}",
        f"role: {role}",
        f'thread: "{thread}"',
        f'request_from: "{request_from}"',
        f'request_to: "{request_to}"',
        f'request_intent: "{request_intent}"',
    ]
    if task_id.strip():
        header.append(f'task_id: "{task_id.strip()}"')
    header.extend(
        [
            f"status: {status}",
            f"codex_rc: {codex_rc}",
            f'finished_at: "{ts}"',
            "---",
            "",
            last_msg.strip(),
            "",
        ]
    )
    text = "\n".join(header)
    out.write_text(text, encoding="utf-8")


def process_one(
    sp: SessionPaths,
    session: str,
    role: str,
    role_cwd: Path,
    dry_run: bool,
    *,
    model: str,
    runtime_state: Optional[Dict[str, str]] = None,
) -> bool:
    files = message_files(sp, role)
    if not files:
        return False

    selected: Optional[Dict[str, object]] = None
    task_id = ""
    task_claimed = False

    for msg_path in files:
        try:
            raw = read_text(msg_path)
        except Exception as e:
            mid = msg_path.stem
            mkdirp(deadletter_path(sp, role, msg_path).parent)
            msg_path.rename(deadletter_path(sp, role, msg_path))
            LOG.error(
                "message_read_failed session=%s role=%s mid=%s path=%s err=%s",
                session,
                role,
                mid,
                msg_path,
                e,
            )
            write_receipt(
                sp,
                mid,
                role,
                status="deadletter",
                codex_rc=98,
                last_msg=f"Unreadable message file: {e}",
                thread=session,
                request_from="",
                request_to=role,
                request_intent="",
            )
            return True

        front, _ = parse_frontmatter(raw)
        mid = message_id(msg_path, front)
        thread = str(front.get("thread", session)).strip() or session
        request_from = str(front.get("from", "")).strip()
        request_to = str(front.get("to", "")).strip()
        request_intent = str(front.get("intent", "")).strip()

        if done_sentinel(sp, mid, role).exists():
            mkdirp((sp.state / "archive" / role))
            msg_path.rename(archive_path(sp, role, msg_path))
            LOG.info("message_already_done session=%s role=%s mid=%s", session, role, mid)
            return True

        lock_dir = processing_lock(sp, mid, role)
        if lock_dir.exists():
            pid = _read_lock_pid(lock_dir)
            age_s = _lock_age_seconds(lock_dir)
            if pid > 0 and _pid_alive(pid) and age_s < LOCK_STALE_SECONDS:
                continue
            _cleanup_lockdir(lock_dir)
        try:
            os.mkdir(lock_dir)
            (lock_dir / "pid").write_text(str(os.getpid()), encoding="utf-8")
        except FileExistsError:
            continue

        task_id = str(front.get("task_id", "")).strip()
        if task_id:
            ok, _, reason = claim_task(sp.session_root, task_id=task_id, role=role, message_id=mid)
            if not ok:
                blocked = reason in ("owner_mismatch", "claimed_by_other") or reason.startswith("deps_blocked")
                if reason == "completed":
                    done_sentinel(sp, mid, role).write_text("ok\n", encoding="utf-8")
                    mkdirp((sp.state / "archive" / role))
                    msg_path.rename(archive_path(sp, role, msg_path))
                    _cleanup_lockdir(lock_dir)
                    LOG.info("task_already_completed session=%s role=%s task_id=%s mid=%s", session, role, task_id, mid)
                    return True
                if blocked:
                    _cleanup_lockdir(lock_dir)
                    LOG.info("task_blocked session=%s role=%s task_id=%s mid=%s reason=%s", session, role, task_id, mid, reason)
                    continue
                # If the task board is missing/invalid for this message, continue execution
                # as message-only mode instead of dropping work.
                task_id = ""
            else:
                task_claimed = True

        selected = {
            "msg_path": msg_path,
            "raw": raw,
            "mid": mid,
            "thread": thread,
            "request_from": request_from,
            "request_to": request_to,
            "request_intent": request_intent,
            "lock_dir": lock_dir,
            "task_id": task_id,
        }
        if runtime_state is not None:
            runtime_state["last_msg_id"] = mid
            runtime_state["last_task_id"] = task_id or "-"
            runtime_state["last_intent"] = request_intent or "-"
            runtime_state["last_path"] = str(msg_path)
        LOG.info(
            "message_selected session=%s role=%s msg_id=%s task_id=%s intent=%s path=%s",
            session,
            role,
            mid,
            task_id or "-",
            request_intent or "-",
            msg_path,
        )
        break

    if not selected:
        return False

    msg_path = selected["msg_path"]
    raw = selected["raw"]
    mid = selected["mid"]
    thread = selected["thread"]
    request_from = selected["request_from"]
    request_to = selected["request_to"]
    request_intent = selected["request_intent"]
    lock_dir = selected["lock_dir"]

    # Deterministic lead bootstrap: generate task graph + initial dispatch without model call.
    if role == "lead" and request_intent == "bootstrap":
        try:
            summary = run_lead_bootstrap(
                sp,
                session=session,
                roles=list_roles(sp),
                source_message_id=mid,
            )
            write_receipt(
                sp,
                mid,
                role,
                status="done",
                codex_rc=0,
                last_msg=summary,
                thread=thread,
                request_from=request_from,
                request_to=request_to,
                request_intent=request_intent,
                task_id=task_id,
            )
            append_role_memory(
                sp,
                session=session,
                role=role,
                mid=mid,
                task_id=task_id,
                intent=request_intent,
                status="done",
                codex_rc=0,
                summary=summary,
            )
            done_sentinel(sp, mid, role).write_text("ok\n", encoding="utf-8")
            mkdirp((sp.state / "archive" / role))
            msg_path.rename(archive_path(sp, role, msg_path))
            LOG.info("bootstrap_handled session=%s role=%s mid=%s", session, role, mid)
            return True
        finally:
            _cleanup_lockdir(lock_dir)

    retries_path = sp.state / "processing" / f"{mid}.{role}.retries.json"
    retries = load_json(retries_path)
    n = int(retries.get("count", 0))
    if n >= 3:
        mkdirp(deadletter_path(sp, role, msg_path).parent)
        msg_path.rename(deadletter_path(sp, role, msg_path))
        if task_id:
            mark_task_failed(
                sp.session_root,
                task_id=task_id,
                role=role,
                error="Exceeded max retries.",
                terminal=True,
            )
        write_receipt(
            sp,
            mid,
            role,
            status="deadletter",
            codex_rc=99,
            last_msg="Exceeded max retries.",
            thread=thread,
            request_from=request_from,
            request_to=request_to,
            request_intent=request_intent,
            task_id=task_id,
        )
        append_role_memory(
            sp,
            session=session,
            role=role,
            mid=mid,
            task_id=task_id,
            intent=request_intent,
            status="deadletter",
            codex_rc=99,
            summary="Exceeded max retries.",
        )
        _cleanup_lockdir(lock_dir)
        LOG.warning("message_deadlettered session=%s role=%s mid=%s retries=%s", session, role, mid, n)
        return True

    global_lock = sp.artifacts / "locks" / "autopilot.global.lockdir"
    last_msg_path = sp.artifacts / "autopilot" / f"{role}.{mid}.last.txt"
    receipt_path = sp.bus / "outbox" / f"{mid}.{role}.md"

    codex_rc = 0
    status = "done"
    last_msg = ""
    baseline_repo_paths: List[str] = []
    try:
        if not dry_run and not _role_allows_repo_writes(role):
            mode = ROLE_BOUNDARY_MODE
            if mode not in ("0", "off", "false", "disabled"):
                baseline_repo_paths = _git_changed_paths(role_cwd)
                if baseline_repo_paths:
                    LOG.warning(
                        "role_worktree_dirty_before_run session=%s role=%s count=%s paths=%s",
                        session,
                        role,
                        len(baseline_repo_paths),
                        baseline_repo_paths[:20],
                    )

        prompt = build_role_prompt(sp, session=session, role=role, msg_path=msg_path, msg_body=raw, task_id=task_id)
        if dry_run:
            last_msg = "DRY_RUN: skipped codex exec."
            LOG.info("dry_run_skip_codex session=%s role=%s mid=%s", session, role, mid)
        else:
            if _use_global_lock():
                # Recover stale global lock (crash-safe).
                if global_lock.exists():
                    pid = _read_lock_pid(global_lock)
                    age_s = _lock_age_seconds(global_lock)
                    if pid <= 0 or not _pid_alive(pid) or age_s >= LOCK_STALE_SECONDS:
                        _cleanup_lockdir(global_lock)
                with DirLock(global_lock, timeout_s=1800.0):
                    codex_rc = codex_exec(
                        role_cwd=role_cwd,
                        prompt=prompt,
                        out_last=last_msg_path,
                        model=model,
                        add_dirs=[sp.session_root],
                    )
            else:
                codex_rc = codex_exec(
                    role_cwd=role_cwd,
                    prompt=prompt,
                    out_last=last_msg_path,
                    model=model,
                    add_dirs=[sp.session_root],
                )
            LOG.info("codex_finished session=%s role=%s mid=%s rc=%s", session, role, mid, codex_rc)
            if last_msg_path.exists():
                last_msg = last_msg_path.read_text(encoding="utf-8")
            else:
                last_msg = "(no last message captured)"
            if codex_rc != 0:
                raise RuntimeError(f"codex rc={codex_rc}")

            # Hard boundary: non-builder roles should not produce repo changes.
            if baseline_repo_paths is not None and not _role_allows_repo_writes(role):
                mode = ROLE_BOUNDARY_MODE
                if mode not in ("0", "off", "false", "disabled"):
                    after_repo_paths = _git_changed_paths(role_cwd)
                    ok_boundary, boundary_msg = _enforce_role_boundary(
                        role,
                        baseline=baseline_repo_paths,
                        after=after_repo_paths,
                    )
                    if not ok_boundary:
                        raise RoleBoundaryError(boundary_msg)
        write_receipt(
            sp,
            mid,
            role,
            status=status,
            codex_rc=codex_rc,
            last_msg=last_msg,
            thread=thread,
            request_from=request_from,
            request_to=request_to,
            request_intent=request_intent,
            task_id=task_id,
        )
        append_role_memory(
            sp,
            session=session,
            role=role,
            mid=mid,
            task_id=task_id,
            intent=request_intent,
            status=status,
            codex_rc=codex_rc,
            summary=last_msg,
        )
        if task_id and task_claimed:
            complete_task(
                sp.session_root,
                task_id=task_id,
                role=role,
                evidence=f"message={mid}",
                receipt_file=str(receipt_path),
            )
            # Dependency-driven next-hop dispatch (task-state-machine mode).
            dispatch_ready_tasks(
                sp,
                session=session,
                roles=list_roles(sp),
                from_role="system",
            )
        done_sentinel(sp, mid, role).write_text("ok\n", encoding="utf-8")
        mkdirp((sp.state / "archive" / role))
        msg_path.rename(archive_path(sp, role, msg_path))
        LOG.info("message_done session=%s role=%s mid=%s status=%s", session, role, mid, status)
        return True
    except RoleBoundaryError as e:
        msg = str(e)
        if task_id and task_claimed:
            mark_task_failed(
                sp.session_root,
                task_id=task_id,
                role=role,
                error=msg,
                terminal=True,
            )
        write_receipt(
            sp,
            mid,
            role,
            status="deadletter",
            codex_rc=97,
            last_msg=f"Role boundary violation (terminal): {msg}",
            thread=thread,
            request_from=request_from,
            request_to=request_to,
            request_intent=request_intent,
            task_id=task_id,
        )
        append_role_memory(
            sp,
            session=session,
            role=role,
            mid=mid,
            task_id=task_id,
            intent=request_intent,
            status="deadletter",
            codex_rc=97,
            summary=f"Role boundary violation (terminal): {msg}",
        )
        mkdirp(deadletter_path(sp, role, msg_path).parent)
        try:
            msg_path.rename(deadletter_path(sp, role, msg_path))
        except Exception:
            pass
        LOG.error("role_boundary_deadletter session=%s role=%s mid=%s err=%s", session, role, mid, msg)
        return True
    except Exception as e:
        n += 1
        retries["count"] = n
        retries["last_error"] = str(e)
        retries["last_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_json(retries_path, retries)
        if task_id and task_claimed:
            mark_task_failed(
                sp.session_root,
                task_id=task_id,
                role=role,
                error=str(e),
                terminal=False,
            )
        write_receipt(
            sp,
            mid,
            role,
            status="retry",
            codex_rc=codex_rc,
            last_msg=f"Error: {e}",
            thread=thread,
            request_from=request_from,
            request_to=request_to,
            request_intent=request_intent,
            task_id=task_id,
        )
        append_role_memory(
            sp,
            session=session,
            role=role,
            mid=mid,
            task_id=task_id,
            intent=request_intent,
            status="retry",
            codex_rc=codex_rc,
            summary=f"Error: {e}",
        )
        LOG.error("message_retry session=%s role=%s mid=%s retry_count=%s err=%s", session, role, mid, n, e)
        return True
    finally:
        _cleanup_lockdir(lock_dir)


def daemon(session: str, role: str, poll_s: float, dry_run: bool, *, model: str) -> int:
    init_logging()
    rc = 0
    runtime_state: Dict[str, str] = {
        "last_msg_id": "-",
        "last_task_id": "-",
        "last_intent": "-",
        "last_path": "-",
    }
    signal_seen = {"signum": 0}
    prev_sigterm = None
    prev_sigint = None

    def _signal_handler(signum: int, _frame: object) -> None:
        signal_seen["signum"] = signum
        LOG.error(
            "SIGNAL received session=%s role=%s pid=%s signum=%s last_msg_id=%s last_task_id=%s last_intent=%s last_path=%s",
            session,
            role,
            os.getpid(),
            signum,
            runtime_state.get("last_msg_id", "-"),
            runtime_state.get("last_task_id", "-"),
            runtime_state.get("last_intent", "-"),
            runtime_state.get("last_path", "-"),
        )
        LOG.error("SIGNAL process_context %s", _signal_process_context(session=session, role=role))
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

        roles = list_roles(sp)
        if role not in roles:
            LOG.error("role_not_found session=%s role=%s", session, role)
            rc = 2
            return rc

        ensure_session_dirs(sp, roles=roles)

        worktrees = parse_role_worktrees(sp.session_root / "SESSION.md")
        role_cwd = worktrees.get(role, sp.main_worktree)
        LOG.info(
            "daemon_start session=%s role=%s pid=%s poll_s=%s model=%s dry_run=%s cwd=%s",
            session,
            role,
            os.getpid(),
            poll_s,
            model,
            dry_run,
            role_cwd,
        )

        next_hb = 0.0
        next_dispatch = 0.0
        while True:
            now = time.monotonic()
            if now >= next_hb:
                log_heartbeat(sp, session=session, role=role, poll_s=poll_s)
                next_hb = now + HEARTBEAT_SECONDS
            if now >= next_dispatch:
                if role == "lead":
                    try:
                        sent = dispatch_ready_tasks(
                            sp,
                            session=session,
                            roles=roles,
                            from_role="lead",
                        )
                        if sent:
                            LOG.info("lead_periodic_dispatch session=%s sent=%s", session, ",".join(sent))
                    except Exception:
                        LOG.exception("lead_periodic_dispatch_failed session=%s", session)
                else:
                    # Self-claim fallback (Claude-like): if lead is down, each role can
                    # still pick up dispatchable tasks owned by itself.
                    try:
                        inbox_count = _count_md_files(sp.bus / "inbox" / role)
                        if inbox_count == 0:
                            sent = dispatch_ready_tasks(
                                sp,
                                session=session,
                                roles=roles,
                                from_role=role,
                                owner=role,
                            )
                            if sent:
                                LOG.info("role_self_dispatch session=%s role=%s sent=%s", session, role, ",".join(sent))
                    except Exception:
                        LOG.exception("role_self_dispatch_failed session=%s role=%s", session, role)
                next_dispatch = now + DISPATCH_SCAN_SECONDS
            did = process_one(
                sp,
                session=session,
                role=role,
                role_cwd=role_cwd,
                dry_run=dry_run,
                model=model,
                runtime_state=runtime_state,
            )
            if not did:
                _wait_for_dir_change(inbox_dir(sp, role), poll_s)
    except KeyboardInterrupt:
        rc = 130
        LOG.error(
            "EXIT by KeyboardInterrupt session=%s role=%s pid=%s last_msg_id=%s last_task_id=%s last_intent=%s last_path=%s",
            session,
            role,
            os.getpid(),
            runtime_state.get("last_msg_id", "-"),
            runtime_state.get("last_task_id", "-"),
            runtime_state.get("last_intent", "-"),
            runtime_state.get("last_path", "-"),
        )
    except SystemExit as e:
        rc = int(e.code) if isinstance(e.code, int) else 1
        LOG.error(
            "EXIT by SystemExit session=%s role=%s pid=%s rc=%s signal=%s last_msg_id=%s last_task_id=%s last_intent=%s last_path=%s",
            session,
            role,
            os.getpid(),
            rc,
            signal_seen.get("signum", 0),
            runtime_state.get("last_msg_id", "-"),
            runtime_state.get("last_task_id", "-"),
            runtime_state.get("last_intent", "-"),
            runtime_state.get("last_path", "-"),
        )
    except Exception:
        rc = 2
        LOG.exception(
            "FATAL: daemon crashed session=%s role=%s pid=%s last_msg_id=%s last_task_id=%s last_intent=%s last_path=%s",
            session,
            role,
            os.getpid(),
            runtime_state.get("last_msg_id", "-"),
            runtime_state.get("last_task_id", "-"),
            runtime_state.get("last_intent", "-"),
            runtime_state.get("last_path", "-"),
        )
    finally:
        if prev_sigterm is not None:
            signal.signal(signal.SIGTERM, prev_sigterm)
        if prev_sigint is not None:
            signal.signal(signal.SIGINT, prev_sigint)
        LOG.error(
            "EXIT rc=%s session=%s role=%s pid=%s signal=%s last_msg_id=%s last_task_id=%s last_intent=%s last_path=%s",
            rc,
            session,
            role,
            os.getpid(),
            signal_seen.get("signum", 0),
            runtime_state.get("last_msg_id", "-"),
            runtime_state.get("last_task_id", "-"),
            runtime_state.get("last_intent", "-"),
            runtime_state.get("last_path", "-"),
        )
        _flush_log_handlers()
    return rc


def run_once(session: str, role: str, dry_run: bool, *, model: str) -> int:
    init_logging()
    start_dir = Path.cwd()
    main = git_main_worktree(start_dir)
    sp = session_paths(main, session)
    roles = list_roles(sp)
    ensure_session_dirs(sp, roles=roles)
    worktrees = parse_role_worktrees(sp.session_root / "SESSION.md")
    role_cwd = worktrees.get(role, sp.main_worktree)
    LOG.info("run_once_start session=%s role=%s pid=%s model=%s dry_run=%s", session, role, os.getpid(), model, dry_run)
    log_heartbeat(sp, session=session, role=role, poll_s=0.0)
    did = process_one(sp, session=session, role=role, role_cwd=role_cwd, dry_run=dry_run, model=model)
    LOG.info("run_once_finish session=%s role=%s processed=%s", session, role, did)
    return 0 if did else 3


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("daemon", help="Run a single role loop (blocking).")
    d.add_argument("--session", required=True)
    d.add_argument("--role", required=True)
    d.add_argument("--poll", type=float, default=2.0)
    d.add_argument("--dry-run", action="store_true")
    d.add_argument("--model", default="", help="Codex model override (default: auto-detect).")

    o = sub.add_parser("once", help="Process at most one message and exit.")
    o.add_argument("--session", required=True)
    o.add_argument("--role", required=True)
    o.add_argument("--dry-run", action="store_true")
    o.add_argument("--model", default="", help="Codex model override (default: auto-detect).")

    args = ap.parse_args()
    model = choose_model(args.model)
    if args.cmd == "daemon":
        return daemon(session=args.session, role=args.role, poll_s=args.poll, dry_run=args.dry_run, model=model)
    if args.cmd == "once":
        return run_once(session=args.session, role=args.role, dry_run=args.dry_run, model=model)
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
