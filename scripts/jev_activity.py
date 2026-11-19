"""Nonblocking, process-wide Jev HTTP request exclusion."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
from pathlib import Path

LOCK = Path.home() / '.vibe-trading/nhplug/jev-regular-request.lock'


class JevRequestBusy(ValueError):
    """The current model judgment is unavailable while another request holds the lock."""


@contextmanager
def regular_request(path=None):
    """Hold an exclusive lock for one complete HTTP request, or fail closed immediately."""
    handle = None
    try:
        path = Path(path or LOCK)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle = path.open('a')
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if handle is not None:
            handle.close()
        raise JevRequestBusy('Jev request busy or lock unavailable') from exc
    try:
        yield
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            handle.close()


def regular_request_busy(path=None):
    """Return True if any cooperating Jev request is active or its state is unknown."""
    try:
        path = Path(path or LOCK)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(handle, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
