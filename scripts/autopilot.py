#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


ROLE_ORDER = ["lead", "builder-a", "builder-b", "reviewer", "tester"]
DEFAULT_FALLBACK_MODELS = ["gpt-5.2-codex", "gpt-5.2", "gpt-5.1-codex-max"]


@dataclass
class SessionPaths:
    main_worktree: Path
    session_root: Path
    shared: Path
    roles: Path
    artifacts: Path
    bus: Path
    state: Path


def _run(cmd: List[str], cwd: Optional[Path] = None) -> str:
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
    for r in roles:
        mkdirp(sp.bus / "inbox" / r)
        mkdirp(sp.bus / "deadletter" / r)
        mkdirp(sp.state / "archive" / r)


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


def next_message_file(sp: SessionPaths, role: str) -> Optional[Path]:
    d = inbox_dir(sp, role)
    if not d.is_dir():
        return None
    files = sorted([p for p in d.glob("*.md") if p.is_file()], key=lambda p: p.name)
    return files[0] if files else None


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
    try:
        for p in lock_dir.glob("*"):
            try:
                p.unlink()
            except Exception:
                pass
        os.rmdir(lock_dir)
    except Exception:
        pass


def build_role_prompt(sp: SessionPaths, session: str, role: str, msg_path: Path, msg_body: str) -> str:
    role_prompt_path = sp.roles / role / "prompt.md"
    base = read_text(role_prompt_path).strip() if role_prompt_path.exists() else f"You are {role}."
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

Task:
Read the message file content below and execute it end-to-end (code changes + verification + writeback).

Message content:
```md
{msg_body.strip()}
```
"""
    return base + "\n" + extra.strip() + "\n"


def codex_exec(role_cwd: Path, prompt: str, out_last: Path, *, model: str, add_dirs: List[Path]) -> int:
    mkdirp(out_last.parent)
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
    p = subprocess.run(cmd, input=prompt, text=True)
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
) -> None:
    out = sp.bus / "outbox" / f"{mid}.{role}.md"
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    text = "\n".join(
        [
            "---",
            f"id: {mid}",
            f"role: {role}",
            f'thread: "{thread}"',
            f'request_from: "{request_from}"',
            f'request_to: "{request_to}"',
            f'request_intent: "{request_intent}"',
            f"status: {status}",
            f"codex_rc: {codex_rc}",
            f'finished_at: "{ts}"',
            "---",
            "",
            last_msg.strip(),
            "",
        ]
    )
    out.write_text(text, encoding="utf-8")


def process_one(sp: SessionPaths, session: str, role: str, role_cwd: Path, dry_run: bool, *, model: str) -> bool:
    msg_path = next_message_file(sp, role)
    if not msg_path:
        return False

    raw = read_text(msg_path)
    front, body = parse_frontmatter(raw)
    mid = message_id(msg_path, front)
    thread = str(front.get("thread", session)).strip() or session
    request_from = str(front.get("from", "")).strip()
    request_to = str(front.get("to", "")).strip()
    request_intent = str(front.get("intent", "")).strip()

    if done_sentinel(sp, mid, role).exists():
        # Already done; archive to keep inbox clean.
        mkdirp((sp.state / "archive" / role))
        msg_path.rename(archive_path(sp, role, msg_path))
        return True

    # Per-message lock: ensure only one instance processes this message.
    lock_dir = processing_lock(sp, mid, role)
    if lock_dir.exists():
        # Recover from crashes: steal lock if the pid is gone.
        pid_file = lock_dir / "pid"
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip()) if pid_file.exists() else 0
        except Exception:
            pid = 0
        if pid > 0 and _pid_alive(pid):
            return False
        _cleanup_lockdir(lock_dir)
    try:
        os.mkdir(lock_dir)
        (lock_dir / "pid").write_text(str(os.getpid()), encoding="utf-8")
    except FileExistsError:
        return False

    retries_path = sp.state / "processing" / f"{mid}.{role}.retries.json"
    retries = load_json(retries_path)
    n = int(retries.get("count", 0))
    if n >= 3:
        mkdirp(deadletter_path(sp, role, msg_path).parent)
        msg_path.rename(deadletter_path(sp, role, msg_path))
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
        )
        _cleanup_lockdir(lock_dir)
        return True

    global_lock = sp.artifacts / "locks" / "autopilot.global.lockdir"
    last_msg_path = sp.artifacts / "autopilot" / f"{role}.{mid}.last.txt"

    codex_rc = 0
    status = "done"
    last_msg = ""
    try:
        prompt = build_role_prompt(sp, session=session, role=role, msg_path=msg_path, msg_body=raw)
        if dry_run:
            last_msg = "DRY_RUN: skipped codex exec."
        else:
            # Recover stale global lock (crash-safe).
            if global_lock.exists():
                pid_file = global_lock / "pid"
                try:
                    pid = int(pid_file.read_text(encoding="utf-8").strip()) if pid_file.exists() else 0
                except Exception:
                    pid = 0
                if pid <= 0 or not _pid_alive(pid):
                    _cleanup_lockdir(global_lock)
            with DirLock(global_lock, timeout_s=1800.0):
                codex_rc = codex_exec(
                    role_cwd=role_cwd,
                    prompt=prompt,
                    out_last=last_msg_path,
                    model=model,
                    add_dirs=[sp.session_root],
                )
            if last_msg_path.exists():
                last_msg = last_msg_path.read_text(encoding="utf-8")
            else:
                last_msg = "(no last message captured)"
            if codex_rc != 0:
                raise RuntimeError(f"codex rc={codex_rc}")
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
        )
        done_sentinel(sp, mid, role).write_text("ok\n", encoding="utf-8")
        mkdirp((sp.state / "archive" / role))
        msg_path.rename(archive_path(sp, role, msg_path))
        return True
    except Exception as e:
        n += 1
        retries["count"] = n
        retries["last_error"] = str(e)
        retries["last_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_json(retries_path, retries)
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
        )
        return True
    finally:
        _cleanup_lockdir(lock_dir)


def daemon(session: str, role: str, poll_s: float, dry_run: bool, *, model: str) -> int:
    start_dir = Path.cwd()
    main = git_main_worktree(start_dir)
    sp = session_paths(main, session)

    if not sp.session_root.is_dir():
        print(f"session not found: {sp.session_root}", file=sys.stderr)
        return 2

    roles = list_roles(sp)
    if role not in roles:
        print(f"role not found in session: {role}", file=sys.stderr)
        return 2

    ensure_session_dirs(sp, roles=roles)

    worktrees = parse_role_worktrees(sp.session_root / "SESSION.md")
    role_cwd = worktrees.get(role, sp.main_worktree)

    while True:
        did = process_one(sp, session=session, role=role, role_cwd=role_cwd, dry_run=dry_run, model=model)
        if not did:
            time.sleep(poll_s)


def run_once(session: str, role: str, dry_run: bool, *, model: str) -> int:
    start_dir = Path.cwd()
    main = git_main_worktree(start_dir)
    sp = session_paths(main, session)
    roles = list_roles(sp)
    ensure_session_dirs(sp, roles=roles)
    worktrees = parse_role_worktrees(sp.session_root / "SESSION.md")
    role_cwd = worktrees.get(role, sp.main_worktree)
    did = process_one(sp, session=session, role=role, role_cwd=role_cwd, dry_run=dry_run, model=model)
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
