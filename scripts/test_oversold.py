import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import nh_paper_isolated as p
import oversold
from strategy_settings import DEFAULTS

KST = ZoneInfo('Asia/Seoul')
NOW = datetime(2026, 11, 6, 11, 0, tzinfo=KST)
SAMSUNG = {'iem_cd': '005930', 'stck_prpr': 180000, 'stck_prdy_clpr': 201500, 'stck_lwpr': 179200}


class PremarketInputAuditTests(unittest.TestCase):
    def test_recorded_input_distinguishes_context_from_missing_evidence(self):
        import research_store
        brief = {'as_of': NOW.isoformat(), 'sources': {'news': {'status': 'partial', 'observed_at': NOW.isoformat()}},
                 'news': [{'title': 'not copied into audit'}]}
        with tempfile.TemporaryDirectory() as d, patch.object(research_store, 'ROOT', Path(d)):
            digest = research_store.record_input('jev', 'oversold', {'state': {'premarket_context': brief}}, {})
            audit = p.premarket_input_audit(digest, 'jev')
            self.assertEqual(audit['status'], 'included_in_recorded_input')
            self.assertEqual(audit['sources']['news']['status'], 'partial')
            self.assertNotIn('not copied into audit', str(audit))
            self.assertEqual(p.premarket_input_audit(digest, 'vibe')['status'], 'input_record_unavailable')
            missing = research_store.record_input('vibe', 'scores', {'context': {'premarket_context': None}}, {})
            self.assertEqual(p.premarket_input_audit(missing, 'vibe')['status'], 'context_unavailable')
            self.assertEqual(p.premarket_input_audit(None, 'jev')['reason'], 'digest_missing_or_invalid')
            self.assertEqual(p.premarket_input_audit('0' * 64, 'jev')['reason'], 'input_row_missing')
        with tempfile.TemporaryDirectory() as d, patch.object(research_store, 'ROOT', Path(d)):
            self.assertEqual(p.premarket_input_audit('0' * 64, 'jev')['reason'], 'input_lookup_error')


class EvaluateTests(unittest.TestCase):
    def test_samsung_11_06_triggers_after_the_fall_stops(self):
        calm = [{'stck_lwpr': 179600}] * 20
        row = oversold.evaluate('005930', SAMSUNG, calm, 6.7, DEFAULTS, NOW)
        self.assertTrue(row['triggered'] and row['stabilized'])
        self.assertEqual((row['target_price'], row['stop_price']), (182700, 178304))  # +1.5%, low -0.5%
        still_falling = [{'stck_lwpr': 179200}] + calm[1:]  # a new low within the last 20 minutes
        self.assertFalse(oversold.evaluate('005930', SAMSUNG, still_falling, 6.7, DEFAULTS, NOW)['stabilized'])

    def test_thresholds_by_kind(self):
        shallow = {**SAMSUNG, 'stck_lwpr': 190000, 'stck_prpr': 191000}  # -5.7%: under the 6% floor
        self.assertFalse(oversold.evaluate('005930', shallow, [], None, DEFAULTS, NOW)['triggered'])
        self.assertFalse(oversold.evaluate('005930', SAMSUNG, [], 8.0, DEFAULTS, NOW)['triggered'])  # -11% < 1.5 x 8% ATR
        lev = {'iem_cd': '122630', 'stck_prpr': 9000, 'stck_prdy_clpr': 10000, 'stck_lwpr': 8800}
        self.assertIsNone(oversold.evaluate('122630', lev, [], 3.0, DEFAULTS, NOW))  # 2x ETFs off by default
        self.assertTrue(oversold.evaluate('122630', lev, [], 3.0, {**DEFAULTS, 'oversold_lev_on': 1}, NOW)['triggered'])

    def test_atr_from_daily_rows(self):
        rows = [{'bsop_date': f'202610{d:02d}', 'stck_hgpr': 102, 'stck_lwpr': 98, 'stck_prpr': 100} for d in range(1, 25)]
        self.assertEqual(oversold.atr_pct(rows), 4.0)
        self.assertIsNone(oversold.atr_pct(rows[:5]))

    def test_failed_daily_atr_retries_after_five_minutes_and_keeps_success_for_the_day(self):
        reader = Mock()
        reader.call_tool.side_effect = [TimeoutError, {'status': 'ok', 'data': {'rsp_cd': '00000', 'Output_1': []}},
                                        {'status': 'ok', 'data': {'rsp_cd': '00000', 'Output_1': [
                                            {'bsop_date': f'202610{d:02d}', 'stck_hgpr': 102, 'stck_lwpr': 98, 'stck_prpr': 100}
                                            for d in range(1, 25)]}}]
        with patch.dict(oversold._ATR, {}, clear=True):
            self.assertIsNone(oversold.daily_atr(reader, '005930', NOW))
            self.assertIsNone(oversold.daily_atr(reader, '005930', NOW + timedelta(minutes=4, seconds=59)))
            self.assertEqual(reader.call_tool.call_count, 1)
            self.assertIsNone(oversold.daily_atr(reader, '005930', NOW + timedelta(minutes=5)))
            self.assertEqual(reader.call_tool.call_count, 2)
            self.assertEqual(oversold.daily_atr(reader, '005930', NOW + timedelta(minutes=10)), 4.0)
            self.assertEqual(oversold.daily_atr(reader, '005930', NOW + timedelta(hours=2)), 4.0)
            self.assertEqual(reader.call_tool.call_count, 3)

    def test_universe_file_and_history(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(oversold.universe(d), oversold.DEFAULT_UNIVERSE)
            oversold.save_universe({'005930': '삼성전자', 'bad': 'x'}, d)
            self.assertEqual(oversold.universe(d), {'005930': '삼성전자'})
            with self.assertRaises(ValueError):
                oversold.save_universe({}, d)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.saved = dict(p.OVERSOLD)
        p.OVERSOLD.clear()
        p.OVERSOLD.update({k: v for k, v in DEFAULTS.items() if k.startswith('oversold_')})

    def tearDown(self):
        p.OVERSOLD.clear()
        p.OVERSOLD.update(self.saved)

    def test_trend_upgrade_keeps_a_rebound_plan(self):
        self.assertIn('oversold', p.OWN_EXIT_PROFILES)  # the strong-tape upgrade skips rebound plans

    def test_runner_records_unknown_atr_once_even_without_trigger_then_records_recovery(self):
        unknown = {'code': '005930', 'atr_status': 'unknown', 'triggered': False, 'need_pct': 6.0, 'drop_pct': -4.0}
        confirmed = {**unknown, 'atr_status': 'confirmed', 'need_pct': 8.0}
        with patch.object(p, '_OVERSOLD_LOGGED', set()), patch.object(oversold, 'universe', return_value={'005930': '삼성전자'}), \
                patch.object(oversold, 'scan', side_effect=[[unknown], [unknown], [confirmed]]), \
                patch.object(p, 'journal_event') as log:
            for _ in range(3):
                self.assertIsNone(p.oversold_entry(None, None, {}, {}, [], None, {'jev': True}, 'test'))
        self.assertEqual([c.args[1] for c in log.call_args_list], ['oversold_atr_status', 'oversold_atr_status'])
        self.assertEqual([c.kwargs['status'] for c in log.call_args_list], ['unknown', 'confirmed'])

    def test_plan_size_and_carry(self):
        plan = p.new_plan('005930', 'oversold', target=1.5, basis={'stop_pct': 0.94})
        self.assertEqual((plan['profile'], plan['all_pct'], plan['stop_pct'], plan['hold_days']), ('oversold', 1.5, 0.94, 1))
        self.assertEqual(p.size_factor('oversold'), 1.0)
        today = NOW.date()
        with patch.object(p, 'next_session_open', return_value=True):
            self.assertTrue(p.oversold_may_carry({**plan, 'opened': NOW.isoformat()}, today))       # day 1: may stay
            self.assertFalse(p.oversold_may_carry({**plan, 'opened': (NOW - timedelta(days=1)).isoformat()}, today))
            self.assertFalse(p.oversold_may_carry({**plan, 'hold_days': 0, 'opened': NOW.isoformat()}, today))

    def test_entry_needs_the_engines_buy(self):
        import research_store
        class Read:
            def call_tool(self, tool, args):
                if tool == 'get_stock_price':
                    return {'data': {'Output_0': SAMSUNG if args['stock_code'] == '005930' else {}}}
                return {'status': 'error'}
        calm = [{'stck_lwpr': 179600, 'stck_prpr': 180000}] * 30
        with tempfile.TemporaryDirectory() as d, patch.object(oversold, 'daily_atr', return_value=6.7), \
             patch.object(oversold, 'market_ok', return_value=True), \
             patch.object(oversold, 'analyst', return_value={}), patch.object(p, 'minute_bars', return_value={'status': 'ok'}), \
             patch.object(p, 'valid_bars', return_value=calm), patch.object(p, 'fresh_series', return_value=True), \
             patch.object(p, 'journal_event') as events, patch.object(p, 'market_view', return_value=None), \
             patch.object(research_store, 'ROOT', Path(d)):
            brief = {'as_of': NOW.isoformat(), 'sources': {'news': {'status': 'ok', 'observed_at': NOW.isoformat()}}}
            digest = research_store.record_input('jev', 'oversold', {'state': {'premarket_context': brief}}, {})
            buy = {'oversold': {'choice': 'BUY', 'confidence': 0.7}, 'input_digest': digest}
            with patch('nh_etf_auto_v2.jev_oversold7', return_value=buy):
                code, rows, basis = p.oversold_entry(Read(), d, {}, {}, [], None, {'jev': True}, 'c1')
            self.assertEqual((code, basis['target_pct']), ('005930', 1.5))
            self.assertIn('oversold_signal', [c.args[1] for c in events.call_args_list])
            signal = [c for c in events.call_args_list if c.args[1] == 'oversold_signal'][-1].kwargs
            self.assertEqual((signal['input_digest'], signal['premarket_audit']['status']), (digest, 'included_in_recorded_input'))
            self.assertEqual(signal['premarket_audit']['as_of'], NOW.isoformat())
            wait = {'oversold': {'choice': 'WAIT', 'confidence': 0.8}}
            p._OVERSOLD_ASKED.clear()
            with patch('nh_etf_auto_v2.jev_oversold7', return_value=wait):
                self.assertIsNone(p.oversold_entry(Read(), d, {}, {}, [], None, {'jev': True}, 'c2'))
            with patch('nh_etf_auto_v2.jev_oversold7', return_value=buy) as again:  # within 5 minutes, no further fall
                self.assertIsNone(p.oversold_entry(Read(), d, {}, {}, [], None, {'jev': True}, 'c2b'))
                again.assert_not_called()
            p._OVERSOLD_ASKED.clear()
            self.assertIsNone(p.oversold_entry(Read(), d, {'005930': 10}, {}, [], None, {'jev': True}, 'c3'))  # held

class FakeRead:
    def __init__(self, quotes):
        self.quotes = quotes

    def call_tool(self, name, args):
        code = args['stock_code']
        prev, low, price = self.quotes[code]
        return {'data': {'Output_0': {'iem_cd': code, 'stck_prdy_clpr': prev, 'stck_lwpr': low, 'stck_prpr': price}}}


class V2GateTests(unittest.TestCase):
    QUOTES = {'069500': (100.0, 91.0, 93.0), '229200': (100.0, 97.0, 98.0),  # KOSPI -9%, KOSDAQ -3%
              '005930': (100.0, 89.0, 92.0), '000660': (100.0, 82.0, 85.0), '196170': (100.0, 80.0, 85.0)}

    def setUp(self):
        oversold._V2_GATE.clear()

    def gate(self, nasdaq=-1.0, prior5=None, quotes=None):
        with patch('oversold.nasdaq_prev_night', return_value=nasdaq), patch('oversold.market_prior5', return_value=prior5):
            return oversold.v2_gate(FakeRead(quotes or self.QUOTES), NOW, DEFAULTS)

    def test_market_crash_passes_unless_nasdaq_rose(self):
        g = self.gate()
        self.assertEqual((g['kospi']['low_pct'], g['kospi']['pass'], g['kosdaq']['pass']), (-9.0, True, False))
        oversold._V2_GATE.clear()
        self.assertFalse(self.gate(nasdaq=0.4)['kospi']['pass'])
        oversold._V2_GATE.clear()
        self.assertFalse(self.gate(nasdaq=None)['kospi']['pass'])  # unknown night: no pass
        oversold._V2_GATE.clear()
        capit = self.gate(prior5=-6.0, quotes={**self.QUOTES, '069500': (100.0, 94.5, 95.0)})  # -5.5% after -6% in 5 days
        self.assertTrue(capit['kospi']['pass'])

    def test_bad_market_quote_and_a_failed_nasdaq_read(self):
        bad = {**self.QUOTES, '069500': (100.0, 0.0, 93.0)}  # a zero low is not a -100% crash
        self.assertFalse(self.gate(quotes=bad)['kospi']['pass'])
        oversold._NASDAQ.clear()
        with patch('oversold_research.fetch', return_value=[]) as fetch:
            self.assertIsNone(oversold.nasdaq_prev_night(NOW.date()))
            self.assertIsNone(oversold.nasdaq_prev_night(NOW.date()))
            self.assertEqual(fetch.call_count, 1)  # within the retry wait
        with patch.object(oversold.time, 'time', return_value=oversold.time.time() + 600), \
             patch('oversold_research.fetch', return_value=[(NOW - timedelta(days=2), 0, 0, 0, 100.0), (NOW - timedelta(days=1), 0, 0, 0, 97.0)]):
            self.assertEqual(oversold.nasdaq_prev_night(NOW.date()), -3.0)  # retried after the wait
        oversold._NASDAQ.clear()

    def test_sent_today_reads_the_runners_own_journal(self):
        import json as _json
        with tempfile.TemporaryDirectory() as d:
            lines = [{'time': '2026-11-16T14:35:01+09:00', 'event': 'submitted', 'symbol': '005930', 'source': 'oversold_v2'},
                     {'time': '2026-11-16T14:36:01+09:00', 'event': 'submitted', 'symbol': '000660', 'source': 'jev'},
                     {'time': '2026-11-13T14:35:01+09:00', 'event': 'submitted', 'symbol': '069500', 'source': 'oversold_v2'}]
            (Path(d) / 'events.jsonl').write_text('\n'.join(_json.dumps(x) for x in lines))
            self.assertEqual(oversold.sent_today(Path(d) / 'events.jsonl', '2026-11-16'), {('oversold_v2', '005930')})
            self.assertEqual(oversold.sent_today(Path(d) / 'missing.jsonl', '2026-11-16'), set())

    def test_candidates_fell_with_their_market(self):
        rows = oversold.v2_candidates(FakeRead(self.QUOTES), self.gate(), DEFAULTS, ['005930', '000660', '196170', '122630'])
        # 005930 -11% vs KOSPI -9%: in; 000660 -18%: 9%p worse, out; 196170 is KOSDAQ (no pass); 2x ETF off by default
        self.assertEqual([r['code'] for r in rows], ['005930'])
        r = rows[0]
        self.assertEqual((r['stop_price'], r['hold_days'], r['rel_drop'], r['half_tp_pct'], r['target_pct']), (90, 3, -2.0, 8.0, 15.0))  # price 92 x 0.98 (stop under the buy price, user 2026-11-17)
        self.assertEqual(oversold.v2_candidates(FakeRead(self.QUOTES), self.gate(), DEFAULTS, ['005930'], held={'005930'}), [])

    def test_candidates_only_expose_a_fresh_ask(self):
        gate = self.gate()
        read = Mock()
        price = FakeRead(self.QUOTES).call_tool('get_stock_price', {'stock_code': '005930'})['data']['Output_0']
        quote = {**price, 'askp': 93, 'market_status': '1', 'hoga_bsop_hour': '11:00:00'}
        read.call_tool.side_effect = [
            {'status': 'ok', 'data': {'rsp_cd': '00000', 'Output_0': quote}},
            {'status': 'ok', 'data': {'rsp_cd': '00000', 'Output_0': {**quote, 'hoga_bsop_hour': '10:55:00'}}},
        ]
        with patch.object(oversold, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = NOW
            fresh = oversold.v2_candidates(read, gate, DEFAULTS, ['005930'])
            stale = oversold.v2_candidates(read, gate, DEFAULTS, ['005930'])
        self.assertEqual(fresh[0]['ask'], 93)
        self.assertIsNone(stale[0]['ask'])
        self.assertEqual(read.call_tool.call_count, 2)

    def test_runner_skips_unaffordable_candidate_until_price_or_budget_allows_one_share(self):
        now = NOW.replace(hour=14, minute=35)
        gate = {'kospi': {'pass': True, 'low_pct': -9.0}, 'kosdaq': {'pass': False}, 'nasdaq_prev': -1.0}
        expensive = {'code': '000660', 'price': 1_090_000, 'ask': 1_091_000, 'drop_pct': -11.0, 'market': 'kospi',
                     'market_low_pct': -9.0, 'rel_drop': -2.0, 'stop_pct': 2.0, 'hold_days': 3}
        affordable = {**expensive, 'code': '005930', 'price': 178_000, 'ask': 178_100, 'drop_pct': -10.0}
        with (
            tempfile.TemporaryDirectory() as d,
            patch.object(p, 'datetime', wraps=datetime) as clock,
            patch.object(p, '_V2_BOUGHT', {}), patch.object(p, '_V2_LOGGED', set()), patch.object(p, '_V2_VERDICT', {}),
            patch.object(p, 'OVERSOLD', {**DEFAULTS, 'oversold_v2_add_on': True, 'oversold_v2_size_pct': 100,
                                        'oversold_v2_boost_pct': 100}),
            patch.object(p, 'SHARED_SINGLE_ORDER_KRW', 1_500_000), patch.object(p, 'SPLIT_TRANCHE_KRW', 1_000_000),
            patch.object(oversold, 'v2_gate', return_value=gate), patch.object(oversold, 'universe', return_value={}),
            patch.object(oversold, 'v2_candidates', return_value=[expensive, affordable]),
            patch.object(p, 'fresh_rows', return_value=[{}] * 6) as bars,
            patch.object(p, 'v2_research', return_value=('normal', 1.0, {'engine': None})),
            patch.object(p, 'journal_event') as events,
        ):
            clock.now.return_value = now
            self.assertEqual(p.oversold_v2_entry(None, d, {}, 'first')[0], '005930')
            expensive['ask'] = 1_094_000
            self.assertEqual(p.oversold_v2_entry(None, d, {}, 'still-over')[0], '005930')
            expensive['ask'] = None
            self.assertEqual(p.oversold_v2_entry(None, d, {}, 'unconfirmed')[0], '005930')
            self.assertEqual([c.args[1] for c in bars.call_args_list], ['005930'] * 3)

            expensive['ask'] = 500_000
            self.assertEqual(p.oversold_v2_entry(None, d, {}, 'lower-price')[0], '000660')
            expensive['ask'] = 1_094_000
            with patch.object(p, 'SPLIT_TRANCHE_KRW', 2_200_000):
                self.assertEqual(p.oversold_v2_entry(None, d, {}, 'higher-budget')[0], '000660')

            deferred = [c.kwargs for c in events.call_args_list if c.kwargs.get('reason') == 'single_order_limit']
            self.assertEqual([(e['symbol'], e['price'], e['buy_budget_krw']) for e in deferred],
                             [('000660', 1_091_000, 500_000)])

    def test_order_limit_deduplication_survives_same_day_restart(self):
        event = {'time': NOW.isoformat(), 'event': 'v2_execution_gate', 'reason': 'single_order_limit',
                 'symbol': '000660', 'source': 'oversold_v2'}
        with tempfile.TemporaryDirectory() as d, patch.object(p, '_V2_LOGGED', set()), patch.object(p, 'journal_event') as log:
            path = Path(d) / 'events.jsonl'
            path.write_text(json.dumps(event) + '\n')
            p.restore_single_order_limits(path, NOW.date())
            self.assertTrue(p.v2_unaffordable(d, NOW.date(), '000660', 'oversold_v2', 1_091_000, 500_000, 'retry'))
            log.assert_not_called()
            self.assertTrue(p.v2_unaffordable(d, NOW.date() + timedelta(days=1), '000660', 'oversold_v2',
                                             1_091_000, 500_000, 'new-day'))
            log.assert_called_once()

    def test_runner_window_research_veto_add_and_plan(self):
        saved = dict(p.OVERSOLD)
        p.OVERSOLD.update({k: v for k, v in DEFAULTS.items() if k.startswith('oversold_')})
        for cache in (p._V2_BOUGHT, p._V2_LOGGED, p._V2_VERDICT):
            cache.clear()
        row = {'code': '005930', 'price': 92.0, 'drop_pct': -11.0, 'market': 'kospi', 'market_low_pct': -9.0, 'rel_drop': -2.0,
               'stop_price': 78, 'stop_pct': 15.2, 'half_tp_pct': 8.0, 'target_pct': 15.0, 'hold_days': 3}
        gate = {'kospi': {'pass': True, 'low_pct': -9.0}, 'kosdaq': {'pass': False}, 'nasdaq_prev': -1.0}
        class Clock(datetime):
            at = NOW.replace(hour=14, minute=20)
            @classmethod
            def now(cls, tz=None):
                return cls.at
        try:
            with tempfile.TemporaryDirectory() as d, patch.object(oversold, 'v2_gate', return_value=gate), \
                 patch.object(oversold, 'v2_candidates', side_effect=lambda read, g, st, codes, held=(): [r for r in [row] if r['code'] not in held]), \
                 patch.object(p, 'fresh_rows', return_value=[{}] * 6), patch.object(p, 'journal_event') as events, \
                 patch.object(p.oversold_cases, 'brief', return_value={}), patch.object(oversold, 'analyst', return_value={}), \
                 patch.object(p, 'market_view', return_value=None), patch.object(p, 'datetime', Clock):
                self.assertIsNone(p.oversold_v2_entry(None, d, {}, 'c0'))  # 14:20: before the window
                Clock.at = NOW.replace(hour=14, minute=35)
                with patch('nh_etf_auto_v2.jev_oversold7', side_effect=RuntimeError('failed')):
                    code, _, basis, source = p.oversold_v2_entry(None, d, {}, 'error', modes={'jev': True})
                self.assertEqual((code, source, basis['research']), ('005930', 'oversold_v2', 'normal'))
                failure = [c for c in events.call_args_list if c.args[1] == 'oversold_v2_research_failure'][-1]
                self.assertEqual((failure.kwargs['fallback_reason'], failure.kwargs['research_error']), ('research_error', 'RuntimeError'))
                self.assertEqual(failure.kwargs['premarket_audit']['status'], 'input_record_unavailable')
                p._V2_LOGGED.clear()
                def fail_audit(root, event, **fields):
                    if event == 'oversold_v2_research_failure':
                        raise OSError('audit unavailable')
                with patch('nh_etf_auto_v2.jev_oversold7', side_effect=RuntimeError('failed')), \
                     patch.object(p, 'journal_event', side_effect=fail_audit):
                    self.assertEqual(p.oversold_v2_entry(None, d, {}, 'error2', modes={'jev': True})[2]['research'], 'normal')
                p._V2_LOGGED.clear()
                wait = {'oversold': {'choice': 'WAIT', 'confidence': 0.9}}
                with patch('nh_etf_auto_v2.jev_oversold7', return_value=wait):
                    self.assertIsNone(p.oversold_v2_entry(None, d, {}, 'c1', modes={'jev': True}))  # research veto
                self.assertEqual([c.kwargs.get('research') for c in events.call_args_list if c.args[1] == 'oversold_v2_signal'], ['veto'])
                for cache in (p._V2_LOGGED, p._V2_VERDICT):
                    cache.clear()
                buy = {'oversold': {'choice': 'BUY', 'confidence': 0.85}}
                p.OVERSOLD['oversold_v2_boost_pct'] = 150
                with patch('nh_etf_auto_v2.jev_oversold7', return_value=buy) as asked:
                    code, rows, basis, source = p.oversold_v2_entry(None, d, {}, 'c2', modes={'jev': True})
                    p.oversold_v2_entry(None, d, {}, 'c3', modes={'jev': True})
                    self.assertEqual(asked.call_count, 1)  # one research answer a day per code
                self.assertEqual((code, source, basis['research'], basis['size_mult'], basis['entry_price']), ('005930', 'oversold_v2', 'boost', 0.75, 92.0))
                p.save_exit_plan(d, '005930', 'c2', 'oversold', target=basis['target_pct'], basis=basis)
                plan = p.exit_plans(d)['005930']
                self.assertEqual((plan['half_pct'], plan['all_pct'], plan['hold_days']), (8.0, 15.0, 3))  # half at +8%, the rest at +15%
                # the next session it trades lower than the first buy and above the stop: the second half
                Clock.at = NOW.replace(hour=14, minute=35) + timedelta(days=1)
                read = FakeRead({'005930': (90.0, 85.0, 86.0)})
                with patch.object(p, 'sessions_held', return_value=1):
                    code, rows, add, source = p.oversold_v2_entry(read, d, {'005930': 10}, 'c4')
                self.assertEqual((source, add['add']), ('oversold_v2_add', True))
                p.update_v2_plan(d, '005930', 86)
                plan = p.exit_plans(d)['005930']
                self.assertTrue(plan['target_basis']['added'])
                self.assertEqual((plan['stop_pct'], plan['target_basis']['stop_price']), (2.0, 87))  # 2% under the new average 89
                with patch.object(p, 'sessions_held', return_value=1):
                    self.assertIsNone(p.oversold_v2_entry(read, d, {'005930': 10}, 'c5'))  # added once only
                self.assertEqual(p.size_factor('oversold_v2_add'), 1.0)
        finally:
            p.OVERSOLD.clear()
            p.OVERSOLD.update(saved)


if __name__ == '__main__':
    unittest.main()
