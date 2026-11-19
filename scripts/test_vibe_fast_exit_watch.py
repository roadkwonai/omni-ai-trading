import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import vibe_fast_exit_watch as watch

NOW = datetime.fromisoformat('2026-11-16T10:10:00+09:00')

class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


def setup_position(root, *, source='vibe-trading', state_time=NOW, pending=False):
    root = Path(root)
    (root / 'account-state.json').write_text(json.dumps({'time': state_time.isoformat(), 'holdings': {
        '100790': {'rsdl_qty': 10, 'phs_pr': 1000}}}))
    with sqlite3.connect(root / 'orders.sqlite3') as db:
        db.executescript('CREATE TABLE fills(order_id TEXT,symbol TEXT,side TEXT,quantity INTEGER,price REAL,time TEXT);'
                         'CREATE TABLE decision_order_links(order_id TEXT,cycle_id TEXT);'
                         'CREATE TABLE intents(status TEXT);')
        db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('id', '100790', 'BUY', 10, 1000, (NOW-timedelta(minutes=8)).isoformat()))
        db.execute('INSERT INTO decision_order_links VALUES (?,?)', ('id', 'cycle'))
        if pending:
            db.execute("INSERT INTO intents VALUES ('acknowledged')")
    (root / 'events.jsonl').write_text(json.dumps({'event': 'submitted', 'symbol': '100790', 'cycle_id': 'cycle', 'source': source}) + '\n')
    return root


def test_verified_normal_entry_without_oversold_plan(tmp_path):
    root = setup_position(tmp_path)
    before = (root / 'orders.sqlite3').read_bytes()
    assert watch.positions(root, NOW)[0]['symbol'] == '100790'
    assert (root / 'orders.sqlite3').read_bytes() == before


def test_oversold_or_unproven_source_never_qualifies(tmp_path):
    root = setup_position(tmp_path, source='oversold_v2')
    assert watch.positions(root, NOW) == []
    (root / 'events.jsonl').write_text('')
    assert watch.positions(root, NOW) == []
    (root / 'oversold-plans.json').write_text('{"100790": {}}')
    assert watch.positions(root, NOW) == []


def test_stale_pending_and_mismatched_holdings_are_unavailable(tmp_path):
    stale = tmp_path / 'stale'; stale.mkdir()
    setup_position(stale, state_time=NOW-timedelta(minutes=4))
    assert watch.inspect_once(Mock(), stale, stale, NOW)[0]['state'] == 'unavailable'
    pending = tmp_path / 'pending'; pending.mkdir()
    setup_position(pending, pending=True)
    assert watch.inspect_once(Mock(), pending, pending, NOW)[0]['state'] == 'unavailable'
    mismatch = tmp_path / 'mismatch'; mismatch.mkdir()
    setup_position(mismatch)
    (mismatch / 'account-state.json').write_text(json.dumps({'time': NOW.isoformat(), 'holdings': {}}))
    assert watch.inspect_once(Mock(), mismatch, mismatch, NOW)[0]['state'] == 'unavailable'


def test_observer_writes_only_separate_observation(tmp_path):
    path = tmp_path / 'observer' / 'observations.jsonl'
    watch.append_results(path, [{'symbol': '100790', 'state': 'candidate'}], NOW)
    row = json.loads(path.read_text())
    assert row['mode'] == 'observation_only' and row['state'] == 'candidate'
    assert not (tmp_path / 'orders.sqlite3').exists()
    assert path.stat().st_mode & 0o077 == 0


def test_recorded_bars_reject_missing_stale_and_wrong_source():
    event = {'time': NOW.isoformat(), 'market_source': 'NH KRX 1-minute completed bars',
             'market_bars': {'005930': [{'bsop_time': '100800'}]}}
    assert watch._recorded_bars(event, '100790', NOW) == (None, None)
    assert watch._recorded_bars({**event, 'time': (NOW - timedelta(seconds=31)).isoformat()}, '005930', NOW) == (None, None)
    assert watch._recorded_bars({**event, 'market_source': 'unknown'}, '005930', NOW) == (None, None)
    wrong_day = {**event, 'market_bars': {'005930': [{'bsop_date': '20261113', 'bsop_time': '100800'}]}}
    assert watch._recorded_bars(wrong_day, '005930', NOW) == (None, None)
    rows, at = watch._recorded_bars(event, '005930', NOW)
    assert rows[0]['bsop_date'] == '20261116' and at == NOW


def test_snapshot_quote_rejects_stale_or_wrong_symbol(tmp_path):
    directory = tmp_path / 'fast-exit-quotes'; directory.mkdir()
    path = directory / '100790.json'
    quote = {'iem_cd':'100790', 'market_status':'1', 'hoga_bsop_hour':'10:10:00', 'bidp':1000}
    data = {'symbol': '100790', 'observed_at': NOW.isoformat(), 'quote': quote}
    path.write_text(json.dumps(data))
    assert watch._snapshot_quote(tmp_path, '100790', NOW)['bidp'] == 1000
    data['observed_at'] = (NOW - timedelta(seconds=20)).isoformat()
    path.write_text(json.dumps(data))
    try:
        watch._snapshot_quote(tmp_path, '100790', NOW)
        assert False, 'stale quote accepted'
    except ValueError:
        pass
    data['observed_at'] = NOW.isoformat()
    data['quote']['iem_cd'] = '005930'
    path.write_text(json.dumps(data))
    try:
        watch._snapshot_quote(tmp_path, '100790', NOW)
        assert False, 'wrong symbol accepted'
    except ValueError:
        pass


def test_existing_protective_and_flatten_exits_take_priority(tmp_path):
    import session_times
    import strategy_settings
    import nh_paper_isolated
    root = setup_position(tmp_path)
    settings = {'stop_loss_pct': 2, 'take_profit_half_pct': 1, 'take_profit_all_pct': 2}
    p = watch.positions(root, NOW)[0]
    with patch.object(strategy_settings, 'read_settings', return_value=(settings, 'custom')), \
         patch.object(session_times, 'load', return_value={'flatten': '14:40'}), \
         patch.object(nh_paper_isolated, 'market_view', return_value={'코스피': {'regime': '하락장'}}), \
         patch.object(nh_paper_isolated, 'stop_symbol_index', return_value='코스피'):
        advice = {'decisions': []}
        assert watch._existing_exit(root, p, {'bidp': 989}, NOW, advice) is True
        assert watch._existing_exit(root, p, {'bidp': 1011}, NOW, advice) is True
        assert watch._existing_exit(root, p, {'bidp': 1000}, NOW, advice) is False
        assert watch._existing_exit(root, p, {'bidp': 1000}, NOW.replace(hour=14, minute=40), advice) is True
        assert watch._existing_exit(root, p, {}, NOW, advice) is None
        assert watch._existing_exit(root, p, {'bidp': 1000}, NOW, {'decisions': [
            {'action':'SELL', 'symbol':'100790'}]}) is True


def test_comparison_joins_partial_sells_only_in_same_position_generation(tmp_path):
    root = setup_position(tmp_path)
    position = watch.positions(root, NOW)[0]
    observations = tmp_path / 'obs.jsonl'
    watch.append_results(observations, [{'symbol': '100790', 'qty': 10, 'position_id': position['position_id'],
                                        'state': 'candidate', 'candidate': True,
                                        'inputs': {'evaluated_at': NOW.isoformat(), 'quote_at': NOW.isoformat(), 'bid': 1040}}], NOW)
    with sqlite3.connect(root / 'orders.sqlite3') as db:
        db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('s1', '100790', 'SELL', 4, 1020, (NOW+timedelta(minutes=1)).isoformat()))
        db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('s2', '100790', 'SELL', 6, 1010, (NOW+timedelta(minutes=2)).isoformat()))
        db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('b2', '100790', 'BUY', 5, 1100, (NOW+timedelta(minutes=3)).isoformat()))
        db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('s3', '100790', 'SELL', 5, 1120, (NOW+timedelta(minutes=4)).isoformat()))
    row = watch.compare_observations(observations, root / 'orders.sqlite3')[0]
    assert row['state'] == 'engine_sells_recorded'
    assert [(fill['order_id'], fill['qty'], fill['price_minus_observed_bid']) for fill in row['engine_sell_fills']] == [
        ('s1', 4, -20), ('s2', 6, -30)]
    assert '체결가가 아니며' in row['limitation']


def test_comparison_invalid_or_missing_data_is_unavailable(tmp_path):
    root = setup_position(tmp_path)
    observations = tmp_path / 'obs.jsonl'
    watch.append_results(observations, [{'symbol': '100790', 'qty': 10, 'position_id': 'unknown',
                                        'candidate': True, 'inputs': {'evaluated_at': NOW.isoformat(),
                                        'quote_at': (NOW-timedelta(minutes=1)).isoformat(), 'bid': 1000}}], NOW)
    assert watch.compare_observations(observations, root / 'orders.sqlite3')[0]['state'] == 'unavailable'
    assert watch.compare_observations(tmp_path / 'missing', root / 'orders.sqlite3')[0]['state'] == 'unavailable'


def test_large_provenance_is_streamed_without_size_cutoff(tmp_path):
    root = setup_position(tmp_path)
    path = root / 'events.jsonl'
    first = path.read_text()
    irrelevant = json.dumps({'event': 'other', 'padding': 'a' * 50000}) + '\n'
    with path.open('w') as stream:
        for _ in range(180):
            stream.write(irrelevant)
        stream.write(first)
    assert path.stat().st_size > 8_000_000
    assert watch.positions(root, NOW)[0]['symbol'] == '100790'


def test_context_uses_exact_saved_model_input_and_rejects_stale_news(tmp_path):
    vibe = tmp_path / 'vibe-paper'; vibe.mkdir()
    shared = tmp_path / 'shared-etf-paper'; shared.mkdir()
    digest = 'a' * 64
    context = {'time': NOW.isoformat(), 'market_regime_themes_global': {'as_of': '2026-11-16T10:09', '코스피': {'regime': '하락장'}},
               'market_news': [{'published': (NOW - timedelta(hours=1)).isoformat()}]}
    with sqlite3.connect(shared / 'market-research.sqlite3') as db:
        db.execute('CREATE TABLE model_inputs (digest TEXT, engine TEXT, question TEXT, inputs TEXT)')
        db.execute('INSERT INTO model_inputs VALUES (?,?,?,?)', (digest, 'vibe', 'scores', json.dumps({'context': context})))
    with patch('nh_paper_isolated.stop_symbol_index', return_value='코스피'):
        result = watch._context_status(vibe, {'input_digest': digest}, NOW, '100790')
    assert result['market']['status'] == 'ok' and result['news']['status'] == 'ok'
    context['market_news'] = [{'published': (NOW - timedelta(days=2)).isoformat()}]
    with sqlite3.connect(shared / 'market-research.sqlite3') as db:
        db.execute('UPDATE model_inputs SET inputs=?', (json.dumps({'context': context}),))
    assert watch._context_status(vibe, {'input_digest': digest}, NOW, '100790')['news']['status'] == 'unavailable'
    assert watch._context_status(vibe, {'input_digest': 'b' * 64}, NOW, '100790')['market']['status'] == 'unavailable'


def test_position_recheck_rejects_same_quantity_reentry(tmp_path):
    root = setup_position(tmp_path)
    position = watch.positions(root, NOW)[0]
    assert watch._position_current(root, position, NOW)
    with sqlite3.connect(root / 'orders.sqlite3') as db:
        db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('sell', '100790', 'SELL', 10, 1000, NOW.isoformat()))
        db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('rebuy', '100790', 'BUY', 10, 1000, NOW.isoformat()))
    assert not watch._position_current(root, position, NOW)


def test_observer_and_evaluator_integration_produces_only_an_observation(tmp_path):
    from test_vibe_fast_exit import inputs
    sample = inputs()
    sample['quote'].update(bidp1=99, bidp_rsqn1=25)
    class Noon(datetime):
        @classmethod
        def now(cls, tz=None): return sample['now']
    position = {'symbol': sample['symbol'], 'qty': 10, 'entry_at': sample['entry_at'],
                'entry_price': sample['entry_price'], 'position_id': 'generation', 'latest_fill_id': 'fill'}
    event = {'time': sample['now'].isoformat(), 'market_source': 'NH KRX 1-minute completed bars',
             'market_bars': {sample['symbol']: sample['bars']}}
    read = Mock()
    with patch.object(watch, 'datetime', Noon), patch.object(watch, 'positions', return_value=[position]), \
         patch.object(watch, '_position_current', return_value=True), \
         patch.object(watch, '_model_position_matches', return_value=True), \
         patch.object(watch, '_existing_exit', return_value=False), \
         patch.object(watch, '_context_status', return_value=sample['context_status']), \
         patch.object(watch, '_snapshot_quote', return_value=sample['quote']), \
         patch('nh_paper_isolated.RecordedBars.latest', return_value=event), \
         patch('vibe_runner.load_advice', return_value=sample['advice']):
        result = watch.inspect_once(read, tmp_path, tmp_path, sample['now'])
    assert result[0]['state'] == 'candidate' and result[0]['mode'] == 'observation_only'
    assert result[0]['inputs']['bid'] == 99 and result[0]['inputs']['bid_size'] == 25
    assert result[0]['inputs']['bid_size_verified'] is True and result[0]['actual_exit_time'] is None
    read.call_tool.assert_not_called()
    assert not (tmp_path / 'orders.sqlite3').exists()


def test_event_trigger_is_independent_of_missing_advice(tmp_path):
    from test_vibe_fast_exit import inputs
    sample = inputs()
    sample['quote']['_snapshot_observed_at'] = sample['now'].isoformat()
    class Noon(datetime):
        @classmethod
        def now(cls, tz=None): return sample['now']
    position = {'symbol': sample['symbol'], 'qty': 10, 'entry_at': sample['entry_at'],
                'entry_price': sample['entry_price'], 'position_id': 'generation', 'latest_fill_id': 'fill'}
    event = {'time': sample['now'].isoformat(), 'market_source': 'NH KRX 1-minute completed bars',
             'market_bars': {sample['symbol']: sample['bars']}}
    trigger = {'state': 'triggered', 'triggered': True, 'reasons': ['pullback']}
    with patch.object(watch, 'datetime', Noon), patch.object(watch, 'positions', return_value=[position]), \
         patch.object(watch, '_position_current', return_value=True), \
         patch.object(watch, '_snapshot_quote', return_value=sample['quote']), \
         patch('nh_paper_isolated.RecordedBars.latest', return_value=event), \
         patch('vibe_event_trigger.detect', return_value=trigger) as detect, \
         patch('vibe_runner.load_advice', return_value=None), \
         patch.object(watch, '_existing_exit', return_value=None), \
         patch.object(watch, '_context_status', return_value={}):
        result = watch.inspect_once(Mock(), tmp_path, tmp_path, sample['now'])
    assert result[0]['state'] == 'unavailable'
    assert result[0]['event_trigger'] == trigger and result[0]['latest_fill_id'] == 'fill'
    assert detect.call_args.kwargs['quote_observed_at'] == sample['now'].isoformat()


def test_event_trigger_is_discarded_if_position_changes_after_detection(tmp_path):
    from test_vibe_fast_exit import inputs
    sample = inputs()
    class Noon(datetime):
        @classmethod
        def now(cls, tz=None): return sample['now']
    position = {'symbol': sample['symbol'], 'qty': 10, 'entry_at': sample['entry_at'],
                'entry_price': sample['entry_price'], 'position_id': 'generation', 'latest_fill_id': 'fill'}
    event = {'time': sample['now'].isoformat(), 'market_source': 'NH KRX 1-minute completed bars',
             'market_bars': {sample['symbol']: sample['bars']}}
    with patch.object(watch, 'datetime', Noon), patch.object(watch, 'positions', return_value=[position]), \
         patch.object(watch, '_position_current', side_effect=[True, False]), \
         patch.object(watch, '_snapshot_quote', return_value=sample['quote']), \
         patch('nh_paper_isolated.RecordedBars.latest', return_value=event), \
         patch('vibe_event_trigger.detect', return_value={'state': 'triggered', 'triggered': True}), \
         patch('vibe_runner.load_advice', return_value=None), \
         patch.object(watch, '_existing_exit', return_value=None), \
         patch.object(watch, '_context_status', return_value={}):
        result = watch.inspect_once(Mock(), tmp_path, tmp_path, sample['now'])
    assert result[0]['event_trigger']['triggered'] is False
    assert result[0]['event_trigger']['reasons'] == ['position_changed']


def test_observer_runs_real_price_trigger_without_fresh_model(tmp_path):
    from test_vibe_event_trigger import sample
    data = sample()
    class Noon(datetime):
        @classmethod
        def now(cls, tz=None): return data['now']
    position = {'symbol': data['symbol'], 'qty': data['qty'], 'entry_at': data['entry_at'],
                'entry_price': data['entry_price'], 'position_id': data['position_id'], 'latest_fill_id': 'fill'}
    event = {'time': data['bars_observed_at'].isoformat(), 'market_source': 'NH KRX 1-minute completed bars',
             'market_bars': {data['symbol']: data['bars']}}
    quote = {**data['quote'], '_snapshot_observed_at': data['quote_observed_at'].isoformat()}
    with patch.object(watch, 'datetime', Noon), patch.object(watch, 'positions', return_value=[position]), \
         patch.object(watch, '_position_current', return_value=True), \
         patch.object(watch, '_snapshot_quote', return_value=quote), \
         patch('nh_paper_isolated.RecordedBars.latest', return_value=event), \
         patch('vibe_runner.load_advice', return_value=None), \
         patch.object(watch, '_existing_exit', return_value=None), \
         patch.object(watch, '_context_status', return_value={}):
        result = watch.inspect_once(Mock(), tmp_path, tmp_path, data['now'])
    assert result[0]['state'] == 'unavailable'
    assert result[0]['event_trigger']['triggered'] is True
    assert result[0]['event_trigger']['reasons'] == ['rapid_rise_retracement']


def test_all_verified_holdings_are_checked_without_external_calls(tmp_path):
    from test_vibe_fast_exit import inputs
    sample = inputs()
    class Noon(datetime):
        @classmethod
        def now(cls, tz=None): return sample['now']
    positions = [{'symbol': f'{n:06d}', 'qty': 10, 'entry_at': sample['entry_at'],
                  'entry_price': sample['entry_price'], 'position_id': f'{n:020x}', 'latest_fill_id': f'fill{n}'}
                 for n in range(1, 5)]
    event = {'time': sample['now'].isoformat(), 'market_source': 'NH KRX 1-minute completed bars',
             'market_bars': {p['symbol']: sample['bars'] for p in positions}}
    read = Mock()
    with patch.object(watch, 'datetime', Noon), patch.object(watch, 'positions', return_value=positions), \
         patch.object(watch, '_position_current', return_value=True), \
         patch.object(watch, '_snapshot_quote', return_value=sample['quote']), \
         patch('nh_paper_isolated.RecordedBars.latest', return_value=event), \
         patch('vibe_event_trigger.detect', return_value={'state': 'wait', 'triggered': False}) as detect, \
         patch('vibe_runner.load_advice', return_value=None), \
         patch.object(watch, '_existing_exit', return_value=None), \
         patch.object(watch, '_context_status', return_value={}):
        result = watch.inspect_once(read, tmp_path, tmp_path, sample['now'])
    assert [row['symbol'] for row in result] == [p['symbol'] for p in positions]
    assert detect.call_count == len(positions)
    read.call_tool.assert_not_called()


def test_recent_bars_are_chosen_per_symbol_within_fresh_window(tmp_path):
    path = tmp_path / 'events.jsonl'
    def event(at, code, price):
        return {'event': 'market_context', 'time': at.isoformat(),
                'market_source': 'NH KRX 1-minute completed bars',
                'market_bars': {code: [{'bsop_time': '100800', 'stck_prpr': price}]}}
    rows = [event(NOW - timedelta(seconds=31), '100790', '900'),
            event(NOW - timedelta(seconds=20), '100790', '1000'),
            event(NOW - timedelta(seconds=10), '005930', '2000'),
            event(NOW - timedelta(seconds=15), '100790', '1100'),
            event(NOW + timedelta(seconds=1), '100790', '9999')]
    path.write_text('\n'.join(json.dumps(item) for item in rows), encoding='utf-8')
    found = watch._recent_bars(path, {'100790', '005930', '035420'}, NOW)
    assert found['100790'][0][0]['stck_prpr'] == '1100'
    assert found['100790'][1] == NOW - timedelta(seconds=15)
    assert found['005930'][0][0]['stck_prpr'] == '2000'
    assert '035420' not in found


def test_bid_depth_requires_exact_fresh_same_symbol_level_one():
    quote = {'iem_cd': '100790', 'market_status': '1', 'hoga_bsop_hour': '10:09:59',
             'bidp': 1000, 'bidp1': 1000, 'bidp_rsqn1': 42}
    assert watch._liquidity(quote, '100790', NOW)['bid_size_verified'] is True
    for changed in ({'bidp1': 999}, {'bidp_rsqn1': 0}, {'bidp_rsqn1': 1.0}, {'iem_cd': '005930'},
                    {'market_status': '0'}, {'hoga_bsop_hour': '10:09:00'}, {'hoga_bsop_hour': '10:10:01'}):
        assert watch._liquidity({**quote, **changed}, '100790', NOW) is None
