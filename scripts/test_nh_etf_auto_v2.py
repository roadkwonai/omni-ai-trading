import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import nh_etf_auto_v2 as v

NOW = datetime(2026, 11, 6, 10, 0, tzinfo=ZoneInfo('Asia/Seoul'))


class FakeBroker:
    def __init__(self, result=None):
        self.result = result or {'status': 'ok', 'data': {'Output_0': {'mkt_orr_no': 12}}}
        self.calls = []
    def call_tool(self, name, args, **kwargs):
        self.calls.append((name, args, kwargs))
        return self.result


class V2Tests(unittest.TestCase):
    def test_entry_gate_reports_each_blocking_condition(self):
        bars = {'INV': [{'stck_prpr': 101}] + [{'stck_prpr': 100}] * 5}
        self.assertEqual(v.entry_gate('WAIT', 0.9, bars), (False, 'decision_wait'))
        self.assertEqual(v.entry_gate('INV', 0.79, bars), (False, 'confidence_below_threshold'))
        self.assertEqual(v.entry_gate('INV', 0.9, {}), (False, 'invalid_bars'))
        self.assertEqual(v.entry_gate('INV', 0.9, bars), (True, 'entry_conditions_met'))
        self.assertEqual(v.entry_gate('INV', 0.9, {'INV': list(reversed(bars['INV']))}), (False, 'trend_not_rising'))
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = v.init_db(Path(self.tmp.name) / 'db.sqlite')
        self.root = patch.object(v, 'ROOT', Path(self.tmp.name))
        self.root.start()
        self.log = patch.object(v.old, 'log')
        self.log.start()
        self.sleep = patch.object(v.time, 'sleep')
        self.sleep_mock = self.sleep.start()
    def tearDown(self):
        self.db.close()
        self.log.stop()
        self.sleep.stop()
        self.root.stop()
        self.tmp.cleanup()

    def test_stop_and_profit_boundaries(self):
        self.assertTrue(v.exit_signal(100, 98))
        self.assertTrue(v.exit_signal(100, 102))
        self.assertFalse(v.exit_signal(100, 101))
        for price in (0, float('nan'), float('inf')):
            with self.assertRaises(ValueError): v.exit_signal(100, price)

    def test_intent_is_durable_before_transport_failure_and_restart_blocks(self):
        broker = FakeBroker({'status': 'error'})
        with self.assertRaises(ValueError): v.submit(self.db, broker, 'paper', NOW, '069500', 'Buy', 100, [])
        with self.assertRaises(ValueError): v.verify_start(self.db, [], [])
        self.assertEqual(len(broker.calls), 1)
        self.assertEqual(len(broker.calls[0][2]['order_intent_id']), 32)

    def test_order_and_position_limits_do_not_submit(self):
        broker = FakeBroker()
        with self.assertRaises(ValueError): v.submit(self.db, broker, 'paper', NOW, '069500', 'Buy', 100, [{}]*1000)
        with self.assertRaises(ValueError): v.verify_start(self.db, [{'iem_cd':'069500', 'rsdl_qty':1}], [])
        v.verify_start(self.db, [{'iem_cd':'005930', 'rsdl_qty':1}], [])
        self.assertFalse(broker.calls)

    def test_invalid_order_or_closed_session_is_never_submitted(self):
        broker = FakeBroker()
        for symbol, side, price, now in [('122630', 'Buy', 100, NOW), ('069500', 'Sell', 0, NOW),
                                         ('069500', 'Buy', 100, NOW.replace(hour=15))]:
            with self.assertRaises(ValueError): v.submit(self.db, broker, 'paper', now, symbol, side, price, [])
        self.assertFalse(broker.calls)

    def test_sell_reconciliation_requires_zero_balance(self):
        self.db.execute('INSERT INTO position VALUES (?,?,?)', ('069500', 1, 100))
        self.db.commit()
        broker = FakeBroker()
        intent, number = v.submit(self.db, broker, 'paper', NOW, '069500', 'Sell', 98, [])
        row = {'itg_orr_no':number, 'iem_cd':'069500', 'orr_qty':1, 'tot_cns_qty':1, 'can_qty':0, 'ny_cns_qty':0, 'sby_dit_cd_nm': '현금매도'}
        with patch.object(v, 'snapshot', return_value=([{'iem_cd':'069500','rsdl_qty':1}], [row])):
            with self.assertRaises(ValueError): v.reconcile(self.db, broker, 'paper', NOW, intent, number, '069500', 'Sell')
        with patch.object(v, 'snapshot', return_value=([{'iem_cd':'069500','rsdl_qty':0}], [row])):
            v.reconcile(self.db, broker, 'paper', NOW, intent, number, '069500', 'Sell')
        self.assertIsNone(self.db.execute('SELECT 1 FROM position').fetchone())

    def test_fill_requires_real_average_and_balance_then_exit(self):
        broker = FakeBroker()
        intent, number = v.submit(self.db, broker, 'paper', NOW, '069500', 'Buy', 100, [])
        row = {'itg_orr_no':number, 'iem_cd':'069500', 'orr_qty':1, 'tot_cns_qty':1, 'can_qty':0, 'ny_cns_qty':0, 'sby_dit_cd_nm': '현금매수'}
        with patch.object(v, 'snapshot', return_value=([{'iem_cd':'069500','rsdl_qty':1}], [row])):
            with self.assertRaises(ValueError): v.reconcile(self.db, broker, 'paper', NOW, intent, number, '069500', 'Buy')
        with patch.object(v, 'snapshot', return_value=([{'iem_cd':'069500','rsdl_qty':1}], [{**row, 'cns_avg_uit_pr':100}])):
            v.reconcile(self.db, broker, 'paper', NOW, intent, number, '069500', 'Buy')
        self.assertEqual(self.db.execute('SELECT avg FROM position').fetchone()[0], 100)
        with self.assertRaises(ValueError): v.verify_start(self.db, [], [])

    def test_legacy_error_and_unresolved_are_blocked(self):
        for events in ([{'event':'error'}], [{'event':'submitted', 'symbol':'069500'}], [{'event':'v2_error'}],
                       [{'event':'submitted', 'symbol':'069500'}, {'event':'roundtrip_verified', 'symbol':'114800'}]):
            with self.assertRaises(ValueError): v.verify_start(self.db, [], events)
        v.verify_start(self.db, [], [{'event':'submitted', 'symbol':'069500'}, {'event':'roundtrip_verified', 'symbol':'069500'}])

    def test_snapshot_blocks_open_orders_and_invalid_quantity(self):
        for balance, orders in (([{'iem_cd':'069500','rsdl_qty':float('nan')}], []),
                                ([], [{'ny_cns_qty':1}]), ([], [{}])):
            with patch.object(v, 'read_paginated', side_effect=[{'Output_1':balance}, {'Output_0':orders}]):
                with self.assertRaises(ValueError): v.snapshot(FakeBroker(), 'paper', NOW)

    def test_pending_is_cancelled_once_and_reconciled(self):
        broker = FakeBroker()
        intent, number = v.submit(self.db, broker, 'paper', NOW, '069500', 'Buy', 100, [])
        pending = {'itg_orr_no':number, 'iem_cd':'069500', 'orr_qty':1, 'tot_cns_qty':0, 'can_qty':0, 'ny_cns_qty':1, 'sby_dit_cd_nm': '현금매수'}
        cancelled = {**pending, 'can_qty':1, 'ny_cns_qty':0}
        no_holding = [{'iem_cd':'069500', 'rsdl_qty':0}]
        with patch.object(v, 'snapshot', side_effect=[(no_holding, [pending])] * 6 + [(no_holding, [cancelled])]), patch.object(v.time, 'sleep'):
            v.reconcile(self.db, broker, 'paper', NOW, intent, number, '069500', 'Buy')
        self.assertEqual([x[1]['operationId'] for x in broker.calls], ['krstockOrderCashBuy', 'krstockOrderCancel'])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM intents WHERE status != 'terminal'").fetchone()[0], 0)

    def test_fill_during_cancel_is_recorded(self):
        broker = FakeBroker()
        intent, number = v.submit(self.db, broker, 'paper', NOW, '069500', 'Buy', 100, [])
        pending = {'itg_orr_no':number, 'iem_cd':'069500', 'orr_qty':1, 'tot_cns_qty':0, 'can_qty':0, 'ny_cns_qty':1, 'sby_dit_cd_nm': '현금매수'}
        filled = {**pending, 'tot_cns_qty':1, 'ny_cns_qty':0, 'cns_avg_uit_pr':100}
        with patch.object(v, 'snapshot', side_effect=[([], [pending])] * 6 + [([{'iem_cd':'069500','rsdl_qty':1}], [filled])]), patch.object(v.time, 'sleep'):
            v.reconcile(self.db, broker, 'paper', NOW, intent, number, '069500', 'Buy')
        self.assertEqual(self.db.execute('SELECT quantity, avg FROM position').fetchone(), (1, 100))

    def test_wrong_side_never_confirms_order(self):
        broker = FakeBroker()
        intent, number = v.submit(self.db, broker, 'paper', NOW, '069500', 'Buy', 100, [])
        row = {'itg_orr_no':number, 'iem_cd':'069500', 'orr_qty':1, 'tot_cns_qty':1, 'can_qty':0,
               'ny_cns_qty':0, 'cns_avg_uit_pr':100, 'sby_dit_cd_nm':'현금매도'}
        with patch.object(v, 'snapshot', return_value=([{'iem_cd':'069500','rsdl_qty':1}], [row])):
            with self.assertRaisesRegex(ValueError, 'side'): v.reconcile(self.db, broker, 'paper', NOW, intent, number, '069500', 'Buy')
        with self.assertRaises(ValueError): v.verify_start(self.db, [], [])

    def test_cancel_failure_does_not_retry_and_blocks_restart(self):
        broker = FakeBroker()
        intent, number = v.submit(self.db, broker, 'paper', NOW, '069500', 'Buy', 100, [])
        broker.result = {'status':'error'}
        pending = {'itg_orr_no':number, 'iem_cd':'069500', 'orr_qty':1, 'tot_cns_qty':0, 'can_qty':0, 'ny_cns_qty':1, 'sby_dit_cd_nm': '현금매수'}
        with patch.object(v, 'snapshot', return_value=([], [pending])), patch.object(v.time, 'sleep'):
            with self.assertRaisesRegex(ValueError, 'never retry'): v.reconcile(self.db, broker, 'paper', NOW, intent, number, '069500', 'Buy')
        self.assertEqual(len(broker.calls), 2)
        with self.assertRaises(ValueError): v.verify_start(self.db, [], [])

    def test_cancel_ack_without_terminal_confirmation_stays_blocked(self):
        broker = FakeBroker()
        intent, number = v.submit(self.db, broker, 'paper', NOW, '069500', 'Buy', 100, [])
        pending = {'itg_orr_no':number, 'iem_cd':'069500', 'orr_qty':1, 'tot_cns_qty':0, 'can_qty':0, 'ny_cns_qty':1, 'sby_dit_cd_nm': '현금매수'}
        with patch.object(v, 'snapshot', return_value=([], [pending])), patch.object(v.time, 'sleep'):
            with self.assertRaisesRegex(ValueError, 'never retry'): v.reconcile(self.db, broker, 'paper', NOW, intent, number, '069500', 'Buy')
        self.assertEqual(len(broker.calls), 2)
        with self.assertRaises(ValueError): v.verify_start(self.db, [], [])

    def test_decision_only_two_candidates(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, *args): return json.dumps({'answers':[{'type':'choice','choice':'K200','confidence':0.9}]}).encode()
        def request(req, timeout):
            payload = json.loads(req.data)
            self.assertEqual({x['value'] for x in payload['questions'][0]['choices']}, {'WAIT','K200','INV'})
            return Response()
        with patch.object(v.old, 'dotenv_values', return_value={'OPENAI_API_KEY':'test'}), patch.object(v.urllib.request, 'urlopen', side_effect=request):
            self.assertEqual(v.decision({}, [])[0], 'K200')

    def test_holding_decision_validates_result(self):
        class Response:
            def __init__(self, answer): self.answer = answer
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, *args): return json.dumps({'answers':[self.answer]}).encode()
        with patch.object(v.old, 'dotenv_values', return_value={'OPENAI_API_KEY':'test'}):
            with patch.object(v.urllib.request, 'urlopen', return_value=Response({'type':'choice','choice':'SELL','confidence':0.9})):
                self.assertEqual(v.holding_decision({}, [], '069500', 100, 100)[0], 'SELL')
            for value in ('BUY', None):
                with patch.object(v.urllib.request, 'urlopen', return_value=Response({'type':'choice','choice':value,'confidence':0.9})):
                    with self.assertRaises(ValueError): v.holding_decision({}, [], '069500', 100, 100)

    def test_holding_flow_low_confidence_holds_and_stop_overrides(self):
        with patch.object(v.old, 'response', return_value={}), patch.object(v.old, 'valid_bars', return_value=[{}]), \
             patch.object(v.old, 'headlines', return_value=[{'title':'test'}]), patch.object(v, 'jev', return_value={}), \
             patch.object(v, 'holding_decision', return_value=('SELL', 0.79, {})), patch.object(v, 'quote', return_value={'bidp':100}):
            self.assertFalse(v.evaluate_holding(None, '069500', 100, 100))
        with patch.object(v, 'holding_decision', side_effect=AssertionError('AI must not run')):
            self.assertTrue(v.evaluate_holding(None, '069500', 100, 98))
        with patch.object(v.old, 'response', return_value={}), patch.object(v.old, 'valid_bars', return_value=[{}]), \
             patch.object(v.old, 'headlines', return_value=[{'title':'test'}]), patch.object(v, 'jev', return_value={}), \
             patch.object(v, 'holding_decision', return_value=('HOLD', 0.9, {})), patch.object(v, 'quote', return_value={'bidp':98}):
            self.assertTrue(v.evaluate_holding(None, '069500', 100, 100))

    def test_full_holding_loop_respects_confidence_and_stop(self):
        for bid, expected_sell in ((100, False), (98, True)):
            with self.subTest(bid=bid):
                self.db.execute('DELETE FROM position')
                self.db.execute('INSERT INTO position VALUES (?,?,?)', ('069500', 1, 100))
                self.db.commit()
                stop = Path(self.tmp.name) / 'STOP'
                stop.unlink(missing_ok=True)
                config = SimpleNamespace(args=['--mode', 'paper', '--account', 'paper'])
                configs = SimpleNamespace(mcp_servers={'nhplug_paper':config, 'nhplug_readonly':config})
                holdings = [{'iem_cd':'069500','rsdl_qty':1}]
                def response(adapter, tool, args):
                    return {'Output_0':[{'acct_no':'paper','acct_type':'03'}]} if tool == 'list_accounts' else {}
                def finish_sleep(seconds): stop.touch()
                with patch.object(v, 'load_agent_config', return_value=configs), patch.object(v, 'MCPServerAdapter', return_value=FakeBroker()), \
                     patch.object(v, 'init_db', return_value=self.db), patch.object(v, 'snapshot', return_value=(holdings, [])), \
                     patch.object(v, 'read_legacy', return_value=[]), patch.object(v, 'verify_start'), \
                     patch.object(v, 'market_open', return_value=True), patch.object(v.old, 'response', side_effect=response), \
                     patch.object(v.old, 'valid_bars', return_value=[{}]), patch.object(v.old, 'headlines', return_value=[{'title':'test'}]), \
                     patch.object(v, 'quote', return_value={'bidp':bid}), patch.object(v, 'holding_decision', return_value=('SELL', 0.79, {})) as judge, \
                     patch.object(v, 'jev', return_value={}), patch.object(v.old, 'api', return_value={'rsp_cd':'00000', 'Output_0':{'iem_cd':'069500','sll_pbl_qty':1}}), \
                     patch.object(v, 'submit', return_value=('intent', 12)) as submit, patch.object(v, 'reconcile'), \
                     patch.object(v.time, 'sleep', side_effect=finish_sleep):
                    v.run()
                self.assertEqual(submit.called, expected_sell)
                if expected_sell:
                    judge.assert_not_called()
                else:
                    judge.assert_called_once()
                self.assertTrue(any(call.args[0] == 'v2_stopped' and call.kwargs.get('reconciliation_required') for call in v.old.log.call_args_list))
                v.old.log.reset_mock()

    def test_legacy_recovery_requires_matching_orders_and_guard(self):
        events = [{'time': NOW.isoformat(), 'event':'submitted', 'symbol':'069500', 'order_number':12},
                  {'time': NOW.isoformat(), 'event':'sell_submitted', 'symbol':'069500', 'order_number':13},
                  {'time': NOW.isoformat(), 'event':'roundtrip_verified', 'symbol':'069500'},
                  {'time': NOW.isoformat(), 'event':'error'}]
        base = {'iem_cd':'069500','orr_qty':1,'tot_cns_qty':1,'can_qty':0,'ny_cns_qty':0}
        orders = [{**base, 'itg_orr_no':12, 'sby_dit_cd_nm':'현금매수'}, {**base, 'itg_orr_no':13, 'sby_dit_cd_nm':'현금매도'}]
        quotation = {'iem_cd':'069500','hoga_bsop_hour':'10:00:00','market_status':'1'}
        with patch.object(v, 'guard_snapshot', return_value='hash'), patch.object(v, 'snapshot', return_value=([], orders)), patch.object(v, 'quote', return_value=quotation), patch.object(v, 'read_legacy', return_value=events):
            result = v.reconcile_legacy_error(None, 'sensitive-account', None, None, NOW, events, self.db)
            self.assertEqual(result, NOW.isoformat())
            with v.sqlite3.connect(Path(self.tmp.name) / 'auto-v2-reconciliation.sqlite3') as db:
                proof = db.execute('SELECT scope, error_time, evidence_sha256 FROM proofs').fetchone()
            self.assertEqual(proof[:2], ('hash', NOW.isoformat()))
            self.assertNotIn('sensitive-account', str(proof))
        with patch.object(v, 'guard_snapshot', side_effect=ValueError('pending')):
            with self.assertRaises(ValueError): v.reconcile_legacy_error(None, 'paper', None, None, NOW, events, self.db)
        bad_orders = [{**orders[0], 'sby_dit_cd_nm':'현금매도'}, orders[1]]
        with patch.object(v, 'guard_snapshot', return_value='hash'), patch.object(v, 'snapshot', return_value=([], bad_orders)):
            with self.assertRaises(ValueError): v.reconcile_legacy_error(None, 'paper', None, None, NOW, events, self.db)
        with self.assertRaisesRegex(ValueError, 'prior-day'):
            v.reconcile_legacy_error(None, 'paper', None, None, NOW.replace(day=7), events, self.db)

    def test_reconciliation_ignores_prices_but_rejects_quantity_and_missing_fields(self):
        holding = {'iem_cd':'069500', 'rsdl_qty':1, 'price':100}
        order = {'itg_orr_no':12, 'iem_cd':'069500', 'sby_dit_cd_nm':'현금매수', 'orr_qty':1,
                 'tot_cns_qty':1, 'can_qty':0, 'ny_cns_qty':0, 'evaluation':100}
        original = v.reconciliation_state([holding], [order])
        self.assertEqual(original, v.reconciliation_state([{**holding, 'price':200}], [{**order, 'evaluation':200}]))
        self.assertNotEqual(original, v.reconciliation_state([{**holding, 'rsdl_qty':2}], [order]))
        for invalid in ({**order, 'orr_qty':None}, {**order, 'sby_dit_cd_nm':''}, {**order, 'itg_orr_no':None},
                        {**order, 'orr_qty':0}, {**order, 'orr_qty':2}, {**order, 'tot_cns_qty':0},
                        {**order, 'sby_dit_cd_nm':'매도취소'}):
            with self.assertRaises(ValueError): v.reconciliation_state([holding], [invalid])

    def test_empty_balance_symbol_only_allows_zero_quantity(self):
        self.assertEqual(v.reconciliation_state([{'iem_cd':'', 'rsdl_qty':0, 'ny_stl_qty':10}], []), ([], []))
        for row in ({'iem_cd':'', 'rsdl_qty':1}, {'iem_cd':''}, {'iem_cd':'', 'rsdl_qty':float('nan')},
                    {'rsdl_qty':0}):
            with self.assertRaises(ValueError): v.reconciliation_state([row], [])

    def test_buy_cancel_requires_unique_matching_terminal_original(self):
        original = {'itg_orr_no':12, 'iem_cd':'069500', 'sby_dit_cd_nm':'현금매수', 'orr_qty':1,
                    'tot_cns_qty':0, 'can_qty':1, 'ny_cns_qty':0}
        cancel = {'itg_orr_no':13, 'org_itg_orr_no':12, 'iem_cd':'069500', 'sby_dit_cd_nm':'매수취소',
                  'orr_qty':1, 'tot_cns_qty':0, 'can_qty':0, 'ny_cns_qty':0, 'orr_rjt_rsn_cd_nm':'정상'}
        state = v.reconciliation_state([], [cancel, original])
        self.assertIn((13, '069500', '매수취소', 1, 0, 0, 0, 12), state[1])
        for bad in ({**cancel, 'org_itg_orr_no':None}, {**cancel, 'org_itg_orr_no':13},
                    {**cancel, 'iem_cd':'114800'}, {**cancel, 'orr_qty':2},
                    {**cancel, 'tot_cns_qty':1}, {**cancel, 'orr_rjt_rsn_cd_nm':'거부'},
                    {**cancel, 'orr_rjt_rsn_cd_nm':None}, {**cancel, 'sby_dit_cd_nm':'매도취소'}):
            with self.assertRaises(ValueError): v.reconciliation_state([], [original, bad])
        with self.assertRaises(ValueError): v.reconciliation_state([], [cancel])
        with self.assertRaises(ValueError): v.reconciliation_state([], [original, original, cancel])
        with self.assertRaises(ValueError):
            v.reconciliation_state([], [{**original, 'can_qty':0, 'ny_cns_qty':1}, cancel])

    def test_slow_reader_spaces_each_paginated_request(self):
        reader = v.SlowRead(FakeBroker())
        reader.call_tool('call_api', {'operationId':'krstockInquiryBalance'})
        reader.call_tool('call_api', {'operationId':'krstockInquiryBalance', 'cts':'next'})
        self.assertEqual(self.sleep_mock.call_args_list, [unittest.mock.call(5), unittest.mock.call(5)])


if __name__ == '__main__':
    unittest.main()
