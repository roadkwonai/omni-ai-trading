"""Release snapshot leases after disconnected Streamlit sessions pass their reconnect window."""
from __future__ import annotations

import logging
from threading import Lock, Thread
from time import monotonic, sleep
from weakref import WeakKeyDictionary

from streamlit import config
from streamlit.runtime import exists, get_instance
from streamlit.runtime.scriptrunner import get_script_run_ctx

_INTERVAL = 5
_lock = Lock()
_watched = WeakKeyDictionary()
_running = False
_last_error = float('-inf')
_logger = logging.getLogger(__name__)


def _reap(check, grace, now):
    global _last_error
    with _lock:
        for lease, state in list(_watched.items()):
            session, since = state
            try:
                active = check(session)
            except Exception:
                if now - _last_error >= 60:
                    _logger.warning('매매내역 세션 상태를 확인하지 못해 스냅숏 해제를 보류합니다.')
                    _last_error = now
                continue
            if active:
                _watched[lease] = (session, None)
            elif since is None:
                _watched[lease] = (session, now)
            elif now - since >= grace:
                lease.release()
                del _watched[lease]


def _run(check, grace):
    global _running
    while True:
        sleep(_INTERVAL)
        _reap(check, grace, monotonic())
        with _lock:
            if not _watched:
                _running = False
                return


def watch(lease):
    """A closed tab may reconnect until Streamlit's configured disconnect TTL elapses."""
    global _running
    ctx = get_script_run_ctx(suppress_warning=True)
    if ctx is None or not exists():
        return
    runtime = get_instance()
    grace = max(0, config.get_option('server.disconnectedSessionTTL')) + _INTERVAL
    with _lock:
        _watched[lease] = (ctx.session_id, None)
        if not _running:
            _running = True
            Thread(target=_run, args=(runtime.is_active_session, grace), daemon=True,
                   name='trade-history-session-cleanup').start()


def release(lease):
    with _lock:
        _watched.pop(lease, None)
        lease.release()
