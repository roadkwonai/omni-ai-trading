"""One KRX short-code rule (etf_universe.CODE_PATTERN / is_code): codes listed since 2024 carry a letter (0153R0)."""
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import etf_universe
import nh_live_follow as f
import nh_live_research_gate as gate
import oversold
import premarket

LETTER = '0153R0'  # the example in etf_universe's own comment
REPO = Path(__file__).resolve().parents[1]


def test_oversold_universe_keeps_a_letter_code_and_writes_only_the_test_history(tmp_path, monkeypatch):
    real = Path.home() / '.vibe-trading/nhplug/settings-history.sqlite3'
    before = real.stat().st_mtime_ns if real.exists() else None
    history = tmp_path / 'history.sqlite3'
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(history))
    assert oversold.save_universe({'005930': '삼성전자', LETTER: 'ETF', 'bad': 'x', '00593a': 'x'}, tmp_path) == \
        {'005930': '삼성전자', LETTER: 'ETF'}
    assert oversold.universe(tmp_path) == {'005930': '삼성전자', LETTER: 'ETF'}
    assert sqlite3.connect(history).execute("SELECT count(*) FROM changes WHERE kind='oversold_universe'").fetchone()[0] > 0
    assert (real.stat().st_mtime_ns if real.exists() else None) == before


def test_premarket_watches_a_held_letter_code(tmp_path):
    (tmp_path / 'account-state.json').write_text(json.dumps({'holdings': {LETTER: {'rsdl_qty': 1}, 'abcdef': {}}}))
    roles = premarket.watch(tmp_path)
    assert roles[LETTER] == '보유' and 'abcdef' not in roles


def test_research_gate_counts_a_letter_code_fill(tmp_path):
    with sqlite3.connect(tmp_path / 'orders.sqlite3') as db:
        db.execute('CREATE TABLE meta (name TEXT, value TEXT)')
        db.execute('INSERT INTO meta VALUES (?,?)', ('account_sha256', 'a' * 64))
        db.execute('CREATE TABLE intents (id TEXT, symbol TEXT, side TEXT, qty INTEGER, status TEXT, order_number INTEGER)')
        db.execute('CREATE TABLE fills (order_id TEXT, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT)')
        for n, side in ((1, 'BUY'), (2, 'SELL')):
            db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?)', (side, LETTER, side, 1, 'terminal', n))
            db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', (side, LETTER, side, 1, 10000, '2026-11-11T11:00:00+09:00'))
    fills = gate.evaluate(tmp_path, datetime(2026, 11, 11, 12, 0, tzinfo=ZoneInfo('Asia/Seoul')))['paper_fills']
    assert fills['available'] and (fills['local_reconciled_fills'], fills['closed_units']) == (2, 1)


def test_live_mirror_requests_keep_valid_codes_and_drop_look_alikes(tmp_path):
    # live mirror: every valid code reads as before; six-character junk that isalnum() let through is dropped
    exits, buys = tmp_path / 'exits.json', tmp_path / 'buys.json'
    exits.write_text(json.dumps({'005930': None, LETTER: 3, 'ABCDEF': None, '00593a': 1, '삼성전자00': None}))
    buys.write_text(json.dumps({'005930': 2, LETTER: 1, 'ABCDEF': 1, '0059３0': 1}))
    assert f._exit_requests(exits) == {'005930': None, LETTER: 3}
    assert f._buy_requests(buys) == {'005930': 2, LETTER: 1}
    exits.write_text(json.dumps(['005930', LETTER, 'ABCDEF']))
    assert f._exit_requests(exits) == {'005930': None, LETTER: None}


def test_agent_guards_spell_the_same_code_rule():
    # agent/src/tools cannot import scripts/etf_universe: every KRX code regex there must be the same rule
    found = [m for path in sorted((REPO / 'agent/src/tools').glob('nhplug_*.py'))
             for m in re.findall(r"fullmatch\(r'(\[0-9\][^']*)'", path.read_text(encoding='utf-8')) if 'A-Z' in m]
    assert len(found) >= 3 and set(found) == {etf_universe.CODE_PATTERN}
