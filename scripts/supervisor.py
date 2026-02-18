#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


ROLE_ORDER = ["lead", "builder-a", "builder-b", "reviewer", "tester"]
HEARTBEAT_SECONDS = 30.0
LOG = logging.getLogger("supervisor")


@dataclass
class SessionPaths:
    main_worktree: Path
    session_root: Path
    artifacts: Path
    roles: Path


@dataclass
class ProcSpec:
    name: str
    log_path: Path
    cmd: List[str]
    env: Dict[str, str]


def init_logging(log_file: Path) -> None:
    if LOG.handlers:
        return
    LOG.setLevel(logging.INFO)
    LOG.propagate = False

    fmt = logging.Formatter("%(asctime)s %(levelname)s [supervisor] %(message)s")

    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    LOG.addHandler(sh)

    log_file.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(str(log_file), encoding="utf-8")
    fh.setFormatter(fmt)
    LOG.addHandler(fh)


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


def session_paths(main_worktree: Path, session: str) -> SessionPaths:
    root = (main_worktree / "sessions" / session).resolve()
    return SessionPaths(
        main_worktree=main_worktree,
        session_root=root,
        artifacts=root / "artifacts",
        roles=root / "roles",
    )


def list_roles(sp: SessionPaths) -> List[str]:
    roles: List[str] = []
    if sp.roles.is_dir():
        for d in sp.roles.iterdir():
            if d.is_dir():
                roles.append(d.name)
    return [r for r in ROLE_ORDER if r in roles]


def _safe_env(base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    # Avoid passing non-UTF8 env vars into Rust-based CLIs.
    out: Dict[str, str] = {}
    for k, v in (base or os.environ).items():
        try:
            k.encode("utf-8")
            v.encode("utf-8")
        except Exception:
            continue
        out[k] = v
    return out


def _open_log(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    # line-buffered text
    return open(path, "a", encoding="utf-8", buffering=1)


def _write_pids(pids_file: Path, procs: Dict[str, subprocess.Popen]) -> None:
    pids_file.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{name} {p.pid}\n" for name, p in sorted(procs.items()) if p and p.pid]
    tmp = pids_file.parent / f".tmp.{pids_file.name}.{os.getpid()}"
    tmp.write_text("".join(lines), encoding="utf-8")
    tmp.replace(pids_file)


def _proc_ps(pid: int) -> str:
    try:
        p = subprocess.run(
            ["ps", "-o", "pid,ppid,pgid,sid,tty,stat,etime,command", "-p", str(pid)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if p.returncode == 0 and p.stdout.strip():
            return " | ".join([ln.strip() for ln in p.stdout.splitlines() if ln.strip()])
        return f"<ps rc={p.returncode} err={p.stderr.strip()}>"
    except Exception as e:
        return f"<ps err={e!r}>"


def spawn(spec: ProcSpec) -> Tuple[subprocess.Popen, object]:
    lf = _open_log(spec.log_path)
    p = subprocess.Popen(
        spec.cmd,
        stdout=lf,
        stderr=subprocess.STDOUT,
        cwd=str(Path.cwd()),
        env=spec.env,
        text=True,
    )
    LOG.info("spawned name=%s pid=%s log=%s cmd=%s", spec.name, p.pid, spec.log_path, " ".join(spec.cmd))
    return p, lf


def main() -> int:
    ap = argparse.ArgumentParser(description="Foreground supervisor for router + per-role daemons (launchd-friendly).")
    ap.add_argument("--session", required=True)
    ap.add_argument("--poll", type=float, default=2.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--model", default="")
    ap.add_argument("--serial", action="store_true", help="Enable global lock (safe but slower).")
    args = ap.parse_args()

    main_wt = git_main_worktree(Path.cwd())
    sp = session_paths(main_wt, args.session)
    if not sp.session_root.is_dir():
        print(f"session not found: {sp.session_root}", file=sys.stderr)
        return 2

    ap_dir = sp.session_root / "artifacts" / "autopilot"
    ap_dir.mkdir(parents=True, exist_ok=True)
    init_logging(ap_dir / "supervisor.log")

    roles = list_roles(sp)
    if not roles:
        LOG.error("no roles found under %s", sp.roles)
        return 2

    base_env = _safe_env()
    if args.serial:
        base_env["AUTOPILOT_GLOBAL_LOCK"] = "1"
    else:
        base_env["AUTOPILOT_GLOBAL_LOCK"] = "0"
    base_env["PYTHONUNBUFFERED"] = "1"

    pids_file = ap_dir / "pids.txt"
    log_files: Dict[str, Path] = {}
    specs: Dict[str, ProcSpec] = {}

    py = sys.executable or "python3"

    router_log = ap_dir / "router.log"
    log_files["router"] = router_log
    router_cmd = [py, str(main_wt / "scripts" / "router.py"), "daemon", "--session", args.session, "--poll", str(args.poll)]
    if args.dry_run:
        router_cmd.append("--dry-run")
    specs["router"] = ProcSpec(name="router", log_path=router_log, cmd=router_cmd, env=dict(base_env))

    for r in roles:
        log_path = ap_dir / f"{r}.log"
        log_files[r] = log_path
        cmd = [
            py,
            str(main_wt / "scripts" / "autopilot.py"),
            "daemon",
            "--session",
            args.session,
            "--role",
            r,
            "--poll",
            str(args.poll),
        ]
        if args.dry_run:
            cmd.append("--dry-run")
        if args.model:
            cmd.extend(["--model", args.model])
        specs[r] = ProcSpec(name=r, log_path=log_path, cmd=cmd, env=dict(base_env))

    stop = {"flag": False}

    def _handle(sig: int, _frame: object) -> None:
        stop["flag"] = True
        LOG.error("SIGNAL received signum=%s", sig)

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    procs: Dict[str, subprocess.Popen] = {}
    logs: Dict[str, object] = {}

    def _start(name: str) -> None:
        p, lf = spawn(specs[name])
        procs[name] = p
        logs[name] = lf
        _write_pids(pids_file, procs)

    for name in ["router"] + roles:
        _start(name)

    next_hb = 0.0
    while True:
        if stop["flag"]:
            break
        now = time.monotonic()
        if now >= next_hb:
            parts = []
            for name, p in procs.items():
                parts.append(f"{name}={p.pid}")
            LOG.info(
                "heartbeat session=%s poll_s=%s dry_run=%s serial=%s procs=%s",
                args.session,
                args.poll,
                args.dry_run,
                args.serial,
                ",".join(sorted(parts)),
            )
            next_hb = now + HEARTBEAT_SECONDS

        # Monitor for exits; restart fast but observable.
        for name, p in list(procs.items()):
            rc = p.poll()
            if rc is None:
                continue
            sig = rc - 128 if rc > 128 else 0
            LOG.error("child_exit name=%s pid=%s rc=%s sig=%s ps=%s", name, p.pid, rc, sig, _proc_ps(p.pid or -1))
            try:
                lf = logs.get(name)
                if lf:
                    lf.flush()
            except Exception:
                pass
            # Restart after a short delay to avoid tight loops.
            time.sleep(0.5)
            _start(name)
        time.sleep(1.0)

    # Shutdown: terminate children.
    LOG.error("shutdown start session=%s", args.session)
    for name, p in list(procs.items()):
        try:
            p.terminate()
        except Exception:
            pass
    time.sleep(2.0)
    for name, p in list(procs.items()):
        try:
            if p.poll() is None:
                p.kill()
        except Exception:
            pass
    try:
        _write_pids(pids_file, {})
    except Exception:
        pass
    for lf in logs.values():
        try:
            lf.close()
        except Exception:
            pass
    LOG.error("shutdown done session=%s", args.session)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
