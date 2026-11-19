import os
import tempfile
import sys

# Tests use the code defaults, not the operator's tunables.json, and keep settings history out of the real runtime.
os.environ.setdefault('VIBE_TRADING_TUNABLES', os.devnull)
os.environ.setdefault('VIBE_TRADING_OVERSOLD_CASES', os.path.join(tempfile.mkdtemp(prefix='oversold-cases-'), 'none.sqlite3'))
os.environ.setdefault('VIBE_TRADING_NEWS_CACHE', os.path.join(tempfile.mkdtemp(prefix='news-cache-'), 'news.json'))
os.environ.setdefault('VIBE_TRADING_STOCK_NEWS_CACHE', os.path.join(tempfile.mkdtemp(prefix='stock-news-'), 'stock-news.json'))
os.environ.setdefault('VIBE_TRADING_SETTINGS_HISTORY', os.path.join(tempfile.mkdtemp(prefix='settings-history-'), 'h.sqlite3'))

import pytest
from pathlib import Path

import research_store


@pytest.fixture(autouse=True, scope='session')
def _isolated_research_store(tmp_path_factory):
    """Tests must never write into the live research database (model_inputs, snapshots)."""
    original = research_store.ROOT
    research_store.ROOT = tmp_path_factory.mktemp('research')
    yield
    research_store.ROOT = original


@pytest.fixture(autouse=True)
def _ordinary_exchange_clock(monkeypatch):
    """Tests use ordinary 09:00~15:30 days; a test of a special-hours day (수능) sets session_times.SHIFTED_DAYS itself."""
    import session_times
    monkeypatch.setattr(session_times, 'SHIFTED_DAYS', {})


@pytest.fixture(autouse=True)
def _no_stock_news_network(monkeypatch):
    """Tests never fetch real per-stock news (watchlist.fresh_stock_news falls back to the recorded headlines)."""
    import watchlist
    monkeypatch.setattr(watchlist, 'FETCH_STOCK_NEWS', lambda *a, **k: (_ for _ in ()).throw(OSError('no network in tests')))


@pytest.fixture(autouse=True)
def _isolated_jev_context(monkeypatch, tmp_path):
    """Jev's session history starts empty in each test and never reads the live runner's events."""
    import jev_context
    monkeypatch.setattr(jev_context, 'EVENTS', tmp_path / 'no-events.jsonl')
    monkeypatch.setitem(jev_context._DAY, 'day', None)
    monkeypatch.setitem(jev_context._DAY, 'bars', {})
    import jev_activity  # tests must not take the live runner's Jev request lock (it then skips a cycle) nor wait on it
    monkeypatch.setattr(jev_activity, 'LOCK', tmp_path / 'jev-regular-request.lock')


@pytest.fixture(autouse=True)
def _no_kiwoom_key(monkeypatch, tmp_path):
    """Tests never call Kiwoom or Toss with the operator's live keys nor write their token or bar cache."""
    import kiwoom_data
    monkeypatch.setattr(kiwoom_data, '_keys', lambda: ('', ''))
    monkeypatch.setattr(kiwoom_data, 'TOKEN_FILE', tmp_path / 'kiwoom-token.json')
    monkeypatch.setattr(kiwoom_data, 'BARS_DIR', tmp_path / 'kiwoom-minute')
    import toss_data
    monkeypatch.setattr(toss_data, '_keys', lambda: ('', ''))
    monkeypatch.setattr(toss_data, 'TOKEN_FILE', tmp_path / 'toss-token.json')


@pytest.fixture(autouse=True)
def _no_macro_network(monkeypatch):
    """Tests never call FRED with the operator's key nor fetch the Fed calendar."""
    import longterm_macro
    monkeypatch.setattr(longterm_macro, '_key', lambda: '')
    monkeypatch.setattr(longterm_macro, '_page', lambda: (_ for _ in ()).throw(OSError('no network in tests')))


@pytest.fixture(autouse=True)
def _no_longterm_news_network(monkeypatch):
    """The long-term engine's morning news (longterm_news) never reaches the network in tests; a test passes its own."""
    import longterm_news
    monkeypatch.setattr(longterm_news, 'collect', lambda now, root=None: {'as_of': now.isoformat(), 'rows': [], 'error': 'OSError'})


@pytest.fixture(autouse=True)
def _isolated_news_cache(tmp_path, monkeypatch):
    """Each test starts without cached headlines (etf_news shares one cache file across runners)."""
    monkeypatch.setenv('VIBE_TRADING_NEWS_CACHE', str(tmp_path / 'news-cache.json'))


@pytest.fixture(autouse=True)
def _isolate_quote_snapshot_writer():
    """Drain the background observer before temporary test directories are removed."""
    module = sys.modules.get('vibe_runner')
    original = getattr(module, '_QUOTE_SNAPSHOTS', None) if module else None
    if module:
        module._QUOTE_SNAPSHOTS = None
    yield
    module = sys.modules.get('vibe_runner')
    if module:
        pending = getattr(module, '_QUOTE_SNAPSHOTS', None)
        if pending is not None:
            pending.join()
        module._QUOTE_SNAPSHOTS = original


@pytest.fixture(autouse=True)
def _never_the_live_journal(monkeypatch):
    """trading_journal's functions default to the live journal DB (db=DB is bound at import), so a test that forgets
    `db=` would overwrite a real day (2026-11-17: 2026-11-16 was overwritten and restored). Writing there fails."""
    import trading_journal
    live = Path(trading_journal.DB).resolve()
    for name in ('store', 'store_period'):
        original = getattr(trading_journal, name, None)
        if original is None:
            continue
        def guarded(*args, _original=original, **kwargs):
            db = kwargs.get('db', args[1] if len(args) > 1 else trading_journal.DB)
            if Path(db).resolve() == live:
                raise RuntimeError('test tried to write the live trading journal DB')
            return _original(*args, **kwargs)
        monkeypatch.setattr(trading_journal, name, guarded)
