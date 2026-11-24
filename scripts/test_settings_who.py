"""Every settings save names who changed it (the CLI, 2026-11-24); the dashboard stays the default."""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'streamlit_namuh'))
import accounts
import engine_ops
import oversold
import paper_ai_modes
import prefs
import strategy_settings
import watchlist


def who(nhplug, kind):
    with sqlite3.connect(nhplug / 'settings-history.sqlite3') as db:
        return {row[0] for row in db.execute('SELECT who FROM changes WHERE kind=?', (kind,))}


@pytest.fixture
def nhplug(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', '')  # the history next to the copy
    root = tmp_path / 'nhplug'
    (root / 'shared-etf-paper').mkdir(parents=True)
    live = {'label': '실계좌 1', 'kind': 'live', 'engine': 'jev', 'auto': True, 'hash': 'x', 'budget': accounts.DEFAULT_BUDGET['live']}
    accounts.save({**accounts.load(root / 'none.json'), 'live-1': live}, root / 'accounts.json')
    (root / 'settings-history.sqlite3').unlink()
    return root


def test_accounts_saves_name_who_and_the_matched_paper_keeps_the_same_name(nhplug):
    path = nhplug / 'accounts.json'
    accounts.set_match('live-1', 2, True, path, who='cli:a')
    accounts.save_one('live-1', {'budget': {**accounts.DEFAULT_BUDGET['live'], 'capital_krw': 3_000_000}}, path, who='cli:a')
    accounts.set_live_orders('live-1', True, path, who='cli:a')
    assert who(nhplug, 'accounts') == {'cli:a'}
    accounts.save_one('live-1', {'principal_krw': 1}, path)
    assert who(nhplug, 'accounts') == {'cli:a', 'dashboard'}


def test_the_other_settings_name_who(nhplug):
    shared = nhplug / 'shared-etf-paper'
    strategy_settings.write_settings(shared, {**strategy_settings.DEFAULTS, 'add_step_pct': 0.9}, who='cli:a')
    paper_ai_modes.write_modes(shared, jev=True, decision=False, who='cli:a')
    prefs.save(nhplug / 'dashboard-prefs.json', {'refresh_s': 60}, who='cli:a')
    watchlist.edit_manual(add='005930', path=shared / 'watchlist-manual.json', who='cli:a')
    oversold.save_universe({'005930': '삼성전자'}, shared, who='cli:a')
    engine_ops.set_mode(nhplug, 'vibe', who='cli:a')
    for kind in ('strategy', 'ai_modes', 'prefs', 'watchlist:watchlist-manual', 'oversold_universe', 'accounts'):
        assert who(nhplug, kind) == {'cli:a'}, kind
