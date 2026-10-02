"""One owner of the iPhone at a time, across processes.

`.state/phone.lock` names whoever is driving the phone: a posting flow run by hand or by
the server, a receipt read, a link soak or experiment, an operator session. The server's
own phone worker only serializes work inside the server; this file also covers a
`scripts/phone_*.py` run from a terminal while a slot post comes due (audit C2,
2026-10-01).

A lock written by a flow carries its process id and is ignored once that process is gone,
so a crash never blocks the phone for good. A lock without a process id (written by hand
or by an older tool) is always honoured.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .link_supervisor import _pid_alive

LOCK_FILE = "phone.lock"


class PhoneLockHeld(RuntimeError):
    pass


def _read(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError:
        return {"owner": "unreadable phone.lock", "pid": None}
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except ValueError:
        pass
    return {"owner": text or "phone.lock", "pid": None}


def holder(state: Path, *, alive=_pid_alive) -> str | None:
    """Who else holds the phone, or None when it is free (or held by this process)."""
    data = _read(Path(state) / LOCK_FILE)
    if data is None:
        return None
    pid = data.get("pid")
    if isinstance(pid, int):
        if pid == os.getpid() or not alive(pid):
            return None
    return str(data.get("owner") or "another phone session")


@contextmanager
def hold(state: Path, owner: str, *, alive=_pid_alive):
    """Hold the phone for this process; refuse before any phone work when another holds it.

    Re-entrant inside one process: an inner hold leaves the outer one's file alone.
    """
    path = Path(state) / LOCK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    record = json.dumps({"owner": owner, "pid": os.getpid(), "since": datetime.now().isoformat(timespec="seconds")})
    try:
        # Created atomically: of two processes starting together, exactly one gets the file.
        with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY), "w", encoding="utf-8") as handle:
            handle.write(record)
    except FileExistsError:
        other = holder(state, alive=alive)
        if other is not None:
            raise PhoneLockHeld(f"The iPhone is in use by {other}; nothing was sent. Try again when it finishes.")
        data = _read(path)
        if data is not None and data.get("pid") == os.getpid():
            yield  # re-entrant: the outer hold owns the file
            return
        path.write_text(record, encoding="utf-8")  # the holder's process is gone
    try:
        yield
    finally:
        current = _read(path)
        if current is not None and current.get("pid") == os.getpid():
            path.unlink(missing_ok=True)


def locked(owner: str):
    """Hold the phone for a flow's ``run(release, db, ...)``; the lock sits next to that database."""
    def wrap(run):
        def runner(*args, **kwargs):
            db = kwargs["db"] if "db" in kwargs else args[1]
            with hold(Path(db).parent, owner):
                try:
                    result = run(*args, **kwargs)
                except Exception as exc:
                    from .failure_capture import capture  # evidence of the screen it failed on
                    capture(Path(db).parent, owner, exc)
                    raise
                if isinstance(result, dict) and result.get("kind") == "error":
                    from .failure_capture import capture
                    capture(Path(db).parent, owner, RuntimeError(str(result.get("message", ""))))
                return result
        runner.__wrapped__ = run
        runner.__name__, runner.__doc__ = run.__name__, run.__doc__
        return runner
    return wrap
