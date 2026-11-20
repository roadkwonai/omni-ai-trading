import json
import tempfile
import unittest
from pathlib import Path

import accounts as a


class AccountsTests(unittest.TestCase):
    def test_defaults_save_and_invalid_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'accounts.json'
            self.assertEqual(a.load(path)['paper-shared']['engine'], 'jev')
            records = a.load(path)
            records['live-1'] = {'label': '실계좌 1', 'kind': 'live', 'masked': a.mask('99900011122'), 'hash': 'h',
                                 'engine': 'vibe', 'auto': True, 'budget': dict(a.DEFAULT_BUDGET['live'])}
            with self.assertRaisesRegex(ValueError, '운용 방식'):  # no account follows the intraday vibe (user 2026-11-17)
                a.save(records, path)
            records['live-1']['engine'] = 'jev'
            records['paper-shared']['engine'] = 'vibe'
            with self.assertRaisesRegex(ValueError, '운용 방식'):
                a.save(records, path)
            records['paper-shared']['engine'] = 'jev'
            a.save(records, path)
            loaded = a.load(path)
            self.assertEqual((loaded['live-1']['engine'], loaded['paper-shared']['engine']), ('jev', 'jev'))
            self.assertEqual(loaded['live-1']['masked'], '999-00-****22')
            self.assertNotIn('99900011122', path.read_text())
            stored = json.loads(path.read_text())
            stored['accounts']['live-1']['budget']['capital_krw'] = 2_000_000_000  # above the live capital ceiling
            path.write_text(json.dumps(stored))
            fallback = a.load(path)['live-1']
            self.assertEqual((fallback['auto'], fallback['budget']['capital_krw'], fallback['invalid']), (False, 1_000_000, True))

    def test_live_price_cap_follows_the_account_and_rules_validate(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'accounts.json'
            records = a.load(path)
            live = {'label': '실계좌 1', 'kind': 'live', 'hash': a.digest('00000000001'), 'engine': 'jev', 'auto': True,
                    'budget': {**a.DEFAULT_BUDGET['live'], 'order_price_max_krw': 250_000}}
            records['live-1'] = live
            a.save(records, path)
            self.assertEqual(a.live_price_cap('000-00-000001', path), 250_000)
            self.assertEqual(a.live_price_cap('99999999999', path), 10_000)  # an account not set up here keeps the old cap
            self.assertEqual(a.live_rules(live), a.DEFAULT_LIVE_RULES)       # saved before rules existed
            for bad in ({'order_price_max_krw': 2_000_001}, ):
                with self.assertRaises(ValueError):
                    a.save({**records, 'live-1': {**live, 'budget': {**live['budget'], **bad}}}, path)
            with self.assertRaises(ValueError):  # the stop may only tighten the approved 2%
                a.save({**records, 'live-1': {**live, 'rules': {**a.DEFAULT_LIVE_RULES, 'stop_loss_pct': 3.0}}}, path)
            a.save({**records, 'live-1': {**live, 'rules': {**a.DEFAULT_LIVE_RULES, 'take_profit_pct': 5.0}}}, path)
            self.assertEqual(a.live_rules(a.load(path)['live-1'])['take_profit_pct'], 5.0)

    def test_malformed_files_fall_back_safely(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'accounts.json'
            path.write_text(json.dumps({'accounts': ['bad']}))
            self.assertEqual(a.load(path)['paper-shared']['kind'], 'paper')
            path.write_text(json.dumps({'accounts': {'paper-shared': {'kind': 'weird', 'engine': 'vibe'}}}))
            record = a.load(path)['paper-shared']
            self.assertEqual((record['kind'], record['auto'], record['budget']['first_buy_krw']), ('paper', False, 1_000_000))
            path.write_text(json.dumps({'accounts': {'paper-shared': {**a.DEFAULTS['accounts']['paper-shared'],
                                                                      'budget': {'first_buy_krw': 1e6, 'per_symbol_krw': 3_000_000}}}}))
            self.assertTrue(a.load(path)['paper-shared']['invalid'])  # float budgets are refused

    def test_save_rejects_out_of_range(self):
        with tempfile.TemporaryDirectory() as temp:
            records = a.load(Path(temp) / 'x.json')
            for change in ({'engine': 'gpt'}, {'auto': 'yes'}, {'budget': {'first_buy_krw': 4_000_000, 'per_symbol_krw': 5_000_000}},
                           {'budget': {'first_buy_krw': 2_000_000, 'per_symbol_krw': 1_000_000}}):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    a.save({'paper-shared': {**records['paper-shared'], **change}}, Path(temp) / 'x.json')


class ResolveTests(unittest.TestCase):
    def test_matches_hash_of_digits_and_only_stock_account_types(self):
        from accounts import digest, resolve
        records = {'paper-shared': {'kind': 'paper'}, 'a': {'kind': 'live', 'hash': digest('111-22-333344')},
                   'b': {'kind': 'live', 'hash': digest('99999999999')}, 'c': {'kind': 'live', 'hash': digest('55555555555')}}
        linked = [{'acct_no': '11122333344', 'acct_type': '01'}, {'acct_no': '99999999999', 'acct_type': '51'},
                  {'acct_no': None, 'acct_type': '01'}, 'bad']
        self.assertEqual(resolve(records, linked), {'a': '11122333344'})



class SwitchAuditFirstTests(unittest.TestCase):
    def test_switch_does_not_take_effect_when_its_audit_cannot_be_written(self):
        import accounts as acc
        import config_store
        from unittest import mock
        live = {'kind': 'live', 'label': '실계좌 1', 'engine': 'jev', 'auto': True,
                'budget': {'capital_krw': 500_000, 'order_price_max_krw': 300_000, 'daily_loss_krw': 20_000}}
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'accounts.json'
            acc.save({**acc.load(path), 'live-1': live}, path)
            with mock.patch.object(config_store, 'record', side_effect=OSError('history locked')):
                with self.assertRaises(OSError):
                    acc.set_live_orders('live-1', True, path)
            self.assertIsNone(acc.load(path)['live-1'].get('live_orders'))  # still off

if __name__ == '__main__':
    unittest.main()


class PaperLimitTests(unittest.TestCase):
    def test_paper_limits_validate_and_fall_back_to_defaults(self):
        import accounts as acc
        paper = {'kind': 'paper', 'engine': 'jev', 'auto': True, 'budget': {'first_buy_krw': 1_000_000, 'per_symbol_krw': 3_000_000}}
        self.assertEqual(acc.problems({**paper, 'limits': {'daily_buy_krw': 10_000_000, 'held_max_krw': 30_000_000}}), [])
        self.assertTrue(acc.problems({**paper, 'limits': {'daily_buy_krw': 2_000_000_000}}))  # above the bound
        self.assertEqual(acc.problems({**paper, 'limits': {'daily_buy_krw': 300_000_000, 'held_max_krw': 1_000_000_000}}), [])
        self.assertTrue(acc.problems({**paper, 'limits': {'order_max_krw': 5_000_000, 'daily_buy_krw': 4_000_000}}))
        self.assertTrue(acc.problems({**paper, 'limits': {'order_max_krw': 500_000}}))  # below the first buy
        self.assertEqual(acc.paper_limits({'limits': {'daily_new_symbols': 8, 'held_max_krw': 'x'}}),
                         {**acc.DEFAULT_PAPER_LIMITS, 'daily_new_symbols': 8})
        self.assertEqual(acc.paper_limits(None), acc.DEFAULT_PAPER_LIMITS)


class LiveLimitTests(unittest.TestCase):
    def test_live_limits_stay_within_the_approved_caps(self):
        import accounts as acc
        live = {'kind': 'live', 'engine': 'jev', 'auto': True,
                'budget': {'capital_krw': 500_000, 'order_price_max_krw': 300_000, 'daily_loss_krw': 20_000}}
        self.assertEqual(acc.problems({**live, 'limits': {'order_max_krw': 200_000, 'daily_new_symbols': 3}}), [])
        self.assertTrue(acc.problems({**live, 'limits': {'daily_new_symbols': 6}}))  # above the approved 5
        self.assertTrue(acc.problems({**live, 'limits': {'order_max_krw': 2_000_000}}))  # above the approved capital
        self.assertEqual(acc.live_limits(record={'limits': {'order_max_krw': 200_000, 'daily_new_symbols': 9}}),
                         {'order_max_krw': 200_000, 'daily_new_symbols': 5})

    def test_live_capital_is_the_accounts_own_setting_within_the_typo_ceiling(self):
        import accounts as acc
        from src.tools import nhplug_live_guard as guard
        self.assertEqual(acc.LIVE_CAPITAL_MAX, guard.CAPITAL_MAX)  # one ceiling for the dashboard and the order guard
        live = {'kind': 'live', 'engine': 'jev', 'auto': True,
                'budget': {'capital_krw': 3_000_000, 'order_price_max_krw': 300_000, 'daily_loss_krw': 20_000}}
        self.assertEqual(acc.problems({**live, 'limits': {'order_max_krw': 2_000_000}}), [])  # above the old 1,000,000
        self.assertTrue(acc.problems({**live, 'budget': {**live['budget'], 'daily_loss_krw': 30_000}}))  # loss cap unchanged


class LiveOrderSwitchTests(unittest.TestCase):
    def test_switch_saves_and_audits_and_rejects_bad_values(self):
        import accounts as acc
        live = {'kind': 'live', 'label': '실계좌 1', 'engine': 'jev', 'auto': True,
                'budget': {'capital_krw': 500_000, 'order_price_max_krw': 300_000, 'daily_loss_krw': 20_000}}
        self.assertTrue(acc.problems({**live, 'live_orders': 'yes'}))
        self.assertEqual(acc.problems({**live, 'live_orders': True}), [])
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'accounts.json'
            acc.save({**acc.load(path), 'live-1': live}, path)
            self.assertIs(acc.load(path)['live-1'].get('live_orders'), None)  # off unless switched on
            self.assertIs(acc.set_live_orders('live-1', True, path)['live_orders'], True)
            self.assertIs(acc.load(path)['live-1']['live_orders'], True)
            acc.set_live_orders('live-1', False, path)
            import config_store
            switches = [(r[2], r[4]) for r in config_store.history(10, 'accounts') if r[2] == 'live-1.live_orders']
            self.assertEqual(switches[:2], [('live-1.live_orders', 'false'), ('live-1.live_orders', 'true')])  # newest first
            with self.assertRaises(ValueError):
                acc.set_live_orders('paper-shared', True, path)


class SaveOneTests(unittest.TestCase):
    def test_saving_one_account_keeps_newer_switches_and_leaves_others_as_stored(self):
        import accounts as acc
        live = {'kind': 'live', 'label': '실계좌 1', 'engine': 'jev', 'auto': True, 'hash': 'h1',
                'budget': {'capital_krw': 500_000, 'order_price_max_krw': 300_000, 'daily_loss_krw': 20_000}}
        broken = {'kind': 'live', 'label': '실계좌 2', 'engine': 'jev', 'auto': True,
                  'budget': {'capital_krw': 2_000_000_000, 'order_price_max_krw': 300_000, 'daily_loss_krw': 20_000}}  # invalid capital
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'accounts.json'
            acc._write({'live-1': live, 'live-2': broken}, path)
            acc.set_live_orders('live-1', True, path)
            # a screen opened before the switch was turned on saves its budget: the switch must stay on
            acc.save_one('live-1', {'budget': {**live['budget'], 'capital_krw': 400_000}}, path)
            raw = acc.stored(path)
            self.assertEqual((raw['live-1']['live_orders'], raw['live-1']['budget']['capital_krw'], raw['live-1']['hash']), (True, 400_000, 'h1'))
            self.assertEqual(raw['live-2'], broken)  # the invalid account is left exactly as stored, not reset to defaults
            with self.assertRaises(ValueError):
                acc.save_one('live-1', {'budget': {**live['budget'], 'capital_krw': 2_000_000_000}}, path)


class GuardLimitParityTests(unittest.TestCase):
    def test_order_guard_and_dashboard_keep_the_same_paper_limits(self):
        # The guard (agent/src/tools/nhplug_order_guard.py) keeps its own copy so it can run without scripts/ on the path.
        import accounts
        from src.tools.nhplug_order_guard import PAPER_LIMITS as guard
        self.assertEqual({k: v[:2] for k, v in guard.items()}, accounts.PAPER_LIMITS)
        self.assertEqual({k: v[2] for k, v in guard.items()}, accounts.DEFAULT_PAPER_LIMITS)


class EngineChoiceTests(unittest.TestCase):
    # user 2026-11-17: each live account runs Jev trading (paper mirror) or the long-term engine; the paper account is Jev
    def test_live_accounts_may_run_longterm_and_paper_stays_jev(self):
        live = {'kind': 'live', 'label': '실계좌 2', 'engine': 'longterm', 'auto': True, 'hash': a.digest('00000000002'),
                'budget': dict(a.DEFAULT_BUDGET['live'])}
        self.assertEqual(a.problems(live), [])
        self.assertTrue(a.problems({**live, 'engine': 'vibe'}))
        paper = {'kind': 'paper', 'engine': 'longterm', 'auto': True, 'budget': dict(a.DEFAULT_BUDGET['paper'])}
        self.assertTrue(a.problems(paper))
        self.assertEqual(set(a.ENGINE_LABELS), {'jev', 'longterm'})

    def test_longterm_accounts_count_only_realized_loss(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'accounts.json'
            base = {'kind': 'live', 'auto': True, 'budget': dict(a.DEFAULT_BUDGET['live'])}
            path.write_text(json.dumps({'accounts': {
                'live-1': {**base, 'label': '실계좌 1', 'engine': 'jev', 'hash': a.digest('00000000001')},
                'live-2': {**base, 'label': '실계좌 2', 'engine': 'longterm', 'hash': a.digest('00000000002')}}}))
            self.assertFalse(a.live_loss_realized_only('00000000001', path))
            self.assertTrue(a.live_loss_realized_only('00000000002', path))
            self.assertFalse(a.live_loss_realized_only('00000000009', path))  # unknown: the stricter basis
            self.assertTrue(a.live_loss_realized_only(record=a.live_record('00000000002', path)))  # nh_guarded_live's one read
            self.assertFalse(a.live_loss_realized_only(record=None))

    def test_longterm_accounts_are_valid_live_accounts_on_the_long_term_engine(self):
        records = {'paper-shared': {'kind': 'paper', 'engine': 'jev'}, 'live-1': {'kind': 'live', 'engine': 'jev'},
                   'live-2': {'kind': 'live', 'engine': 'longterm', 'label': '실계좌 2'},
                   'live-3': {'kind': 'live', 'engine': 'longterm', 'invalid': True}, 'odd': {'kind': 'paper', 'engine': 'longterm'}}
        self.assertEqual(a.longterm_accounts(records), {'live-2': records['live-2']})


def test_the_paper_account_matches_a_live_accounts_capital_and_restores(tmp_path):  # user 2026-11-20 '분할 금액까지 맞추기'
    import json
    import accounts as A
    path = tmp_path / 'accounts.json'
    paper = {'kind': 'paper', 'engine': 'jev', 'auto': True, 'label': '모의', 'budget': {'first_buy_krw': 1_000_000, 'per_symbol_krw': 20_000_000},
             'limits': {'order_max_krw': 10_000_000, 'daily_buy_krw': 1_000_000_000, 'held_max_krw': 1_000_000_000,
                        'daily_new_symbols': 5, 'daily_loss_krw': 5_000_000}}
    live = {'kind': 'live', 'engine': 'jev', 'auto': True, 'label': '실계좌 1', 'hash': 'h',
            'budget': {'capital_krw': 1_490_000, 'order_price_max_krw': 400_000, 'daily_loss_krw': 20_000},
            'limits': {'order_max_krw': 1_000_000, 'daily_new_symbols': 5}}
    path.write_text(json.dumps({'version': 1, 'accounts': {'paper-shared': paper, 'live-1': live}}))
    A.set_match('live-1', 2, True, path)
    got = A.load(path)['paper-shared']
    assert got['budget']['first_buy_krw'] == 248_333 and got['budget']['per_symbol_krw'] == 745_000  # 149만 ÷ 6, ÷ 2
    assert A.paper_limits(got) == {'order_max_krw': 1_000_000, 'daily_buy_krw': 1_000_000_000, 'held_max_krw': 1_490_000,
                                   'daily_new_symbols': 5, 'daily_loss_krw': 20_000}
    A.save_one('live-1', {'budget': {**live['budget'], 'capital_krw': 3_000_000}}, path)  # the paper follows a new capital
    assert A.load(path)['paper-shared']['limits']['held_max_krw'] == 3_000_000
    A.save_one('paper-shared', {'budget': {'first_buy_krw': 300_000, 'per_symbol_krw': 900_000}}, path)  # set by hand
    A.save_one('live-1', {'budget': {**live['budget'], 'capital_krw': 2_000_000}}, path)
    kept = A.load(path)['paper-shared']
    assert kept['budget'] == {'first_buy_krw': 300_000, 'per_symbol_krw': 900_000}  # the hand-set values stay
    assert kept['match_live']['manual'] == {'first_buy_krw': 300_000, 'per_symbol_krw': 900_000}
    A.set_match('live-1', 2, True, path)  # 다시 계산해 저장
    assert A.load(path)['paper-shared']['budget']['first_buy_krw'] == 333_333 and not A.load(path)['paper-shared']['match_live'].get('manual')
    A.set_match('live-1', on=False, path=path)
    back = A.load(path)['paper-shared']
    assert back['budget'] == paper['budget'] and back['limits'] == paper['limits'] and back.get('match_live') is None
