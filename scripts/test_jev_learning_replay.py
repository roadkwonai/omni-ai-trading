from contextlib import closing
from datetime import datetime
import json
import sqlite3

from jev_learning_replay import collect


DAY = '2026-11-25'


def stamp(clock):
    return f'{DAY}T{clock}:00+09:00'


def test_collect_joins_exact_cycle_preserves_wait_and_builds_only_mature_position():
    with closing(sqlite3.connect(':memory:')) as ledger, closing(sqlite3.connect(':memory:')) as research, \
            closing(sqlite3.connect(':memory:')) as orders:
        ledger.execute('CREATE TABLE events_v2 (digest, time, event, data)')
        research.execute('CREATE TABLE model_inputs (digest, time, engine, question, inputs, response)')
        orders.executescript('CREATE TABLE fills (order_id, symbol, side, quantity, price, time); '
                             'CREATE TABLE decision_order_links (order_id, cycle_id);')
        rows = [
            ('c', '09:30', 'jev_shadow', {'jev_choice': 'WAIT', 'jev_input_digest': 'exact',
                                        'jev_response_time': stamp('09:30')}),
            ('c', '09:31', 'watchlist_strength', {'symbol': '005930'}),
            ('c', '09:31', 'submitted', {'symbol': '005930', 'source': 'watchlist_strength', 'qty': 2,
                                        'open_gap_pct': 10, 'entry_timing': {'gate_time': stamp('09:31')}}),
            ('h', '09:40', 'jev_holding_decision', {'symbol': '005930', 'choice': 'HOLD', 'input_digest': 'held'}),
            ('later', '11:30', 'jev_shadow', {'jev_choice': 'WAIT'}),
        ]
        for sequence, (cycle, clock, event, data) in enumerate(rows):
            ledger.execute('INSERT INTO events_v2 VALUES (?,?,?,?)',
                           (str(sequence), stamp(clock), event, json.dumps({'cycle_id': cycle, **data})))
        research.executemany('INSERT INTO model_inputs VALUES (?,?,?,?,?,?)', [
            ('exact', stamp('09:30'), 'jev', 'candidate', '{"criteria":{"005930":"삼성전자 개별주식(KOSPI200)"}}',
             '{"choice":"WAIT"}'),
            ('other', stamp('09:31'), 'jev', 'candidate', '{}', '{"choice":"005930"}'),
            ('held', stamp('09:41'), 'jev', 'holding', '{}', '{"choice":"HOLD"}'),
            ('w', stamp('09:15'), 'jev', 'watchlist', '{}', '{"choice":"NONE"}'),
        ])
        orders.executemany('INSERT INTO fills VALUES (?,?,?,?,?,?)', [
            ('buy', '005930', 'BUY', 2, 100, stamp('09:31')),
            ('part', '005930', 'SELL', 1, 110, stamp('09:50')),
            ('exit', '005930', 'SELL', 1, 104, stamp('10:30')),
        ])
        orders.execute("INSERT INTO decision_order_links VALUES ('buy','c')")
        cutoff = datetime.fromisoformat(stamp('10:29'))
        report = collect(ledger, research, orders, start=cutoff.date(), end=cutoff.date(), as_of=cutoff, cost_bps=50)
        assert report['counts']['candidate'] == 1
        assert report['counts']['watchlist'] == 1
        entry = report['decisions']['candidate'][0]
        assert (entry['choice'], entry['input_digest'], entry['orders'][0]['order_id']) == ('WAIT', 'exact', 'buy')
        assert entry['strength'][0]['policies']['conditional']['status'] == 'unknown'
        assert report['decisions']['holding'][0]['input_status'] == 'recorded_after_decision'
        assert report['samples'] == []
        assert report['position_status']['position_open'] == 1
        cutoff = datetime.fromisoformat(stamp('11:00'))
        def evidence(symbol, at):
            return {'pressure': {'symbol': symbol, 'source': 'fixture', 'source_at': stamp('09:30'),
                                 'received_at': at.isoformat(), 'selling': True}}

        def bars(day):
            assert day == DAY
            return {'005930': [(datetime.fromisoformat(stamp('09:28')), 100, 100, 100, 100,
                                datetime.fromisoformat(stamp('09:29'))),
                               (datetime.fromisoformat(stamp('09:34')), 110, 110, 110, 110,
                                datetime.fromisoformat(stamp('09:35')))]}

        report = collect(ledger, research, orders, start=cutoff.date(), end=cutoff.date(), as_of=cutoff, cost_bps=50,
                         evidence_for=evidence, bars_for=bars)
        assert len(report['samples']) == 1
        sample = report['samples'][0]
        assert sample['purpose'] == 'candidate'
        assert sample['route'] == 'watchlist_strength'
        assert sample['cost_basis'] == 'included'
        assert sample['return_pct'] == 5.965
        assert sample['outcome_available_at'] == stamp('10:30')
        assert report['decisions']['candidate'][0]['strength'][0]['policies']['conditional']['status'] == 'block'
        observed = report['decisions']['candidate'][0]['price_observations'][0]
        assert observed['symbol'] == '005930'
        assert observed['r5_pct'] == 10
        assert observed['r15_pct'] is None
        assert observed['label_type'] == 'price_observation'
