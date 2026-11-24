"""One NH MCP server process per program for reads (2026-11-24, docs/audit/api-load-audit-2026-11-24.md proposal 1).

Every NH call used to start its own Python -> Node process (about 10,000 a day). On 11-23 109 of those starts hung for
30 seconds before anything reached NH, and the paper runner lost about 160 seconds each time.

Reads (quotes, balances, executions) now share one process per server configuration. Orders, cancels, guarded calls and
any operation not listed as a read keep their own process and their own start limit, exactly as before: a failed start
leaves an order intent uncertain, and a fresh process reads the token file itself.

The token: the server keeps its access token in memory, and another process may have issued a newer one since. So the
shared process is used only while the token file is the one it started with and is not about to expire; otherwise the
call gets its own process, which reads (or issues) the token as before. Any failure of the shared process puts that
server back to one process a call for REST_S.
"""
from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import json
import os
import sys
import threading
import time
from pathlib import Path

ENABLED = 'pytest' not in sys.modules
READ_INIT_S = 8.0   # a start takes about 0.2 s; a read that waits longer is better asked again
MAX_AGE_S = 3600.0
REST_S = 60.0
EXPIRY_S = 120.0    # the server renews a token 30 s before it ends
TOKENS = Path.home() / '.vibe-trading' / 'nhplug' / 'tokens'  # where dist/auth.js keeps it (homedir, not VIBE_TRADING_HOME)
READ_TOOLS = ('get_stock_price', 'list_accounts')
READ_OPERATIONS = ('krstockQuote', 'krstockInquiry')

_sessions: dict = {}   # (pid, server) -> _Session; touched only on the loop thread
_resting: dict = {}    # server -> monotonic time reuse may be tried again
_loop_lock = threading.Lock()
_loop: tuple | None = None  # (pid, loop): a forked child starts its own


class _Session:
    def __init__(self, client, token):
        self.client, self.token, self.started = client, token, time.monotonic()
        self.ready = asyncio.ensure_future(client.__aenter__())


def reusable(server_name, config, tool, arguments, custom_client) -> bool:
    if not ENABLED or custom_client is not None or 'nhplug' not in (server_name or '') or config.resolved_transport() != 'stdio':
        return False
    operation = arguments.get('operationId') if isinstance(arguments, dict) else None
    return tool in READ_TOOLS or (tool == 'call_api' and str(operation or '').startswith(READ_OPERATIONS))


def token_stamp():
    """What identifies the token in the file; None when there is none to rely on (missing, unreadable, about to expire)."""
    try:
        path = max(TOKENS.glob('*.json'), key=lambda p: p.stat().st_mtime_ns)
        stamp = (path.name, path.stat().st_mtime_ns)
        return stamp if json.loads(path.read_text())['expiresAt'] / 1000 - time.time() > EXPIRY_S else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def key_of(config):
    return (config.command, *config.args, *sorted(config.env.items()))


def _background_loop():
    global _loop
    with _loop_lock:
        if _loop is None or _loop[0] != os.getpid():
            loop = asyncio.new_event_loop()
            threading.Thread(target=loop.run_forever, name='nh-sessions', daemon=True).start()
            _loop = (os.getpid(), loop)
            _sessions.clear()
        return _loop[1]


def _drop(key, session):
    """Forget it now; end its process in the background once the calls it is answering had time to finish."""
    if _sessions.get(key) is session:
        del _sessions[key]

    async def close():
        failed = session.ready.done() and (session.ready.cancelled() or session.ready.exception() is not None)
        await asyncio.sleep(0 if failed else 45)  # longer than a tool call may take
        try:
            await session.client.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001 - it is being thrown away
            pass
    asyncio.ensure_future(close())


async def _call(key, token, build, tool, arguments, timeout):
    session = _sessions.get(key)
    if session is not None and (session.token != token or time.monotonic() - session.started > MAX_AGE_S):
        _drop(key, session)
        session = None
    if session is None:
        session = _sessions[key] = _Session(build(), token)
    try:
        await asyncio.shield(session.ready)  # everyone waiting for this start fails or goes on together
        result = await session.client.call_tool(tool, arguments=arguments, timeout=timeout, raise_on_error=False)
    except BaseException:
        _drop(key, session)
        raise
    if result.is_error and any('"auth"' in str(getattr(c, 'text', '')) for c in result.content or []):
        _drop(key, session)
    return result


async def call(config, build, tool, arguments, timeout):
    """The result, or None when this call should use its own process. Reads only, so a call the shared process lost at
    once (it died) may be asked again; one that failed to start or ran into its time limit is raised."""
    key, token = key_of(config), token_stamp()
    if token is None or time.monotonic() < _resting.get(key, 0):
        return None
    started = time.monotonic()
    future = asyncio.run_coroutine_threadsafe(_call(key, token, build, tool, arguments, timeout), _background_loop())
    try:
        return await asyncio.wrap_future(future)
    except Exception as exc:
        _resting[key] = time.monotonic() + REST_S
        text = str(exc).lower()
        if 'initialize server session' in text or 'timed out' in text or time.monotonic() - started > 5:
            raise
        return None


def close_all():
    """End every shared process (program exit, tests)."""
    global _loop
    if _loop is None or _loop[0] != os.getpid():
        return

    async def close():
        sessions, _sessions_now = list(_sessions.values()), _sessions.clear()
        for s in sessions:
            try:
                await asyncio.wait_for(s.client.__aexit__(None, None, None), 2)
            except BaseException:  # noqa: BLE001 - the pipe closes with the program anyway
                pass
    try:
        asyncio.run_coroutine_threadsafe(close(), _loop[1]).result(3)
    except (concurrent.futures.TimeoutError, RuntimeError):
        pass
    _resting.clear()


atexit.register(close_all)
