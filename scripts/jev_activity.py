"""Nonblocking, process-wide Jev HTTP request exclusion."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from src import file_lock

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
        handle = path.open('a', encoding='utf-8')
        file_lock.lock(handle, blocking=False)
    except OSError as exc:
        if handle is not None:
            handle.close()
        raise JevRequestBusy('Jev request busy or lock unavailable') from exc
    try:
        yield
    finally:
        try:
            file_lock.unlock(handle)
        finally:
            handle.close()


def regular_request_busy(path=None):
    """Return True if any cooperating Jev request is active or its state is unknown."""
    try:
        path = Path(path or LOCK)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open('a', encoding='utf-8') as handle:
            file_lock.lock(handle, blocking=False)
            file_lock.unlock(handle)
        return False
    except OSError:
        return True
