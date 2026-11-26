import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPTS = Path(__file__).parent
DAY = '2026-11-25'
CYCLE = 'a' * 32
DIGEST = 'b' * 64
SYMBOL = '005930'


def at(clock):
    return f'{DAY}T{clock}+09:00'


def event(kind, clock, **fields):
    return {
        'kind': kind, 'at': at(clock), 'scope': 'isolated_paper', 'day': DAY,
        'cycle_id': CYCLE, 'symbol': SYMBOL, 'input_digest': DIGEST, **fields,
    }


@pytest.fixture
def events():
    return [
        event('judgment', '10:00:05', verdict='BUY', judgment_source='fixture_independent',
              judgment_model='jev-1.14.0', confidence=0.9, probabilities={'BUY': 0.95, 'WAIT': 0.05},
              signal_expires_at=at('10:01:00')),
        event('quote', '10:00:24', ask=10000, bid=9990),
        event('check', '10:00:25', qty=2, available_cash=25000, held_limit=50000, held_exposure=25000,
              reserved_buy=5000, rules_permit=True, pending=False, first_reconciled_at=at('10:00:23'),
              exit_bid=10500),
    ]


def capture(events):
    tape = ''.join(json.dumps(row) + '\n' for row in events)
    return subprocess.run([sys.executable, str(SCRIPTS / 'jev_entry_capture.py'), '--events', '-'],
                          input=tape, capture_output=True, text=True, check=False)


def test_capture_feeds_the_stage3_command(events, tmp_path):
    captured = capture(events)
    assert captured.returncode == 0, captured.stderr
    [row] = json.loads(captured.stdout)
    assert row['remaining_room'] == 20000
    assert row['quote_at'] == at('10:00:24')
    assert row['verdict'] == 'BUY'
    assert row['judgment_source'] == 'fixture_independent'
    assert replay_evidence(captured.stdout, tmp_path)['cases'][0]['net_pnl_krw'] == 938.5
    assert capture(events).stdout == captured.stdout


def replay_evidence(payload, tmp_path):
    evidence = tmp_path / 'evidence.json'
    evidence.write_text(payload, encoding='utf-8')
    reports = tmp_path / 'reports'
    reports.mkdir()
    (reports / f'{DAY}.json').write_text(json.dumps({
        'day': DAY,
        'cycles': [{
            'cycle_id': CYCLE, 'input_digest': DIGEST, 'input_linked': True, 'model_second_symbol': SYMBOL,
            'response_time': at('10:00:00'), 'execution_status': 'filled',
            'submissions': [{'symbol': '000660'}],
            'intents': [{'symbol': '000660', 'side': 'BUY', 'status': 'terminal'}],
            'fills': [{'symbol': '000660', 'side': 'BUY', 'time': at('10:00:20')}],
        }],
        'candidates': [{'cycle_id': CYCLE, 'input_digest': DIGEST, 'symbol': SYMBOL, 'model_rank': 2}],
    }), encoding='utf-8')
    result = subprocess.run([sys.executable, str(SCRIPTS / 'jev_entry_replay.py'), '--reports', str(reports),
                             '--evidence', str(evidence)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('quote_changes', [
    {'cycle_id': 'c' * 32},
    {'input_digest': 'c' * 64},
    {'symbol': '000660'},
    {'at': at('10:00:19')},
    {'at': at('10:00:22')},
])
def test_never_reuses_incompatible_or_stale_quote(events, quote_changes):
    events[1].update(quote_changes)
    result = capture(events)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []


def test_future_quote_cannot_backfill_an_earlier_check(events):
    events[1]['at'] = at('10:00:26')
    result = capture([events[0], events[2], events[1]])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []


def test_unsafe_source_and_conflicting_checkpoint_return_no_partial_evidence(events):
    for extra in (
        {**events[2], 'scope': 'shared_paper'},
        {**events[2], 'reserved_buy': 9000},
    ):
        result = capture([*events, extra])
        assert result.returncode != 0
        assert not result.stdout


@pytest.mark.parametrize(('first_limit', 'expected_cash', 'expected_room', 'status'), [
    (10000, 25000, 20000, 'modeled'),
    (9999, 25001, 20001, 'blocked'),
])
def test_in_memory_paper_balance_is_collected_without_sending_candidate_order(
        events, tmp_path, first_limit, expected_cash, expected_room, status):
    from datetime import datetime

    from jev_entry_virtual_observation import virtual_checkpoint
    from virtual_paper_broker import VirtualBroker

    class PaperQuotes:
        def call_tool(self, tool, params):
            assert tool == 'get_stock_price'
            return {'status': 'ok', 'data': {'rsp_cd': '00000', 'Output_0': {
                'iem_cd': params['stock_code'], 'market_status': '1', 'hoga_bsop_hour': '10:00:20',
                'bidp': 9990, 'askp': 10000,
            }}}

    now = datetime.fromisoformat(at('10:00:20'))
    broker = VirtualBroker(':memory:', PaperQuotes(), cash=35000, clock=lambda: now, limits={'held_krw': 30000})
    try:
        assert broker.order('BUY', {'iem_cd': '000660', 'orr_qty': 1, 'orr_pr': first_limit})['status'] == 'ok'
        request = events[2].copy()
        for key in ('available_cash', 'held_limit', 'held_exposure', 'reserved_buy', 'pending'):
            request.pop(key)
        now = datetime.fromisoformat(request['at'])
        events[2] = virtual_checkpoint(broker, request)
        captured = capture(events)
        assert captured.returncode == 0, captured.stderr
        [evidence] = json.loads(captured.stdout)
        assert evidence['available_cash'] == expected_cash
        assert evidence['remaining_room'] == expected_room
        assert broker.db.execute('SELECT symbol FROM orders').fetchall() == [('000660',)]
        assert replay_evidence(captured.stdout, tmp_path)['counts'][status] == 1
    finally:
        broker.db.close()
