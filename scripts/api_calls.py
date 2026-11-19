"""External API call log (2026-11-17): which provider was called, how often, how fast, and what failed.

Importing this module installs a transparent hook on `requests.Session.send` and `urllib.request.OpenerDirector.open`,
so every HTTP call of the process is counted by provider and endpoint in 10-minute slots; NH calls go through the MCP
adapter (agent/src/tools/mcp.py), which reports here only when this module is loaded. yfinance (curl_cffi) and other
clients are recorded with `track()`. Logical errors inside a 200 answer go through `problem()` (Kiwoom return_code,
NH tool errors). A redirect is one call: only the outermost request of a thread is counted.

Never stored: query strings, request or response bodies, headers, exception messages (they can echo URLs with keys).
Stored: provider, host+path (digit-heavy segments masked), caller script, status code, elapsed ms, response bytes,
exception class. Logging is best effort: a locked or broken DB drops the record and never raises into the caller.
Disabled under pytest so tests never write the live log (see the 2026-11-17 journal incident).
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

KST = timezone(timedelta(hours=9))
DB = Path(os.environ.get('VIBE_TRADING_HOME', '').strip() or Path.home() / '.vibe-trading').expanduser() / 'nhplug' / 'api-calls.sqlite3'
ENABLED = 'pytest' not in sys.modules
KEEP_DAYS = 30
SLOT_MIN = 10
BUSY_TIMEOUT = 0.5  # seconds; recording sits on every HTTP call, so a lock must not hold a runner up longer
HOSTS = (  # host suffix -> provider key (api_catalog.CATALOG); first match wins
    ('nhplug.com', 'nh'), ('kiwoom.com', 'kiwoom'), ('tossinvest.com', 'toss'), ('naver.com', 'naver'),
    ('kind.krx.co.kr', 'kind'), ('krx.co.kr', 'krx'), ('fss.or.kr', 'opendart'), ('news.google.com', 'google_news'),
    ('bing.com', 'bing_news'), ('yahoo.com', 'yahoo'), ('tradingview.com', 'tradingview'), ('openai.com', 'openai'),
    ('typesafe.ai', 'jev'), ('stlouisfed.org', 'fred'), ('federalreserve.gov', 'fed'), ('sec.gov', 'sec'), ('discord.com', 'discord'),
    ('github.io', 'report_pages'), ('api.github.com', 'report_pages'), ('jsdelivr.net', 'report_pages'))
CODE_AFTER = {'chart', 'quote', 'stock', 'stocks', 'item', 'index', 'symbols'}
SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (day TEXT, slot TEXT, provider TEXT, endpoint TEXT, caller TEXT, n INTEGER, fail INTEGER,
  bad INTEGER, ms_sum REAL, ms_max REAL, bytes INTEGER, last_ok TEXT, last_fail TEXT,
  PRIMARY KEY (day, slot, provider, endpoint, caller));
CREATE TABLE IF NOT EXISTS failures (time TEXT, provider TEXT, endpoint TEXT, caller TEXT, kind TEXT, status INTEGER, detail TEXT);
CREATE INDEX IF NOT EXISTS failures_time ON failures (time);
"""
LOCAL = {'127.0.0.1', 'localhost', '::1'}
_lock = threading.Lock()
_depth = threading.local()  # nested sends (redirect hops) inside one outer call
_conn = {}  # pid -> connection (a forked child opens its own)


def provider_of(host):
    host = (host or '').lower()
    return next((key for suffix, key in HOSTS if host == suffix or host.endswith('.' + suffix)), 'other')


def endpoint_of(url, api_id=None):
    """host + path with codes masked: /api/stock/005930/trend -> /api/stock/{code}/trend. No query string."""
    parts = urlsplit(url)
    segs, out = [s for s in parts.path.split('/') if s], []
    for i, seg in enumerate(segs):
        after = i and segs[i - 1].lower() in CODE_AFTER and not seg.islower()  # /stocks/A005930 yes, /stocks/all no
        out.append('{token}' if len(seg) >= 24 else '{code}' if after or len(re.findall(r'\d', seg)) >= 3 else seg)  # webhook tokens, article ids
    return f"{parts.hostname or ''}/{'/'.join(out)}" + (f'#{api_id}' if api_id else '')


def caller():
    name = Path(sys.argv[0] if sys.argv and sys.argv[0] else 'python').stem
    return 'dashboard' if name in ('streamlit', 'app') else name or 'python'


def mask(text, limit=160):
    """Short, digit-masked text for failure detail (no account numbers, keys or codes longer than 5 digits)."""
    return re.sub(r'\d{6,}', '******', str(text or ''))[:limit]


def _db():
    pid = os.getpid()
    conn = _conn.get(pid)
    if conn is None:
        DB.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB, timeout=BUSY_TIMEOUT, check_same_thread=False, isolation_level=None)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')
        conn.executescript(SCHEMA)
        try:
            os.chmod(DB, 0o600)
        except OSError:
            pass
        _conn[pid] = conn  # before the prune: a writer holding the lock must not make every later call reconnect
        try:
            cut = (datetime.now(KST) - timedelta(days=KEEP_DAYS)).strftime('%Y-%m-%d')
            conn.execute('DELETE FROM calls WHERE day < ?', (cut,))
            conn.execute('DELETE FROM failures WHERE time < ?', (cut,))
        except sqlite3.Error:  # the next process start prunes
            pass
    return conn


def record(provider, endpoint, ok, ms, status=None, size=None, error=None, bad=False, detail=None, who=None, now=None):
    """Count one call. ok False = transport/HTTP failure; bad True = answered but unusable (problem())."""
    if not ENABLED:
        return
    try:
        now = now or datetime.now(KST)
        stamp, who = now.isoformat(timespec='seconds'), who or caller()
        slot = f'{now:%H}:{now.minute // SLOT_MIN * SLOT_MIN:02d}'
        failed = not ok or bad
        with _lock:
            db = _db()
            db.execute("""INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT (day, slot, provider, endpoint, caller) DO UPDATE SET n = n + excluded.n, fail = fail + excluded.fail,
                bad = bad + excluded.bad, ms_sum = ms_sum + excluded.ms_sum, ms_max = max(ms_max, excluded.ms_max),
                bytes = bytes + excluded.bytes, last_ok = nullif(max(coalesce(excluded.last_ok, ''), coalesce(last_ok, '')), ''),
                last_fail = nullif(max(coalesce(excluded.last_fail, ''), coalesce(last_fail, '')), '')""",
                       (f'{now:%Y-%m-%d}', slot, provider, endpoint, who, 0 if bad and ok is None else 1, int(not ok and ok is not None),
                        int(bad), ms or 0.0, ms or 0.0, size or 0, None if failed else stamp, stamp if failed else None))
            if failed:
                db.execute('INSERT INTO failures VALUES (?,?,?,?,?,?,?)', (stamp, provider, endpoint, who,
                           'response' if bad else 'http' if status else 'network', status, mask(detail or error)))
    except Exception:  # noqa: BLE001 - logging must never break a trading process
        pass


def problem(provider, endpoint, detail):
    """An answered call whose content was an error (Kiwoom return_code, NH rsp_cd...). Not counted as a new call."""
    record(provider, endpoint, None, 0, bad=True, detail=detail)


@contextmanager
def track(provider, endpoint):
    """For clients the hook cannot see (yfinance): with track('yahoo', 'yfinance/download'): ..."""
    started = time.monotonic()
    try:
        yield
    except Exception as exc:
        record(provider, endpoint, False, (time.monotonic() - started) * 1000, error=type(exc).__name__)
        raise
    record(provider, endpoint, True, (time.monotonic() - started) * 1000)


def mcp_call(server, tool, arguments, started, result, error):
    """From the MCP adapter: one NH tool call (call_api is named by its operationId). A local pre-send rejection sent
    nothing to NH and is not a call; a tool error is an answered call with an unusable result."""
    if 'nhplug' not in (server or '') or type(error).__name__ == 'NHPreSendCheckFailed':
        return
    name = (arguments or {}).get('operationId') if tool in ('call_api', 'call_api_guarded') else None
    endpoint, ms = f'mcp:{name or tool}', (time.monotonic() - started) * 1000
    if error is not None:
        record('nh', endpoint, False, ms, error=type(error).__name__)
    else:
        bad = bool(getattr(result, 'is_error', False))
        record('nh', endpoint, True, ms, bad=bad, detail=tool_error(result) if bad else None)


def tool_error(result):
    """'tool error' plus the NH MCP error category and code when it gives them (rate_limit IGW42902 = HTTP 429), so a
    refused burst is told apart from other errors (2026-11-18). Only those two fields are kept, never the message."""
    text = ' '.join(str(getattr(c, 'text', '') or '') for c in (getattr(result, 'content', None) or []))
    found = [m.group(1) for key in ('category', 'code') if (m := re.search(rf'"{key}"\s*:\s*"([A-Za-z0-9_]{{1,40}})"', text))]
    return ' '.join(['tool error:', *found]) if found else 'tool error'


def _http(url, api_id, started, status, size, error):
    try:
        host = urlsplit(url).hostname
        if not host or host in LOCAL:
            return
        ok = error is None and status is not None and status < 400
        record(provider_of(host), endpoint_of(url, api_id), ok, (time.monotonic() - started) * 1000, status=status, size=size,
               error=type(error).__name__ if error is not None else f'HTTP {status}' if not ok else None)
    except Exception:  # noqa: BLE001 - the request already went out; never turn logging into its failure
        pass


@contextmanager
def _outer():
    """True only for the outermost hooked call of this thread (redirect hops re-enter the hook)."""
    depth = getattr(_depth, 'n', 0)
    _depth.n = depth + 1
    try:
        yield depth == 0
    finally:
        _depth.n = depth


def install():
    """Wrap the two HTTP stacks once per process. The originals run unchanged; only timing and outcome are noted."""
    try:
        import requests
        if not getattr(requests.Session.send, '_api_calls', False):
            original = requests.Session.send

            def send(self, request, **kwargs):
                with _outer() as outer:
                    started, response = time.monotonic(), None
                    try:
                        response = original(self, request, **kwargs)
                        return response
                    except Exception as exc:
                        if outer:
                            _http(request.url, request.headers.get('api-id'), started, None, None, exc)
                        raise
                    finally:
                        if outer and response is not None:
                            try:
                                size = None if kwargs.get('stream') else len(response.content or b'')
                            except Exception:  # noqa: BLE001
                                size = None
                            _http(request.url, request.headers.get('api-id'), started, response.status_code, size, None)
            send._api_calls = True
            requests.Session.send = send
    except ImportError:
        pass
    import urllib.error
    import urllib.request
    if not getattr(urllib.request.OpenerDirector.open, '_api_calls', False):
        original_open = urllib.request.OpenerDirector.open

        def open_(self, fullurl, *args, **kwargs):
            with _outer() as outer:
                if not outer:
                    return original_open(self, fullurl, *args, **kwargs)
                url = fullurl if isinstance(fullurl, str) else fullurl.full_url
                started = time.monotonic()
                try:
                    response = original_open(self, fullurl, *args, **kwargs)
                except urllib.error.HTTPError as exc:
                    _http(url, None, started, exc.code, None, None)
                    raise
                except Exception as exc:
                    _http(url, None, started, None, None, exc)
                    raise
                length = response.headers.get('Content-Length') if getattr(response, 'headers', None) else None
                _http(url, None, started, getattr(response, 'status', 200), int(length) if str(length or '').isdigit() else None, None)
                return response
        open_._api_calls = True
        urllib.request.OpenerDirector.open = open_


def _read(sql, args=()):
    """Rows as dicts; [] while the log is missing or has no tables yet (a reader never creates it)."""
    if not DB.exists():
        return []
    from sqlite_ro import ro_connect
    try:
        with ro_connect(DB, timeout=1) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute(sql, args)]
    except sqlite3.Error:
        return []


def rows(day_from, day_to=None):
    """Slot rows between two days (inclusive, 'YYYY-MM-DD') as dicts; [] when the log does not exist yet."""
    return _read('SELECT * FROM calls WHERE day BETWEEN ? AND ? ORDER BY day, slot', (day_from, day_to or day_from))


def failures(since, limit=1000, provider=None):
    """Newest failures since an ISO time or day, of one provider or all."""
    return _read('SELECT * FROM failures WHERE time >= ? AND (? IS NULL OR provider = ?) ORDER BY time DESC LIMIT ?',
                 (since, provider, provider, limit))


def last_seen():
    """provider -> (last ok, last failure) over the whole kept log."""
    return {r['provider']: (r['ok'], r['bad']) for r in
            _read('SELECT provider, max(last_ok) AS ok, max(last_fail) AS bad FROM calls GROUP BY provider')}

install()
