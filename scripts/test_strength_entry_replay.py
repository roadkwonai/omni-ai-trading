import json
import sqlite3
import sys
from types import SimpleNamespace

import strength_entry_replay as replay


def make_db(path, sql):
    with sqlite3.connect(path) as db:
        for statement, args in sql:
            db.execute(statement, args)


def test_replays_qualifying_candidate_and_records_missing_model(tmp_path, monkeypatch):
    time = '2026-11-26T10:06:02+09:00'
    gate = {'time': time, 'event': 'submitted', 'cycle_id': 'cycle', 'symbol': '100790', 'source': 'watchlist_strength',
            'entry_timing': {'gate_time': time, 'ask_vs_recent_low_pct': 1.946}, 'open_gap_pct': 21.66, 'high_gap_pct': -3.34}
    context = {'cycle_id': 'cycle', 'context': 'entry', 'candidates': {'100790': {'index': 'KOSDAQ150'}}}
    other = {**gate, 'cycle_id': 'second', 'time': '2026-11-26T10:20:02+09:00',
             'entry_timing': {'gate_time': '2026-11-26T10:20:02+09:00'}}
    make_db(tmp_path / 'trading-ledger.sqlite3', [
        ('CREATE TABLE events_v2(time TEXT,event TEXT,data TEXT)', ()),
        ('INSERT INTO events_v2 VALUES(?,?,?)', (time, 'submitted', json.dumps(gate))),
        ('INSERT INTO events_v2 VALUES(?,?,?)', (time, 'market_context', json.dumps(context))),
        ('INSERT INTO events_v2 VALUES(?,?,?)', (other['time'], 'submitted', json.dumps(other))),
    ])
    state = {'market_regime_themes_global': {'코스닥': {'today_tone': '강세'}},
             'recent_news_headlines': [{'published': time}], 'watchlist_details': {'100790': {'headlines': []}}}
    make_db(tmp_path / 'market-research.sqlite3', [
        ('CREATE TABLE model_inputs(time TEXT,engine TEXT,question TEXT,inputs TEXT,response TEXT)', ()),
        ('INSERT INTO model_inputs VALUES(?,?,?,?,?)', ('2026-11-26T10:06:01+09:00', 'jev', 'candidate',
          json.dumps({'state': state}), json.dumps({'choice': 'WAIT'}))),
    ])
    make_db(tmp_path / 'orders.sqlite3', [
        ('CREATE TABLE fills(order_id TEXT,symbol TEXT,side TEXT,quantity INTEGER,price REAL,time TEXT)', ()),
        ('CREATE TABLE decision_order_links(order_id TEXT,cycle_id TEXT)', ()),
        ('INSERT INTO fills VALUES(?,?,?,?,?,?)', ('o', '100790', 'BUY', 25, 19373, time)),
        ('INSERT INTO decision_order_links VALUES(?,?)', ('o', 'cycle')),
    ])
    seen = []

    def review(**kw):
        seen.append(kw)
        return ['jev_wait'] if kw['model_choice'] == 'WAIT' else ['jev_missing']

    monkeypatch.setitem(sys.modules, 'strength_entry_review', SimpleNamespace(review=review))
    result = replay.replay(tmp_path, '2026-11-26')['results']
    assert result[0]['reasons'] == ['jev_wait'] and result[0]['model_link'] == 'time_proximity_only'
    assert result[0]['entry_fills'][0]['quantity'] == 25
    assert seen[0]['index'] == '코스닥' and seen[0]['timing']['open_gap_pct'] == 21.66
    assert result[1]['reasons'] == ['jev_missing'] and result[1]['index'] is None
