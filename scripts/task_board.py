#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import secrets
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


TASK_LOCK_STALE_SECONDS = int(os.environ.get("TASK_BOARD_LOCK_STALE_SECONDS", "21600"))


@dataclass
class TaskBoardPaths:
    root: Path
    dir: Path
    file: Path
    lock: Path
    stale_dir: Path


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _new_task_id() -> str:
    ts = time.strftime("%Y%m%d-%H%M%S")
    return f"T{ts}-{secrets.token_hex(3)}"


def _board_paths(session_root: Path) -> TaskBoardPaths:
    td = session_root / "state" / "tasks"
    return TaskBoardPaths(
        root=session_root,
        dir=td,
        file=td / "tasks.json",
        lock=td / "tasks.lockdir",
        stale_dir=td / "_stale_lockdirs",
    )


def _default_board() -> Dict[str, object]:
    ts = _now()
    return {
        "version": 1,
        "created_at": ts,
        "updated_at": ts,
        "tasks": [],
    }


def _mkdirp(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _atomic_write_json(path: Path, data: Dict[str, object]) -> None:
    _mkdirp(path.parent)
    tmp = path.parent / f".tmp.{path.name}.{os.getpid()}"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> Dict[str, object]:
    if not path.exists():
        return _default_board()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _default_board()
    if not isinstance(data, dict):
        return _default_board()
    if "tasks" not in data or not isinstance(data.get("tasks"), list):
        data["tasks"] = []
    if "version" not in data:
        data["version"] = 1
    if "updated_at" not in data:
        data["updated_at"] = _now()
    return data


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


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


def _cleanup_lockdir(lock_dir: Path, stale_root: Path) -> None:
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
    try:
        _mkdirp(stale_root)
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


class DirLock:
    def __init__(self, lock_dir: Path, stale_root: Path, timeout_s: float = 10.0, poll_s: float = 0.1):
        self.lock_dir = lock_dir
        self.stale_root = stale_root
        self.timeout_s = timeout_s
        self.poll_s = poll_s
        self.owned = False

    def __enter__(self) -> "DirLock":
        _mkdirp(self.lock_dir.parent)
        _mkdirp(self.stale_root)
        started = time.time()
        while True:
            try:
                os.mkdir(self.lock_dir)
                (self.lock_dir / "pid").write_text(str(os.getpid()), encoding="utf-8")
                self.owned = True
                return self
            except FileExistsError:
                pid = _read_lock_pid(self.lock_dir)
                age_s = _lock_age_seconds(self.lock_dir)
                if pid <= 0 or not _pid_alive(pid) or age_s >= TASK_LOCK_STALE_SECONDS:
                    _cleanup_lockdir(self.lock_dir, self.stale_root)
                    continue
                if time.time() - started > self.timeout_s:
                    raise TimeoutError(f"task-board lock timeout: {self.lock_dir}")
                time.sleep(self.poll_s)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.owned:
            _cleanup_lockdir(self.lock_dir, self.stale_root)


def ensure_task_board(session_root: Path) -> Path:
    bp = _board_paths(session_root)
    _mkdirp(bp.dir)
    if not bp.file.exists():
        _atomic_write_json(bp.file, _default_board())
    return bp.file


def _normalize_text_list(items: Optional[List[str]]) -> List[str]:
    out: List[str] = []
    for x in items or []:
        s = str(x).strip()
        if s:
            out.append(s)
    return out


def _history(task: Dict[str, object], *, action: str, by: str, note: str = "") -> None:
    h = task.get("history")
    if not isinstance(h, list):
        h = []
        task["history"] = h
    rec = {"at": _now(), "action": action, "by": by}
    if note:
        rec["note"] = note
    h.append(rec)


def _task_index(board: Dict[str, object], task_id: str) -> int:
    tasks = board.get("tasks")
    if not isinstance(tasks, list):
        return -1
    for i, t in enumerate(tasks):
        if isinstance(t, dict) and str(t.get("id", "")).strip() == task_id:
            return i
    return -1


def _deps_satisfied(board: Dict[str, object], task: Dict[str, object]) -> Tuple[bool, List[str]]:
    deps = _normalize_text_list(task.get("depends_on") if isinstance(task.get("depends_on"), list) else [])
    if not deps:
        return True, []
    missing: List[str] = []
    tasks = board.get("tasks") if isinstance(board.get("tasks"), list) else []
    by_id: Dict[str, Dict[str, object]] = {}
    for t in tasks:
        if isinstance(t, dict):
            tid = str(t.get("id", "")).strip()
            if tid:
                by_id[tid] = t
    for dep in deps:
        dep_task = by_id.get(dep)
        if not dep_task or str(dep_task.get("status", "")).strip() != "completed":
            missing.append(dep)
    return len(missing) == 0, missing


def _sort_tasks(tasks: List[Dict[str, object]]) -> List[Dict[str, object]]:
    return sorted(
        tasks,
        key=lambda t: (str(t.get("created_at", "")), str(t.get("id", ""))),
    )


def _update_board(
    session_root: Path,
    mutate_fn,
    *,
    timeout_s: float = 10.0,
) -> object:
    ensure_task_board(session_root)
    bp = _board_paths(session_root)
    with DirLock(bp.lock, stale_root=bp.stale_dir, timeout_s=timeout_s):
        board = _read_json(bp.file)
        result, changed = mutate_fn(board)
        if changed:
            board["updated_at"] = _now()
            _atomic_write_json(bp.file, board)
        return result


def list_tasks(session_root: Path, statuses: Optional[List[str]] = None) -> List[Dict[str, object]]:
    bp = _board_paths(session_root)
    ensure_task_board(session_root)
    board = _read_json(bp.file)
    tasks = [t for t in board.get("tasks", []) if isinstance(t, dict)]
    tasks = _sort_tasks(tasks)
    wanted = {s.strip() for s in (statuses or []) if s.strip()}
    if not wanted:
        return tasks
    return [t for t in tasks if str(t.get("status", "")).strip() in wanted]


def list_dispatchable_tasks(session_root: Path, owner: str = "") -> List[Dict[str, object]]:
    """
    Return pending tasks that are ready for dispatch:
    - status == pending
    - deps satisfied
    - owner present (and optionally owner matches)
    - no dispatch.message_id yet
    """
    ensure_task_board(session_root)
    board = _read_json(_board_paths(session_root).file)
    tasks = [t for t in board.get("tasks", []) if isinstance(t, dict)]
    out: List[Dict[str, object]] = []
    owner = owner.strip()
    for t in _sort_tasks(tasks):
        status = str(t.get("status", "")).strip()
        role = str(t.get("owner", "")).strip()
        if status != "pending":
            continue
        if not role:
            continue
        if owner and role != owner:
            continue
        dispatch = t.get("dispatch")
        if isinstance(dispatch, dict) and str(dispatch.get("message_id", "")).strip():
            continue
        ok, _ = _deps_satisfied(board, t)
        if not ok:
            continue
        out.append(t)
    return out


def get_task(session_root: Path, task_id: str) -> Optional[Dict[str, object]]:
    for t in list_tasks(session_root):
        if str(t.get("id", "")).strip() == task_id:
            return t
    return None


def add_task(
    session_root: Path,
    *,
    title: str,
    created_by: str,
    owner: str = "",
    work_type: str = "implement",
    risk: str = "low",
    acceptance: Optional[List[str]] = None,
    depends_on: Optional[List[str]] = None,
    intent: str = "implement",
    source_message_id: str = "",
) -> Dict[str, object]:
    title = title.strip()
    if not title:
        raise ValueError("title is required")

    def _mutate(board: Dict[str, object]):
        tasks = board.get("tasks")
        if not isinstance(tasks, list):
            tasks = []
            board["tasks"] = tasks
        task = {
            "id": _new_task_id(),
            "title": title,
            "status": "pending",
            "owner": owner.strip(),
            "claimed_by": "",
            "work_type": work_type.strip() or "implement",
            "risk": (risk.strip() or "low").lower(),
            "intent": intent.strip() or "implement",
            "acceptance": _normalize_text_list(acceptance),
            "depends_on": _normalize_text_list(depends_on),
            "source_message_id": source_message_id.strip(),
            "created_by": created_by.strip() or "system",
            "created_at": _now(),
            "updated_at": _now(),
            "history": [],
        }
        _history(task, action="created", by=task["created_by"])
        tasks.append(task)
        return task, True

    return _update_board(session_root, _mutate)


def set_dispatch(
    session_root: Path,
    *,
    task_id: str,
    from_role: str,
    to_role: str,
    intent: str,
    message_id: str,
) -> Tuple[bool, Optional[Dict[str, object]], str]:
    def _mutate(board: Dict[str, object]):
        idx = _task_index(board, task_id)
        if idx < 0:
            return (False, None, "not_found"), False
        tasks = board.get("tasks")
        assert isinstance(tasks, list)
        task = tasks[idx]
        assert isinstance(task, dict)
        prev = task.get("dispatch")
        if isinstance(prev, dict):
            prev_mid = str(prev.get("message_id", "")).strip()
            if prev_mid:
                if prev_mid == message_id.strip():
                    return (True, task, "already_dispatched_same"), False
                return (False, task, "already_dispatched"), False
        task["dispatch"] = {
            "from": from_role.strip(),
            "to": to_role.strip(),
            "intent": intent.strip(),
            "message_id": message_id.strip(),
            "at": _now(),
        }
        task["updated_at"] = _now()
        _history(task, action="dispatched", by=from_role.strip() or "system", note=message_id.strip())
        return (True, task, "ok"), True

    return _update_board(session_root, _mutate)


def claim_task(
    session_root: Path,
    *,
    task_id: str,
    role: str,
    message_id: str = "",
) -> Tuple[bool, Optional[Dict[str, object]], str]:
    role = role.strip()

    def _mutate(board: Dict[str, object]):
        idx = _task_index(board, task_id)
        if idx < 0:
            return (False, None, "not_found"), False
        tasks = board.get("tasks")
        assert isinstance(tasks, list)
        task = tasks[idx]
        assert isinstance(task, dict)

        status = str(task.get("status", "")).strip()
        owner = str(task.get("owner", "")).strip()
        claimed_by = str(task.get("claimed_by", "")).strip()

        if status == "completed":
            return (False, task, "completed"), False
        if status == "failed":
            return (False, task, "failed"), False
        if status == "in_progress":
            if claimed_by == role:
                return (True, task, "already_claimed"), False
            return (False, task, "claimed_by_other"), False
        if status != "pending":
            return (False, task, "invalid_status"), False

        if owner and owner != role:
            return (False, task, "owner_mismatch"), False

        ok, missing = _deps_satisfied(board, task)
        if not ok:
            return (False, task, f"deps_blocked:{','.join(missing)}"), False

        task["status"] = "in_progress"
        task["claimed_by"] = role
        task["claimed_at"] = _now()
        if message_id.strip():
            task["claim_message_id"] = message_id.strip()
        task["updated_at"] = _now()
        _history(task, action="claimed", by=role, note=message_id.strip())
        return (True, task, "claimed"), True

    return _update_board(session_root, _mutate)


def claim_next_task(
    session_root: Path,
    *,
    role: str,
    message_id: str = "",
) -> Tuple[bool, Optional[Dict[str, object]], str]:
    role = role.strip()

    def _mutate(board: Dict[str, object]):
        tasks = board.get("tasks")
        if not isinstance(tasks, list):
            return (False, None, "none_available"), False

        ordered = _sort_tasks([t for t in tasks if isinstance(t, dict)])
        chosen: Optional[Dict[str, object]] = None
        reason = "none_available"
        for t in ordered:
            status = str(t.get("status", "")).strip()
            owner = str(t.get("owner", "")).strip()
            if status != "pending":
                continue
            if owner and owner != role:
                reason = "owner_mismatch"
                continue
            ok, missing = _deps_satisfied(board, t)
            if not ok:
                reason = f"deps_blocked:{','.join(missing)}"
                continue
            chosen = t
            break

        if not chosen:
            return (False, None, reason), False

        chosen["status"] = "in_progress"
        chosen["claimed_by"] = role
        chosen["claimed_at"] = _now()
        if message_id.strip():
            chosen["claim_message_id"] = message_id.strip()
        chosen["updated_at"] = _now()
        _history(chosen, action="claimed", by=role, note=message_id.strip())
        return (True, chosen, "claimed"), True

    return _update_board(session_root, _mutate)


def complete_task(
    session_root: Path,
    *,
    task_id: str,
    role: str,
    evidence: str = "",
    receipt_file: str = "",
) -> Tuple[bool, Optional[Dict[str, object]], str]:
    role = role.strip()

    def _mutate(board: Dict[str, object]):
        idx = _task_index(board, task_id)
        if idx < 0:
            return (False, None, "not_found"), False
        tasks = board.get("tasks")
        assert isinstance(tasks, list)
        task = tasks[idx]
        assert isinstance(task, dict)

        status = str(task.get("status", "")).strip()
        claimed_by = str(task.get("claimed_by", "")).strip()
        if status == "completed":
            return (True, task, "already_completed"), False
        if status != "in_progress":
            return (False, task, "not_in_progress"), False
        if claimed_by and claimed_by != role:
            return (False, task, "claimed_by_other"), False

        task["status"] = "completed"
        task["completed_by"] = role
        task["completed_at"] = _now()
        task["updated_at"] = _now()
        if evidence.strip():
            ev = task.get("evidence")
            if not isinstance(ev, list):
                ev = []
                task["evidence"] = ev
            ev.append(evidence.strip())
        if receipt_file.strip():
            task["receipt_file"] = receipt_file.strip()
        _history(task, action="completed", by=role, note=evidence.strip() or receipt_file.strip())
        return (True, task, "completed"), True

    return _update_board(session_root, _mutate)


def mark_task_failed(
    session_root: Path,
    *,
    task_id: str,
    role: str,
    error: str = "",
    terminal: bool = False,
) -> Tuple[bool, Optional[Dict[str, object]], str]:
    role = role.strip()

    def _mutate(board: Dict[str, object]):
        idx = _task_index(board, task_id)
        if idx < 0:
            return (False, None, "not_found"), False
        tasks = board.get("tasks")
        assert isinstance(tasks, list)
        task = tasks[idx]
        assert isinstance(task, dict)

        status = str(task.get("status", "")).strip()
        if status == "completed":
            return (False, task, "completed"), False
        if terminal:
            task["status"] = "failed"
            action = "failed"
        else:
            action = "retry_error"
        task["last_error"] = error.strip()
        task["last_error_by"] = role
        task["last_error_at"] = _now()
        task["updated_at"] = _now()
        _history(task, action=action, by=role, note=error.strip())
        return (True, task, "updated"), True

    return _update_board(session_root, _mutate)


def format_task_brief(task: Dict[str, object]) -> str:
    tid = str(task.get("id", "")).strip()
    status = str(task.get("status", "")).strip() or "?"
    owner = str(task.get("owner", "")).strip() or "-"
    claimed = str(task.get("claimed_by", "")).strip() or "-"
    title = str(task.get("title", "")).strip() or "(untitled)"
    deps = _normalize_text_list(task.get("depends_on") if isinstance(task.get("depends_on"), list) else [])
    deps_s = ",".join(deps) if deps else "-"
    return f"{tid} | {status:11} | owner={owner:9} | claimed={claimed:9} | deps={deps_s} | {title}"
