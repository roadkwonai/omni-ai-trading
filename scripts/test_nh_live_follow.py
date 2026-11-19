import json
import sqlite3
import tempfile
from decimal import Decimal
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

import nh_live_follow as f
from src.tools.nhplug_live_guard import LiveOrderGuard

KST = ZoneInfo('Asia/Seoul')
NOW = datetime(2026, 11, 6, 11, 0, tzinfo=KST)
A = '12345678901'
BASE = dict(target={}, buyable=set(), owned={}, held={}, own_pending=False, broker_open=False, halted=False, exits={},
            buy_paused=False, clock='11:00')
RECORD = {'kind': 'live', 'engine': 'jev', 'auto': True, 'budget': {'order_price_max_krw': 300_000, 'capital_krw': 10_000_000,
                                                                     'daily_loss_krw': 20_000}, 'rules': {}}


def decide(**kwargs):
    return f.decide(**{**BASE, **kwargs})


class Decide(unittest.TestCase):
    @patch.object(f, 'buy_window_end', return_value='14:30')
    def test_buys_only_what_the_paper_runner_bought_inside_the_window(self, _cutoff):
        self.assertEqual(decide(target={'005930': 1}, buyable={'005930'})['action'], 'BUY')
        self.assertEqual(decide(target={'005930': 1})['action'], 'WAIT')
        for blocked in ({'clock': '14:30'}, {'clock': '08:59'}, {'buy_paused': True}):
            self.assertEqual(decide(target={'005930': 1}, buyable={'005930'}, **blocked)['action'], 'WAIT', blocked)
        self.assertEqual(decide(target={'005930': 1}, buyable={'005930'}, halted=True),
                         {'action': 'HOLD', 'reason': 'daily loss latch'})

    @patch.object(f, 'buy_window_end', return_value='14:50')
    def test_configured_buy_cutoff_applies_at_the_boundary(self, _cutoff):
        self.assertEqual(decide(target={'005930': 1}, buyable={'005930'}, clock='14:49')['action'], 'BUY')
        self.assertEqual(decide(target={'005930': 1}, buyable={'005930'}, clock='14:50')['action'], 'WAIT')

    @patch.object(f, 'buy_window_end', return_value='14:30')
    def test_v2_positions_may_still_buy_after_the_cutoff_until_the_v2_window_ends(self, _cutoff):
        # user 2026-11-18: the live mirror follows 선별 v2 buys (14:30~14:55) the same day
        late = dict(late={'005930'}, late_until='14:55')
        self.assertEqual(decide(target={'005930': 1}, buyable={'005930'}, clock='14:52', **late)['action'], 'BUY')
        self.assertEqual(decide(target={'005930': 1}, buyable={'005930'}, clock='14:52')['action'], 'WAIT')
        self.assertEqual(decide(target={'005930': 1}, buyable={'005930'}, clock='14:55', **late)['action'], 'WAIT')
        plan = decide(target={'005930': 1, '000660': 1}, buyable={'005930', '000660'}, clock='14:52', **late)
        self.assertEqual((plan['action'], plan['symbol']), ('BUY', '005930'))  # an ordinary pick still waits
        self.assertEqual(decide(target={'000660': 1}, buyable={'000660'}, clock='14:52', **late)['action'], 'WAIT')

    def test_sells_come_first_and_only_ledger_shares_are_sold(self):
        plan = decide(target={'005930': 1}, buyable={'005930'}, owned={'069500': 1}, held={'069500': 1, '035420': 5})
        self.assertEqual((plan['action'], plan['symbol'], plan['reason']), ('SELL', '069500', 'paper runner holds less'))
        self.assertEqual(decide(owned={}, held={'035420': 5})['action'], 'HOLD')  # the user's own 035420 is never touched
        self.assertEqual(decide(target={'005930': 2}, owned={'005930': 2}, held={'005930': 2})['action'], 'HOLD')

    def test_daily_loss_and_symbol_stop_flatten(self):
        held = {'005930': 1, '069500': 1}
        plan = decide(target=held, owned=held, held=held, halted=True)
        self.assertEqual((plan['action'], plan['reason']), ('SELL', 'daily loss latch'))
        plan = decide(target=held, owned=held, held=held, exits={'069500': '2% stop from average price'})
        self.assertEqual((plan['action'], plan['symbol']), ('SELL', '069500'))
        self.assertEqual(decide(target={'069500': 1}, buyable={'069500'}, exits={'069500': 'stop'})['action'], 'HOLD')

    def test_open_or_unreconciled_state_blocks(self):
        self.assertEqual(decide(own_pending=True, target={'005930': 1}, buyable={'005930'})['action'], 'RECONCILE')
        self.assertEqual(decide(broker_open=True, owned={'005930': 1}, held={'005930': 1})['action'], 'BLOCK')
        self.assertEqual(decide(owned={'005930': 2}, held={'005930': 1})['reason'], 'ledger holds more than the broker balance')


def paper(root, fills):
    """The paper ledger with `fills` (idempotent: a later call with more fills appends the new ones)."""
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / 'orders.sqlite3')
    db.execute('CREATE TABLE IF NOT EXISTS fills (order_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, side TEXT NOT NULL, quantity INTEGER NOT NULL, price REAL NOT NULL, time TEXT NOT NULL)')
    for i, (symbol, side, qty, price, clock) in enumerate(fills):
        db.execute('INSERT OR IGNORE INTO fills VALUES (?,?,?,?,?,?)', (str(i), symbol, side, qty, price, f'2026-11-{clock}+09:00'))
    db.commit()
    db.close()
    return root


def own(d, code, qty, day='20261106'):
    """The mirror's guard ledger holds `qty` shares of `code` (an adoption dated `day`)."""
    guard = LiveOrderGuard(A, Path(d), symbols=(code,))
    with guard._db() as db:
        db.execute('DELETE FROM live_adoptions WHERE symbol=?', (code,))
        db.execute('INSERT INTO live_adoptions VALUES (?,?,?,?)', (guard.scope, day, code, qty))


def test_ledger_owned_reads_symbols_beyond_the_default_guard():
    """11/18: the mirror bought 043260; the dashboard's default guard (114800 only) refused to read it."""
    with tempfile.TemporaryDirectory() as d:
        own(d, '043260', 1)
        own(d, '114800', 3)
        guard, owned = f.ledger_owned(A, Path(d))
        assert owned == {'043260': 1, '114800': 3}
        with pytest.raises(Exception):
            LiveOrderGuard(A, Path(d)).owned('043260')  # what the dashboard used to do
        assert f.ledger_owned(A, Path(d) / 'empty')[1] == {}


class Remainder(unittest.TestCase):
    """User 2026-11-18: live shares the paper does not hold must get Jev's holding view, quickly and reliably."""
    NOW = datetime(2026, 11, 6, 11, 0, tzinfo=KST)

    def ask(self, d, *, recorded, busy=(False,), answer=None):
        from etf_universe import SYMBOLS
        bars = [{'bsop_time': '105900'}]
        fetched = []

        def minute_bars(read, code):
            fetched.append(code)
            return {'status': 'ok', 'data': {}}
        flags = iter(busy)
        with patch.object(f, 'recent_bars', return_value={c: (bars, self.NOW) for c in recorded}), \
                patch('nh_paper_isolated.minute_bars', side_effect=minute_bars), \
                patch('nh_etf_paper_runner.valid_bars', return_value=bars), \
                patch('nh_paper_isolated.fresh_etf7_bars', return_value=True), \
                patch('nh_paper_isolated.market_view', return_value={}), patch('etf_news.headlines7', return_value=[]), \
                patch('jev_activity.regular_request_busy', side_effect=lambda: next(flags, busy[-1])), \
                patch('vibe_event_jev._invoke', return_value={'holding': answer or {'choice': 'SELL', 'confidence': 0.8},
                                                              'response_time': 't'}) as invoke:
            view = f.remainder_jev_view(MagicMock(), Path(d), '043260', Decimal(26000), Decimal(25900), self.NOW,
                                        sleep=lambda s: None)
        return view, fetched, invoke, set(SYMBOLS.values())

    def test_bars_the_runner_no_longer_records_are_read_from_nh(self):
        """11/18: after the paper sold 043260 out, no recorded bars meant no Jev view at all."""
        from etf_universe import SYMBOLS
        with tempfile.TemporaryDirectory() as d:
            (view, why), fetched, invoke, _ = self.ask(d, recorded=set(SYMBOLS.values()))
            self.assertEqual((view['choice'], why, fetched), ('SELL', None, ['043260']))
            payload = invoke.call_args.args[0]
            self.assertEqual((payload['symbol'], payload['avg'], payload['bid'], set(payload['bars'])),
                             ('043260', 26000.0, 25900.0, set(SYMBOLS) | {'043260'}))

    def test_jev_main_busy_is_waited_for_not_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            (view, why), _, invoke, etf = self.ask(d, recorded={'043260'}, busy=(True, True, False))
            self.assertEqual((view['choice'], why), ('SELL', None))
            (view, why), _, invoke, _ = self.ask(d, recorded={'043260'}, busy=(True,))
            self.assertEqual((view, why), (None, 'jev_main_busy'))
            invoke.assert_not_called()

    def test_a_kept_hand_buy_sells_on_jev_sell_and_alerts_once_without_a_view(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            record = {'hash': 'h' * 16}
            f.manual_path(Path(d), record).parent.mkdir(parents=True, exist_ok=True)
            f.save_manual(Path(d), record, {'offsets': {'043260': 2}, 'anchors': {}, 'since': {}, 'left': {}}, self.NOW)
            manual = {'offsets': {'043260': 2}, 'left': {}}
            alerts = []
            quotes = {'043260': (Decimal(25900), Decimal(26000))}
            for _ in range(2):  # Jev cannot be asked: kept, logged, alerted once a day
                target = {'043260': 2}
                out = f.review_remainders(None, Path(d), 'live-1', record, target, {}, {'043260': 2}, {'043260': '26000'},
                                          quotes, manual, self.NOW, view=lambda *a: (None, 'bars_stale'),
                                          notify=lambda *a: alerts.append(a))
                self.assertEqual((target, out['043260']['why']), ({'043260': 2}, 'bars_stale'))
            self.assertEqual(len(alerts), 1)

            def broken(*a):
                raise ConnectionError('NH down')
            target = {'043260': 2}
            out = f.review_remainders(None, Path(d), 'live-1', record, target, {}, {'043260': 2}, {'043260': 26000},
                                      quotes, manual, self.NOW, view=broken, notify=lambda *a: None)
            self.assertEqual((target, out['043260']['why']), ({'043260': 2}, 'error:ConnectionError'))  # not raised
            hold = lambda *a: ({'choice': 'SELL', 'confidence': 0.5}, None)
            target = {'043260': 2}
            out = f.review_remainders(None, Path(d), 'live-1', record, target, {}, {'043260': 2}, {'043260': 26000}, quotes,
                                      manual, self.NOW, view=hold)
            self.assertNotIn('action', out['043260'])  # below sell_confidence
            sell = lambda *a: ({'choice': 'SELL', 'confidence': 0.75}, None)
            out = f.review_remainders(None, Path(d), 'live-1', record, target, {}, {'043260': 2}, {'043260': 26000}, quotes,
                                      manual, self.NOW, view=sell)
            self.assertEqual((out['043260']['action'], manual['offsets']), ('sell: jev SELL 0.75', {}))
            self.assertEqual(json.loads(f.manual_path(Path(d), record).read_text())['offsets'], {})
            self.assertEqual(f.review_remainders(None, Path(d), 'live-1', record, {'043260': 1}, {'043260': 1},
                                                 {'043260': 1}, {}, quotes, manual, self.NOW, view=sell), {})  # paper holds it


class Mirror(unittest.TestCase):
    def test_small_paper_rebuy_is_not_reduced_by_earlier_partial_sale(self):
        with tempfile.TemporaryDirectory() as d:
            root = paper(Path(d), [('114800', 'BUY', 100, 1000, '06T10:00:00'),
                                   ('114800', 'SELL', 50, 1020, '06T10:10:00'),
                                   ('114800', 'BUY', 1, 1000, '06T10:20:00')])
            sizing = f.tranche_shares({'budget': {'order_price_max_krw': 300_000},
                                       'rules': {'sizing': 'amount', 'tranche_krw': 100_000}})
            # 100 -> 50 after the half sale; the 1-share rebuy adds its own proportional share, not a full tranche
            self.assertEqual(f.mirror_target(f.paper_book(root / 'orders.sqlite3', sizing)), {'114800': 51})

    def test_rebuy_after_partial_sale_changes_target_without_duplicate_buy(self):
        with tempfile.TemporaryDirectory() as d:
            root = paper(Path(d), [('114800', 'BUY', 100, 1000, '06T10:00:00'),
                                   ('114800', 'SELL', 50, 1020, '06T10:10:00'),
                                   ('114800', 'BUY', 100, 1000, '06T10:20:00')])
            sizing = f.tranche_shares({'budget': {'order_price_max_krw': 300_000},
                                       'rules': {'sizing': 'amount', 'tranche_krw': 100_000}})
            book = f.paper_book(root / 'orders.sqlite3', sizing)
            target = f.mirror_target(book)
            self.assertEqual(target, {'114800': 150})
            self.assertEqual(decide(target=target, owned=target, held=target)['action'], 'HOLD')
            self.assertEqual(decide(target=target, owned={'114800': 50}, held={'114800': 50},
                                    buyable={'114800'})['action'], 'BUY')

    def test_each_paper_tranche_is_one_live_share(self):
        with tempfile.TemporaryDirectory() as d:
            root = paper(Path(d), [('005930', 'BUY', 4, 212000, '06T10:54:00'), ('005930', 'BUY', 5, 210000, '06T12:38:00'),
                                   ('069500', 'BUY', 30, 33000, '05T10:00:00'), ('069500', 'SELL', 30, 33500, '05T11:00:00'),
                                   ('069500', 'BUY', 31, 32000, '06T10:10:00'), ('069500', 'SELL', 15, 32500, '06T10:30:00'),
                                   ('122630', 'BUY', 40, 25000, '06T09:30:00'), ('122630', 'SELL', 30, 25500, '06T10:00:00')])
            book = f.paper_book(root / 'orders.sqlite3')
            self.assertEqual((book['005930']['tranches'], book['069500']['tranches']), (2, 1))  # a closed cycle restarts
            self.assertEqual(f.mirror_target(book), {'005930': 2, '069500': 1, '122630': 1})  # partial exits keep >= 1

    def test_paper_sale_beyond_holdings_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = paper(Path(d), [('005930', 'SELL', 1, 1, '06T10:00:00')])
            with self.assertRaises(ValueError):
                f.paper_book(root / 'orders.sqlite3')

    def test_settings_and_new_fills_invalidate_buy_but_not_protective_sell(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = paper(root / 'shared-etf-paper', [('114800', 'BUY', 100, 1000, '06T10:00:00')]) / 'orders.sqlite3'
            record = {'kind': 'live', 'engine': 'jev', 'auto': True, 'live_orders': True, 'hash': 'account',
                      'budget': {'order_price_max_krw': 300_000}, 'rules': {}, 'limits': {}}
            book = f.paper_book(source, f.tranche_shares(record))
            with patch('accounts.live_record', return_value=record) as live, \
                 patch('nh_live_buy_control.buy_allowed', return_value=True), \
                 patch.object(f, 'paper_source', return_value=source):
                self.assertTrue(f.follow_order_current(A, root, record, 'BUY', source, book))
                live.return_value = {**record, 'rules': {'tranche_krw': 200_000}}
                self.assertFalse(f.follow_order_current(A, root, record, 'BUY', source, book))
                self.assertFalse(f.follow_order_current(A, root, record, 'SELL', source, book))
                self.assertTrue(f.follow_order_current(A, root, record, 'SELL', source, book, protective=True))
                live.return_value = {**record, 'live_orders': False}
                self.assertFalse(f.follow_order_current(A, root, record, 'BUY', source, book))
                self.assertFalse(f.follow_order_current(A, root, record, 'SELL', source, book, protective=True))
                live.return_value = record
                with sqlite3.connect(source) as db:
                    db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('new', '114800', 'SELL', 50, 1020, '2026-11-06T10:10:00+09:00'))
                self.assertFalse(f.follow_order_current(A, root, record, 'BUY', source, book))
                self.assertFalse(f.follow_order_current(A, root, record, 'SELL', source, book))
                self.assertTrue(f.follow_order_current(A, root, record, 'SELL', source, book, protective=True))


def snapshots(holdings=(), loss=0):
    ok = {'rsp_cd': '00166', 'rsp_msg': '조회가 완료되었습니다.'}
    return {'accounts': {'rsp_cd': '00000', 'Output_0': [{'acct_no': A, 'acct_type': '01'}]},
            'history': {**ok, '_pagination_complete': True, '_account': A, '_day': '20261106', 'Output_0': []},
            'balance': {**ok, '_pagination_complete': True, '_account': A,
                        'Output_0': {'orr_pbl_amt1': 1_000_000, 'tot_aet_amt': 1_000_000, 'tot_byn_amt': 0},
                        'Output_1': [{'iem_cd': c, 'rsdl_qty': q, 'phs_pr': p} for c, q, p in holdings]},
            'pnl': {**ok, 'Output_0': {'sby_pls_amt': 0, 'sdr_xps': 0, 'eal_pls_amt': -loss}}}


def reader(bid, ask):
    r = MagicMock()
    r.call_tool.side_effect = lambda tool, params: {'status': 'ok', 'data': {'Output_0': {
        'iem_cd': params['stock_code'], 'bidp': bid, 'askp': ask}}}
    return r


class Step(unittest.TestCase):
    def run_step(self, d, *, fills, holdings=(), bid=50_000, ask=50_100, execute=False, engine='jev', rules=None, evidence=False,
                 may_cancel=None, loss=0, now=NOW, jev=(None, 'not asked in tests'), send=None, rd=None):
        record = {'kind': 'live', 'engine': engine, 'auto': True, 'budget': RECORD['budget'], 'rules': rules or {}}
        paper(Path(d) / 'shared-etf-paper', fills)
        with patch('nh_live_shadow.effective_rules', return_value={'stop_loss_pct': 2.0}), \
                patch('nh_live_broker.collect_snapshots', return_value=snapshots(holdings, loss)), \
                patch('accounts.live_price_cap', return_value=300_000), \
                patch('src.live.halt.halt_flag_set', return_value=False), \
                patch.object(f, 'engine_supports', return_value=evidence), \
                patch.object(f, 'remainder_jev_view', return_value=jev) as self.view, patch.object(f, 'mac_notify'), \
                patch('nh_guarded_live.execute', return_value='intent-1', side_effect=send) as sent:
            plan = f.step(rd or reader(bid, ask), A, {}, record, execute_orders=execute, root=Path(d), now=now, key='live-1',
                          may_cancel=may_cancel)
        return plan, sent

    def test_dry_run_mirrors_a_paper_buy_and_execute_orders_one_share(self):
        fills = [('005930', 'BUY', 4, 50_000, '06T10:58:00')]
        with tempfile.TemporaryDirectory() as d:
            plan, send = self.run_step(d, fills=fills)
            self.assertEqual((plan['action'], plan['symbol'], plan['execution'], plan['price']), ('BUY', '005930', 'DRY_RUN', 50_100))
            self.assertEqual((plan['paper'], plan['target']), ({'005930': 4}, {'005930': 1}))
            send.assert_not_called()
            plan, send = self.run_step(d, fills=fills, execute=True)
            self.assertEqual(plan['execution'], 'ACKNOWLEDGED_NOT_FILLED')
            self.assertTrue(callable(send.call_args.kwargs['pre_reserve_check']))
            op, fields = send.call_args.args[3], send.call_args.args[4]
            self.assertEqual((op, fields['iem_cd'], fields['orr_qty'], fields['orr_pr']), ('krstockOrderCashBuy', '005930', 1, 50_100))

    def test_a_v2_paper_buy_after_the_cutoff_is_followed_the_same_day(self):
        fills = [('005930', 'BUY', 4, 50_000, '06T14:51:00')]
        late = NOW.replace(hour=14, minute=52)
        v2 = {'005930': {'profile': 'oversold', 'stop_pct': 2.0, 'half_pct': 8.0, 'all_pct': 15.0,
                         'target_basis': {'variant': 'v2'}}}
        with patch.object(f, 'buy_window_end', return_value='14:30'):
            with tempfile.TemporaryDirectory() as d:
                paper(Path(d) / 'shared-etf-paper', fills)
                (Path(d) / 'shared-etf-paper' / 'exit-plans.json').write_text(json.dumps(v2))
                plan, _ = self.run_step(d, fills=fills, now=late)
                self.assertEqual((plan['action'], plan['symbol']), ('BUY', '005930'))
                plan, _ = self.run_step(d, fills=fills, now=NOW.replace(hour=14, minute=55))
                self.assertEqual(plan['action'], 'WAIT')
            with tempfile.TemporaryDirectory() as d:  # an ordinary plan keeps the cutoff
                plan, _ = self.run_step(d, fills=fills, now=late)
                self.assertEqual(plan['action'], 'WAIT')

    def test_chase_limit_evidence_old_entry_and_price_cap(self):
        with tempfile.TemporaryDirectory() as d:  # ask 0.5% above the paper fill: inside the default 1% chase limit
            plan, _ = self.run_step(d, fills=[('005930', 'BUY', 4, 49_850, '06T10:58:00')])
            self.assertEqual((plan['action'], plan['buy_basis']['005930']), ('BUY', 'within 1% of the paper fill'))
        with tempfile.TemporaryDirectory() as d:  # 1.5% above: waits without evidence ...
            plan, _ = self.run_step(d, fills=[('005930', 'BUY', 4, 49_360, '06T10:58:00')], rules={'chase_pct': 1.0})
            self.assertEqual(plan['action'], 'WAIT')
            with patch.object(f, 'first_target', return_value=Decimal('51000')):  # ... buys while Jev still picks it below the first target
                plan, _ = self.run_step(d, fills=[('005930', 'BUY', 4, 49_360, '06T10:58:00')], evidence=True)
            self.assertEqual((plan['action'], plan['buy_basis']['005930']), ('BUY', 'engine still picks it, below the paper first target'))
            with patch.object(f, 'first_target', return_value=Decimal('50000')):  # already past the paper's first target: no
                plan, _ = self.run_step(d, fills=[('005930', 'BUY', 4, 49_360, '06T10:58:00')], evidence=True)
            self.assertEqual(plan['action'], 'WAIT')
        with tempfile.TemporaryDirectory() as d:  # yesterday's paper entry is not bought today
            plan, send = self.run_step(d, fills=[('005930', 'BUY', 4, 50_100, '05T10:58:00')], execute=True)
            self.assertEqual(plan['action'], 'WAIT')
        with tempfile.TemporaryDirectory() as d:
            plan, send = self.run_step(d, fills=[('005930', 'BUY', 1, 300_100, '06T10:58:00')], bid=300_000, ask=300_100, execute=True)
            self.assertEqual(plan['execution_reason'], 'above the account price cap')
            send.assert_not_called()

    def test_amount_sizing_buys_several_shares_in_one_order_without_the_share_cap(self):
        rules = {'sizing': 'amount', 'tranche_krw': 200_000, 'max_order_qty': 3}
        # 200,000 at 50,000 = 4 shares, at 50,050 = 3.996: 7.996 -> 8, in one order (amount sizing ignores max_order_qty)
        fills = [('005930', 'BUY', 4, 50_000, '06T10:30:00'), ('005930', 'BUY', 4, 50_050, '06T10:58:00')]
        with tempfile.TemporaryDirectory() as d:
            plan, send = self.run_step(d, fills=fills, rules=rules, execute=True)
            self.assertEqual((plan['target'], plan['qty']), ({'005930': 8}, 8))
            self.assertEqual(send.call_args.args[4]['orr_qty'], 8)
        with tempfile.TemporaryDirectory() as d:  # share sizing keeps the account's share cap
            plan, send = self.run_step(d, fills=fills * 3, rules={'sizing': 'share', 'max_order_qty': 3}, execute=True)
            self.assertEqual((plan['target'], plan['qty']), ({'005930': 6}, 3))

    def test_sell_follows_full_mirrored_gap_not_buy_quantity_limit(self):
        with tempfile.TemporaryDirectory() as d:
            g = LiveOrderGuard(A, Path(d), symbols=('069500',))
            with g._db() as db:
                db.execute('INSERT INTO live_adoptions VALUES (?,?,?,?)', (g.scope, '20261106', '069500', 1500))
            fills = [('069500', 'BUY', 1500, 1000, '06T10:00:00'),
                     ('069500', 'SELL', 1500, 1010, '06T10:30:00')]
            plan, send = self.run_step(d, fills=fills, holdings=[('069500', 1500, 1000)], bid=1000, ask=1001,
                                       execute=True, rules={'max_order_qty': 3})
            self.assertEqual((plan['action'], plan['qty']), ('SELL', 1500))
            self.assertEqual(send.call_args.args[4]['orr_qty'], 1500)

    def test_older_paper_fill_can_resize_existing_live_holding_only(self):
        fills = [('005930', 'BUY', 4, 50_000, '05T10:30:00')]
        rules = {'sizing': 'amount', 'tranche_krw': 100_000, 'max_order_qty': 100}
        with tempfile.TemporaryDirectory() as d:
            g = LiveOrderGuard(A, Path(d), symbols=('005930',))
            with g._db() as db:
                db.execute('INSERT INTO live_adoptions VALUES (?,?,?,?)', (g.scope, '20261105', '005930', 1))
            plan, _ = self.run_step(d, fills=fills, holdings=[('005930', 1, 50_000)], rules=rules)
            self.assertEqual((plan['action'], plan['qty'], plan['target']['005930']), ('BUY', 1, 2))
        with tempfile.TemporaryDirectory() as d:
            plan, send = self.run_step(d, fills=fills, rules=rules, execute=True)
            self.assertEqual(plan['action'], 'WAIT')
            send.assert_not_called()

    def test_paper_exit_sells_and_engine_mismatch_buys_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            g = LiveOrderGuard(A, Path(d), symbols=('005930',))
            with g._db() as db:
                db.execute('INSERT INTO live_adoptions VALUES (?,?,?,?)', (g.scope, '20261106', '005930', 1))
            fills = [('005930', 'BUY', 4, 50_000, '06T10:00:00'), ('005930', 'SELL', 4, 50_500, '06T10:59:00')]
            plan, _ = self.run_step(d, fills=fills, holdings=[('005930', 1, 50_000)])
            self.assertEqual((plan['action'], plan['symbol'], plan['price']), ('SELL', '005930', 50_000))
        with tempfile.TemporaryDirectory() as d:  # an engine without a paper ledger mirrors nothing
            plan, _ = self.run_step(d, fills=[('005930', 'BUY', 4, 50_100, '06T10:58:00')], engine='longterm')
            self.assertEqual((plan['action'], plan['target'], plan['warning']), ('HOLD', {}, 'no paper ledger for engine longterm'))

    def test_hand_buys_and_quantity_sells_move_the_target_without_being_undone(self):
        """User 2026-11-17: a hand buy is bought through the guard and kept; a hand sell of some shares is not re-bought."""
        import json
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'live-manual-buys-live-1.json').write_text(json.dumps({'005930': 2}))
            plan, _ = self.run_step(d, fills=[])
            self.assertEqual((plan['action'], plan['symbol'], plan['qty'], plan['buy_basis']['005930']),
                             ('BUY', '005930', 2, 'manual buy request'))
            self.assertFalse((Path(d) / 'live-manual-buys-live-1.json').exists())
            g = LiveOrderGuard(A, Path(d), symbols=('005930',))
            with g._db() as db:
                db.execute('INSERT INTO live_adoptions VALUES (?,?,?,?)', (g.scope, '20261106', '005930', 2))
            plan, _ = self.run_step(d, fills=[], holdings=[('005930', 2, 50_000)])
            self.assertEqual((plan['action'], plan['target']), ('HOLD', {'005930': 2}))  # kept, not sold back
        with tempfile.TemporaryDirectory() as d:
            fills = [('005930', 'BUY', 4, 50_000, '06T10:58:00')]
            g = LiveOrderGuard(A, Path(d), symbols=('005930',))
            with g._db() as db:
                db.execute('INSERT INTO live_adoptions VALUES (?,?,?,?)', (g.scope, '20261106', '005930', 3))
            (Path(d) / 'live-exit-requests-live-1.json').write_text(json.dumps({'005930': 1}))
            plan, _ = self.run_step(d, fills=fills, holdings=[('005930', 3, 50_000)], rules={'sizing': 'amount', 'tranche_krw': 150_000})
            self.assertEqual((plan['action'], plan['qty'], plan['target']['005930']), ('SELL', 1, 2))
            with g._db() as db:  # the hand sale filled: the mirror now owns 2
                db.execute('UPDATE live_adoptions SET qty=2 WHERE symbol=?', ('005930',))
            plan, _ = self.run_step(d, fills=fills, holdings=[('005930', 2, 50_000)], rules={'sizing': 'amount', 'tranche_krw': 150_000})
            self.assertEqual((plan['action'], plan['target']['005930']), ('HOLD', 2))  # the cut is not bought back

    def test_the_live_account_sells_out_when_the_paper_sells_out(self):
        """User decision 2026-11-18 (replacing 11/17's kept remainder): the paper sold 043260 out, so the live share goes too."""
        fills = [('005930', 'BUY', 4, 50_000, '06T10:00:00'), ('005930', 'SELL', 4, 49_000, '06T10:59:00')]
        with tempfile.TemporaryDirectory() as d:
            own(d, '005930', 1)
            plan, _ = self.run_step(d, fills=fills, holdings=[('005930', 1, 50_000)])
            self.assertEqual((plan['action'], plan['symbol'], plan['qty']), ('SELL', '005930', 1))
            self.assertNotIn('kept', plan)

    def test_a_carried_position_stays_mirrored_then_partial_exit_and_live_stop_stay_guarded(self):
        import nh_live_shadow
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = paper(root / 'shared-etf-paper', [('005930', 'BUY', 100, 1000, '05T10:00:00'),
                                                      ('005930', 'SELL', 50, 1020, '05T14:00:00')]) / 'orders.sqlite3'
            guard = LiveOrderGuard(A, root, symbols=('005930',))
            with guard._db() as db:
                db.execute('INSERT INTO live_adoptions VALUES (?,?,?,?)', (guard.scope, '20261105', '005930', 50))
            rules = {'sizing': 'amount', 'tranche_krw': 100_000}
            sizing = f.tranche_shares({'budget': {'order_price_max_krw': 300_000}, 'rules': rules})
            self.assertEqual(f.mirror_target(f.paper_book(source, sizing)), {'005930': 50})
            shadow = nh_live_shadow.ledger_shadow(source, '2026-11-06', [], 1_000_000, 300_000, shares_for=sizing)
            self.assertTrue(shadow['open']['005930']['overnight'])
            self.assertEqual(shadow['mode'], 'observation_only_no_orders')
            plan, send = self.run_step(d, fills=[], rules=rules, holdings=[('005930', 50, 1000)], bid=1000, ask=1001)
            self.assertEqual((plan['action'], plan['target'], plan['execution']), ('HOLD', {'005930': 50}, 'NO_ORDER'))
            send.assert_not_called()
            with sqlite3.connect(source) as db:
                db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('2', '005930', 'SELL', 25, 1010,
                                                                      '2026-11-06T10:30:00+09:00'))
            self.assertEqual(f.mirror_target(f.paper_book(source, sizing)), {'005930': 25})
            plan, send = self.run_step(d, fills=[], rules=rules, holdings=[('005930', 50, 1000)], bid=1000, ask=1001)
            self.assertEqual((plan['action'], plan['qty'], plan['execution']), ('SELL', 25, 'DRY_RUN'))
            send.assert_not_called()
            plan, send = self.run_step(d, fills=[], rules=rules, holdings=[('005930', 50, 1000)], bid=975, ask=976)
            self.assertEqual((plan['action'], plan['qty'], plan['execution']), ('SELL', 50, 'DRY_RUN'))
            self.assertIn('stop', plan['reason'])
            send.assert_not_called()
            record = {'kind': 'live', 'engine': 'jev', 'auto': True, 'live_orders': False, 'hash': 'account',
                      'budget': {'order_price_max_krw': 300_000}, 'rules': rules, 'limits': {}}
            with patch('accounts.live_record', return_value=record):
                self.assertFalse(f.follow_order_current(A, root, record, 'SELL', source,
                                                        f.paper_book(source, sizing), protective=True))

    def test_broker_average_price_stop_sells_and_latches(self):
        with tempfile.TemporaryDirectory() as d:
            g = LiveOrderGuard(A, Path(d), symbols=('005930',))
            with g._db() as db:
                db.execute('INSERT INTO live_adoptions VALUES (?,?,?,?)', (g.scope, '20261106', '005930', 1))
            fills = [('005930', 'BUY', 4, 50_000, '06T10:00:00')]
            plan, _ = self.run_step(d, fills=fills, holdings=[('005930', 1, '50000')], bid=49_000, ask=49_100)
            self.assertEqual((plan['action'], plan['symbol'], plan['price'], plan['execution']), ('SELL', '005930', 49_000, 'DRY_RUN'))
            plan, _ = self.run_step(d, fills=fills, holdings=[('005930', 1, 50_000)], bid=50_500, ask=50_600)
            self.assertEqual(plan['action'], 'SELL')  # latched: a rebound does not cancel the stop

    def test_dashboard_exit_request_sells_owned_shares_and_is_cleared(self):
        with tempfile.TemporaryDirectory() as d:
            g = LiveOrderGuard(A, Path(d), symbols=('005930',))
            with g._db() as db:
                db.execute('INSERT INTO live_adoptions VALUES (?,?,?,?)', (g.scope, '20261106', '005930', 1))
            fills = [('005930', 'BUY', 4, 50_000, '06T10:00:00')]
            f.exit_request_path(Path(d), 'live-1').write_text('["005930", "bad"]')
            plan, _ = self.run_step(d, fills=fills, holdings=[('005930', 1, 50_000)], bid=50_500, ask=50_600)
            self.assertEqual((plan['action'], plan['symbol'], plan['reason']), ('SELL', '005930', 'exit requested from the dashboard'))
            self.assertFalse(f.exit_request_path(Path(d), 'live-1').exists())
            plan, _ = self.run_step(d, fills=fills, holdings=[('005930', 1, 50_000)], bid=50_500, ask=50_600)
            self.assertEqual(plan['action'], 'SELL')  # latched for the day

    def test_switched_off_account_plans_only_but_an_armed_service_may_cancel_its_open_order(self):
        fills = [('005930', 'BUY', 4, 50_000, '06T10:58:00')]
        with tempfile.TemporaryDirectory() as d:  # live-order switch off: the armed service plans, never buys
            plan, send = self.run_step(d, fills=fills, execute=False, may_cancel=True)
            self.assertEqual((plan['action'], plan['execution']), ('BUY', 'DRY_RUN'))
            send.assert_not_called()

    def test_buy_is_sized_to_the_capital_left(self):
        fills = [('005930', 'BUY', 4, 50_000, '06T10:58:00')]
        rules = {'sizing': 'amount', 'tranche_krw': 1_000_000, 'max_order_qty': 100}
        with tempfile.TemporaryDirectory() as d:  # capital 1,000,000 with 900,000 already invested: room for 1 share at 50,100
            with patch.object(f, 'tranche_shares', return_value=lambda price, qty=1, fill=None, nth=1: 20):
                snap = snapshots()
                snap['balance']['Output_0'] = {'orr_pbl_amt1': 1_000_000, 'tot_aet_amt': 1_000_000, 'tot_byn_amt': 900_000}
                with patch('nh_live_broker.collect_snapshots', return_value=snap):
                    record = {'kind': 'live', 'engine': 'jev', 'auto': True, 'budget': {'order_price_max_krw': 300_000, 'capital_krw': 1_000_000},
                              'rules': rules}
                    paper(Path(d) / 'shared-etf-paper', fills)
                    with patch('nh_live_shadow.effective_rules', return_value={'stop_loss_pct': 2.0}), \
                            patch('accounts.live_price_cap', return_value=300_000), patch('src.live.halt.halt_flag_set', return_value=False), \
                            patch.object(f, 'engine_supports', return_value=False):
                        plan = f.step(reader(50_000, 50_100), A, {}, record, root=Path(d), now=NOW, key='live-1')
                    self.assertEqual((plan['action'], plan['qty'], plan['capital_room']), ('BUY', 1, 100_000))
                    snap['balance']['Output_0']['tot_byn_amt'] = 990_000
                    with patch('nh_live_shadow.effective_rules', return_value={'stop_loss_pct': 2.0}), \
                            patch('accounts.live_price_cap', return_value=300_000), patch('src.live.halt.halt_flag_set', return_value=False), \
                            patch.object(f, 'engine_supports', return_value=False):
                        plan = f.step(reader(50_000, 50_100), A, {}, record, root=Path(d), now=NOW, key='live-1')
                    self.assertEqual((plan['execution'], plan['execution_reason']), ('NO_ORDER', 'capital limit reached'))

    def test_pending_intent_is_reconciled_before_anything_else(self):
        with tempfile.TemporaryDirectory() as d:
            g = LiveOrderGuard(A, Path(d), symbols=('005930',))
            with g._db() as db:
                db.execute('INSERT INTO live_intents VALUES (?,?,?,?,?,?,?,?,?)',
                           ('fp', g.scope, '20261106', 'krstockOrderCashBuy', '005930', 1, None, 'uncertain', 0))
            plan, send = self.run_step(d, fills=[('005930', 'BUY', 4, 50_100, '06T10:58:00')], execute=True)
            self.assertEqual((plan['action'], plan['pending']), ('RECONCILE', 1))
            send.assert_not_called()

    # ---- review 2 (2026-11-17): hand trades and the opening rule ----

    CLOSED = [('005930', 'BUY', 4, 50_000, '06T10:00:00'), ('005930', 'SELL', 4, 49_000, '06T10:59:00')]  # the paper sold today
    HOLD = {'choice': 'HOLD', 'confidence': 0.9}

    def test_a_hand_bought_share_follows_jevs_holding_view_in_the_cycle(self):
        """User 2026-11-18: a live share the paper does not hold gets Jev's view each cycle; SELL sells it now."""
        import json
        fills = [('005930', 'BUY', 1, 50_000, '06T10:00:00')]
        with tempfile.TemporaryDirectory() as d:
            own(d, '005930', 1)
            (Path(d) / 'live-manual-buys-live-1.json').write_text(json.dumps({'005930': 1}))
            plan, _ = self.run_step(d, fills=fills, holdings=[('005930', 1, 50_000)], execute=True)
            self.assertEqual((plan['action'], plan['qty']), ('BUY', 1))
            own(d, '005930', 2)  # the hand buy filled
            plan, _ = self.run_step(d, fills=fills, holdings=[('005930', 2, 50_000)], jev=(self.HOLD, None))
            self.assertNotIn('remainder', plan)  # a dry run cannot order: Jev is not asked (AGENTS 11-18, review 3 M9)
            self.view.assert_not_called()
            for answer, action in (({'choice': 'HOLD', 'confidence': 0.9}, 'HOLD'), ({'choice': 'SELL', 'confidence': 0.8}, 'SELL')):
                plan, _ = self.run_step(d, fills=fills, holdings=[('005930', 2, 50_000)], jev=(answer, None), execute=True)
                self.assertEqual((plan['action'], plan['remainder']['005930']['jev']), (action, answer))
                self.assertEqual(self.view.call_args.args[2], '005930')
            self.assertEqual((plan['qty'], plan['target']), (1, {'005930': 1}))  # back to the paper's share

    def test_a_hand_buy_buys_its_own_quantity_and_the_mirror_gap_keeps_its_rules(self):
        """M2: a 1-share hand buy bought the whole chase-blocked paper gap, and opened yesterday's paper position."""
        import json
        blocked = [('005930', 'BUY', 1, 45_000, f'06T10:{m}:00') for m in ('10', '20', '30', '40')]  # ask 11% above the fills
        old = [('005930', 'BUY', 1, 50_000, f'05T10:{m}:00') for m in ('10', '20', '30')]  # yesterday's, never mirrored
        for fills in (blocked, old):
            with tempfile.TemporaryDirectory() as d:
                plan, _ = self.run_step(d, fills=fills)
                self.assertEqual(plan['action'], 'WAIT')
                (Path(d) / 'live-manual-buys-live-1.json').write_text(json.dumps({'005930': 1}))
                plan, send = self.run_step(d, fills=fills, execute=True)
                self.assertEqual((plan['action'], send.call_args.args[4]['orr_qty']), ('BUY', 1))
                own(d, '005930', 1)  # the hand buy filled
                plan, send = self.run_step(d, fills=fills, holdings=[('005930', 1, 50_100)], execute=True)
                self.assertEqual(plan['action'], 'WAIT')
                send.assert_not_called()

    def test_a_paper_add_today_opens_only_its_own_units(self):
        """M3: one paper add today made yesterday's three never-mirrored tranches buyable at once."""
        fills = [('005930', 'BUY', 1, 50_000, f'05T14:5{m}:00') for m in '123'] + [('005930', 'BUY', 1, 50_000, '06T10:00:00')]
        with tempfile.TemporaryDirectory() as d:
            plan, send = self.run_step(d, fills=fills, execute=True)
            self.assertEqual((plan['action'], plan['target'], send.call_args.args[4]['orr_qty']), ('BUY', {'005930': 4}, 1))
            own(d, '005930', 1)  # today's unit filled; yesterday's three still cannot open
            plan, send = self.run_step(d, fills=fills, holdings=[('005930', 1, 50_100)], execute=True)
            self.assertEqual(plan['action'], 'WAIT')
            send.assert_not_called()

    def test_a_hand_cut_ends_with_its_paper_position(self):
        """M4: a hand cut carried into the next paper position (and the paper's sell-out sells the rest)."""
        import json
        first = [('005930', 'BUY', 1, 50_000, f'06T10:{m}:00') for m in ('10', '20', '30', '40')]
        closed = first + [('005930', 'SELL', 4, 50_500, '06T10:50:00')]
        reopened = closed + [('005930', 'BUY', 1, 50_000, f'06T10:5{m}:00') for m in '1234']
        for later, expected in ((closed, ('SELL', {})), (reopened, ('BUY', {'005930': 4}))):
            with tempfile.TemporaryDirectory() as d:
                own(d, '005930', 4)
                f.exit_request_path(Path(d), 'live-1').write_text(json.dumps({'005930': 2}))
                plan, _ = self.run_step(d, fills=first, holdings=[('005930', 4, 50_000)])
                self.assertEqual((plan['action'], plan['qty']), ('SELL', 2))
                own(d, '005930', 2)  # the cut filled
                plan, _ = self.run_step(d, fills=later, holdings=[('005930', 2, 50_000)])
                self.assertEqual((plan['action'], plan['target']), expected)

    def test_one_cycle_quotes_each_held_symbol_once(self):
        """M6: every cycle quoted 114800 twice (2 s apart, unused) and a held symbol twice more."""
        from types import SimpleNamespace

        class Broker:
            server_name, server_config = 'nhplug_readonly', SimpleNamespace(args=['--mode', 'readonly'])

            def __init__(self):
                self.quotes = []

            def call_tool(self, tool, args):
                done = {'rsp_cd': '00166', 'rsp_msg': '조회가 완료되었습니다.'}
                if tool == 'list_accounts':
                    return {'status': 'ok', 'data': {'rsp_cd': '00000', 'Output_0': [{'acct_no': A, 'acct_type': '01'}]}}
                if tool == 'get_stock_price':
                    self.quotes.append(args['stock_code'])
                    return {'status': 'ok', 'data': {'Output_0': {'iem_cd': args['stock_code'], 'bidp': 50_000, 'askp': 50_100}}}
                page = {'_nh_pagination': {'complete': True}}
                data = {'krstockInquiryDailyOrderExecution': {'rsp_cd': '11512', 'rsp_msg': '데이터가 존재하지 않습니다.', **page},
                        'krstockInquiryBalance': {**done, **page, 'Output_0': {'orr_pbl_amt1': 1_000_000, 'tot_byn_amt': 0},
                                                  'Output_1': [{'iem_cd': '005930', 'rsdl_qty': 10, 'phs_pr': 50_000}]},
                        }.get(args['operationId'], {**done, 'Output_0': {'sby_pls_amt': 0, 'sdr_xps': 0, 'eal_pls_amt': 0}})
                return {'status': 'ok', 'data': data}

        with tempfile.TemporaryDirectory() as d:
            own(d, '005930', 10)
            paper(Path(d) / 'shared-etf-paper', self.CLOSED)
            broker = Broker()
            with patch('nh_live_shadow.effective_rules', return_value={'stop_loss_pct': 2.0}), \
                    patch('accounts.live_price_cap', return_value=300_000), patch('src.live.halt.halt_flag_set', return_value=False):
                plan = f.step(broker, A, {}, RECORD, root=Path(d), now=NOW, key='live-1')
            self.assertEqual((plan['action'], plan['target']), ('SELL', {}))  # the paper sold it out
            self.assertEqual(broker.quotes, ['005930'])

    # ---- M5: the dashboard card (nh_live_shadow) replays what the mirror does ----

    def card(self, d, events=(), now=NOW):
        """The dashboard's virtual run for the same account, reading the mirror's state under `d` (= <runtime>/nhplug)."""
        import os
        import nh_live_shadow
        with patch.dict(os.environ, {'VIBE_TRADING_HOME': str(Path(d).parent)}):
            return nh_live_shadow.account_shadow(RECORD, list(events), now.strftime('%Y-%m-%d'), now=now)

    @staticmethod
    def tree(d):
        return {str(p): p.read_bytes() for p in sorted(Path(d).rglob('*')) if p.is_file()}

    def test_the_card_sells_out_with_the_paper_like_the_mirror(self):
        tick = lambda t, p: {'time': f'2026-11-06T{t}+09:00', 'market_bars': {'005930': [{'stck_prpr': p}]}}
        with tempfile.TemporaryDirectory() as t:
            d = Path(t) / 'nhplug'
            own(d, '005930', 1)
            plan, _ = self.run_step(d, fills=self.CLOSED, holdings=[('005930', 1, 50_000)])
            self.assertEqual((plan['action'], plan['target']), ('SELL', {}))  # the mirror sells it out
            before = self.tree(d)
            card = self.card(d, [tick('10:30:00', 49_500)])
            self.assertEqual(self.tree(d), before)  # read-only
            self.assertEqual([(x['side'], x['qty'], x['price']) for x in card['trades']], [('BUY', 1, 50_000), ('SELL', 1, 49_000)])
            self.assertEqual(card['open'], {})

    def test_the_card_applies_the_mirrors_hand_cut_and_plan_stop(self):
        import json
        fills = [('005930', 'BUY', 1, 50_000, '06T10:00:00'), ('005930', 'BUY', 1, 50_000, '06T10:10:00')]
        with tempfile.TemporaryDirectory() as t:
            d = Path(t) / 'nhplug'
            own(d, '005930', 2)
            f.exit_request_path(d, 'live-1').write_text(json.dumps({'005930': 1}))
            plan, _ = self.run_step(d, fills=fills, holdings=[('005930', 2, 50_000)])
            self.assertEqual((plan['action'], plan['qty']), ('SELL', 1))
            card = self.card(d, [{'time': '2026-11-06T10:20:00+09:00', 'market_bars': {'005930': [{'stck_prpr': 50_200}]}}])
            self.assertEqual([(x['side'], x['qty']) for x in card['trades']], [('BUY', 1), ('BUY', 1), ('SELL', 1)])
            self.assertEqual(card['open']['005930']['qty'], 1)
        with tempfile.TemporaryDirectory() as t:  # a 낙폭과대 plan stop (0.5%) is the mirror's stop, not the fixed 2%
            d = Path(t) / 'nhplug'
            paper(d / 'shared-etf-paper', fills[:1])
            (d / 'shared-etf-paper' / 'exit-plans.json').write_text(json.dumps({'005930': {
                'profile': 'oversold', 'stop_pct': 0.5, 'half_pct': 1.5, 'all_pct': 1.5}}))
            card = self.card(d, [{'time': '2026-11-06T10:20:00+09:00', 'market_bars': {'005930': [{'stck_prpr': 49_700}]}}])
            self.assertEqual([(x['side'], x['price']) for x in card['trades']], [('BUY', 50_000), ('SELL', 49_700)])


if __name__ == '__main__':
    unittest.main()


@pytest.fixture(autouse=True)
def _paper_plan_equals_live_capital(monkeypatch):
    """Amount sizing scales paper fills by live capital / the paper plan (first_buy x splits x daily new symbols,
    2026-11-17). The tests below set that plan to 1,000,000 (ratio 1, so a paper fill of at least tranche_krw maps to
    tranche_krw) without depending on the real accounts.json."""
    import accounts
    import nh_paper_isolated
    original = accounts.load

    def load(*args, **kwargs):
        data = dict(original(*args, **kwargs))
        paper = dict(data.get('paper-shared') or {})
        paper['limits'] = {**(paper.get('limits') or {}), 'daily_new_symbols': 1}
        paper['budget'] = {**(paper.get('budget') or {}), 'first_buy_krw': 1_000_000}
        return {**data, 'paper-shared': paper}
    monkeypatch.setattr(accounts, 'load', load)
    monkeypatch.setattr(nh_paper_isolated, 'SPLIT_TRANCHES', 1)


class Sizing(unittest.TestCase):
    def test_tranche_shares_and_target(self):
        share = f.tranche_shares({'budget': {'order_price_max_krw': 300_000}})
        amount = f.tranche_shares({'budget': {'order_price_max_krw': 300_000}, 'rules': {'sizing': 'amount', 'tranche_krw': 100_000}})
        self.assertEqual((share(1_249), int(amount(1_249, 1_000)), amount(90_000, 1), amount(210_000, 1)), (1, 80, 1, 0))
        # a share above tranche_krw (100,000 here) maps to none; a smaller fill maps to its own fraction
        self.assertAlmostEqual(float(amount(1_249, 10)), 10.0)
        book = {'114800': {'remaining_units': Decimal('40.4')}, '000660': {'remaining_units': Decimal(0)}}
        self.assertEqual(f.mirror_target(book), {'114800': 40})  # rounded; a tranche above the cap maps to none

    def test_amount_sizing_follows_the_paper_fill_by_capital_ratio(self):
        paper = {'limits': {'daily_new_symbols': 21}, 'budget': {'first_buy_krw': 1_000_000}}  # 1,000,000 / (1,000,000 x 1 x 21)
        amount = f.tranche_shares({'budget': {'order_price_max_krw': 300_000, 'capital_krw': 1_000_000},
                                   'rules': {'sizing': 'amount', 'tranche_krw': 300_000}}, paper)
        self.assertAlmostEqual(float(amount(1_341, 745)), 745 / 21, places=6)  # ~1,000,000 paper -> ~47,600 live
        self.assertEqual(amount(1_341, 100_000) * 1_341, 300_000)  # at most tranche_krw per paper fill
        samsung = amount(190_000, 5)  # 950,000 paper -> 45,238 live: a quarter share ...
        self.assertLess(samsung, 1)
        target = lambda tranches: f.mirror_target({'005930': {'remaining_units': samsung * tranches}})
        self.assertEqual(target(1), {'005930': 1})  # ... still one share while the paper holds it
        self.assertEqual(amount(310_000, 1), 0)  # above tranche_krw / the price cap: none
        self.assertEqual(target(3), {'005930': 1})  # 0.71 share: still one
        self.assertEqual(target(5), {'005930': 1})  # 1.19 shares
        self.assertEqual(target(7), {'005930': 2})  # 1.67 shares: the second share

    def test_the_ratio_is_live_capital_over_the_paper_plan(self):
        import nh_paper_isolated
        with patch.object(nh_paper_isolated, 'SPLIT_TRANCHES', 3):
            amount = f.tranche_shares({'budget': {'order_price_max_krw': 300_000, 'capital_krw': 1_000_000},
                                       'rules': {'sizing': 'amount', 'tranche_krw': 300_000}},
                                      {'limits': {'daily_new_symbols': 5, 'held_max_krw': 1_000_000_000},
                                       'budget': {'first_buy_krw': 1_000_000}})
        # 1,000,000 / (1,000,000 x 3 x 5) = 1/15: a 1,000,000 paper tranche of 1,000 KRW shares -> 66.7 live shares
        self.assertAlmostEqual(float(amount(1_000, 1_000)), 1_000 / 15, places=6)

    def test_a_sizing_change_applies_to_new_paper_fills_only(self):
        """User decision 2026-11-17: a held position is never re-sized (so never sold) by a settings change alone."""
        import os
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = paper(root / 'jev', [('114800', 'BUY', 100, 1000, '06T10:00:00')]) / 'orders.sqlite3'
            record = {'hash': 'abc', 'budget': {'order_price_max_krw': 300_000},
                      'rules': {'sizing': 'amount', 'tranche_krw': 100_000}}
            sizing = f.tranche_shares(record, root=root, source=source)
            self.assertEqual(f.mirror_target(f.paper_book(source, sizing)), {'114800': 100})
            self.assertFalse(f.pins_path(root, record).exists())  # reading (the dashboard) saves nothing
            f.save_pins(root, record, sizing)  # the live mirror saves what it saw
            self.assertEqual(os.stat(f.pins_path(root, record)).st_mode & 0o777, 0o600)
            smaller = {**record, 'rules': {'sizing': 'amount', 'tranche_krw': 30_000}}
            self.assertEqual(f.mirror_target(f.paper_book(source, f.tranche_shares(smaller, root=root, source=source))),
                             {'114800': 100})  # the held fill keeps its 100 shares: no sale from the change
            self.assertEqual(f.mirror_target(f.paper_book(source, f.tranche_shares(smaller))), {'114800': 30})  # unpinned
            db = sqlite3.connect(source)
            db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('9', '114800', 'BUY', 100, 1000, '2026-11-06T10:30:00+09:00'))
            db.commit(); db.close()
            self.assertEqual(f.mirror_target(f.paper_book(source, f.tranche_shares(smaller, root=root, source=source))),
                             {'114800': 130})  # the new fill takes the new size
            other = paper(root / 'vibe', [('114800', 'BUY', 100, 1000, '06T10:00:00')]) / 'orders.sqlite3'
            self.assertEqual(f.mirror_target(f.paper_book(other, f.tranche_shares(smaller, root=root, source=other))),
                             {'114800': 30})  # another engine ledger does not reuse these pins

    def test_the_accounts_add_cap_limits_followed_adds_without_selling_on_a_change(self):
        """User request 2026-11-17: a live account may follow fewer paper adds than the engine makes."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = paper(root / 'jev', [('114800', 'BUY', 100, 1000, '06T10:00:00'), ('114800', 'BUY', 100, 1000, '06T10:10:00'),
                                          ('114800', 'BUY', 100, 1000, '06T10:20:00')]) / 'orders.sqlite3'
            rules = {'sizing': 'amount', 'tranche_krw': 100_000}
            record = {'hash': 'cap', 'budget': {'order_price_max_krw': 300_000}, 'rules': {**rules, 'max_adds': 1}}
            sizing = f.tranche_shares(record, root=root, source=source)
            self.assertEqual(f.mirror_target(f.paper_book(source, sizing)), {'114800': 200})  # first buy + 1 add
            f.save_pins(root, record, sizing)
            looser = {**record, 'rules': {**rules, 'max_adds': 5}}  # a raised cap applies to new paper adds only
            self.assertEqual(f.mirror_target(f.paper_book(source, f.tranche_shares(looser, root=root, source=source))), {'114800': 200})
            tighter = {**record, 'rules': {**rules, 'max_adds': 0}}  # a lowered cap never sells what was followed
            self.assertEqual(f.mirror_target(f.paper_book(source, f.tranche_shares(tighter, root=root, source=source))), {'114800': 200})
            self.assertEqual(f.mirror_target(f.paper_book(source, f.tranche_shares({**record, 'rules': rules}))), {'114800': 300})  # default 100

    def test_configured_accounts(self):
        values = {'ACCOUNT_NUMBER': '123-45-678901', 'ACCOUNT_NUMBER_2': '10987654321', 'ACCOUNT_NUMBER_X': '1', 'ACCOUNT_NUMBER_3': 'short'}
        self.assertEqual(f.configured_accounts(values), ['12345678901', '10987654321'])


def test_ordinary_position_requires_verified_fill_and_event(tmp_path, monkeypatch):
    import json
    import sqlite3
    import nh_live_follow as follow
    import nh_paper_isolated as paper

    root = tmp_path / 'jev'
    root.mkdir()
    db = root / 'orders.sqlite3'
    with sqlite3.connect(db) as conn:
        conn.execute('create table fills (order_id text, symbol text, side text, quantity integer)')
        conn.execute('create table decision_order_links (order_id text, cycle_id text)')
        conn.executemany('insert into fills values (?,?,?,?)', [('a', '005930', 'BUY', 1), ('b', '114800', 'BUY', 1)])
        conn.executemany('insert into decision_order_links values (?,?)', [('a', 'one'), ('b', 'two')])
    (root / 'events.jsonl').write_text('\n'.join(json.dumps(x) for x in [
        {'event': 'submitted', 'cycle_id': 'one', 'symbol': '005930', 'source': 'jev'},
        {'event': 'submitted', 'cycle_id': 'two', 'symbol': '114800', 'source': 'oversold_v2'}]))
    monkeypatch.setattr(paper, 'market_view', lambda _: {'코스피': {'regime': '하락장'}})
    monkeypatch.setattr(paper, 'stop_symbol_index', lambda *_: '코스피')
    result = follow.stop_percent_for_positions(db, tmp_path, {'005930': 1, '114800': 1, '000660': 1}, 2.0)
    assert result['005930']['pct'] == 1.0
    assert result['114800']['pct'] == 2.0
    assert result['000660']['pct'] == 2.0
    assert follow.stop_percent_for_positions(db, tmp_path, {'005930': 1}, 0.5)['005930']['pct'] == 0.5
    monkeypatch.setattr(paper, 'market_view', lambda _: None)
    assert follow.stop_percent_for_positions(db, tmp_path, {'005930': 1}, 2.0)['005930']['pct'] == 2.0
    (root / 'exit-plans.json').write_text(json.dumps({  # an oversold plan: the mirror uses its stop as is
        '114800': {'profile': 'oversold', 'stop_pct': 0.5, 'half_pct': 1.5, 'all_pct': 1.5},
        '000660': {'profile': 'oversold', 'stop_pct': 12.0, 'half_pct': 8.0, 'all_pct': 15.0}}))
    result = follow.stop_percent_for_positions(db, tmp_path, {'114800': 1, '000660': 1, '005930': 1}, 2.0)
    assert (result['114800']['pct'], result['114800']['profile']) == (0.5, 'oversold')
    assert result['000660']['pct'] == 12.0
    assert result['005930']['pct'] == 2.0  # a confirmed ordinary position keeps the account stop (no regime view here)


def test_step_regime_stop_latches_only_confirmed_ordinary(tmp_path, monkeypatch):
    import json
    import nh_paper_isolated as paper_rules
    import nh_live_shadow
    import nh_live_broker
    import nh_guarded_live
    import accounts
    import src.live.halt as halt

    monkeypatch.setattr(paper_rules, 'market_view', lambda _: {'코스피': {'regime': '하락장'}})
    monkeypatch.setattr(paper_rules, 'stop_symbol_index', lambda *_: '코스피')
    monkeypatch.setattr(nh_live_broker, 'collect_snapshots', lambda *_, **__: snapshots([('005930', 1, 100)]))
    monkeypatch.setattr(accounts, 'live_price_cap', lambda *_: 300000)
    monkeypatch.setattr(halt, 'halt_flag_set', lambda *_: False)
    send = MagicMock()
    monkeypatch.setattr(nh_guarded_live, 'execute', send)

    def run(case, source, limit, known=True):
        runtime = tmp_path / case
        directory = paper(runtime / 'shared-etf-paper', [('005930', 'BUY', 1, 100, '06T10:58:00')])
        with sqlite3.connect(directory / 'orders.sqlite3') as db:
            db.execute('create table decision_order_links (order_id text, cycle_id text)')
            if known:
                db.execute('insert into decision_order_links values (?,?)', ('0', 'cycle'))
        (directory / 'events.jsonl').write_text(json.dumps({
            'event': 'submitted', 'cycle_id': 'cycle', 'symbol': '005930', 'source': source}))
        guard = LiveOrderGuard(A, runtime)
        with guard._db() as db:
            db.execute('insert into live_adoptions values (?,?,?,?)', (guard.scope, '20261106', '005930', 1))
        monkeypatch.setattr(nh_live_shadow, 'effective_rules', lambda *_: {'stop_loss_pct': limit})
        record = {'kind': 'live', 'engine': 'jev', 'auto': True, 'budget': {'order_price_max_krw': 300000}, 'rules': {}}
        result = f.step(reader(99, 100), A, {}, record, execute_orders=False, root=runtime, now=NOW)
        with guard._db() as db:
            latched = db.execute('select reason from follow_exits where scope=?', (guard.scope,)).fetchall()
        return result, latched

    ordinary, latch = run('ordinary', 'jev', 2)
    assert ordinary['action'] == 'SELL' and len(latch) == 1 and '1.00% stop' in latch[0][0]
    narrow, latch = run('narrow', 'jev', .5)
    assert narrow['action'] == 'SELL' and '0.500% stop' in latch[0][0]
    for case, source, limit, known in [('oversold', 'oversold_v2', 2, True), ('ipo', 'new_listing', 2, True),
                                       ('unknown', 'jev', 2, False)]:
        result, latch = run(case, source, limit, known)
        assert result['action'] == 'HOLD' and latch == [], (case, result, latch)
    send.assert_not_called()


class MirrorLagTests(unittest.TestCase):
    """11/17: the paper stop at 12:11 reached live 1 at 12:36 with no alarm."""

    def event(self, owned, target, action='HOLD', mode='execute'):
        return {'mode': mode, 'action': action, 'owned': owned, 'target': target}

    def test_holding_more_than_the_target_for_five_minutes_alerts_once_and_clears(self):
        KST = ZoneInfo('Asia/Seoul')
        t0 = datetime(2026, 11, 17, 12, 11, tzinfo=KST)
        sent = []
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lag = lambda event, minute: f.mirror_lag(root, 'live-1', event, t0.replace(minute=minute), notify=lambda *a: sent.append(a))
            self.assertIsNone(lag(self.event({'114800': 496}, {}), 11))            # the paper just sold: episode opens
            self.assertIsNone(lag({'mode': 'execute', 'action': 'BLOCK'}, 14))    # a guard refusal keeps it open
            alert = lag({'mode': 'execute', 'action': 'BLOCK', 'reason': 'market data stale or market closed'}, 16)
            self.assertEqual((alert['minutes'], alert['excess'], alert['last_action']), (5.0, {'114800': 496}, 'BLOCK'))
            self.assertTrue((root / 'live-follow' / 'lag-alert-live-1.json').exists())
            self.assertIsNotNone(lag(self.event({'114800': 496}, {}), 20))
            self.assertEqual(len(sent), 1)                                          # one notification per episode
            self.assertIsNone(lag(self.event({}, {}), 36))                          # sold: cleared
            self.assertFalse((root / 'live-follow' / 'lag-alert-live-1.json').exists())

    def test_under_buying_and_dry_run_are_not_a_lag(self):
        KST = ZoneInfo('Asia/Seoul')
        now = datetime(2026, 11, 17, 13, 0, tzinfo=KST)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertIsNone(f.mirror_lag(root, 'live-1', self.event({'114800': 100}, {'114800': 223}), now, notify=None))
            self.assertIsNone(f.mirror_lag(root, 'live-2', self.event({'114800': 9}, {}, mode='dry_run'), now, notify=None))
            self.assertFalse((root / 'live-follow' / 'lag-live-1.json').exists())


class FoldTests(unittest.TestCase):
    def test_repeated_dry_run_plans_are_folded_with_a_two_minute_heartbeat(self):
        KST = ZoneInfo('Asia/Seoul')
        t = datetime(2026, 11, 17, 13, 0, tzinfo=KST)
        plan = lambda **kw: {'mode': 'dry_run', 'action': 'BUY', 'symbol': '114800', 'qty': 12, 'execution': 'DRY_RUN',
                             'target': {'114800': 12}, 'owned': {}, **kw}
        write, state = f.fold(None, plan(), t)
        self.assertTrue(write)
        write, state = f.fold(state, plan(price=1340), t.replace(minute=1))   # same plan, a new price: folded
        self.assertFalse(write)
        later = plan()
        write, state = f.fold(state, later, t.replace(minute=2))              # heartbeat at 2 minutes
        self.assertTrue(write)
        self.assertEqual(later['folded'], 1)
        changed = plan(action='HOLD', execution='NO_ORDER')
        write, state = f.fold(state, changed, t.replace(minute=2, second=30))
        self.assertTrue(write)  # a changed plan is written at once
        refused = plan(action='HOLD', execution='NO_ORDER', manual_refused={'114800': {'shares': 1, 'why': 'late'}})
        self.assertTrue(f.fold(state, refused, t.replace(minute=2, second=40))[0])  # a hand-buy refusal is logged

    def test_execute_mode_lines_are_never_folded(self):
        KST = ZoneInfo('Asia/Seoul')
        t = datetime(2026, 11, 17, 13, 0, tzinfo=KST)
        line = {'mode': 'execute', 'action': 'HOLD', 'target': {'114800': 223}, 'owned': {'114800': 223}}
        _, state = f.fold(None, dict(line), t)
        self.assertTrue(f.fold(state, dict(line), t.replace(minute=1))[0])


def test_live_regime_stop_is_direction_aware(tmp_path, monkeypatch):
    # user 2026-11-17: an inverse is tightened to 1% in 상승장·횡보장, not in the 하락장 that favors it
    import nh_paper_isolated as paper
    monkeypatch.setattr(f, 'ordinary_positions', lambda source: {'114800': True, '069500': True})
    monkeypatch.setattr(paper, 'exit_plans', lambda root: {})
    monkeypatch.setattr(paper, 'stop_symbol_index', lambda *_: '코스피')
    source = tmp_path / 'orders.sqlite3'
    monkeypatch.setattr(paper, 'market_view', lambda _: {'코스피': {'regime': '하락장'}})
    falling = f.stop_percent_for_positions(source, tmp_path, {'114800': 1, '069500': 1}, 2.0)
    assert (falling['114800']['pct'], falling['069500']['pct']) == (2.0, 1.0)
    monkeypatch.setattr(paper, 'market_view', lambda _: {'코스피': {'regime': '상승장'}})
    rising = f.stop_percent_for_positions(source, tmp_path, {'114800': 1, '069500': 1}, 2.0)
    assert (rising['114800']['pct'], rising['069500']['pct']) == (1.0, 2.0)


def test_broker_open_treats_an_unknown_open_quantity_as_open():
    from nh_live_follow import broker_open
    assert not broker_open([{'ny_cns_qty': 0}, {'ny_cns_qty': 0}]) and not broker_open([])
    assert broker_open([{'ny_cns_qty': 0}, {'ny_cns_qty': 3}])
    assert broker_open([{}]) and broker_open([{'ny_cns_qty': '0'}]) and broker_open(['bad'])


def test_paper_new_listing_fills_are_followed_like_any_paper_fill(tmp_path):
    # user 2026-11-19: the live account follows the paper runner's new-listing buys too (the 11/17 exclusion is gone)
    import json as _json
    folder = tmp_path / 'shared-etf-paper'
    folder.mkdir()
    (folder / 'exit-plans.json').write_text(_json.dumps({'493260': {'profile': 'ipo', 'stop_pct': 2.0, 'half_pct': 3.0, 'all_pct': 3.0,
                                                                    'trend_half_pct': 3.0, 'trend_trail_pct': 2.0}}))
    units = f.pinned(lambda price, qty=1: 1, {'hash': 'x'}, tmp_path, folder / 'orders.sqlite3')
    assert units(26000, 10, '1|493260|10|26000|t') == 1
    assert units(1300, 10, '2|114800|10|1300|t') == 1


# ---- review 3 M6/X4/X5: the shared runner's events.jsonl (39 MB after six sessions, never rotated) is read incrementally ----

def _ledger(folder, fills, events):
    """A paper ledger whose buys are linked to cycles c<order id>, and its journal `events` (terminated lines)."""
    folder.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(folder / 'orders.sqlite3') as db:
        db.execute('create table if not exists fills (order_id text, symbol text, side text, quantity integer)')
        db.execute('create table if not exists decision_order_links (order_id text, cycle_id text)')
        db.executemany('insert into fills values (?,?,?,?)', fills)
        db.executemany('insert into decision_order_links values (?,?)', [(i, 'c' + i) for i, *_ in fills])
    with (folder / 'events.jsonl').open('a') as stream:
        stream.write(''.join(e if isinstance(e, str) else json.dumps(e) + '\n' for e in events))
    return folder / 'orders.sqlite3'


def sub(cycle, symbol, source, time='2026-11-06T10:00:00+09:00'):
    return {'time': time, 'event': 'submitted', 'symbol': symbol, 'source': source, 'cycle_id': cycle}


def test_ordinary_positions_skip_an_unparsable_line_and_read_a_partial_last_line_next_time(tmp_path):
    """X5: one bad line made the whole answer {} (every live stop then widened to the 2% account stop). A crash-truncated
    line is skipped; the runner's half-written last line counts once it parses."""
    folder = tmp_path / 'shared-etf-paper'
    source = _ledger(folder, [('a', '069500', 'BUY', 10), ('b', '005930', 'BUY', 1)],
                     [sub('ca', '069500', 'jev'), '{"time": "2026-11-06T10:01:00+09:00", "event": "subm\n'])
    assert f.ordinary_positions(source) == {'069500': True, '005930': False}
    whole = json.dumps(sub('cb', '005930', 'split'))
    with (folder / 'events.jsonl').open('a') as stream:
        stream.write(whole[:40])  # the runner is mid-append
    assert f.ordinary_positions(source) == {'069500': True, '005930': False}
    with (folder / 'events.jsonl').open('a') as stream:
        stream.write(whole[40:])  # complete, not yet terminated
    assert f.ordinary_positions(source) == {'069500': True, '005930': True}
    with (folder / 'events.jsonl').open('a') as stream:
        stream.write('\n' + json.dumps(sub('cb', '005930', 'split')) + '\n')  # one cycle submitted twice: unconfirmed
    assert f.ordinary_positions(source) == {'069500': True, '005930': False}


def test_journal_readers_parse_only_new_lines_and_start_over_on_a_replaced_file(tmp_path, monkeypatch):
    """M6: ordinary_positions, paper_reentries and engine_supports ran json.loads over the whole journal every cycle."""
    folder = tmp_path / 'shared-etf-paper'
    filler = [{'time': '2026-11-06T09:00:00+09:00', 'event': 'market_context', 'n': i} for i in range(50)]
    source = _ledger(folder, [('a', '069500', 'BUY', 10)], [sub('ca', '069500', 'jev'), *filler])
    assert f.ordinary_positions(source) == {'069500': True}
    parsed = []
    real = json.loads
    monkeypatch.setattr(json, 'loads', lambda s, *a, **k: parsed.append(s) or real(s, *a, **k))
    now = datetime(2026, 11, 6, 10, 30, tzinfo=KST)
    assert (f.ordinary_positions(source), f.paper_reentries(source, now)) == ({'069500': True}, {})
    assert parsed == []  # nothing new: nothing parsed
    with (folder / 'events.jsonl').open('a') as stream:
        stream.write(json.dumps(sub('cx', '005930', 'oversold_v2_reentry', '2026-11-06T10:25:00+09:00')) + '\n')
    assert f.paper_reentries(source, now) == {'005930': datetime(2026, 11, 6, 10, 25, tzinfo=KST)}
    assert len(parsed) == 1
    monkeypatch.setattr(json, 'loads', real)
    replaced = tmp_path / 'new.jsonl'
    replaced.write_text(json.dumps(sub('ca', '069500', 'oversold')) + '\n')
    replaced.replace(folder / 'events.jsonl')  # a rewritten journal (new inode) is read from the start
    assert (f.ordinary_positions(source), f.paper_reentries(source, now)) == ({'069500': False}, {})


def test_engine_support_reads_the_journal_tail(tmp_path):
    """engine_supports looks at the last lines only (Jev's pick within EVIDENCE_MINUTES); same answer as before."""
    folder = tmp_path / 'shared-etf-paper'
    folder.mkdir()
    (folder / 'strategy-settings.json').write_text(json.dumps({'entry_confidence': 0.6}))
    pick = {'time': '2026-11-06T10:55:00+09:00', 'event': 'jev_shadow', 'jev': {'candidate': {'choice': '005930', 'confidence': 0.7}}}
    source = _ledger(folder, [], [pick, *[{'time': '2026-11-06T10:56:00+09:00', 'event': 'market_context'}] * 30])
    assert f.engine_supports(source, '005930', NOW) is True
    assert f.engine_supports(source, '035420', NOW) is False
    assert f.engine_supports(source, '005930', NOW.replace(minute=11)) is False  # the pick is 16 minutes old


def test_the_remainder_reviews_recorded_bars_come_from_the_journal_tail(tmp_path):
    """X4: the remainder review scanned the whole journal for bars recorded in the last 30 s (vibe_fast_exit_watch
    ._recent_bars); the mirror reads the tail with the same answer."""
    from datetime import timedelta
    import vibe_fast_exit_watch as watch
    now = datetime(2026, 11, 6, 10, 8, 30, tzinfo=KST)

    def event(at, code, price):
        return {'event': 'market_context', 'time': at.isoformat(), 'market_source': 'NH KRX 1-minute completed bars',
                'market_bars': {code: [{'bsop_time': '100800', 'stck_prpr': price}]}}
    rows = [event(now - timedelta(seconds=31), '100790', '900'), event(now - timedelta(seconds=20), '100790', '1000'),
            event(now - timedelta(seconds=10), '005930', '2000'), event(now - timedelta(seconds=15), '100790', '1100'),
            event(now + timedelta(seconds=1), '100790', '9999')]
    path = tmp_path / 'events.jsonl'
    path.write_text(''.join(json.dumps(r) + '\n' for r in [{'event': 'market_context', 'time': 'x'}] * 10 + rows))
    codes = {'100790', '005930', '035420'}
    found = f.recent_bars(path, codes, now)
    assert found == watch._recent_bars(path, codes, now)
    assert (found['100790'][0][0]['stck_prpr'], found['005930'][0][0]['stck_prpr'], '035420' in found) == ('1100', '2000', False)
    assert f.recent_bars(tmp_path / 'missing.jsonl', codes, now) == {}


class Protection(unittest.TestCase):
    """Review 3 M3: the live stop and the daily loss latch need only the broker (AGENTS 11-18: 'Jev 없이 매 회차 그대로
    판다'); nothing on the paper side may stop them."""
    run_step = Step.run_step
    YESTERDAY = [('005930', 'BUY', 4, 50_000, '05T10:00:00')]

    def broken_ledger(self, d):
        paper(Path(d) / 'shared-etf-paper', self.YESTERDAY)
        with sqlite3.connect(Path(d) / 'shared-etf-paper' / 'orders.sqlite3') as db:  # one inconsistent paper row
            db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('bad', '069500', 'SELL', 1, 30_000, '2026-11-06T10:59:00+09:00'))

    def test_a_paper_ledger_error_still_runs_the_stop_and_the_loss_latch_and_buys_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            own(d, '005930', 1, day='20261105')
            self.broken_ledger(d)
            plan, _ = self.run_step(d, fills=[], holdings=[('005930', 1, 50_000)], bid=48_000, ask=48_100)
            self.assertEqual((plan['action'], plan['symbol']), ('SELL', '005930'))
            self.assertIn('stop', plan['reason'])
            self.assertIn('ValueError', plan['warning'])
            plan, _ = self.run_step(d, fills=[], holdings=[('005930', 1, 50_000)], loss=25_000)
            self.assertEqual((plan['action'], plan['reason']), ('SELL', 'daily loss latch'))
        with tempfile.TemporaryDirectory() as d:  # healthy price: hold what the runner owns, buy nothing
            own(d, '005930', 1, day='20261105')
            self.broken_ledger(d)
            (Path(d) / 'live-manual-buys-live-1.json').write_text(json.dumps({'035420': 1}))
            plan, _ = self.run_step(d, fills=[], holdings=[('005930', 1, 50_000)])
            self.assertEqual((plan['action'], plan['target']), ('HOLD', {'005930': 1}))
            self.assertEqual(f.manual_state(Path(d), {})['offsets'], {})
            with sqlite3.connect(Path(d) / 'shared-etf-paper' / 'orders.sqlite3') as db:
                db.execute("DELETE FROM fills WHERE order_id='bad'")  # the paper ledger reads again
            self.run_step(d, fills=[], holdings=[('005930', 1, 50_000)])
            self.assertEqual(f.manual_state(Path(d), {})['left'], {'035420': 1})  # the request waited for it

    def test_an_unwritable_hand_trade_file_still_runs_the_stop(self):
        with tempfile.TemporaryDirectory() as d:
            own(d, '005930', 1, day='20261105')
            with patch.object(f, 'save_manual', side_effect=OSError('read-only folder')):
                plan, _ = self.run_step(d, fills=self.YESTERDAY, holdings=[('005930', 1, 50_000)], bid=48_000, ask=48_100)
            self.assertEqual((plan['action'], plan['symbol']), ('SELL', '005930'))
            self.assertIn('OSError', plan['warning'])

    def test_an_unwritable_pins_file_still_runs_the_stop(self):
        with tempfile.TemporaryDirectory() as d:
            own(d, '005930', 1, day='20261105')
            with patch.object(f, 'save_pins', side_effect=OSError('read-only folder')):
                plan, _ = self.run_step(d, fills=self.YESTERDAY, holdings=[('005930', 1, 50_000)], bid=48_000, ask=48_100)
            self.assertEqual((plan['action'], plan['symbol']), ('SELL', '005930'))
            self.assertIn('OSError', plan['warning'])


class HandTrades(unittest.TestCase):
    """Review 3 M1/M2/M4/M5: dashboard hand trades on the live mirror."""
    run_step = Step.run_step
    YESTERDAY = [('005930', 'BUY', 4, 50_000, '05T10:00:00')]  # the live account carried its share into today

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.d = Path(self.dir.name)
        own(self.d, '005930', 1, day='20261105')

    def tearDown(self):
        self.dir.cleanup()

    def ask_buy(self, code='005930', qty=1):
        """The dashboard's own write (app.py: merge into the pending requests)."""
        f.write_request_file(f.manual_buy_path(self.d, 'live-1'), {**f.manual_buy_requests(self.d, 'live-1'), code: qty})

    def state(self):
        return f.manual_state(self.d, {'hash': None})

    def test_a_hand_buy_refused_before_sending_is_bought_next_cycle(self):
        """M1: the guard refused (nothing reached the broker) yet the hand buy was spent, and never bought."""
        from src.tools.nhplug_live_guard import LiveOrderBlocked

        def refuse(*a, **k):
            raise LiveOrderBlocked('daily new symbol limit')  # raised before any intent
        self.ask_buy()
        held = [('005930', 1, 50_000)]
        with self.assertRaises(LiveOrderBlocked):
            self.run_step(self.d, fills=self.YESTERDAY, holdings=held, bid=51_900, ask=52_000, execute=True, send=refuse)
        self.assertEqual(self.state()['left'], {'005930': 1})
        plan, sent = self.run_step(self.d, fills=self.YESTERDAY, holdings=held, bid=51_900, ask=52_000, execute=True)
        self.assertEqual((plan['action'], plan['buy_basis']['005930']), ('BUY', 'manual buy request'))
        self.assertEqual(sent.call_args.args[4]['orr_qty'], 1)
        self.assertEqual(self.state()['left'], {})  # sent: spent

    def test_a_hand_buy_aborted_before_sending_is_kept_and_an_unknown_send_is_not_retried(self):
        from nh_guarded_live import AISignalBeforeSend
        from src.tools.nhplug_live_guard import LiveOrderBlocked
        guard = LiveOrderGuard(A, self.d, symbols=('005930',))

        def intent(aborted):
            with guard._db() as db:
                n = db.execute('SELECT COUNT(*) FROM live_intents').fetchone()[0]
                db.execute('INSERT INTO live_intents VALUES (?,?,?,?,?,?,?,?,?)',
                           (f'fp{n}', guard.scope, '20261106', 'krstockOrderCashBuy', '005930', 1, None,
                            'terminal' if aborted else 'uncertain', 0))
                if aborted:
                    db.execute('INSERT INTO live_pre_send_aborts VALUES (?,?,?)', (f'fp{n}', guard.scope, 't'))

        def abort(*a, **k):
            intent(aborted=True)
            raise AISignalBeforeSend('strategy or market data expired or changed before send')

        def unknown(*a, **k):
            intent(aborted=False)
            raise LiveOrderBlocked('broker write outcome unknown; reconcile without retry')
        self.ask_buy()
        held = [('005930', 1, 50_000)]
        with self.assertRaises(AISignalBeforeSend):
            self.run_step(self.d, fills=self.YESTERDAY, holdings=held, execute=True, send=abort)
        self.assertEqual(self.state()['left'], {'005930': 1})  # proven unsent: kept
        with self.assertRaises(LiveOrderBlocked):
            self.run_step(self.d, fills=self.YESTERDAY, holdings=held, execute=True, send=unknown)
        self.assertEqual(self.state()['left'], {})  # it may have reached the broker: never bought twice

    def test_a_hand_buy_after_the_buy_window_is_refused_with_a_reason(self):
        """M2: a request after the cutoff was taken, waited 'outside the entry window' and silently lapsed at the flatten."""
        held = [('005930', 1, 50_000)]
        with patch.object(f, 'buy_window_end', return_value='14:30'):
            self.ask_buy()
            plan, _ = self.run_step(self.d, fills=self.YESTERDAY, holdings=held, now=NOW.replace(hour=9, minute=5))
            self.assertEqual(f.manual_buy_requests(self.d, 'live-1'), {})
            self.assertEqual(self.state()['left'], {'005930': 1})  # before the window opens it waits
            plan, sent = self.run_step(self.d, fills=self.YESTERDAY, holdings=held, now=NOW.replace(hour=14, minute=30), execute=True)
            self.assertEqual(plan['manual_refused'], {'005930': {'shares': 1, 'why': 'after the live buy window (14:30)'}})
            self.assertEqual((plan['action'], plan['target'], self.state()['offsets']), ('HOLD', {'005930': 1}, {}))
            sent.assert_not_called()
            self.ask_buy(qty=2)  # a new request after the cutoff
            plan, _ = self.run_step(self.d, fills=self.YESTERDAY, holdings=held, now=NOW.replace(hour=14, minute=31))
            self.assertEqual((plan['manual_refused']['005930']['shares'], self.state()['offsets']), (2, {}))
            self.assertEqual(f.manual_buy_requests(self.d, 'live-1'), {})

    def test_a_quantity_sell_is_applied_once_across_a_crash(self):
        """M5: saving the offset and clearing the request were two writes; a stop between them sold twice."""
        fills = [('005930', 'BUY', 4, 50_000, '05T10:00:00'), ('005930', 'BUY', 4, 50_000, '05T10:30:00')]
        own(self.d, '005930', 2, day='20261105')
        f.exit_request_path(self.d, 'live-1').write_text(json.dumps({'005930': 1}))
        held = [('005930', 2, 50_000)]
        with patch.object(f, 'release_requests', side_effect=OSError('killed')):  # saved, then stopped before releasing
            plan, _ = self.run_step(self.d, fills=fills, holdings=held)
        self.assertEqual((plan['action'], plan['target']), ('HOLD', {'005930': 2}))  # this cycle holds
        plan, _ = self.run_step(self.d, fills=fills, holdings=held)
        self.assertEqual((plan['action'], plan['qty'], plan['target']), ('SELL', 1, {'005930': 1}))
        self.assertEqual([p.name for p in self.d.iterdir() if 'requests' in p.name or 'manual-buys' in p.name], [])

    def test_a_request_written_while_the_mirror_holds_one_is_applied_once_later(self):
        """M5: the buy-request file was unlinked wholesale, losing a request the dashboard wrote in that window."""
        self.ask_buy('005930', 1)
        with patch.object(f, 'save_manual', side_effect=OSError('disk full')):  # the cycle holds; the claim waits
            self.run_step(self.d, fills=self.YESTERDAY, holdings=[('005930', 1, 50_000)])
        self.ask_buy('035420', 1)  # the dashboard, while the first request is still being taken
        self.run_step(self.d, fills=self.YESTERDAY, holdings=[('005930', 1, 50_000)])
        self.assertEqual(self.state()['left'], {'005930': 1})
        self.run_step(self.d, fills=self.YESTERDAY, holdings=[('005930', 1, 50_000)])
        self.assertEqual(self.state()['left'], {'005930': 1, '035420': 1})
        self.run_step(self.d, fills=self.YESTERDAY, holdings=[('005930', 1, 50_000)])
        self.assertEqual(self.state()['offsets'], {'005930': 1, '035420': 1})  # each once

    def test_sells_after_a_slow_remainder_review_use_a_fresh_bid(self):
        """M4: quotes were read before the remainder review (Jev: up to 45 s); another code's sell kept the old bid."""
        fills = [('005930', 'BUY', 4, 50_000, '06T09:30:00'), ('005930', 'BUY', 4, 50_000, '06T09:40:00'),
                 ('005930', 'SELL', 4, 50_000, '06T10:58:00')]  # paper target 1, live owns 2: sell 1
        own(self.d, '005930', 2, day='20261105')
        own(self.d, '035420', 1, day='20261106')
        f.save_manual(self.d, {'hash': None}, {'offsets': {'035420': 1}, 'anchors': {}, 'since': {}, 'left': {}}, NOW)
        market = {'005930': (50_000, 50_100), '035420': (200_000, 200_500)}
        rd = MagicMock()
        rd.call_tool.side_effect = lambda tool, p: {'status': 'ok', 'data': {'Output_0': {
            'iem_cd': p['stock_code'], 'bidp': market[p['stock_code']][0], 'askp': market[p['stock_code']][1]}}}

        def slow_jev(*a, **k):
            market['005930'] = (48_500, 48_600)  # the market moved while Jev was asked
            return {'choice': 'HOLD', 'confidence': 0.9, 'time': 't'}, None
        with patch('nh_live_shadow.effective_rules', return_value={'stop_loss_pct': 2.0}), \
                patch('nh_live_broker.collect_snapshots', return_value=snapshots([('005930', 2, 50_000), ('035420', 1, 200_000)])), \
                patch('accounts.live_price_cap', return_value=300_000), patch('src.live.halt.halt_flag_set', return_value=False), \
                patch.object(f, 'engine_supports', return_value=False), patch.object(f, 'mac_notify'), \
                patch.object(f, 'remainder_jev_view', side_effect=slow_jev), \
                patch('nh_guarded_live.execute', return_value='intent-1'):
            paper(self.d / 'shared-etf-paper', fills)
            plan = f.step(rd, A, {}, {**RECORD, 'hash': None}, execute_orders=True, root=self.d, now=NOW, key='live-1')
        self.assertEqual((plan['action'], plan['symbol'], plan['price']), ('SELL', '005930', 48_500))

    def test_the_failed_view_alert_is_sent_once_a_day_across_restarts(self):
        """M9: the once-a-day memory lived in the process; a restart alerted the same symbol again."""
        record, quotes = {'hash': None}, {'043260': (25_900, 26_000)}
        alerts = []

        def review(now):
            manual = {'offsets': {'043260': 1}, 'left': {}}
            f.review_remainders(None, self.d, 'live-1', record, {'043260': 1}, {}, {'043260': 1}, {'043260': 26_000},
                                quotes, manual, now, view=lambda *a: (None, 'bars_stale'), notify=lambda *a: alerts.append(a))
        review(NOW)
        getattr(f, '_REMAINDER_ALERTS', set()).clear()  # a restart forgets the process memory
        review(NOW.replace(minute=5))
        self.assertEqual(len(alerts), 1)
        review(NOW.replace(day=9))  # the next session alerts again
        self.assertEqual(len(alerts), 2)
