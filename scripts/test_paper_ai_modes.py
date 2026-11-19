import fcntl
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import nh_paper_isolated as runner
from etf_universe import SYMBOLS
from paper_ai_modes import apply_pending_modes, read_modes, read_pending_modes, write_modes

NOW = datetime(2026, 11, 6, 12, tzinfo=ZoneInfo('Asia/Seoul'))
ACCOUNT = '50001000002'


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


class Reader:
    def call_tool(self, tool, params):
        return {'status': 'ok', 'data': {}}


def pages(broker, operation, fields, block):
    if operation == 'krstockInquiryDailyOrderExecution':
        return {'Output_0': []}
    return {'Output_0': {'orr_pbl_amt1': 1000000}, 'Output_1': []}


class PaperModesTests(unittest.TestCase):
    def setUp(self):  # mode handling, not the data confirmation (tested in test_nh_paper_isolated)
        from unittest.mock import patch as _patch
        self._confirm = _patch.object(runner, 'entry_confirmation', return_value=None); self._confirm.start()
        self._inverse = _patch.object(runner, 'inverse_gate', return_value=None); self._inverse.start()  # its own tests

    def tearDown(self):
        self._confirm.stop()
        self._inverse.stop()

    def test_settings_are_staged_during_runner_lock_and_applied_at_boundary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'ai-modes.json').write_text(json.dumps({'version':1,'jev':True,'decision':True}))
            with (root / 'runner.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                requested = write_modes(root, jev=True, decision=False)
                self.assertEqual(read_modes(root), {'version': 1, 'jev': True, 'decision': True})
                self.assertEqual(read_pending_modes(root), requested)
                self.assertEqual(apply_pending_modes(root), requested)
                self.assertIsNone(read_pending_modes(root))
            self.assertEqual(read_modes(root), requested)
            with self.assertRaises(ValueError):
                write_modes(root, jev=1, decision=False)

    def test_incomplete_pending_cannot_replace_active_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'ai-modes.json').write_text(json.dumps({'version':1,'jev':True,'decision':True}))
            (root / 'ai-modes.pending.json').write_text('{"version":1,"jev":true}')
            with self.assertRaises(ValueError):
                apply_pending_modes(root)
            self.assertTrue(read_modes(root)['decision'])
            self.assertTrue((root / 'ai-modes.pending.json').exists())

    def test_missing_active_settings_fail_closed_and_never_default_to_models_on(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(ValueError, 'unavailable'):
                read_modes(root)
            db = runner.connect_journal(root/'orders.sqlite3', runner.account_hash(ACCOUNT))
            with patch.object(runner, 'paper_pages', side_effect=pages), patch.object(runner, 'datetime', Clock), \
                 patch.object(runner, 'journal_event') as events, \
                 patch.object(runner, 'decision7', side_effect=AssertionError), \
                 patch.object(runner, 'jev7', side_effect=AssertionError), \
                 patch.object(runner, 'submit', side_effect=AssertionError):
                self.assertEqual(runner.cycle(db, Reader(), object(), ACCOUNT, root, True, NOW, etf7=True), 'wait')
                self.assertEqual(events.call_args.kwargs['reason'], 'ai_settings_unavailable')
            db.close()

    def test_crash_after_active_replace_keeps_pending_for_idempotent_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            requested = write_modes(root, jev=True, decision=False)
            with patch('paper_ai_modes._sync_directory', side_effect=OSError('simulated sync failure')):
                with self.assertRaises(OSError):
                    apply_pending_modes(root)
            self.assertEqual(read_pending_modes(root), requested)
            self.assertEqual(apply_pending_modes(root), requested)
            self.assertIsNone(read_pending_modes(root))

    def test_concurrent_requests_remain_well_formed(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda i: write_modes(root, jev=bool(i % 2), decision=bool((i // 2) % 2)), range(24)))
            requested = read_pending_modes(root)
            self.assertIn(requested['jev'], (True, False))
            self.assertIn(requested['decision'], (True, False))
            self.assertEqual(apply_pending_modes(root), requested)

    def test_model_modes_entry_policy_and_deadlines(self):
        jev = {'model': 'jev-1.14.0', 'chart_sha256': 'a'*64,
               'candidate': {'type': 'choice', 'choice': 'INV', 'confidence': 0.7}}
        self.assertEqual(runner.select_entry('WAIT', 0.9, jev, etf7=True)[0], 'INV')
        self.assertEqual(runner.select_entry(None, None, jev, etf7=True,
                         modes={'decision': False, 'jev': True})[:2], ('INV', 'jev'))
        self.assertEqual(runner.select_entry('INV', 0.8, None, etf7=True,
                         modes={'decision': True, 'jev': False})[:2], ('INV', 'decision'))
        self.assertEqual(runner.select_entry('INV', 0.79, None, etf7=True,
                         modes={'decision': True, 'jev': False})[2], 'confidence_below_threshold')
        self.assertEqual(runner.select_entry(None, None, None, etf7=True,
                         modes={'decision': False, 'jev': False})[2], 'models_disabled')
        bars = {key: [{'bsop_time': '115800'}] for key in SYMBOLS}
        jev_mode = {'decision': False, 'jev': True}
        self.assertIsNotNone(runner.etf7_signal_deadline(bars, None, NOW.isoformat(), NOW, modes=jev_mode))
        self.assertIsNone(runner.etf7_signal_deadline(bars, None, (NOW-timedelta(seconds=61)).isoformat(), NOW, modes=jev_mode))

    def test_jev_only_buy_never_calls_decision(self):
        bars = [{'bsop_time': f'11{58-i:02d}00', 'stck_prpr': str(1400-i)} for i in range(8)]
        jev = {'model': 'jev-1.14.0', 'chart_sha256': 'a'*64, 'response_time': NOW.isoformat(),
               'candidate': {'type': 'choice', 'choice': 'INV', 'confidence': 0.7}}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = runner.connect_journal(root/'orders.sqlite3', runner.account_hash(ACCOUNT))
            write_modes(root, jev=True, decision=False)
            with patch.object(runner, 'datetime', Clock), patch.object(runner, 'paper_pages', side_effect=pages), \
                 patch.object(runner, 'valid_bars', return_value=bars), patch('etf_news.headlines7', return_value=[{'title':'market','published':NOW.isoformat()}]), \
                 patch.object(runner, 'decision7', side_effect=AssertionError('Decision must not run')) as decision, \
                 patch.object(runner, 'jev7', return_value=jev), patch.object(runner, 'fresh_quote', return_value={'askp':1300}), \
                 patch.object(runner, 'learned_filter', side_effect=AssertionError('Decision research must not filter Jev-only')) as learned, \
                 patch.object(runner, 'journal_event') as events, patch.object(runner, 'submit', return_value=('a'*32, 1)) as submit:
                self.assertEqual(runner.cycle(db, Reader(), object(), ACCOUNT, root, True, NOW, etf7=True), 'buy')
                decision.assert_not_called()
                learned.assert_not_called()
                self.assertTrue(submit.call_args.kwargs['signal_check']())
                kinds = [c.args[1] for c in events.call_args_list]
                self.assertIn('market_context', kinds)
                self.assertIn('jev_shadow', kinds)
                self.assertNotIn('decision', kinds)
                queued = next(c for c in events.call_args_list if c.args[1] == 'jev_shadow')
                self.assertNotIn('openai_choice', queued.kwargs)
                write_modes(root, jev=False, decision=True)
                self.assertTrue(submit.call_args.kwargs['signal_check'](), 'pending request must not alter active cycle')
                (root/'ai-modes.json').write_text(json.dumps({'version':1,'jev':False,'decision':True}))
                self.assertFalse(submit.call_args.kwargs['signal_check'](), 'out-of-band change invalidates signal')
            db.close()

    def test_decision_only_buy_never_calls_jev(self):
        bars = [{'bsop_time': f'11{58-i:02d}00', 'stck_prpr': str(1400-i)} for i in range(8)]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = runner.connect_journal(root/'orders.sqlite3', runner.account_hash(ACCOUNT))
            write_modes(root, jev=False, decision=True)
            with patch.object(runner, 'datetime', Clock), patch.object(runner, 'paper_pages', side_effect=pages), \
                 patch.object(runner, 'valid_bars', return_value=bars), patch('etf_news.headlines7', return_value=[{'title':'market','published':NOW.isoformat()}]), \
                 patch.object(runner, 'decision7', return_value=('INV', 0.8, {'response_time':NOW.isoformat()})), \
                 patch.object(runner, 'jev7', side_effect=AssertionError('Jev must not run')) as jev, patch.object(runner, 'volatility', return_value=1), \
                 patch.object(runner, 'fresh_quote', return_value={'askp':1300}), patch.object(runner, 'journal_event'), \
                 patch.object(runner, 'submit', return_value=('a'*32, 1)):
                self.assertEqual(runner.cycle(db, Reader(), object(), ACCOUNT, root, True, NOW, etf7=True), 'buy')
                jev.assert_not_called()
            db.close()

    def test_jev_only_context_and_judgment_reach_real_ledger_without_fake_decision(self):
        import sqlite3
        bars = [{'bsop_time': f'11{58-i:02d}00', 'stck_oprc':str(1400-i),
                 'stck_hgpr':str(1401-i), 'stck_lwpr':str(1399-i),
                 'stck_prpr':str(1400-i), 'vol':'100'} for i in range(8)]
        jev = {'model':'jev-1.14.0', 'chart_sha256':'a'*64, 'response_time':NOW.isoformat(),
               'candidate':{'type':'choice','choice':'INV','confidence':0.7}}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            db=runner.connect_journal(root/'orders.sqlite3', runner.account_hash(ACCOUNT))
            write_modes(root, jev=True, decision=False)
            with patch.object(runner, 'datetime', Clock), patch.object(runner, 'paper_pages', side_effect=pages), \
                 patch.object(runner, 'valid_bars', return_value=bars), patch('etf_news.headlines7', return_value=[{'title':'market','published':NOW.isoformat()}]), \
                 patch.object(runner, 'decision7', side_effect=AssertionError('Decision must not run')), \
                 patch.object(runner, 'jev7', return_value=jev), patch.object(runner, 'fresh_quote', return_value={'askp':1300}):
                self.assertEqual(runner.cycle(db,Reader(),object(),ACCOUNT,root,False,NOW,etf7=True),'would_buy')
            with sqlite3.connect(root/'trading-ledger.sqlite3') as ledger:
                events=[(kind,json.loads(data)) for kind,data in ledger.execute('SELECT event,data FROM events_v2 ORDER BY offset')]
            self.assertEqual([kind for kind,_ in events], ['market_context','jev_shadow','v2_entry_gate'])
            self.assertEqual(events[0][1]['context'],'entry')
            self.assertEqual(events[0][1]['universe'],'etf7')
            self.assertEqual(events[0][1]['cycle_id'],events[1][1]['cycle_id'])
            self.assertEqual(events[1][1]['jev_response_time'],NOW.isoformat())
            self.assertNotIn('openai_choice',events[1][1])
            db.close()

    def test_both_off_blocks_ai_entry_but_not_protective_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = runner.connect_journal(root/'orders.sqlite3', runner.account_hash(ACCOUNT))
            write_modes(root, jev=False, decision=False)
            with patch.object(runner, 'paper_pages', side_effect=pages), patch.object(runner, 'journal_event') as events, \
                 patch.object(runner, 'datetime', Clock):
                self.assertEqual(runner.cycle(db, Reader(), object(), ACCOUNT, root, True, NOW, etf7=True), 'wait')
                self.assertEqual(events.call_args.kwargs['reason'], 'models_disabled')
            self.assertTrue(runner.holding_action(Reader(), root, '114800', 100, 98,
                                                 etf7=True, modes={'decision':False,'jev':False}))
            db.close()

    def test_corrupt_settings_do_not_block_protective_sale(self):
        class Broker:
            def call_tool(self, tool, params):
                return {'status':'ok','data':{'rsp_cd':'00000','Output_0':{'iem_cd':'114800','sll_pbl_qty':1}}}
        def held_pages(broker, operation, fields, block):
            if operation == 'krstockInquiryDailyOrderExecution':
                return {'Output_0': []}
            return {'Output_0':{},'Output_1':[{'iem_cd':'114800','rsdl_qty':1,'phs_pr':100}]}
        quote={'iem_cd':'114800','market_status':'1','hoga_bsop_hour':'11:59:30','bidp':98}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            db=runner.connect_journal(root/'orders.sqlite3',runner.account_hash(ACCOUNT))
            db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)',('buy','114800','BUY',1,100,NOW.isoformat()))
            db.commit()
            (root/'ai-modes.json').write_text('{invalid')
            with patch.object(runner,'datetime',Clock), patch.object(runner,'paper_pages',side_effect=held_pages), \
                 patch.object(runner,'fresh_quote',return_value=quote), patch.object(runner,'journal_event'), \
                 patch.object(runner,'submit',return_value=('a'*32,1)) as submit:
                self.assertEqual(runner.cycle(db,Reader(),Broker(),ACCOUNT,root,True,NOW,etf7=True),'sell')
                self.assertEqual(submit.call_args.args[5],'SELL')
                self.assertTrue(submit.call_args.kwargs['signal_check']())
            db.close()

    def test_jev_only_holding_sell_and_decision_only_sell_are_distinct(self):
        bars = [{'bsop_time': f'11{58-i:02d}00', 'stck_prpr': str(1400-i)} for i in range(8)]
        jev = {'model':'jev-1.14.0', 'chart_sha256':'a'*64, 'response_time':NOW.isoformat(),
               'holding':{'type':'choice','choice':'SELL','confidence':0.7}}
        with tempfile.TemporaryDirectory() as temp, patch.object(runner, 'datetime', Clock), \
             patch.object(runner, 'valid_bars', return_value=bars), patch('etf_news.headlines7', return_value=[{'title':'market','published':NOW.isoformat()}]), \
             patch.object(runner, 'journal_event') as events, patch.object(runner, 'decision7', side_effect=AssertionError), \
             patch.object(runner, 'holding_decision7', side_effect=AssertionError('Decision must not run')) as decision, \
             patch.object(runner, 'jev_holding7', return_value=jev) as model:
            context = {}
            self.assertTrue(runner.holding_action(Reader(), Path(temp), '114800', 100, 100, etf7=True,
                                                modes={'decision':False,'jev':True}, signal_context=context))
            decision.assert_not_called()
            model.assert_called_once()
            self.assertIsNone(context['response_time'])
            self.assertIn('market_context', [call.args[1] for call in events.call_args_list])

        with tempfile.TemporaryDirectory() as temp, patch.object(runner, 'datetime', Clock), \
             patch.object(runner, 'valid_bars', return_value=bars), patch('etf_news.headlines7', return_value=[{'title':'market','published':NOW.isoformat()}]), \
             patch.object(runner, 'journal_event'), patch.object(runner, 'jev_holding7', side_effect=AssertionError('Jev must not run')) as model, \
             patch.object(runner, 'holding_decision7', return_value=('SELL',0.8,{'response_time':NOW.isoformat()})):
            self.assertTrue(runner.holding_action(Reader(), Path(temp), '114800', 100, 100, etf7=True,
                                                modes={'decision':True,'jev':False}, signal_context={}))
            model.assert_not_called()


if __name__ == '__main__':
    unittest.main()
