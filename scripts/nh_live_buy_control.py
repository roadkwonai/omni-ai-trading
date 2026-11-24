"""A cooperative buy-only pause; protective sells and reconciliation remain enabled."""
from contextlib import contextmanager
from pathlib import Path
from src import file_lock

MARKER = 'streamlit-live-buy-pause-v1\n'


@contextmanager
def locked(root):
    root = Path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root / 'live-buy-control.lock').open('a', encoding='utf-8') as lock:
        file_lock.lock(lock)
        yield


def buy_allowed(root):
    return not (Path(root) / 'LIVE_BUY_PAUSED').exists()


@contextmanager
def buy_lock(root):
    from src.tools.nhplug_live_guard import LiveOrderBlocked  # here, not above: it loads the whole agent package (0.7 s)
    with locked(root):
        if not buy_allowed(root):
            raise LiveOrderBlocked('new live buys are paused')
        yield


def pause(root):
    with locked(root):
        path = Path(root) / 'LIVE_BUY_PAUSED'
        try:
            with path.open('x', encoding='utf-8') as f:
                f.write(MARKER)
        except FileExistsError:
            pass


def resume(root):
    with locked(root):
        path = Path(root) / 'LIVE_BUY_PAUSED'
        if path.exists():
            if path.read_text(encoding='utf-8') != MARKER:
                raise ValueError('pause belongs to another operator')
            path.unlink()
