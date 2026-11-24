import os
import tempfile

# Tests use the code defaults, not the operator's tunables.json, and keep settings history out of the real runtime.
os.environ.setdefault('VIBE_TRADING_TUNABLES', os.devnull)
os.environ.setdefault('VIBE_TRADING_OVERSOLD_CASES', os.path.join(tempfile.mkdtemp(prefix='oversold-cases-'), 'none.sqlite3'))
os.environ.setdefault('VIBE_TRADING_SETTINGS_HISTORY', os.path.join(tempfile.mkdtemp(prefix='settings-history-'), 'h.sqlite3'))
# As scripts/conftest.py: agent/tests/conftest.py's sandbox HOME must not hide the installed Archify (r5).
try:
    import pwd  # noqa: E402
    _home = pwd.getpwuid(os.getuid()).pw_dir
except ImportError:  # Windows
    _home = os.path.expanduser('~')
os.environ.setdefault('ARCHIFY_BIN', os.path.join(_home, '.claude/skills/archify/bin/archify.mjs'))

import sys

import pytest
from pathlib import Path
from streamlit.testing.v1 import AppTest

# AppTest's 3-second default fails renders that are only slow because the machine is busy (a full-suite run, a cold
# matplotlib font cache); 30 seconds still catches a hung script.
_run = AppTest.run
AppTest.run = lambda self, *, timeout=None: _run(self, timeout=timeout if timeout is not None else max(self.default_timeout, 30))


@pytest.fixture(autouse=True)
def _ordinary_exchange_clock(monkeypatch):
    """Tests use ordinary 09:00~15:30 days; a test of a special-hours day (수능) sets nhplug_calendar.SHIFTED_DAYS itself."""
    import src.tools.nhplug_calendar as calendar
    monkeypatch.setattr(calendar, 'SHIFTED_DAYS', {})


@pytest.fixture(autouse=True)
def _restore_main_module():
    """Streamlit AppTest leaves sys.modules['__main__'] set to its temporary script; a later test that starts a
    spawn-mode process (multiprocessing on macOS) would re-run that script as the child's __main__ and die."""
    main = sys.modules['__main__']
    yield
    sys.modules['__main__'] = main


@pytest.fixture(autouse=True)
def _no_broker_keys(monkeypatch, tmp_path):
    """Like scripts/conftest.py: tests never call Kiwoom or Toss with the operator's live keys nor write their token or
    bar cache (trading_journal.chart_data tries Kiwoom 1-minute bars before Yahoo)."""
    import kiwoom_data
    import toss_data
    for module, cache in ((kiwoom_data, 'kiwoom'), (toss_data, 'toss')):
        monkeypatch.setattr(module, '_keys', lambda: ('', ''))
        monkeypatch.setattr(module, 'TOKEN_FILE', tmp_path / f'{cache}-token.json')
    monkeypatch.setattr(kiwoom_data, 'BARS_DIR', tmp_path / 'kiwoom-minute')


@pytest.fixture(autouse=True)
def _no_krx_holiday_download(monkeypatch):
    """Dashboard tests never ask KRX for its holiday table (nh_paper_isolated.is_trading_day backs the session and API
    cadence checks): a year without holidays unless the test sets its own."""
    import nh_paper_isolated
    monkeypatch.setattr(nh_paper_isolated, '_HOLIDAYS', {year: (frozenset(), 0.0) for year in range(2024, 2031)})


@pytest.fixture(autouse=True)
def _isolated_api_log(monkeypatch, tmp_path):
    """Dashboard tests read an empty API call log, never the live one (its failures would leak into alert lists)."""
    import api_calls
    import api_status_view
    monkeypatch.setattr(api_calls, 'DB', tmp_path / 'api-calls.sqlite3')
    monkeypatch.setattr(api_calls, '_conn', {})
    for cached in (api_status_view.load, api_status_view.provider_failures, api_status_view.credentials):
        cached.clear()  # st.cache_data lives in the process: another test's log must not answer this one


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
