import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import nh_paper_isolated as p
import vibe_runner as v

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=ZoneInfo('Asia/Seoul'))
ACCOUNT = '50001000002'


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


class Broker:
    def __init__(self, sellable=100): self.sellable = sellable
    def call_tool(self, tool, params, **kwargs):
        code = params['input']['iem_cd']
        return {'status': 'ok', 'data': {'rsp_cd': 'XA102', 'Output_0': {'iem_cd': code, 'sll_pbl_qty': self.sellable}}}


class VibeRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.db = p.connect_journal(self.root / 'orders.sqlite3', p.account_hash(ACCOUNT))
        from unittest import mock  # the user's saved dashboard limits must not leak into these tests: use the defaults
        add_window = mock.patch.object(p, 'add_window_open', return_value=True)  # wall clock; the add cutoff has its own test
        add_window.start(); self.addCleanup(add_window.stop)
        ipo = mock.patch.object(v, 'ipo_buy', return_value=None)  # the new-listing slot reads KIND; it has its own test
        ipo.start(); self.addCleanup(ipo.stop)
        self.limits = mock.patch.object(v, 'limits', return_value={'order': v.ORDER_KRW, 'daily_buy': v.DAILY_BUY_KRW,
                                                                    'new_symbols': v.DAILY_NEW_SYMBOLS, 'loss': v.DAILY_LOSS_KRW,
                                                                    'symbol': v.SYMBOL_KRW, 'tranche': v.TRANCHE_KRW})
        self.limits.start()

    def tearDown(self):
        self.limits.stop()
        self.db.close(); self.temp.cleanup()

    def advise(self, decisions, at=NOW, universe=('005930',)):
        codes = set(universe) | {row['symbol'] for row in decisions if isinstance(row, dict) and isinstance(row.get('symbol'), str)}
        (self.root / 'vibe-advice.json').write_text(json.dumps(
            {'id': 'adv1', 'time': at.isoformat(), 'decisions': decisions, 'universe': list(universe),
             'technical_basic': {code: {'as_of': (at - timedelta(minutes=2)).strftime('%H:%M'), 'vote': 1} for code in codes}}))

    def test_vibe_carry_requires_confirmed_profit_current_model_and_budget(self):
        from strategy_settings import DEFAULTS
        view = {'코스피': {'regime': '상승장', 'today_tone': '강세', 'advancers_decliners': [600, 200],
                        'investor_net_100m_krw': {'외국인': 10, '기관': 10},
                        'program_net_100m_krw': {'전체': 10}}}
        stats = {'volume_ratio': 2.0, 'today': {'high': 1040, 'low': 990, 'open': 1000},
                 'ma5': 1000, 'ma20': 990}
        advice = {'grounded': True, 'confirmed_by_previous_run': True, 'signals': {'005930': 0.4}, 'decisions': []}
        kwargs = dict(view=view, index='코스피', short=False, bid=1035, cost=5000,
                      budget_left=10000, stats=stats, advice=advice, model_ok=True, first_target_filled=True,
                      carried=False, opened={'opened': NOW.isoformat(), 'symbol': '005930'}, now=NOW,
                      settings={**DEFAULTS, 'swing_max_days': 5})
        with patch.object(p, 'next_session_open', return_value=True):
            self.assertIsNone(v.carry_reason(**kwargs))
            self.assertEqual(v.carry_reason(**{**kwargs, 'first_target_filled': False}), 'not_first_target')
            self.assertEqual(v.carry_reason(**{**kwargs, 'model_ok': False}), 'vibe_not_supportive')
            conflict = {**advice, 'decisions': [{'symbol': '005930', 'action': 'SELL'}]}
            self.assertEqual(v.carry_reason(**{**kwargs, 'advice': conflict}), 'vibe_not_supportive')
            self.assertEqual(v.carry_reason(**{**kwargs, 'budget_left': 4000}), 'overnight_limit')
            self.assertEqual(v.carry_reason(**{**kwargs, 'stats': None}), 'carry_evidence_unavailable')
        with patch.object(p, 'next_session_open', return_value=False):
            self.assertEqual(v.carry_reason(**kwargs), 'market_closed_next_day')
        with patch.object(p, 'next_session_open', return_value=True), patch.object(p, 'sessions_held', return_value=2):
            earlier = {**kwargs['opened'], 'opened': (NOW - timedelta(days=3)).isoformat()}
            self.assertIsNone(v.carry_reason(**{**kwargs, 'carried': True, 'opened': earlier}))
            self.assertEqual(v.carry_reason(**{**kwargs, 'carried': True, 'opened': earlier,
                                               'settings': {**DEFAULTS, 'swing_max_days': 2}}), 'swing_days_exceeded')

    def test_carry_state_is_bound_to_generation(self):
        original = {'symbol': '005930', 'position_id': 'a' * 20, 'opened': NOW.isoformat()}
        v.save_carry(self.root, '005930', original, NOW)
        v.save_carry(self.root, '005930', original, NOW + timedelta(days=1))
        self.assertEqual(v.carry_state(self.root)['005930']['first_approved_on'], NOW.date().isoformat())
        other = {**original, 'position_id': 'b' * 20}
        v.save_carry(self.root, '005930', other, NOW + timedelta(days=1))
        self.assertEqual(v.carry_state(self.root)['005930']['first_approved_on'], (NOW + timedelta(days=1)).date().isoformat())

    def test_carry_requires_ordinary_entry_provenance_even_without_oversold_file(self):
        self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('b', '005930', 'BUY', 10, 1000, NOW.isoformat()))
        self.db.execute('INSERT INTO decision_order_links VALUES (?,?)', ('b', 'cycle'))
        self.db.commit()
        event = {'time': NOW.isoformat(), 'event': 'submitted', 'symbol': '005930', 'source': 'vibe-trading', 'cycle_id': 'cycle'}
        (self.root / 'events.jsonl').write_text(json.dumps(event) + '\n')
        position = v.carry_position(self.db, self.root, '005930', 10)
        self.assertEqual(position['opened'], NOW.isoformat())
        event['source'] = 'oversold_v2'
        (self.root / 'events.jsonl').write_text(json.dumps(event) + '\n')
        with self.assertRaisesRegex(ValueError, 'carry_position_unavailable'):
            v.carry_position(self.db, self.root, '005930', 10)

    def test_flatten_carries_qualified_vibe_position_without_selling(self):
        import sys
        from strategy_settings import DEFAULTS
        here = sys.modules[__name__]
        late = NOW.replace(hour=14, minute=41)
        settings = {**DEFAULTS, 'overnight_max_manwon': 100, 'swing_max_days': 5}
        view = {'코스피': {'regime': '상승장', 'today_tone': '강세', 'advancers_decliners': [600, 200],
                        'investor_net_100m_krw': {'외국인': 10, '기관': 10}, 'program_net_100m_krw': {'전체': 10}}}
        stats = {'volume_ratio': 2, 'today': {'high': 1020, 'low': 980, 'open': 1000}, 'ma5': 1005, 'ma20': 1000}
        advice = {'grounded': True, 'confirmed_by_previous_run': True, 'signals': {'005930': 0.4}, 'decisions': []}
        position = {'position_id': 'a' * 20, 'opened': late.isoformat()}
        with patch.object(here, 'NOW', late), patch.object(v, 'scale_settings', return_value=settings), \
             patch('strategy_settings.read_settings', return_value=(settings, 'ok')), \
             patch.object(v, 'market_view', return_value=view), patch.object(v, 'stop_symbol_index', return_value='코스피'), \
             patch.object(v, 'carry_position', return_value=position), patch.object(v, 'load_advice', return_value=advice), \
             patch.object(v, 'model_position_check', return_value=True), patch.object(p, 'verified_take_profit_sold', return_value=5), \
             patch.object(p, 'take_profit_progress', return_value=True), patch.object(p, 'daily_stats', return_value=stats), \
             patch.object(p, 'next_session_open', return_value=True):
            status, submit, events = self.run_cycle({'005930': 10}, bid=1015)
        self.assertEqual(status, 'flattened')
        submit.assert_not_called()
        event = next(c for c in events.call_args_list if c.args[1] == 'overnight_hold')
        self.assertTrue(event.kwargs['permitted'])
        self.assertEqual(v.carry_state(self.root)['005930']['position_id'], 'a' * 20)

    def test_flatten_carry_uses_confirmed_fills_and_same_position_advice(self):
        from strategy_settings import DEFAULTS
        late = NOW.replace(hour=14, minute=41)
        self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('buy', '005930', 'BUY', 20, 1000, (late - timedelta(minutes=20)).isoformat()))
        self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('sell', '005930', 'SELL', 10, 1010, (late - timedelta(minutes=10)).isoformat()))
        self.db.execute('INSERT INTO decision_order_links VALUES (?,?)', ('buy', 'entry-cycle'))
        self.db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?,?,?,?)', ('sell', '20261112', '005930', 'SELL', 10, 1010, 'terminal', 1, 0))
        self.db.execute('INSERT INTO sell_order_triggers VALUES (?,?)', ('sell', 'take_profit'))
        self.db.commit()
        (self.root / 'events.jsonl').write_text(json.dumps({'event': 'submitted', 'symbol': '005930',
                                                            'source': 'vibe-trading', 'cycle_id': 'entry-cycle'}) + '\n')
        advice = {'id': 'current', 'time': late.isoformat(), 'grounded': True, 'confirmed_by_previous_run': True,
                  'account_holdings': {'005930': 10}, 'account_fill_ids': {'005930': 'sell'},
                  'signals': {'005930': 0.4}, 'decisions': []}
        (self.root / 'vibe-advice.json').write_text(json.dumps(advice))
        settings = {**DEFAULTS, 'overnight_max_manwon': 100, 'swing_max_days': 5}
        view = {'코스피': {'regime': '상승장', 'today_tone': '강세', 'advancers_decliners': [600, 200],
                        'investor_net_100m_krw': {'외국인': 10, '기관': 10}, 'program_net_100m_krw': {'전체': 10}}}
        stats = {'volume_ratio': 2, 'today': {'high': 1020, 'low': 980, 'open': 1000}, 'ma5': 1005, 'ma20': 1000}
        def pages(_broker, operation, _fields, _block):
            return {'Output_0': []} if operation == 'krstockInquiryDailyOrderExecution' else {
                'Output_0': {'orr_pbl_amt1': 10000}, 'Output_1': [{'iem_cd': '005930', 'rsdl_qty': 10, 'phs_pr': 1000, 'eal_pls_amt': 150}]}
        class LateClock(datetime):
            @classmethod
            def now(cls, tz=None): return late
        with patch.object(v, 'datetime', LateClock), patch.object(v, 'paper_pages', side_effect=pages), \
             patch.object(v, 'protective_quote', return_value={'askp': 1015, 'bidp': 1015}), \
             patch.object(v, 'ensure_trading_open'), \
             patch.object(v, 'etf7_protective_deadline', return_value=2**52), patch.object(v, 'scale_settings', return_value=settings), \
             patch('strategy_settings.read_settings', return_value=(settings, 'ok')), \
             patch.object(v, 'market_view', return_value=view), patch.object(v, 'stop_symbol_index', return_value='코스피'), \
             patch.object(p, 'daily_stats', return_value=stats), patch.object(p, 'next_session_open', return_value=True), \
             patch.object(v, 'journal_event') as events, patch.object(v, 'submit', return_value=('a' * 32, 1)) as submit:
            self.assertEqual(v.cycle(self.db, object(), Broker(), ACCOUNT, self.root, late), 'flattened')
            submit.assert_not_called()
            self.assertTrue(next(c for c in events.call_args_list if c.args[1] == 'overnight_hold').kwargs['permitted'])
            advice['account_fill_ids']['005930'] = 'old-generation'
            (self.root / 'vibe-advice.json').write_text(json.dumps(advice))
            self.assertEqual(v.cycle(self.db, object(), Broker(), ACCOUNT, self.root, late), 'sell')
            self.assertEqual(submit.call_args.args[4:7], ('005930', 'SELL', 10))
            self.assertEqual([c.kwargs['reason'] for c in events.call_args_list if c.args[1] == 'overnight_hold'][-1],
                             'vibe_not_supportive')

    def test_changed_dashboard_limits_during_carry_research_force_flatten(self):
        import sys
        from strategy_settings import DEFAULTS
        here = sys.modules[__name__]
        late = NOW.replace(hour=14, minute=41)
        settings = {**DEFAULTS, 'overnight_max_manwon': 100}
        changed = {**settings, 'overnight_max_manwon': 0}
        advice = {'grounded': True, 'confirmed_by_previous_run': True, 'signals': {'005930': 0.4}, 'decisions': []}
        position = {'position_id': 'a' * 20, 'opened': late.isoformat()}
        view = {'코스피': {'regime': '상승장', 'today_tone': '강세'}}
        with patch.object(here, 'NOW', late), patch.object(v, 'scale_settings', return_value=settings), \
             patch('strategy_settings.read_settings', return_value=(changed, 'ok')), \
             patch.object(v, 'market_view', return_value=view), patch.object(v, 'carry_position', return_value=position), \
             patch.object(v, 'load_advice', return_value=advice), patch.object(v, 'model_position_check', return_value=True), \
             patch.object(p, 'verified_take_profit_sold', return_value=5), patch.object(p, 'take_profit_progress', return_value=True), \
             patch.object(p, 'daily_stats', return_value={'today': {}}):
            status, submit, events = self.run_cycle({'005930': 10}, bid=1015)
        self.assertEqual(status, 'sell')
        self.assertEqual(submit.call_args.args[4:7], ('005930', 'SELL', 10))
        event = next(c for c in events.call_args_list if c.args[1] == 'overnight_hold')
        self.assertEqual((event.kwargs['permitted'], event.kwargs['reason']), (False, 'carry_evidence_changed'))
        self.assertFalse((self.root / v.VIBE_CARRY_FILE).exists())

    def test_flatten_research_never_precedes_other_holding_stop(self):
        late = NOW.replace(hour=14, minute=41)
        for code in ('005930', '114800'):
            self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', (code, code, 'BUY', 10, 1000, late.isoformat()))
        self.db.commit()
        def pages(_broker, operation, _fields, _block):
            if operation == 'krstockInquiryDailyOrderExecution':
                return {'Output_0': []}
            return {'Output_0': {'orr_pbl_amt1': 10000}, 'Output_1': [
                {'iem_cd': code, 'rsdl_qty': 10, 'phs_pr': 1000} for code in ('005930', '114800')]}
        def quotes(_read, code):
            return {'askp': 1000, 'bidp': 1000 if code == '005930' else 979}
        class LateClock(datetime):
            @classmethod
            def now(cls, tz=None): return late
        with patch.object(v, 'datetime', LateClock), patch.object(v, 'paper_pages', side_effect=pages), \
             patch.object(v, 'protective_quote', side_effect=quotes), patch.object(v, 'ensure_trading_open'), \
             patch.object(v, 'etf7_protective_deadline', return_value=2**52), patch.object(v, 'journal_event'), \
             patch.object(p, 'daily_stats') as research, patch.object(v, 'submit', return_value=('a' * 32, 1)) as submit:
            status = v.cycle(self.db, object(), Broker(), ACCOUNT, self.root, late)
        self.assertEqual(status, 'sell')
        self.assertEqual(submit.call_args.args[4:7], ('114800', 'SELL', 10))
        research.assert_not_called()

    def run_carry_batch(self, at, quantities, settings, positions, *, rebounds=None, pnl=0, model_ok=True, held_days=0):
        from contextlib import ExitStack
        for code, qty in quantities.items():
            if not self.db.execute('SELECT 1 FROM fills WHERE symbol=?', (code,)).fetchone():
                self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', (code, code, 'BUY', qty, 1000, at.isoformat()))
        self.db.commit()
        view = {'코스피': {'regime': '상승장', 'today_tone': '강세', 'advancers_decliners': [600, 200],
                        'investor_net_100m_krw': {'외국인': 10, '기관': 10}, 'program_net_100m_krw': {'전체': 10}}}
        stats = {'volume_ratio': 2, 'today': {'high': 1020, 'low': 980, 'open': 1000}, 'ma5': 1005, 'ma20': 1000}
        advice = {'grounded': True, 'confirmed_by_previous_run': True,
                  'signals': {code: 0.4 for code in quantities}, 'decisions': []}
        def pages(_broker, operation, _fields, _block):
            return {'Output_0': []} if operation == 'krstockInquiryDailyOrderExecution' else {
                'Output_0': {'orr_pbl_amt1': 10000}, 'Output_1': [
                    {'iem_cd': code, 'rsdl_qty': qty, 'phs_pr': 1000, 'eal_pls_amt': pnl}
                    for code, qty in quantities.items()]}
        class TestClock(datetime):
            @classmethod
            def now(cls, tz=None): return at
        with ExitStack() as stack:
            def use(obj, name, **kw): return stack.enter_context(patch.object(obj, name, **kw))
            use(v, 'datetime', new=TestClock)
            use(v, 'paper_pages', side_effect=pages)
            use(v, 'protective_quote', return_value={'bidp': 1015, 'askp': 1015})
            use(v, 'etf7_protective_deadline', return_value=2**52)
            use(v, 'ensure_trading_open')
            use(v, 'scale_settings', return_value=settings)
            stack.enter_context(patch('strategy_settings.read_settings', return_value=(settings, 'ok')))
            use(v, 'market_view', return_value=view)
            use(v, 'stop_symbol_index', return_value='코스피')
            use(v, 'carry_position', side_effect=lambda _db, _root, code, _qty: dict(positions[code]))
            use(v, 'load_advice', return_value=advice)
            use(v, 'model_position_check', return_value=model_ok)
            use(v, 'oversold_plans', return_value=rebounds or {})
            use(v, 'may_carry', return_value=True)
            use(p, 'verified_take_profit_sold', return_value=5)
            use(p, 'take_profit_progress', return_value=True)
            use(p, 'daily_stats', return_value=stats)
            use(p, 'next_session_open', return_value=True)
            use(p, 'sessions_held', return_value=held_days)
            events = use(v, 'journal_event')
            submit = use(v, 'submit', return_value=('a' * 32, 1))
            status = v.cycle(self.db, object(), Broker(), ACCOUNT, self.root, at)
            return status, submit.call_args_list, events.call_args_list

    def test_multiple_general_positions_share_overnight_budget(self):
        from strategy_settings import DEFAULTS
        late = NOW.replace(hour=14, minute=41)
        positions = {code: {'position_id': code, 'opened': late.isoformat()} for code in ('005930', '035420')}
        status, orders, events = self.run_carry_batch(late, {'005930': 10, '035420': 10},
                                                       {**DEFAULTS, 'overnight_max_manwon': 1}, positions)
        self.assertEqual(status, 'sell')
        self.assertEqual(orders[0].args[4:7], ('035420', 'SELL', 10))
        decisions = [c.kwargs for c in events if c.args[1] == 'overnight_hold']
        self.assertEqual([(d['symbol'], d['permitted']) for d in decisions], [('005930', True), ('035420', False)])
        self.assertEqual(decisions[1]['reason'], 'overnight_limit')

    def test_oversold_carry_consumes_budget_without_changing_its_exception(self):
        from strategy_settings import DEFAULTS
        late = NOW.replace(hour=14, minute=41)
        positions = {'035420': {'position_id': 'normal', 'opened': late.isoformat()}}
        rebound = {'005930': {'stop_price': 900, 'target_price': 1200}}
        status, orders, events = self.run_carry_batch(late, {'005930': 10, '035420': 10},
                                                       {**DEFAULTS, 'overnight_max_manwon': 1}, positions, rebounds=rebound)
        self.assertEqual(status, 'sell')
        self.assertEqual(orders[0].args[4:7], ('035420', 'SELL', 10))
        self.assertEqual(next(c.kwargs['reason'] for c in events if c.args[1] == 'overnight_hold'), 'overnight_limit')

    def test_carry_survives_restart_but_obeys_next_day_maximum(self):
        from strategy_settings import DEFAULTS
        first = NOW.replace(hour=14, minute=41)
        later = first + timedelta(days=1)
        position = {'position_id': 'generation', 'opened': first.isoformat()}
        settings = {**DEFAULTS, 'overnight_max_manwon': 100, 'swing_max_days': 1}
        first_status, first_orders, _ = self.run_carry_batch(first, {'005930': 10}, settings, {'005930': position})
        self.assertEqual((first_status, first_orders), ('flattened', []))
        # The second cycle reads the durable state and original fill ledger anew.
        self.db.close()
        self.db = p.connect_journal(self.root / 'orders.sqlite3', p.account_hash(ACCOUNT))
        status, orders, events = self.run_carry_batch(later, {'005930': 10}, settings, {'005930': position}, held_days=1)
        self.assertEqual(status, 'sell')
        self.assertEqual(orders[0].args[4:7], ('005930', 'SELL', 10))
        self.assertEqual(next(c.kwargs['reason'] for c in events if c.args[1] == 'overnight_hold'), 'swing_days_exceeded')

    def test_carry_missing_model_or_daily_loss_forces_flatten(self):
        from strategy_settings import DEFAULTS
        late = NOW.replace(hour=14, minute=41)
        position = {'position_id': 'generation', 'opened': late.isoformat()}
        settings = {**DEFAULTS, 'overnight_max_manwon': 100}
        status, orders, events = self.run_carry_batch(late, {'005930': 10}, settings, {'005930': position}, model_ok=False)
        self.assertEqual(status, 'sell')
        self.assertEqual(next(c.kwargs['reason'] for c in events if c.args[1] == 'overnight_hold'), 'vibe_not_supportive')
        status, orders, events = self.run_carry_batch(late, {'005930': 10}, settings, {'005930': position}, pnl=-300001)
        self.assertEqual(status, 'sell')
        self.assertEqual(next(c.kwargs['reason'] for c in events if c.args[1] == 'overnight_hold'), 'daily_loss_halt')
        with patch.object(v, 'realized_today', return_value=float('nan')):
            status, orders, events = self.run_carry_batch(late, {'005930': 10}, settings, {'005930': position})
        self.assertEqual(status, 'sell')
        self.assertEqual(next(c.kwargs['reason'] for c in events if c.args[1] == 'overnight_hold'), 'daily_loss_halt')

    def test_stale_generation_or_corrupt_carry_state_cannot_authorize_carry(self):
        from strategy_settings import DEFAULTS
        late = NOW.replace(hour=14, minute=41)
        old = {'position_id': 'old', 'opened': (late - timedelta(days=2)).isoformat()}
        v.save_carry(self.root, '005930', old, late - timedelta(days=1))
        changed = {'position_id': 'new', 'opened': (late - timedelta(days=1)).isoformat()}
        settings = {**DEFAULTS, 'overnight_max_manwon': 100}
        status, orders, events = self.run_carry_batch(late, {'005930': 10}, settings, {'005930': changed})
        self.assertEqual(status, 'sell')
        self.assertEqual(next(c.kwargs['reason'] for c in events if c.args[1] == 'overnight_hold'), 'carry_history_unavailable')
        state_file = self.root / v.VIBE_CARRY_FILE
        state_file.write_text('{broken')
        status, orders, events = self.run_carry_batch(late, {'005930': 10}, settings,
                                                       {'005930': {'position_id': 'new', 'opened': late.isoformat()}})
        self.assertEqual(status, 'sell')
        self.assertEqual(next(c.kwargs['reason'] for c in events if c.args[1] == 'overnight_hold'), 'carry_state_invalid')
        self.assertEqual(state_file.read_text(), '{broken')

    def test_pending_order_reconciliation_precedes_carry_review(self):
        late = NOW.replace(hour=14, minute=41)
        self.db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?,?,?,?)',
                        ('pending', '20261112', '005930', 'BUY', 10, 1000, 'acknowledged', 1, 0))
        self.db.commit()
        with patch.object(v, 'reconcile', return_value=False) as reconcile, patch.object(v, 'paper_pages') as pages, \
             patch.object(p, 'daily_stats') as research, patch.object(v, 'submit') as submit:
            self.assertEqual(v.cycle(self.db, object(), Broker(), ACCOUNT, self.root, late), 'pending')
        reconcile.assert_called_once()
        pages.assert_not_called()
        research.assert_not_called()
        submit.assert_not_called()
        self.assertFalse((self.root / v.VIBE_CARRY_FILE).exists())

    def test_manual_partial_sale_is_not_counted_as_profit_target_fill(self):
        self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('buy', '005930', 'BUY', 10, 1000, NOW.isoformat()))
        self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('manual', '005930', 'SELL', 1, 1005, NOW.isoformat()))
        self.db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?,?,?,?)', ('manual', '20261112', '005930', 'SELL', 1, 1005, 'terminal', 1, 0))
        self.db.execute('INSERT INTO sell_order_triggers VALUES (?,?)', ('manual', 'manual'))
        self.db.commit()
        def pages(_broker, operation, _fields, _block):
            return {'Output_0': []} if operation == 'krstockInquiryDailyOrderExecution' else {
                'Output_0': {'orr_pbl_amt1': 10000}, 'Output_1': [{'iem_cd': '005930', 'rsdl_qty': 9, 'phs_pr': 1000, 'eal_pls_amt': 100}]}
        with patch.object(v, 'datetime', Clock), patch.object(v, 'paper_pages', side_effect=pages), \
             patch.object(v, 'protective_quote', return_value={'askp': 1015, 'bidp': 1015}), \
             patch.object(v, 'ensure_trading_open'), patch.object(v, 'etf7_protective_deadline', return_value=2**52), \
             patch.object(v, 'journal_event'), patch.object(v, 'submit', return_value=('a' * 32, 1)) as submit:
            self.assertEqual(v.cycle(self.db, object(), Broker(), ACCOUNT, self.root, NOW), 'sell')
        self.assertEqual(submit.call_args.args[4:7], ('005930', 'SELL', 5))

    def run_cycle(self, holdings=None, cash=10_000_000, bid=1000, ask=1000, pnl=0, history=None):
        holdings = holdings or {}
        def pages(b, operation, fields, block):
            if operation == 'krstockInquiryDailyOrderExecution':
                return {'Output_0': []}
            return {'Output_0': {'orr_pbl_amt1': cash}, 'Output_1': [
                {'iem_cd': c, 'rsdl_qty': q, 'phs_pr': 1000, 'eal_pls_amt': pnl} for c, q in holdings.items()]}
        for order, code, side, qty in history or [(code + str(qty), code, 'BUY', qty) for code, qty in holdings.items()]:
            self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', (order, code, side, qty, 1000, NOW.isoformat()))
        self.db.commit()
        with patch.object(v, 'datetime', Clock), patch.object(v, 'paper_pages', side_effect=pages), \
             patch.object(v, 'quote_of', return_value={'askp': ask, 'bidp': bid}), \
             patch.object(v, 'etf7_protective_deadline', return_value=2**52), patch.object(v, 'ensure_trading_open'), \
             patch.object(v, 'journal_event') as events, patch.object(v, 'submit', return_value=('a' * 32, 1)) as submit:
            status = v.cycle(self.db, object(), Broker(), ACCOUNT, self.root, NOW)
        return status, submit, events

    def test_stop_loss_runs_before_advice(self):
        self.advise([{'action': 'BUY', 'symbol': '005930', 'quantity': 5, 'confidence': 0.9}])
        status, submit, _ = self.run_cycle({'114800': 10}, bid=979)
        self.assertEqual((status, submit.call_args.args[4:7]), ('sell', ('114800', 'SELL', 10)))
        self.assertEqual(submit.call_args.kwargs['sell_trigger'], 'risk')

    def test_failed_held_quote_records_only_safe_error_type_and_waits(self):
        self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('buy', '114800', 'BUY', 10, 1000, NOW.isoformat()))
        self.db.commit()
        def pages(broker, operation, fields, block):
            return {'Output_0': []} if operation == 'krstockInquiryDailyOrderExecution' else {
                'Output_0': {'orr_pbl_amt1': 10000},
                'Output_1': [{'iem_cd': '114800', 'rsdl_qty': 10, 'phs_pr': 1000}]}
        class Reader:
            def __init__(self, kind): self.kind = kind
            def call_tool(self, *args):
                return {'status': 'error', 'error_type': self.kind, 'error': 'private response'}
        with patch.object(v, 'datetime', Clock), patch.object(v, 'paper_pages', side_effect=pages), \
             patch.object(v, 'journal_event') as events, patch.object(v, 'submit') as submit, patch.object(v.time, 'sleep'):
            for raw, expected in (('TimeoutError', 'TimeoutError'), ('private response', 'unknown'), (None, 'unknown'), ([], 'unknown')):
                with self.subTest(raw=raw):
                    self.assertEqual(v.cycle(self.db, Reader(raw), Broker(), ACCOUNT, self.root, NOW), 'protective_wait')
                    event = next(call for call in reversed(events.call_args_list) if call.args[1] == 'v2_risk_exit_error')
                    self.assertEqual(event.kwargs['quote_error_type'], expected)
                    self.assertNotIn('private response', str(event))
                    submit.assert_not_called()

    def test_protective_quote_retries_only_fresh_independent_read(self):
        response = {'status': 'ok', 'data': {'rsp_cd': '00000', 'Output_0': {
            'iem_cd': '114800', 'market_status': '1', 'hoga_bsop_hour': '10:00:00', 'bidp': 980}}}
        reader = Mock()
        reader.call_tool.side_effect = [{'status': 'error', 'error_type': 'TimeoutError'}, response]
        with patch.object(v, 'datetime', Clock), patch.object(v.time, 'sleep') as sleep:
            self.assertEqual(v.protective_quote(reader, '114800')['bidp'], 980)
        self.assertEqual(reader.call_tool.call_count, 2)
        sleep.assert_called_once_with(1)
        response['data']['Output_0']['iem_cd'] = '005930'
        reader.call_tool.side_effect = [{'status': 'error'}, response]
        with patch.object(v, 'datetime', Clock), patch.object(v.time, 'sleep'):
            with self.assertRaisesRegex(ValueError, 'quote symbol or session invalid'):
                v.protective_quote(reader, '114800')

    def test_quote_snapshot_is_atomic_and_preserves_provenance(self):
        quote = {'iem_cd': '114800', 'market_status': '1', 'hoga_bsop_hour': '10:00:00',
                 'bidp': 980, 'bidp1': 980, 'bidp_rsqn1': 50, 'askp': 985, 'private': 'not recorded'}
        with patch.object(v, '_QUOTE_SNAPSHOTS', None):
            v.start_quote_snapshots()
            v.record_protective_quote(self.root, '114800', quote, NOW)
            v._QUOTE_SNAPSHOTS.join()
            result = json.loads((self.root / 'fast-exit-quotes/114800.json').read_text())
            self.assertEqual((result['symbol'], result['observed_at']), ('114800', NOW.isoformat()))
            self.assertEqual(result['quote']['bidp_rsqn1'], 50)
            self.assertNotIn('private', result['quote'])
            self.assertFalse((self.root / 'fast-exit-quotes/.114800.tmp').exists())

    def test_quote_snapshot_queue_failure_does_not_affect_protection(self):
        pending = Mock()
        pending.put_nowait.side_effect = RuntimeError('queue unavailable')
        with patch.object(v, '_QUOTE_SNAPSHOTS', pending):
            self.assertIsNone(v.record_protective_quote(self.root, '114800', {'bidp': 980}, NOW))

    def test_protective_wait_stays_fast_through_pending_reconciliation(self):
        db = Mock()
        delays = []
        def sleep(seconds):
            delays.append(seconds)
            if len(delays) == 5:
                raise RuntimeError('end test')
        with patch.object(v, 'datetime', Clock), patch.object(v, 'refresh_clock'), \
             patch.object(v, 'in_session', return_value=True), \
             patch.object(v, 'cycle', side_effect=['sell_protective_wait', 'pending', 'reconciled', 'wait', 'pending']), \
             patch.object(v.time, 'monotonic', return_value=0), \
             patch.object(v.time, 'sleep', side_effect=sleep):
            with self.assertRaisesRegex(RuntimeError, 'end test'):
                v.loop(db, None, None, ACCOUNT, self.root, False)
        self.assertEqual(delays, [30, 30, 1, 60, 60])
        db.close.assert_called_once()

    def test_normal_monitoring_counts_cycle_runtime_and_keeps_minimum_rest(self):
        db = Mock()
        delays = []
        def sleep(seconds):
            delays.append(seconds)
            if len(delays) == 2:
                raise RuntimeError('end test')
        with patch.object(v, 'datetime', Clock), patch.object(v, 'refresh_clock'), \
             patch.object(v, 'in_session', return_value=True), patch.object(v, 'cycle', return_value='wait'), \
             patch.object(v.time, 'monotonic', side_effect=[100, 120, 160, 221]), \
             patch.object(v.time, 'sleep', side_effect=sleep):
            with self.assertRaisesRegex(RuntimeError, 'end test'):
                v.loop(db, None, None, ACCOUNT, self.root, False)
        self.assertEqual(delays, [40, 10])
        db.close.assert_called_once()

    def test_other_holding_sell_keeps_failed_quote_on_fast_recheck(self):
        for symbol in ('005930', '114800'):
            self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', (symbol, symbol, 'BUY', 10, 1000, NOW.isoformat()))
        self.db.commit()
        def pages(broker, operation, fields, block):
            return {'Output_0': []} if operation == 'krstockInquiryDailyOrderExecution' else {
                'Output_0': {'orr_pbl_amt1': 10000}, 'Output_1': [
                    {'iem_cd': code, 'rsdl_qty': 10, 'phs_pr': 1000} for code in ('005930', '114800')]}
        with patch.object(v, 'datetime', Clock), patch.object(v, 'paper_pages', side_effect=pages), \
             patch.object(v, 'quote_of', side_effect=[ValueError('quote unavailable'), ValueError('quote unavailable'),
                                                       {'bidp': 980, 'askp': 981}]), \
             patch.object(v.time, 'sleep'), patch.object(v, 'journal_event'), \
             patch.object(v, 'place', return_value=True) as place:
            self.assertEqual(v.cycle(self.db, object(), Broker(), ACCOUNT, self.root, NOW), 'sell_protective_wait')
            self.assertEqual(place.call_args.args[5:7], ('114800', 'SELL'))
            place.assert_called_once()

    def test_recovered_protective_quote_sells_once_and_pending_blocks_next_send(self):
        self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('buy', '114800', 'BUY', 10, 1000, NOW.isoformat()))
        self.db.commit()
        def pages(broker, operation, fields, block):
            return {'Output_0': []} if operation == 'krstockInquiryDailyOrderExecution' else {
                'Output_0': {'orr_pbl_amt1': 10000},
                'Output_1': [{'iem_cd': '114800', 'rsdl_qty': 10, 'phs_pr': 1000}]}
        with patch.object(v, 'datetime', Clock), patch.object(v, 'paper_pages', side_effect=pages), \
             patch.object(v, 'quote_of', side_effect=[ValueError('quote unavailable'), {'bidp': 980, 'askp': 981}]) as quote, \
             patch.object(v.time, 'sleep'), patch.object(v, 'journal_event'), \
             patch.object(v, 'place', return_value=True) as place:
            self.assertEqual(v.cycle(self.db, object(), Broker(), ACCOUNT, self.root, NOW), 'sell')
            self.assertEqual(quote.call_count, 2)
            place.assert_called_once()
            self.db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?,?,?,?)',
                            ('pending', '20261112', '114800', 'SELL', 10, 980, 'uncertain', None, 0))
            self.db.commit()
            with patch.object(v, 'reconcile', return_value=False):
                self.assertEqual(v.cycle(self.db, object(), Broker(), ACCOUNT, self.root, NOW), 'pending')
            place.assert_called_once()

    def test_regular_position_takes_half_before_full_target(self):
        status, submit, events = self.run_cycle({'114800': 10}, bid=1010)
        self.assertEqual((status, submit.call_args.args[4:7]), ('sell', ('114800', 'SELL', 5)))
        self.assertEqual(submit.call_args.kwargs['sell_trigger'], 'take_profit')
        self.assertEqual(next(c.kwargs['reason'] for c in events.call_args_list if c.args[1] == 'v2_risk_exit'), 'take_profit_half')

    def test_rest_leaves_at_breakeven_after_a_half_take_profit(self):
        # user approval 2026-11-17 (replay 11/16 100790): vibe's ordinary positions get the Jev runner's breakeven stop
        status, submit, events = self.run_cycle({'114800': 5}, bid=1000, history=[('a', '114800', 'BUY', 10), ('b', '114800', 'SELL', 5)])
        self.assertEqual((status, submit.call_args.args[4:7]), ('sell', ('114800', 'SELL', 5)))
        self.assertEqual(submit.call_args.kwargs['sell_trigger'], 'risk')
        self.assertEqual(next(c.kwargs['reason'] for c in events.call_args_list if c.args[1] == 'v2_risk_exit'), 'breakeven_stop')

    def test_breakeven_waits_above_the_average_and_after_a_new_buy(self):
        status, submit, _ = self.run_cycle({'114800': 5}, bid=1004, history=[('a', '114800', 'BUY', 10), ('b', '114800', 'SELL', 5)])
        submit.assert_not_called()  # above the average: kept
        self.db.execute('DELETE FROM fills'); self.db.commit()
        status, submit, _ = self.run_cycle({'114800': 15}, bid=1000, history=[('a', '114800', 'BUY', 10), ('b', '114800', 'SELL', 5),
                                                                                ('c', '114800', 'BUY', 10)])
        self.assertFalse(any(c.args[5] == 'SELL' for c in submit.call_args_list if len(c.args) > 5))  # a re-buy re-bases it

    def test_half_target_counts_new_buys_after_partial_profit(self):
        for order, side, quantity in (('a', 'BUY', 100), ('b', 'SELL', 50), ('c', 'BUY', 100)):
            self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', (order, '114800', side, quantity, 1000, NOW.isoformat()))
        plan = p.position_since_open(self.db, '114800', 150)
        self.assertEqual((plan['bought'], plan['sold'], p.take_profit_target(plan) - plan['sold']), (200, 50, 50))

    def test_v2_half_exit_retries_unsent_and_recovers_partial_fill(self):
        plan = {'variant': 'v2', 'opened': NOW.isoformat(), 'hold_days': 3,
                'stop_price': 900, 'half_price': 1080, 'target_price': 1150, 'half_done': False}
        v.save_oversold_plans(self.root, {'114800': plan})
        self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('buy', '114800', 'BUY', 10, 1000, NOW.isoformat()))
        self.db.commit()
        def pages(broker, operation, fields, block):
            if operation == 'krstockInquiryDailyOrderExecution':
                return {'Output_0': []}
            qty = p.owned_positions(self.db).get('114800', 0)
            return {'Output_0': {'orr_pbl_amt1': 10_000_000}, 'Output_1': [
                {'iem_cd': '114800', 'rsdl_qty': qty, 'phs_pr': 1000, 'eal_pls_amt': 0}]}
        with patch.object(v, 'datetime', Clock), patch.object(v, 'paper_pages', side_effect=pages), \
             patch.object(v, 'quote_of', return_value={'bidp': 1080, 'askp': 1081}), \
             patch.object(v, 'journal_event'), patch.object(v, 'place', return_value=False) as place:
            self.assertEqual(v.cycle(self.db, object(), Broker(), ACCOUNT, self.root, NOW), 'protective_wait')
            self.assertFalse(v.oversold_plans(self.root)['114800']['half_done'])
            self.assertEqual(place.call_args.args[7], 5)
            place.return_value = True
            self.assertEqual(v.cycle(self.db, object(), Broker(), ACCOUNT, self.root, NOW), 'sell')
            self.assertFalse(v.oversold_plans(self.root)['114800']['half_done'])
            self.db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?,?,?,?)', ('ambiguous', '20261112', '114800', 'SELL', 5, 1080, 'uncertain', None, 0))
            self.db.commit()
            place.reset_mock()
            with patch.object(v, 'reconcile', return_value=False):
                self.assertEqual(v.cycle(self.db, object(), Broker(), ACCOUNT, self.root, NOW), 'pending')
            place.assert_not_called()
            self.db.execute("DELETE FROM intents WHERE id='ambiguous'")
            self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('partial', '114800', 'SELL', 3, 1080, NOW.isoformat()))
            self.db.commit()
            self.assertEqual(v.cycle(self.db, object(), Broker(), ACCOUNT, self.root, NOW), 'sell')
            self.assertEqual(place.call_args.args[7], 2)
            self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('remainder', '114800', 'SELL', 2, 1080, NOW.isoformat()))
            self.db.commit()
            place.reset_mock()
            self.assertEqual(v.cycle(self.db, object(), Broker(), ACCOUNT, self.root, NOW), 'wait')
            place.assert_not_called()
            self.assertTrue(v.oversold_plans(self.root)['114800']['half_done'])

    def test_v2_stop_is_not_blocked_by_unavailable_half_exit_history(self):
        v.save_oversold_plans(self.root, {'114800': {'variant': 'v2', 'opened': NOW.isoformat(), 'hold_days': 3,
                                                   'stop_price': 900, 'half_price': 1080, 'target_price': 1150}})
        with patch.object(p, 'position_since_open', side_effect=ValueError('history unavailable')):
            status, submit, _ = self.run_cycle({'114800': 10}, bid=899, ask=900)
        self.assertEqual((status, submit.call_args.args[5:7]), ('sell', ('SELL', 10)))
        self.assertEqual(submit.call_args.kwargs['sell_trigger'], 'risk')

    def test_buy_once_capped_by_order_notional(self):
        self.advise([{'action': 'BUY', 'symbol': '005930', 'quantity': 5000, 'confidence': 0.9}])
        status, submit, _ = self.run_cycle(ask=1000)
        self.assertEqual((status, submit.call_args.args[4:7]), ('buy', ('005930', 'BUY', 1000)))  # 3,000,000 // 1000 and 1,000 shares
        self.db.execute('DELETE FROM fills'); self.db.commit()
        status, submit, _ = self.run_cycle()
        self.assertEqual(status, 'wait')
        submit.assert_not_called()

    def test_new_buy_rechecks_exact_advice_before_transport(self):
        decision = {'action': 'BUY', 'symbol': '005930', 'quantity': 5, 'confidence': 0.9}
        self.advise([decision])
        status, submit, events = self.run_cycle()
        self.assertEqual(status, 'buy')
        check = submit.call_args.kwargs['signal_check']
        with patch.object(v, 'datetime', Clock), patch.object(v, 'ensure_trading_open'), \
             patch.object(v, 'etf7_protective_deadline', return_value=2**52):
            self.assertTrue(check())
            path = self.root / 'vibe-advice.json'
            changed = json.loads(path.read_text())
            self.assertFalse(v.model_buy_check(self.root, changed, 0, '005930', decision, 'wrong-id'))
            self.assertFalse(v.model_buy_check(self.root, changed, 0, '005930', {**decision, 'quantity': 8}, 'adv1'))
            changed['decisions'][0]['quantity'] = 7  # even an unchanged id must not authorize altered content
            path.write_text(json.dumps(changed))
            self.assertFalse(check())
            changed['decisions'][0] = decision
            changed['time'] = (NOW - timedelta(minutes=21)).isoformat()
            path.write_text(json.dumps(changed))
            self.assertFalse(check())
        before = next(c for c in events.call_args_list if c.args[1] == 'vibe_decision_intent')
        after = next(c for c in events.call_args_list if c.args[1] == 'submitted')
        self.assertEqual(before.kwargs['cycle_id'], after.kwargs['cycle_id'])
        self.assertEqual((after.kwargs['advice_id'], after.kwargs['decision_idx']), ('adv1', 0))
        self.assertEqual(after.kwargs['advice_started_at'], NOW.isoformat())
        self.assertIsNone(after.kwargs['decision_available_at'])

    def test_decision_available_time_requires_aware_nonfuture_evidence(self):
        base = {'id': 'a', 'time': (NOW - timedelta(minutes=1)).isoformat()}
        with patch.object(v, 'datetime', Clock):
            self.assertIsNone(v.decision_evidence({**base, 'available_at': '2026-11-12T09:59:30'}, 0)['decision_available_at'])
            self.assertIsNone(v.decision_evidence({**base, 'available_at': (NOW + timedelta(seconds=1)).isoformat()}, 0)['decision_available_at'])
            at = (NOW - timedelta(seconds=1)).isoformat()
            self.assertEqual(v.decision_evidence({**base, 'available_at': at}, 0)['decision_available_at'], at)

    def test_new_buy_requires_recent_same_day_completed_technical_bar(self):
        advice = {'time': NOW.isoformat(), 'technical_basic': {'005930': {'as_of': '09:58', 'vote': 1}}}
        self.assertTrue(v.technical_buy_fresh(advice, '005930', NOW))
        advice['technical_basic']['005930']['as_of'] = '09:55'  # 5 minutes before the run started (11/16: 2-4 typical)
        self.assertTrue(v.technical_buy_fresh(advice, '005930', NOW + timedelta(minutes=2)))
        for value in ('09:54', '10:00', '10:01', '2026-11-12T09:58:00', 'invalid', None):
            with self.subTest(value=value):
                advice['technical_basic']['005930']['as_of'] = value
                self.assertFalse(v.technical_buy_fresh(advice, '005930', NOW))
        advice['technical_basic']['005930']['as_of'] = (NOW - timedelta(minutes=2)).isoformat()
        self.assertTrue(v.technical_buy_fresh(advice, '005930', NOW))
        self.assertTrue(v.technical_buy_fresh(advice, '005930', NOW + timedelta(minutes=3)))  # run + poll delay
        self.assertFalse(v.technical_buy_fresh(advice, '005930', NOW + timedelta(minutes=6)))  # 8 minutes old
        self.assertFalse(v.technical_buy_fresh(advice, '000660', NOW))
        for vote in (-1, None, True, '1'):
            advice['technical_basic']['005930']['vote'] = vote
            self.assertFalse(v.technical_buy_fresh(advice, '005930', NOW))

    def test_technical_bar_expiring_during_transport_aborts_buy(self):
        import sys
        here = sys.modules[__name__]
        decision = {'action': 'BUY', 'symbol': '005930', 'quantity': 5, 'confidence': 0.9}
        self.advise([decision]); v.executions(self.db)
        advice = json.loads((self.root / 'vibe-advice.json').read_text())
        path = self.root / 'account-state.json'
        path.write_text(json.dumps({'time': NOW.isoformat(), 'holdings': {}}))
        class RacingBroker:
            def call_tool(inner, tool, arguments, **kwargs):
                self.assertTrue(kwargs['pre_reserve_check']())
                later = NOW + timedelta(minutes=6)  # the bar (2 minutes before the run) is now 8 minutes old: past the 7-minute ceiling
                path.write_text(json.dumps({'time': later.isoformat(), 'holdings': {}}))
                with patch.object(here, 'NOW', later):
                    self.assertFalse(kwargs['pre_send_check']())
                return {'status': 'error', 'error_type': 'NHPreSendCheckFailed',
                        'order_safety': {'state': 'aborted_no_send', 'resubmit_allowed': False}}
        with patch.object(v, 'datetime', Clock), patch.object(p, 'order_session', return_value=True), \
             patch.object(v, 'ensure_trading_open'), patch.object(v, 'etf7_protective_deadline', return_value=2**52), \
             patch.object(v, 'quote_of', return_value={'askp': 1000, 'bidp': 999}), patch.object(v, 'journal_event'):
            result = v.execute(self.db, object(), RacingBroker(), ACCOUNT, self.root, '20261112', 'adv1', 0,
                               decision, {'005930'}, {}, 10_000_000, 0, set(), False, model_buy_advice=advice)
        self.assertIsNone(result)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM intents WHERE status!='terminal'").fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM pre_send_aborts').fetchone()[0], 1)

    def test_missing_or_nonfinite_held_pnl_blocks_new_buys(self):
        for pnl in (None, float('nan'), float('inf'), float('-inf')):
            with self.subTest(pnl=pnl):
                self.db.execute('DELETE FROM fills')
                self.db.execute('DELETE FROM advice_executions') if self.db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='advice_executions'").fetchone() else None
                self.db.commit()
                self.advise([{'action': 'BUY', 'symbol': '005930', 'quantity': 5, 'confidence': 0.9}])
                status, submit, events = self.run_cycle({'114800': 1}, pnl=pnl)
                self.assertEqual(status, 'wait')
                submit.assert_not_called()
                self.assertEqual(events.call_args.kwargs['reason'], 'daily_loss_unverified')

    def test_unverified_pnl_does_not_block_protective_stop(self):
        self.advise([{'action': 'BUY', 'symbol': '005930', 'quantity': 5, 'confidence': 0.9}])
        status, submit, _ = self.run_cycle({'114800': 10}, bid=979, pnl=float('nan'))
        self.assertEqual((status, submit.call_args.args[4:7]), ('sell', ('114800', 'SELL', 10)))
        self.assertEqual(submit.call_args.kwargs['sell_trigger'], 'risk')

    def losing_round_trips(self):
        for i in range(3):
            start = NOW - timedelta(minutes=20 - 4 * i)
            self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', (f'loss-buy-{i}', '069500', 'BUY', 1, 1000, start.isoformat()))
            self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', (f'loss-sell-{i}', '069500', 'SELL', 1, 990, (start + timedelta(minutes=1)).isoformat()))
        self.db.commit()

    def test_losing_streak_blocks_normal_and_oversold_buys_but_switch_can_disable(self):
        from strategy_settings import DEFAULTS
        self.losing_round_trips()
        self.advise([{'action': 'BUY', 'symbol': '005930', 'quantity': 5, 'confidence': 0.9}])
        with patch.object(v, 'oversold_v2_buy') as oversold:
            status, submit, events = self.run_cycle()
        self.assertEqual(status, 'wait'); submit.assert_not_called(); oversold.assert_not_called()
        self.assertEqual(events.call_args.kwargs['reason'], 'losing_streak_cooldown')
        self.db.execute('DELETE FROM advice_executions'); self.db.commit()
        with patch('strategy_settings.read_settings', return_value=({**DEFAULTS, 'losing_streak_guard': 0}, 'custom')):
            status, submit, _ = self.run_cycle()
        self.assertEqual(status, 'buy'); submit.assert_called_once()

    def test_losing_streak_does_not_block_protective_sell(self):
        self.losing_round_trips()
        self.advise([{'action': 'BUY', 'symbol': '005930', 'quantity': 5, 'confidence': 0.9}])
        status, submit, _ = self.run_cycle({'114800': 10}, bid=979)
        self.assertEqual((status, submit.call_args.args[4:7]), ('sell', ('114800', 'SELL', 10)))
        self.assertEqual(submit.call_args.kwargs['sell_trigger'], 'risk')

    def test_buy_pre_send_rechecks_loss_snapshot_and_current_limit(self):
        self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('owned', '114800', 'BUY', 1, 1000, NOW.isoformat()))
        self.db.commit()
        state = {'time': NOW.isoformat(), 'holdings': {'114800': {'rsdl_qty': 1, 'eal_pls_amt': 0}}}
        path = self.root / 'account-state.json'
        path.write_text(json.dumps(state))
        captured = []
        def submit(*args, **kwargs):
            check = kwargs['signal_check']
            captured.append(check())  # pre-reserve
            state['holdings']['114800']['eal_pls_amt'] = -300_000
            path.write_text(json.dumps(state))
            captured.append(check())  # pre-send
            raise v.PaperOrderNotSent('changed')
        with patch.object(v, 'datetime', Clock), patch.object(v, 'ensure_trading_open'), \
             patch.object(v, 'etf7_protective_deadline', return_value=2**52), \
             patch.object(v, 'submit', side_effect=submit), patch.object(v, 'journal_event'):
            result = v.place(self.db, Broker(), ACCOUNT, self.root, '20261112', '005930', 'BUY', 1, {}, 1000,
                             {'005930'}, 'a' * 32)
        self.assertFalse(result)
        self.assertEqual(captured, [True, False])

    def test_unverified_fill_history_or_stale_account_snapshot_blocks_buy(self):
        self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('bad', '114800', 'SELL', 1, 1000, NOW.isoformat()))
        self.db.commit()
        self.assertEqual(v.daily_loss_reason(self.db, '20261112', {}, {}, 300_000), 'daily_loss_unverified')
        self.db.execute('DELETE FROM fills'); self.db.commit()
        state = {'time': (NOW - timedelta(seconds=61)).isoformat(), 'holdings': {}}
        (self.root / 'account-state.json').write_text(json.dumps(state))
        with patch.object(v, 'datetime', Clock):
            self.assertFalse(v.buy_loss_check(self.db, self.root, '20261112'))
            state['time'] = NOW.isoformat()
            (self.root / 'account-state.json').write_text(json.dumps(state))
            self.assertTrue(v.buy_loss_check(self.db, self.root, '20261112'))
        self.assertEqual(v.daily_loss_reason(self.db, '20261112', {'114800': 1},
                                            {'114800': {'rsdl_qty': True, 'eal_pls_amt': 0}}, 300_000),
                         'daily_loss_unverified')

    def test_advice_change_during_transport_aborts_proven_unsent_intent(self):
        decision = {'action': 'BUY', 'symbol': '005930', 'quantity': 5, 'confidence': 0.9}
        self.advise([decision])
        path = self.root / 'vibe-advice.json'
        advice = json.loads(path.read_text())
        v.executions(self.db)
        (self.root / 'account-state.json').write_text(json.dumps({'time': NOW.isoformat(), 'holdings': {}}))
        calls = []
        class RacingBroker:
            def call_tool(inner, tool, arguments, **kwargs):
                calls.append('reservation')
                self.assertTrue(kwargs['pre_reserve_check']())
                updated = {**advice, 'decisions': [{**decision, 'quantity': 6}]}
                path.write_text(json.dumps(updated))
                self.assertFalse(kwargs['pre_send_check']())
                return {'status': 'error', 'error_type': 'NHPreSendCheckFailed',
                        'order_safety': {'state': 'aborted_no_send', 'resubmit_allowed': False}}
        with patch.object(v, 'datetime', Clock), patch.object(p, 'order_session', return_value=True), \
             patch.object(v, 'ensure_trading_open'), patch.object(v, 'etf7_protective_deadline', return_value=2**52), \
             patch.object(v, 'quote_of', return_value={'askp': 1000, 'bidp': 999}), patch.object(v, 'journal_event'):
            result = v.execute(self.db, object(), RacingBroker(), ACCOUNT, self.root, '20261112', 'adv1', 0,
                               decision, {'005930'}, {}, 10_000_000, 0, set(), False, model_buy_advice=advice)
        self.assertIsNone(result)
        self.assertEqual(calls, ['reservation'])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM intents WHERE status!='terminal'").fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM pre_send_aborts').fetchone()[0], 1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM fills').fetchone()[0], 0)

    def test_loss_snapshot_change_during_transport_aborts_proven_unsent_buy(self):
        decision = {'action': 'BUY', 'symbol': '005930', 'quantity': 5, 'confidence': 0.9}
        self.advise([decision])
        advice = json.loads((self.root / 'vibe-advice.json').read_text())
        self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('owned', '114800', 'BUY', 1, 1000, NOW.isoformat()))
        self.db.commit(); v.executions(self.db)
        path = self.root / 'account-state.json'
        state = {'time': NOW.isoformat(), 'holdings': {'114800': {'rsdl_qty': 1, 'eal_pls_amt': 0}}}
        path.write_text(json.dumps(state))
        class RacingBroker:
            def call_tool(inner, tool, arguments, **kwargs):
                self.assertTrue(kwargs['pre_reserve_check']())
                state['holdings']['114800']['eal_pls_amt'] = -300_000
                path.write_text(json.dumps(state))
                self.assertFalse(kwargs['pre_send_check']())
                return {'status': 'error', 'error_type': 'NHPreSendCheckFailed',
                        'order_safety': {'state': 'aborted_no_send', 'resubmit_allowed': False}}
        with patch.object(v, 'datetime', Clock), patch.object(p, 'order_session', return_value=True), \
             patch.object(v, 'ensure_trading_open'), patch.object(v, 'etf7_protective_deadline', return_value=2**52), \
             patch.object(v, 'quote_of', return_value={'askp': 1000, 'bidp': 999}), patch.object(v, 'journal_event'):
            result = v.execute(self.db, object(), RacingBroker(), ACCOUNT, self.root, '20261112', 'adv1', 0,
                               decision, {'005930'}, {'114800': 1}, 10_000_000, 0, set(), False, model_buy_advice=advice)
        self.assertIsNone(result)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM intents WHERE status!='terminal'").fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM pre_send_aborts').fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM fills WHERE symbol='005930'").fetchone()[0], 0)

    def test_cooldown_becoming_active_during_transport_aborts_buy(self):
        decision = {'action': 'BUY', 'symbol': '005930', 'quantity': 5, 'confidence': 0.9}
        self.advise([decision]); v.executions(self.db)
        advice = json.loads((self.root / 'vibe-advice.json').read_text())
        (self.root / 'account-state.json').write_text(json.dumps({'time': NOW.isoformat(), 'holdings': {}}))
        class RacingBroker:
            def call_tool(inner, tool, arguments, **kwargs):
                self.assertTrue(kwargs['pre_reserve_check']())
                self.assertFalse(kwargs['pre_send_check']())
                return {'status': 'error', 'error_type': 'NHPreSendCheckFailed',
                        'order_safety': {'state': 'aborted_no_send', 'resubmit_allowed': False}}
        with patch.object(v, 'datetime', Clock), patch.object(p, 'order_session', return_value=True), \
             patch.object(p, 'losing_streak', side_effect=[None, 'losing_streak_cooldown']), \
             patch.object(v, 'ensure_trading_open'), patch.object(v, 'etf7_protective_deadline', return_value=2**52), \
             patch.object(v, 'quote_of', return_value={'askp': 1000, 'bidp': 999}), patch.object(v, 'journal_event'):
            result = v.execute(self.db, object(), RacingBroker(), ACCOUNT, self.root, '20261112', 'adv1', 0,
                               decision, {'005930'}, {}, 10_000_000, 0, set(), False, model_buy_advice=advice)
        self.assertIsNone(result)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM intents WHERE status!='terminal'").fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM pre_send_aborts').fetchone()[0], 1)

    def test_limits_and_confidence_block_buys_and_are_recorded(self):
        cases = ((dict(pnl=-300_000), 'daily_loss_limit', 0.9, '005930'),
                 (dict(), 'invalid_score', None, '005930'),
                 (dict(), 'symbol_not_allowed', 0.9, '000660'))
        for kwargs, reason, confidence, symbol in cases:
            with self.subTest(reason=reason):
                self.db.execute('DELETE FROM advice_executions') if self.db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='advice_executions'").fetchone() else None
                self.db.execute('DELETE FROM fills'); self.db.commit()
                self.advise([{'action': 'BUY', 'symbol': symbol, 'quantity': 5, 'confidence': confidence}])
                status, submit, events = self.run_cycle({'114800': 1} if 'pnl' in kwargs else None, **kwargs)
                submit.assert_not_called()
                self.assertEqual(events.call_args.kwargs['reason'], reason)

    def test_notional_sets_quantity(self):
        self.advise([{'action': 'BUY', 'symbol': '005930', 'notional': 1_650_000, 'confidence': 0.55}])
        _, submit, _ = self.run_cycle(ask=10_000)
        self.assertEqual(submit.call_args.args[6], 165)

    def test_total_quantity_limit_can_be_disabled(self):
        for limit, expected in ((1000, 100), (0, 2000)):
            with self.subTest(limit=limit), patch.object(v, 'setting', return_value=limit):
                self.db.execute('DELETE FROM fills')
                self.db.execute('DELETE FROM advice_executions') if self.db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='advice_executions'").fetchone() else None
                self.db.commit()
                self.advise([{'action': 'BUY', 'symbol': '005930', 'quantity': 2000, 'confidence': 0.9}])
                status, submit, _ = self.run_cycle({'114800': 900})
                self.assertEqual((status, submit.call_args.args[6]), ('buy', expected))

    def test_daily_buy_limit_counts_todays_buys(self):
        self.db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?,?,?,?)',
                        ('b' * 32, '20261112', '069500', 'BUY', 60, 99_000, 'terminal', 1, 0))
        self.db.commit()
        self.advise([{'action': 'BUY', 'symbol': '005930', 'quantity': 100, 'confidence': 0.9}])
        _, submit, events = self.run_cycle()
        submit.assert_not_called()
        self.assertEqual(events.call_args.kwargs['reason'], 'daily_buy_limit')

    def test_crash_after_send_never_resends_and_quote_errors_do_not_stop_runner(self):
        self.advise([{'action': 'BUY', 'symbol': '005930', 'quantity': 5, 'confidence': 0.9}])
        with patch.object(v, 'place', side_effect=OSError('ledger write failed after acknowledgement')):
            with self.assertRaises(OSError):
                self.run_cycle()
        status, submit, _ = self.run_cycle()
        self.assertEqual(status, 'wait')  # marked 'sending' before transport
        submit.assert_not_called()
        self.db.execute('DELETE FROM advice_executions'); self.db.execute('DELETE FROM fills'); self.db.commit()
        self.advise([{'action': 'BUY', 'symbol': '005930', 'quantity': 5, 'confidence': 0.9}])
        with patch.object(v, 'quote_of', side_effect=ValueError('quote unavailable')):
            def pages(b, operation, fields, block):
                return {'Output_0': []} if operation == 'krstockInquiryDailyOrderExecution' else {'Output_0': {'orr_pbl_amt1': 1}, 'Output_1': []}
            with patch.object(v, 'paper_pages', side_effect=pages), patch.object(v, 'journal_event'), \
                 patch.object(v, 'datetime', Clock), patch.object(v, 'submit') as never:
                self.assertEqual(v.cycle(self.db, object(), Broker(), ACCOUNT, self.root, NOW), 'wait')
                never.assert_not_called()
        outcome = self.db.execute('SELECT outcome FROM advice_executions').fetchone()[0]
        self.assertEqual(outcome, 'error:ValueError')

    def test_sell_all_and_stale_advice(self):
        self.advise([{'action': 'SELL', 'symbol': '114800', 'quantity': 'ALL', 'confidence': 0.8}])
        path = self.root / 'vibe-advice.json'
        advice = json.loads(path.read_text())
        advice.update(account_holdings={'114800': 7.0}, account_fill_ids={'114800': '1148007'})
        path.write_text(json.dumps(advice))
        status, submit, _ = self.run_cycle({'114800': 7})
        self.assertEqual((status, submit.call_args.args[4:7]), ('sell', ('114800', 'SELL', 7)))
        self.assertEqual(submit.call_args.kwargs['sell_trigger'], 'model')
        self.db.execute('DELETE FROM fills'); self.db.execute('DELETE FROM advice_executions'); self.db.commit()
        self.advise([{'action': 'BUY', 'symbol': '005930', 'quantity': 5, 'confidence': 0.9}], at=NOW - timedelta(minutes=21))
        status, submit, _ = self.run_cycle()
        self.assertEqual(status, 'wait')
        submit.assert_not_called()

    def test_model_sell_checks_position_generation_and_advice_again_before_transport(self):
        decision = {'action': 'SELL', 'symbol': '114800', 'quantity': 'ALL', 'confidence': 0.8}
        self.advise([decision])
        path = self.root / 'vibe-advice.json'
        advice = json.loads(path.read_text())
        advice.update(account_holdings={'114800': 7}, account_fill_ids={'114800': '1148007'})
        path.write_text(json.dumps(advice))
        status, submit, _ = self.run_cycle({'114800': 7})
        self.assertEqual(status, 'sell')
        check = submit.call_args.kwargs['signal_check']
        with patch.object(v, 'datetime', Clock), patch.object(v, 'ensure_trading_open'), \
             patch.object(v, 'etf7_protective_deadline', return_value=2**52):
            self.assertTrue(check())
            # Equal holdings do not make a closed and re-entered position the same position.
            self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('exit', '114800', 'SELL', 7, 1000, NOW.isoformat()))
            self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('new', '114800', 'BUY', 7, 1000, NOW.isoformat()))
            self.db.commit()
            self.assertFalse(check())
            self.db.execute("DELETE FROM fills WHERE order_id IN ('exit', 'new')")
            self.db.commit()
            self.assertTrue(check())
            advice['time'] = (NOW - timedelta(minutes=21)).isoformat()
            path.write_text(json.dumps(advice))
            self.assertFalse(check())
            advice['time'] = NOW.isoformat()
            advice['signals'] = {'114800': -0.8}  # same id is not the same advice snapshot
            path.write_text(json.dumps(advice))
            self.assertFalse(check())
            advice['decisions'] = []
            path.write_text(json.dumps(advice))
            self.assertFalse(check())

    def test_model_sell_missing_position_evidence_never_orders(self):
        self.advise([{'action': 'SELL', 'symbol': '114800', 'quantity': 'ALL', 'confidence': 0.8}])
        status, submit, events = self.run_cycle({'114800': 7})
        self.assertEqual(status, 'wait')
        submit.assert_not_called()
        self.assertEqual(events.call_args.kwargs['reason'], 'model_position_changed')


    def test_take_profit_flatten_cutoff_and_symbol_budget(self):
        import sys
        here = sys.modules[__name__]
        status, submit, events = self.run_cycle({'114800': 10}, bid=1020)  # +2% = take_profit_all_pct default
        self.assertEqual((status, next(c.kwargs['reason'] for c in events.call_args_list if c.args[1] == 'v2_risk_exit')), ('sell', 'take_profit'))
        self.db.execute('DELETE FROM fills'); self.db.commit()
        late = NOW.replace(hour=14, minute=41)
        with patch.object(here, 'NOW', late):
            status, submit, events = self.run_cycle({'114800': 10}, bid=1000)
            self.assertEqual((status, next(c.kwargs['reason'] for c in events.call_args_list if c.args[1] == 'v2_risk_exit')),
                             ('sell', 'session_flatten'))
        self.db.execute('DELETE FROM fills'); self.db.commit()
        with patch.object(here, 'NOW', NOW.replace(hour=14, minute=31)):
            self.advise([{'action': 'BUY', 'symbol': '005930', 'quantity': 5, 'confidence': 0.9}], at=NOW.replace(hour=14, minute=30))
            status, submit, events = self.run_cycle()
            submit.assert_not_called()
            self.assertEqual(events.call_args.kwargs['reason'], 'entry_cutoff')
        self.db.execute('DELETE FROM advice_executions'); self.db.commit()
        # A new held-position decision is capped by the configured one-tranche amount.
        self.advise([{'action': 'BUY', 'symbol': '005930', 'notional': 3_000_000, 'confidence': 0.9}])
        with patch.object(v, 'held_buy_check', return_value=True), patch.object(v, 'add_better', return_value=True), \
             patch('nh_paper_isolated.add_size_factor', return_value=1.0):
            status, submit, _ = self.run_cycle({'005930': 250}, ask=10_000)
        self.assertEqual(submit.call_args.args[4:7], ('005930', 'BUY', 100))

    def test_held_buy_checks_fresh_decision_provenance_and_projected_stop(self):
        from strategy_settings import DEFAULTS
        advice = {'id': 'adv1', 'time': NOW.isoformat(), 'decisions': [{'action': 'BUY', 'symbol': '005930'}], 'signals': {'005930': 0.7},
                  'grounded': True, 'confirmed_by_previous_run': True}
        (self.root / 'vibe-advice.json').write_text(json.dumps(advice))
        args = (self.db, self.root, '005930', 10, 1000, 10, {'bidp': 1000, 'askp': 1005}, 'adv1', DEFAULTS)
        with patch.object(v, 'datetime', Clock), patch.object(v, 'market_view', return_value={'코스피': {'regime': '하락장'}}), \
             patch.object(v, 'stop_symbol_index', return_value='코스피'), patch.object(v, 'setting', return_value=0.5), \
             patch.object(p, 'scale_in_allowed', return_value=True):
            self.assertTrue(v.held_buy_check(*args))
            bad_quote = (*args[:6], {'bidp': 990, 'askp': 1005}, *args[7:])
            self.assertFalse(v.held_buy_check(*bad_quote))
            advice['confirmed_by_previous_run'] = False
            (self.root / 'vibe-advice.json').write_text(json.dumps(advice))
            self.assertFalse(v.held_buy_check(*args))

    def test_held_signal_and_step_switches_are_independent(self):
        from strategy_settings import DEFAULTS
        advice = {'id': 'adv1', 'time': NOW.isoformat(), 'decisions': [{'action': 'BUY', 'symbol': '005930'}],
                  'signals': {'005930': 0.7}, 'grounded': True, 'confirmed_by_previous_run': True}
        (self.root / 'vibe-advice.json').write_text(json.dumps(advice))
        args = (self.db, self.root, '005930', 10, 1000, 10, {'bidp': 1000, 'askp': 1005}, 'adv1')
        bars = [{'stck_prpr': '1004'}, {'stck_prpr': '1003'}]
        with patch.object(v, 'datetime', Clock), patch.object(v, 'market_view', return_value={'코스피': {'regime': '횡보장'}}), \
             patch.object(v, 'stop_symbol_index', return_value='코스피'), patch.object(v, 'setting', return_value=0.5), \
             patch.object(p, 'scale_in_allowed', return_value=True), patch.object(p, 'fresh_series', return_value=True), \
             patch.object(p, 'scale_in_signal', return_value='trend') as signal:
            only_step = {**DEFAULTS, 'scale_signal_add_on': 0, 'scale_step_add_on': 1, 'scale_interval_min': 9}
            self.assertFalse(v.held_buy_check(*args, only_step))
            self.assertTrue(v.held_buy_check(*args, only_step, bars))
            self.assertEqual(signal.call_args.kwargs['interval_min'], 9)
            only_signal = {**only_step, 'scale_signal_add_on': 1, 'scale_step_add_on': 0}
            self.assertTrue(v.held_buy_check(*args, only_signal))
            self.assertFalse(v.held_buy_check(*args, only_signal, bars))

    def test_held_profit_rebuy_and_interval_use_verified_fill_history(self):
        from strategy_settings import DEFAULTS
        advice = {'id': 'adv1', 'time': NOW.isoformat(), 'decisions': [{'action': 'BUY', 'symbol': '114800'}],
                  'signals': {'114800': 0.7}, 'grounded': True, 'confirmed_by_previous_run': True}
        (self.root / 'vibe-advice.json').write_text(json.dumps(advice))
        self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('b', '114800', 'BUY', 100, 1272, (NOW-timedelta(minutes=20)).isoformat()))
        self.db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?,?,?,?)', ('s', '20261112', '114800', 'SELL', 50, 1299, 'terminal', 123, (NOW-timedelta(minutes=11)).timestamp()))
        self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('s', '114800', 'SELL', 50, 1299, (NOW-timedelta(minutes=10)).isoformat()))
        self.db.execute('INSERT INTO sell_order_triggers VALUES (?,?)', ('s', 'take_profit'))
        self.db.commit()
        bars = [{'stck_prpr': '1290'}, {'stck_prpr': '1280'}]
        args = (self.db, self.root, '114800', 50, 1272, 775, {'bidp': 1288, 'askp': 1289}, 'adv1')
        cfg = {**DEFAULTS, 'scale_signal_add_on': 1, 'scale_step_add_on': 1, 'scale_profit_rebuy_on': 0}
        with patch.object(v, 'datetime', Clock), patch.object(v, 'market_view', return_value={'코스피': {'regime': '횡보장'}}), \
             patch.object(v, 'stop_symbol_index', return_value='코스피'), patch.object(v, 'setting', return_value=0.5), \
             patch.object(p, 'fresh_series', return_value=True):
            self.assertFalse(v.held_buy_check(*args, cfg))
            cfg = {**cfg, 'scale_profit_rebuy_on': 1}
            self.assertTrue(v.held_buy_check(*args, cfg))
            self.assertTrue(v.held_buy_check(*args, {**cfg, 'scale_interval_min': 1}, bars))
            self.assertFalse(v.held_buy_check(*args, {**cfg, 'scale_interval_min': 60}, bars))

    def test_invalid_scale_settings_fail_closed_and_are_logged_without_blocking_sell(self):
        from strategy_settings import DEFAULTS
        with patch('strategy_settings.read_settings', return_value=(dict(DEFAULTS), 'invalid')), \
             patch.object(v, 'journal_event') as events:
            v._APPLIED_SCALE = None
            settings = v.scale_settings(self.root)
            self.assertEqual([settings[k] for k in ('scale_signal_add_on', 'scale_step_add_on', 'scale_profit_rebuy_on')], [0, 0, 0])
            self.assertEqual(events.call_args.kwargs['status'], 'invalid')
            status, submit, _ = self.run_cycle({'114800': 10}, bid=979)
            self.assertEqual((status, submit.call_args.args[5]), ('sell', 'SELL'))

    def test_scale_settings_log_next_change_and_survive_log_failure(self):
        from strategy_settings import DEFAULTS
        custom = {**DEFAULTS, 'scale_signal_add_on': 1, 'scale_step_add_on': 1,
                  'scale_profit_rebuy_on': 1, 'scale_interval_min': 5}
        with patch('strategy_settings.read_settings', return_value=(custom, 'custom')) as read, \
             patch.object(v, 'journal_event') as event:
            v._APPLIED_SCALE = None
            v.scale_settings(self.root)
            v.scale_settings(self.root)
            self.assertEqual(event.call_count, 1)
            read.return_value = ({**custom, 'scale_step_add_on': 0, 'scale_interval_min': 7}, 'custom')
            v.scale_settings(self.root)
            self.assertEqual(event.call_count, 2)
            self.assertEqual(event.call_args.kwargs['settings']['scale_interval_min'], 7)
            read.return_value = (custom, 'custom')
            event.side_effect = OSError('unwritable journal')
            self.assertEqual(v.scale_settings(self.root)['scale_interval_min'], 5)

    def test_held_buy_refuses_unverified_sell_and_stale_signal(self):
        from strategy_settings import DEFAULTS
        advice = {'id': 'adv1', 'time': NOW.isoformat(), 'decisions': [{'action': 'BUY', 'symbol': '005930'}], 'signals': {'005930': 0.7},
                  'grounded': True, 'confirmed_by_previous_run': True}
        (self.root / 'vibe-advice.json').write_text(json.dumps(advice))
        args = (self.db, self.root, '005930', 10, 1000, 10, {'bidp': 1000, 'askp': 1005}, 'adv1', DEFAULTS)
        with patch.object(v, 'datetime', Clock), patch.object(v, 'setting', return_value=0.5), \
             patch.object(p, 'scale_in_allowed', return_value=False):
            self.assertFalse(v.held_buy_check(*args))
        advice['time'] = (NOW - timedelta(minutes=21)).isoformat()
        (self.root / 'vibe-advice.json').write_text(json.dumps(advice))
        with patch.object(v, 'datetime', Clock), patch.object(v, 'setting', return_value=0.5), \
             patch.object(p, 'scale_in_allowed', return_value=True):
            self.assertFalse(v.held_buy_check(*args))

    def test_step_add_reuses_advice_once_per_verified_fill(self):
        advice = {'id': 'adv1', 'time': NOW.isoformat(), 'decisions': [
            {'action': 'BUY', 'symbol': '005930', 'notional': 1_000_000, 'confidence': 0.7}],
            'universe': ['005930'], 'signals': {'005930': 0.7},
            'grounded': True, 'confirmed_by_previous_run': True}
        (self.root / 'vibe-advice.json').write_text(json.dumps(advice))
        v.executions(self.db)
        v.mark(self.db, 'adv1', 0, 'buy_submitted')
        bars = [{'stck_prpr': '1008'}, {'stck_prpr': '1007'}]
        with patch.object(v, 'market_view', return_value={'코스피': {'regime': '상승장'}}), \
             patch.object(v, 'stop_symbol_index', return_value='코스피'), \
             patch.object(p, 'minute_bars', return_value={'status': 'ok', 'data': {}}), \
             patch('nh_etf_paper_runner.valid_bars', return_value=bars), \
             patch.object(p, 'fresh_series', return_value=True), patch.object(p, 'scale_in_allowed', return_value=True), \
             patch.object(p, 'scale_in_signal', return_value='trend'):
            status, submit, _ = self.run_cycle({'005930': 10}, bid=1008, ask=1009)
            self.assertEqual(status, 'buy')
            self.assertGreater(submit.call_args.args[6], 0)
            self.assertIn(('adv1:scale:00593010', 0), v.executions(self.db))
            self.db.execute('DELETE FROM fills'); self.db.commit()
            status, submit, _ = self.run_cycle({'005930': 10}, bid=1008, ask=1009)
            self.assertEqual(status, 'wait')
            submit.assert_not_called()

    def test_staged_add_respects_price_step_and_weak_market_guard(self):
        from strategy_settings import DEFAULTS
        advice = {'id': 'adv1', 'time': NOW.isoformat(), 'decisions': [{'action': 'BUY', 'symbol': '005930'}], 'signals': {'005930': 0.7},
                  'grounded': True, 'confirmed_by_previous_run': True}
        (self.root / 'vibe-advice.json').write_text(json.dumps(advice))
        args = (self.db, self.root, '005930', 10, 1000, 10, {'bidp': 1000, 'askp': 1005}, 'adv1', DEFAULTS,
                [{'stck_prpr': '1004'}, {'stck_prpr': '1003'}])
        with patch.object(v, 'datetime', Clock), patch.object(v, 'market_view', return_value={'코스피': {'regime': '하락장'}}), \
             patch.object(v, 'stop_symbol_index', return_value='코스피'), patch.object(v, 'setting', return_value=0.5), \
             patch.object(p, 'scale_in_allowed', return_value=True), patch.object(p, 'fresh_series', return_value=True), \
             patch.object(p, 'averaging_down_blocked', return_value=True), patch.object(p, 'scale_in_signal') as signal:
            signal.return_value = None
            self.assertFalse(v.held_buy_check(*args))
            signal.return_value = 'dip'
            self.assertFalse(v.held_buy_check(*args))
            signal.return_value = 'trend'
            self.assertTrue(v.held_buy_check(*args))

    def test_staged_add_uses_verified_fill_and_rechecks_latest_advice(self):
        from strategy_settings import DEFAULTS
        advice = {'id': 'adv1', 'time': NOW.isoformat(), 'decisions': [{'action': 'BUY', 'symbol': '005930'}],
                  'signals': {'005930': 0.7}, 'grounded': True, 'confirmed_by_previous_run': True}
        path = self.root / 'vibe-advice.json'
        path.write_text(json.dumps(advice))
        self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)',
                        ('initial', '005930', 'BUY', 10, 1000, (NOW - timedelta(minutes=6)).isoformat()))
        self.db.commit()
        args = (self.db, self.root, '005930', 10, 1000, 10, {'bidp': 1008, 'askp': 1009}, 'adv1', DEFAULTS,
                [{'stck_prpr': '1008'}, {'stck_prpr': '1007'}])
        with patch.object(v, 'datetime', Clock), patch.object(v, 'market_view', return_value={'코스피': {'regime': '상승장'}}), \
             patch.object(v, 'stop_symbol_index', return_value='코스피'), patch.object(v, 'setting', return_value=0.5), \
             patch.object(p, 'fresh_series', return_value=True):
            self.assertTrue(v.held_buy_check(*args))
            advice['decisions'] = [{'action': 'SELL', 'symbol': '005930'}]
            path.write_text(json.dumps(advice))
            self.assertFalse(v.held_buy_check(*args))
            advice['decisions'] = [{'action': 'BUY', 'symbol': '005930'}]
            advice['signals']['005930'] = 0.4
            path.write_text(json.dumps(advice))
            self.assertFalse(v.held_buy_check(*args))



class OversoldVibeTests(unittest.TestCase):
    """낙폭과대 반등 for vibe-trading: bought on vibe's raw score, with its own target/stop plan saved before sending."""
    def test_buys_on_score_and_saves_the_plan(self):
        import tempfile
        import research_store
        from pathlib import Path
        from unittest import mock
        from strategy_settings import DEFAULTS
        row = {'code': '005930', 'triggered': True, 'stabilized': True, 'target_pct': 1.5, 'target_price': 182700,
               'stop_price': 178304, 'drop_pct': -11.07, 'atr_multiple': 1.65, 'market_low_pct': -4.0}  # a market-wide fall
        cap = {'order': 3_000_000, 'daily_buy': 6_000_000, 'new_symbols': 5, 'loss': 300_000, 'symbol': 3_000_000, 'tranche': 1_000_000}
        with tempfile.TemporaryDirectory() as d, mock.patch.object(research_store, 'ROOT', Path(d)), \
             mock.patch('oversold.scan', return_value=[row]), \
             mock.patch('oversold.universe', return_value={'005930': '삼성전자'}), \
             mock.patch.object(v, 'quote_of', return_value={'askp': 180000}), \
             mock.patch.object(v, 'place', return_value=True) as place, mock.patch.object(v, 'journal_event') as events:
            low = {'scores': {'005930': {'score': 0.1}}}
            self.assertIsNone(v.oversold_buy(None, None, None, 'a', Path(d), '20261106', low, DEFAULTS, cap, {}, 10_000_000, 0, set(), set()))
            place.assert_not_called()
            brief = {'as_of': NOW.isoformat(), 'sources': {'news': {'status': 'ok', 'observed_at': NOW.isoformat()}}}
            digest = research_store.record_input('vibe', 'scores', {'context': {'premarket_context': brief}}, {})
            high = {'id': 'vibe-advice', 'input_digest': digest, 'scores': {'005930': {'score': 0.4}}}
            self.assertEqual(v.oversold_buy(None, None, None, 'a', Path(d), '20261106', high, DEFAULTS, cap, {}, 10_000_000, 0, set(), set()), 'buy')
            signal = [c for c in events.call_args_list if c.args[1] == 'oversold_signal'][-1].kwargs
            self.assertEqual((signal['advice_id'], signal['input_digest']), ('vibe-advice', digest))
            self.assertEqual(signal['premarket_audit']['status'], 'included_in_recorded_input')
            self.assertEqual(place.call_args.args[7], 5)  # 1,000,000 x 100% // 180,000
            self.assertEqual(place.call_args.args[-1], 'oversold')
            self.assertEqual(v.oversold_plans(Path(d))['005930']['target_price'], 182700)

    def test_v2_buys_half_once_a_day_then_the_second_half(self):
        from unittest import mock
        from strategy_settings import DEFAULTS
        row = {'code': '005930', 'drop_pct': -11.0, 'market': 'kospi', 'market_low_pct': -9.0, 'rel_drop': -2.0,
               'stop_price': 78000, 'stop_pct': 15.2, 'half_tp_pct': 8.0, 'target_pct': 15.0, 'hold_days': 3}
        gate = {'kospi': {'pass': True}, 'kosdaq': {'pass': False}}
        cap = {'order': 3_000_000, 'daily_buy': 6_000_000, 'new_symbols': 5, 'loss': 300_000, 'symbol': 3_000_000, 'tranche': 1_000_000}
        clock = type('C', (datetime,), {'at': NOW.replace(hour=14, minute=35), 'now': classmethod(lambda cls, tz=None: cls.at)})
        v._V2_SENT.clear()
        with tempfile.TemporaryDirectory() as d, mock.patch('oversold.v2_gate', return_value=gate), \
             mock.patch('oversold.v2_candidates', side_effect=lambda read, g, st, codes, held=(): [r for r in [row] if r['code'] not in held]), \
             mock.patch('oversold.universe', return_value={'005930': '삼성전자'}), \
             mock.patch.object(v, 'quote_of', return_value={'askp': 90000}), \
             mock.patch.object(v, 'place', return_value=False) as place, mock.patch.object(v, 'journal_event') as events, \
             mock.patch.object(v, 'datetime', clock):
            args = (None, None, None, 'a', Path(d), '20261112', DEFAULTS, cap, {}, 10_000_000, 0, set(), set())
            veto = {'scores': {'005930': {'score': -0.5}}}
            self.assertIsNone(v.oversold_v2_buy(*args, veto))  # vibe's own score vetoes it
            place.assert_not_called()
            v._V2_SENT.clear()
            self.assertIsNone(v.oversold_v2_buy(*args, {'scores': {'005930': {'score': 0.1}}}))  # the broker did not acknowledge
            self.assertEqual((place.call_count, place.call_args.args[7], place.call_args.args[-1]), (1, 5, 'oversold_v2'))  # 500,000 // 90,000
            plan = v.oversold_plans(Path(d))['005930']
            self.assertEqual((plan['half_price'], plan['target_price'], plan['entry_price'], plan['added']), (97200, 103500, 90000, False))
            self.assertIsNone(v.oversold_v2_buy(*args))
            self.assertEqual(place.call_count, 1)  # not sent again the same day
            self.assertEqual([c.kwargs.get('research') for c in events.call_args_list if c.args[1] == 'oversold_v2_signal'], ['veto', 'normal'])
            v._V2_SENT.clear()
            v.save_oversold_plans(Path(d), {})
            self.assertIsNone(v.oversold_v2_buy(*args, None))
            fallback = [c for c in events.call_args_list if c.args[1] == 'oversold_v2_signal'][-1]
            self.assertEqual((fallback.kwargs['research'], fallback.kwargs['fallback_reason']), ('normal', 'score_unavailable'))
            self.assertEqual(fallback.kwargs['premarket_audit']['status'], 'input_record_unavailable')
            # the next session, owned and lower than the first buy: the second half, the targets move to the new average
            clock.at = NOW.replace(hour=14, minute=35) + timedelta(days=1)
            v.save_oversold_plans(Path(d), {'005930': {**plan, 'opened': NOW.replace(hour=14, minute=35).isoformat()}})
            with mock.patch.object(v, 'quote_of', return_value={'askp': 86000}), mock.patch.object(p, 'sessions_held', return_value=1):
                v.oversold_v2_buy(None, None, None, 'a', Path(d), '20261113', DEFAULTS, cap, {'005930': 5}, 10_000_000, 0, {'005930'}, set())
                self.assertFalse(v.oversold_plans(Path(d))['005930']['added'])  # not sent: the first plan stays
                v._V2_SENT.clear()
                place.return_value = True
                v.oversold_v2_buy(None, None, None, 'a', Path(d), '20261113', DEFAULTS, cap, {'005930': 5}, 10_000_000, 0, {'005930'}, set())
            self.assertEqual((place.call_args.args[-1], place.call_args.args[7]), ('oversold_v2_add', 5))
            added = v.oversold_plans(Path(d))['005930']
            self.assertEqual((added['added'], added['half_price'], added['target_price']), (True, 95040, 101200))  # average 88,000
        plan = {'opened': datetime(2026, 11, 13, 14, 35, tzinfo=NOW.tzinfo).isoformat(), 'hold_days': 3, 'variant': 'v2'}
        with mock.patch.object(p, 'next_session_open', return_value=False), mock.patch.object(p, 'is_trading_day', side_effect=lambda d: d.weekday() < 5):
            self.assertTrue(v.may_carry(plan, datetime(2026, 11, 13).date()))  # a Friday v2 buy stays over the weekend
            self.assertFalse(v.may_carry({**plan, 'variant': None}, datetime(2026, 11, 13).date()))


class LimitsFromDashboardTests(unittest.TestCase):
    def test_limits_follow_the_saved_paper_account(self):
        from unittest import mock
        record = {'budget': {'first_buy_krw': 1_000_000, 'per_symbol_krw': 9_000_000},
                  'limits': {'order_max_krw': 5_000_000, 'daily_buy_krw': 20_000_000, 'daily_new_symbols': 7, 'daily_loss_krw': 400_000}}
        with mock.patch('accounts.load', return_value={'paper-shared': record}):
            self.assertEqual(v.limits(), {'order': 5_000_000, 'daily_buy': 20_000_000, 'new_symbols': 7, 'loss': 400_000, 'symbol': 9_000_000,
                                          'tranche': 1_000_000})


class IpoSlotVibeTests(unittest.TestCase):
    """신규주 슬롯 for vibe (user 2026-11-17): the Jev runner's new-listing conditions, a day cap and its own plan."""
    def test_buys_a_new_listing_saves_its_plan_and_respects_the_day_cap(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        from strategy_settings import DEFAULTS
        facts = {'name': '옵토닉스', 'listed': '20261117', 'sessions': 0, 'change_pct': 4.0, 'turnover_pct': 30.0}
        cap = {'order': 3_000_000, 'daily_buy': 6_000_000, 'new_symbols': 5, 'loss': 300_000, 'symbol': 3_000_000, 'tranche': 1_000_000}
        settings = {**DEFAULTS, 'ipo_stop_pct': 2.0, 'ipo_half_pct': 3.0, 'ipo_size_pct': 50}
        with tempfile.TemporaryDirectory() as d, \
             mock.patch('nh_paper_isolated.ipo_candidate', return_value=('493260', facts, [])), \
             mock.patch('nh_paper_isolated.ipo_entries_today', return_value=0), \
             mock.patch.object(v, 'quote_of', return_value={'askp': 26000}), \
             mock.patch.object(v, 'place', return_value=True) as place, mock.patch.object(v, 'journal_event'):
            self.assertEqual(v.ipo_buy(None, None, None, 'a', Path(d), '20261117', settings, cap, {}, 10_000_000, 0, set(), set()), 'buy')
            self.assertEqual(place.call_args.args[5:8], ('493260', 'BUY', 500_000 // 26000))
            self.assertEqual(place.call_args.args[-1], 'new_listing')
            plan = v.oversold_plans(Path(d))['493260']
            self.assertEqual((plan['variant'], plan['stop_price'], plan['target_price'], plan['hold_days']), ('ipo', 25480, 26780, 0))
            with mock.patch('nh_paper_isolated.ipo_entries_today', return_value=2):
                self.assertIsNone(v.ipo_buy(None, None, None, 'a', Path(d), '20261117', settings, cap, {}, 10_000_000, 0, set(), set()))
            off = {**settings, 'ipo_slot_on': 0}
            self.assertIsNone(v.ipo_buy(None, None, None, 'a', Path(d), '20261117', off, cap, {}, 10_000_000, 0, set(), set()))
            self.assertEqual(place.call_count, 1)


class JevFastTests(unittest.TestCase):
    """User decision 2026-11-17: Jev's 1-minute results also buy and sell for the vibe book."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.nhplug = Path(self.temp.name) / 'nhplug'
        self.root = self.nhplug / 'vibe-paper'
        (self.nhplug / 'shared-etf-paper').mkdir(parents=True); self.root.mkdir()
        self.db = p.connect_journal(self.root / 'orders.sqlite3', p.account_hash(ACCOUNT))
        self.addCleanup(self.temp.cleanup); self.addCleanup(self.db.close)

    def jev(self, at, choice, confidence):
        with (self.nhplug / 'shared-etf-paper' / 'events.jsonl').open('a') as f:
            f.write(json.dumps({'time': at.isoformat(), 'event': 'jev_shadow',
                                'jev': {'candidate': {'choice': choice, 'confidence': confidence}}}) + '\n')

    def test_latest_pick_is_fresh_and_not_wait(self):
        self.jev(NOW - timedelta(seconds=30), 'INV', 0.7)
        self.assertEqual(v.latest_jev_pick(self.nhplug, NOW), ((NOW - timedelta(seconds=30)).isoformat(), '114800', 0.7))
        self.assertIsNone(v.latest_jev_pick(self.nhplug, NOW + timedelta(minutes=3)))  # older than 2 minutes
        self.jev(NOW, 'WAIT', 0.9)
        self.assertIsNone(v.latest_jev_pick(self.nhplug, NOW))  # the latest view is WAIT

    NEWS = [{'title': '코스피 상승', 'published': NOW.isoformat(), 'source': 'bing_news_rss', 'topic': 'market', 'publisher': 'P'}]

    def buy(self, *, owned=None, signal=None, votes=None, confidence=0.7, news=None):
        import vibe_advisor
        self.jev(NOW - timedelta(seconds=20), '005930', confidence)
        advice = {'signals': {'005930': signal}} if signal is not None else None
        cap = {'order': 3_000_000, 'daily_buy': 6_000_000, 'new_symbols': 5, 'loss': 300_000, 'symbol': 3_000_000, 'tranche': 1_000_000}
        votes = votes or {'vote': 1, 'vs_vwap_pct': 0.5, 'range_position_pct': 50}
        with patch.object(v, 'datetime', Clock), patch.object(v, 'ENTRY_CUTOFF', '14:30'), \
             patch.object(vibe_advisor, 'technical_votes', return_value={'005930': votes}), \
             patch.object(vibe_advisor, 'STRENGTH_KRW', 1_000_000), patch.object(v, 'journal_event') as events, \
             patch.object(v, 'quote_of', return_value={'askp': 50_000, 'bidp': 49_950}), \
             patch('etf_news.headlines7', **({'side_effect': news} if isinstance(news, Exception) else
                                             {'return_value': news or self.NEWS})), \
             patch.object(v, 'place', return_value=True) as place:
            status = v.jev_fast_buy(self.db, object(), object(), ACCOUNT, self.root, '20261112', advice, {'entry_confidence': 0.5},
                                    cap, owned or {}, 10_000_000, 0, set(), set(), None)
        return status, place, events

    def test_buys_a_fresh_confident_pick_once(self):
        status, place, events = self.buy(signal=0.1)
        self.assertEqual(status, 'buy')
        self.assertEqual(place.call_args.args[5:8], ('005930', 'BUY', 20))  # 1,000,000 // 50,000
        self.assertEqual(place.call_args.kwargs['source'], 'jev_fast')
        self.assertEqual(place.call_args.kwargs['decision_evidence']['news'], self.NEWS)  # headlines it bought on
        status, place, _ = self.buy(signal=0.1)  # the same Jev pick is judged once
        self.assertIsNone(status); place.assert_not_called()

    def test_vibe_veto_chase_low_confidence_and_held_block(self):
        for kwargs, reason in ((dict(signal=-0.2), 'vibe_disagrees'),
                               (dict(votes={'vote': 1, 'vs_vwap_pct': 3.4, 'range_position_pct': 60}), 'above_vwap'),
                               (dict(votes={'vote': 1, 'vs_vwap_pct': 0.5}), 'chase_unmeasured'),
                               (dict(votes={'vote': -1, 'vs_vwap_pct': 0.5, 'range_position_pct': 40}), 'technical_short_or_unavailable'),
                               (dict(confidence=0.4), 'low_confidence'),
                               (dict(news=OSError('HTTP 429')), 'news_unavailable'),
                               (dict(news=ValueError('no eligible recent news')), 'news_unavailable'),
                               (dict(owned={'005930': 5}), 'already_held')):
            with self.subTest(reason=reason):
                self.db.execute('DELETE FROM jev_fast') if self.db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='jev_fast'").fetchone() else None
                self.db.commit()
                (self.nhplug / 'shared-etf-paper' / 'events.jsonl').unlink(missing_ok=True)
                status, place, events = self.buy(**kwargs)
                self.assertIsNone(status); place.assert_not_called()
                self.assertEqual(events.call_args.kwargs['reason'], reason)

    def test_event_jev_sell_must_be_fresh_and_match_the_holding(self):
        import sqlite3
        directory = self.nhplug / 'vibe-event-analysis'; directory.mkdir()
        def record(stance='exit', status='ok', qty=7, completed=NOW - timedelta(seconds=30)):
            with sqlite3.connect(directory / 'events.sqlite3') as db:
                db.execute('CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, day TEXT, symbol TEXT, time TEXT, status TEXT, result TEXT)')
                db.execute('DELETE FROM attempts')
                db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?)', ('a', NOW.date().isoformat(), '280360', completed.isoformat(), 'completed',
                           json.dumps({'completed_at': completed.isoformat(), 'analysis': {'stance': stance, 'status': status},
                                       'event': {'symbol': '280360', 'qty': qty, 'position_id': 'p', 'latest_fill_id': 'f'},
                                       'model_evidence': {'choice': 'SELL', 'confidence': 0.8}})))
        with patch('vibe_event_advisor.current_position', return_value=True):
            record()
            self.assertEqual(v.jev_event_exit(self.root, '280360', 7, NOW)['confidence'], 0.8)
            self.assertIsNone(v.jev_event_exit(self.root, '280360', 6, NOW))  # another quantity: another position
            self.assertIsNone(v.jev_event_exit(self.root, '280360', 7, NOW + timedelta(minutes=3)))  # stale
            record(stance='hold')
            self.assertIsNone(v.jev_event_exit(self.root, '280360', 7, NOW))
            record(status='below_sell_threshold')
            self.assertIsNone(v.jev_event_exit(self.root, '280360', 7, NOW))
        record()
        with patch('vibe_event_advisor.current_position', return_value=False):  # the live book no longer matches
            self.assertIsNone(v.jev_event_exit(self.root, '280360', 7, NOW))
