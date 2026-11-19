import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import vibe_advisor as a

UNIVERSE = ['069500', '114800', '005930']


class AdvisorTests(unittest.TestCase):
    def test_account_state_rejects_stale_future_naive_corrupt_and_bad_holdings(self):
        import json, tempfile
        from datetime import datetime, timedelta
        from pathlib import Path
        now = datetime(2026, 11, 16, 12, 0, tzinfo=a.KST)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'account-state.json'
            good = {'time': (now - timedelta(seconds=30)).isoformat(), 'holdings': {'100790': {'rsdl_qty': 65}}}
            path.write_text(json.dumps(good))
            self.assertEqual(a.account_state(tmp, now), good)
            for state in ({**good, 'time': (now - timedelta(hours=3)).isoformat()},
                          {**good, 'time': (now + timedelta(seconds=1)).isoformat()},
                          {**good, 'time': '2026-11-16T11:59:00'},
                          {**good, 'holdings': {'100790': {'rsdl_qty': -1}}}):
                path.write_text(json.dumps(state))
                with self.assertRaises(a.AccountStateUnavailable):
                    a.account_state(tmp, now)
            path.write_text('{')
            with self.assertRaises(a.AccountStateUnavailable):
                a.account_state(tmp, now)
            path.unlink()
            with self.assertRaises(a.AccountStateUnavailable):
                a.account_state(tmp, now)

    def test_main_invalidates_prior_advice_when_account_unknown(self):
        import json, sys, tempfile
        from pathlib import Path
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(a, 'ROOT', Path(tmp)), \
             mock.patch('research_store.record') as store, \
             mock.patch.object(sys, 'argv', ['vibe_advisor.py', '--once']), \
             mock.patch('src.config.loader.load_agent_config'), \
             mock.patch('src.tools.mcp.MCPServerAdapter'), \
             mock.patch.object(a, 'advise', side_effect=a.AccountStateUnavailable('unknown')) as advise, \
             mock.patch.object(a.time, 'sleep') as slept:
            (Path(tmp) / 'vibe-advice.json').write_text('{"decisions": [{"action":"BUY"}]}')
            a.main()
            self.assertEqual((advise.call_count, slept.call_args.args), (2, (45,)))  # looked once more after the state race
            invalid = json.loads((Path(tmp) / 'vibe-advice.json').read_text())
            self.assertEqual(store.call_args.args[0], 'vibe_advice')
        self.assertEqual((invalid['scores'], invalid['signals'], invalid['decisions']), ({}, {}, []))
        self.assertFalse(invalid['grounded'])
        self.assertEqual(invalid['error'], 'account_state_unavailable')

    def test_account_fill_ids_detects_stale_or_changed_position_generation(self):
        import sqlite3, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'orders.sqlite3'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE fills (order_id TEXT PRIMARY KEY, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT)')
                db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('first', '100790', 'BUY', 65, 100, '2026-11-16T11:59:00+09:00'))
            state = {'time': '2026-11-16T12:00:00+09:00', 'holdings': {'100790': {'rsdl_qty': 65}}}
            self.assertEqual(a.account_fill_ids(tmp, state), {'100790': 'first'})
            with sqlite3.connect(path) as db:
                db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('sell', '100790', 'SELL', 65, 99, '2026-11-16T12:01:00+09:00'))
                db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('next', '100790', 'BUY', 65, 100, '2026-11-16T12:02:00+09:00'))
            with self.assertRaises(a.AccountStateUnavailable):
                a.account_fill_ids(tmp, state)
            state['time'] = '2026-11-16T12:03:00+09:00'
            self.assertEqual(a.account_fill_ids(tmp, state), {'100790': 'next'})
            state['holdings']['100790']['rsdl_qty'] = 64
            with self.assertRaises(a.AccountStateUnavailable):
                a.account_fill_ids(tmp, state)

    def test_premarket_uses_common_filtered_view_and_tolerates_read_error(self):
        now, root = object(), object()
        read = Mock(return_value={'time': 'today'})
        with patch.dict('sys.modules', {'premarket': SimpleNamespace(engine_brief=read)}):
            self.assertEqual(a.premarket_view(root, now), {'time': 'today'})
            read.assert_called_once_with(root, now)
            read.side_effect = ValueError('invalid brief')
            self.assertIsNone(a.premarket_view(root, now))

    def test_parse_scores_from_last_json(self):
        text = ('thinking {"draft": true}\n'
                '{"market_view": "상승", "scores": ['
                '{"symbol": "005930", "score": 0.8, "evidence": "거래대금"},'
                '{"symbol": "114800", "score": -0.6, "evidence": "x"},'
                '{"symbol": "000660", "score": 0.9},'
                '{"symbol": "069500", "score": 1.5}]}\nStatus: SUCCESS')
        advice = a.parse(text, UNIVERSE)
        self.assertEqual(set(advice['scores']), {'005930', '114800'})
        self.assertEqual(advice['market_view'], '상승')

    def test_rule_turns_scores_into_decisions(self):
        scores = {'005930': {'score': 0.8, 'evidence': 'a'}, '069500': {'score': 0.4, 'evidence': 'b'},
                  '114800': {'score': -0.3, 'evidence': 'c'}, '229200': {'score': 0.55, 'evidence': 'd'},
                  '233740': {'score': -0.9, 'evidence': 'e'}}
        previous = {'005930': {'score': 0.8, 'evidence': ''}, '229200': {'score': 0.55, 'evidence': ''}}
        decisions = a.decisions_from(scores, held={'114800'}, previous=previous)
        self.assertEqual([(d['action'], d['symbol'], d.get('notional', d.get('quantity'))) for d in decisions],
                         [('SELL', '114800', 'ALL'), ('BUY', '005930', 2_400_000), ('BUY', '229200', 1_650_000)])
        # without an agreeing previous run a rise is not confirmed; a fall still sells
        self.assertEqual([d['action'] for d in a.decisions_from(scores, held={'114800'})], ['SELL'])

    def test_saturated_single_run_does_not_buy(self):
        # 12:34 on 2026-11-12: almost every score +-1.0 after a calm run; it bought 3,000,000 KRW of 005930 before
        run = {c: {'score': v, 'evidence': ''} for c, v in
               {'005930': 1.0, '007660': 1.0, '069500': -1.0, '114800': 1.0, '122630': -1.0, '125490': 0.0}.items()}
        calm = {c: {'score': v, 'evidence': ''} for c, v in {'005930': 0.2, '007660': 0.25, '114800': 0.1}.items()}
        self.assertTrue(a.desaturate(run)[1])
        self.assertEqual(a.signals(run, calm)['005930'], 0.35)
        self.assertEqual([d['action'] for d in a.decisions_from(run, held=set(), previous=calm)], [])
        # two agreeing saturated runs still buy, at half size
        self.assertEqual([(d['symbol'], d['notional']) for d in a.decisions_from(run, held=set(), previous=run)][:1],
                         [('005930', 1_500_000)])

    def test_never_both_sides_of_one_index(self):
        both = {'122630': {'score': 0.9, 'evidence': ''}, '114800': {'score': 0.7, 'evidence': ''}}
        picked = a.decisions_from(both, held=set(), previous=both)
        self.assertEqual([d['symbol'] for d in picked], ['122630'])  # the stronger side only
        self.assertEqual([d['symbol'] for d in a.decisions_from(both, held={'114800'}, previous=both)], ['114800'])  # same side only

    def test_parse_rejects_missing_json_and_flags_order_tools(self):
        with self.assertRaises(ValueError):
            a.parse('no json here', UNIVERSE)
        self.assertTrue(a.FORBIDDEN.search('calling trading_place_order now'))
        self.assertFalse(a.FORBIDDEN.search('{"scores": []}'))


    def test_ungrounded_run_or_skill_short_vote_never_buys(self):
        run = {'005930': {'score': 0.8, 'evidence': ''}, '069500': {'score': 0.7, 'evidence': ''}}
        self.assertEqual([d['symbol'] for d in a.decisions_from(run, set(), run)], ['005930', '069500'])
        self.assertEqual(a.decisions_from(run, set(), run, grounded=False), [])  # read only the prompt: no buys
        self.assertEqual([d['symbol'] for d in a.decisions_from(run, set(), run, votes={'005930': -1})], ['069500'])
        falling = {'005930': {'score': -0.6, 'evidence': ''}}
        self.assertEqual([d['action'] for d in a.decisions_from(falling, {'005930'}, grounded=False)], ['SELL'])  # sells still act

    def test_technical_basic_skill_runs_on_nh_minute_bars(self):
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        now = datetime(2026, 11, 12, 11, 0, tzinfo=ZoneInfo('Asia/Seoul'))

        class Read:
            def call_tool(self, tool, params):
                rows = [{'bsop_date': '20261112', 'bsop_time': (now.replace(hour=9, minute=59) + timedelta(minutes=m)).strftime('%H%M%S'),
                         'stck_oprc': str(1000 + 2 * m), 'stck_hgpr': str(1003 + 2 * m), 'stck_lwpr': str(999 + 2 * m),
                         'stck_prpr': str(1002 + 2 * m), 'vol': str(1000 + 10 * m)} for m in range(60)]  # 09:10-10:09, rising
                return {'status': 'ok', 'data': {'rsp_cd': '00000', 'Output_0': {'iem_cd': params['input']['iem_cd']},
                                                 'Output_1': rows[::-1] if params['input']['iem_cd'] == '069500' else rows[:20]}}
        votes = a.technical_votes(Read(), ['069500', '114800'], now)
        self.assertEqual((votes['069500']['bars'], votes['069500']['trend'], votes['069500']['obv_vs_mean']), (60, 'up', 'up'))
        self.assertIn(votes['069500']['vote'], (0, 1))
        self.assertEqual(votes['114800'], {'status': 'insufficient_bars'})


    def test_reading_a_skill_document_is_not_market_data(self):
        import json, tempfile
        from pathlib import Path
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            run = Path(d) / 'runs' / 'r1' / 'artifacts'
            run.mkdir(parents=True)
            (run / 'grounding_evidence.json').write_text(json.dumps({'evidence': [{'tool': 'load_skill'}] * 6}))
            with patch.object(a, 'ADVISOR_HOME', Path(d)):
                self.assertEqual(a.run_grounding('r1')['tool_observations'], 0)
                (run / 'grounding_evidence.json').write_text(json.dumps({'evidence': [{'tool': 'load_skill'}, {'tool': 'get_market_data'}]}))
                self.assertEqual(a.run_grounding('r1')['tool_observations'], 1)
                self.assertEqual(a.run_grounding(None)['tool_observations'], 0)


    def test_one_tool_call_covers_every_scored_code(self):
        ctx = {'watchlist_stocks': {'036540': {'index': 'KOSDAQ150'}, '005930': {'index': 'KOSPI200'}}}
        self.assertEqual(a.yahoo_codes(ctx, ['005930', '036540', '229200']), ['005930.KS', '036540.KQ', '229200.KS'])
        text = a.prompt(ctx, ['005930', '036540'])
        self.assertIn('005930.KS, 036540.KQ', text)
        self.assertIn('| count | directional score', text)
        self.assertIn('max_rows 3', text)


    def test_reuses_the_shared_runner_record_instead_of_nh(self):
        import json, tempfile
        from datetime import datetime, timedelta
        from pathlib import Path
        from zoneinfo import ZoneInfo
        import vibe_market_brief as brief
        now = datetime(2026, 11, 12, 11, 0, 30, tzinfo=ZoneInfo('Asia/Seoul'))
        bar = lambda m: {'bsop_time': f'{9 + (m + 10) // 60:02d}{(m + 10) % 60:02d}00', 'stck_oprc': str(1000 + m),
                         'stck_hgpr': str(1002 + m), 'stck_lwpr': str(999 + m), 'stck_prpr': str(1001 + m), 'vol': '1000'}
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'events.jsonl'
            with path.open('w') as f:  # one record a minute, each with its latest 10 bars (as the runner writes them)
                for m in range(10, 60):
                    at = now.replace(hour=9, minute=10, second=5) + timedelta(minutes=m + 2)
                    f.write(json.dumps({'time': at.isoformat(), 'event': 'market_context',
                                        'market_source': 'NH KRX 1-minute completed bars',
                                        'market_bars': {'K200': [bar(k) for k in range(m, m - 10, -1)]},
                                        'investor_flows': {'K200': {'last_close_day': {'date': '20261111', 'foreign': 1}}}}) + '\n')
            latest, bars = a.recorded_market(now.replace(hour=10, minute=11), path)
            self.assertEqual(len(bars['069500']), 59)  # union of the minute records: 09:11-10:09
            self.assertIsNotNone(latest)
            self.assertEqual(a.recorded_market(now.replace(hour=11, minute=0), path)[0], None)  # stale: not used as current

            class NoNH:
                calls = []
                def call_tool(self, tool, params):
                    self.calls.append(params.get('operationId') or tool)
                    raise AssertionError('NH must not be asked')
            _, fresh_bars = a.recorded_market(now.replace(hour=10, minute=11), path)
            votes = a.technical_votes(NoNH(), ['069500'], now.replace(hour=10, minute=11), fresh_bars)
            self.assertEqual((votes['069500']['source'], votes['069500']['bars']), ('shared_runner_record', 59))

            class QuotesOnly:
                calls = []
                def call_tool(self, tool, params):
                    self.calls.append(params.get('operationId') or tool)
                    return {'status': 'ok', 'data': {'rsp_cd': '00000', 'Output_0': {'stck_prpr': 1, 'stck_prdy_clpr': 1}}}
            reader = QuotesOnly()
            rows = brief.market(reader, now, latest)
            self.assertEqual(rows['K200']['investor_daily_net_qty'], {'last_close_day': {'date': '20261111', 'foreign': 1}})
            self.assertNotIn('krstockQuotePeriod', [c for c in reader.calls[:1]])  # K200: quote only
            self.assertEqual(reader.calls[0], 'get_stock_price')

    def test_stale_per_symbol_bars_fall_back_despite_fresh_other_symbol(self):
        import json, tempfile
        from datetime import datetime, timedelta
        from pathlib import Path
        now = datetime(2026, 11, 16, 14, 0, tzinfo=a.KST)
        def bars(start, count):
            return [{'bsop_date': '20261116', 'bsop_time': (start + timedelta(minutes=i)).strftime('%H%M%S'),
                     'stck_oprc': 100+i, 'stck_hgpr': 102+i, 'stck_lwpr': 99+i,
                     'stck_prpr': 101+i, 'vol': 100+i} for i in range(count)]
        stale = bars(now.replace(hour=9, minute=10), 45)  # last completed bar 09:54
        fresh = bars(now - timedelta(minutes=46), 45)  # last completed bar 13:58
        class Read:
            calls = []
            def call_tool(self, tool, params):
                self.calls.append(params['input']['iem_cd'])
                return {'status': 'ok', 'data': {'rsp_cd': '00000', 'Output_0': {'iem_cd': '100790'},
                                                  'Output_1': fresh}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'events.jsonl'
            events = [
                {'time': now.replace(hour=9, minute=56).isoformat(), 'event': 'market_context',
                 'market_source': 'NH KRX 1-minute completed bars', 'market_bars': {'100790': stale}},
                {'time': (now - timedelta(seconds=30)).isoformat(), 'event': 'market_context',
                 'market_source': 'NH KRX 1-minute completed bars', 'market_bars': {'K200': fresh}},
            ]
            path.write_text(''.join(json.dumps(e) + '\n' for e in events))
            latest, recorded = a.recorded_market(now, path)
            self.assertIsNotNone(latest)
            read = Read()
            votes = a.technical_votes(read, ['100790'], now, recorded)
            self.assertEqual(read.calls, ['100790'])
            self.assertEqual(votes['100790']['source'], 'nh')
            self.assertEqual(votes['100790']['as_of'], '13:58')

    def test_bad_or_stale_fallback_bars_never_produce_a_vote(self):
        from datetime import datetime, timedelta
        now = datetime(2026, 11, 16, 14, 0, tzinfo=a.KST)
        rows = [{'bsop_date': '20261116', 'bsop_time': (now.replace(hour=9, minute=10) + timedelta(minutes=i)).strftime('%H%M%S'),
                 'stck_oprc': 100, 'stck_hgpr': 102, 'stck_lwpr': 99, 'stck_prpr': 101, 'vol': 100}
                for i in range(45)]
        class Read:
            def __init__(self, data): self.data = data
            def call_tool(self, *_): return self.data
        for data in (
                {'status': 'ok', 'data': {'rsp_cd': '00000', 'Output_0': {'iem_cd': '100790'}, 'Output_1': rows}},
                {'status': 'ok', 'data': {'rsp_cd': '00000', 'Output_0': {'iem_cd': '000001'}, 'Output_1': rows}},
                {'status': 'error', 'data': {'rsp_cd': '00000', 'Output_0': {'iem_cd': '100790'}, 'Output_1': rows}}):
            self.assertEqual(a.technical_votes(Read(data), ['100790'], now)['100790']['status'], 'unavailable')

    def test_duplicate_missing_or_future_bars_are_not_interpolated(self):
        from datetime import datetime, timedelta
        now = datetime(2026, 11, 16, 14, 0, tzinfo=a.KST)
        rows = [{'bsop_date': '20261116', 'bsop_time': (now - timedelta(minutes=46-i)).strftime('%H%M%S'),
                 'stck_oprc': 100, 'stck_hgpr': 102, 'stck_lwpr': 99, 'stck_prpr': 101, 'vol': 100}
                for i in range(45)]
        self.assertEqual(len(a.technical_rows(rows, '100790', now)), 45)
        with self.assertRaises(ValueError):
            a.technical_rows(rows + [rows[-1]], '100790', now)
        self.assertEqual(a.technical_rows(rows[:20] + rows[21:], '100790', now), [])
        future = {**rows[-1], 'bsop_time': '140100'}
        self.assertEqual(len(a.technical_rows(rows + [future], '100790', now)), 45)

    def test_recorded_symbol_and_observation_provenance_are_required(self):
        from datetime import datetime, timedelta
        now = datetime(2026, 11, 16, 14, 0, tzinfo=a.KST)
        rows = [{'bsop_date': '20261116', 'bsop_time': (now - timedelta(minutes=46-i)).strftime('%H%M%S'),
                 'stck_oprc': 100, 'stck_hgpr': 102, 'stck_lwpr': 99, 'stck_prpr': 101, 'vol': 100,
                 '_symbol': '100790', '_observed_at': now.isoformat()}
                for i in range(45)]
        self.assertEqual(len(a.technical_rows(rows, '100790', now, recorded=True)), 45)
        for change in ({'_symbol': '280360'}, {'_observed_at': (now + timedelta(seconds=1)).isoformat()},
                       {'_observed_at': (now - timedelta(minutes=10)).isoformat()}):
            altered = rows[:-1] + [{**rows[-1], **change}]
            with self.assertRaises(ValueError):
                a.technical_rows(altered, '100790', now, recorded=True)

    def test_untrusted_or_future_recorded_market_event_is_not_reused(self):
        import json, tempfile
        from datetime import datetime, timedelta
        from pathlib import Path
        now = datetime(2026, 11, 16, 14, 0, tzinfo=a.KST)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'events.jsonl'
            rows = [{'bsop_time': '135800', 'stck_oprc': 100, 'stck_hgpr': 102,
                     'stck_lwpr': 99, 'stck_prpr': 101, 'vol': 100}]
            events = [
                {'time': now.isoformat(), 'event': 'market_context', 'market_source': 'unknown',
                 'market_bars': {'100790': rows}},
                {'time': (now + timedelta(seconds=1)).isoformat(), 'event': 'market_context',
                 'market_source': 'NH KRX 1-minute completed bars', 'market_bars': {'100790': rows}},
            ]
            path.write_text(''.join(json.dumps(e) + '\n' for e in events))
            self.assertEqual(a.recorded_market(now, path), (None, {}))

    def test_write_records_available_time_after_publication(self):
        import json, tempfile
        from pathlib import Path
        from datetime import datetime
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp, patch.object(a, 'ROOT', Path(tmp)), \
             patch('research_store.record') as store:
            record = {'time': datetime.now(a.KST).isoformat(), 'scores': {}}
            a.write(record)
            self.assertEqual(json.loads((Path(tmp) / 'vibe-advice.json').read_text()), record)
            saved = store.call_args.args[1]
            self.assertGreaterEqual(datetime.fromisoformat(saved['available_at']), datetime.fromisoformat(record['time']))


class AdviseRetryTests(unittest.TestCase):
    def test_unavailable_technical_bars_block_buys_but_keep_protective_model_sell(self):
        import tempfile
        from datetime import datetime
        from pathlib import Path
        from unittest import mock
        account = {'time': datetime.now(a.KST).isoformat(), 'holdings': {'100790': {'rsdl_qty': 3}}}
        ctx = {'watchlist_stocks': {'005930': {'change_pct': 5}}, 'my_paper_account': account,
               'account_fill_ids': {'100790': 'fill'}}
        text = ('{"scores": [{"symbol": "005930", "score": 0.9, "evidence": "buy"},'
                '{"symbol": "100790", "score": -0.9, "evidence": "sell"}]}')
        previous = {'005930': {'score': 0.9, 'evidence': ''}}
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(a, 'ROOT', Path(tmp)), \
             mock.patch.object(a, 'recorded_market', return_value=(None, None)), \
             mock.patch.object(a, 'context', return_value=ctx), \
             mock.patch.object(a, 'oversold_watch', return_value={}), \
             mock.patch('oversold_cases.bars_context', return_value={}), \
             mock.patch.object(a, 'account_state', return_value=account), \
             mock.patch.object(a, 'account_fill_ids', return_value={'100790': 'fill'}), \
             mock.patch.object(a, 'technical_votes') as technical, \
             mock.patch.object(a, 'prompt', return_value='p'), \
             mock.patch.object(a, 'previous_scores', return_value=previous), \
             mock.patch.object(a, 'run_grounding', return_value={'tool_observations': 1}), \
             mock.patch('research_store.record_input', return_value='d'), \
             mock.patch.object(a, 'ask', return_value=(text, 'r1')):
            for bad in ({'status': 'unavailable'}, {'vote': True}, {'vote': 1, 'volume_ratio': float('inf')}):
                technical.return_value = {'005930': bad, '100790': {'status': 'unavailable'}}
                result = a.advise(None, datetime.now(a.KST))
                self.assertEqual([(x['action'], x['symbol']) for x in result['decisions']], [('SELL', '100790')])
                self.assertEqual(result['signals']['005930'], 0.0)
                self.assertIsNotNone(datetime.fromisoformat(result['completed_at']).tzinfo)

    def test_a_rejected_answer_is_asked_once_more_then_given_up(self):
        import tempfile
        from datetime import datetime
        from pathlib import Path
        from unittest import mock
        good = '{"market_view": "v", "scores": [{"symbol": "069500", "score": 0.2, "evidence": "e"}]}'
        account = {'time': datetime.now(a.KST).isoformat(), 'holdings': {}}
        ctx = {'watchlist_stocks': [], 'my_paper_account': account, 'account_fill_ids': {}}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(a, 'ROOT', Path(tmp)), \
                mock.patch.object(a, 'recorded_market', return_value=(None, None)), \
                mock.patch.object(a, 'context', return_value=ctx), \
                mock.patch.object(a, 'account_state', return_value=account), \
                mock.patch.object(a, 'account_fill_ids', return_value={}), \
                mock.patch.object(a, 'technical_votes', return_value={}), \
                mock.patch.object(a, 'prompt', return_value='p'), \
                mock.patch.object(a, 'previous_scores', return_value=None), \
                mock.patch.object(a, 'run_grounding', return_value={'tool_observations': 1}), \
                mock.patch('research_store.record_input', return_value='d'):
            with mock.patch.object(a, 'ask', side_effect=[('rejected', 'r1'), (good, 'r2')]) as ask:
                advice = a.advise(None, datetime.now(a.KST))
            self.assertEqual((ask.call_count, advice['run_id']), (2, 'r2'))
            with mock.patch.object(a, 'ask', side_effect=[('rejected', 'r1'), ('again', 'r2')]) as ask:
                with self.assertRaises(ValueError):
                    a.advise(None, datetime.now(a.KST))
            self.assertEqual(ask.call_count, 2)
            with mock.patch.object(a, 'ask', return_value=(good, 'r1')), \
                 mock.patch.object(a, 'account_fill_ids', return_value={'005930': 'new-fill'}):
                with self.assertRaises(a.AccountStateUnavailable):
                    a.advise(None, datetime.now(a.KST))


class StrengthRouteTests(unittest.TestCase):
    def test_strength_route_buys_a_rising_watchlist_stock_on_a_modest_score(self):
        ctx = {'watchlist_stocks': {'036540': {'change_pct': 6.7}, '005930': {'change_pct': 1.0}, '009150': {'change_pct': 4.0},
                                    '000660': {'change_pct': 5.0}}}
        votes = {'036540': {'vote': 0, 'volume_ratio': 1.5}, '005930': {'vote': 1, 'volume_ratio': 2.0},
                 '009150': {'vote': 0, 'volume_ratio': 0.8}, '000660': {'vote': -1, 'volume_ratio': 3.0}}
        settings = {'strength_change_min': 2.0, 'strength_change_max': 25.0, 'entry_volume_ratio': 1.2}
        self.assertEqual(a.strength_candidates(ctx, votes, settings), ['036540'])  # too little rise / volume / short vote
        self.assertEqual(a.strength_candidates(ctx, votes, {**settings, 'entry_volume_ratio': 0}), ['036540', '009150'])
        scores = {'036540': {'score': 0.25, 'evidence': 'e'}, '069500': {'score': 0.1, 'evidence': 'e'}}
        decisions = a.decisions_from(scores, set(), None, {}, True, ['036540'])
        self.assertEqual([(d['symbol'], d['notional'], d['route']) for d in decisions], [('036540', a.STRENGTH_KRW, 'strength')])
        self.assertEqual(a.decisions_from({'036540': {'score': 0.15, 'evidence': 'e'}}, set(), None, {}, True, ['036540']), [])
        self.assertEqual(a.decisions_from(scores, {'036540'}, None, {}, True, ['036540']), [])  # already held
        self.assertEqual(a.decisions_from(scores, set(), None, {}, False, ['036540']), [])  # ungrounded run

    def test_strength_requires_verified_vote_even_when_volume_check_disabled(self):
        ctx = {'watchlist_stocks': {'005930': {'change_pct': 5}, '100790': {'change_pct': 5},
                                    '280360': {'change_pct': 5}, '000660': {'change_pct': 5}}}
        votes = {'005930': {'status': 'unavailable'}, '100790': {'vote': True, 'volume_ratio': 2},
                 '280360': {'vote': 1, 'volume_ratio': float('inf')}, '000660': {'vote': 0}}
        self.assertEqual(a.strength_candidates(ctx, votes, {'entry_volume_ratio': 0}), ['000660'])

    def test_intraday_skills_report_vwap_volume_and_candles(self):
        import pandas as pd
        n = 30
        frame = pd.DataFrame({'open': [100.0] * n, 'high': [101.0] * n, 'low': [99.0] * n, 'close': [100.0] * (n - 1) + [100.8],
                              'volume': [100.0] * 25 + [300.0] * 5}, index=pd.date_range('2026-11-13 09:00', periods=n, freq='min'))
        out = a.intraday(frame, a.skill_module('minute-analysis'), a.skill_module('candlestick').SignalEngine())
        self.assertEqual(out['volume_ratio'], 3.0)
        self.assertGreater(out['vs_vwap_pct'], 0)
        self.assertIn('range_position_pct', out)
        self.assertIsInstance(out['candles_last3'], list)


    def test_error_placeholder_is_not_a_confirming_previous_run(self):
        from datetime import datetime, timedelta
        from unittest import mock
        from zoneinfo import ZoneInfo
        started = datetime(2026, 11, 17, 10, 15, tzinfo=ZoneInfo('Asia/Seoul'))
        earlier = (started - timedelta(minutes=15)).isoformat()
        cases = (({'time': earlier, 'scores': {}, 'error': 'account_state_unavailable'}, None),
                 ({'time': earlier, 'scores': {}}, None),
                 ({'time': earlier, 'scores': {'005930': {'score': 0.6}}}, {'005930': {'score': 0.6}}))
        for record, expected in cases:
            with self.subTest(record=record), mock.patch('research_store.history', return_value=[record]):
                self.assertEqual(a.previous_scores(started), expected)


    def test_chase_filter_skips_strength_buys_near_the_top(self):
        from unittest import mock
        with mock.patch.object(a, 'CHASE_VWAP_PCT', 2.0), mock.patch.object(a, 'CHASE_RANGE_PCT', 85):
            # 11/16: 100790 VWAP +4.04%, 280360 range 92% -> both skipped
            self.assertEqual(a.chase_reason({'vs_vwap_pct': 4.04, 'range_position_pct': 69}), 'above_vwap')
            self.assertEqual(a.chase_reason({'vs_vwap_pct': 1.5, 'range_position_pct': 92}), 'near_range_top')
            self.assertIsNone(a.chase_reason({'vs_vwap_pct': 2.0, 'range_position_pct': 84}))  # at the limits: buy
            self.assertIsNone(a.chase_reason({'vs_vwap_pct': -0.5, 'range_position_pct': 40}))  # a pullback re-entry is fine
            self.assertEqual(a.chase_reason({'range_position_pct': 40}), 'chase_unmeasured')  # unknown: reported, not blocked
            self.assertEqual(a.chase_reason(None), 'chase_unmeasured')


if __name__ == '__main__':
    unittest.main()
