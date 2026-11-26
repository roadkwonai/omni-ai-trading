import json
from pathlib import Path
import subprocess
import sys

import pytest


DAY = '2026-11-25'
SYMBOL = '005930'
CYCLE = 'a' * 32
DIGEST = 'b' * 64
SCRIPT = Path(__file__).with_name('jev_entry_replay.py')


def at(clock):
    return f'{DAY}T{clock}+09:00'


@pytest.fixture
def evidence():
    return {
        'day': DAY, 'cycle_id': CYCLE, 'symbol': SYMBOL, 'input_digest': DIGEST,
        'verdict': 'BUY', 'judgment_model': 'jev-1.14.0', 'confidence': 0.9,
        'probabilities': {'BUY': 0.95, 'WAIT': 0.05}, 'decided_at': at('10:00:05'), 'checked_at': at('10:00:25'),
        'signal_expires_at': at('10:01:00'), 'quote_at': at('10:00:24'),
        'ask': 10000, 'bid': 9990, 'qty': 2, 'available_cash': 25000, 'remaining_room': 20000,
        'rules_permit': True, 'pending': False, 'first_reconciled_at': at('10:00:23'),
        'exit_bid': 10500,
    }


def replay(tmp_path, evidence=None, *, fallback=False, first_symbol='000660', intent_status='terminal',
           gate_choice='000660', model_choice='000660', first_side='BUY'):
    report_dir = tmp_path / 'reports'
    report_dir.mkdir(exist_ok=True)
    (report_dir / f'{DAY}.json').write_text(json.dumps({
        'day': DAY, 'source': '/example/archive', 'engine': 'jev',
        'cycles': [{
            'cycle_id': CYCLE, 'model_second_symbol': SYMBOL, 'input_digest': DIGEST, 'input_linked': True,
            'response_time': at('10:00:00'), 'execution_status': 'blocked' if fallback else 'filled',
            'model_choice': model_choice,
            'gates': [{'permitted': False, 'choice': gate_choice, 'time': at('10:00:15'),
                       'event': 'v2_entry_gate', 'reason': 'trend_not_rising'}] if fallback else [],
            'submissions': [] if fallback else [{'symbol': first_symbol, 'time': at('10:00:08')}],
            'intents': [] if fallback else [{'symbol': first_symbol, 'side': first_side, 'quantity': 1, 'status': intent_status}],
            'fills': [] if fallback else [{'symbol': first_symbol, 'side': first_side, 'time': at('10:00:20'),
                                           'price': 10000, 'quantity': 1}],
        }],
        'candidates': [{'cycle_id': CYCLE, 'symbol': SYMBOL, 'model_rank': 2, 'input_digest': DIGEST,
                        'next_buy_fill_time': at('10:03:00')}],
    }), encoding='utf-8')
    cmd = [sys.executable, str(SCRIPT), '--reports', str(report_dir), '--fee-bps', '15']
    if evidence is not None:
        evidence_path = tmp_path / 'evidence.json'
        rows = evidence if isinstance(evidence, list) else [evidence]
        evidence_path.write_text(json.dumps(rows), encoding='utf-8')
        cmd += ['--evidence', str(evidence_path)]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def result_of(process):
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout)


def test_archive_rank_alone_cannot_confirm_opportunity(tmp_path):
    result = result_of(replay(tmp_path))
    assert result['counts']['modeled'] == 0
    assert result['counts']['unknown'] == 1


@pytest.mark.parametrize(('fallback', 'mode'), [(True, 'fallback'), (False, 'sequential')])
def test_models_a_single_alternative_only_with_paired_evidence(tmp_path, evidence, fallback, mode):
    if fallback:
        del evidence['first_reconciled_at']
    first = replay(tmp_path, evidence, fallback=fallback)
    case = result_of(first)['cases'][0]
    assert case['status'] == 'modeled'
    assert case['mode'] == mode
    assert case['net_pnl_krw'] == 938.5
    assert replay(tmp_path, evidence, fallback=fallback).stdout == first.stdout


@pytest.mark.parametrize('change', [
    {'pending': True},
    {'verdict': 'WAIT'},
    {'rules_permit': False},
    {'remaining_room': 19999},
    {'checked_at': at('10:01:00'), 'quote_at': at('10:00:59')},
    {'quote_at': at('10:00:21')},
])
def test_pending_refusal_capacity_or_stale_evidence_blocks_order(tmp_path, evidence, change):
    result = result_of(replay(tmp_path, {**evidence, **change}))
    assert result['counts']['modeled'] == 0
    assert result['cases'][0]['status'] == 'blocked'


def test_wrong_digest_does_not_authorize_hypothetical_entry(tmp_path, evidence):
    result = result_of(replay(tmp_path, {**evidence, 'input_digest': 'c' * 64}))
    assert result['counts']['modeled'] == 0
    assert result['cases'][0]['status'] == 'unknown'


def test_already_bought_second_candidate_cannot_be_bought_again(tmp_path, evidence):
    result = result_of(replay(tmp_path, evidence, first_symbol=SYMBOL))
    assert result['counts']['modeled'] == 0


def test_open_order_in_baseline_overrides_evidence(tmp_path, evidence):
    result = result_of(replay(tmp_path, evidence, intent_status='submitted'))
    assert result['counts']['modeled'] == 0
    assert result['cases'][0]['status'] == 'blocked'


def test_duplicate_evidence_is_rejected(tmp_path, evidence):
    assert replay(tmp_path, [evidence, evidence]).returncode != 0


def test_fallback_recognizes_etf_alias_in_archived_rejection(tmp_path, evidence):
    result = result_of(replay(tmp_path, evidence, fallback=True, gate_choice='INV', model_choice='114800'))
    assert result['counts']['modeled'] == 1


def test_sell_fill_cannot_establish_a_first_buy(tmp_path, evidence):
    result = result_of(replay(tmp_path, evidence, first_side='SELL'))
    assert result['counts']['baseline_buy_cycles'] == 0
    assert result['counts']['modeled'] == 0


@pytest.mark.parametrize(('confidence', 'probabilities', 'reason'), [
    (0.02, {'BUY': 0.51, 'WAIT': 0.49}, 'low_confidence'),
    (0.9, None, 'judgment_confidence_unverified'),
    (0.9, {'BUY': 0.48, 'WAIT': 0.52}, 'judgment_confidence_unverified'),
    (0.9, {'BUY': 0.95, 'WAIT': 0.15}, 'judgment_confidence_unverified'),
    (0.9, {'BUY': 0.51, 'WAIT': 0.49}, 'judgment_confidence_unverified'),
])
def test_buy_requires_verified_choice_confidence(tmp_path, evidence, confidence, probabilities, reason):
    result = result_of(replay(tmp_path, {**evidence, 'judgment_model': 'jev-1.14.0',
                                       'confidence': confidence, 'probabilities': probabilities}))
    assert result['counts']['modeled'] == 0
    assert result['cases'][0]['reason'] == reason


def test_cash_must_cover_entry_fee_as_well_as_notional(tmp_path, evidence):
    result = result_of(replay(tmp_path, {**evidence, 'available_cash': 20000}))
    assert result['counts']['modeled'] == 0
    assert result['cases'][0]['reason'] == 'insufficient_capital'
