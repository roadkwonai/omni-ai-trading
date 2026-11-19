import hashlib
import json
import sqlite3
import subprocess
import sys
import os
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from test_vibe_event_advisor import NOW, row
import vibe_event_jev as jev


def inputs():
    item = row(position=hashlib.sha256(b'100790\0fill-a').hexdigest()[:20])
    item['event_trigger']['inputs'].update(entry_price=100, bid=101)
    prior_at = (NOW - timedelta(minutes=5)).isoformat()
    context = {'market': {'status': 'ok', 'observed_at': prior_at, 'data': {'regime': '횡보장'}},
               'news': {'status': 'ok', 'observed_at': prior_at,
                        'items': [{'title': '시장 뉴스', 'published': prior_at}]},
               'prior_analysis': {'status': 'reference_only', 'observed_at': prior_at, 'technical': {'vote': 1}}}
    return item, context


def existing(tmp_path):
    item, context = inputs()
    root = tmp_path / 'vibe-paper'
    shared = tmp_path / 'shared-etf-paper'
    root.mkdir(); shared.mkdir()
    with sqlite3.connect(root / 'orders.sqlite3') as db:
        db.execute('CREATE TABLE fills(order_id TEXT,symbol TEXT,side TEXT,quantity INTEGER,price REAL,time TEXT)')
        db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('fill-a', '100790', 'BUY', 10, 100, (NOW - timedelta(minutes=10)).isoformat()))
    digest, chart = 'a' * 64, 'b' * 64
    answer = {'type': 'choice', 'choice': 'SELL', 'confidence': 0.9}
    event = {'event': 'jev_holding_decision', 'symbol': '100790', 'model': jev.MODEL,
             'model_response_time': (NOW - timedelta(seconds=3)).isoformat(), 'input_digest': digest,
             'choice': 'SELL', 'confidence': 0.9}
    (root / 'events.jsonl').write_text(json.dumps(event) + '\n')
    state = {'position': {'symbol': '100790', 'quantity': 10, 'entry_average': 100, 'bid': 101},
             'chart_as_of': {'100790': '132800'}, 'market_regime_themes_global': context['market']['data'],
             'vibe_trading_view': {'as_of': context['prior_analysis']['observed_at']},
             'recent_news_headlines': context['news']['items'], 'vibe_strategy': jev._strategy(root, NOW),
             'vibe_position_source': {'engine': 'vibe', 'ledger': root.name}}
    with sqlite3.connect(shared / 'market-research.sqlite3') as db:
        db.execute('CREATE TABLE model_inputs(digest TEXT,engine TEXT,question TEXT,inputs TEXT,response TEXT)')
        db.execute('INSERT INTO model_inputs VALUES (?,?,?,?,?)', (digest, 'jev', 'holding',
                   json.dumps({'state': state, 'chart_sha256': chart}), json.dumps(answer)))
    return root, item, context


def test_existing_proven_same_position_reuses_jev_without_api(tmp_path):
    root, item, context = existing(tmp_path)
    before = (root / 'orders.sqlite3').read_bytes(), (root.parent / 'shared-etf-paper/market-research.sqlite3').read_bytes()
    with patch.object(jev, '_same_vibe_root', return_value=True), patch.object(jev, 'current_position', return_value=True), patch.object(jev, '_position_plan', return_value={'quantity': 10}), patch.object(jev, '_invoke') as call:
        result = jev.review(item, context, root, clock=lambda: NOW)
    assert result['source'] == 'jev_existing' and json.loads(result['text'])['stance'] == 'exit'
    assert result['confidence'] == 0.9 and result['sell_confidence'] == 0.7
    assert result['chart_sha256'] == 'b' * 64 and result['response_time']
    call.assert_not_called()
    assert before == ((root / 'orders.sqlite3').read_bytes(), (root.parent / 'shared-etf-paper/market-research.sqlite3').read_bytes())


def test_reuse_rejects_another_position_bid_and_stale_evidence(tmp_path):
    root, item, context = existing(tmp_path)
    changed = dict(item, position_id='b' * 20)
    strategy = jev._strategy(root, NOW)
    assert jev._recent_existing(root, changed, context, NOW, strategy, {'quantity': 10}) is None
    item['event_trigger']['inputs']['bid'] = 102
    assert jev._recent_existing(root, item, context, NOW, strategy, {'quantity': 10}) is None
    item['event_trigger']['inputs']['bid'] = 101
    assert jev._recent_existing(root, item, context, NOW + timedelta(seconds=61), strategy, {'quantity': 10}) is None


def test_context_or_position_unknown_never_calls_jev(tmp_path):
    item, context = inputs()
    context['news']['status'] = 'unavailable'
    with patch.object(jev, '_same_vibe_root', return_value=True), patch.object(jev, 'current_position', return_value=True), patch.object(jev, '_invoke') as call:
        result = jev.review(item, context, tmp_path / 'vibe-paper', clock=lambda: NOW)
    assert result['source'] == 'unavailable' and json.loads(result['text'])['stance'] == 'uncertain'
    call.assert_not_called()


def test_new_jev_sell_threshold_and_research_path_isolated(tmp_path):
    item, context = inputs()
    response = {'model': jev.MODEL, 'holding': {'type': 'choice', 'choice': 'SELL', 'confidence': 0.69},
                'response_time': NOW.isoformat(), 'chart_sha256': 'b' * 64, 'input_digest': 'c' * 64}
    root = tmp_path / 'vibe-paper'
    with patch.object(jev, '_same_vibe_root', return_value=True), patch.object(jev, 'current_position', return_value=True), patch.object(jev, '_position_plan', return_value={'quantity': 10}), patch.object(jev, '_recent_existing', return_value=None), \
         patch.object(jev, '_fresh_inputs', return_value={'position_extra': {'quantity': 10}}), patch('jev_activity.regular_request_busy', return_value=False), patch.object(jev, '_invoke', return_value=response) as invoke, \
         patch.object(jev, '_verify_stored_input', return_value=True):
        result = jev.review(item, context, root, clock=lambda: NOW)
    assert result['source'] == 'jev_event' and json.loads(result['text'])['stance'] == 'uncertain'
    assert result['choice'] == 'SELL' and result['confidence'] == 0.69 and result['sell_confidence'] == 0.7
    assert invoke.call_args.args[1] == tmp_path / 'vibe-event-analysis/jev-event-research'


def test_changed_position_or_expired_answer_is_never_accepted(tmp_path):
    item, context = inputs()
    response = {'model': jev.MODEL, 'holding': {'type': 'choice', 'choice': 'SELL', 'confidence': 0.9},
                'response_time': NOW.isoformat(), 'chart_sha256': 'b' * 64}
    with patch.object(jev, '_same_vibe_root', return_value=True), patch.object(jev, 'current_position', side_effect=[True, False]), patch.object(jev, '_position_plan', return_value={'quantity': 10}), patch.object(jev, '_recent_existing', return_value=None), \
         patch.object(jev, '_fresh_inputs', return_value={}), patch('jev_activity.regular_request_busy', return_value=False), patch.object(jev, '_invoke', return_value=response):
        result = jev.review(item, context, tmp_path / 'vibe-paper', clock=lambda: NOW)
    assert result['source'] == 'unavailable' and json.loads(result['text'])['stance'] == 'uncertain'


def test_regular_jev_inflight_is_not_competed_with(tmp_path):
    item, context = inputs()
    with patch.object(jev, '_same_vibe_root', return_value=True), patch.object(jev, 'current_position', return_value=True), \
         patch.object(jev, '_position_plan', return_value={'quantity': 10}), patch.object(jev, '_recent_existing', return_value=None), \
         patch.object(jev, '_fresh_inputs', return_value={}), patch('jev_activity.regular_request_busy', return_value=True), \
         patch.object(jev, '_invoke') as invoke:
        result = jev.review(item, context, tmp_path / 'vibe-paper', clock=lambda: NOW)
    assert result['source'] == 'unavailable' and result['api_calls'] == 0
    invoke.assert_not_called()


def test_missing_market_bars_never_queries_broker(tmp_path):
    item, context = inputs()
    with patch('vibe_fast_exit_watch._recent_bars', return_value={}):
        try:
            jev._fresh_inputs(tmp_path / 'vibe-paper', item, context, NOW, {}, {'quantity': 10})
        except ValueError as exc:
            assert str(exc) == 'jev_bars_unavailable'
        else:
            raise AssertionError('missing bars accepted')


def test_fresh_input_passes_validated_strategy_and_symbol_bars(tmp_path):
    from etf_universe import SYMBOLS
    item, context = inputs()
    root = tmp_path / 'vibe-paper'
    strategy = jev._strategy(root, NOW)
    bar = {'bsop_time': '132800', 'stck_prpr': '100'}
    recent = {code: ([bar] * 6, NOW) for code in set(SYMBOLS.values()) | {'100790'}}
    with patch('vibe_fast_exit_watch._recent_bars', return_value=recent), \
         patch('nh_paper_isolated.fresh_etf7_bars', return_value=True):
        payload = jev._fresh_inputs(root, item, context, NOW, strategy, {'quantity': 10})
    assert set(SYMBOLS) <= set(payload['bars']) and '100790' in payload['bars']
    assert payload['position_extra']['quantity'] == 10
    assert payload['extra_state']['vibe_strategy'] == strategy
    assert payload['extra_state']['strategy_observed_at'] == NOW.isoformat()


def test_changed_strategy_or_missing_input_digest_rejects_new_result(tmp_path):
    item, context = inputs()
    root = tmp_path / 'vibe-paper'
    strategy = jev._strategy(root, NOW)
    response = {'model': jev.MODEL, 'holding': {'type': 'choice', 'choice': 'SELL', 'confidence': 0.9},
                'response_time': NOW.isoformat(), 'chart_sha256': 'b' * 64, 'input_digest': 'c' * 64}
    changed = {**strategy, 'sha256': 'f' * 64}
    with patch.object(jev, '_same_vibe_root', return_value=True), patch.object(jev, 'current_position', return_value=True), patch.object(jev, '_position_plan', return_value={'quantity': 10}), patch.object(jev, '_recent_existing', return_value=None), \
         patch.object(jev, '_fresh_inputs', return_value={}), patch('jev_activity.regular_request_busy', return_value=False), patch.object(jev, '_invoke', return_value=response), \
         patch.object(jev, '_strategy', side_effect=[strategy, changed]):
        result = jev.review(item, context, root, clock=lambda: NOW)
    assert result['source'] == 'unavailable'
    response['input_digest'] = None
    with patch.object(jev, '_same_vibe_root', return_value=True), patch.object(jev, 'current_position', return_value=True), patch.object(jev, '_position_plan', return_value={'quantity': 10}), patch.object(jev, '_recent_existing', return_value=None), \
         patch.object(jev, '_fresh_inputs', return_value={}), patch('jev_activity.regular_request_busy', return_value=False), patch.object(jev, '_invoke', return_value=response):
        result = jev.review(item, context, root, clock=lambda: NOW)
    assert result['source'] == 'unavailable'


def test_previous_jev_without_exact_strategy_snapshot_cannot_be_reused(tmp_path):
    root, item, context = existing(tmp_path)
    database = root.parent / 'shared-etf-paper/market-research.sqlite3'
    with sqlite3.connect(database) as db:
        saved = json.loads(db.execute('SELECT inputs FROM model_inputs').fetchone()[0])
        saved['state'].pop('vibe_strategy')
        db.execute('UPDATE model_inputs SET inputs=?', (json.dumps(saved),))
    assert jev._recent_existing(root, item, context, NOW, jev._strategy(root, NOW), {'quantity': 10}) is None


def test_reuse_requires_every_current_news_item(tmp_path):
    root, item, context = existing(tmp_path)
    context['news']['items'].append({'title': '추가 뉴스', 'published': context['news']['observed_at']})
    assert jev._recent_existing(root, item, context, NOW, jev._strategy(root, NOW), {'quantity': 10}) is None


def test_position_plan_uses_actual_virtual_book_and_market_stop(tmp_path):
    root = tmp_path / 'vibe-paper'
    root.mkdir(); (tmp_path / 'shared-etf-paper').mkdir()
    with sqlite3.connect(root / 'orders.sqlite3') as db:
        db.execute('CREATE TABLE fills(symbol TEXT,side TEXT,quantity INTEGER,price REAL)')
        db.executemany('INSERT INTO fills VALUES (?,?,?,?)', [('100790', 'BUY', 10, 100), ('100790', 'SELL', 3, 101)])
    item, context = inputs()
    item['qty'] = 7
    market = {'코스피': {'regime': '하락장'}}
    context['market']['data'] = market
    strategy = jev._strategy(root, NOW)
    with patch('nh_paper_isolated.market_view', return_value=market), patch('nh_paper_isolated.stop_symbol_index', return_value='코스피'), \
         patch('nh_paper_isolated.verified_take_profit_sold', return_value=0):
        plan = jev._position_plan(root, item, context, strategy)
    assert plan['quantity'] == 7 and plan['stop_pct'] == -1.0
    assert plan['exit_profile'] == 'defensive' and plan['trailing_pct'] is None and plan['peak_bid'] is None
    assert plan['half_already_sold'] is False
    with patch('nh_paper_isolated.market_view', return_value=market), patch('nh_paper_isolated.stop_symbol_index', return_value='코스피'), \
         patch('nh_paper_isolated.verified_take_profit_sold', return_value=None):
        with pytest.raises(ValueError, match='take_profit_history_unavailable'):
            jev._position_plan(root, item, context, strategy)
    with patch('nh_paper_isolated.market_view', return_value=market), patch('nh_paper_isolated.stop_symbol_index', return_value=None):
        try:
            jev._position_plan(root, item, context, strategy)
        except ValueError as exc:
            assert str(exc) == 'position_regime_unavailable'
        else:
            raise AssertionError('unknown index accepted')


def test_same_root_requires_explicit_valid_account_role(tmp_path):
    root = tmp_path / 'vibe-paper'
    role = {'paper-shared': {'engine': 'jev'}}
    with patch('accounts.stored', return_value=role), patch('accounts.load', return_value=role), \
         patch('engine_roles.ledger_root', return_value=root):
        assert jev._same_vibe_root(root)
    with patch('accounts.stored', return_value={}), patch('accounts.load', return_value=role):
        assert not jev._same_vibe_root(root)


def test_jev_child_redirects_research_to_isolated_directory(tmp_path):
    (tmp_path / 'research_store.py').write_text("ROOT = 'operational'\n")
    (tmp_path / 'jev_activity.py').write_text('LOCK = None\nclass JevRequestBusy(ValueError): pass\n')
    (tmp_path / 'nh_etf_auto_v2.py').write_text(
        "def jev_holding7(**kwargs):\n"
        " import research_store\n"
        " from pathlib import Path\n"
        " Path(research_store.ROOT, 'isolated-marker').write_text('ok')\n"
        " return {'model': 'fake'}\n")
    isolated = tmp_path / 'isolated'
    isolated.mkdir()
    result = subprocess.run([sys.executable, '-c', jev._CHILD, str(isolated)], input='{}', text=True,
                            capture_output=True, cwd=tmp_path, env={**os.environ, 'PYTHONPATH': str(tmp_path)}, timeout=10)
    assert result.returncode == 0 and json.loads(result.stdout) == {'model': 'fake'}
    assert (isolated / 'isolated-marker').read_text() == 'ok'
    assert not (tmp_path / 'operational').exists()


def test_child_reports_only_pre_send_lock_failure_as_not_sent(tmp_path):
    (tmp_path / 'research_store.py').write_text("ROOT = 'operational'\n")
    (tmp_path / 'jev_activity.py').write_text('class JevRequestBusy(ValueError): pass\n')
    (tmp_path / 'nh_etf_auto_v2.py').write_text(
        'from jev_activity import JevRequestBusy\n'
        'def jev_holding7(**kwargs):\n raise JevRequestBusy("sensitive detail")\n')
    child = subprocess.run([sys.executable, '-c', jev._CHILD, str(tmp_path)], input='{}', text=True,
                           capture_output=True, cwd=tmp_path,
                           env={**os.environ, 'PYTHONPATH': str(tmp_path)}, timeout=10)
    assert child.returncode == 73 and child.stdout.strip() == jev._NOT_SENT_MARKER
    assert 'sensitive detail' not in child.stdout and 'sensitive detail' not in child.stderr
    with patch.object(jev.subprocess, 'run', return_value=child):
        try:
            jev._invoke({}, tmp_path)
        except jev.JevEventNotSent:
            pass
        else:
            raise AssertionError('confirmed pre-send lock failure not classified')
    # Exit status alone is not enough to prove a lock failure before transmission.
    with patch.object(jev.subprocess, 'run', return_value=SimpleNamespace(returncode=73, stdout='other')):
        try:
            jev._invoke({}, tmp_path)
        except jev.JevEventNotSent:
            raise AssertionError('ambiguous child failure counted as not sent')
        except ValueError:
            pass


def test_review_distinguishes_confirmed_lock_skip_and_unknown_failure(tmp_path):
    item, context = inputs()
    root = tmp_path / 'vibe-paper'
    for error, expected in ((jev.JevEventNotSent('lock'), 0),
                            (subprocess.TimeoutExpired('child', 35), None),
                            (ValueError('child failed'), None)):
        with patch.object(jev, '_same_vibe_root', return_value=True), \
             patch.object(jev, 'current_position', return_value=True), \
             patch.object(jev, '_position_plan', return_value={'quantity': 10}), \
             patch.object(jev, '_recent_existing', return_value=None), \
             patch.object(jev, '_fresh_inputs', return_value={}), \
             patch('jev_activity.regular_request_busy', return_value=False), \
             patch.object(jev, '_invoke', side_effect=error):
            result = jev.review(item, context, root, clock=lambda: NOW)
        assert result['source'] == 'unavailable' and result['api_calls'] == expected
        assert json.loads(result['text'])['stance'] == 'uncertain'


def test_new_jev_digest_must_resolve_to_exact_isolated_inputs_and_answer(tmp_path):
    digest, chart = 'a' * 64, 'b' * 64
    answer = {'type': 'choice', 'choice': 'SELL', 'confidence': 0.8}
    extra = {'vibe_strategy': {'source': 'default'}, 'strategy_observed_at': NOW.isoformat(),
             'vibe_reference': {'observed_at': NOW.isoformat()}, 'trigger_context': {'reason': 'rapid_loss'},
             'market_regime_themes_global': {'regime': '횡보장'}}
    payload = {'symbol': '100790', 'avg': 100, 'bid': 101, 'position_extra': {'quantity': 10}, 'extra_state': extra}
    state = {**extra, 'position': {'symbol': '100790', 'entry_average': 100, 'bid': 101, 'quantity': 10}}
    assert not jev._verify_stored_input(tmp_path, digest, chart, answer, payload)
    with sqlite3.connect(tmp_path / 'market-research.sqlite3') as db:
        db.execute('CREATE TABLE model_inputs(digest TEXT,engine TEXT,question TEXT,inputs TEXT,response TEXT)')
        db.execute('INSERT INTO model_inputs VALUES (?,?,?,?,?)', (digest, 'jev', 'holding',
                   json.dumps({'state': state, 'chart_sha256': chart}), json.dumps(answer)))
    assert jev._verify_stored_input(tmp_path, digest, chart, answer, payload)
    assert not jev._verify_stored_input(tmp_path, digest, 'c' * 64, answer, payload)
    assert not jev._verify_stored_input(tmp_path, digest, chart, {**answer, 'confidence': 0.9}, payload)
    assert not jev._verify_stored_input(tmp_path, 'd' * 64, chart, answer, payload)


def test_vibe_helper_jev_call_never_holds_jev_mains_lock(tmp_path):
    """User 2026-11-17: the vibe helper must not affect Jev main. Jev main's requests fail at once while its lock is
    held, so the event child must take a different lock; here Jev main holds its (temporary) lock the whole time."""
    import fcntl
    real = Path(__file__).with_name('jev_activity.py').read_text()
    main_lock = tmp_path / 'regular.lock'
    (tmp_path / 'jev_activity.py').write_text(real.replace(
        "LOCK = Path.home() / '.vibe-trading/nhplug/jev-regular-request.lock'", f"LOCK = Path({str(main_lock)!r})"))
    (tmp_path / 'research_store.py').write_text("ROOT = 'operational'\n")
    (tmp_path / 'nh_etf_auto_v2.py').write_text(
        'import jev_activity\n'
        'def jev_holding7(**kwargs):\n'
        ' with jev_activity.regular_request():\n'
        '  return {"lock": str(jev_activity.LOCK)}\n')
    with main_lock.open('a') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)  # Jev main is mid-request
        child = subprocess.run([sys.executable, '-c', jev._CHILD, str(tmp_path)], input='{}', text=True,
                               capture_output=True, cwd=tmp_path, env={**os.environ, 'PYTHONPATH': str(tmp_path)}, timeout=10)
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout)['lock'].endswith('jev-event-request.lock')
