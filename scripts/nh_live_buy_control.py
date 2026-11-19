"""A cooperative buy-only pause; protective sells and reconciliation remain enabled."""
import fcntl
from contextlib import contextmanager
from pathlib import Path
from src.tools.nhplug_live_guard import LiveOrderBlocked

MARKER = 'streamlit-live-buy-pause-v1\n'


@contextmanager
def locked(root):
    root = Path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root / 'live-buy-control.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def buy_allowed(root):
    return not (Path(root) / 'LIVE_BUY_PAUSED').exists()


@contextmanager
def buy_lock(root):
    with locked(root):
        if not buy_allowed(root):
            raise LiveOrderBlocked('new live buys are paused')
        yield


def pause(root):
    with locked(root):
        path = Path(root) / 'LIVE_BUY_PAUSED'
        try:
            with path.open('x') as f:
                f.write(MARKER)
        except FileExistsError:
            pass


def resume(root):
    with locked(root):
        path = Path(root) / 'LIVE_BUY_PAUSED'
        if path.exists():
            if path.read_text() != MARKER:
                raise ValueError('pause belongs to another operator')
            path.unlink()
