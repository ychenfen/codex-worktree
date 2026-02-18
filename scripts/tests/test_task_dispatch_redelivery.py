#!/usr/bin/env python3
import os
import tempfile
from pathlib import Path
import sys


def _import_task_board(scripts_dir: Path):
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import task_board  # noqa: PLC0415

    return task_board


def main() -> int:
    os.environ["TASK_BOARD_DISPATCH_STALE_SECONDS"] = "0"
    scripts_dir = Path(__file__).resolve().parents[1]
    task_board = _import_task_board(scripts_dir)

    with tempfile.TemporaryDirectory(prefix="task-redelivery-") as td:
        root = Path(td)
        sid = "sid-redelivery"
        session_root = root / "sessions" / sid
        (session_root / "bus" / "outbox").mkdir(parents=True, exist_ok=True)
        (session_root / "bus" / "inbox" / "builder-a").mkdir(parents=True, exist_ok=True)
        (session_root / "bus" / "deadletter" / "builder-a").mkdir(parents=True, exist_ok=True)
        (session_root / "state" / "archive" / "builder-a").mkdir(parents=True, exist_ok=True)
        (session_root / "state" / "done").mkdir(parents=True, exist_ok=True)

        t = task_board.add_task(
            session_root,
            title="hello",
            created_by="lead",
            owner="builder-a",
            work_type="implement",
            risk="low",
            acceptance=["ok"],
            depends_on=[],
            intent="implement",
            source_message_id="",
        )
        tid = str(t.get("id", "")).strip()
        assert tid, "missing task id"

        ok1, _, r1 = task_board.set_dispatch(
            session_root,
            task_id=tid,
            from_role="lead",
            to_role="builder-a",
            intent="implement",
            message_id="m1",
        )
        assert ok1 is True, f"expected first dispatch ok; got ok={ok1} reason={r1}"

        # No message exists for m1, and TTL is 0 => should be considered stale/missing and allow redispatch.
        ok2, _, r2 = task_board.set_dispatch(
            session_root,
            task_id=tid,
            from_role="lead",
            to_role="builder-a",
            intent="implement",
            message_id="m2",
        )
        assert ok2 is True, f"expected redispatch ok; got ok={ok2} reason={r2}"

    print("PASS test_task_dispatch_redelivery")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

