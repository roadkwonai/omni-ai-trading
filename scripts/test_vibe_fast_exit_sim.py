import json
import sqlite3
import hashlib
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from vibe_fast_exit_sim import replay, simulate, summarize_event_sources


def observation(minute, second, *, candidate=False, qty=10, bid=101, size=None, generation='a' * 20):
    stamp = f'2026-11-16T12:{minute:02d}:{second:02d}+09:00'
    inputs = {'quote_at': stamp, 'evaluated_at': stamp, 'bid': bid, 'entry_price': 100}
    if size is not None:
        inputs['bid_size'] = size
        inputs['bid_size_verified'] = True
    return {'mode': 'observation_only', 'state': 'candidate' if candidate else 'wait', 'candidate': candidate,
            'observed_at': stamp, 'symbol': '100790', 'position_id': generation, 'qty': qty, 'inputs': inputs}


def test_no_depth_or_later_observation_means_no_assumed_fill():
    rows = [observation(30, 0, candidate=True, size=100), observation(31, 0)]
    result = simulate(rows, [])[0]
    assert result['state'] == 'pending_unknown'
    assert result['remaining'] == 10 and not result['fills']
    assert result['gross_pnl_assumed'] is None and result['costs'] == 'unknown'


def test_new_quote_depth_limits_partial_fills_and_duplicate_candidates_do_not_reorder():
    rows = [observation(30, 0, candidate=True), observation(30, 30, size=3),
            observation(30, 30, size=50), observation(31, 0, candidate=True, size=8)]
    result = simulate(rows, [])[0]
    assert result['state'] == 'filled_assumed'
    assert [fill['quantity'] for fill in result['fills']] == [3, 7]
    assert all(fill['quantity'] <= fill['displayed_bid_size'] for fill in result['fills'])
    assert result['gross_pnl_assumed'] == 10


def test_unfavorable_bid_position_changes_and_other_generations_do_not_fill():
    rows = [observation(30, 0, candidate=True), observation(30, 10, bid=100, size=10),
            observation(30, 20, generation='b' * 20, size=10), observation(30, 30, qty=5, size=10),
            observation(31, 0, size=10)]
    result = simulate(rows)[0]
    assert result['state'] == 'position_changed_unknown' and not result['fills']


def test_replay_reads_engine_ledger_and_observations_without_mutating_them(tmp_path):
    source, ledger = tmp_path / 'obs.jsonl', tmp_path / 'engine.sqlite3'
    generation = hashlib.sha256('100790\0entry'.encode()).hexdigest()[:20]
    row = observation(30, 0, candidate=True, generation=generation)
    source.write_text(json.dumps(row) + '\n')
    with sqlite3.connect(ledger) as db:
        db.execute('CREATE TABLE fills (order_id TEXT,symbol TEXT,side TEXT,quantity INTEGER,price INTEGER,time TEXT)')
        db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('entry', '100790', 'BUY', 10, 100,
                                                             '2026-11-16T12:20:00+09:00'))
    before = ledger.read_bytes(), source.read_bytes()
    result = replay(source, ledger)
    assert result['mode'] == 'offline_simulation_not_broker_fill'
    assert result['simulation'][0]['state'] == 'pending_unknown'
    assert (ledger.read_bytes(), source.read_bytes()) == before


def test_replay_rejects_unknown_position_generation(tmp_path):
    source, ledger = tmp_path / 'obs.jsonl', tmp_path / 'engine.sqlite3'
    source.write_text(json.dumps(observation(30, 0, candidate=True)) + '\n')
    with sqlite3.connect(ledger) as db:
        db.execute('CREATE TABLE fills (order_id TEXT,symbol TEXT,side TEXT,quantity INTEGER,price INTEGER,time TEXT)')
    result = replay(source, ledger)
    assert result['simulation'] == [] and result['rejected_unverified_candidates'] == 1


def test_intervening_engine_fill_makes_hypothetical_position_uncertain():
    rows = [observation(30, 0, candidate=True), observation(31, 0, size=10)]
    fills = [('100790', datetime.fromisoformat('2026-11-16T12:30:30+09:00'))]
    result = simulate(rows, fills)[0]
    assert result['state'] == 'position_changed_unknown'
    assert not result['fills']


def test_unverified_depth_or_preorder_quote_never_fills():
    trigger = observation(30, 0, candidate=True)
    trigger['observed_at'] = trigger['inputs']['evaluated_at'] = '2026-11-16T12:30:15+09:00'
    earlier_quote = observation(30, 10, size=10)
    earlier_quote['observed_at'] = earlier_quote['inputs']['evaluated_at'] = '2026-11-16T12:30:20+09:00'
    unverified = observation(30, 30, size=10)
    unverified['inputs']['bid_size_verified'] = False
    assert simulate([trigger, earlier_quote, unverified], [])[0]['state'] == 'pending_unknown'


def test_observer_evaluator_and_sandbox_use_later_verified_depth(tmp_path):
    import vibe_fast_exit_watch as watch
    from test_vibe_fast_exit import inputs

    sample = inputs()
    generation = hashlib.sha256('100790\0entry'.encode()).hexdigest()[:20]
    position = {'symbol': '100790', 'qty': 10, 'entry_at': sample['entry_at'],
                'entry_price': 100, 'position_id': generation, 'latest_fill_id': 'entry'}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return sample['now']

    root, shared = tmp_path / 'vibe-paper', tmp_path / 'shared-etf-paper'
    (root / 'fast-exit-quotes').mkdir(parents=True)
    shared.mkdir()
    observations, ledger = tmp_path / 'observations.jsonl', root / 'orders.sqlite3'
    with sqlite3.connect(ledger) as db:
        db.execute('CREATE TABLE fills (order_id TEXT,symbol TEXT,side TEXT,quantity INTEGER,price INTEGER,time TEXT)')
        db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('entry', '100790', 'BUY', 10, 100,
                                                             sample['entry_at'].isoformat()))
    read = Mock()

    def persist_inputs():
        quote = {**sample['quote'], 'bidp1': 99, 'bidp_rsqn1': 10}
        (root / 'fast-exit-quotes' / '100790.json').write_text(json.dumps({
            'symbol': '100790', 'observed_at': sample['now'].isoformat(), 'quote': quote}))
        (shared / 'events.jsonl').write_text(json.dumps({
            'event': 'market_context', 'time': sample['now'].isoformat(),
            'market_source': 'NH KRX 1-minute completed bars',
            'market_bars': {'100790': sample['bars']}}) + '\n')

    with patch.object(watch, 'datetime', Clock), patch.object(watch, 'positions', return_value=[position]), \
         patch.object(watch, '_position_current', return_value=True), \
         patch.object(watch, '_model_position_matches', return_value=True), \
         patch.object(watch, '_existing_exit', return_value=False), \
         patch.object(watch, '_context_status', side_effect=lambda *_: sample['context_status']), \
         patch('vibe_runner.load_advice', side_effect=lambda *_: sample['advice']):
        persist_inputs()
        first = watch.inspect_once(read, root, root, sample['now'])
        watch.append_results(observations, first, sample['now'])
        sample['now'] += timedelta(minutes=1)
        sample['bars'] = [dict(row, bsop_time=f"12{int(row['bsop_time'][2:4]) + 1:02d}00") for row in sample['bars']]
        sample['quote']['hoga_bsop_hour'] = '12:41:25'
        sample['advice']['signals']['100790'] = 0.1
        persist_inputs()
        second = watch.inspect_once(read, root, root, sample['now'])
        watch.append_results(observations, second, sample['now'])
    assert first[0]['state'] == 'candidate'
    read.call_tool.assert_not_called()
    result = replay(observations, ledger)['simulation'][0]
    assert result['state'] == 'filled_assumed' and result['fills'][0]['quantity'] == 10
    assert result['fills'][0]['quote_at'].endswith('12:41:25+09:00')


def event_fixture(tmp_path, *, extra_fills=(), stance='exit', completed='2026-11-16T12:30:10+09:00'):
    source, ledger, events = tmp_path / 'obs.jsonl', tmp_path / 'engine.sqlite3', tmp_path / 'events.sqlite3'
    generation = hashlib.sha256('100790\0entry'.encode()).hexdigest()[:20]
    rows = [observation(30, 0, generation=generation), observation(30, 20, generation=generation, size=10),
            observation(30, 40, generation=generation, size=10)]
    source.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    with sqlite3.connect(ledger) as db:
        db.execute('CREATE TABLE fills (order_id TEXT,symbol TEXT,side TEXT,quantity INTEGER,price INTEGER,time TEXT)')
        db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('entry', '100790', 'BUY', 10, 100,
                                                             '2026-11-16T12:20:00+09:00'))
        db.executemany('INSERT INTO fills VALUES (?,?,?,?,?,?)', extra_fills)
    record = {'mode': 'analysis_only', 'event': {'id': 'e1', 'symbol': '100790', 'qty': 10,
              'position_id': generation, 'latest_fill_id': 'entry', 'observed_at': '2026-11-16T12:30:00+09:00'},
              'completed_at': completed, 'analysis': {'stance': stance, 'reason': 'test', 'status': 'ok'}}
    with sqlite3.connect(events) as db:
        db.execute('CREATE TABLE attempts (id TEXT, status TEXT, result TEXT)')
        db.execute('INSERT INTO attempts VALUES (?,?,?)', ('e1', 'completed', json.dumps(record)))
    return source, ledger, events


def test_ai_exit_uses_only_new_quote_after_result_and_another_for_assumed_fill(tmp_path):
    source, ledger, events = event_fixture(tmp_path)
    before = source.read_bytes(), ledger.read_bytes(), events.read_bytes()
    result = replay(source, ledger, events)
    order = result['event_simulation'][0]
    assert order['order_at'].endswith('12:30:20+09:00')
    assert order['fills'][0]['quote_at'].endswith('12:30:40+09:00')
    assert order['state'] == 'filled_assumed' and order['costs'] == 'unknown'
    assert (source.read_bytes(), ledger.read_bytes(), events.read_bytes()) == before


def test_jev_exit_requires_recorded_matching_confidence_and_threshold(tmp_path):
    source, ledger, events = event_fixture(tmp_path)
    with sqlite3.connect(events) as db:
        record = json.loads(db.execute('SELECT result FROM attempts').fetchone()[0])
        record['source'] = 'jev_existing'
        record['analysis'].update(confidence=0.8, sell_confidence=0.7)
        record['model_evidence'] = {'choice': 'SELL', 'confidence': 0.8, 'sell_confidence': 0.7}
        db.execute('UPDATE attempts SET result=?', (json.dumps(record),))
    result = replay(source, ledger, events)
    assert result['event_simulation'][0]['decision_source'] == 'jev_existing'
    assert result['event_simulation'][0]['model_confidence'] == 0.8
    assert result['event_simulation'][0]['api_cost'] == 'unknown'
    assert result['event_sources']['jev_existing']['completed'] == 1
    with sqlite3.connect(events) as db:
        record['model_evidence'].pop('confidence')
        db.execute('UPDATE attempts SET result=?', (json.dumps(record),))
    rejected = replay(source, ledger, events)
    assert rejected['event_simulation'] == [] and rejected['rejected_event_results'] == 1


def test_jev_under_threshold_or_mismatched_evidence_never_simulates(tmp_path):
    source, ledger, events = event_fixture(tmp_path)
    with sqlite3.connect(events) as db:
        record = json.loads(db.execute('SELECT result FROM attempts').fetchone()[0])
        record['source'] = 'jev_event'
        record['analysis'].update(confidence=0.6, sell_confidence=0.7)
        record['model_evidence'] = {'choice': 'SELL', 'confidence': 0.6, 'sell_confidence': 0.7}
        db.execute('UPDATE attempts SET result=?', (json.dumps(record),))
    result = replay(source, ledger, events)
    assert not result['event_simulation'] and result['rejected_event_results'] == 1
    assert result['event_sources']['jev_event']['completed'] == 1


def test_gpt_and_unknown_historical_sources_are_not_mixed(tmp_path):
    source, ledger, events = event_fixture(tmp_path)
    with sqlite3.connect(events) as db:
        record = json.loads(db.execute('SELECT result FROM attempts').fetchone()[0])
        record['usage'] = {'provider': 'openai-codex', 'model': 'gpt-6.1-terra'}
        db.execute('UPDATE attempts SET result=?', (json.dumps(record),))
        other = {**record, 'usage': {'status': 'unavailable'}}
        db.execute('INSERT INTO attempts VALUES (?,?,?)', ('other', 'completed', json.dumps(other)))
    result = replay(source, ledger, events)
    assert result['event_simulation'][0]['decision_source'] == 'gpt'
    assert result['event_sources']['gpt']['completed'] == 1
    assert result['event_sources']['unknown']['completed'] == 1
    assert all(item['api_cost'] == 'unknown' for item in result['event_sources'].values())


def test_recorded_jev_calls_and_unknown_attempts_are_counted_separately():
    def item(ident, source, calls, status='completed'):
        return ident, status, json.dumps({'source': source, 'api_calls': calls})
    counts = summarize_event_sources([
        item('a', 'jev_event', 1), item('b', 'jev_existing', 0),
        item('c', 'unavailable', 1, 'model_failed'),
        ('d', 'model_timeout', None), item('e', 'jev_existing', 1),
        item('f', 'jev_event', True), item('g', 'gpt', 1),
    ])
    assert counts['jev_event']['api_calls_known'] == 1
    assert counts['jev_event']['api_calls_unknown_attempts'] == 1
    assert counts['jev_existing']['api_calls_known'] == 0
    assert counts['jev_existing']['api_calls_unknown_attempts'] == 1
    assert counts['unknown']['api_calls_known'] == 1
    assert counts['unknown']['api_calls_unknown_attempts'] == 1
    assert counts['gpt']['api_calls_unknown_attempts'] == 1
    assert sum(v['api_calls_known'] for v in counts.values()) == 2
    assert sum(v['api_calls_unknown_attempts'] for v in counts.values()) == 4


def test_ai_result_rejects_old_position_even_when_quantity_matches(tmp_path):
    source, ledger, events = event_fixture(tmp_path, extra_fills=(
        ('old-sell', '100790', 'SELL', 10, 99, '2026-11-16T12:30:03+09:00'),
        ('new-buy', '100790', 'BUY', 10, 100, '2026-11-16T12:30:04+09:00')))
    result = replay(source, ledger, events)
    assert result['event_simulation'] == [] and result['rejected_event_results'] == 1


def test_ai_exit_without_later_quote_or_depth_remains_unfilled(tmp_path):
    source, ledger, events = event_fixture(tmp_path)
    rows = [json.loads(line) for line in source.read_text().splitlines()]
    source.write_text(json.dumps(rows[0]) + '\n')
    result = replay(source, ledger, events)
    assert result['event_simulation'][0]['state'] == 'quote_unavailable'
    assert not replay(source, ledger, events)['simulation']


def test_ai_exit_rejects_stale_post_analysis_quotes(tmp_path):
    source, ledger, events = event_fixture(tmp_path)
    rows = [json.loads(line) for line in source.read_text().splitlines()]
    rows[1]['observed_at'] = rows[1]['inputs']['evaluated_at'] = '2026-11-16T12:30:36+09:00'
    rows[2]['observed_at'] = rows[2]['inputs']['evaluated_at'] = '2026-11-16T12:30:56+09:00'
    source.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    assert replay(source, ledger, events)['event_simulation'][0]['state'] == 'quote_unavailable'


def test_ai_hold_or_stale_result_never_starts_simulated_order(tmp_path):
    source, ledger, events = event_fixture(tmp_path, stance='hold')
    assert replay(source, ledger, events)['event_simulation'] == []
    with sqlite3.connect(events) as db:
        value = json.loads(db.execute('SELECT result FROM attempts').fetchone()[0])
        value['analysis']['stance'] = 'exit'
        value['completed_at'] = '2026-11-16T12:31:01+09:00'
        db.execute('UPDATE attempts SET result=?', (json.dumps(value),))
    result = replay(source, ledger, events)
    assert result['event_simulation'] == [] and result['rejected_event_results'] == 1


def test_ai_exit_with_unconfirmed_context_is_never_simulated(tmp_path):
    source, ledger, events = event_fixture(tmp_path)
    with sqlite3.connect(events) as db:
        value = json.loads(db.execute('SELECT result FROM attempts').fetchone()[0])
        value['analysis']['status'] = 'insufficient_context'
        db.execute('UPDATE attempts SET result=?', (json.dumps(value),))
    assert replay(source, ledger, events)['event_simulation'] == []


def test_unreadable_experimental_results_do_not_hide_original_simulation(tmp_path):
    source, ledger, events = event_fixture(tmp_path)
    with sqlite3.connect(events) as db:
        db.execute('DROP TABLE attempts')
    result = replay(source, ledger, events)
    assert result['mode'] == 'offline_simulation_not_broker_fill'
    assert result['event_results_status'] == 'unavailable' and result['event_simulation'] == []


def test_price_event_to_ai_to_isolated_simulation_without_operational_writes(tmp_path):
    import accounts
    import vibe_event_advisor as advisor
    import vibe_fast_exit_watch as watch
    from test_vibe_fast_exit import inputs

    sample = inputs()
    base = sample['now']
    root, shared = tmp_path / 'nhplug/vibe-paper', tmp_path / 'nhplug/shared-etf-paper'
    root.mkdir(parents=True)
    shared.mkdir()
    account_config = root.parent / 'accounts.json'
    account_config.write_text(json.dumps(accounts.DEFAULTS))
    quotes = root / 'fast-exit-quotes'
    quotes.mkdir()
    ledger, advice = root / 'orders.sqlite3', root / 'vibe-advice.json'
    state = root / 'account-state.json'
    state.write_text(json.dumps({'time': base.isoformat(), 'holdings': {
        '100790': {'rsdl_qty': 10, 'phs_pr': 100}}}))
    digest = 'a' * 64
    advice.write_text(json.dumps({'time': (base - timedelta(seconds=5)).isoformat(),
                                  'input_digest': digest, 'decisions': [], 'sentinel': 'do not replace'}))
    research = shared / 'market-research.sqlite3'
    with sqlite3.connect(research) as db:
        db.execute('CREATE TABLE model_inputs(digest TEXT,engine TEXT,question TEXT,inputs TEXT)')
        context = {'time': (base - timedelta(seconds=10)).isoformat(),
                   'market_regime_themes_global': {'as_of': (base - timedelta(seconds=20)).isoformat()},
                   'market_news': [{'title': 'test', 'published': (base - timedelta(seconds=15)).isoformat()}]}
        db.execute('INSERT INTO model_inputs VALUES (?,?,?,?)', (digest, 'vibe', 'scores', json.dumps({'context': context})))
    with sqlite3.connect(ledger) as db:
        db.executescript('CREATE TABLE fills(order_id TEXT,symbol TEXT,side TEXT,quantity INTEGER,price REAL,time TEXT);'
                         'CREATE TABLE decision_order_links(order_id TEXT,cycle_id TEXT);'
                         'CREATE TABLE intents(status TEXT);')
        db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('entry', '100790', 'BUY', 10, 100,
                                                             sample['entry_at'].isoformat()))
        db.execute('INSERT INTO decision_order_links VALUES (?,?)', ('entry', 'cycle'))
    engine_events = root / 'events.jsonl'
    engine_events.write_text(json.dumps({'event': 'submitted', 'symbol': '100790',
                                         'cycle_id': 'cycle', 'source': 'vibe-trading'}) + '\n')
    (shared / 'events.jsonl').write_text(json.dumps({
        'event': 'market_context', 'time': sample['bars_observed_at'].isoformat(),
        'market_source': 'NH KRX 1-minute completed bars', 'market_bars': {'100790': sample['bars']}}) + '\n')
    original = (ledger.read_bytes(), advice.read_bytes(), state.read_bytes(), engine_events.read_bytes(),
                research.read_bytes(), account_config.read_bytes())
    clock = [base]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return clock[0]

    def snapshot(when):
        quote_time = when - timedelta(seconds=1)
        quote = {**sample['quote'], 'hoga_bsop_hour': quote_time.strftime('%H:%M:%S'),
                 'bidp1': 99, 'bidp_rsqn1': 10}
        (quotes / '100790.json').write_text(json.dumps({
            'symbol': '100790', 'observed_at': when.isoformat(), 'quote': quote}))

    observations = tmp_path / 'observer/observations.jsonl'
    results = tmp_path / 'event-analysis'
    with patch.object(watch, 'datetime', Clock):
        snapshot(base)
        first = watch.inspect_once(Mock(), root, root, base)
        assert first[0]['event_trigger']['triggered'] is True
        watch.append_results(observations, first, base)
        event_row = json.loads(observations.read_text().splitlines()[0])
        done = base + timedelta(seconds=2)
        invoked = []
        status = advisor.process(event_row, root, results, clock=lambda: done,
                                 model=lambda text: invoked.append(text) or '{"stance":"exit","reason":"test"}',
                                 lock_path=tmp_path / 'model.lock')
        assert status == 'completed' and len(invoked) == 1
        for seconds in (10, 20):
            clock[0] = base + timedelta(seconds=seconds)
            snapshot(clock[0])
            watch.append_results(observations, watch.inspect_once(Mock(), root, root, clock[0]), clock[0])

    replayed = replay(observations, ledger, results / 'events.sqlite3')
    assert replayed['event_results_status'] == 'ok' and len(replayed['event_simulation']) == 1
    order = replayed['event_simulation'][0]
    assert order['state'] == 'filled_assumed'
    assert order['order_at'] == (base + timedelta(seconds=10)).isoformat()
    assert order['fills'][0]['quote_at'] == (base + timedelta(seconds=19)).isoformat()
    assert (ledger.read_bytes(), advice.read_bytes(), state.read_bytes(), engine_events.read_bytes(),
            research.read_bytes(), account_config.read_bytes()) == original
