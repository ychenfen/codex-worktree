#!/usr/bin/env python3
import tempfile
from pathlib import Path
import sys


def _make_session_paths(root: Path, sid: str):
    scripts_dir = Path(__file__).resolve().parents[1]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import router  # noqa: PLC0415

    session_root = root / "sessions" / sid
    paths = [
        session_root / "bus" / "outbox",
        session_root / "bus" / "inbox",
        session_root / "state" / "router" / "processed",
        session_root / "state" / "router" / "bad-receipts",
        session_root / "state" / "router" / "bad-locks",
        session_root / "artifacts" / "locks" / "autopilot.global.lockdir",
    ]
    for p in paths:
        p.mkdir(parents=True, exist_ok=True)

    sp = router.SessionPaths(
        main_worktree=root,
        session_root=session_root,
        bus=session_root / "bus",
        state=session_root / "state",
        artifacts=session_root / "artifacts",
        shared=session_root / "shared",
        roles=session_root / "roles",
    )
    return router, sp


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="router-errno22-") as td:
        root = Path(td)
        sid = "sid-errno22"
        router, sp = _make_session_paths(root, sid)
        pid_path = sp.artifacts / "locks" / "autopilot.global.lockdir" / "pid"

        # Broken case: non-numeric bytes.
        pid_path.write_bytes(b"12\x00oops")
        confirmed, reason = router.confirm_global_lock_pid_broken(sp, pid_path)
        assert confirmed is True, f"expected confirmed=True, got {confirmed}, reason={reason}"
        assert "invalid" in reason.lower() or "broken" in reason.lower(), reason

        # Healthy case: pure numeric PID.
        pid_path.write_text("12345", encoding="utf-8")
        confirmed2, reason2 = router.confirm_global_lock_pid_broken(sp, pid_path)
        assert confirmed2 is False, f"expected confirmed=False, got {confirmed2}, reason={reason2}"
        assert "pid looks ok" in reason2.lower(), reason2

    print("PASS test_router_errno22")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
