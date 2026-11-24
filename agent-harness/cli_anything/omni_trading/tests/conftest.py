"""Every test runs on a temporary HOME; the real settings' hashes must be the same afterwards."""
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

REAL = Path(os.path.expanduser('~')) / '.vibe-trading'
WATCHED = ['nhplug/accounts.json', 'nhplug/tunables.json', 'nhplug/shared-etf-paper/strategy-settings.json', 'nhplug/vibe-longterm/settings.json',
           'nhplug/settings-history.sqlite3', 'nhplug/dashboard-prefs.json', 'cli/cli.sqlite3', 'live/HALT', 'nhplug/LIVE_BUY_PAUSED']


def digests():
    return {n: hashlib.sha256((REAL / n).read_bytes()).hexdigest() if (REAL / n).exists() else None for n in WATCHED}


@pytest.fixture(scope='session', autouse=True)
def real_settings_untouched():
    before = digests()
    yield
    assert digests() == before, '실제 설정이 바뀌었습니다'


def account(live=True, **kw):
    import accounts as A
    kind = 'live' if live else 'paper'
    return {'label': '실계좌 1' if live else '모의계좌 (공유)', 'kind': kind, 'engine': 'jev', 'auto': True, 'budget': dict(A.DEFAULT_BUDGET[kind]),
            **({'hash': 'h' * 64, 'masked': '***', 'live_orders': True} if live else {}), **kw}


def ours():
    folders = tuple(str(Path(__file__).resolve().parents[4] / d) + os.sep for d in ('scripts', 'streamlit_namuh'))
    return [m for m, module in sys.modules.items() if (getattr(module, '__file__', None) or '').startswith(folders)
            and not m.rpartition('.')[2].startswith(('test_', 'conftest'))]


@pytest.fixture
def home(tmp_path, monkeypatch):
    for name in list(os.environ):
        if name.startswith('VIBE_TRADING_'):
            monkeypatch.delenv(name)
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    empty = tmp_path / 'empty.env'
    empty.write_text('')
    monkeypatch.setenv('VIBE_TRADING_ENV_FILE', str(empty))
    for name in ours():
        monkeypatch.delitem(sys.modules, name)  # their paths are fixed at import: one another suite loaded would be the real runtime's
    from cli_anything.omni_trading.utils import omni_backend
    omni_backend.load()
    nh = tmp_path / '.vibe-trading/nhplug'
    (nh / 'shared-etf-paper').mkdir(parents=True)
    (nh / 'vibe-longterm').mkdir()
    write(nh, {'paper-shared': account(False), 'live-1': account(), 'live-2': account(engine='longterm', label='실계좌 2')})
    import strategy_settings as ST
    (nh / 'shared-etf-paper/strategy-settings.json').write_text(json.dumps(ST.DEFAULTS))
    (nh / 'shared-etf-paper/ai-modes.json').write_text(json.dumps({'version': 1, 'jev': True, 'decision': True}))
    import src.config.paths as paths
    assert str(paths.get_runtime_root()).startswith(str(tmp_path))
    yield nh
    for name in ours():  # what this test imported is bound to its temporary home; monkeypatch then puts the old modules back
        del sys.modules[name]


def write(nh, records):
    (nh / 'accounts.json').write_text(json.dumps({'version': 1, 'accounts': records}, ensure_ascii=False), encoding='utf-8')


@pytest.fixture
def quiet(home):
    """No account's real-order switch is on."""
    write(home, {'paper-shared': account(False), 'live-1': account(live_orders=False), 'live-2': account(engine='longterm', live_orders=False)})
    return home
