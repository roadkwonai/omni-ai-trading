from contextlib import nullcontext
from datetime import date, timedelta
import io
import json
import sqlite3
import sys

import pytest

import jev_program_paper as p

DAY = date(2025, 11, 25)
START = p.clock(DAY, '09:00')


def point(minute, buy, sell):
    return {'tm': (START + minute * p.MINUTE).strftime('%H%M%S'), 'prm_buy_qty': str(buy),
            'prm_sell_qty': str(sell), 'prm_netprps_qty': f'{buy - sell:+d}'}


@pytest.fixture
def capture():
    rows = [point(5, 100, 600), point(10, 300, 600), point(15, 300, 1000)]
    bars = []
    for i in range(17):
        price = 10_000 if i < 5 else 10_050 + (i - 5) * 20 if i < 10 else 10_090 - (i - 10) * 25
        volume = 100 if i < 5 else 200 if i < 10 else 300
        bars.append((START + i * p.MINUTE, price, price, price, price, volume))
    return rows, bars


def test_recent_flow_and_closed_bars_confirm_entry_and_exit(capture):
    tape = p.Tape(DAY, *capture)
    buy = tape.signal(START + 10 * p.MINUTE)
    sell = tape.signal(START + 15 * p.MINUTE)
    assert (buy['net_shares'], buy['recent_net_shares'], buy['direction']) == (-300, 200, 'buy_pressure')
    assert p.proposed_action(buy, held=False) == 'review_entry'
    assert p.proposed_action(sell, held=False) == 'defer_entry'
    assert p.proposed_action(sell, held=True) == 'review_exit'
    assert p.proposed_action(buy, held=True) == 'hold'
    assert tape.chart(START + 10 * p.MINUTE)[0]['bsop_time'] == '090900'


def test_missing_stale_and_opposite_chart_are_non_trading(capture):
    rows, bars = capture
    at = START + 10 * p.MINUTE
    tape = p.Tape(DAY, [point(5, 100, 600), point(10, 100, 800)], bars)
    assert tape.signal(at)['direction'] == 'neutral'  # recent sell flow, but the chart rose
    assert p.Tape(DAY, rows, bars[:-8]).signal(at)['status'] == 'chart_window_missing'
    assert p.Tape(DAY, rows[1:], bars).signal(at)['status'] == 'program_missing_or_stale'
    assert p.Tape(DAY, [rows[0], point(10, 50, 700)], bars).signal(at)['status'] == 'program_cumulative_reset'
    assert p.Tape(DAY, [rows[0], point(10, 50_000, 700)], bars).signal(at)['status'] == 'program_volume_mismatch'
    assert p.proposed_action(tape.signal(at + timedelta(minutes=2)), held=True) == 'hold'


def test_feed_conflicts_resets_and_misaligned_windows_do_not_trade(capture):
    rows, bars = capture
    at = START + 10 * p.MINUTE
    slipped = [{**rows[0], 'tm': '090455'}, {**rows[1], 'tm': '090905'}]
    assert p.Tape(DAY, slipped, bars).signal(at)['status'] == 'program_window_misaligned'
    assert p.Tape(DAY, [rows[0], point(7, 50, 500), rows[1]], bars).signal(at)['status'] == 'program_cumulative_reset'
    assert p.Tape(DAY, [{**row, 'stex_tp': 'NXT'} for row in rows], bars).signal(at)['status'] == 'program_missing_or_stale'
    conflicting = (*bars[9][:1], 10_000, 10_000, 10_000, 10_000, 200)
    assert p.Tape(DAY, rows, [*bars, conflicting]).signal(at)['status'] == 'chart_window_missing'


@pytest.mark.parametrize('from_audit', [False, True])
def test_replay_cli_has_delayed_fill_and_cost(capture, tmp_path, monkeypatch, capsys, from_audit):
    rows, bars = capture
    snapshot = tmp_path / 'capture.json'
    quote = {'symbol': '005930', 'day': str(DAY), 'captured_at': (START + 17 * p.MINUTE).isoformat(),
             'program': rows, 'bars': [[t.isoformat(), *v] for t, *v in bars]}
    snapshot.write_text(json.dumps({'captures': {'005930': quote}} if from_audit else quote))
    monkeypatch.setattr(sys, 'argv', ['jev_program_paper', '005930', '--input', str(snapshot), '--at', '09:10'])
    p.main()
    result = json.loads(capsys.readouterr().out)
    fills = result['replay']['fills']
    assert [(r['time'][11:16], r['side']) for r in fills] == [('09:11', 'buy'), ('09:16', 'sell')]
    assert result['replay']['realized_net_krw'] == round(fills[1]['price'] * .9985 - fills[0]['price'] * 1.0015, 2)
    assert result['replay']['open_shares'] == 0


def test_jev_receives_only_known_chart_and_flow_in_isolated_request(capture, tmp_path, monkeypatch):
    import etf_news
    import jev_activity
    import jev_context
    import nh_etf_auto_v2 as model

    monkeypatch.setattr(p, 'RESEARCH', tmp_path / 'isolated')
    monkeypatch.setattr(model.old, 'dotenv_values', lambda *a, **kw: {'JEV_API_KEY': 'test'})
    monkeypatch.setattr(jev_activity, 'regular_request', nullcontext)
    monkeypatch.setattr(jev_context, 'context', lambda *_: pytest.fail('read production chart history'))
    monkeypatch.setattr(etf_news, 'supplement_jev_topics', lambda *_: pytest.fail('read news from after replay time'))
    requests = []

    def fake_model(req, timeout):
        payload = json.loads(req.data)
        requests.append(payload)
        question = next(iter(payload['questions']))
        return io.StringIO(json.dumps({'model': model.JEV_MODEL, 'answers': {question: {
            'type': 'choice', 'choice': 'BUY' if question == 'program_entry' else 'SELL', 'confidence': .8}}}))

    monkeypatch.setattr(model.urllib.request, 'urlopen', fake_model)
    tape = p.Tape(DAY, *capture)
    at = START + 10 * p.MINUTE
    buy = tape.signal(at)
    assert p.ask_jev('005930', tape, at, buy, held=False)['action'] == 'review_entry'
    at = START + 15 * p.MINUTE
    sell = tape.signal(at)
    assert p.ask_jev('005930', tape, at, sell, held=False)['action'] == 'defer_entry'
    assert p.ask_jev('005930', tape, at, sell, held=True)['action'] == 'review_exit'
    assert requests[0]['state']['symbol_program_flow'] == buy
    assert requests[0]['state']['chart_as_of'] == {'005930': '090900'}
    assert set(requests[0]['questions']) == {'program_entry'}
    assert 'recent_news_headlines' not in requests[0]['state']
    assert len(requests[0]['assets']) == 1
    with sqlite3.connect(p.RESEARCH / 'judgments.sqlite3') as db:
        assert db.execute('SELECT DISTINCT engine FROM model_inputs').fetchall() == [('jev_program_paper',)]
    assert not (tmp_path / 'orders.sqlite3').exists()
