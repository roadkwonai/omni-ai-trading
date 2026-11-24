import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import patch

from src.config.schema import MCPServerConfig
import nh_paper_isolated as p

NOW = datetime(2026, 11, 6, 12, tzinfo=ZoneInfo('Asia/Seoul'))
DEFAULT = '50001000001'
ALT = '50001000002'
FUTURES = '50051000001'
OVERSEAS = '50071000001'


def enable_models(root):
    (root / 'ai-modes.json').write_text(json.dumps({'version':1,'jev':True,'decision':True}))


import pytest as _pytest


@_pytest.fixture(autouse=True)
def _full_size_buys(monkeypatch):
    """These tests check entries, limits and exits at full tranche size; position sizing (holdings and market
    direction, user 2026-11-17) and its regime trim are tested in test_position_sizing.py."""
    import position_sizing
    monkeypatch.setattr(position_sizing, 'buy_factor', lambda *a, **k: (1.0, {}))
    monkeypatch.setattr(position_sizing, 'regime_trim', lambda *a, **k: 0)
    p._COOLING.clear()  # a refused pick's rest is process state (user 2026-11-19)
    p._PICK.clear()

class Fake:
    def __init__(self, name, config):
        self.name, self.config = name, config
    def call_tool(self, tool, params):
        if tool == 'list_accounts':
            return {'status':'ok', 'data': {'rsp_cd':'00000', 'Output_0':[
                {'acct_no':DEFAULT,'acct_type':'03'}, {'acct_no':ALT,'acct_type':'03'},
                {'acct_no':'33333333333','acct_type':'01'}]}}
        assert params['input']['act_no'] == ALT
        if params['operationId'] == 'krstockInquiryBalance':
            return {'status':'ok','data':{'rsp_cd':'00000','rsp_msg':'모의투자 마지막 자료입니다',
                'Output_0':{'dca':10000},'Output_1':[],'_nh_pagination':{'complete':True}}}
        if params['operationId'] == 'krstockInquiryDailyOrderExecution':
            return {'status':'ok','data':{'rsp_cd':'XA102','rsp_msg':'모의투자 조회가 완료되었습니다',
                'Output_0':[],'_nh_pagination':{'complete':True}}}
        raise AssertionError('unexpected broker call')

def configs():
    return {'nhplug_paper':MCPServerConfig(command='/tmp/python',args=['nhplug_mcp.py','--mode','paper','--account',DEFAULT]),
            'nhplug_readonly':MCPServerConfig(command='/tmp/python',args=['nhplug_mcp.py','--mode','readonly','--account',DEFAULT])}

class IsolatedTests(unittest.TestCase):
    def test_opening_observation_never_sends_before_order_session(self):
        self._session.stop()  # this test is about the real session clock
        with patch('session_times.SESSION_OPEN', '09:00'):
            for clock in ('08:59', '09:00', '09:01', '09:09', '09:10'):
                now = datetime.fromisoformat(f'2026-11-16T{clock}:00+09:00')
                self.assertEqual(p.in_observation_session(now), clock >= '09:00')
                self.assertEqual(p.order_session(now), clock >= '09:00')  # user 2026-11-19: orders from the open
            with patch.object(p, 'datetime') as mocked:
                mocked.now.return_value = datetime.fromisoformat('2026-11-16T08:59:59+09:00')
                with self.assertRaises(p.PaperOrderNotSent):
                    p.submit(None, None, None, '20261116', '114800', 'SELL', 1, 1000)

    def test_opening_cycle_records_decision_without_creating_order(self):
        self._session.stop()  # this test is about the real session clock
        opening = datetime.fromisoformat('2026-11-16T09:05:00+09:00')
        late_open = patch('session_times.SESSION_OPEN', '09:30')  # orders start 09:00 now; a later 장 시작 still observes
        late_open.start(); self.addCleanup(late_open.stop)
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return opening
        class Reader:
            def call_tool(self, tool, params): return {'status': 'ok', 'data': {}}
        def pages(broker, operation, fields, block):
            return {'Output_0': []} if operation == 'krstockInquiryDailyOrderExecution' else {'Output_0': {'orr_pbl_amt1': 1000000}, 'Output_1': []}
        bars = [{'bsop_time': '090300', 'stck_oprc': '1000', 'stck_prpr': '1000', 'stck_hgpr': '1001', 'stck_lwpr': '999', 'vol': '100'}]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); enable_models(root); db = p.connect_journal(root / 'orders.sqlite3', p.account_hash(ALT))
            with patch.object(p, 'datetime', Clock), patch.object(p, 'paper_pages', side_effect=pages), \
                 patch.object(p, 'valid_bars', return_value=bars), patch.object(p, 'fresh_etf7_bars', return_value=True), \
                 patch('etf_news.headlines7', return_value=[]), patch.object(p, 'investor_flows', return_value=None), \
                 patch.object(p, 'decision7', return_value=('WAIT', 0.9, {'response_time': opening.isoformat()})), \
                 patch.object(p, 'jev7', return_value={'candidate': {'type': 'choice', 'choice': 'INV', 'confidence': 0.9}}), \
                 patch.object(p, 'submit') as submit, patch.object(p, 'select_entry') as select, \
                 patch.object(p, 'exit_requests') as requests, patch.object(p, 'prune_exit_plans') as prune:
                self.assertEqual(p.cycle(db, Reader(), object(), ALT, root, False, opening, etf7=True), 'opening_observation')
                submit.assert_not_called(); select.assert_not_called()
                requests.assert_not_called(); prune.assert_not_called()
            self.assertEqual(db.execute('SELECT COUNT(*) FROM intents').fetchone()[0], 0)
            db.close()

    def test_settings_change_blocks_buy_but_not_protective_sell(self):
        class Broker:
            def __init__(self, late=False): self.late = late
            def call_tool(self, tool, args, **kwargs):
                before = kwargs['pre_reserve_check']()
                if self.late:
                    config.write_text('new')
                self.allowed = before and kwargs['pre_send_check']()
                if not self.allowed:
                    return {'status': 'error', 'error_type': 'NHPreSendCheckFailed',
                            'order_safety': {'state': 'aborted_no_send', 'resubmit_allowed': False}}
                operation = args['operationId']
                return {'status': 'ok', 'data': {'rsp_cd': '00048' if operation.endswith('Buy') else '00047',
                        'rsp_msg': '매수 완료' if operation.endswith('Buy') else '매도 완료', 'Output_0': {'mkt_orr_no': 1}}}

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'shared-etf-paper'
            root.mkdir()
            configs = [root.parent / 'accounts.json', root / 'strategy-settings.json', root.parent / 'tunables.json']
            for config in configs:
                config.write_text('old')
            @p.buy_settings_cycle
            def run(db, read, broker, account, root, side):
                if not broker.late:
                    config.write_text('new')
                try:
                    p.submit(db, broker, account, '20261106', '229200', side, 1, 100, etf7=True,
                             signal_check=lambda: True, deadline_ms=int(p.time.time() * 1000) + 60000)
                except p.PaperOrderNotSent:
                    pass
                return broker.allowed
            with patch('tunables.path', return_value=configs[-1]):
                for config in configs:
                    for side in ('BUY', 'SELL'):
                        for late in (False, True):
                            config.write_text('old')
                            db = p.connect_journal(root / f'{configs.index(config)}-{side}-{late}.sqlite3', p.account_hash(ALT))
                            self.assertEqual(run(db, None, Broker(late), ALT, root, side), side == 'SELL')
                            db.close()

    def setUp(self):  # these tests are about order paths, not the data confirmation (tested in EntryConfirmationTests)
        self._confirm = patch.object(p, 'entry_confirmation', return_value=None); self._confirm.start()
        self._inverse = patch.object(p, 'inverse_gate', return_value=None); self._inverse.start()  # tested in InverseGuardTests
        # order paths run at any wall-clock time; the session clock itself is tested in the opening tests below
        self._session = patch.object(p, 'order_session', return_value=True); self._session.start()

    def tearDown(self):
        self._confirm.stop()
        self._inverse.stop()
        self._session.stop()

    def test_etf7_candidates_reach_only_approved_paper_order_path(self):
        from etf_universe import SYMBOLS as ETF7
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return NOW
        class Reader:
            def call_tool(self, tool, params): return {'status': 'ok', 'data': {}}
        def pages(broker, operation, fields, block):
            return {'Output_0': []} if operation == 'krstockInquiryDailyOrderExecution' else {'Output_0': {'orr_pbl_amt1': 1000000}, 'Output_1': []}
        bars = [{'bsop_time': f'11{58-i:02d}00', 'stck_prpr': str(1400-i)} for i in range(8)]
        now_iso = NOW.isoformat()
        for name, code in ETF7.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                enable_models(root)
                db = p.connect_journal(root/'orders.sqlite3', p.account_hash(ALT))
                jev = {'model':'jev-1.14.0', 'chart_sha256':'a'*64, 'response_time':now_iso,
                       'candidate':{'type':'choice','choice':name,'confidence':0.7}}
                with patch.object(p, 'datetime', Clock), patch.object(p, 'paper_pages', side_effect=pages), \
                     patch.object(p, 'valid_bars', return_value=bars), patch('etf_news.headlines7', return_value=[{'title':'market','published':now_iso}]), \
                     patch.object(p, 'decision7', return_value=('WAIT', 0.9, {'response_time':now_iso})), \
                     patch.object(p, 'jev7', return_value=jev), patch.object(p, 'volatility', return_value=1), \
                     patch.object(p, 'inverse_tape', return_value=None), \
                     patch.object(p, 'fresh_quote', return_value={'askp':1300, 'bidp':1295}), \
                     patch.object(p, 'journal_event') as events, patch.object(p, 'submit', return_value=('a'*32, 1)) as submit:
                    self.assertEqual(p.cycle(db, Reader(), object(), ALT, root, True, NOW, etf7=True), 'buy')
                    self.assertEqual(submit.call_args.args[4], code)
                    self.assertTrue(submit.call_args.kwargs['etf7'])
                    decision_event = next(c for c in events.call_args_list if c.args[1] == 'decision')
                    self.assertEqual(decision_event.kwargs['universe'], 'etf7')
                db.close()

    def test_etf7_freshness_and_legacy_symbol_boundaries(self):
        from etf_universe import SYMBOLS as ETF7
        bars = {name:[{'bsop_time':'115800'}] for name in ETF7}
        self.assertTrue(p.fresh_etf7_bars(bars, NOW))
        self.assertFalse(p.fresh_etf7_bars({**bars, 'INV':[{'bsop_time':'115500'}]}, NOW))
        self.assertFalse(p.fresh_etf7_bars({**bars, 'INV':[{'bsop_time':'120100'}]}, NOW))
        self.assertFalse(p.fresh_etf7_bars({name: value for name, value in bars.items() if name != 'INV'}, NOW))
        self.assertTrue(p.fresh_model_time(NOW.isoformat(), NOW))
        self.assertFalse(p.fresh_model_time((NOW-timedelta(seconds=61)).isoformat(), NOW))
        self.assertFalse(p.fresh_model_time('2026-11-06T12:00:00', NOW))
        jev = {'model':'jev-1.14.0', 'chart_sha256':'a'*64, 'candidate':{'type':'choice','choice':'KQ150','confidence':0.7}}
        self.assertEqual(p.select_entry('WAIT', 0.9, jev)[2], 'jev_invalid')
        self.assertEqual(p.select_entry('WAIT', 0.9, jev, etf7=True)[0], 'KQ150')
        with self.assertRaisesRegex(ValueError, 'unowned'):
            p.verify_positions([{'iem_cd': ETF7['KQ150'], 'rsdl_qty':1}], {ETF7['KQ150']:1})
        p.verify_positions([{'iem_cd': ETF7['KQ150'], 'rsdl_qty':1}], {ETF7['KQ150']:1}, etf7=True)
        with tempfile.TemporaryDirectory() as temp:
            db=p.connect_journal(Path(temp)/'orders.sqlite3',p.account_hash(ALT))
            with self.assertRaisesRegex(ValueError,'invalid order'):
                p.submit(db,object(),ALT,'20261106','005930','BUY',1,100,etf7=True)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM intents').fetchone()[0],0)
            db.close()

    def test_etf7_deadline_is_earliest_model_bar_or_session_cutoff(self):
        from etf_universe import SYMBOLS as ETF7
        bars = {name:[{'bsop_time':'115800'}] for name in ETF7}
        as_ms=lambda stamp:int(stamp.timestamp()*1000)
        self.assertEqual(p.etf7_signal_deadline(bars,NOW.isoformat(),(NOW-timedelta(seconds=45)).isoformat(),NOW),
                         as_ms(NOW+timedelta(seconds=15)))
        bars['INV']=[{'bsop_time':'115630'}]
        self.assertEqual(p.etf7_signal_deadline(bars,NOW.isoformat(),NOW.isoformat(),NOW),as_ms(NOW+timedelta(seconds=30)))
        at=NOW.replace(hour=14,minute=59,second=30)
        close_bars={name:[{'bsop_time':'145730'}] for name in ETF7}
        self.assertEqual(p.etf7_signal_deadline(close_bars,at.isoformat(),at.isoformat(),at),as_ms(at+timedelta(seconds=30)))
        self.assertIsNone(p.etf7_signal_deadline(close_bars,at.isoformat(),at.isoformat(),at+timedelta(seconds=30)))
        self.assertIsNone(p.etf7_signal_deadline(bars,'2026-11-06T12:00:00',NOW.isoformat(),NOW))
        self.assertIsNone(p.etf7_signal_deadline(bars,(NOW-timedelta(seconds=60)).isoformat(),NOW.isoformat(),NOW))
        self.assertIsNone(p.etf7_signal_deadline(bars,(NOW+timedelta(seconds=1)).isoformat(),NOW.isoformat(),NOW))

    def test_protective_deadline_uses_quote_and_session_not_ai(self):
        quote={'iem_cd':'229200','market_status':'1','hoga_bsop_hour':'11:58:30'}
        self.assertEqual(p.etf7_protective_deadline(quote,'229200',NOW),int((NOW+timedelta(seconds=30)).timestamp()*1000))
        self.assertIsNone(p.etf7_protective_deadline(quote,'005930',NOW))
        self.assertIsNone(p.etf7_protective_deadline({**quote,'hoga_bsop_hour':'11:57:00'},'229200',NOW))
        at=NOW.replace(hour=14,minute=59,second=30)
        closing={**quote,'hoga_bsop_hour':'14:59:20'}
        self.assertEqual(p.etf7_protective_deadline(closing,'229200',at),int((at+timedelta(seconds=30)).timestamp()*1000))
        self.assertIsNone(p.etf7_protective_deadline(closing,'229200',at+timedelta(seconds=30)))

    def test_etf7_all_orders_require_guarded_deadline(self):
        class Broker:
            def call_tool(self, tool, args, **kwargs):
                return {'status':'ok','data':{'rsp_cd':'00047','rsp_msg':'매도주문이 완료되었습니다','Output_0':{'mkt_orr_no':1}}}
        with tempfile.TemporaryDirectory() as temp:
            db=p.connect_journal(Path(temp)/'orders.sqlite3',p.account_hash(ALT))
            for side in ('BUY','SELL'):
                with self.assertRaisesRegex(ValueError,'deadline'):
                    p.submit(db,object(),ALT,'20261106','229200',side,1,100,etf7=True)
                for deadline in (True,0,2**53,int(p.time.time()*1000)-1):
                    with self.assertRaisesRegex(ValueError,'deadline'):
                        p.submit(db,object(),ALT,'20261106','229200',side,1,100,etf7=True,
                                 signal_check=lambda:True,deadline_ms=deadline)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM intents').fetchone()[0],0)
            p.submit(db,Broker(),ALT,'20261106','229200','SELL',1,100,etf7=True,
                     signal_check=lambda:True, deadline_ms=int(p.time.time()*1000)+60000)
            self.assertEqual(db.execute('SELECT status FROM intents').fetchone()[0],'acknowledged')
            db.close()

    def test_etf7_order_guard_accepts_exact_approved_codes(self):
        from etf_universe import SYMBOLS as ETF7
        class Broker:
            def call_tool(self, tool, args, **kwargs):
                return {'status':'ok','data':{'rsp_cd':'00048','rsp_msg':'매수주문이 완료되었습니다','Output_0':{'mkt_orr_no':1}}}
        for code in ETF7.values():
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temp:
                db=p.connect_journal(Path(temp)/'orders.sqlite3',p.account_hash(ALT))
                p.submit(db,Broker(),ALT,'20261106',code,'BUY',1,100,etf7=True,
                         signal_check=lambda:True, deadline_ms=int(p.time.time()*1000)+60000)
                self.assertEqual(db.execute('SELECT symbol,status FROM intents').fetchone(),(code,'acknowledged'))
                db.close()

    def test_etf7_mock_order_persists_full_seven_symbol_evidence(self):
        from etf_universe import SYMBOLS as ETF7
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return NOW
        class Reader:
            def call_tool(self, tool, params): return {'status':'ok','data':{}}
        class Broker:
            def call_tool(self, tool, args, **kwargs):
                return {'status':'ok','data':{'rsp_cd':'00048','rsp_msg':'매수주문이 완료되었습니다','Output_0':{'mkt_orr_no':1}}}
        def pages(broker, operation, fields, block):
            return {'Output_0': []} if operation == 'krstockInquiryDailyOrderExecution' else {'Output_0': {'orr_pbl_amt1':1000000}, 'Output_1':[]}
        bars = [{'bsop_time':f'11{58-i:02d}00','stck_oprc':str(1400-i),
                 'stck_hgpr':str(1401-i),'stck_lwpr':str(1399-i),
                 'stck_prpr':str(1400-i),'vol':'100'} for i in range(8)]
        jev = {'model':'jev-1.14.0','chart_sha256':'a'*64,'response_time':NOW.isoformat(),
               'candidate':{'type':'choice','choice':'SEMILEV','confidence':0.75}}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);enable_models(root);db=p.connect_journal(root/'orders.sqlite3',p.account_hash(ALT))
            with patch.object(p,'datetime',Clock), patch.object(p,'paper_pages',side_effect=pages), \
                 patch.object(p,'valid_bars',return_value=bars), patch('etf_news.headlines7',return_value=[{'title':'market','published':NOW.isoformat()}]), \
                 patch.object(p,'decision7',return_value=('WAIT',0.9,{'response_time':NOW.isoformat()})), \
                 patch.object(p,'jev7',return_value=jev), patch.object(p,'fresh_quote',return_value={'askp':1300}):
                self.assertEqual(p.cycle(db,Reader(),Broker(),ALT,root,True,NOW,etf7=True),'buy')
            self.assertEqual(db.execute('SELECT symbol,status FROM intents').fetchone(),(ETF7['SEMILEV'],'acknowledged'))
            with p.sqlite3.connect(root/'trading-ledger.sqlite3') as ledger:
                records=[json.loads(row[0]) for row in ledger.execute('SELECT data FROM events_v2 ORDER BY offset')]
            d=next(x for x in records if x['event']=='decision')
            j=next(x for x in records if x['event']=='jev_shadow')
            self.assertEqual(set(d['market_bars']),set(ETF7))
            self.assertEqual(d['universe'],'etf7')
            self.assertEqual(j['jev_choice'],'SEMILEV')
            self.assertEqual(d['cycle_id'],j['cycle_id'])
            db.close()

    def test_etf7_stale_decision_blocks_order_and_records_reason(self):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return NOW
        class Reader:
            def call_tool(self, tool, params): return {'status':'ok','data':{}}
        def pages(broker, operation, fields, block):
            return {'Output_0': []} if operation == 'krstockInquiryDailyOrderExecution' else {'Output_0': {'orr_pbl_amt1': 1000000}, 'Output_1': []}
        bars = [{'bsop_time': f'11{58-i:02d}00', 'stck_prpr': str(1400-i)} for i in range(8)]
        old = (NOW-timedelta(seconds=61)).isoformat()
        jev = {'model':'jev-1.14.0','chart_sha256':'a'*64,'response_time':NOW.isoformat(),
               'candidate':{'type':'choice','choice':'KQ150','confidence':0.9}}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); enable_models(root); db=p.connect_journal(root/'orders.sqlite3',p.account_hash(ALT))
            with patch.object(p,'datetime',Clock), patch.object(p,'paper_pages',side_effect=pages), \
                 patch.object(p,'valid_bars',return_value=bars), patch('etf_news.headlines7',return_value=[{'title':'market','published':NOW.isoformat()}]), \
                 patch.object(p,'decision7',return_value=('WAIT',0.9,{'response_time':old})), patch.object(p,'jev7',return_value=jev), \
                 patch.object(p,'volatility',return_value=1), patch.object(p,'journal_event') as events, patch.object(p,'submit') as submit:
                self.assertEqual(p.cycle(db,Reader(),object(),ALT,root,True,NOW,etf7=True),'wait')
                submit.assert_not_called()
                self.assertEqual(events.call_args.kwargs['reason'],'stale_model_signal')
            db.close()

    def test_etf7_news_timeout_waits_without_model_or_order(self):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return NOW
        class Reader:
            def call_tool(self, tool, params): return {'status':'ok','data':{}}
        def pages(broker, operation, fields, block):
            return {'Output_0': []} if operation == 'krstockInquiryDailyOrderExecution' else {'Output_0': {}, 'Output_1': []}
        bars = [{'bsop_time':'115800','stck_prpr':'1400'} for _ in range(6)]
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); enable_models(root); db=p.connect_journal(root/'orders.sqlite3',p.account_hash(ALT))
            with patch.object(p,'datetime',Clock), patch.object(p,'paper_pages',side_effect=pages), \
                 patch.object(p,'valid_bars',return_value=bars), patch('etf_news.headlines7',side_effect=TimeoutError('private')), \
                 patch.object(p,'decision7') as decision, patch.object(p,'journal_event') as events, patch.object(p,'submit') as submit:
                self.assertEqual(p.cycle(db,Reader(),object(),ALT,root,True,NOW,etf7=True),'wait')
                decision.assert_not_called(); submit.assert_not_called()
                self.assertEqual(events.call_args.args[1],'v2_news_error')
                self.assertEqual(events.call_args.kwargs['error_type'],'TimeoutError')
                self.assertNotIn('private',repr(events.call_args))
            db.close()

    def test_no_market_news_still_asks_jev_for_entries_and_holdings(self):
        # user 2026-11-24: no fresh story (or every feed down) stopped every Jev call 13:12~13:19; the judgment goes on without it
        from xml.etree.ElementTree import ParseError
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return NOW
        class Reader:
            def call_tool(self, tool, params): return {'status':'ok','data':{}}
        def pages(broker, operation, fields, block):
            return {'Output_0': []} if operation == 'krstockInquiryDailyOrderExecution' else {'Output_0': {}, 'Output_1': []}
        bars = [{'bsop_time':'115800','stck_prpr':'1400'} for _ in range(6)]
        wait = {'model':'jev-1.14.0','chart_sha256':'a'*64,'response_time':NOW.isoformat(),
                'candidate':{'type':'choice','choice':'WAIT','confidence':0.9}}
        sell = {'model':'jev-1.14.0','chart_sha256':'a'*64,'response_time':NOW.isoformat(),
                'holding':{'type':'choice','choice':'SELL','confidence':0.95}}
        for failure, name in ((ValueError('no recent news'),'ValueError'), (TimeoutError('private'),'TimeoutError'),
                              (OSError('private'),'OSError'), (ParseError('private'),'ParseError')):
            with self.subTest(failure=name), tempfile.TemporaryDirectory() as temp:
                root=Path(temp)/'shared-etf-paper'; root.mkdir()
                (root/'ai-modes.json').write_text(json.dumps({'version':1,'jev':True,'decision':False}))
                db=p.connect_journal(root/'orders.sqlite3',p.account_hash(ALT))
                with patch.object(p,'datetime',Clock), patch.object(p,'paper_pages',side_effect=pages), \
                     patch.object(p,'valid_bars',return_value=bars), patch.object(p,'fresh_etf7_bars',return_value=True), \
                     patch('etf_news.headlines7',side_effect=failure), patch.object(p,'investor_flows',return_value=None), \
                     patch.object(p,'jev7',return_value=wait) as entry, patch.object(p,'jev_holding7',return_value=sell) as holding, \
                     patch.object(p,'slot_entry',return_value=None, create=True), \
                     patch.object(p,'journal_event') as events, patch.object(p,'submit') as submit:
                    p.cycle(db,Reader(),object(),ALT,root,True,NOW,etf7=True)
                    self.assertTrue(p.holding_action(Reader(),root,'229200',100,100,etf7=True,modes={'decision':False,'jev':True}))
                submit.assert_not_called()
                self.assertEqual((entry.call_args.args[1], holding.call_args.args[1]), ([], []))
                said=[c for c in events.call_args_list if c.args[1]=='v2_news_error']
                self.assertEqual([c.kwargs['error_type'] for c in said],[name]*2)
                self.assertNotIn('private',repr(said))
                gates=[c.kwargs['reason'] for c in events.call_args_list if c.args[1]=='v2_entry_gate']
                self.assertEqual(gates,['jev_wait'])  # the strength and new-listing paths were looked at, as on any Jev WAIT
                db.close()

    def test_a_jev_error_records_its_internal_message_only(self):
        self.assertEqual(p.internal_detail(ValueError('ETF Jev response unavailable')), {'detail': 'etf jev response unavailable'})
        for hidden in (ValueError('account 12345678'), OSError('HTTP Error 429: x'), ValueError('한글'), ValueError('')):
            self.assertEqual(p.internal_detail(hidden), {})

    def test_etf7_preclose_review_and_protective_exit(self):
        at = NOW.replace(hour=14, minute=51)
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return at
        class Reader:
            def call_tool(self, tool, params): return {'status':'ok','data':{}}
        bars = [{'bsop_time':'144900', 'stck_prpr':'1400'} for _ in range(6)]
        jev = {'model':'jev-1.14.0','chart_sha256':'a'*64,'response_time':at.isoformat(),
               'holding':{'type':'choice','choice':'SELL','confidence':0.7}}
        with patch.object(p,'datetime',Clock), patch.object(p,'valid_bars',return_value=bars), \
             patch('etf_news.headlines7',return_value=[{'title':'market','published':at.isoformat()}]), \
             patch.object(p,'holding_decision7',return_value=('SELL',0.9,{'response_time':at.isoformat()})) as judge, \
             patch.object(p,'jev_holding7',return_value=jev), patch.object(p,'journal_event') as events:
            self.assertTrue(p.holding_action(Reader(),Path('/tmp'),'229200',100,100,etf7=True))
            review = next(c for c in events.call_args_list if c.args[1]=='v2_holding_decision')
            self.assertEqual(review.kwargs['review_type'],'preclose')
            self.assertEqual(review.kwargs['model_response_time'],at.isoformat())
            jev_review = next(c for c in events.call_args_list if c.args[1]=='jev_holding_decision')
            self.assertEqual(jev_review.kwargs['choice'],'SELL')
            self.assertEqual(jev_review.kwargs['review_type'],'preclose')
            judge.reset_mock()
            self.assertTrue(p.holding_action(Reader(),Path('/tmp'),'229200',100,98,etf7=True))
            judge.assert_not_called()

    def test_etf7_expired_signal_at_final_quote_never_buys(self):
        class Clock(datetime):
            late = False
            @classmethod
            def now(cls, tz=None): return NOW + timedelta(seconds=61 if cls.late else 0)
        class Reader:
            def call_tool(self, tool, params): return {'status':'ok','data':{}}
        def pages(broker, operation, fields, block):
            return {'Output_0': []} if operation == 'krstockInquiryDailyOrderExecution' else {'Output_0': {'orr_pbl_amt1': 1000000}, 'Output_1': []}
        def quote(*args):
            Clock.late = True
            return {'askp':1300}
        bars = [{'bsop_time': f'11{58-i:02d}00', 'stck_prpr': str(1400-i)} for i in range(8)]
        jev = {'model':'jev-1.14.0', 'chart_sha256':'a'*64, 'response_time':NOW.isoformat(),
               'candidate':{'type':'choice','choice':'KQ150','confidence':0.9}}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); enable_models(root); db=p.connect_journal(root/'orders.sqlite3',p.account_hash(ALT))
            with patch.object(p,'datetime',Clock), patch.object(p,'paper_pages',side_effect=pages), \
                 patch.object(p,'valid_bars',return_value=bars), patch('etf_news.headlines7',return_value=[{'title':'market','published':NOW.isoformat()}]), \
                 patch.object(p,'decision7',return_value=('WAIT',0.9,{'response_time':NOW.isoformat()})), \
                 patch.object(p,'jev7',return_value=jev), patch.object(p,'volatility',return_value=1), \
                 patch.object(p,'fresh_quote',side_effect=quote), patch.object(p,'journal_event') as events, patch.object(p,'submit') as submit:
                self.assertEqual(p.cycle(db,Reader(),object(),ALT,root,True,NOW,etf7=True),'wait')
                submit.assert_not_called()
                self.assertEqual(events.call_args.kwargs['reason'],'stale_model_signal')
            db.close()

    def test_etf7_early_exit_requires_both_models_and_fresh_evidence(self):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return NOW
        class Reader:
            def call_tool(self, tool, params): return {'status':'ok','data':{}}
        bars = [{'bsop_time':'115800','stck_prpr':'1400'} for _ in range(6)]
        def jev(choice, confidence, stamp=NOW.isoformat()):
            return {'model':'jev-1.14.0','chart_sha256':'a'*64,'response_time':stamp,
                    'holding':{'type':'choice','choice':choice,'confidence':confidence}}
        cases = [('SELL',0.8,jev('SELL',0.7),True), ('HOLD',0.9,jev('SELL',0.9),False),
                 ('SELL',0.79,jev('SELL',0.9),False), ('SELL',0.9,jev('HOLD',0.9),False),
                 ('SELL',0.9,jev('SELL',0.69),False),
                 ('SELL',0.9,jev('SELL',0.9,(NOW-timedelta(seconds=61)).isoformat()),False),
                 ('SELL',0.9,ValueError('provider unavailable'),False)]
        for decision, confidence, other, expected in cases:
            with self.subTest(decision=decision,confidence=confidence,other=str(other)), \
                 patch.object(p,'datetime',Clock), patch.object(p,'valid_bars',return_value=bars), \
                 patch('etf_news.headlines7',return_value=[{'title':'market','published':NOW.isoformat()}]), \
                 patch.object(p,'holding_decision7',return_value=(decision,confidence,{'response_time':NOW.isoformat()})), \
                 patch.object(p,'jev_holding7',side_effect=other if isinstance(other,Exception) else None,
                              return_value=None if isinstance(other,Exception) else other), patch.object(p,'journal_event'):
                self.assertEqual(p.holding_action(Reader(),Path('/tmp'),'229200',100,100,etf7=True),expected)
        # the error record keeps an internal message (2026-11-12: two ValueErrors were logged with no cause)
        with patch.object(p,'datetime',Clock), patch.object(p,'valid_bars',return_value=bars), \
             patch('etf_news.headlines7',return_value=[{'title':'market','published':NOW.isoformat()}]), \
             patch.object(p,'holding_decision7',return_value=('SELL',0.9,{'response_time':NOW.isoformat()})), \
             patch.object(p,'jev_holding7',side_effect=ValueError('provider unavailable')), patch.object(p,'journal_event') as events:
            p.holding_action(Reader(),Path('/tmp'),'229200',100,100,etf7=True)
        errors = [c.kwargs for c in events.call_args_list if c.args[1] == 'v2_holding_decision_error']
        self.assertEqual((errors[0]['error_type'], errors[0]['detail']), ('ValueError', 'provider unavailable'))

    def test_jev_holding_input_has_no_retired_vibe_view(self):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return NOW
        class Reader:
            def call_tool(self, tool, params): return {'status': 'ok', 'data': {}}
        bars = [{'bsop_time': '115800', 'stck_prpr': '1400'} for _ in range(6)]
        result = {'model': 'jev-1.14.0', 'chart_sha256': 'a' * 64, 'response_time': NOW.isoformat(),
                  'holding': {'type': 'choice', 'choice': 'HOLD', 'confidence': 0.9}}
        with tempfile.TemporaryDirectory() as temp, \
             patch.dict(p.__dict__, {'vibe_view': lambda *a, **k: {'its_paper_trades': [{'old': 1}]}}), \
             patch.object(p, 'datetime', Clock), patch.object(p, 'valid_bars', return_value=bars), \
             patch.object(p, 'fresh_etf7_bars', return_value=True), \
             patch('etf_news.headlines7', return_value=[{'title': 'market', 'published': NOW.isoformat()}]), \
             patch.object(p, 'jev_holding7', return_value=result) as jev, patch.object(p, 'journal_event'), \
             patch.object(p, 'track_record', return_value={'trades': 1}), \
             patch.object(p, 'market_view', return_value=None), patch.object(p, 'trading_journal_brief', return_value=None), \
             patch.object(p, 'premarket_view', return_value=None), patch.object(p.oversold_cases, 'bars_context', return_value=None):
            self.assertFalse(p.holding_action(Reader(), Path(temp) / 'shared-etf-paper', '229200', 100, 100, etf7=True,
                                              modes={'decision': False, 'jev': True}, position_extra={'quantity': 5}))
        state = jev.call_args.kwargs['extra_state'] or {}
        self.assertEqual(state.get('own_track_record'), {'trades': 1})
        import market_playbook  # user 2026-11-18: the 실전 시황 트레이딩 lens as reference context
        self.assertEqual(state.get('market_playbook'), market_playbook.brief('holding'))
        self.assertFalse({'vibe_trading_view', 'vibe_strategy', 'vibe_position_source'} & set(state))  # vibe is the long-term engine since 11-17

    def test_etf7_holding_evidence_survives_ledger_ingest(self):
        from etf_universe import SYMBOLS as ETF7
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return NOW
        class Reader:
            def call_tool(self, tool, params): return {'status':'ok','data':{}}
        bars = [{'bsop_time':f'11{58-i:02d}00','stck_oprc':'1400','stck_hgpr':'1401',
                 'stck_lwpr':'1399','stck_prpr':'1400','vol':'100'} for i in range(6)]
        jev = {'model':'jev-1.14.0','chart_sha256':'a'*64,'response_time':NOW.isoformat(),
               'holding':{'type':'choice','choice':'SELL','confidence':0.7}}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with patch.object(p,'datetime',Clock), patch.object(p,'valid_bars',return_value=bars), \
                 patch('etf_news.headlines7',return_value=[{'title':'market','published':NOW.isoformat()}]), \
                 patch.object(p,'holding_decision7',return_value=('SELL',0.8,{'response_time':NOW.isoformat()})), \
                 patch.object(p,'jev_holding7',return_value=jev):
                self.assertTrue(p.holding_action(Reader(),root,'229200',100,100,cycle_id='a'*32,etf7=True))
            with p.sqlite3.connect(root/'trading-ledger.sqlite3') as db:
                events=[json.loads(row[0]) for row in db.execute('SELECT data FROM events_v2 ORDER BY offset')]
            self.assertEqual([x['event'] for x in events],['v2_holding_decision','jev_holding_decision'])
            self.assertEqual(set(events[0]['market_bars']),set(ETF7))
            self.assertEqual(events[0]['market_source'],'NH KRX 1-minute completed bars')
            self.assertEqual(events[0]['universe'],'etf7')
            self.assertEqual(events[1]['choice'],'SELL')
            self.assertEqual(events[1]['cycle_id'],'a'*32)
            from paper_learning import evaluate
            self.assertEqual(evaluate(root/'trading-ledger.sqlite3',root/'observations.sqlite3'),
                             {'observed':0,'insufficient':0})

    def test_etf7_expired_sell_signal_does_not_block_protective_exit(self):
        from etf_universe import SYMBOLS as ETF7
        class Clock(datetime):
            late = False
            @classmethod
            def now(cls, tz=None): return NOW + timedelta(seconds=61 if cls.late else 0)
        class Reader:
            def call_tool(self, tool, params): return {'status':'ok','data':{}}
        class Broker:
            def call_tool(self, tool, params):
                return {'status':'ok','data':{'rsp_cd':'XA102','Output_0':{'iem_cd':'229200','sll_pbl_qty':1}}}
        def pages(broker, operation, fields, block):
            return {'Output_0': []} if operation == 'krstockInquiryDailyOrderExecution' else {'Output_0': {}, 'Output_1':[{'iem_cd':'229200','rsdl_qty':1,'phs_pr':100}]}
        bars = {name:[{'bsop_time':'115800'}] for name in ETF7}
        def hold(*args, **kwargs):
            kwargs['signal_context'].update(response_time=NOW.isoformat(), jev_time=NOW.isoformat(), bars=bars)
            return True
        # An expired AI sell moves on to the next holding/entry stage ('wait': models are off in this temp root).
        for final_bid, expected in ((100,'wait'), (98,'sell')):
            with self.subTest(final_bid=final_bid), tempfile.TemporaryDirectory() as temp:
                Clock.late = False
                def quote(*args):
                    quote.count += 1
                    if quote.count == 3:
                        Clock.late = True
                        return {'bidp':final_bid,'iem_cd':'229200','market_status':'1','hoga_bsop_hour':'12:00:00'}
                    return {'bidp':100,'iem_cd':'229200','market_status':'1','hoga_bsop_hour':'12:00:00'}
                quote.count = 0
                root=Path(temp); db=p.connect_journal(root/'orders.sqlite3',p.account_hash(ALT))
                db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)',('a','229200','BUY',1,100,NOW.isoformat()));db.commit()
                with patch.object(p,'datetime',Clock), patch.object(p,'paper_pages',side_effect=pages), \
                     patch.object(p,'holding_action',side_effect=hold), patch.object(p,'fresh_quote',side_effect=quote), \
                     patch.object(p,'journal_event'), patch.object(p,'submit',return_value=('b'*32,1)) as submit:
                    self.assertEqual(p.cycle(db,Reader(),Broker(),ALT,root,True,NOW,etf7=True),expected)
                    self.assertEqual(submit.call_count,1 if expected=='sell' else 0)
                    if expected == 'sell':
                        self.assertTrue(submit.call_args.kwargs['signal_check']())
                        self.assertEqual(submit.call_args.kwargs['deadline_ms'],int((NOW+timedelta(seconds=120)).timestamp()*1000))
                db.close()

    def test_only_proven_pre_send_abort_finalizes_local_intent(self):
        class Broker:
            def __init__(self, response): self.response=response
            def call_tool(self, tool, args, **kwargs):
                self.checks=(kwargs.get('pre_reserve_check'),kwargs.get('pre_send_check'))
                return self.response
        proven = {'status':'error','error_type':'NHPreSendCheckFailed',
                  'order_safety':{'state':'aborted_no_send','resubmit_allowed':False}}
        for response, expected in ((proven,'terminal'),
                                   ({'status':'error','error_type':'TimeoutError'},'uncertain'),
                                   ({'status':'error','error_type':'NHPreSendCheckFailed',
                                     'order_safety':{'state':'uncertain','resubmit_allowed':False}},'uncertain')):
            with self.subTest(response=response), tempfile.TemporaryDirectory() as temp:
                db=p.connect_journal(Path(temp)/'orders.sqlite3',p.account_hash(ALT));broker=Broker(response)
                check=lambda:False
                with self.assertRaises(p.PaperOrderNotSent if expected=='terminal' else ValueError):
                    p.submit(db,broker,ALT,'20261106','229200','BUY',1,100,etf7=True,signal_check=check,
                             deadline_ms=int(p.time.time()*1000)+60000)
                self.assertEqual(broker.checks,(check,check))
                self.assertEqual(db.execute('SELECT status FROM intents').fetchone()[0],expected)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM pre_send_aborts').fetchone()[0],int(expected=='terminal'))
                db.close()

    def test_protective_pre_send_abort_is_recorded_separately(self):
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None): return NOW
        class Reader:
            def call_tool(self,tool,params): return {'status':'ok','data':{}}
        class Broker:
            def call_tool(self,tool,params): return {'status':'ok','data':{'rsp_cd':'XA102','Output_0':{'iem_cd':'229200','sll_pbl_qty':1}}}
        def pages(broker,operation,fields,block):
            return {'Output_0':[]} if operation=='krstockInquiryDailyOrderExecution' else {'Output_0':{},'Output_1':[{'iem_cd':'229200','rsdl_qty':1,'phs_pr':100}]}
        quote={'bidp':98,'iem_cd':'229200','market_status':'1','hoga_bsop_hour':'12:00:00'}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);db=p.connect_journal(root/'orders.sqlite3',p.account_hash(ALT))
            db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)',('a','229200','BUY',1,100,NOW.isoformat()));db.commit()
            with patch.object(p,'datetime',Clock),patch.object(p,'paper_pages',side_effect=pages), \
                 patch.object(p,'fresh_quote',return_value=quote),patch.object(p,'journal_event') as events, \
                 patch.object(p,'submit',side_effect=p.PaperOrderNotSent('pre-send')) as submit:
                self.assertEqual(p.cycle(db,Reader(),Broker(),ALT,root,True,NOW,etf7=True),'protective_wait')
                self.assertEqual(events.call_args.args[1],'v2_risk_exit_error')
                self.assertTrue(submit.call_args.kwargs['signal_check']())
            db.close()

    def test_expired_signal_aborts_both_journals_before_mcp_send(self):
        class Broker:
            sent=0
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
            async def call_tool(self,*args,**kwargs):
                self.sent+=1
                raise AssertionError('transport must not be called')
        with tempfile.TemporaryDirectory() as temp, patch.dict('os.environ',{'VIBE_TRADING_HOME':temp}), \
             patch.object(p.MCPServerAdapter,'_nh_buy_preflight'):
            broker=Broker();config=MCPServerConfig(command='python',args=['nhplug_mcp.py','--mode','paper','--account',ALT])
            adapter=p.MCPServerAdapter('nhplug_paper_isolated',config,client_factory=lambda:broker)
            root=Path(temp);db=p.connect_journal(root/'orders.sqlite3',p.account_hash(ALT))
            checks=iter([True,False])
            with self.assertRaises(p.PaperOrderNotSent):
                p.submit(db,adapter,ALT,'20261106','229200','BUY',1,100,etf7=True,signal_check=lambda:next(checks),
                         deadline_ms=int(p.time.time()*1000)+60000)
            self.assertEqual(broker.sent,0)
            self.assertEqual(db.execute('SELECT status FROM intents').fetchone()[0],'terminal')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pre_send_aborts').fetchone()[0],1)
            with p.sqlite3.connect(root/'nhplug/orders.sqlite3') as guard:
                self.assertEqual(guard.execute('SELECT state FROM intents').fetchone()[0],'terminal')
                self.assertEqual(guard.execute('SELECT COUNT(*) FROM pre_send_aborts').fetchone()[0],1)
            db.close()

    def test_transport_failure_keeps_both_journals_uncertain(self):
        class Broker:
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
            async def call_tool(self,*args,**kwargs): raise TimeoutError('response lost')
        with tempfile.TemporaryDirectory() as temp, patch.dict('os.environ',{'VIBE_TRADING_HOME':temp}), \
             patch.object(p.MCPServerAdapter,'_nh_buy_preflight'):
            config=MCPServerConfig(command='python',args=['nhplug_mcp.py','--mode','paper','--account',ALT])
            adapter=p.MCPServerAdapter('nhplug_paper_isolated',config,client_factory=Broker)
            root=Path(temp);db=p.connect_journal(root/'orders.sqlite3',p.account_hash(ALT))
            with self.assertRaisesRegex(ValueError,'uncertain'):
                p.submit(db,adapter,ALT,'20261106','229200','BUY',1,100,etf7=True,signal_check=lambda:True,
                         deadline_ms=int(p.time.time()*1000)+60000)
            self.assertEqual(db.execute('SELECT status FROM intents').fetchone()[0],'uncertain')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pre_send_aborts').fetchone()[0],0)
            with p.sqlite3.connect(root/'nhplug/orders.sqlite3') as guard:
                self.assertEqual(guard.execute('SELECT state FROM intents').fetchone()[0],'uncertain')
            db.close()

    def test_node_deadline_proof_must_match_before_local_abort(self):
        from fastmcp.client.client import CallToolResult
        class Broker:
            def __init__(self, tamper): self.tamper=tamper
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
            async def call_tool(self,name,arguments,**kwargs):
                self.name=name; guard=dict(arguments['order_guard'])
                if self.tamper: guard['expires_at_epoch_ms']+=1
                marker={'version':1,'code':'NH_SIGNAL_EXPIRED','no_send':True,**guard}
                data={'_nh_order_guard':marker}
                return CallToolResult(content=[],structured_content=data,data=data,is_error=False,meta=None)
        for tamper in (False,True):
            with self.subTest(tamper=tamper), tempfile.TemporaryDirectory() as temp, \
                 patch.dict('os.environ',{'VIBE_TRADING_HOME':temp}), patch.object(p.MCPServerAdapter,'_nh_buy_preflight'):
                config=MCPServerConfig(command='python',args=['nhplug_mcp.py','--mode','paper','--account',ALT])
                broker=Broker(tamper); adapter=p.MCPServerAdapter('nhplug_paper_isolated',config,client_factory=lambda:broker)
                root=Path(temp);db=p.connect_journal(root/'orders.sqlite3',p.account_hash(ALT))
                with self.assertRaises(ValueError if tamper else p.PaperOrderNotSent):
                    p.submit(db,adapter,ALT,'20261106','229200','BUY',1,100,etf7=True,signal_check=lambda:True,
                             deadline_ms=int(p.time.time()*1000)+60000)
                self.assertEqual(broker.name,'call_api_guarded')
                self.assertEqual(db.execute('SELECT status FROM intents').fetchone()[0],'uncertain' if tamper else 'terminal')
                with p.sqlite3.connect(root/'nhplug/orders.sqlite3') as guard:
                    self.assertEqual(guard.execute('SELECT state FROM intents').fetchone()[0],'uncertain' if tamper else 'terminal')
                db.close()

    def test_etf7_next_day_holding_restarts_only_after_reconciliation(self):
        class Reader:
            def call_tool(self, tool, params): return {'status':'ok','data':{}}
        class Broker:
            def call_tool(self, *args, **kwargs): raise AssertionError('no order may be sent')
        next_day=NOW+timedelta(days=3)
        class Clock(datetime):  # midday, so the pre-close exit (14:50) cannot depend on when the test runs
            @classmethod
            def now(cls, tz=None): return next_day
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); path=root/'orders.sqlite3'; db=p.connect_journal(path,p.account_hash(ALT))
            db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)',('a','229200','BUY',1,100,NOW.isoformat()));db.commit();db.close()
            db=p.connect_journal(path,p.account_hash(ALT))
            def pages(broker, operation, fields, block):
                if operation == 'krstockInquiryDailyOrderExecution': return {'Output_0':[]}
                return {'Output_0':{},'Output_1':[{'iem_cd':'229200','rsdl_qty':1,'phs_pr':100}]}
            with patch.object(p,'paper_pages',side_effect=pages), patch.object(p,'fresh_quote',return_value={'bidp':100}), \
                 patch.object(p,'holding_action',return_value=False), patch.object(p,'submit') as submit, patch.object(p,'datetime',Clock):
                # A kept holding no longer ends the cycle; entry evaluation runs (models disabled here).
                self.assertEqual(p.cycle(db,Reader(),Broker(),ALT,root,True,next_day,etf7=True),'wait')
                submit.assert_not_called()
            def mismatch(broker, operation, fields, block):
                return {'Output_0':[]} if operation == 'krstockInquiryDailyOrderExecution' else {'Output_0':{},'Output_1':[]}
            with patch.object(p,'paper_pages',side_effect=mismatch), patch.object(p,'submit') as submit:
                with self.assertRaisesRegex(ValueError,'differ from journal'):
                    p.cycle(db,Reader(),Broker(),ALT,root,True,next_day,etf7=True)
                submit.assert_not_called()
            db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?,?,?,?)',('b'*32,'20261109','229200','SELL',1,100,'uncertain',None,0));db.commit()
            with patch.object(p,'submit') as submit:
                with self.assertRaisesRegex(ValueError,'identity unknown'):
                    p.cycle(db,Reader(),Broker(),ALT,root,True,next_day,etf7=True)
                submit.assert_not_called()
            db.close()

    def test_etf7_learning_never_uses_other_universe_or_legacy_version(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with p.sqlite3.connect(root/'paper-model.sqlite3') as db:
                db.execute('CREATE TABLE runs (digest TEXT PRIMARY KEY, version TEXT)')
                db.execute('CREATE TABLE predictions (run_digest TEXT, symbol TEXT, decision_time TEXT, alpha INTEGER, beta INTEGER, probability REAL, universe TEXT)')
                db.executemany('INSERT INTO runs VALUES (?,?)', [('old','beta-direction-prequential-v1'),('new','beta-direction-prequential-v2')])
                earlier=(NOW-timedelta(minutes=2)).isoformat()
                db.executemany('INSERT INTO predictions VALUES (?,?,?,?,?,?,?)', [
                    ('old','K200',earlier,21,1,0.95,None),
                    ('new','K200',earlier,1,21,0.05,'etf7'),
                    ('new','K200',earlier,2,20,0.09,'legacy2')])
            self.assertEqual(p.learned_filter(root,'K200',NOW,etf7=True)['probability'],0.05)
            self.assertEqual(p.learned_filter(root,'K200',NOW)['probability'],0.09)
            with p.sqlite3.connect(root/'paper-model.sqlite3') as db:
                db.execute("DELETE FROM predictions WHERE universe='legacy2'")
            self.assertEqual(p.learned_filter(root,'K200',NOW)['probability'],0.95)
            with p.sqlite3.connect(root/'paper-model.sqlite3') as db:
                db.execute("DELETE FROM predictions WHERE universe='etf7'")
            self.assertEqual(p.learned_filter(root,'K200',NOW,etf7=True)['status'],'insufficient')

    def test_news_timeout_waits_without_order_and_next_cycle_recovers(self):
        class Reader:
            def call_tool(self, tool, params):
                return {'status': 'ok', 'data': {}}
        class Broker:
            def call_tool(self, *args, **kwargs):
                raise AssertionError('unexpected order')
        def pages(broker, operation, fields, block):
            return {'Output_0': []} if operation == 'krstockInquiryDailyOrderExecution' else {'Output_0': {}, 'Output_1': []}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = p.connect_journal(root / 'orders.sqlite3', p.account_hash(ALT))
            bars = [{'stck_prpr': str(1400-i)} for i in range(10)]
            with patch.object(p, 'paper_pages', side_effect=pages), patch.object(p, 'valid_bars', return_value=bars), \
                 patch.object(p, 'headlines', side_effect=[TimeoutError('private'), []]), \
                 patch.object(p, 'decision', return_value=('WAIT', 0.9, {})) as decision, \
                 patch.object(p, 'jev', return_value={'model': 'jev-1.14.0', 'chart_sha256': 'a'*64,
                     'candidate': {'type': 'choice', 'choice': 'WAIT', 'confidence': 0.9}}), \
                 patch.object(p, 'volatility', return_value=1), patch.object(p, 'journal_event') as events:
                self.assertEqual(p.cycle(db, Reader(), Broker(), ALT, root, True, NOW), 'wait')
                decision.assert_not_called()
                self.assertEqual(events.call_args.args[1], 'v2_news_error')
                self.assertEqual(events.call_args.kwargs['error_type'], 'TimeoutError')
                self.assertEqual(len(events.call_args.kwargs['cycle_id']), 32)
                self.assertNotIn('private', repr(events.call_args))
                self.assertEqual(p.cycle(db, Reader(), Broker(), ALT, root, True, NOW), 'wait')
                decision.assert_called_once()
                self.assertEqual(db.execute('SELECT COUNT(*) FROM intents').fetchone()[0], 0)
            db.close()
    def test_shared_default_requires_verified_account_and_full_existing_policy(self):
        class Guard:
            def __init__(self, args): self.scope = 'a' * 64
            def paper_policy(self):
                return {'enabled': True, 'auto_loss_stop': True, 'krx_session_check': True}
        with tempfile.TemporaryDirectory() as temp, patch.object(p, 'NHOrderGuard', Guard):
            root = Path(temp)
            registry = root / 'nhplug' / 'paper-account-owners.json'
            registry.parent.mkdir(parents=True)
            registry.write_text(json.dumps({'version': 1, 'accounts': {'a' * 64: 'nhplug_paper_isolated'}}))
            chosen = p.shared_default_config(configs(), root, Fake)
            self.assertEqual(p.paper_account(chosen), DEFAULT)
            registry.write_text(json.dumps({'version': 1, 'accounts': {'a' * 64: 'nhplug_paper'}}))
            with self.assertRaisesRegex(ValueError, 'owner'):
                p.shared_default_config(configs(), root, Fake)
        class Disabled(Guard):
            def paper_policy(self): return {'enabled': True, 'auto_loss_stop': False, 'krx_session_check': True}
        with tempfile.TemporaryDirectory() as temp, patch.object(p, 'NHOrderGuard', Disabled):
            with self.assertRaisesRegex(ValueError, 'policy'):
                p.shared_default_config(configs(), Path(temp), Fake)
        class Unknown(Fake):
            def call_tool(self, tool, params):
                return {'status':'ok', 'data': {'rsp_cd':'00000', 'Output_0':[{'acct_no':DEFAULT,'acct_type':'01'}]}}
        with tempfile.TemporaryDirectory() as temp, patch.object(p, 'NHOrderGuard', Guard):
            root = Path(temp)
            registry = root / 'nhplug' / 'paper-account-owners.json'
            registry.parent.mkdir(parents=True)
            registry.write_text(json.dumps({'version': 1, 'accounts': {'a' * 64: 'nhplug_paper_isolated'}}))
            with self.assertRaisesRegex(ValueError, 'account'):
                p.shared_default_config(configs(), root, Unknown)

    def test_shared_runner_uses_different_journal_and_preserves_isolated_record(self):
        class Guard:
            def __init__(self, args): self.scope = 'a' * 64
            def paper_policy(self):
                return {'enabled': True, 'auto_loss_stop': True, 'krx_session_check': True}
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return NOW
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            isolated = root / 'nhplug' / 'isolated-paper'
            isolated.mkdir(parents=True)
            (isolated / 'account.json').write_text('{"original": true}')
            (root / 'nhplug' / 'paper-account-owners.json').write_text(json.dumps({'version': 1, 'accounts': {'a' * 64: 'nhplug_paper_isolated'}}))
            with patch.object(p, 'NHOrderGuard', Guard), patch.object(p, 'MCPServerAdapter', Fake), \
                 patch.object(p, 'datetime', Clock), patch.object(p, 'cycle', return_value='wait') as cycle:
                p.run(configs(), root, execute_orders=True, once=True, shared_default=True)
            self.assertEqual((isolated / 'account.json').read_text(), '{"original": true}')
            self.assertTrue((root / 'nhplug' / 'shared-etf-paper' / 'orders.sqlite3').exists())
            self.assertEqual(cycle.call_args.args[3], DEFAULT)
            self.assertEqual(cycle.call_args.args[1].config.args.count('--guarded-paper'),0)
            self.assertEqual(cycle.call_args.args[2].config.args.count('--guarded-paper'),1)

    def test_a_failed_quote_read_does_not_end_the_runner_but_other_errors_do(self):
        class Guard:
            def __init__(self, args): self.scope = 'a' * 64
            def paper_policy(self):
                return {'enabled': True, 'auto_loss_stop': True, 'krx_session_check': True}
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return NOW
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'nhplug').mkdir(parents=True)
            (root / 'nhplug' / 'paper-account-owners.json').write_text(json.dumps({'version': 1, 'accounts': {'a' * 64: 'nhplug_paper_isolated'}}))
            run = lambda error: p.run(configs(), root, execute_orders=True, once=True, shared_default=True)
            with patch.object(p, 'NHOrderGuard', Guard), patch.object(p, 'MCPServerAdapter', Fake), patch.object(p, 'datetime', Clock), \
                 patch.object(p, 'journal_event') as events:
                with patch.object(p, 'cycle', side_effect=ValueError('quote unavailable')):
                    run(None)  # one bad minute: logged, runner keeps going
                self.assertEqual(events.call_args.kwargs['reason'], 'transient_read_failure')
                self.assertEqual(events.call_args.kwargs['detail'], 'quote unavailable')  # which read failed
                with patch.object(p, 'cycle', side_effect=ValueError('paper broker page unavailable')):
                    run(None)  # NH paper read outage (11/13 14:34): retried next minute, not a crash
                self.assertEqual(events.call_args.kwargs['reason'], 'transient_read_failure')
                with patch.object(p, 'cycle', side_effect=ValueError('order history ambiguous')), self.assertRaisesRegex(ValueError, 'ambiguous'):
                    run(None)  # a safety stop still stops
                with patch.object(p, 'cycle', side_effect=ValueError('quote unavailable')), patch.object(p, 'TRANSIENT_READ_LIMIT', 1), \
                     self.assertRaisesRegex(ValueError, 'quote unavailable'):
                    run(None)  # too many in a row: stop as before

    def test_shared_runner_waits_a_minute_between_cycles(self):
        class Guard:
            def __init__(self, args): self.scope = 'a' * 64
            def paper_policy(self):
                return {'enabled': True, 'auto_loss_stop': True, 'krx_session_check': True}
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return NOW
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'nhplug').mkdir(parents=True)
            (root / 'nhplug' / 'paper-account-owners.json').write_text(json.dumps({'version': 1, 'accounts': {'a' * 64: 'nhplug_paper_isolated'}}))
            for engine, expected in (('jev', 60),):
                with self.subTest(engine=engine), \
                     patch.object(p, 'NHOrderGuard', Guard), patch.object(p, 'MCPServerAdapter', Fake), \
                     patch.object(p, 'datetime', Clock), patch.object(p, 'refresh_clock'), \
                     patch.object(p, 'in_observation_session', return_value=True), \
                     patch.object(p, 'cycle', return_value='wait'), patch.object(p, 'cycle_delay', return_value=40), \
                     patch.object(p.time, 'sleep', side_effect=RuntimeError('end test')) as sleep:
                    with self.assertRaisesRegex(RuntimeError, 'end test'):
                        p.run(configs(), root, execute_orders=True, shared_default=True)
                    sleep.assert_called_once_with(expected)

    def test_legacy_runner_keeps_original_broker_launcher(self):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return NOW
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);isolated=root/'nhplug/isolated-paper';isolated.mkdir(parents=True)
            (isolated/'account.json').write_text(json.dumps({'account_sha256':p.account_hash(ALT)}))
            with patch.object(p,'MCPServerAdapter',Fake), patch.object(p,'datetime',Clock), \
                 patch.object(p,'selected_config',return_value=p.isolated_config(configs()['nhplug_paper'],ALT)), \
                 patch.object(p,'cycle',return_value='wait') as cycle:
                p.run(configs(),root,once=True)
            self.assertNotIn('--guarded-paper',cycle.call_args.args[1].config.args)
            self.assertNotIn('--guarded-paper',cycle.call_args.args[2].config.args)

    def test_entry_models_require_valid_agreement_or_high_confidence_jev_fallback(self):
        jev = lambda choice, confidence: {'model':'jev-1.14.0', 'chart_sha256':'a'*64,
                                          'candidate': {'type': 'choice', 'choice': choice, 'confidence': confidence}}
        self.assertEqual(p.select_entry('WAIT', 0.95, jev('INV', 0.7)), ('INV', 'jev', 'entry_conditions_met'))
        self.assertEqual(p.select_entry('WAIT', 0.95, jev('INV', 0.699))[2], 'jev_low_confidence')
        self.assertEqual(p.select_entry('INV', 0.79, jev('INV', 0.7))[2], 'confidence_below_threshold')
        self.assertEqual(p.select_entry('INV', 0.8, jev('INV', 0.7)), ('INV', 'consensus', 'entry_conditions_met'))
        self.assertEqual(p.select_entry('INV', 0.9, jev('K200', 0.9))[2], 'model_disagreement')
        self.assertEqual(p.select_entry('INV', 0.9, jev('INV', 0.9)), ('INV', 'consensus', 'entry_conditions_met'))
        self.assertEqual(p.select_entry('INV', 0.9, jev('WAIT', 0.9))[2], 'jev_wait')
        self.assertEqual(p.select_entry('WAIT', 0.9, None)[2], 'jev_error')
        self.assertEqual(p.select_entry('WAIT', 0.9, {**jev('INV', 0.9), 'model':'other'})[2], 'jev_model_mismatch')
        self.assertEqual(p.select_entry('WAIT', 0.9, {**jev('INV', 0.9), 'chart_sha256':'bad'})[2], 'jev_invalid')
        for invalid in (jev('INV', True), jev('INV', 1.1), jev('005930', 0.9), jev([], 0.9)):
            self.assertEqual(p.select_entry('WAIT', 0.9, invalid)[2], 'jev_invalid')
    def test_holding_requires_confidence_or_price_threshold(self):
        with patch.object(p, 'holding_decision', return_value=('SELL', 0.79, {})) as judge, \
             patch.object(p, 'valid_bars', return_value=[]), patch.object(p, 'headlines', return_value=[]), \
             patch.object(p, 'jev', return_value={}), patch.object(p, 'journal_event'):
            class Reader:
                def call_tool(self, *args): return {'status':'ok','data':{}}
            self.assertFalse(p.holding_action(Reader(), Path('/tmp'), '114800', 100, 100))
            self.assertTrue(p.holding_action(Reader(), Path('/tmp'), '114800', 100, 98))
            self.assertEqual(judge.call_count, 1)

    def test_news_failure_does_not_block_protective_exit(self):
        class Reader:
            def call_tool(self, *args): return {'status': 'ok', 'data': {}}
        with patch.object(p, 'valid_bars', return_value=[]), \
             patch.object(p, 'headlines', side_effect=TimeoutError('private')) as news, \
             patch.object(p, 'journal_event'):
            self.assertFalse(p.holding_action(Reader(), Path('/tmp'), '114800', 100, 100))
            self.assertTrue(p.holding_action(Reader(), Path('/tmp'), '114800', 100, 98))
            self.assertEqual(news.call_count, 1)

    def test_submit_blocks_symbols_outside_user_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            db=p.connect_journal(Path(temp)/'journal.sqlite3',p.account_hash(ALT))
            with self.assertRaises(ValueError):
                p.submit(db, object(), ALT, '20261106', '005930', 'BUY', 1, 100)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM intents').fetchone()[0], 0)
            db.close()

    def test_cycle_link_is_durable_before_submit_and_uncertainty_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'journal.sqlite3'
            db = p.connect_journal(path, p.account_hash(ALT))
            cycle_id = 'b' * 32
            class Broker:
                def call_tool(self, *args, **kwargs):
                    with p.sqlite3.connect(path) as reader:
                        row = reader.execute('SELECT i.status, l.cycle_id FROM intents i JOIN decision_order_links l ON l.order_id=i.id').fetchone()
                    self_row.append(row)
                    return {'status': 'error'}
            self_row = []
            with self.assertRaisesRegex(ValueError, 'uncertain'):
                p.submit(db, Broker(), ALT, '20261106', '114800', 'BUY', 1, 1300, cycle_id=cycle_id)
            self.assertEqual(self_row, [('uncertain', cycle_id)])
            db.close()
            db = p.connect_journal(path, p.account_hash(ALT))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM decision_order_links').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM fills').fetchone()[0], 0)
            db.close()

    def test_invalid_cycle_id_cannot_create_or_submit_order(self):
        with tempfile.TemporaryDirectory() as temp:
            db = p.connect_journal(Path(temp) / 'journal.sqlite3', p.account_hash(ALT))
            with self.assertRaisesRegex(ValueError, 'cycle identity'):
                p.submit(db, object(), ALT, '20261106', '114800', 'BUY', 1, 1300, cycle_id='bad')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM intents').fetchone()[0], 0)
            db.close()

    def test_existing_journal_is_migrated_without_linking_old_intents(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'journal.sqlite3'
            db = p.connect_journal(path, p.account_hash(ALT))
            row = ('a'*32,'20261106','114800','BUY',1,1300,'acknowledged',42,0)
            db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?,?,?,?)', row)
            db.execute('DROP TABLE decision_order_links')
            db.commit(); db.close()
            db = p.connect_journal(path, p.account_hash(ALT))
            self.assertEqual(db.execute('SELECT * FROM intents').fetchone(), row)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM decision_order_links').fetchone()[0], 0)
            db.close()

    def test_stop_marker_blocks_submission_after_analysis(self):
        class Clock:
            @staticmethod
            def now(_): return NOW
        with tempfile.TemporaryDirectory() as temp, patch.object(p, 'datetime', Clock):
            root=Path(temp)
            p.ensure_trading_open(root, '20261106')
            (root/'STOP').write_text('stop')
            with self.assertRaisesRegex(ValueError, 'session stopped'):
                p.ensure_trading_open(root, '20261106')

    def test_stop_reconciles_pending_and_records_unresolved(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);db=p.connect_journal(root/'journal.sqlite3',p.account_hash(ALT))
            row=('a'*32,'20261106','114800','BUY',1,1300,'acknowledged',42,0)
            db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?,?,?,?)',row);db.commit()
            with patch.object(p,'reconcile',side_effect=[False,True]) as check, patch.object(p.time,'sleep'), patch.object(p,'journal_event') as event:
                p.settle_before_stop(db,object(),ALT,root,True)
                self.assertEqual(check.call_count,2)
                self.assertTrue(check.call_args.kwargs['stopping'])
                self.assertEqual(event.call_args.kwargs['reason'],'session or STOP')
            with patch.object(p,'reconcile',side_effect=ValueError('uncertain')), patch.object(p,'journal_event') as event:
                p.settle_before_stop(db,object(),ALT,root,True)
                self.assertTrue(event.call_args.kwargs['reconciliation_required'])
            with patch.object(p,'reconcile',return_value=False) as check, patch.object(p.time,'sleep'), patch.object(p,'journal_event') as event:
                p.settle_before_stop(db,object(),ALT,root,False)
                self.assertFalse(check.call_args.kwargs['allow_cancel'])
                self.assertTrue(event.call_args.kwargs['reconciliation_required'])
            db.close()
    def test_positive_number_without_business_success_is_uncertain(self):
        class Broker:
            def call_tool(self,*args,**kwargs):
                return {'status':'ok','data':{'rsp_cd':'99999','rsp_msg':'거부','Output_0':{'mkt_orr_no':42}}}
        with tempfile.TemporaryDirectory() as temp:
            db=p.connect_journal(Path(temp)/'journal.sqlite3',p.account_hash(ALT))
            with self.assertRaisesRegex(ValueError,'uncertain'):p.submit(db,Broker(),ALT,'20261106','114800','BUY',1,1300)
            self.assertEqual(db.execute('SELECT status FROM intents').fetchone()[0],'uncertain');db.close()

    def test_read_retry_helper_refuses_order_operations(self):
        class Never:
            def call_tool(self, *args, **kwargs): raise AssertionError('transport must not be called')
        with self.assertRaisesRegex(ValueError,'only paper'):
            p.paper_pages(Never(),'krstockOrderCashBuy',{},'Output_0')

    def test_candidate_excludes_default_and_preserves_paper_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            digest, config = p.select(configs(), Path(tmp), NOW, Fake)
            self.assertEqual(digest, p.account_hash(ALT))
            self.assertEqual(p.paper_account(config), ALT)
            self.assertEqual(p.paper_account(configs()['nhplug_paper']), DEFAULT)

    def test_derivatives_are_not_selected_even_when_reported_as_paper(self):
        class Mixed(Fake):
            def call_tool(self, tool, params):
                if tool == 'list_accounts':
                    return {'status': 'ok', 'data': {'rsp_cd': '00000', 'Output_0': [
                        {'acct_no': x, 'acct_type': '03'} for x in (DEFAULT, FUTURES, OVERSEAS)]}}
                raise AssertionError('derivative accounts must never be queried as stock candidates')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'no verified'):
                p.select(configs(), Path(tmp), NOW, Mixed)
            for account in (FUTURES, OVERSEAS):
                with self.assertRaisesRegex(ValueError, 'stock'):
                    p.selected_config(configs(), p.account_hash(account), Mixed)

    def test_selected_stock_account_remains_supported(self):
        self.assertEqual(p.paper_account(p.selected_config(configs(), p.account_hash(ALT), Fake)), ALT)

    def test_unverified_template_product_fails_closed(self):
        cfg = configs()
        cfg['nhplug_paper'] = MCPServerConfig(command='x', args=['--mode', 'paper', '--account', FUTURES])
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'stock'):
                p.select(cfg, Path(tmp), NOW, Fake)
            with self.assertRaisesRegex(ValueError, 'stock'):
                p.selected_config(cfg, p.account_hash(ALT), Fake)
    def test_incomplete_pages_never_select(self):
        class Bad(Fake):
            def call_tool(self, tool, params):
                result = super().call_tool(tool, params)
                if tool != 'list_accounts': result['data']['_nh_pagination']['complete'] = None
                return result
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'pagination'):
                p.select(configs(), Path(tmp), NOW, Bad)
    def test_reserved_strategy_account_is_never_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); d=root/'nhplug/strategies/swing';d.mkdir(parents=True)
            (d/'forward.json').write_text(json.dumps({'account_sha256':p.account_hash(ALT)}))
            with self.assertRaisesRegex(ValueError, 'no verified'):
                p.select(configs(), root, NOW, Fake)
    def test_nonempty_account_is_rejected(self):
        class Busy(Fake):
            def call_tool(self, tool, params):
                result=super().call_tool(tool, params)
                if tool!='list_accounts' and params['operationId']=='krstockInquiryBalance':
                    result['data']['Output_1']=[{'iem_cd':'114800','rsdl_qty':1}]
                return result
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'no verified'):
                p.select(configs(),Path(tmp),NOW,Busy)
    def test_paper_profile_rejects_live(self):
        cfg=MCPServerConfig(command='x',args=['--mode','readonly','--account',ALT])
        with self.assertRaises(ValueError):p.isolated_config(cfg,ALT)

    def test_submission_is_durable_and_reconciles_only_matching_fill(self):
        class Broker:
            def __init__(self): self.calls=[]; self.history=[]
            def call_tool(self, tool, args, **kwargs):
                self.calls.append((args,kwargs))
                if args['operationId']=='krstockInquiryDailyOrderExecution':
                    return {'status':'ok','data':{'rsp_cd':'XA102','rsp_msg':'모의투자 조회가 완료되었습니다',
                            'Output_0':self.history,'_nh_pagination':{'complete':True}}}
                return {'status':'ok','data':{'rsp_cd':'00048','rsp_msg':'매수주문이 완료되었습니다','Output_0':{'mkt_orr_no':42}}}
        with tempfile.TemporaryDirectory() as temp:
            db=p.connect_journal(Path(temp)/'journal.sqlite3',p.account_hash(ALT));b=Broker()
            oid,num=p.submit(db,b,ALT,'20261106','114800','BUY',1,1300, cycle_id='c'*32)
            self.assertEqual(num,42)
            self.assertEqual(len(b.calls[0][1]['order_intent_id']),32)
            row=db.execute('SELECT id,day,symbol,side,qty,price,status,order_number,created FROM intents').fetchone()
            with self.assertRaises(ValueError):p.reconcile(db,b,ALT,row)
            b.history=[{'itg_orr_no':42,'iem_cd':'114800','sby_dit_cd_nm':'현금매수',
                'orr_qty':1,'tot_cns_qty':1,'ny_cns_qty':0,'can_qty':0,'cns_avg_uit_pr':1299}]
            self.assertTrue(p.reconcile(db,b,ALT,row))
            self.assertEqual(db.execute('SELECT l.cycle_id, i.status, f.quantity FROM decision_order_links l JOIN intents i ON i.id=l.order_id JOIN fills f ON f.order_id=l.order_id').fetchone(), ('c'*32, 'terminal', 1))
            self.assertEqual(p.owned_positions(db),{'114800':1})
            p.verify_positions([{'iem_cd':'114800','rsdl_qty':1}],p.owned_positions(db))
            with self.assertRaises(ValueError):p.verify_positions([],p.owned_positions(db))
            db.close()

    def test_uncertain_submit_survives_restart_and_never_retries(self):
        class Broker:
            calls=0
            def call_tool(self,*args,**kwargs):
                self.calls+=1;return {'status':'error'}
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'journal.sqlite3';b=Broker();db=p.connect_journal(path,p.account_hash(ALT))
            with self.assertRaisesRegex(ValueError,'uncertain'):p.submit(db,b,ALT,'20261106','114800','BUY',1,1300)
            db.close();db=p.connect_journal(path,p.account_hash(ALT))
            with self.assertRaisesRegex(ValueError,'previous submission'):p.submit(db,b,ALT,'20261106','114800','BUY',1,1300)
            row=db.execute('SELECT id,day,symbol,side,qty,price,status,order_number,created FROM intents').fetchone()
            with self.assertRaisesRegex(ValueError,'identity unknown'):p.reconcile(db,b,ALT,row)
            self.assertEqual(b.calls,1);db.close()

    def test_partial_fill_is_not_finalized_until_reconciliation_completes(self):
        class Broker:
            remaining = 1
            def call_tool(self, tool, args, **kwargs):
                return {'status':'ok','data':{'rsp_cd':'XA102','rsp_msg':'모의투자 조회가 완료되었습니다',
                    'Output_0':[{'itg_orr_no':42,'iem_cd':'114800','sby_dit_cd_nm':'현금매수',
                        'orr_qty':2,'tot_cns_qty':1,'ny_cns_qty':self.remaining,
                        'can_qty':1-self.remaining,'cns_avg_uit_pr':1299}], '_nh_pagination':{'complete':True}}}
        with tempfile.TemporaryDirectory() as temp:
            db=p.connect_journal(Path(temp)/'journal.sqlite3',p.account_hash(ALT));b=Broker()
            row=('a'*32,'20261106','114800','BUY',2,1300,'acknowledged',42,0)
            db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?,?,?,?)',row);db.commit()
            self.assertFalse(p.reconcile(db,b,ALT,row,allow_cancel=False))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM fills').fetchone()[0],0)
            b.remaining=0
            self.assertTrue(p.reconcile(db,b,ALT,row))
            self.assertEqual(db.execute('SELECT quantity FROM fills').fetchone()[0],1)
            db.close()

    def test_unfilled_order_cancel_attempt_is_not_repeated(self):
        class Broker:
            cancels=0
            def call_tool(self,tool,args,**kw):
                if args['operationId']=='krstockInquiryDailyOrderExecution':
                    return {'status':'ok','data':{'rsp_cd':'XA102','rsp_msg':'모의투자 조회가 완료되었습니다',
                        'Output_0':[{'itg_orr_no':42,'iem_cd':'114800','sby_dit_cd_nm':'현금매수',
                            'orr_qty':1,'tot_cns_qty':0,'ny_cns_qty':1,'can_qty':0}], '_nh_pagination':{'complete':True}}}
                self.cancels+=1;return {'status':'error'}
        with tempfile.TemporaryDirectory() as temp:
            db=p.connect_journal(Path(temp)/'journal.sqlite3',p.account_hash(ALT));b=Broker()
            row=('a'*32,'20261106','114800','BUY',1,1300,'acknowledged',42,0)
            db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?,?,?,?)',row);db.commit()
            with self.assertRaisesRegex(ValueError,'cancel outcome uncertain'):p.reconcile(db,b,ALT,row)
            self.assertFalse(p.reconcile(db,b,ALT,row))
            self.assertEqual(b.cancels,1);db.close()

    def test_cycles_allow_repeated_entries_and_sell_accumulated_shares(self):
        class Reader:
            def call_tool(self, tool, params):
                return {'status':'ok','data':{'Output_0':{'askp':1300,'bidp':1299}}}
        class Broker:
            def __init__(self):self.holdings=0;self.rows=[];self.calls=[];self.cash=10000
            def call_tool(self,tool,params,**kwargs):
                op=params['operationId'];self.calls.append(op)
                if op=='krstockInquiryDailyOrderExecution':
                    return {'status':'ok','data':{'rsp_cd':'XA102','rsp_msg':'모의투자 조회가 완료되었습니다',
                        'Output_0':list(self.rows),'_nh_pagination':{'complete':True}}}
                if op=='krstockInquiryBalance':
                    return {'status':'ok','data':{'rsp_cd':'00000','rsp_msg':'모의투자 마지막 자료입니다',
                        'Output_0':{'orr_pbl_amt1':self.cash},'Output_1':([{'iem_cd':'114800','rsdl_qty':self.holdings,'phs_pr':1301}] if self.holdings else []),
                        '_nh_pagination':{'complete':True}}}
                if op=='krstockInquirySellableQuantity':
                    return {'status':'ok','data':{'rsp_cd':'XA102','Output_0':{'iem_cd':'114800','sll_pbl_qty':self.holdings}}}
                side='현금매수' if op=='krstockOrderCashBuy' else '현금매도'
                number=len(self.rows)+1
                self.holdings+=1 if side=='현금매수' else -1
                self.rows.insert(0,{'itg_orr_no':number,'iem_cd':'114800','sby_dit_cd_nm':side,
                    'orr_qty':1,'tot_cns_qty':1,'ny_cns_qty':0,'can_qty':0,'cns_avg_uit_pr':1300})
                return {'status':'ok','data':{'rsp_cd':'00048' if side=='현금매수' else '00047',
                    'rsp_msg':'매수주문이 완료되었습니다' if side=='현금매수' else '매도주문이 완료되었습니다',
                    'Output_0':{'mkt_orr_no':number}}}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);db=p.connect_journal(root/'orders.sqlite3',p.account_hash(ALT));broker=Broker();now=datetime.now(p.KST)
            values=[{'stck_prpr':str(1400-i)} for i in range(10)]
            with patch.object(p,'valid_bars',return_value=values),patch.object(p,'headlines',return_value=[]),\
                 patch.object(p,'decision',return_value=('WAIT',0.9,{})),patch.object(p,'volatility',return_value=1),\
                 patch.object(p,'jev',return_value={'model':'jev-1.14.0','chart_sha256':'a'*64,'candidate':{'type':'choice','choice':'INV','confidence':0.69}}) as jev,patch.object(p,'fresh_quote',return_value={'askp':1300,'bidp':1299}) as quote,\
                 patch.object(p,'holding_action',return_value=False) as holding,patch.object(p,'ensure_trading_open'),patch.object(p,'journal_event') as events:
                self.assertEqual(p.cycle(db,Reader(),broker,ALT,root,True,now),'wait')
                self.assertNotIn('krstockOrderCashBuy',broker.calls)
                jev.return_value['candidate']['confidence']=0.9
                events.reset_mock()
                with patch.object(p, 'learned_filter', return_value={'status':'ready','probability':0.4}):
                    self.assertEqual(p.cycle(db,Reader(),broker,ALT,root,True,now),'learned_filter_wait')
                blocked = events.call_args_list[-1]
                self.assertEqual((blocked.args[1], blocked.kwargs['reason'], blocked.kwargs['permitted']),
                                 ('v2_execution_gate', 'learning_filter', False))
                self.assertEqual(blocked.kwargs['cycle_id'], events.call_args_list[0].kwargs['cycle_id'])
                self.assertNotIn('krstockOrderCashBuy',broker.calls)
                day = now.strftime('%Y%m%d')
                db.executemany('INSERT INTO intents VALUES (?,?,?,?,?,?,?,?,?)',
                               [(f'{i:032x}',day,'114800','BUY',1,1300,'terminal',1,0) for i in range(998)])
                db.commit()
                events.reset_mock()
                self.assertEqual(p.cycle(db,Reader(),broker,ALT,root,True,now),'limit')
                self.assertEqual(events.call_args_list[-1].kwargs['reason'], 'order_limit')
                self.assertEqual(events.call_args_list[-1].kwargs['cycle_id'], events.call_args_list[0].kwargs['cycle_id'])
                db.execute('DELETE FROM intents'); db.commit()
                for cash in (0, 1299):
                    broker.cash = cash
                    events.reset_mock()
                    self.assertEqual(p.cycle(db,Reader(),broker,ALT,root,True,now), 'wait')
                    blocked = events.call_args_list[-1]
                    self.assertEqual((blocked.args[1], blocked.kwargs['reason'], blocked.kwargs['permitted']),
                                     ('v2_execution_gate', 'insufficient_buying_power', False))
                    self.assertEqual(blocked.kwargs['cycle_id'], events.call_args_list[0].kwargs['cycle_id'])
                    self.assertNotIn('krstockOrderCashBuy', broker.calls)
                for cash in (None, '0', True, float('nan'), -1):
                    broker.cash = cash
                    with self.assertRaises(ValueError):
                        p.cycle(db,Reader(),broker,ALT,root,True,now)
                broker.cash = 10000
                quote.return_value = {'askp': 0, 'bidp': 1299}
                with self.assertRaises(ValueError):
                    p.cycle(db,Reader(),broker,ALT,root,True,now)
                quote.return_value = {'askp': 1300, 'bidp': 1299}
                events.reset_mock()
                self.assertEqual(p.cycle(db,Reader(),broker,ALT,root,True,now),'buy')
                buy_cycle = db.execute('SELECT cycle_id FROM decision_order_links').fetchone()[0]
                self.assertEqual([call.kwargs['cycle_id'] for call in events.call_args_list], [buy_cycle]*4)
                self.assertEqual([call.args[1] for call in events.call_args_list], ['decision', 'jev_shadow', 'v2_entry_gate', 'submitted'])
                self.assertEqual(p.cycle(db,Reader(),broker,ALT,root,True,now),'reconciled')
                self.assertEqual(p.cycle(db,Reader(),broker,ALT,root,True,now),'hold')
                self.assertEqual(holding.call_args.args[3],1301)
                self.assertEqual(p.owned_positions(db),{'114800':1})
                later=datetime.now(p.KST)+timedelta(minutes=6)
                self.assertEqual(p.cycle(db,Reader(),broker,ALT,root,True,later),'hold')
                holding.return_value=True
                quote.return_value={'askp':1300,'bidp':1250}
                events.side_effect=lambda root, kind, **fields: (_ for _ in ()).throw(OSError('log unavailable')) if kind == 'v2_risk_exit' else None
                self.assertEqual(p.cycle(db,Reader(),broker,ALT,root,True,later),'sell')
                self.assertEqual(p.cycle(db,Reader(),broker,ALT,root,True,later),'reconciled')
                self.assertEqual(p.owned_positions(db),{})
            db.close()


class MultiSymbolTests(unittest.TestCase):
    """Shared ETF runner: protective exits first, entries while holding, up to 100 shares per order."""

    def run_cycle(self, root, db, holdings, *, cash=1_000_000, ask=1300, bids=None, holding_sell=False,
                  jev_choice='KQ150', sellable=None, broker=None, submit_effect=None, at=NOW, deadline=2**52, execute=True, gate=None):
        from unittest.mock import Mock
        # a market that confirms longs and inverses alike, unless the test patched its own view around this call
        view_patch = (patch.object(p, 'market_view', return_value={'코스피': {'regime': '횡보장', 'today_tone': '혼조', 'today_change_pct': 0.0},
                                                                    '코스닥': {'regime': '횡보장', 'today_tone': '혼조', 'today_change_pct': 0.0}})
                      if not isinstance(p.market_view, Mock) else patch.object(p, 'ENTRY_VOLUME_RATIO', p.ENTRY_VOLUME_RATIO))
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return at
        bids = bids or {}
        class Reader:
            def call_tool(self, tool, params):
                return {'status':'ok','data':{'code':params.get('stock_code')}}
        def quote(data, symbol, now):
            # an entry's bid sits one tick under its ask; held symbols keep the 1300 bid unless a test sets one
            return {'askp': ask, 'bidp': bids.get(symbol, 1300 if symbol in holdings else ask - 1), 'iem_cd': symbol}
        def pages(b, operation, fields, block):
            if operation == 'krstockInquiryDailyOrderExecution':
                return {'Output_0': []}
            return {'Output_0': {'orr_pbl_amt1': cash},
                    'Output_1': [{'iem_cd': c, 'rsdl_qty': q, 'phs_pr': 1300} for c, q in holdings.items()]}
        class Broker:
            def call_tool(self, tool, params, **kwargs):
                code = params['input']['iem_cd']
                return {'status':'ok','data':{'rsp_cd':'XA102','Output_0':{'iem_cd':code,
                        'sll_pbl_qty': holdings[code] if sellable is None else sellable}}}
        bars = [{'bsop_time': f'11{58-i:02d}00', 'stck_prpr': str(1400-i), 'vol': str(200 if i < 5 else 100)} for i in range(10)]
        jev = {'model':'jev-1.14.0','chart_sha256':'a'*64,'response_time':NOW.isoformat(),
               'candidate':{'type':'choice','choice':jev_choice,'confidence':0.9}}
        with view_patch, patch.object(p, 'inverse_gate', return_value=gate), patch.object(p, 'inverse_tape', return_value=gate), patch.object(p,'datetime',Clock), patch.object(p,'paper_pages',side_effect=pages), \
             patch.object(p,'valid_bars',return_value=bars), patch('etf_news.headlines7',return_value=[{'title':'m','published':NOW.isoformat()}]), \
             patch.object(p,'jev7',return_value=jev), patch.object(p,'fresh_quote',side_effect=quote), \
             patch.object(p,'etf7_protective_deadline',return_value=deadline), \
             patch.object(p,'etf7_signal_deadline',return_value=2**52), \
             patch.object(p,'holding_action',side_effect=lambda r, rt, sym, avg, bid, **k: holding_sell or (
                 k['stop_hit'] if k.get('stop_hit') is not None else p.exit_signal(avg, bid))) as holding, \
             patch.object(p,'ensure_trading_open'), patch.object(p,'journal_event') as events, \
             patch.object(p,'submit',return_value=('a'*32, 1), side_effect=submit_effect) as submit:
            status = p.cycle(db, Reader(), broker or Broker(), ALT, root, execute, at, etf7=True)
        return status, submit, holding, events

    def setUp(self):
        p._OVERNIGHT_LOGGED.clear()  # the once-per-day overnight log is process state
        p._SWITCH_EXITS.clear()  # so is the direction switch's mark on its exit requests
        # engine-sized adds are tested in test_v2_reentry; stopped in tearDown (other tests call setUp/tearDown directly)
        self._add_patches = [patch.object(p, name, return_value=value) for name, value in (('add_reason', 'confidence_up'), ('add_size_factor', 1.0))]
        for patcher in self._add_patches:
            patcher.start()
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        (self.root/'ai-modes.json').write_text(json.dumps({'version':1,'jev':True,'decision':False}))
        self.db = p.connect_journal(self.root/'orders.sqlite3', p.account_hash(ALT))

    def tearDown(self):
        for patcher in self._add_patches:
            patcher.stop()
        self.db.close(); self.temp.cleanup()

    def own(self, **qty):
        from etf_universe import SYMBOLS as ETF7
        for name, q in qty.items():
            self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', (name, ETF7[name], 'BUY', q, 1300, NOW.isoformat()))
        self.db.commit()
        return {ETF7[n]: q for n, q in qty.items()}

    def test_streak_history_unavailable_is_recorded_as_its_actual_reason(self):
        with patch.object(p, 'losing_streak', return_value='losing_streak_history_unavailable'):
            status, submit, _, events = self.run_cycle(self.root, self.db, {})
        self.assertEqual(status, 'wait')
        submit.assert_not_called()
        gates = [c.kwargs for c in events.call_args_list if c.args[1] == 'v2_entry_gate']
        self.assertEqual(gates[-1]['reason'], 'losing_streak_history_unavailable')

    def test_pre_close_exit_sells_holdings_through_the_protective_path(self):
        holdings = self.own(K200=3)
        status, submit, _, events = self.run_cycle(self.root, self.db, holdings, at=NOW.replace(hour=14, minute=55))
        self.assertEqual(status, 'sell')
        self.assertEqual(submit.call_args.args[4:7], ('069500', 'SELL', 3))
        exits = [c.kwargs for c in events.call_args_list if c.args[1] == 'v2_risk_exit']
        self.assertEqual(exits[0]['reason'], 'session_flatten')
        sent = [c.kwargs for c in events.call_args_list if c.args[1] == 'sell_submitted']
        self.assertEqual(sent[0]['trigger'], 'risk')  # the sell record says why it left

    def test_flatten_sells_all_verified_holdings_even_above_100_shares(self):
        holdings = self.own(K200=250)
        actual_setting = p.setting
        with patch.object(p, 'setting', side_effect=lambda key: 0 if key == 'paper_max_order_qty' else actual_setting(key)):
            status, submit, _, _ = self.run_cycle(self.root, self.db, holdings, at=NOW.replace(hour=14, minute=55))
        self.assertEqual((status, submit.call_args.args[6]), ('sell', 250))
        p.apply_settings(self.root)

    def test_protective_stop_sells_available_holding_without_100_share_cap(self):
        holdings = self.own(INV=767)
        status, submit, _, _ = self.run_cycle(self.root, self.db, holdings, bids={'114800':1280}, sellable=300)
        self.assertEqual((status, submit.call_args.args[4:7]), ('sell', ('114800', 'SELL', 300)))
        status, submit, _, _ = self.run_cycle(self.root, self.db, holdings, bids={'114800':1280}, sellable=767)
        self.assertEqual((status, submit.call_args.args[4:7]), ('sell', ('114800', 'SELL', 767)))

    def test_signal_and_partial_profit_sell_follow_configured_order_quantity(self):
        holdings = self.own(INV=767)
        actual_setting = p.setting
        for cap, expected_model, expected_half in ((100, 100, 100), (0, 767, 384)):
            with self.subTest(cap=cap), patch.object(p, 'setting', side_effect=lambda key: cap if key == 'paper_max_order_qty' else actual_setting(key)):
                status, submit, _, _ = self.run_cycle(self.root, self.db, holdings, holding_sell=True)
                self.assertEqual((status, submit.call_args.args[4:7]), ('sell', ('114800', 'SELL', expected_model)))
                status, submit, _, _ = self.run_cycle(self.root, self.db, holdings, bids={'114800':1313})
                self.assertEqual((status, submit.call_args.args[4:7]), ('sell', ('114800', 'SELL', expected_half)))
        p.apply_settings(self.root)

    def test_manual_exit_sells_full_available_holding_and_pending_still_blocks(self):
        holdings = self.own(INV=767)
        with patch.object(p, 'exit_requests', return_value={'114800': None}):
            status, submit, _, _ = self.run_cycle(self.root, self.db, holdings)
            self.assertEqual((status, submit.call_args.args[4:7]), ('sell', ('114800', 'SELL', 767)))
            self.db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?,?,?,?)', ('ambiguous', '20261116', '114800', 'SELL', 767, 1300, 'uncertain', None, 0))
            self.db.commit()
            with patch.object(p, 'reconcile', return_value=False):
                status, submit, _, _ = self.run_cycle(self.root, self.db, holdings)
            self.assertEqual(status, 'pending')
            submit.assert_not_called()

    def test_entry_window_closes_before_the_pre_close_exit(self):
        status, submit, _, events = self.run_cycle(self.root, self.db, {}, at=NOW.replace(hour=14, minute=45))
        self.assertEqual(status, 'wait')
        self.assertEqual(events.call_args.kwargs['reason'], 'entry_window_closed')
        submit.assert_not_called()

    def test_rest_of_a_partly_sold_position_exits_at_breakeven(self):
        from etf_universe import SYMBOLS as ETF7
        self.own(K200=4)
        self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('s', ETF7['K200'], 'SELL', 2, 1313, (NOW + timedelta(minutes=5)).isoformat()))
        self.db.execute('INSERT INTO sell_order_triggers VALUES (?,?)', ('s', 'take_profit'))  # the half take-profit arms it
        self.db.commit()
        status, submit, _, events = self.run_cycle(self.root, self.db, {'069500': 2}, bids={'069500': 1300})
        self.assertEqual((status, submit.call_args.args[4:7]), ('sell', ('069500', 'SELL', 2)))
        self.assertEqual([c.kwargs.get('reason') for c in events.call_args_list if c.args[1] == 'v2_risk_exit'], ['breakeven_stop'])
        self.assertEqual([c.kwargs.get('trigger') for c in events.call_args_list if c.args[1] == 'sell_submitted'], ['risk'])

    def test_unsold_position_at_its_average_is_kept(self):
        holdings = self.own(K200=2)
        _, submit, _, _ = self.run_cycle(self.root, self.db, holdings, bids={'069500': 1300})
        self.assertNotIn(('069500', 'SELL'), [c.args[4:6] for c in submit.call_args_list])

    def test_levels_fixed_at_entry_override_later_settings(self):
        holdings = self.own(K200=2)
        (self.root/'exit-plans.json').write_text(json.dumps({'069500': {'stop_pct': 5.0, 'half_pct': 1.0, 'all_pct': 2.0}}))
        with patch.object(p, 'market_view', return_value={'코스피': {'regime': '상승장', 'today_tone': '강세'}}):
            _, submit, _, _ = self.run_cycle(self.root, self.db, holdings, bids={'069500': 1270})
        self.assertNotIn(('069500', 'SELL'), [c.args[4:6] for c in submit.call_args_list])
        (self.root/'exit-plans.json').unlink()
        _, submit, _, _ = self.run_cycle(self.root, self.db, holdings, bids={'069500': 1270})  # the -2% default applies
        self.assertEqual(submit.call_args.args[4:6], ('069500', 'SELL'))

    def test_a_price_exit_keeps_its_own_reason_in_the_pre_close_window(self):
        holdings = self.own(K200=2)
        _, _, _, events = self.run_cycle(self.root, self.db, holdings, bids={'069500': 1270}, at=NOW.replace(hour=14, minute=45))
        self.assertEqual([c.kwargs.get('reason') for c in events.call_args_list if c.args[1] == 'v2_risk_exit'], ['stop_loss'])

    def test_stale_quote_in_the_pre_close_window_skips_that_holding_without_stopping(self):
        holdings = self.own(K200=2, INV=3)
        status, submit, _, events = self.run_cycle(self.root, self.db, holdings, at=NOW.replace(hour=14, minute=45), deadline=None)
        self.assertEqual(status, 'protective_wait')  # both holdings tried, neither stopped the runner, no new entry
        submit.assert_not_called()
        errors = [c.kwargs for c in events.call_args_list if c.args[1] == 'v2_risk_exit_error']
        self.assertEqual([(e['symbol'], e['reason']) for e in errors], [('069500', 'quote_not_fresh'), ('114800', 'quote_not_fresh')])

    def test_cutoff_still_records_prices_for_settlements(self):
        _, _, _, events = self.run_cycle(self.root, self.db, {}, at=NOW.replace(hour=14, minute=35))
        late = [c.kwargs for c in events.call_args_list if c.args[1] == 'market_context']
        self.assertEqual(late[0]['context'], 'late_session')
        self.assertIn('K200', late[0]['market_bars'])
        self.assertEqual(events.call_args.kwargs['reason'], 'entry_window_closed')

    def test_flat_symbols_lose_their_exit_plan(self):
        (self.root/'exit-plans.json').write_text(json.dumps({'069500': {'stop_pct': 5.0, 'half_pct': 1.0, 'all_pct': 2.0},
                                                             '114800': {'stop_pct': 5.0, 'half_pct': 1.0, 'all_pct': 2.0}}))
        holdings = self.own(INV=2)
        self.run_cycle(self.root, self.db, holdings)
        self.assertEqual(set(p.exit_plans(self.root)), {'114800'})

    def test_rising_market_switches_to_trend_and_skips_the_fixed_final_take_profit(self):
        holdings = self.own(K200=1)
        rising = {'코스피': {'regime': '상승장', 'today_tone': '강세'}}
        with patch.object(p, 'market_view', return_value=rising):
            _, submit, _, events = self.run_cycle(self.root, self.db, holdings, bids={'069500': 1330})  # +2.3%
        self.assertNotIn(('069500', 'SELL'), [c.args[4:6] for c in submit.call_args_list])  # defensive would sell at +2%
        plan = p.exit_plans(self.root)['069500']
        self.assertEqual((plan['profile'], plan['peak']), ('trend', 1330))
        self.assertIn('trend_upgrade', [c.kwargs.get('reason') for c in events.call_args_list if c.args[1] == 'exit_plan'])

    def test_falling_market_keeps_the_defensive_take_profit(self):
        holdings = self.own(K200=1)
        falling = {'코스피': {'regime': '하락장', 'today_tone': '혼조'}}
        with patch.object(p, 'market_view', return_value=falling):
            _, submit, _, _ = self.run_cycle(self.root, self.db, holdings, bids={'069500': 1330})
        self.assertEqual(submit.call_args.args[4:6], ('069500', 'SELL'))

    def test_trend_position_in_profit_is_carried_past_the_close_in_a_rising_market(self):
        from etf_universe import SYMBOLS as ETF7
        self.own(K200=4)  # half already sold at the first target: the rest is a swing candidate
        self.db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?,?,?,?)', ('s', '20261106', ETF7['K200'], 'SELL', 2, 1326, 'terminal', 1, 0.0))
        self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('s', ETF7['K200'], 'SELL', 2, 1326, (NOW + timedelta(minutes=5)).isoformat()))
        self.db.execute('INSERT INTO sell_order_triggers VALUES (?,?)', ('s', 'take_profit'))
        self.db.commit()
        (self.root/'exit-plans.json').write_text(json.dumps({'069500': {**p.new_plan('069500', 'trend'), 'peak': 1330}}))
        strong = {'코스피': {'regime': '상승장', 'today_tone': '강세', 'advancers_decliners': [800, 300],
                   'investor_net_100m_krw': {'외국인': 900.0, '기관': 400.0, '개인': -1300.0}, 'program_net_100m_krw': {'전체': 1200.0}}}
        with patch.object(p, 'market_view', return_value=strong), patch.object(p, 'next_session_open', return_value=True), \
             patch.object(p, 'daily_stats', return_value={'median_up_pct': 1.5, 'volume_ratio': 2.1, 'sessions': 20, 'ma5': 1300.0, 'ma20': 1280.0,
                 'today': {'open': 1290.0, 'high': 1320.0, 'low': 1285.0, 'price': 1316.0, 'volume': 1.0}}), \
             patch.object(p, 'latest_holding_view', return_value=('HOLD', 0.72)):
            _, submit, _, events = self.run_cycle(self.root, self.db, {'069500': 2}, bids={'069500': 1316}, at=NOW.replace(hour=14, minute=45))
        self.assertNotIn(('069500', 'SELL'), [c.args[4:6] for c in submit.call_args_list])
        decision = [c.kwargs for c in events.call_args_list if c.args[1] == 'overnight_hold'][0]
        self.assertEqual((decision['permitted'], decision['reason']), (True, 'closing_trade_supported'))

    def test_defensive_position_with_a_data_target_sells_everything_there(self):
        holdings = self.own(K200=2)
        (self.root/'exit-plans.json').write_text(json.dumps({'069500': p.new_plan('069500', target=0.8)}))
        with patch.object(p, 'market_view', return_value={'코스피': {'regime': '하락장', 'today_tone': '혼조'}}):
            _, submit, _, events = self.run_cycle(self.root, self.db, holdings, bids={'069500': 1311})  # +0.85%
        self.assertEqual(submit.call_args.args[4:7], ('069500', 'SELL', 2))  # the whole position, not half
        self.assertEqual([c.kwargs.get('reason') for c in events.call_args_list if c.args[1] == 'v2_risk_exit'], ['take_profit_all'])

    def test_strong_day_in_a_falling_market_is_still_sold_before_the_close(self):
        p._OVERNIGHT_LOGGED.clear()
        holdings = self.own(K200=2)
        (self.root/'exit-plans.json').write_text(json.dumps({'069500': {**p.new_plan('069500', 'trend'), 'peak': 1320}}))
        rally = {'코스피': {'regime': '하락장', 'today_tone': '강세'}}
        with patch.object(p, 'market_view', return_value=rally), patch.object(p, 'next_session_open', return_value=True):
            _, submit, _, events = self.run_cycle(self.root, self.db, holdings, bids={'069500': 1316}, at=NOW.replace(hour=14, minute=45))
        self.assertEqual(submit.call_args.args[4:6], ('069500', 'SELL'))
        decision = [c.kwargs for c in events.call_args_list if c.args[1] == 'overnight_hold'][0]
        self.assertEqual((decision['permitted'], decision['reason']), (False, 'regime_not_rising'))

    def test_holding_review_is_told_the_positions_exit_plan(self):
        holdings = self.own(K200=2)
        (self.root/'exit-plans.json').write_text(json.dumps({'069500': {**p.new_plan('069500', 'trend'), 'peak': 1320}}))
        _, _, holding, _ = self.run_cycle(self.root, self.db, holdings, bids={'069500': 1313})
        context = holding.call_args.kwargs['position_extra']
        self.assertEqual((context['chart_title'], context['exit_profile'], context['return_pct'], context['peak_bid']), ('K200', 'trend', 1.0, 1320))
        self.assertEqual((context['final_take_profit_pct'], context['trailing_pct'], context['pre_close_review']), (None, 1.5, False))

    def test_a_started_full_exit_keeps_selling_after_the_price_slips_back(self):
        holdings = self.own(K200=150)
        (self.root/'exit-plans.json').write_text(json.dumps({'069500': {**p.new_plan('069500', target=0.8), 'exiting': 'take_profit_all'}}))
        _, submit, _, _ = self.run_cycle(self.root, self.db, holdings, bids={'069500': 1302})  # back under the +0.8% target
        self.assertEqual(submit.call_args.args[4:7], ('069500', 'SELL', 150))

    def test_upgrade_keeps_the_highest_bid_already_seen(self):
        holdings = self.own(K200=1)
        (self.root/'exit-plans.json').write_text(json.dumps({'069500': {**p.new_plan('069500'), 'peak': 1340}}))
        with patch.object(p, 'market_view', return_value={'코스피': {'regime': '상승장', 'today_tone': '강세'}}):
            self.run_cycle(self.root, self.db, holdings, bids={'069500': 1310})
        self.assertEqual(p.exit_plans(self.root)['069500']['peak'], 1340)

    def test_entry_records_its_exit_plan(self):
        status, _, _, events = self.run_cycle(self.root, self.db, {})
        self.assertEqual(status, 'buy')
        plan = p.exit_plans(self.root)['229200']
        self.assertEqual((plan['stop_pct'], plan['half_pct'], plan['all_pct']), (2.0, 1.0, 2.0))
        self.assertIn('exit_plan', [c.args[1] for c in events.call_args_list])

    def test_protective_exit_of_any_holding_runs_before_other_reviews(self):
        holdings = self.own(K200=3, INV=150)
        status, submit, holding, _ = self.run_cycle(self.root, self.db, holdings, bids={'114800': 1270})
        self.assertEqual(status, 'sell')
        self.assertEqual(submit.call_args.args[4:7], ('114800', 'SELL', 150))
        self.assertEqual([c.args[2] for c in holding.call_args_list], ['114800'])

    def test_kept_holdings_still_allow_a_new_entry_of_up_to_the_order_cap(self):
        holdings = self.own(KQ150LEV=1)
        status, submit, holding, events = self.run_cycle(self.root, self.db, holdings)
        self.assertEqual(status, 'buy')
        self.assertEqual(submit.call_args.args[4:7], ('229200', 'BUY', 100))
        self.assertEqual(events.call_args.kwargs['qty'], 100)

    def test_entry_quantity_follows_the_position_sizing_factor(self):
        # user 2026-11-17: holdings and market direction size the buy; the factor and its parts go into the order record
        import position_sizing
        with patch.object(position_sizing, 'buy_factor', return_value=(0.5, {'holding': 1.0, 'market': 0.5})):
            status, submit, _, events = self.run_cycle(self.root, self.db, self.own(KQ150LEV=1))
        self.assertEqual((status, submit.call_args.args[4:7]), ('buy', ('229200', 'BUY', 50)))
        self.assertEqual(events.call_args.kwargs['sizing'], {'holding': 1.0, 'market': 0.5})

    def test_entry_quantity_respects_cash_single_order_and_total_position_limits(self):
        for cash, ask, held, expected in ((3 * 1300 + 5, 1300, 0, 3), (10_000_000, 400_000, 0, 2), (1_000_000, 1300, 995, 5)):
            with self.subTest(cash=cash, ask=ask, held=held):
                self.db.execute('DELETE FROM fills'); self.db.commit()
                holdings = self.own(KQ150LEV=held) if held else {}
                status, submit, _, _ = self.run_cycle(self.root, self.db, holdings, cash=cash, ask=ask)
                self.assertEqual(status, 'buy')
                self.assertEqual(submit.call_args.args[6], expected)

    def test_sell_quantity_is_capped_by_broker_sellable_quantity(self):
        holdings = self.own(KQ150=8)
        status, submit, _, events = self.run_cycle(self.root, self.db, holdings, holding_sell=True, sellable=5)
        self.assertEqual(status, 'sell')
        self.assertEqual(submit.call_args.args[4:7], ('229200', 'SELL', 5))
        self.assertEqual([c.kwargs.get('trigger') for c in events.call_args_list if c.args[1] == 'sell_submitted'], ['model'])

    def test_unsent_protective_exit_still_tries_other_holdings_and_blocks_entry(self):
        holdings = self.own(K200=3, INV=4)
        calls = []
        def effect(*args, **kwargs):
            calls.append(args[4])
            if len(calls) == 1:
                raise p.PaperOrderNotSent('expired')
            return ('a'*32, 1)
        status, submit, _, _ = self.run_cycle(self.root, self.db, holdings, bids={'069500': 1270, '114800': 1270},
                                             submit_effect=effect)
        self.assertEqual((status, calls), ('sell', ['069500', '114800']))
        calls.clear()
        status, submit, _, _ = self.run_cycle(self.root, self.db, holdings, bids={'069500': 1270},
                                             submit_effect=lambda *a, **k: calls.append(a[4]) or (_ for _ in ()).throw(p.PaperOrderNotSent('x')))
        self.assertEqual((status, calls), ('protective_wait', ['069500']))

    def test_guard_reconciliation_refusal_stops_instead_of_retrying_quietly(self):
        status, *_ = self.run_cycle(self.root, self.db, {}, submit_effect=p.PaperOrderGuardRefused('Swing cumulative buy limit exceeded'))
        self.assertEqual(status, 'wait')
        with self.assertRaisesRegex(ValueError, 'reconciliation'):
            self.run_cycle(self.root, self.db, {}, submit_effect=p.PaperOrderGuardRefused(
                'Previous NH order needs reconciliation; query daily executions before any new order'))

    def test_a_held_jev_pick_that_cannot_add_records_the_strength_shadow(self):
        # user 2026-11-17: when Jev keeps picking a held stock it cannot add to, note what the strength path would buy
        holdings = self.own(INV=1)
        with patch.object(p, 'scale_in_allowed', return_value=False):
            status, submit, _, events = self.run_cycle(self.root, self.db, holdings, jev_choice='INV')
        self.assertEqual(status, 'wait')
        submit.assert_not_called()
        gate = [c for c in events.call_args_list if c.args[1] == 'v2_entry_gate'][-1].kwargs
        self.assertIn(gate['reason'], ('split_plan_holding', 'add_cutoff'))
        shadow = next(c for c in events.call_args_list if c.args[1] == 'strength_shadow').kwargs
        self.assertEqual((shadow['blocked'], shadow['opposite_held']), (gate['reason'], True))

    def test_state_of_closed_positions_and_spent_v2_watches_is_dropped(self):
        # r3 X10: regime-trims.json kept every symbol ever trimmed, and a v2 re-entry watch whose sessions ended stayed
        # unless a watch was still open or it was a v2 gate day
        import v2_reentry
        (self.root/'regime-trims.json').write_text(json.dumps({'114800': {'position': 1, 'entry_regime': '하락장', 'trimmed': True},
                                                               '005930': {'position': 9, 'entry_regime': '상승장', 'trimmed': False}}))
        v2_reentry.stopped(self.root, '000660', opened='2026-10-01T14:40:00+09:00', hold_days=3, qty=7, reentries=0, limit=2)
        with patch.object(p, 'sessions_held', lambda opened, today: (today - opened).days):  # no holiday table read
            self.run_cycle(self.root, self.db, self.own(INV=1))
        self.assertEqual(set(json.loads((self.root/'regime-trims.json').read_text())), {'114800'})  # 005930 is not held
        self.assertEqual(v2_reentry.watch(self.root), {})

    def test_holding_reviews_and_the_entry_share_the_cycles_minute_charts(self):
        from etf_universe import SYMBOLS as ETF7
        status, submit, holding, _ = self.run_cycle(self.root, self.db, self.own(INV=1))
        cache = holding.call_args.kwargs['bar_cache']
        self.assertTrue(set(ETF7.values()) <= set(cache))  # the entry read went through the reviews' cache

    def test_entry_opposite_to_a_holding_is_blocked_but_same_direction_is_allowed(self):
        holdings = self.own(INV=1)
        for choice, expected, reason in (('KQ150LEV', 'wait', 'opposite_direction_holding'),
                                         ('SEMILEV', 'wait', 'opposite_direction_holding'),
                                         ('KQ150INV', 'buy', None), ('INV', 'buy', None)):
            with self.subTest(choice=choice):
                status, submit, _, events = self.run_cycle(self.root, self.db, holdings, jev_choice=choice)
                self.assertEqual(status, expected)
                if expected == 'wait':
                    submit.assert_not_called()
                    gate = [c for c in events.call_args_list if c.args[1] == 'v2_entry_gate'][-1]  # strength_shadow follows it
                    self.assertEqual(gate.kwargs['reason'], reason)

    def test_opposite_direction_switch_off_allows_the_entry(self):
        from strategy_settings import DEFAULTS, write_settings
        write_settings(self.root, {**DEFAULTS, 'opposite_guard': 0})  # 제한 규칙: 반대 방향 동시 보유 차단 끔 (runner re-reads it)
        status, submit, _, _ = self.run_cycle(self.root, self.db, self.own(INV=1), jev_choice='KQ150LEV')
        self.assertEqual(status, 'buy')
        submit.assert_called()

    def test_direction_switch_sells_a_losing_long_only_for_an_inverse_the_gate_would_buy(self):
        # r3 E4: the switch used to request the long's sale before the inverse gate refused the inverse it was for
        holdings = self.own(K200=3)
        with patch.object(p, 'direction_switch_targets', return_value=['069500']):
            status, submit, _, events = self.run_cycle(self.root, self.db, holdings, jev_choice='INV', gate='inverse_index_not_falling')
            self.assertEqual(status, 'wait')
            self.assertFalse((self.root/'exit-requests.json').exists())
            self.assertNotIn('direction_switch', [c.args[1] for c in events.call_args_list])
            status, submit, _, events = self.run_cycle(self.root, self.db, holdings, jev_choice='INV')
        self.assertEqual(json.loads((self.root/'exit-requests.json').read_text()), ['069500'])
        self.assertIn('direction_switch', [c.args[1] for c in events.call_args_list])

    def test_a_direction_switch_sale_is_recorded_as_the_engines_not_the_users(self):
        # r3 X8: queued through exit-requests.json, it was sold and shown as 수동 (trigger 'manual', event manual_exit)
        holdings = self.own(K200=3)
        with patch.object(p, 'direction_switch_targets', return_value=['069500']):
            self.run_cycle(self.root, self.db, holdings, jev_choice='INV')
            status, submit, _, events = self.run_cycle(self.root, self.db, holdings, jev_choice='INV')
        self.assertEqual((status, submit.call_args.args[4:7]), ('sell', ('069500', 'SELL', 3)))
        self.assertEqual(submit.call_args.kwargs['sell_trigger'], 'risk')
        kinds = {c.args[1]: c.kwargs for c in events.call_args_list}
        self.assertNotIn('manual_exit', kinds)
        self.assertEqual(kinds['v2_risk_exit']['reason'], 'direction_switch')
        p._SWITCH_EXITS.clear()
        (self.root/'exit-requests.json').write_text(json.dumps(['069500']))  # the user's own request still reads 수동
        status, submit, _, events = self.run_cycle(self.root, self.db, holdings)
        self.assertEqual(submit.call_args.kwargs['sell_trigger'], 'manual')

    def test_user_exit_request_sells_without_ai_review_and_lapses_after_close(self):
        holdings = self.own(INV=3, KQ150INV=2)
        (self.root/'exit-requests.json').write_text(json.dumps(['114800']))
        status, submit, holding, _ = self.run_cycle(self.root, self.db, holdings)
        self.assertEqual(status, 'sell')
        self.assertEqual(submit.call_args.args[4:7], ('114800', 'SELL', 3))
        self.assertNotIn('114800', [c.args[2] for c in holding.call_args_list])
        self.assertEqual(json.loads((self.root/'exit-requests.json').read_text()), ['114800'])
        self.assertEqual(p.exit_requests(self.root, {'251340': 2}), {})
        self.assertFalse((self.root/'exit-requests.json').exists())
        for bad in ('not json', '["005930"]', '{"114800": 0}', '{"114800": "x"}'):
            (self.root/'exit-requests.json').write_text(bad)
            self.assertEqual(p.exit_requests(self.root, {'114800': 3}), {})

    def test_a_manual_buy_request_buys_that_many_through_the_guarded_path(self):
        # user 2026-11-17: buy a chosen number of shares by hand; consumed once tried, recorded as source 'manual'
        (self.root/'manual-buys.json').write_text(json.dumps({'229200': 7}))
        status, submit, _, events = self.run_cycle(self.root, self.db, {})
        self.assertEqual((status, submit.call_args.args[4:7]), ('buy', ('229200', 'BUY', 7)))
        self.assertFalse((self.root/'manual-buys.json').exists())
        self.assertIn('manual', [c.kwargs.get('source') for c in events.call_args_list if c.args[1] == 'submitted'])

    def test_a_quantity_exit_request_sells_that_many_once(self):
        # user 2026-11-17: sell a chosen number of shares by hand; the request is done once sent
        holdings = self.own(INV=3, KQ150INV=2)
        (self.root/'exit-requests.json').write_text(json.dumps({'114800': 2}))
        status, submit, _, _ = self.run_cycle(self.root, self.db, holdings)
        self.assertEqual((status, submit.call_args.args[4:7]), ('sell', ('114800', 'SELL', 2)))
        self.assertFalse((self.root/'exit-requests.json').exists())

    def test_etf7_jev_entry_threshold_is_point_six_and_legacy_stays_point_seven(self):
        jev = lambda c: {'model':'jev-1.14.0','chart_sha256':'a'*64,'candidate':{'type':'choice','choice':'INV','confidence':c}}
        modes = {'decision': False, 'jev': True}
        with patch.object(p, 'ETF7_JEV_ENTRY_MIN', 0.5):  # the default; data confirmation is checked after this gate
            self.assertEqual(p.select_entry(None, None, jev(0.5), etf7=True, modes=modes)[2], 'entry_conditions_met')
            self.assertEqual(p.select_entry(None, None, jev(0.499), etf7=True, modes=modes)[2], 'jev_low_confidence')
        self.assertEqual(p.select_entry('WAIT', 0.95, jev(0.65))[2], 'jev_low_confidence')

    def test_investor_flows_split_closed_and_provisional_days_and_fail_soft(self):
        class Reader:
            calls = 0
            def call_tool(self, tool, params):
                Reader.calls += 1
                return {'status': 'ok', 'data': {'rsp_cd': '00000', 'Output_0': [
                    {'bsop_date1': '20261106', 'frgn_ntby_qty': -100000, 'gigwan': 0, 'person': 0},
                    {'bsop_date1': '20261105', 'frgn_ntby_qty': 5, 'gigwan': 6, 'person': -11},
                    {'bsop_date1': '20261104', 'frgn_ntby_qty': 0, 'gigwan': 0, 'person': 0}]}}
        p._INVESTOR_CACHE.clear()
        flows = p.investor_flows(Reader(), NOW)
        self.assertEqual(flows['K200'], {'last_close_day': {'date': '20261105', 'foreign': 5, 'institution': 6, 'individual': -11},
                                         'today_provisional': {'date': '20261106', 'foreign': -100000, 'institution': 0, 'individual': 0}})
        self.assertEqual(Reader.calls, 7)
        p.investor_flows(Reader(), NOW + timedelta(seconds=540))
        self.assertEqual(Reader.calls, 7)  # cached for ten minutes
        class Broken:
            def call_tool(self, tool, params): return {'status': 'error'}
        self.assertIsNone(p.investor_flows(Broken(), NOW + timedelta(seconds=601)))
        p._INVESTOR_CACHE.clear()

    def test_minute_bar_read_is_retried_once(self):
        class Reader:
            def __init__(self, answers): self.answers, self.calls = list(answers), 0
            def call_tool(self, tool, params): self.calls += 1; return self.answers.pop(0)
        with patch.object(p.time, 'sleep'):
            flaky = Reader([{'status': 'error'}, {'status': 'ok'}])
            self.assertEqual(p.minute_bars(flaky, '069500')['status'], 'ok')
            self.assertEqual(flaky.calls, 2)
            down = Reader([{'status': 'error'}, {'status': 'error'}, {'status': 'ok'}])
            self.assertEqual(p.minute_bars(down, '069500')['status'], 'error')
            self.assertEqual(down.calls, 2)  # only one retry

    def test_one_cycle_reads_each_minute_chart_once(self):
        # r3 X3: each holding review re-read all seven ETF charts and the entry read them again (about 26% of NH bar
        # calls on 11/18, under the shared app key's rate limit); a failed read is not kept, so the next one retries
        from etf_universe import SYMBOLS as ETF7
        class Reader:
            def __init__(self): self.reads = []
            def call_tool(self, tool, params):
                self.reads.append(params['input']['iem_cd'])
                return {'status': 'ok', 'data': {}}
        jev = {'model': 'jev-1.14.0', 'chart_sha256': 'a' * 64, 'response_time': NOW.isoformat(),
               'holding': {'type': 'choice', 'choice': 'HOLD', 'confidence': 0.6}}
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return NOW
        read, cache = Reader(), {}
        with patch.object(p, 'datetime', Clock), patch.object(p, 'valid_bars', return_value=[{'bsop_time': '115800', 'stck_prpr': '1400'}] * 6), \
             patch.object(p, 'fresh_etf7_bars', return_value=True), patch('etf_news.headlines7', return_value=[{'title': 'm', 'published': NOW.isoformat()}]), \
             patch.object(p, 'investor_flows', return_value=None), patch.object(p, 'jev_holding7', return_value=jev), \
             patch.object(p, 'journal_event'):
            for symbol in ('069500', '114800'):
                p.holding_action(read, Path('/tmp'), symbol, 100, 100, etf7=True, modes={'decision': False, 'jev': True}, bar_cache=cache)
        self.assertEqual(sorted(read.reads), sorted(ETF7.values()))
        class Down:
            calls = 0
            def call_tool(self, tool, params): Down.calls += 1; return {'status': 'error'}
        with patch.object(p.time, 'sleep'):
            p.minute_bars(Down(), '000001', cache)
            p.minute_bars(Down(), '000001', cache)
        self.assertEqual((Down.calls, '000001' in cache), (4, False))

    def test_entry_bar_read_failure_waits_instead_of_stopping_the_runner(self):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return NOW
        class Reader:
            def call_tool(self, tool, params): return {'status': 'error'}
        def pages(b, operation, fields, block):
            return {'Output_0': []} if operation == 'krstockInquiryDailyOrderExecution' else {'Output_0': {'orr_pbl_amt1': 1}, 'Output_1': []}
        with patch.object(p,'datetime',Clock), patch.object(p,'paper_pages',side_effect=pages), \
             patch.object(p,'journal_event') as events, patch.object(p,'submit') as submit:
            self.assertEqual(p.cycle(self.db, Reader(), object(), ALT, self.root, True, NOW, etf7=True), 'wait')
        submit.assert_not_called()
        self.assertEqual(events.call_args.kwargs['reason'], 'market_bars_unavailable')
        self.assertEqual(events.call_args.kwargs['symbol'], next(iter(p.ETF7_SYMBOLS.values())))  # the first ETF read failed
        self.assertEqual(events.call_args.kwargs['bar_problem'], 'quote_error')

    def test_scanner_stock_can_be_bought_and_is_recorded_with_its_reasons(self):
        movers = {'005930': {'name': '삼성전자', 'reasons': ['거래대금 상위'], 'change_pct': 3.5, 'value_mil': 3000000, 'index': 'KOSPI200'}}
        with patch.object(p, 'mover_candidates', return_value=movers):
            status, submit, _, events = self.run_cycle(self.root, self.db, {}, jev_choice='005930', ask=70000)
        self.assertEqual(status, 'buy')
        self.assertEqual(submit.call_args.args[4:7], ('005930', 'BUY', 14))  # 1,000,000 // 70,000
        self.assertIn('005930', submit.call_args.kwargs['allowed'])
        context = next(c for c in events.call_args_list if c.args[1] == 'market_context')
        self.assertEqual(context.kwargs['candidates']['005930']['reasons'], ['거래대금 상위'])

    def strength_setup(self, probability):
        import json
        (self.root / 'watchlist.json').write_text(json.dumps({'time': NOW.isoformat(), 'selected': ['035900'],
                                                             'probabilities': {'035900': probability}}))
        weak = {'regime': '하락장', 'today_tone': '혼조', 'today_change_pct': 0.2}  # a long would normally be refused
        return ({'035900': {'name': 'JYP Ent.', 'reasons': ['급상승'], 'price': 1300, 'change_pct': 5.0, 'index': 'KOSDAQ150'}},
                patch.object(p, 'market_view', return_value={'코스피': weak, '코스닥': weak}))

    def test_watchlist_strength_buys_a_strong_jev_pick_when_jev_waits_in_a_weak_market(self):
        movers, view = self.strength_setup(0.69)
        with patch.object(p, 'mover_candidates', return_value=movers), view:
            status, submit, _, events = self.run_cycle(self.root, self.db, {}, jev_choice='WAIT')
        self.assertEqual(status, 'buy')
        self.assertEqual(submit.call_args.args[4:6], ('035900', 'BUY'))
        picked = next(c for c in events.call_args_list if c.args[1] == 'watchlist_strength')
        self.assertEqual(picked.kwargs['symbol'], '035900')
        self.assertEqual(next(c for c in events.call_args_list if c.args[1] == 'submitted').kwargs['source'], 'watchlist_strength')

    def test_a_blocked_jev_pick_records_what_the_strength_path_would_pick_without_ordering(self):
        # user 2026-11-17: observation only — Jev picked KQ150, the weak market refused it, nothing is bought
        movers, view = self.strength_setup(0.69)
        with patch.object(p, 'mover_candidates', return_value=movers), view:
            status, submit, _, events = self.run_cycle(self.root, self.db, {}, jev_choice='KQ150')
        self.assertEqual(status, 'wait')
        submit.assert_not_called()
        shadow = next(c for c in events.call_args_list if c.args[1] == 'strength_shadow').kwargs
        self.assertEqual((shadow['blocked'], shadow['symbol'], shadow['opposite_held']), ('direction_not_confirmed', '035900', False))
        with patch.object(p, 'strength_candidate', side_effect=KeyError('x')), patch.object(p, 'journal_event') as again:
            p.strength_shadow(self.root, movers, {}, {}, {}, None, 'trend_not_rising', 'a' * 32)  # never raises
        self.assertEqual(again.call_args.kwargs['error_type'], 'KeyError')

    def test_a_jev_wait_strength_pick_the_data_gate_refuses_leaves_the_cycle_to_a_new_listing(self):
        # 11-20 180640: Jev waited, the strength path picked a same-day re-entry the careful tape refused three times
        movers, view = self.strength_setup(0.69)
        rows = [{'bsop_time': f'11{58-i:02d}00', 'stck_prpr': str(1400-i), 'vol': str(200 if i < 5 else 100)} for i in range(10)]
        facts = {'name': '옵토닉스', 'listed': '20261117', 'sessions': 0, 'change_pct': 4.2, 'turnover_pct': 30.0}
        gate = []
        with patch.object(p, 'mover_candidates', return_value=movers), view, \
             patch.object(p, 'entry_block', side_effect=lambda *a, **k: gate.append(a[1]) or ('careful_reentry_chase' if a[1] == '035900' else None)), \
             patch.object(p, 'ipo_candidate', return_value=('493260', facts, rows)), patch.object(p, 'IPO_SIZE_PCT', 50), \
             patch.object(p, 'ipo_entries_today', return_value=0):
            status, submit, _, events = self.run_cycle(self.root, self.db, {}, jev_choice='WAIT', ask=1300)
        self.assertEqual(status, 'buy')
        self.assertEqual(submit.call_args.args[4:6], ('493260', 'BUY'))
        self.assertNotIn('watchlist_strength', [c.args[1] for c in events.call_args_list])
        self.assertEqual(gate.count('035900'), 1)
        with patch.object(p, 'mover_candidates', return_value=movers), view, \
             patch.object(p, 'entry_block', side_effect=lambda *a, **k: gate.append(a[1])) as block:
            status, submit, _, _ = self.run_cycle(self.root, self.db, {}, jev_choice='WAIT')
        self.assertEqual((status, submit.call_args.args[4:6]), ('buy', ('035900', 'BUY')))
        self.assertEqual(block.call_count, 1)  # passed once before the pick, not again at the main gate

    def test_watchlist_strength_needs_the_jev_watchlist_confidence(self):
        movers, view = self.strength_setup(0.1)  # under the 0.15 floor
        with patch.object(p, 'mover_candidates', return_value=movers), view:
            status, submit, _, events = self.run_cycle(self.root, self.db, {}, jev_choice='WAIT')
        self.assertEqual(status, 'wait')
        submit.assert_not_called()
        self.assertEqual(events.call_args.kwargs['strength_reason'], 'no_strength_candidate')

    def test_new_listing_slot_buys_while_jev_picks_and_an_inverse_is_held(self):
        # user 2026-11-17: the new listing is its own slot; Jev's inverse pick and the inverse held do not block it
        rows = [{'bsop_time': f'11{58-i:02d}00', 'stck_prpr': str(1400-i), 'vol': str(200 if i < 5 else 100)} for i in range(10)]
        facts = {'name': '옵토닉스', 'listed': '20261117', 'sessions': 0, 'change_pct': 4.2, 'turnover_pct': 30.0}
        holdings = self.own(INV=1)
        with patch.object(p, 'ipo_candidate', return_value=('493260', facts, rows)), patch.object(p, 'IPO_SIZE_PCT', 50), \
             patch.object(p, 'ipo_entries_today', return_value=0), patch.object(p, 'mover_candidates', return_value={}):
            status, submit, _, events = self.run_cycle(self.root, self.db, holdings, jev_choice='INV', ask=1300)
        self.assertEqual(status, 'buy')
        self.assertEqual(submit.call_args.args[4:6], ('493260', 'BUY'))
        self.assertEqual(next(c for c in events.call_args_list if c.args[1] == 'submitted').kwargs['source'], 'new_listing')
        with patch.object(p, 'ipo_candidate', return_value=('493260', facts, rows)) as ipo, \
             patch.object(p, 'ipo_entries_today', return_value=2), patch.object(p, 'mover_candidates', return_value={}):
            status, submit, _, _ = self.run_cycle(self.root, self.db, holdings, jev_choice='INV', ask=1300)
        ipo.assert_not_called()  # the day's two new-listing buys are used up
        self.assertNotEqual(submit.call_args.args[4:6] if submit.call_args else None, ('493260', 'BUY'))

    def test_strength_slot_buys_beside_jevs_pick_when_on_and_waits_behind_an_inverse(self):
        # user 2026-11-17 parallel slots: off by default (11-30 review); on, Jev's ETF pick does not hide a strong stock
        movers, view = self.strength_setup(0.69)
        with patch.object(p, 'mover_candidates', return_value=movers), view, patch.object(p, 'IPO_SLOT', False):
            status, submit, _, events = self.run_cycle(self.root, self.db, {}, jev_choice='KQ150')
        self.assertNotEqual(submit.call_args.args[4] if submit.call_args else None, '035900')  # slot off: no change
        movers, view = self.strength_setup(0.69)
        with patch.object(p, 'mover_candidates', return_value=movers), view, patch.object(p, 'IPO_SLOT', False), \
             patch('strategy_settings.read_settings', side_effect=lambda root: (
                 {**__import__('strategy_settings').DEFAULTS, 'strength_slot_on': 1}, 'ok')), \
             patch.object(p, 'entries_today', return_value=0):
            status, submit, _, events = self.run_cycle(self.root, self.db, {}, jev_choice='KQ150')
        self.assertEqual((status, submit.call_args.args[4]), ('buy', '035900'))
        self.assertEqual(next(c for c in events.call_args_list if c.args[1] == 'submitted').kwargs['source'], 'watchlist_strength')

    def test_slot_entry_limits(self):
        movers = {'035900': {'name': 'JYP', 'price': 1300, 'change_pct': 5.0}}
        rows = [{'bsop_time': f'11{58-i:02d}00', 'stck_prpr': str(1400-i), 'vol': str(200 if i < 5 else 100)} for i in range(10)]
        facts = {'jev_watchlist_confidence': 0.6, 'change_pct': 5.0}
        with patch.object(p, 'IPO_SLOT', False), patch.object(p, 'STRENGTH_SLOT', True), patch.object(p, 'OPPOSITE_GUARD', True), \
             patch.object(p, 'strength_candidate', return_value=('035900', facts)), patch.object(p, 'entries_today', return_value=0), \
             patch.object(p, 'exit_plans', return_value={}), patch.object(p, 'entry_block', return_value=None):  # the data gate has its own test
            slot = lambda owned: p.slot_entry(None, self.root, owned, movers, {'035900': rows}, None, {'114800'}, NOW)
            self.assertEqual(slot({})[:2], ('035900', 'watchlist_strength'))
            self.assertEqual(slot({'114800': 10})[:2], ('035900', 'watchlist_strength'))  # a held inverse no longer keeps stocks out (user 2026-11-20)
            self.assertIsNone(slot({'000001': 1, '000002': 1}))            # strength_slot_max (2) held
            with patch.object(p, 'entries_today', return_value=3):
                self.assertIsNone(slot({}))                                 # strength_slot_daily (3) used
            self.assertIsNotNone(p.strength_candidate.call_args.kwargs.get('sized'))  # sized as the order path sizes it

    def test_new_listing_time_stop(self):
        plan = {'profile': 'ipo', 'opened': NOW.isoformat()}
        self.assertIsNone(p.ipo_time_stop(plan, NOW + timedelta(minutes=59)))
        self.assertEqual(p.ipo_time_stop(plan, NOW + timedelta(minutes=60)), 'ipo_time_stop')
        self.assertIsNone(p.ipo_time_stop({**plan, 'profile': 'trend'}, NOW + timedelta(hours=3)))
        with patch.object(p, 'IPO_TIME_STOP_MIN', 0):
            self.assertIsNone(p.ipo_time_stop(plan, NOW + timedelta(hours=3)))

    def test_new_listing_is_bought_at_its_size_with_an_ipo_plan_when_nothing_else_qualifies(self):
        rows = [{'bsop_time': f'11{58-i:02d}00', 'stck_prpr': str(1400-i), 'vol': str(200 if i < 5 else 100)} for i in range(10)]
        facts = {'name': '바로팜', 'listed': '20261111', 'sessions': 1, 'change_pct': 4.2, 'turnover_pct': 30.0}
        with patch.object(p, 'ipo_candidate', return_value=('0153R0', facts, rows)), patch.object(p, 'IPO_SIZE_PCT', 50), \
             patch.object(p, 'mover_candidates', return_value={}):
            status, submit, _, events = self.run_cycle(self.root, self.db, {}, jev_choice='WAIT', ask=1300)
        self.assertEqual(status, 'buy')
        self.assertEqual(submit.call_args.args[4:7], ('0153R0', 'BUY', 100))  # min(100-share cap, 500,000 // 1,300 = 384)
        self.assertIn('0153R0', submit.call_args.kwargs['allowed'])
        self.assertEqual(p.exit_plans(self.root)['0153R0']['profile'], 'ipo')
        self.assertEqual(next(c for c in events.call_args_list if c.args[1] == 'submitted').kwargs['source'], 'new_listing')

    def test_strength_takes_jevs_top_three_and_skips_stocks_already_held(self):
        import json
        probabilities = {'000001': 0.30, '000002': 0.25, '000003': 0.20, '000004': 0.19, 'NONE': 0.06}
        (self.root / 'watchlist.json').write_text(json.dumps({'time': NOW.isoformat(), 'probabilities': probabilities}))
        rising = [{'stck_prpr': '105'}] + [{'stck_prpr': '103'}] * 5
        movers = {code: {'price': 100, 'change_pct': 0.0} for code in probabilities if code != 'NONE'}
        bars = {code: rising for code in movers}
        self.assertEqual(p.strength_candidate(self.root, movers, bars, NOW)[0], '000001')
        self.assertEqual(p.strength_candidate(self.root, movers, bars, NOW, owned={'000001': 4})[0], '000002')  # the next strong one
        only_fourth = {'000004': movers['000004']}
        self.assertEqual(p.strength_candidate(self.root, only_fourth, bars, NOW), (None, 'no_strength_candidate'))  # rank 4

    def test_strength_candidate_skips_stocks_near_the_limit_or_falling(self):
        import json
        (self.root / 'watchlist.json').write_text(json.dumps({'time': NOW.isoformat(), 'probabilities': {'000001': 0.9}}))
        rows = lambda first, sixth: [{'stck_prpr': str(first)}] + [{'stck_prpr': str(sixth)}] * 5
        movers = {'000001': {'price': 100, 'change_pct': 0.0}}
        self.assertEqual(p.strength_candidate(self.root, movers, {'000001': rows(127, 120)}, NOW), (None, 'strength_change_out_of_range'))
        self.assertEqual(p.strength_candidate(self.root, movers, {'000001': rows(101, 120)}, NOW), (None, 'strength_change_out_of_range'))
        self.assertEqual(p.strength_candidate(self.root, movers, {'000001': rows(105, 106)}, NOW), (None, 'trend_not_rising'))
        self.assertEqual(p.strength_candidate(self.root, movers, {'000001': rows(105, 103)}, NOW)[0], '000001')

    def test_a_pick_the_order_path_refuses_rests_instead_of_repeating_every_minute(self):
        # user 2026-11-19: a slot pick refused every minute (009150, 13:44~14:46) is fatal — it holds Jev's turn
        import json
        events = []
        record = lambda root, name, **fields: events.append((name, fields))
        def cycle_refusing(db, read, broker, account, root, *a, **k):
            p._PICK['pick'] = ('009150', 'watchlist_strength')
            return 'wait'
        def cycle_raising(db, read, broker, account, root, *a, **k):
            p._PICK['pick'] = ('0153R0', 'new_listing')
            raise ValueError('quote unavailable')
        def cycle_buying(db, read, broker, account, root, *a, **k):
            p._PICK['pick'] = ('000002', 'watchlist_strength')
            return 'buy'
        with patch.object(p, 'journal_event', side_effect=record):
            p.cool_refused_picks(cycle_refusing)(None, None, None, None, self.root)
            with self.assertRaises(ValueError):
                p.cool_refused_picks(cycle_raising)(None, None, None, None, self.root)
            p.cool_refused_picks(cycle_buying)(None, None, None, None, self.root)
        now = datetime.now(p.KST)
        self.assertTrue(p.cooling('009150', now) and p.cooling('0153R0', now))
        self.assertFalse(p.cooling('000002', now))                                  # sent: nothing to rest
        # review 11-19: one transient refusal (a stale signal, a failed quote) costs the pick only a short rest; a pick
        # refused again after each rest rests longer and longer (3, 6, 12 ... up to 30 minutes)
        self.assertFalse(p.cooling('009150', now + timedelta(minutes=3, seconds=5)))
        with patch.object(p, 'journal_event', side_effect=record):
            p.cool_refused_picks(cycle_refusing)(None, None, None, None, self.root)
        later = datetime.now(p.KST)
        self.assertTrue(p.cooling('009150', later + timedelta(minutes=5)))
        self.assertFalse(p.cooling('009150', later + timedelta(minutes=6, seconds=5)))
        for _ in range(5):
            with patch.object(p, 'journal_event', side_effect=record):
                p.cool_refused_picks(cycle_refusing)(None, None, None, None, self.root)
        self.assertFalse(p.cooling('009150', datetime.now(p.KST) + timedelta(minutes=30, seconds=5)))  # capped at 30
        with patch.object(p, 'journal_event', side_effect=record):
            p.cool_refused_picks(lambda *a, **k: p._PICK.update(pick=('009150', 'watchlist_strength')) or 'buy')(
                None, None, None, None, self.root)
        self.assertFalse(p.cooling('009150', datetime.now(p.KST)))                  # a buy clears its record
        self.assertEqual([(n, f['symbol'], f['source'], f['minutes']) for n, f in events[:3]],
                         [('pick_cooldown', '009150', 'watchlist_strength', 3), ('pick_cooldown', '0153R0', 'new_listing', 3),
                          ('pick_cooldown', '009150', 'watchlist_strength', 6)])
        with patch.object(p, 'journal_event', side_effect=record):
            p.cool_refused_picks(cycle_refusing)(None, None, None, None, self.root)
        now = datetime.now(p.KST)
        # while it rests the strength path looks past it, so the slot does not take the cycle with it again
        (self.root / 'watchlist.json').write_text(json.dumps({'time': now.isoformat(), 'probabilities': {'009150': 0.6, '000002': 0.3}}))
        rows = [{'stck_prpr': '105'}] + [{'stck_prpr': '103'}] * 5
        movers = {'009150': {'price': 100, 'change_pct': 0.0}, '000002': {'price': 100, 'change_pct': 0.0}}
        bars = {'009150': rows, '000002': rows}
        self.assertEqual(p.strength_candidate(self.root, movers, bars, now)[0], '000002')
        self.assertEqual(p.strength_candidate(self.root, {'009150': movers['009150']}, bars, now), (None, 'pick_cooldown'))
        import new_listings  # the new-listing search looks past a resting code the same way, before any quote read
        listing = {'code': '0153R0', 'name': '신규', 'listed': now.strftime('%Y%m%d')}
        class NoRead:
            def call_tool(self, *a): raise AssertionError('a resting code is not quoted')
        with patch.object(new_listings, 'recent', return_value=[listing]), patch.object(p, 'IPO_START', '00:00'), \
             patch.object(p, 'IPO_DAYS', 2), patch.object(p, 'exit_plans', return_value={}):
            self.assertEqual(p.ipo_candidate(NoRead(), self.root, {}, now), (None, 'pick_cooldown', None))
        p._COOLING.clear()
        self.assertEqual(p.strength_candidate(self.root, movers, bars, now)[0], '009150')

    def test_strength_candidate_skips_a_stock_one_buy_cannot_afford(self):
        # 11-19 13:44~13:52: 009150 at 847,000원 under a 1,000,000원 tranche × 0.5 (하락장) came out 0 shares every minute,
        # and as the slot's pick it held Jev's turn; the next strong stock should get the slot instead
        import json
        (self.root / 'watchlist.json').write_text(json.dumps({'time': NOW.isoformat(), 'probabilities': {'009150': 0.6, '000002': 0.3}}))
        rows = lambda price: [{'stck_prpr': str(price)}] + [{'stck_prpr': str(price * 0.98)}] * 5
        movers = {'009150': {'price': 847000, 'change_pct': 5.0}, '000002': {'price': 10500, 'change_pct': 5.0}}
        bars = {'009150': rows(847000), '000002': rows(10500)}
        import position_sizing  # the file's autouse fixture pins buy_factor at 1.0; this test needs a 하락장's 0.5
        with patch.object(position_sizing, 'buy_factor', return_value=(0.5, {'holding': 1.0, 'market': 0.5})), \
             patch.object(p, 'stop_symbol_index', return_value='코스피'), \
             patch.object(p, 'SPLIT_TRANCHE_KRW', 1_000_000), patch.object(p, 'SHARED_SINGLE_ORDER_KRW', 10_000_000):
            self.assertEqual(p.strength_shares(self.root, '009150', 847000), 0)
            self.assertEqual(p.strength_shares(self.root, '000002', 10500), 47)   # 1,000,000 // 10,500 = 95, × 0.5
            sized = lambda code, price: p.strength_shares(self.root, code, price)
            self.assertEqual(p.strength_candidate(self.root, movers, bars, NOW, sized=sized)[0], '000002')
            self.assertEqual(p.strength_candidate(self.root, {'009150': movers['009150']}, bars, NOW, sized=sized),
                             (None, 'one_share_over_buy'))
            self.assertEqual(p.strength_candidate(self.root, movers, bars, NOW)[0], '009150')  # unsized callers unchanged
        with patch.object(position_sizing, 'buy_factor', return_value=(1.0, {'holding': 1.0, 'market': 1.0})), \
             patch.object(p, 'stop_symbol_index', return_value='코스피'), patch.object(p, 'SPLIT_TRANCHE_KRW', 1_000_000), \
             patch.object(p, 'SHARED_SINGLE_ORDER_KRW', 10_000_000):
            self.assertEqual(p.strength_shares(self.root, '009150', 847000), 1)  # a rising market buys the full tranche
            # a careful re-entry (traded today, regime not 상승장) buys half again in the order path: 1 share → 0 (review 11-19)
            with patch.object(p, 'market_view', return_value={'코스피': {'regime': '횡보장'}}), \
                 patch.object(p, 'symbol_index', return_value='코스피'), patch.dict(p.CAREFUL, {'on': True, 'size': 50.0}):
                self.assertEqual(p.strength_shares(self.root, '009150', 847000, closed_today={'009150': 840000}), 0)
                self.assertEqual(p.strength_shares(self.root, '009150', 847000, owned={'009150': 1},
                                                   closed_today={'009150': 840000}), 1)  # held: an add, not a re-entry
                self.assertEqual(p.strength_shares(self.root, '000002', 10500, closed_today={'000002': 10000}), 47)
            with patch.object(p, 'market_view', return_value={'코스피': {'regime': '상승장'}}), \
                 patch.object(p, 'symbol_index', return_value='코스피'), patch.dict(p.CAREFUL, {'on': True, 'size': 50.0}):
                self.assertEqual(p.strength_shares(self.root, '009150', 847000, closed_today={'009150': 840000}), 1)

    def test_stale_scanner_series_is_dropped_not_blocking_etf_entries(self):
        movers = {'005930': {'name': '삼성전자', 'reasons': ['급상승']}}
        fresh = [{'bsop_time': f'11{58-i:02d}00', 'stck_prpr': str(1400-i)} for i in range(8)]
        stale = [{'bsop_time': f'10{58-i:02d}00', 'stck_prpr': '1'} for i in range(8)]
        with patch.object(p, 'mover_candidates', return_value=movers), \
             patch.object(p, 'valid_bars', side_effect=lambda data, code, now: stale if code == '005930' else fresh):
            # run_cycle patches valid_bars itself, so call the inner pieces directly
            self.assertFalse(p.fresh_series(stale, NOW))
            self.assertTrue(p.fresh_series(fresh, NOW))
            self.assertTrue(p.fresh_etf7_bars({n: fresh for n in p.ETF7_SYMBOLS}, NOW))
            self.assertFalse(p.fresh_etf7_bars({**{n: fresh for n in p.ETF7_SYMBOLS}, '005930': stale}, NOW))
            self.assertFalse(p.fresh_etf7_bars({'K200': fresh}, NOW))

    def test_owned_scanner_stock_passes_reconciliation_and_unowned_does_not(self):
        p.verify_positions([{'iem_cd': '005930', 'rsdl_qty': 3}], {'005930': 3}, etf7=True)
        with self.assertRaisesRegex(ValueError, 'unowned'):
            p.verify_positions([{'iem_cd': '005930', 'rsdl_qty': 3}], {}, etf7=True)
        with self.assertRaisesRegex(ValueError, 'unowned'):
            p.verify_positions([{'iem_cd': '005930', 'rsdl_qty': 3}], {'005930': 3})  # legacy universe unchanged

    def test_journal_with_watchlist_stock_bars_does_not_raise(self):
        row = {'bsop_time': '115800', 'stck_oprc': '100', 'stck_hgpr': '101', 'stck_lwpr': '99', 'stck_prpr': '100', 'vol': '5'}
        bars = {**{name: [row] for name in p.ETF7_SYMBOLS}, '005930': [row]}
        p.journal_event(self.root, 'market_context', market_bars=bars, context='entry', universe='etf7',
                        candidates={'005930': {'name': '삼성전자', 'reasons': ['급상승']}}, cycle_id='b' * 32)
        with p.sqlite3.connect(self.root / 'trading-ledger.sqlite3') as db:
            self.assertEqual(db.execute('select count(*) from events_v2').fetchone()[0], 1)

    def account(self, **changes):
        import accounts
        record = {**accounts.DEFAULTS['accounts']['paper-shared'], **changes}
        return patch('accounts.load', return_value={'paper-shared': record})

    def test_order_quantity_setting_is_refreshed_each_cycle(self):
        budget = {'first_buy_krw': 500_000, 'per_symbol_krw': 1_000_000}
        with self.account(budget=budget), patch.object(p, 'setting', return_value=400):
            status, submit, _, _ = self.run_cycle(self.root, self.db, {}, jev_choice='K200', ask=1000)
        self.assertEqual((status, submit.call_args.args[6]), ('buy', 400))
        p.apply_settings(self.root)

    def test_quantity_limits_can_be_disabled_or_set_independently(self):
        budget = {'first_buy_krw': 500_000, 'per_symbol_krw': 1_000_000}
        actual_setting = p.setting
        with self.account(budget=budget):
            for order_limit, total_limit, expected in [(0, 0, 500), (0, 150, 150), (80, 0, 80)]:
                with self.subTest(order_limit=order_limit, total_limit=total_limit), \
                     patch.object(p, 'setting', side_effect=lambda key: {'paper_max_order_qty': order_limit,
                                  'paper_max_total_qty': total_limit}.get(key, actual_setting(key))):
                    status, submit, _, _ = self.run_cycle(self.root, self.db, {}, jev_choice='K200', ask=1000)
                    self.assertEqual((status, submit.call_args.args[6]), ('buy', expected))
        p.apply_settings(self.root)

    def test_split_step_rules(self):
        plan = {'buys': 1, 'sells': 0, 'first': 1000, 'bought': 10, 'sold': 0, 'profit_sold': 0}
        self.assertEqual(p.split_step(plan, 10, 1000, 1010), 'take_profit')
        self.assertIsNone(p.split_step(plan, 1, 1000, 1010))  # one share waits for +2%
        self.assertIsNone(p.split_step(plan, 10, 1000, 1009))  # under the +1% first target
        self.assertIsNone(p.split_step(plan, 10, 1000, 992))  # a dip is not a sale (adds go through scale_in_signal)
        self.assertEqual(p.split_step({**plan, 'sells': 1, 'sold': 4, 'profit_sold': 4}, 6, 1000, 1015), 'take_profit')
        self.assertIsNone(p.split_step({**plan, 'sells': 1, 'sold': 5, 'profit_sold': 5}, 5, 1000, 1015))
        self.assertEqual(p.split_step({**plan, 'sells': 1, 'sold': 5, 'profit_sold': 0}, 5, 1000, 1015), 'take_profit')
        self.assertIsNone(p.split_step({**plan, 'sells': 1, 'sold': 4, 'profit_sold': None}, 6, 1000, 1015))

    def test_position_since_open_counts_only_the_current_position(self):
        rows = [('a', '114800', 'BUY', 5, 1300, NOW.isoformat()), ('b', '114800', 'SELL', 5, 1310, NOW.isoformat()),
                ('c', '114800', 'BUY', 10, 1000, NOW.isoformat()), ('d', '114800', 'BUY', 10, 993, NOW.isoformat())]
        self.db.executemany('INSERT INTO fills VALUES (?,?,?,?,?,?)', rows); self.db.commit()
        self.assertEqual(p.position_since_open(self.db, '114800', 20),
                         {'buys': 2, 'sells': 0, 'first': 1000, 'bought': 20, 'sold': 0, 'last': 'BUY'})
        with self.assertRaises(ValueError):
            p.position_since_open(self.db, '114800', 7)

    def test_rebuy_after_partial_sale_drops_the_breakeven_stop(self):
        # 11/16 114800: 100 bought, 50 sold at +2%, then 767 bought at 1,303 (average ~1,301); the bid 1,298 hit breakeven
        fill = lambda i, side, qty, price: (f'r{i}', '114800', side, qty, price, NOW.isoformat())
        self.db.executemany('INSERT INTO fills VALUES (?,?,?,?,?,?)', [fill(1, 'BUY', 100, 1273), fill(2, 'SELL', 50, 1299)])
        self.db.commit()
        half_sold = p.position_since_open(self.db, '114800', 50)
        self.assertTrue(p.breakeven_protected(half_sold, {}))  # after the sale the rest is guarded at breakeven
        self.assertFalse(p.breakeven_protected(half_sold, {'breakeven_after_half': False}))  # 낙폭과대 plans opt out
        self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', fill(3, 'BUY', 767, 1303)); self.db.commit()
        rebought = p.position_since_open(self.db, '114800', 817)
        self.assertFalse(p.breakeven_protected(rebought, {}))  # the re-buy uses the new average and the normal stop
        from decimal import Decimal
        levels = {'all': Decimal('1.02'), 'stop': Decimal('0.98'), 'half': Decimal('1.01'), 'trail': None}
        self.assertIsNone(p.exit_reason(1301.2, 1298, levels, p.breakeven_protected(rebought, {})))
        self.assertEqual(p.exit_reason(1301.2, 1275, levels, False), 'stop_loss')
        self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', fill(4, 'SELL', 400, 1320)); self.db.commit()
        self.assertTrue(p.breakeven_protected(p.position_since_open(self.db, '114800', 417), {}))  # selling again re-arms it
        self.assertFalse(p.breakeven_protected({'sells': 0, 'last': 'BUY'}, {}))  # never sold: no breakeven

    def test_take_profit_sells_half_and_dip_buys_next_tranche(self):
        holdings = self.own(KQ150=10)
        # +1.5% over the 1300 average: sell ceil(10/2)=5 through the protective path
        status, submit, _, events = self.run_cycle(self.root, self.db, holdings, bids={'229200': 1320})
        self.assertEqual((status, submit.call_args.args[4:7]), ('sell', ('229200', 'SELL', 5)))
        self.assertIn('split_take_profit', [c.args[1] for c in events.call_args_list])

    def test_nonprofit_partial_sale_does_not_count_as_take_profit_and_sellable_caps_order(self):
        self.own(KQ150=10)
        self.db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?,?,?,?)', ('manual', '20261106', '229200', 'SELL', 7, 1305, 'terminal', 1, 0.0))
        self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('manual', '229200', 'SELL', 7, 1305, NOW.isoformat()))
        self.db.execute('INSERT INTO sell_order_triggers VALUES (?,?)', ('manual', 'manual'))
        self.db.commit()
        status, submit, holding, _ = self.run_cycle(self.root, self.db, {'229200': 3}, bids={'229200': 1320}, sellable=2)
        self.assertEqual((status, submit.call_args.args[4:7]), ('sell', ('229200', 'SELL', 2)))
        self.assertIs(holding.call_args.kwargs['position_extra']['half_already_sold'], False)

    def test_unknown_sell_provenance_blocks_partial_profit_but_not_stop(self):
        self.own(KQ150=10)
        self.db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?,?,?,?)', ('unknown', '20261106', '229200', 'SELL', 4, 1305, 'terminal', 1, 0.0))
        self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('unknown', '229200', 'SELL', 4, 1305, NOW.isoformat()))
        self.db.commit()
        status, submit, holding, events = self.run_cycle(self.root, self.db, {'229200': 6}, bids={'229200': 1320})
        self.assertEqual(status, 'wait')
        submit.assert_not_called()
        self.assertIsNone(holding.call_args.kwargs['position_extra']['half_already_sold'])
        self.assertTrue(any(c.kwargs.get('reason') == 'take_profit_history_unavailable' for c in events.call_args_list))
        status, submit, _, _ = self.run_cycle(self.root, self.db, {'229200': 6}, bids={'229200': 1280})
        self.assertEqual((status, submit.call_args.args[4:7]), ('sell', ('229200', 'SELL', 6)))

    def test_holding_review_marks_half_sold_only_after_verified_profit_target(self):
        self.own(KQ150=10)
        self.db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?,?,?,?)', ('profit', '20261106', '229200', 'SELL', 5, 1320, 'terminal', 1, 0.0))
        self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('profit', '229200', 'SELL', 5, 1320, NOW.isoformat()))
        self.db.execute('INSERT INTO sell_order_triggers VALUES (?,?)', ('profit', 'take_profit'))
        self.db.commit()
        _, submit, holding, _ = self.run_cycle(self.root, self.db, {'229200': 5}, bids={'229200': 1315})
        self.assertIs(holding.call_args.kwargs['position_extra']['half_already_sold'], True)
        submit.assert_not_called()

    def test_dip_with_rebound_buys_a_capped_tranche(self):
        holdings = self.own(KQ150=10)
        bars = [{'bsop_time': f'11{58-i:02d}00', 'stck_prpr': str(1290 if i == 0 else 1280)} for i in range(8)]
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return NOW
        class Reader:
            def call_tool(self, tool, params): return {'status':'ok','data':{}}
        def pages(b, operation, fields, block):
            if operation == 'krstockInquiryDailyOrderExecution':
                return {'Output_0': []}
            return {'Output_0': {'orr_pbl_amt1': 10_000_000}, 'Output_1': [{'iem_cd': '229200', 'rsdl_qty': 10, 'phs_pr': 1300}]}
        def hold(r, rt, sym, avg, bid, **k):
            k['signal_context'].update(bars={n: bars for n in p.ETF7_SYMBOLS})
            return False
        with patch.object(p,'datetime',Clock), patch.object(p,'fresh_etf7_bars',return_value=True), patch.object(p,'paper_pages',side_effect=pages), \
             patch.object(p,'fresh_quote',return_value={'askp':1290,'bidp':1289,'iem_cd':'229200'}), \
             patch.object(p,'holding_action',side_effect=hold), patch.object(p,'etf7_protective_deadline',return_value=2**52), \
             patch.object(p,'ensure_trading_open'), patch.object(p,'journal_event') as events, \
             patch.object(p,'submit',return_value=('a'*32,1)) as submit:
            self.assertEqual(p.cycle(self.db, Reader(), object(), ALT, self.root, True, NOW, etf7=True), 'wait')
        submit.assert_not_called()  # holding data alone is not a fresh entry judgment

    def test_held_jev_signal_buys_once_and_keeps_exit_plan(self):
        holdings = self.own(INV=1)
        plan = {'114800': {'profile': 'trend', 'opened': NOW.isoformat(), 'peak': 1300,
                            'stop_pct': 2.0, 'trend_half_pct': 2.0, 'trend_trail_pct': 1.5}}
        with patch.object(p, 'exit_plans', return_value=plan), patch.object(p, 'save_exit_plan') as save:
            status, submit, _, _ = self.run_cycle(self.root, self.db, holdings, jev_choice='INV')
            self.assertEqual((status, submit.call_args.args[4:6]), ('buy', ('114800', 'BUY')))
            save.assert_not_called()
            status, submit, _, _ = self.run_cycle(self.root, self.db, holdings, jev_choice='INV')
            self.assertEqual(status, 'wait')
            submit.assert_not_called()

    def test_scale_signal_setting_applies_on_next_cycle_and_preserves_exit(self):
        from strategy_settings import DEFAULTS
        holdings = self.own(INV=1)
        plan = {'114800': {'profile': 'trend', 'opened': NOW.isoformat(), 'peak': 1300,
                            'stop_pct': 2.0, 'trend_half_pct': 2.0, 'trend_trail_pct': 1.5}}
        selected = {**DEFAULTS, 'scale_signal_add_on': 0, 'scale_step_add_on': 0, 'scale_profit_rebuy_on': 1}
        with patch.object(p, 'exit_plans', return_value=plan), patch('strategy_settings.read_settings', return_value=(selected, 'custom')) as read:
            status, submit, _, _ = self.run_cycle(self.root, self.db, holdings, jev_choice='INV')
            self.assertEqual(status, 'wait')
            submit.assert_not_called()
            read.return_value = ({**selected, 'scale_signal_add_on': 1}, 'custom')
            status, submit, _, _ = self.run_cycle(self.root, self.db, holdings, jev_choice='INV')
            self.assertEqual((status, submit.call_args.args[4:6]), ('buy', ('114800', 'BUY')))
            read.return_value = (dict(DEFAULTS), 'invalid')
            status, submit, _, _ = self.run_cycle(self.root, self.db, holdings, holding_sell=True)
            self.assertEqual((status, submit.call_args.args[5]), ('sell', 'SELL'))

    def test_jev_step_switch_requires_price_step_when_signal_add_is_disabled(self):
        from strategy_settings import DEFAULTS
        holdings = self.own(INV=1)
        plan = {'114800': {'profile': 'trend', 'opened': NOW.isoformat(), 'peak': 1300,
                            'stop_pct': 2.0, 'trend_half_pct': 2.0, 'trend_trail_pct': 1.5}}
        selected = {**DEFAULTS, 'scale_signal_add_on': 0, 'scale_step_add_on': 0, 'scale_profit_rebuy_on': 1}
        with patch.object(p, 'exit_plans', return_value=plan), patch.object(p, 'fresh_etf7_bars', return_value=True), \
             patch.object(p, 'scale_in_signal', return_value='dip'), patch.object(p, 'scale_in_projected_safe', return_value=True), \
             patch('strategy_settings.read_settings', return_value=(selected, 'custom')) as read:
            status, submit, _, _ = self.run_cycle(self.root, self.db, holdings, jev_choice='INV')
            self.assertEqual(status, 'wait')
            submit.assert_not_called()
            read.return_value = ({**selected, 'scale_step_add_on': 1}, 'custom')
            status, submit, _, _ = self.run_cycle(self.root, self.db, holdings, jev_choice='INV')
            self.assertEqual((status, submit.call_args.args[4:6]), ('buy', ('114800', 'BUY')))

    def test_jev_profit_rebuy_switch_blocks_only_verified_partially_sold_position(self):
        from strategy_settings import DEFAULTS
        self.own(INV=2)
        self.db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?,?,?,?)', ('profit', '20261116', '114800', 'SELL', 1, 1315, 'terminal', 123, NOW.timestamp()))
        self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('profit', '114800', 'SELL', 1, 1315, NOW.isoformat()))
        self.db.execute('INSERT INTO sell_order_triggers VALUES (?,?)', ('profit', 'take_profit'))
        self.db.commit()
        plan = {'114800': {'profile': 'trend', 'opened': NOW.isoformat(), 'peak': 1301,
                            'stop_pct': 2.0, 'trend_half_pct': 2.0, 'trend_trail_pct': 1.5}}
        selected = {**DEFAULTS, 'scale_signal_add_on': 1, 'scale_step_add_on': 0, 'scale_profit_rebuy_on': 0}
        with patch.object(p, 'exit_plans', return_value=plan), patch.object(p, 'scale_in_projected_safe', return_value=True), \
             patch('strategy_settings.read_settings', return_value=(selected, 'custom')) as read:
            status, submit, _, _ = self.run_cycle(self.root, self.db, {'114800': 1}, ask=1301,
                                                   bids={'114800': 1301}, jev_choice='INV')
            self.assertEqual(status, 'wait')
            submit.assert_not_called()
            read.return_value = ({**selected, 'scale_profit_rebuy_on': 1}, 'custom')
            status, submit, _, _ = self.run_cycle(self.root, self.db, {'114800': 1}, ask=1301,
                                                   bids={'114800': 1301}, jev_choice='INV')
            self.assertEqual((status, submit.call_args.args[4:6]), ('buy', ('114800', 'BUY')))

    def test_scale_in_requires_proven_profit_fill_and_price_step(self):
        buy_at, created_at, sold_at = NOW - timedelta(minutes=20), NOW - timedelta(minutes=12), NOW - timedelta(minutes=10)
        cycle = 'a' * 32
        self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('buy','114800','BUY',100,1272,buy_at.isoformat()))
        self.db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?,?,?,?)', ('sell','20261116','114800','SELL',50,1299,'terminal',123,created_at.timestamp()))
        self.db.execute('INSERT INTO decision_order_links VALUES (?,?)', ('sell',cycle))
        self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('sell','114800','SELL',50,1299,sold_at.isoformat()))
        self.db.commit()
        event = {'event':'sell_submitted','cycle_id':cycle,'symbol':'114800','qty':50,'trigger':'take_profit',
                 'time':(NOW-timedelta(minutes=11)).isoformat()}
        (self.root/'events.jsonl').write_text(json.dumps(event)+'\n')
        with patch.object(p, 'add_window_open', return_value=True):  # the add cutoff has its own test
            self.assertTrue(p.scale_in_allowed(self.db, self.root, '114800', 50))
            self.assertFalse(p.scale_in_allowed(self.db, self.root, '114800', 50, allow_profit_rebuy=False))
        # no add-ons in the last 30 minutes before the close cleanup (14:30 with the 15:00 flatten)
        with patch.object(p, 'FLATTEN_AT', '15:00'):
            late = NOW.replace(hour=14, minute=30)
            self.assertTrue(p.add_window_open(late - timedelta(minutes=1)))
            self.assertFalse(p.add_window_open(late))
            self.assertIsNone(p.scale_in_signal(self.db, self.root, '114800', 50, 1289, [1290,1280], late, 0.7))
            with patch.object(p, 'datetime') as clock:
                clock.now.return_value = late
                self.assertFalse(p.scale_in_allowed(self.db, self.root, '114800', 50))
        with patch.object(p, 'FLATTEN_AT', '14:40'):
            self.assertFalse(p.add_window_open(NOW.replace(hour=14, minute=10)))  # follows the flatten setting
        self.assertEqual(p.scale_in_signal(self.db, self.root, '114800', 50, 1289, [1290,1280], NOW, 0.7), 'dip')
        self.assertIsNone(p.scale_in_signal(self.db, self.root, '114800', 50, 1310, [1310,1300], NOW, 0.7))
        self.assertIsNone(p.scale_in_signal(self.db, self.root, '114800', 50, 1289, [1280,1290], NOW, 0.7))
        self.assertIsNone(p.scale_in_signal(self.db, self.root, '114800', 50, 1289, [1290,1280], sold_at+timedelta(minutes=4), 0.7))
        self.assertIsNone(p.scale_in_signal(self.db, self.root, '114800', 50, 1289, [1290,1280], NOW, 0.7, interval_min=11))
        for interval in (1, 60):
            with self.subTest(interval=interval):
                self.assertIsNone(p.scale_in_signal(self.db, self.root, '114800', 50, 1289, [1290,1280], sold_at+timedelta(minutes=interval, seconds=-1), 0.7, interval_min=interval))
                self.assertEqual(p.scale_in_signal(self.db, self.root, '114800', 50, 1289, [1290,1280], sold_at+timedelta(minutes=interval), 0.7, interval_min=interval), 'dip')
        with patch.object(p, 'add_window_open', return_value=True):  # not the wall clock
            (self.root/'events.jsonl').write_text(json.dumps({**event,'qty':49})+'\n')
            self.assertFalse(p.scale_in_allowed(self.db, self.root, '114800', 50))
            (self.root/'events.jsonl').write_text((json.dumps(event)+'\n')*2)
            self.assertFalse(p.scale_in_allowed(self.db, self.root, '114800', 50))
            (self.root/'events.jsonl').write_text(json.dumps({**event,'trigger':'risk'})+'\n')
            self.assertFalse(p.scale_in_allowed(self.db, self.root, '114800', 50))
            self.db.execute('INSERT INTO sell_order_triggers VALUES (?,?)', ('sell','model'))
            self.assertFalse(p.scale_in_allowed(self.db, self.root, '114800', 50))
            self.db.execute("UPDATE sell_order_triggers SET trigger='take_profit' WHERE order_id='sell'")
            self.assertTrue(p.scale_in_allowed(self.db, self.root, '114800', 50))

    def test_projected_average_must_not_immediately_trigger_exit(self):
        plan = {'profile':'defensive','stop_pct':2,'half_pct':1,'all_pct':2}
        market = {'코스피': {'regime':'하락장'}}
        self.assertFalse(p.scale_in_projected_safe(1000, 10, 1000, 10, 989, plan, market, '코스피', False))
        self.assertFalse(p.scale_in_projected_safe(1000, 10, 1020, 10, 1000, plan, market, '코스피', True))
        self.assertFalse(p.scale_in_projected_safe(1000, 10, 995, 10, 994, {**plan,'exiting':'stop_loss'}, market, '코스피', False))
        self.assertTrue(p.scale_in_projected_safe(1000, 10, 995, 10, 994, plan, market, '코스피', False))
        self.assertFalse(p.scale_in_projected_safe(1272, 50, 1302, 767, 1301, plan, market, '코스피', True))
        self.assertFalse(p.scale_in_projected_safe(1272, 50, 1289, 775, 1288, plan, market, '코스피', True))
        self.assertTrue(p.scale_in_projected_safe(1272, 50, 1289, 100, 1288, plan, market, '코스피', True))
        self.assertTrue(p.scale_in_projected_safe(1000, 1, 1006, 1, 1005, plan, market, '코스피', True))
        self.assertFalse(p.scale_in_projected_safe(1000, 1, 1006, 1, 1004, plan, market, '코스피', True))

    def test_take_profit_is_dropped_if_the_final_quote_loses_one_percent(self):
        holdings = self.own(KQ150=10)
        (self.root/'ai-modes.json').write_text(json.dumps({'version':1,'jev':False,'decision':False}))
        prices = iter([1320, 1320, 1305])  # pre-check bid, recheck bid, final bid before the sell
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return NOW
        class Reader:
            def call_tool(self, tool, params): return {'status':'ok','data':{}}
        class Broker:
            def call_tool(self, tool, params, **kwargs):
                return {'status':'ok','data':{'rsp_cd':'XA102','Output_0':{'iem_cd':'229200','sll_pbl_qty':10}}}
        def pages(b, operation, fields, block):
            if operation == 'krstockInquiryDailyOrderExecution':
                return {'Output_0': []}
            return {'Output_0': {'orr_pbl_amt1': 0}, 'Output_1': [{'iem_cd': '229200', 'rsdl_qty': 10, 'phs_pr': 1300}]}
        with patch.object(p,'datetime',Clock), patch.object(p,'paper_pages',side_effect=pages), \
             patch.object(p,'fresh_quote',side_effect=lambda *a: {'bidp': next(prices), 'askp': 1321}), \
             patch.object(p,'holding_action',return_value=False), patch.object(p,'journal_event'), \
             patch.object(p,'submit') as submit:
            p.cycle(self.db, Reader(), Broker(), ALT, self.root, True, NOW, etf7=True)
        submit.assert_not_called()

    def test_guard_refusal_before_reservation_is_terminal_and_does_not_block_next_cycle(self):
        class Broker:
            def call_tool(self, tool, args, **kwargs):
                return {'status':'error','error_type':'NHOrderBlocked','error':'Swing cumulative buy limit exceeded'}
        deadline = int(NOW.timestamp() * 1000) + 60_000
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return NOW
        with patch.object(p, 'datetime', Clock):
            with self.assertRaises(p.PaperOrderGuardRefused):
                p.submit(self.db, Broker(), ALT, '20261106', '229200', 'BUY', 10, 1300, etf7=True,
                         signal_check=lambda: True, deadline_ms=deadline)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM intents WHERE status!='terminal'").fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM pre_send_aborts').fetchone()[0], 1)
        class Ambiguous:
            def call_tool(self, tool, args, **kwargs):
                return {'status':'error','error_type':'NHOrderBlocked',
                        'order_safety':{'state':'uncertain','resubmit_allowed':False}}
        with patch.object(p, 'datetime', Clock):
            with self.assertRaisesRegex(ValueError, 'uncertain'):
                p.submit(self.db, Ambiguous(), ALT, '20261106', '229200', 'BUY', 10, 1300, etf7=True,
                         signal_check=lambda: True, deadline_ms=deadline)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM intents WHERE status='uncertain'").fetchone()[0], 1)

    # second review 2026-11-17: one test per defect, through the cycle where the defect lives

    def test_new_listing_is_sold_once_its_time_stop_passes(self):  # X1
        holdings = self.own(K200=10)
        for minutes, sold in ((59, []), (61, [('069500', 'SELL', 10)])):
            with self.subTest(minutes=minutes):
                plan = {**p.new_plan('069500', 'ipo'), 'opened': (NOW - timedelta(minutes=minutes)).isoformat()}
                (self.root/'exit-plans.json').write_text(json.dumps({'069500': plan}))
                _, submit, _, events = self.run_cycle(self.root, self.db, holdings, bids={'069500': 1300})
                self.assertEqual([c.args[4:7] for c in submit.call_args_list if c.args[5] == 'SELL'], sold)
        self.assertEqual([c.kwargs.get('reason') for c in events.call_args_list if c.args[1] == 'v2_risk_exit'], ['ipo_time_stop'])

    def test_only_a_take_profit_sale_arms_the_breakeven_stop(self):  # X2
        from etf_universe import SYMBOLS as ETF7
        self.own(K200=300)
        for trigger in ('manual', 'model', 'risk'):  # a hand-chosen quantity, Jev's 33% SELL_PART or a regime trim, a stop
            with self.subTest(trigger=trigger):
                self.db.execute("DELETE FROM fills WHERE order_id='s'"); self.db.execute('DELETE FROM sell_order_triggers')
                self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('s', ETF7['K200'], 'SELL', 10, 1298, (NOW + timedelta(minutes=1)).isoformat()))
                self.db.execute('INSERT INTO sell_order_triggers VALUES (?,?)', ('s', trigger)); self.db.commit()
                _, submit, _, _ = self.run_cycle(self.root, self.db, {'069500': 290}, bids={'069500': 1299})
                self.assertEqual([c.args[4:7] for c in submit.call_args_list if c.args[5] == 'SELL'], [])  # the rest keeps its normal stop

    def test_a_capped_regime_trim_is_marked_once_sent(self):  # X3
        holdings = self.own(INV=767)
        actual = p.setting
        with patch.object(p, 'setting', side_effect=lambda k: 100 if k == 'paper_max_order_qty' else actual(k)), \
             patch('position_sizing.regime_trim', return_value=383), patch('position_sizing.mark_trimmed') as marked:
            status, submit, _, _ = self.run_cycle(self.root, self.db, holdings)
        p.apply_settings(self.root)
        self.assertEqual((status, submit.call_args.args[4:7], marked.call_count), ('sell', ('114800', 'SELL', 100), 1))

    def test_an_oversold_review_is_told_its_half_was_sold(self):  # X5
        from etf_universe import SYMBOLS as ETF7
        self.own(K200=10)
        self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('h', ETF7['K200'], 'SELL', 5, 1352, (NOW + timedelta(minutes=1)).isoformat()))
        self.db.commit()
        (self.root/'exit-plans.json').write_text(json.dumps({'069500': p.new_plan('069500', 'oversold', target=8.0, basis={'half_tp_pct': 4.0})}))
        _, _, holding, _ = self.run_cycle(self.root, self.db, {'069500': 5}, bids={'069500': 1300})
        self.assertIs(holding.call_args.kwargs['position_extra']['half_already_sold'], True)

    def test_an_oversold_carry_is_journaled_as_the_decision_taken(self):  # X6
        holdings = self.own(K200=10)
        plan = {**p.new_plan('069500', 'oversold', target=8.0), 'hold_days': 1, 'opened': NOW.isoformat()}
        (self.root/'exit-plans.json').write_text(json.dumps({'069500': plan}))
        with patch.object(p, 'next_session_open', return_value=True):
            _, submit, _, events = self.run_cycle(self.root, self.db, holdings, bids={'069500': 1300}, at=NOW.replace(hour=14, minute=45))
        self.assertNotIn(('069500', 'SELL'), [c.args[4:6] for c in submit.call_args_list])  # carried for its sessions
        decisions = [(c.kwargs['permitted'], c.kwargs['reason']) for c in events.call_args_list if c.args[1] == 'overnight_hold']
        self.assertEqual(decisions, [(True, 'oversold_hold_days')])  # the live mirror reads this to carry it too

    def test_a_quantity_exit_request_keeps_its_unsold_rest_and_never_caps_a_stop(self):  # X7
        holdings = self.own(INV=10)
        path = self.root/'exit-requests.json'
        path.write_text(json.dumps({'114800': 5}))
        status, submit, _, _ = self.run_cycle(self.root, self.db, holdings, sellable=2)
        self.assertEqual((status, submit.call_args.args[4:7]), ('sell', ('114800', 'SELL', 2)))
        self.assertEqual(json.loads(path.read_text()), {'114800': 3})  # the 3 not sold yet stay requested
        path.write_text(json.dumps({'114800': 2}))
        status, submit, _, _ = self.run_cycle(self.root, self.db, holdings, bids={'114800': 1270})  # -2.3%: the stop
        self.assertEqual((status, submit.call_args.args[4:7]), ('sell', ('114800', 'SELL', 10)))
        self.assertFalse(path.exists())

    def test_a_v2_second_half_keeps_its_v2_plan(self):  # J1
        holdings = self.own(K200=50)
        basis = {'variant': 'v2', 'entry_price': 1350, 'stop_price': 1323, 'stop_pct': 2.0, 'half_tp_pct': 8.0,
                 'target_pct': 15.0, 'hold_days': 3, 'size_mult': 0.5}
        p.save_exit_plan(self.root, '069500', None, 'oversold', target=15.0, basis=basis)
        rows = [{'bsop_time': f'11{58-i:02d}00', 'stck_prpr': str(1400-i), 'vol': str(200 if i < 5 else 100)} for i in range(10)]
        with patch.object(p, 'v2_window', return_value=True), patch.object(p, 'v2_possible', return_value=True), \
             patch.object(p, 'oversold_v2_entry', return_value=('069500', rows, {**basis, 'add': True}, 'oversold_v2_add')):
            status, submit, _, _ = self.run_cycle(self.root, self.db, holdings)
        self.assertEqual((status, submit.call_args.args[4:6]), ('buy', ('069500', 'BUY')))
        plan = p.exit_plans(self.root)['069500']
        self.assertTrue(p.is_v2(plan))
        self.assertEqual((plan['hold_days'], plan['target_basis']['added']), (3, True))

    def _v2_plan(self, peak=None):
        basis = {'variant': 'v2', 'entry_price': 1300, 'stop_price': 1274, 'stop_pct': 2.0, 'half_tp_pct': 8.0,
                 'target_pct': 15.0, 'hold_days': 3, 'size_mult': 0.5}
        p.save_exit_plan(self.root, '069500', None, 'oversold', target=15.0, basis=basis)
        plans = p.exit_plans(self.root)
        p.write_exit_plans(self.root, {'069500': {**plans['069500'], 'peak': peak}})

    def _v2_cycle(self, settings, bid, **kwargs):
        from strategy_settings import DEFAULTS
        with patch('strategy_settings.read_settings', return_value=({**DEFAULTS, **settings}, 'custom')):
            _, submit, _, events = self.run_cycle(self.root, self.db, {'069500': 1}, bids={'069500': bid}, **kwargs)
        sold = [c.args[4:7] for c in submit.call_args_list if c.args[5] == 'SELL']
        return sold, [c.kwargs.get('reason') for c in events.call_args_list if c.args[1] == 'v2_risk_exit']

    def test_a_v2_position_in_profit_is_sold_on_a_confident_jev_sell_only_when_switched_on(self):  # user 2026-11-24
        self.own(K200=1)  # bought at 1300
        for on, bid, want in ((0, 1360, []), (1, 1360, [('069500', 'SELL', 1)]), (1, 1299, [])):  # at a loss its stop decides
            self._v2_plan()
            self.assertEqual((on, bid, self._v2_cycle({'oversold_v2_jev_sell_on': on}, bid, holding_sell=True)[0]), (on, bid, want))

    def test_a_v2_position_leaves_after_falling_back_from_its_high(self):  # user 2026-11-24
        self.own(K200=1)
        for at, peak, bid, leaves in ((5, 1400, 1370, True), (5, 1400, 1373, False), (5, 1360, 1310, False), (0, 1400, 1340, False)):
            self._v2_plan(peak)
            sold, reasons = self._v2_cycle({'oversold_v2_trail_at_pct': at, 'oversold_v2_trail_pct': 2}, bid)
            self.assertEqual((at, peak, bid, sold, reasons), (at, peak, bid, [('069500', 'SELL', 1)] if leaves else [],
                                                             ['trailing_stop'] if leaves else []))

    def test_every_holding_look_and_every_exit_records_the_quote_it_was_judged_on(self):
        """11-24 09:34 the 삼성전자 trailing exit left no bid: whether the line was touched could not be checked later."""
        from strategy_settings import DEFAULTS
        self.own(K200=1)
        self._v2_plan(1400)
        on = {**DEFAULTS, 'oversold_v2_trail_at_pct': 5, 'oversold_v2_trail_pct': 2}
        for bid, leaves in ((1373, False), (1370, True)):
            with patch('strategy_settings.read_settings', return_value=(on, 'custom')):
                _, _, _, events = self.run_cycle(self.root, self.db, {'069500': 1}, bids={'069500': bid})
            looks = [c.kwargs for c in events.call_args_list if c.args[1] == 'holding_quote']
            self.assertEqual([(e['symbol'], e['bid'], e['avg'], e['peak']) for e in looks], [('069500', bid, 1300, 1400)])
            exits = [c.kwargs for c in events.call_args_list if c.args[1] == 'v2_risk_exit']
            self.assertEqual([(e['reason'], e['bid'], e['first_bid'], e['peak']) for e in exits],
                             [('trailing_stop', 1370, 1370, 1400)] if leaves else [])

    def test_a_failed_quote_record_never_stops_an_exit(self):
        from strategy_settings import DEFAULTS
        self.own(K200=1)
        self._v2_plan(1400)
        with patch.object(p, 'holding_quote_record', side_effect=OSError('disk full')):
            sold, _ = self._v2_cycle({'oversold_v2_trail_at_pct': 5, 'oversold_v2_trail_pct': 2}, 1370)
        self.assertEqual(sold, [('069500', 'SELL', 1)])

    def test_the_add_rule_switch_holds_back_or_lets_through_an_add_that_is_not_better(self):
        from strategy_settings import DEFAULTS
        holdings = self.own(INV=1)
        for on in (1, 0):
            with patch.object(p, 'add_reason', return_value=None), \
                 patch('strategy_settings.read_settings', return_value=({**DEFAULTS, 'add_conf_sizing_on': on}, 'custom')):
                status, submit, _, events = self.run_cycle(self.root, self.db, holdings, jev_choice='INV')
            gates = [c.kwargs.get('reason') for c in events.call_args_list if c.args[1] == 'v2_execution_gate']
            sent = [c.kwargs for c in events.call_args_list if c.args[1] == 'submitted']
            if on:
                self.assertEqual((status, gates[-1:], sent), ('wait', ['add_not_better'], []))
                submit.assert_not_called()
            else:
                self.assertEqual((status, 'add_not_better' in gates, sent[-1]['add_reason']), ('buy', False, 'rule_off'))

    def test_used_up_adds_are_journaled_as_the_add_count_limit(self):  # J2
        for i in range(3):  # the first buy and two adds (scale_max_adds 2)
            self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', (f'b{i}', '114800', 'BUY', 100, 1300, (NOW - timedelta(minutes=30 - i)).isoformat()))
        self.db.commit()
        status, submit, _, events = self.run_cycle(self.root, self.db, {'114800': 300}, jev_choice='INV')
        self.assertEqual(status, 'wait')
        submit.assert_not_called()
        self.assertEqual([c.kwargs for c in events.call_args_list if c.args[1] == 'v2_entry_gate'][-1]['reason'], 'add_count_limit')

    def test_a_strength_slot_the_reentry_check_refuses_does_not_take_jevs_turn(self):  # J5
        from strategy_settings import DEFAULTS
        for order, side, price, minutes in (('r1', 'BUY', 1250, 120), ('r2', 'SELL', 1300, 60)):  # round trip closed today
            self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', (order, '000660', side, 10, price, (NOW - timedelta(minutes=minutes)).isoformat()))
        self.db.commit()
        (self.root/'watchlist.json').write_text(json.dumps({'time': NOW.isoformat(), 'probabilities': {'000660': 0.4}}))
        movers = {'000660': {'name': 'SK하이닉스', 'reasons': ['급상승'], 'price': 1400, 'change_pct': 5.0, 'index': 'KOSPI200'}}
        falling = {'regime': '하락장', 'today_tone': '약세', 'today_change_pct': -1.8}  # backs the inverse, not a long re-entry
        with patch.object(p, 'mover_candidates', return_value=movers), \
             patch.object(p, 'market_view', return_value={'코스피': falling, '코스닥': falling}), \
             patch('strategy_settings.read_settings', return_value=({**DEFAULTS, 'strength_slot_on': 1, 'ipo_slot_on': 0}, 'custom')), \
             patch.object(p, 'entries_today', return_value=0):
            status, submit, _, _ = self.run_cycle(self.root, self.db, {}, jev_choice='INV')
        self.assertEqual((status, submit.call_args.args[4:6] if submit.call_args else None), ('buy', ('114800', 'BUY')))

    def test_a_careful_strength_slot_is_reviewed_once_and_bought_smaller(self):  # r3 E1/E13
        from strategy_settings import DEFAULTS
        for order, side, price, minutes in (('r1', 'BUY', 1250, 120), ('r2', 'SELL', 1300, 60)):  # round trip closed today
            self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', (order, '000660', side, 10, price, (NOW - timedelta(minutes=minutes)).isoformat()))
        self.db.commit()
        (self.root/'watchlist.json').write_text(json.dumps({'time': NOW.isoformat(), 'probabilities': {'000660': 0.4}}))
        movers = {'000660': {'name': 'SK하이닉스', 'reasons': ['급상승'], 'price': 1400, 'change_pct': 5.0, 'index': 'KOSPI200'}}
        sideways = {'regime': '횡보장', 'today_tone': '혼조', 'today_change_pct': 0.3}  # careful for a long, the index its way
        bought = {}
        for careful in (False, True):
            tape, asked = [], []
            with patch.object(p, 'mover_candidates', return_value=movers), \
                 patch.object(p, 'market_view', return_value={'코스피': sideways, '코스닥': sideways}), \
                 patch('strategy_settings.read_settings', return_value=({**DEFAULTS, 'strength_slot_on': 1, 'ipo_slot_on': 0,
                                                                         'careful_reentry_on': int(careful)}, 'custom')), \
                 patch.object(p, 'entries_today', return_value=0), \
                 patch.object(p, 'careful_tape', side_effect=lambda *a: tape.append(a) or (None, {'vs_vwap_pct': 1.0})), \
                 patch('nh_etf_auto_v2.jev_reentry7', side_effect=lambda *a, **k: asked.append(a) or {'reentry': {'choice': 'BUY', 'confidence': 0.7}}):
                p._CAREFUL_REVIEW.clear()
                status, submit, _, events = self.run_cycle(self.root, self.db, {}, jev_choice='INV')
            self.assertEqual((status, submit.call_args.args[4:6]), ('buy', ('000660', 'BUY')))
            self.assertEqual((len(tape), len(asked)), (int(careful), int(careful)))  # once per pick, not again at the main gate
            bought[careful] = submit.call_args.args[6]
            self.assertEqual(next(c for c in events.call_args_list if c.args[1] == 'submitted').kwargs.get('careful_reentry'),
                             '횡보장' if careful else None)
        self.assertEqual(bought[True], int(bought[False] * p.CAREFUL['size'] / 100))
        p._CAREFUL_REVIEW.clear()

    def test_a_manual_buy_request_survives_a_dry_run_and_is_refused_after_the_flatten(self):  # J7
        path = self.root/'manual-buys.json'
        path.write_text(json.dumps({'229200': 7}))
        status, submit, _, _ = self.run_cycle(self.root, self.db, {}, execute=False)
        self.assertEqual(status, 'would_buy')
        self.assertEqual(json.loads(path.read_text()), {'229200': 7})  # not tried: still requested
        status, submit, _, events = self.run_cycle(self.root, self.db, {}, at=NOW.replace(hour=14, minute=45))
        submit.assert_not_called()
        self.assertFalse(path.exists())
        self.assertIn('manual_after_flatten', [c.kwargs.get('reason') for c in events.call_args_list if c.args[1] == 'v2_execution_gate'])

    def test_an_unsent_manual_buy_keeps_its_request_without_taking_the_cycle(self):
        # a kept request that cannot be sent this minute must not block every other entry until someone removes it
        path = self.root/'manual-buys.json'
        path.write_text(json.dumps({'069500': 3}))
        sent = ('a'*32, 1)
        status, submit, _, _ = self.run_cycle(self.root, self.db, {}, submit_effect=[p.PaperOrderNotSent('unsent'), sent])
        self.assertEqual([c.args[4:6] for c in submit.call_args_list], [('069500', 'BUY'), ('229200', 'BUY')])
        self.assertEqual((status, json.loads(path.read_text())), ('buy', {'069500': 3}))

    def test_the_entry_decision_reads_the_market_playbook(self):
        # user 2026-11-18: Jev's entry pick gets the 실전 시황 트레이딩 entry checks as reference context
        import market_playbook
        asked = []
        real = market_playbook.brief
        with patch.object(market_playbook, 'brief', side_effect=lambda kind: asked.append(kind) or real(kind)):
            self.run_cycle(self.root, self.db, {})
        self.assertIn('entry', asked)

    def test_confidence_is_recorded_only_for_jevs_own_pick(self):  # J8
        rows = [{'bsop_time': f'11{58-i:02d}00', 'stck_prpr': str(1400-i), 'vol': str(200 if i < 5 else 100)} for i in range(10)]
        facts = {'name': '옵토닉스', 'listed': '20261106', 'sessions': 0, 'change_pct': 4.2, 'turnover_pct': 30.0}
        for found, bought, confidence in ((('493260', facts, rows), '493260', None), ((None, 'no_new_listing', None), '114800', 0.9)):
            with self.subTest(bought=bought), patch.object(p, 'ipo_candidate', return_value=found), \
                 patch.object(p, 'ipo_entries_today', return_value=0), patch.object(p, 'mover_candidates', return_value={}):
                _, _, _, events = self.run_cycle(self.root, self.db, {}, jev_choice='INV')
            sent = next(c.kwargs for c in events.call_args_list if c.args[1] == 'submitted')
            self.assertEqual((sent['symbol'], sent.get('confidence')), (bought, confidence))


class SecondReviewUnitTests(unittest.TestCase):
    def test_a_failed_holiday_read_is_kept_ten_minutes_and_means_the_shorter_hold(self):  # X4
        from datetime import date
        calls, clock = [], [1000.0]
        def down(years):
            calls.append(years)
            raise OSError('KRX down')
        p._HOLIDAYS.clear()
        try:
            with patch('kr_swing_v5_daily_data.krx_holidays', side_effect=down), patch('time.monotonic', side_effect=lambda: clock[0]):
                self.assertEqual(p.sessions_held(date(2026, 12, 23), date(2026, 12, 28)), 3)  # unknown: every weekday counts
                p.sessions_held(date(2026, 12, 23), date(2026, 12, 28))
                self.assertFalse(p.next_session_open(date(2026, 12, 23)))  # unknown: not known to open, so not carried
                self.assertEqual(len(calls), 1)  # one read for all three questions
                clock[0] += 600
                p.sessions_held(date(2026, 12, 23), date(2026, 12, 28))
                self.assertEqual(len(calls), 2)  # tried again after ten minutes
            p._HOLIDAYS.clear()
            with patch('kr_swing_v5_daily_data.krx_holidays', return_value={date(2026, 12, 25), date(2026, 12, 31)}):
                self.assertEqual(p.sessions_held(date(2026, 12, 23), date(2026, 12, 28)), 2)
                self.assertEqual((p.next_session_open(date(2026, 12, 23)), p.next_session_open(date(2026, 12, 24))), (True, False))
        finally:
            p._HOLIDAYS.clear()

    def test_daily_buys_count_once_after_the_journal_is_rewritten(self):  # J3
        import os
        import shutil
        from trading_ledger import ingest
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            event = {'time': NOW.isoformat(), 'event': 'submitted', 'symbol': '123456', 'limit': 1000, 'qty': 1,
                     'source': 'new_listing', 'cycle_id': 'a' * 32}
            (root/'events.jsonl').write_text(json.dumps(event, ensure_ascii=False) + '\n')
            ingest(root/'events.jsonl', root/'trading-ledger.sqlite3')
            shutil.copy(root/'events.jsonl', root/'copy')
            os.replace(root/'copy', root/'events.jsonl')  # a restore or rewrite gives the file a new identity
            ingest(root/'events.jsonl', root/'trading-ledger.sqlite3')
            self.assertEqual(p.entries_today(root, NOW, 'new_listing'), 1)

    def test_a_v2_research_veto_survives_a_restart(self):  # J4
        import oversold
        from strategy_settings import DEFAULTS
        at = NOW.replace(hour=14, minute=35)
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return at
        row = {'code': '005930', 'price': 92.0, 'drop_pct': -11.0, 'market': 'kospi', 'market_low_pct': -9.0, 'rel_drop': -2.0,
               'stop_price': 90, 'stop_pct': 2.0, 'half_tp_pct': 8.0, 'target_pct': 15.0, 'hold_days': 3}
        gate = {'kospi': {'pass': True}, 'kosdaq': {'pass': False}, 'nasdaq_prev': -1.0}
        saved = dict(p.OVERSOLD)
        p.OVERSOLD.update({k: v for k, v in DEFAULTS.items() if k.startswith('oversold_')})
        for cache in (p._V2_BOUGHT, p._V2_LOGGED, p._V2_VERDICT):
            cache.clear()  # a restarted runner
        try:
            with tempfile.TemporaryDirectory() as d:
                veto = {'time': at.isoformat(), 'event': 'oversold_v2_signal', 'symbol': '005930', 'permitted': False, 'research': 'veto'}
                (Path(d)/'events.jsonl').write_text(json.dumps(veto) + '\n')
                with patch.object(p, 'datetime', Clock), patch.object(oversold, 'v2_gate', return_value=gate), \
                     patch.object(oversold, 'v2_candidates', side_effect=lambda read, g, st, codes, held=(): [r for r in [row] if r['code'] not in held]), \
                     patch.object(oversold, 'universe', return_value={'005930': '삼성전자'}), \
                     patch.object(p, 'fresh_rows', return_value=[{}] * 6), patch.object(p, 'journal_event'), \
                     patch.object(p, 'v2_research', return_value=('normal', 1.0, {})) as research:
                    self.assertIsNone(p.oversold_v2_entry(None, d, {}, 'c1'))
                research.assert_not_called()  # vetoed this morning: not researched or bought again today
        finally:
            p.OVERSOLD.clear()
            p.OVERSOLD.update(saved)
            for cache in (p._V2_BOUGHT, p._V2_LOGGED, p._V2_VERDICT):
                cache.clear()

    def test_oversold_entry_uses_the_configured_number_of_completed_bars(self):
        import oversold
        from strategy_settings import DEFAULTS

        now = NOW.replace(second=30)
        code = '005930'
        quote = {'stck_prpr': 94, 'stck_prdy_clpr': 100, 'stck_lwpr': 92}
        rows = [{'bsop_date': now.strftime('%Y%m%d'), 'bsop_time': (now - timedelta(minutes=i)).strftime('%H%M00'),
                 'stck_oprc': 93, 'stck_hgpr': 95, 'stck_lwpr': 93, 'stck_prpr': 94, 'vol': 10}
                for i in range(30)]
        reply = {'status': 'ok', 'data': {'rsp_cd': '00000', 'Output_0': {'iem_cd': code}, 'Output_1': rows}}

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return now

        for calm in (20, 25):
            p._OVERSOLD_ASKED.clear()
            try:
                def scan(read, at, settings, codes, held, bars_of):
                    return [oversold.evaluate(code, quote, bars_of(code), None, settings, at)]

                with self.subTest(calm=calm), patch.object(p, 'datetime', Clock), \
                        patch.dict(p.OVERSOLD, {**DEFAULTS, 'oversold_calm_min': calm}), \
                        patch.object(oversold, 'universe', return_value={code: '삼성전자'}), \
                        patch.object(oversold, 'scan', side_effect=scan), patch.object(oversold, 'brief', return_value={}), \
                        patch.object(oversold, 'market_ok', return_value=True), patch.object(p, 'minute_bars', return_value=reply), \
                        patch.object(p, 'journal_event'), patch.object(p, 'market_view'), patch.object(p, 'premarket_view'), \
                        patch('nh_etf_auto_v2.jev_oversold7',
                              return_value={'oversold': {'choice': 'BUY', 'confidence': 0.9}}) as asked:
                    result = p.oversold_entry(None, None, {}, {}, [], None, {'jev': True}, 'test')
                self.assertIsNotNone(result)
                self.assertEqual(len(asked.call_args.args[0][code]), calm)
                self.assertEqual(asked.call_args.args[0][code][0]['bsop_time'], '115800')
            finally:
                p._OVERSOLD_ASKED.clear()

    def test_an_oversold_buy_answer_is_remembered_like_a_refusal(self):  # J6
        import oversold
        row = {'code': '005930', 'triggered': True, 'stabilized': True, 'target_pct': 1.5, 'drop_pct': -7.0, 'atr_multiple': 2.0,
               'bounce_pct': 0.5, 'stop_pct': 0.5, 'prev_close': 100, 'low': 92, 'target_price': 95, 'stop_price': 91}
        def scan(read, now, settings, codes, held, bars_of):
            bars_of('005930')
            return [row]
        p._OVERSOLD_ASKED.clear()
        try:
            with patch.object(oversold, 'universe', return_value={'005930': '삼성전자'}), patch.object(oversold, 'scan', side_effect=scan), \
                 patch.object(oversold, 'brief', return_value={}), patch.object(oversold, 'market_ok', return_value=True), \
                 patch.object(p, 'minute_bars', return_value={'status': 'ok'}), patch.object(p, 'valid_bars', return_value=[{}] * 6), \
                 patch.object(p, 'fresh_series', return_value=True), patch.object(p, 'journal_event'), \
                 patch.object(p, 'market_view', return_value=None), patch.object(p, 'premarket_view', return_value=None), \
                 patch.dict(p.OVERSOLD, {'oversold_jev_min': 0.5}), \
                 patch('nh_etf_auto_v2.jev_oversold7', return_value={'oversold': {'choice': 'BUY', 'confidence': 0.9}}) as asked:
                first = p.oversold_entry(None, None, {}, {}, [], None, {'jev': True}, 'c1')
                again = p.oversold_entry(None, None, {}, {}, [], None, {'jev': True}, 'c2')  # a later gate refused the first
            self.assertEqual((first[0], again[0], asked.call_count), ('005930', '005930', 1))
        finally:
            p._OVERSOLD_ASKED.clear()


class ReentrySwitchTests(unittest.TestCase):
    def test_reentry_switch_and_market_check_for_strength_reentries(self):
        closed = {'100790': 14890}
        self.assertEqual(p.reentry_check('100790', {}, 'watchlist_strength', closed, True), (True, None))
        self.assertEqual(p.reentry_check('100790', {}, 'jev', closed, False), (True, 'reentry_disabled'))
        self.assertEqual(p.reentry_check('100790', {'100790': 5}, 'jev', closed, False), (False, None))  # held: an add
        self.assertEqual(p.reentry_check('005930', {}, 'jev', closed, False), (False, None))  # not traded today
        self.assertEqual(p.reentry_check('100790', {}, 'oversold_v2', closed, False), (False, None))  # its own rules
        # a strength re-entry is judged like Jev's path: the index must move its way (first strength entries use the
        # stock's own strength)
        view = {'코스피': {'regime': '하락장', 'today_tone': '약세', 'today_change_pct': -2.0}}
        rows = [{'vol': '200'}] * 5 + [{'vol': '100'}] * 5
        with patch.object(p, 'DIRECTION_CONFIRM', True), patch.object(p, 'volume_check', return_value=None):
            self.assertIsNone(p.entry_confirmation(view, '코스피', False, rows, own_strength=True))
            self.assertEqual(p.entry_confirmation(view, '코스피', False, rows, own_strength=False), 'direction_not_confirmed')
        import strategy_settings as ss
        self.assertEqual(ss.DEFAULTS['reentry_allow'], 1)
        self.assertIn('reentry_allow', ss.SWITCH_KEYS)


class ExitRuleTests(unittest.TestCase):
    def test_averaging_down_is_blocked_only_against_a_falling_tape(self):
        view = {'코스피': {'regime': '하락장', 'today_tone': '약세'}, '코스닥': {'regime': '하락장', 'today_tone': '강세'}}
        self.assertTrue(p.averaging_down_blocked(view, '069500', short=False))
        self.assertFalse(p.averaging_down_blocked(view, '233740', short=False))  # KOSDAQ ETF follows the KOSDAQ view
        self.assertFalse(p.averaging_down_blocked(view, '114800', short=True))
        self.assertTrue(p.averaging_down_blocked({'코스피': {'regime': '상승장', 'today_tone': '강세'}}, '114800', short=True))
        self.assertFalse(p.averaging_down_blocked(None, '069500', short=False))

    def test_stop_inside_spread_needs_a_real_spread(self):
        self.assertFalse(p.stop_inside_spread({'askp': 1300, 'bidp': 1295}))   # 26 won stop vs 5 won spread
        self.assertTrue(p.stop_inside_spread({'askp': 1300, 'bidp': 1280}))    # 26 won stop vs 20 won spread
        self.assertFalse(p.stop_inside_spread({'askp': 1300}))                 # no bid: cannot judge, entry unchanged

    def test_exit_reason_and_invalid_plans(self):
        levels = p.plan_levels(None)
        self.assertEqual(p.exit_reason(1000, 1020, levels, False), 'take_profit_all')
        self.assertEqual(p.exit_reason(1000, 980, levels, False), 'stop_loss')
        self.assertEqual(p.exit_reason(1000, 1000, levels, True), 'breakeven_stop')
        self.assertIsNone(p.exit_reason(1000, 1000, levels, False))
        with tempfile.TemporaryDirectory() as d:
            Path(d, 'exit-plans.json').write_text(json.dumps({'a': {'stop_pct': 2, 'half_pct': 3, 'all_pct': 2}, 'b': 'x'}))
            self.assertEqual(p.exit_plans(d), {})


class ReviewFixTests(unittest.TestCase):
    def test_scanner_stock_index_comes_from_watchlist_or_movers_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, 'watchlist.json').write_text(json.dumps({'stocks': {'247540': {'index': 'KOSDAQ150'}}}))
            Path(d, 'movers.json').write_text(json.dumps({'rows': {'000660': {'index': 'KOSPI200'}}}))
            self.assertEqual(p.symbol_index(d, '247540'), '코스닥')
            self.assertEqual(p.symbol_index(d, '000660'), '코스피')
            self.assertEqual(p.symbol_index(d, '251340'), '코스닥')
            self.assertEqual(p.symbol_index(d, '999999'), '코스피')
        view = {'코스닥': {'regime': '하락장', 'today_tone': '약세'}, '코스피': {'regime': '상승장', 'today_tone': '강세'}}
        self.assertTrue(p.averaging_down_blocked(view, '247540', False, '코스닥'))



class TrendProfileTests(unittest.TestCase):
    def test_trend_mode_follows_regime_and_strong_days(self):
        view = lambda r, t: {'코스피': {'regime': r, 'today_tone': t}}
        self.assertTrue(p.trend_mode(view('상승장', '혼조'), '코스피', False))
        self.assertFalse(p.trend_mode(view('상승장', '약세'), '코스피', False))
        self.assertTrue(p.trend_mode(view('하락장', '강세'), '코스피', False))
        self.assertFalse(p.trend_mode(view('횡보장', '혼조'), '코스피', False))
        self.assertTrue(p.trend_mode(view('하락장', '혼조'), '코스피', True))   # inverse rides a falling market
        self.assertFalse(p.trend_mode(None, '코스피', False))

    def test_trailing_stop_starts_after_the_first_take_profit_level(self):
        levels = p.plan_levels({'profile': 'trend', 'stop_pct': 2.0, 'half_pct': 1.0, 'all_pct': 2.0,
                                'trend_half_pct': 2.0, 'trend_trail_pct': 1.5})
        self.assertIsNone(levels['all'])
        self.assertIsNone(p.exit_reason(1000, 1060, levels, True, peak=1060))          # no fixed final take-profit
        self.assertEqual(p.exit_reason(1000, 1014, levels, True, peak=1030), 'trailing_stop')
        self.assertIsNone(p.exit_reason(1000, 1016, levels, True, peak=1030))
        self.assertIsNone(p.exit_reason(1000, 1005, levels, False, peak=1015))          # peak below +2%: no trail yet
        self.assertEqual(p.exit_reason(1000, 980, levels, False, peak=1015), 'stop_loss')
        self.assertEqual(p.new_plan('122630', 'trend')['trend_trail_pct'], round(p.TREND_TRAIL_PCT * 1.5, 3))

    def test_overnight_reasons(self):
        from datetime import date
        trend = {**p.new_plan('069500', 'trend'), 'peak': 1330}  # reached the +2% trend target (1326)
        strong = {'코스피': {'regime': '상승장', 'today_tone': '강세', 'advancers_decliners': [800, 300],
                   'investor_net_100m_krw': {'외국인': 900.0, '기관': 400.0, '개인': -1300.0}, 'program_net_100m_krw': {'전체': 1200.0}}}
        stats = {'median_up_pct': 1.5, 'volume_ratio': 2.1, 'sessions': 20, 'ma5': 1300.0, 'ma20': 1280.0,
                 'today': {'open': 1290.0, 'high': 1320.0, 'low': 1285.0, 'price': 1316.0, 'volume': 1.0}}
        args = dict(view=strong, index='코스피', short=False, avg=1300, bid=1316, protected=True, first_target_filled=True, budget_left=3_000_000, cost=2600,
                    today=date(2026, 11, 12), stats=stats, jev_view=('HOLD', 0.72))
        item = strong['코스피']
        with patch.object(p, 'next_session_open', return_value=True):
            self.assertIsNone(p.overnight_reason(trend, **args))
            failing = {
                'not_trend_profile': dict(plan=p.new_plan('069500')),
                'regime_not_rising': dict(view={'코스피': {**item, 'today_tone': '혼조'}}),
                'breadth_not_broad': dict(view={'코스피': {**item, 'advancers_decliners': [500, 300]}}),
                'flows_not_supporting': dict(view={'코스피': {**item, 'program_net_100m_krw': {'전체': -5.0}}}),
                'not_first_target': dict(protected=False),
                'not_first_target ': dict(plan={**trend, 'peak': 1320}),  # sold half earlier, but never reached the trend target
                'not_first_target  ': dict(first_target_filled=False),
                'volume_not_high': dict(stats={**stats, 'volume_ratio': 1.2}),
                'weak_close': dict(bid=1295),  # lower 20% of the day's range
                'not_uptrend': dict(stats={**stats, 'ma5': 1270.0}),
                'jev_not_supportive': dict(jev_view=('HOLD', 0.5)),
                'overnight_limit': dict(cost=4_000_000),
            }
            for code, change in failing.items():
                plan = change.pop('plan', trend)
                with self.subTest(code=code):
                    self.assertEqual(p.overnight_reason(plan, **{**args, **change}), code.strip())
            self.assertEqual([c for c, _ in p.OVERNIGHT_RULES][:-1], list(dict.fromkeys(c.strip() for c in failing)))  # the dashboard list matches the checks
        with patch.object(p, 'next_session_open', return_value=False):
            self.assertEqual(p.overnight_reason(trend, **args), 'market_closed_next_day')
        self.assertFalse(p.next_session_open(date(2026, 11, 13)))  # Friday -> Saturday, no network needed

    def test_confirmed_take_profit_target_tracks_rebuy_and_rounding(self):
        with tempfile.TemporaryDirectory() as folder:
            db = p.connect_journal(Path(folder) / 'orders.sqlite3', p.account_hash(ALT))
            def fill(order, side, qty, trigger=None, status='terminal', ordered=None):
                db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?,?,?,?)',
                           (order, '20261106', '114800', side, ordered or qty, 1000, status, 1, 0.0))
                db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', (order, '114800', side, qty, 1010 if side == 'SELL' else 1000, NOW.isoformat()))
                if trigger is not None:
                    db.execute('INSERT INTO sell_order_triggers VALUES (?,?)', (order, trigger))
                db.commit()
            # A prior fully closed position must not contribute to this position's target.
            fill('old-buy', 'BUY', 4); fill('old-sell', 'SELL', 4, 'take_profit')
            fill('buy', 'BUY', 11); fill('partial', 'SELL', 5, 'take_profit', ordered=6)
            self.assertEqual(p.verified_take_profit_sold(db, '114800', 6), 5)
            self.assertFalse(p.take_profit_progress(db, '114800', 6))  # ceil(11/2) = 6
            fill('rest', 'SELL', 1, 'take_profit')
            self.assertTrue(p.take_profit_progress(db, '114800', 5))
            fill('rebuy', 'BUY', 5)
            self.assertFalse(p.take_profit_progress(db, '114800', 10))  # ceil((11+5)/2) = 8
            fill('rebuy-profit', 'SELL', 2, 'take_profit')
            self.assertTrue(p.take_profit_progress(db, '114800', 8))
            db.close()

    def test_the_first_take_profit_halves_what_is_left_after_other_sales(self):
        # user decision 2026-11-18 (review 3 X1): after a regime trim or a Jev partial sell, the first take-profit sells half
        # of the shares still held, not 'half of the shares bought' (which sold the whole rest after a 50% trim)
        half = p.plan_levels({'profile': 'trend', 'stop_pct': 2.0, 'half_pct': 1.0, 'all_pct': 2.0, 'trend_half_pct': 2.0,
                              'trend_trail_pct': 1.5})['half']
        for sold, trigger, expected in ((50, 'model', 25), (33, 'model', 34), (10, 'manual', 45), (0, None, 50)):
            with self.subTest(sold=sold, trigger=trigger), tempfile.TemporaryDirectory() as folder:
                db = p.connect_journal(Path(folder) / 'orders.sqlite3', p.account_hash(ALT))
                db.execute("INSERT INTO intents VALUES ('b1','20261118','114800','BUY',100,1000,'terminal',1,0)")
                db.execute("INSERT INTO fills VALUES ('b1','114800','BUY',100,1000,?)", (NOW.isoformat(),))
                if sold:
                    db.execute("INSERT INTO intents VALUES ('s1','20261118','114800','SELL',?,990,'terminal',2,0)", (sold,))
                    db.execute("INSERT INTO fills VALUES ('s1','114800','SELL',?,990,?)", (sold, NOW.isoformat()))
                    db.execute("INSERT INTO sell_order_triggers VALUES ('s1',?)", (trigger,))
                db.commit()
                held = 100 - sold
                plan = p.position_since_open(db, '114800', held)
                plan['base'] = p.take_profit_base(db, '114800', held)
                plan['profit_sold'] = p.verified_take_profit_sold(db, '114800', held)
                self.assertEqual(p.split_step(plan, held, 1000, 1021, half=half), 'take_profit')
                self.assertEqual(p.take_profit_target(plan) - plan['profit_sold'], expected)
                db.close()

    def test_a_sale_after_the_first_take_profit_does_not_move_its_target(self):
        with tempfile.TemporaryDirectory() as folder:
            db = p.connect_journal(Path(folder) / 'orders.sqlite3', p.account_hash(ALT))
            def fill(order, side, qty, trigger=None):
                db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?,?,?,?)', (order, '20261118', '114800', side, qty, 1000, 'terminal', 1, 0.0))
                db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', (order, '114800', side, qty, 1010 if side == 'SELL' else 1000, NOW.isoformat()))
                if trigger:
                    db.execute('INSERT INTO sell_order_triggers VALUES (?,?)', (order, trigger))
                db.commit()
            fill('b', 'BUY', 100); fill('trim', 'SELL', 40, 'model')
            self.assertEqual(p.take_profit_base(db, '114800', 60), 60)
            fill('tp', 'SELL', 30, 'take_profit')
            self.assertTrue(p.take_profit_progress(db, '114800', 30))
            fill('part', 'SELL', 10, 'model')  # sold after the first take-profit: the half already taken stays taken
            self.assertEqual(p.take_profit_base(db, '114800', 20), 60)
            self.assertTrue(p.take_profit_progress(db, '114800', 20))
            fill('rebuy', 'BUY', 20)  # a rebuy adds to the shares the take-profit halves, as before
            self.assertEqual(p.take_profit_base(db, '114800', 40), 80)
            self.assertFalse(p.take_profit_progress(db, '114800', 40))
            db.execute("DELETE FROM sell_order_triggers WHERE order_id='trim'"); db.commit()
            self.assertIsNone(p.take_profit_base(db, '114800', 40))  # a sale of unknown rule: no take-profit step
            self.assertFalse(p.take_profit_progress(db, '114800', 40))
            db.close()

    def test_nonprofit_and_uncertain_sales_are_not_profit_fills(self):
        with tempfile.TemporaryDirectory() as folder:
            db = p.connect_journal(Path(folder) / 'orders.sqlite3', p.account_hash(ALT))
            db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('buy', '114800', 'BUY', 10, 1000, NOW.isoformat()))
            db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?,?,?,?)', ('manual', '20261106', '114800', 'SELL', 4, 1000, 'terminal', 1, 0.0))
            db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('manual', '114800', 'SELL', 4, 1000, NOW.isoformat()))
            db.execute('INSERT INTO sell_order_triggers VALUES (?,?)', ('manual', 'manual'))
            db.commit()
            self.assertEqual(p.verified_take_profit_sold(db, '114800', 6), 0)
            self.assertFalse(p.take_profit_progress(db, '114800', 6))
            db.execute("UPDATE sell_order_triggers SET trigger='risk' WHERE order_id='manual'")
            db.commit()
            self.assertEqual(p.verified_take_profit_sold(db, '114800', 6), 0)
            db.execute("UPDATE sell_order_triggers SET trigger='take_profit' WHERE order_id='manual'")
            db.execute("UPDATE intents SET status='acknowledged' WHERE id='manual'")
            db.commit()
            self.assertIsNone(p.verified_take_profit_sold(db, '114800', 6))
            self.assertFalse(p.take_profit_progress(db, '114800', 6))
            db.execute("UPDATE intents SET status='terminal' WHERE id='manual'")
            db.execute("UPDATE fills SET price=999 WHERE order_id='manual'")
            db.commit()
            self.assertIsNone(p.verified_take_profit_sold(db, '114800', 6))  # below the sell limit is not a verified fill
            db.execute("UPDATE fills SET price=1000 WHERE order_id='manual'")
            db.commit()
            self.assertIsNone(p.verified_take_profit_sold(db, '114800', 6))  # tagged sale at cost is not a profit
            db.execute("DELETE FROM sell_order_triggers WHERE order_id='manual'")
            db.commit()
            self.assertIsNone(p.verified_take_profit_sold(db, '114800', 6))
            db.close()

    def test_swing_reasons_for_a_carried_trend_position(self):
        from datetime import date
        plan = {**p.new_plan('069500', 'trend'), 'opened': '2026-11-10T10:00:00+09:00'}  # Tuesday entry
        view = {'코스피': {'regime': '상승장', 'today_tone': '혼조'}}  # a mixed day is enough once it is a swing
        stats = {'ma5': 1300.0, 'ma20': 1280.0}
        args = dict(view=view, index='코스피', short=False, bid=1316, today=date(2026, 11, 12), budget_left=3_000_000, cost=2600,
                    stats=stats, jev_view=('HOLD', 0.4))
        self.assertEqual(p.sessions_held(date(2026, 11, 13), date(2026, 11, 16)), 1)  # Friday -> Monday is one session
        with patch.object(p, 'next_session_open', return_value=True), patch.object(p, 'SWING_MAX_DAYS', 5):
            self.assertIsNone(p.swing_reason(plan, **args))
            failing = {
                'swing_days_exceeded': dict(today=date(2026, 11, 14 + 3)),  # Tue entry, next Tue close: 5 sessions held
                'regime_not_rising': dict(view={'코스피': {'regime': '상승장', 'today_tone': '약세'}}),
                'not_uptrend': dict(stats={'ma5': 1320.0, 'ma20': 1280.0}),
                'jev_selling': dict(jev_view=('SELL', 0.65)),
                'overnight_limit': dict(cost=4_000_000),
            }
            for code, change in failing.items():
                with self.subTest(code=code):
                    self.assertEqual(p.swing_reason(plan, **{**args, **change}), code)
            self.assertEqual([c for c, _ in p.SWING_RULES][:-1], list(failing))  # the dashboard list matches the checks
            inverse = {**args, 'short': True, 'view': {'코스피': {'regime': '하락장', 'today_tone': '혼조'}}}
            self.assertIsNone(p.swing_reason(plan, **inverse))


class DataTargetTests(unittest.TestCase):
    def test_daily_stats_use_past_sessions_and_todays_volume(self):
        from datetime import date
        bar = lambda d, h, c, v: {'bsop_date': d, 'stck_oprc': '100', 'stck_hgpr': str(h), 'stck_lwpr': '99', 'stck_prpr': str(c), 'vol': str(v)}
        rows = [bar('20261112', 103, 102, 300)] + [bar(f'202611{d:02d}', 100 + d % 4, 100, 100) for d in range(11, 0, -1)]
        stats = p.stats_from_daily(rows, date(2026, 11, 12))
        self.assertEqual((stats['sessions'], stats['volume_ratio']), (11, 3.0))
        self.assertEqual(stats['median_up_pct'], 2.0)
        self.assertEqual((stats['today']['high'], stats['ma5'], stats['ma20']), (103.0, 100.4, None))  # 12 closes: no 20-day mean
        self.assertIsNone(p.stats_from_daily(rows[:5], date(2026, 11, 12)))   # too few sessions to trust

    def test_defensive_target_scales_by_regime_within_bounds(self):
        stats = {'median_up_pct': 3.0}
        self.assertEqual(p.defensive_target(stats, '하락장'), 1.2)
        self.assertEqual(p.defensive_target(stats, '횡보장'), 1.5)
        self.assertEqual(p.defensive_target({'median_up_pct': 1.0}, '하락장'), 0.8)   # floor above costs
        self.assertEqual(p.defensive_target({'median_up_pct': 9.0}, '횡보장'), 3.0)   # cap
        self.assertIsNone(p.defensive_target(None, '하락장'))
        plan = p.new_plan('069500', target=1.2)
        self.assertEqual((plan['half_pct'], plan['all_pct']), (1.2, 1.2))
        with tempfile.TemporaryDirectory() as d:  # a full-exit plan (half == all) survives validation
            Path(d, 'exit-plans.json').write_text(json.dumps({'069500': plan}))
            self.assertIn('069500', p.exit_plans(d))



class EntryConfirmationTests(unittest.TestCase):
    def test_index_direction_and_rising_volume_confirm_a_buy(self):
        rows = lambda recent, older: [{'vol': str(recent)}] * 5 + [{'vol': str(older)}] * 5
        flat = {'코스피': {'regime': '횡보장', 'today_tone': '혼조', 'today_change_pct': 0.3}}
        self.assertIsNone(p.entry_confirmation(flat, '코스피', False, rows(130, 100)))
        self.assertEqual(p.entry_confirmation(flat, '코스피', False, rows(110, 100)), 'volume_not_rising')
        self.assertEqual(p.entry_confirmation(flat, '코스피', True, rows(130, 100)), 'direction_not_confirmed')  # inverse on an up day
        falling = {'코스피': {'regime': '하락장', 'today_tone': '혼조', 'today_change_pct': -0.4}}
        self.assertEqual(p.entry_confirmation(falling, '코스피', False, rows(130, 100)), 'direction_not_confirmed')
        self.assertIsNone(p.entry_confirmation(falling, '코스피', True, rows(130, 100)))
        rally = {'코스피': {'regime': '하락장', 'today_tone': '강세', 'today_change_pct': 2.6}}
        self.assertIsNone(p.entry_confirmation(rally, '코스피', False, rows(130, 100)))
        self.assertEqual(p.entry_confirmation(None, '코스피', False, rows(130, 100)), 'market_unconfirmed')
        self.assertEqual(p.entry_confirmation(flat, '코스피', False, rows(130, 100)[:6]), 'volume_not_rising')
        with patch.object(p, 'DIRECTION_CONFIRM', False):  # switched off on the dashboard: volume only
            self.assertIsNone(p.entry_confirmation(falling, '코스피', False, rows(130, 100)))
            self.assertIsNone(p.entry_confirmation(None, '코스피', False, rows(130, 100)))
            self.assertEqual(p.entry_confirmation(None, '코스피', False, rows(110, 100)), 'volume_not_rising')

    def test_unconfirmed_pick_is_not_bought(self):
        case = MultiSymbolTests('test_entry_records_its_exit_plan')
        case.setUp()
        try:
            falling = {'코스피': {'regime': '하락장', 'today_tone': '약세', 'today_change_pct': -1.5},
                       '코스닥': {'regime': '하락장', 'today_tone': '약세', 'today_change_pct': -1.8}}
            with patch.object(p, 'market_view', return_value=falling):
                status, submit, _, events = case.run_cycle(case.root, case.db, {})  # Jev picks KQ150 (long)
            self.assertEqual(status, 'wait')
            submit.assert_not_called()
            self.assertEqual([c for c in events.call_args_list if c.args[1] == 'v2_entry_gate'][-1].kwargs['reason'], 'direction_not_confirmed')
        finally:
            case.tearDown()



class ReentryTests(unittest.TestCase):
    """User 2026-11-13: after any exit today, the strength route re-buys only above that exit."""
    def test_floors_come_from_losing_or_trailing_stop_exits_today_only(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            db = p.connect_journal(root / 'orders.sqlite3', p.account_hash(ALT))
            t = lambda m: (NOW + timedelta(minutes=m)).isoformat()
            fills = [('a1', '000001', 'BUY', 10, 100, t(0)), ('a2', '000001', 'SELL', 10, 103, t(30)),   # win by trailing stop
                     ('b1', '000002', 'BUY', 10, 100, t(0)), ('b2', '000002', 'SELL', 10, 98, t(10)),    # loss
                     ('c1', '000003', 'BUY', 10, 100, t(0)), ('c2', '000003', 'SELL', 10, 102, t(10)),   # take profit
                     ('d1', '000004', 'BUY', 10, 100, (NOW - timedelta(days=1)).isoformat()),
                     ('d2', '000004', 'SELL', 10, 90, (NOW - timedelta(days=1, minutes=-5)).isoformat())]  # yesterday's loss
            db.executemany('INSERT INTO fills VALUES (?,?,?,?,?,?)', fills)
            db.commit()
            from trading_ledger import ingest
            with (root / 'events.jsonl').open('w') as f:
                for symbol, reason, minute in (('000001', 'trailing_stop', 29), ('000003', 'take_profit_all', 9)):
                    f.write(json.dumps({'time': t(minute), 'event': 'v2_risk_exit', 'symbol': symbol, 'reason': reason}) + '\n')
            ingest(root / 'events.jsonl', root / 'trading-ledger.sqlite3')
            # every exit closed today, take profit included (11/13 review #2); yesterday's does not count
            self.assertEqual(p.reentry_floors(db, root, '20261106'), {'000001': 103, '000002': 98, '000003': 102})

    def test_entry_context_measures_the_price_against_todays_high_and_open(self):
        self.assertEqual(p.entry_context({'stck_hgpr': '10000', 'stck_oprc': '9000'}, 9500), {'high_gap_pct': -5.0, 'open_gap_pct': 5.56})
        self.assertEqual(p.entry_context({'stck_hgpr': None}, 9500), {})

    def test_entry_context_records_nh_vi_and_warning_fields(self):
        quote = {'vi_antc_sdpr': 191400, 'vi_antc_mxpr': 211000, 'vi_antc_llam': 172200, 'short_over_code': '0',
                 'mrkt_alrm_code': '2', 'vi_type_code': None}
        self.assertEqual(p.entry_context(quote, 191400), {'vi_up_gap_pct': 10.24, 'vi_down_gap_pct': -10.03,
                                                          'overheat_code': '0', 'alarm_code': '2'})
        self.assertEqual(p.entry_context({'vi_antc_mxpr': 0, 'short_over_code': 'X', 'vi_type_code': '1'}, 100), {'vi_halt': '1'})

    def test_entry_timing_records_studio_dragon_prices_from_current_completed_bars(self):
        at = NOW.replace(hour=11, minute=29, second=36)
        rows = [{'bsop_time': '112700', 'stck_prpr': '33700', 'stck_lwpr': '33600'},
                {'bsop_time': '112600', 'stck_lwpr': '33500'}, {'bsop_time': '112500', 'stck_lwpr': '33450'},
                {'bsop_time': '112400', 'stck_lwpr': '33550'}, {'bsop_time': '112300', 'stck_lwpr': '33600'},
                {'bsop_time': '112200', 'stck_lwpr': '33600'},
                {'bsop_time': '155000', 'stck_lwpr': '21000', 'bsop_date': '20261105'}]
        data = p.entry_timing_context({'hoga_bsop_hour': '11:29:39'}, 33900, rows, at)
        self.assertEqual((data['decision_price'], data['order_ask'], data['recent_low']), (33700, 33900, 33450))
        self.assertEqual((data['ask_vs_decision_pct'], data['ask_vs_recent_low_pct']), (0.593, 1.345))
        self.assertNotIn('recent_low', p.entry_timing_context({}, 33900, rows[:3], at))

    def test_strength_skips_a_floored_stock_until_it_trades_above_its_last_exit(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'watchlist.json').write_text(json.dumps({'time': NOW.isoformat(), 'probabilities': {'000001': 0.3}}))
            movers = {'000001': {'price': 100, 'change_pct': 0.0}}
            rows = lambda price: [{'stck_prpr': str(price)}] + [{'stck_prpr': '103'}] * 5
            self.assertEqual(p.strength_candidate(root, movers, {'000001': rows(105)}, NOW, floors={'000001': 106}),
                             (None, 'reentry_below_last_exit'))
            self.assertEqual(p.strength_candidate(root, movers, {'000001': rows(107)}, NOW, floors={'000001': 106})[0], '000001')
            self.assertEqual(p.strength_candidate(root, movers, {'000001': rows(105)}, NOW, floors=None)[0], '000001')  # switch off


class DayClockTests(unittest.TestCase):
    """User 2026-11-13: the cutoff and flatten are settings; the session runs 20 minutes past the flatten (max 15:18)."""
    def test_refresh_clock_moves_the_session_close(self):
        from strategy_settings import DEFAULTS, write_settings
        with tempfile.TemporaryDirectory() as d:
            write_settings(Path(d), {**DEFAULTS, 'entry_cutoff_hhmm': 1455, 'flatten_hhmm': 1500})
            saved = (p.ENTRY_CUTOFF, p.FLATTEN_AT, p.SESSION_CLOSE)
            try:
                p.refresh_clock(Path(d))
                self.assertEqual((p.ENTRY_CUTOFF, p.FLATTEN_AT, p.SESSION_CLOSE), ('14:55', '15:00', '15:18'))
                late = datetime(2026, 11, 6, 15, 10, tzinfo=ZoneInfo('Asia/Seoul'))
                self.assertTrue(p.in_session(late))
                self.assertEqual(p.session_close_at(late).strftime('%H:%M'), '15:18')
                p.refresh_clock(Path(d) / 'missing')  # no settings file: the old day
                self.assertEqual(p.SESSION_CLOSE, '15:00')
                self.assertFalse(p.in_session(late))
            finally:
                p.ENTRY_CUTOFF, p.FLATTEN_AT, p.SESSION_CLOSE = saved

    def test_rising_check_switch_lets_a_flat_strength_pick_through(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'watchlist.json').write_text(json.dumps({'time': NOW.isoformat(), 'probabilities': {'000001': 0.9}}))
            movers = {'000001': {'price': 100, 'change_pct': 0.0}}
            flat = [{'stck_prpr': '105'}] + [{'stck_prpr': '106'}] * 5
            self.assertEqual(p.strength_candidate(root, movers, {'000001': flat}, NOW), (None, 'trend_not_rising'))
            with patch.object(p, 'RISING_CHECK', False):
                self.assertEqual(p.strength_candidate(root, movers, {'000001': flat}, NOW)[0], '000001')


class CooldownTests(unittest.TestCase):
    def test_streak_uses_only_fills_known_at_the_requested_time(self):
        with tempfile.TemporaryDirectory() as d:
            db = p.connect_journal(Path(d) / 'orders.sqlite3', p.account_hash(ALT))
            for i in range(3):
                buy = (NOW + timedelta(minutes=10 * i)).isoformat()
                sell = (NOW + timedelta(minutes=10 * i + 5)).isoformat()
                db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', (f'b{i}', '069500', 'BUY', 1, 100, buy))
                db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', (f's{i}', '069500', 'SELL', 1, 99, sell))
            db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('b9', '069500', 'BUY', 1, 100, (NOW + timedelta(minutes=40)).isoformat()))
            db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('s9', '069500', 'SELL', 1, 101, (NOW + timedelta(minutes=45)).isoformat()))
            db.commit()
            self.assertIsNone(p.losing_streak(db, '20261106', NOW + timedelta(minutes=24)))
            self.assertEqual(p.losing_streak(db, '20261106', NOW + timedelta(minutes=25)), 'losing_streak_cooldown')
            self.assertEqual(p.losing_streak(db, '20261106', NOW + timedelta(minutes=39)), 'losing_streak_cooldown')
            self.assertIsNone(p.losing_streak(db, '20261106', NOW + timedelta(minutes=45)))
            db.close()

    def test_streak_rejects_unverifiable_time_and_keeps_prior_day_entry(self):
        with tempfile.TemporaryDirectory() as d:
            db = p.connect_journal(Path(d) / 'orders.sqlite3', p.account_hash(ALT))
            db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('carry', '069500', 'BUY', 1, 100, (NOW - timedelta(days=1)).isoformat()))
            for i in range(3):
                if i:
                    db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', (f'b{i}', '069500', 'BUY', 1, 100, (NOW + timedelta(minutes=i * 10)).isoformat()))
                db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', (f's{i}', '069500', 'SELL', 1, 99, (NOW + timedelta(minutes=i * 10 + 5)).isoformat()))
            db.commit()
            self.assertEqual(p.losing_streak(db, '20261106', NOW + timedelta(minutes=25)), 'losing_streak_cooldown')
            self.assertEqual(p.losing_streak(db, '20261106', NOW.replace(tzinfo=None)), 'losing_streak_history_unavailable')
            db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('bad', '005930', 'BUY', 1, 100, '2026-11-06T12:26:00'))
            db.commit()
            self.assertEqual(p.losing_streak(db, '20261106', NOW + timedelta(minutes=27)), 'losing_streak_history_unavailable')
            db.close()

    def test_streak_blocks_unverifiable_orphan_sell(self):
        with tempfile.TemporaryDirectory() as d:
            db = p.connect_journal(Path(d) / 'orders.sqlite3', p.account_hash(ALT))
            db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('s', '069500', 'SELL', 1, 100, NOW.isoformat()))
            db.commit()
            self.assertEqual(p.losing_streak(db, '20261106', NOW), 'losing_streak_history_unavailable')
            db.close()

    def test_three_losing_round_trips_pause_new_entries_for_thirty_minutes(self):
        with tempfile.TemporaryDirectory() as d:
            db = p.connect_journal(Path(d)/'orders.sqlite3', p.account_hash(ALT))
            t = lambda m: (NOW + timedelta(minutes=m)).isoformat()
            for i, (buy, sell) in enumerate(((100, 99), (100, 98), (100, 99))):
                db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', (f'b{i}', '069500', 'BUY', 1, buy, t(10 * i)))
                db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', (f's{i}', '069500', 'SELL', 1, sell, t(10 * i + 5)))
            db.commit()
            self.assertEqual(p.losing_streak(db, '20261106', NOW + timedelta(minutes=30)), 'losing_streak_cooldown')
            self.assertIsNone(p.losing_streak(db, '20261106', NOW + timedelta(minutes=60)))   # 35 minutes after the last loss
            db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('b9', '069500', 'BUY', 1, 100, t(40)))
            db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('s9', '069500', 'SELL', 1, 101, t(45)))
            db.commit()
            self.assertIsNone(p.losing_streak(db, '20261106', NOW + timedelta(minutes=50)))   # a win breaks the streak
            db.close()

    def test_stock_brief_keeps_only_compact_facts(self):
        fresh = datetime.now(p.KST).isoformat()
        brief = p.stock_brief({'price': 150, 'facts': {'PER': 3.2, '52주 최고': 200, '52주 최저': 100, '시가': 1},
                               'investor_daily_net_qty': {'last_close_day': {'foreign': 5}},
                               'news': [{'title': 'a' * 100, 'published': fresh}, {'title': 'b', 'published': fresh}, {'title': 'c', 'published': fresh},
                                        {'title': 'no time'}]})
        self.assertEqual(brief['position_in_52w_range_pct'], 50.0)
        self.assertNotIn('시가', brief)
        self.assertEqual([len(h['title']) for h in brief['headlines']], [80, 1])  # recent ones only, with their age (2026-11-18)


if __name__=='__main__':unittest.main()


class PaperPagesContinuationTests(unittest.TestCase):
    def test_paper_continuation_code_reads_the_next_page(self):
        # 11/13 13:09: the day's order history passed one page and NH paper answered XA109; the runner crashed
        pages = iter([
            {'status': 'ok', 'data': {'rsp_cd': 'XA109', 'rsp_msg': '모의투자 조회가 계속 됩니다. 다음 버튼을 누르세요',
                                      'Output_0': [{'n': 1}], '_nh_pagination': {'complete': False, 'cts': 'c1', 'cts_flag': 'Y'}}},
            {'status': 'ok', 'data': {'rsp_cd': '00000', 'rsp_msg': '모의투자 마지막 자료입니다',
                                      'Output_0': [{'n': 2}], '_nh_pagination': {'complete': True}}}])

        class Adapter:
            def call_tool(self, name, params):
                return next(pages)
        data = p.paper_pages(Adapter(), 'krstockInquiryDailyOrderExecution', {}, 'Output_0', sleep=lambda _: None)
        self.assertEqual(data['Output_0'], [{'n': 1}, {'n': 2}])

    def test_a_second_page_waits_for_nhs_one_request_a_second(self):
        # 11-19 from 11:00: the balance grew past 10 rows (today's traded stocks stay listed at 0 shares), so it took a
        # second page; asked at once, NH refused it (IGW42903, measured: ~1 request a second per query), and the retry
        # after 1 s made every cycle 3 calls and ~1 s late. The second page now waits; two pages cost two calls.
        clock = {'t': 0.0}
        class NH:
            def __init__(self):
                self.starts = []
            def call_tool(self, name, params):
                now = clock['t']
                refused = bool(self.starts) and now - self.starts[-1] < 1.0
                self.starts.append(now)
                clock['t'] += 0.27  # one response
                if refused:
                    return {'status': 'error', 'error': {'category': 'rate_limit', 'code': 'IGW42903'}}
                if 'cts' not in params:
                    return {'status': 'ok', 'data': {'rsp_cd': 'XA109', 'rsp_msg': '모의투자 조회가 계속 됩니다. 다음 버튼을 누르세요',
                                                     'Output_1': [{'n': 1}], '_nh_pagination': {'complete': False, 'cts': 'c1', 'cts_flag': 'Y'}}}
                return {'status': 'ok', 'data': {'rsp_cd': '00000', 'rsp_msg': '모의투자 마지막 자료입니다',
                                                 'Output_1': [{'n': 2}], '_nh_pagination': {'complete': True}}}
        nh = NH()
        sleep = lambda seconds: clock.__setitem__('t', clock['t'] + seconds)
        data = p.paper_pages(nh, 'krstockInquiryBalance', {}, 'Output_1', clock=lambda: clock['t'], sleep=sleep)
        self.assertEqual(data['Output_1'], [{'n': 1}, {'n': 2}])
        self.assertEqual(len(nh.starts), 2)                       # no refused call, no retry
        self.assertGreaterEqual(nh.starts[1] - nh.starts[0], 1.0)

    def test_continuation_code_on_a_complete_page_is_contradictory(self):
        class Adapter:
            def call_tool(self, name, params):
                return {'status': 'ok', 'data': {'rsp_cd': 'XA109', 'rsp_msg': '모의투자 조회가 계속 됩니다. 다음 버튼을 누르세요',
                                                 'Output_0': [], '_nh_pagination': {'complete': True}}}
        with self.assertRaisesRegex(ValueError, 'contradictory'):
            p.paper_pages(Adapter(), 'krstockInquiryDailyOrderExecution', {}, 'Output_0')


class GuardNoteTests(unittest.TestCase):
    def test_guard_note_keeps_plain_refusal_text_only(self):
        self.assertEqual(p.guard_note(ValueError('Swing cumulative buy limit exceeded')), {'guard_detail': 'Swing cumulative buy limit exceeded'})
        self.assertEqual(p.guard_note(ValueError('account 12345678')), {})


def test_regime_stop_tightens_ordinary_positions_only():
    from decimal import Decimal
    from nh_paper_isolated import regime_stop_levels, exit_reason
    plan = {'profile': 'defensive', 'stop_pct': 2, 'half_pct': 1, 'all_pct': 2}
    view = {'코스피': {'regime': '하락장'}, '코스닥': {'regime': '상승장'}}
    levels = regime_stop_levels(plan, view, '코스피')
    assert levels['stop'] == Decimal('0.99')
    assert exit_reason(10000, 9900, levels, False) == 'stop_loss'
    assert regime_stop_levels(plan, view, '코스닥')['stop'] == Decimal('0.98')
    assert regime_stop_levels(plan, None, '코스피')['stop'] == Decimal('0.98')
    # direction-aware (user 2026-11-17): an inverse is tightened in 상승장·횡보장, not in the 하락장 that favors it
    assert regime_stop_levels(plan, {'코스피': {'regime': '하락장'}}, '코스피', '114800')['stop'] == Decimal('0.98')
    assert regime_stop_levels(plan, {'코스피': {'regime': '상승장'}}, '코스피', '114800')['stop'] == Decimal('0.99')
    assert regime_stop_levels(plan, {'코스피': {'regime': '상승장'}}, '코스피', '069500')['stop'] == Decimal('0.98')
    assert regime_stop_levels(plan, {'코스피': {'regime': '하락장'}}, '코스피', '069500')['stop'] == Decimal('0.99')
    assert regime_stop_levels({**plan, 'stop_pct': 0.5}, view, '코스피')['stop'] == Decimal('0.995')
    assert regime_stop_levels({**plan, 'profile': 'oversold'}, view, '코스피')['stop'] == Decimal('0.98')
    assert regime_stop_levels({**plan, 'profile': 'ipo', 'trend_half_pct': 1, 'trend_trail_pct': 1}, view, '코스피')['stop'] == Decimal('0.98')


class JournalLedgerFailureTests(unittest.TestCase):
    def test_a_ledger_failure_keeps_the_line_and_never_stops_the_runner(self):
        # 11/17 11:46: an event name the ledger did not know raised inside journal_event and stopped the vibe runner
        import json
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            p.journal_event(root, 'not_a_known_event', symbol='328130')  # must not raise
            lines = (root / 'events.jsonl').read_text().splitlines()
            self.assertEqual(json.loads(lines[-1])['event'], 'not_a_known_event')
            note = json.loads((root / 'ledger-ingest-error.json').read_text())
            self.assertEqual((note['event'], note['error_type']), ('not_a_known_event', 'ValueError'))
            self.assertEqual(oct((root / 'ledger-ingest-error.json').stat().st_mode & 0o777), '0o600')
            with patch('trading_ledger.ingest', return_value=0):
                p.journal_event(root, 'stopped', reason='session or STOP')  # a later clean ingest clears the marker
            self.assertFalse((root / 'ledger-ingest-error.json').exists())
            self.assertEqual(len((root / 'events.jsonl').read_text().splitlines()), 2)


class IpoDailyCountTests(unittest.TestCase):
    def test_counts_todays_new_listing_buys_and_is_safe_without_a_ledger(self):
        import sqlite3 as _sq
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertEqual(p.ipo_entries_today(root, NOW), 0)  # no ledger yet
            with _sq.connect(root / 'trading-ledger.sqlite3') as db:
                db.execute('CREATE TABLE events_v2 (digest TEXT, event TEXT, time TEXT, data TEXT)')
                day = NOW.strftime('%Y-%m-%d')
                db.executemany('INSERT INTO events_v2 VALUES (?,?,?,?)', [
                    ('a', 'submitted', day + 'T09:30:00+09:00', '{"source": "new_listing"}'),
                    ('b', 'submitted', day + 'T10:00:00+09:00', '{"source": "jev"}'),
                    ('c', 'submitted', '2000-01-01T09:30:00+09:00', '{"source": "new_listing"}')])
            self.assertEqual(p.ipo_entries_today(root, NOW), 1)

    def test_a_stalled_ledger_counts_as_full(self):
        # 4th review F2: while ingest fails the ledger stops counting, so the daily caps must not reopen
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            import sqlite3 as _sq
            with _sq.connect(root / 'trading-ledger.sqlite3') as db:
                db.execute('CREATE TABLE events_v2 (digest TEXT, event TEXT, time TEXT, data TEXT)')
            (root / 'ledger-ingest-error.json').write_text('{"error_type": "ValueError"}')
            self.assertIsNone(p.entries_today(root, NOW, 'watchlist_strength'))
            self.assertEqual(p.ipo_entries_today(root, NOW), p.IPO_DAILY_MAX)



class InverseGuardTests(unittest.TestCase):
    """User 2026-11-18: inverse ETFs lost money in a sideways/weak tape (8 round trips 11/11~11/18: the one crash day
    made +154,553, the rest about -60,000). Buy them only when every view agrees the market is falling, and take a
    small profit at once unless it is a crash day."""
    falling = {'코스피': {'regime': '하락장', 'today_change_pct': -1.2, 'program_net_100m_krw': {'전체': -800.0}},
               'index_intraday': {'코스피': {'change_30m_pct': -0.3}}, 'global_last_daily_change_pct': {'나스닥100': -0.7}}

    def view(self, **index):
        return {**self.falling, '코스피': {**self.falling['코스피'], **index}}

    def test_every_view_must_agree_before_an_inverse_buy(self):
        self.assertIsNone(p.inverse_gate(self.falling, '코스피'))
        self.assertEqual(p.inverse_gate(None, '코스피'), 'inverse_market_unknown')
        self.assertEqual(p.inverse_gate(self.view(regime='횡보장'), '코스피'), 'inverse_regime_not_falling')
        self.assertEqual(p.inverse_gate(self.view(today_change_pct=-0.3), '코스피'), 'inverse_index_not_falling')
        self.assertEqual(p.inverse_gate(self.view(today_change_pct=None), '코스피'), 'inverse_market_unknown')
        self.assertEqual(p.inverse_gate({**self.falling, 'index_intraday': {'코스피': {'change_30m_pct': 0.2}}}, '코스피'),
                         'inverse_index_rebounding')
        self.assertEqual(p.inverse_gate(self.view(program_net_100m_krw={'전체': 120.0}), '코스피'), 'inverse_flows_not_selling')
        self.assertEqual(p.inverse_gate({**self.falling, 'global_last_daily_change_pct': {'나스닥100': 0.4}}, '코스피'),
                         'inverse_us_not_falling')
        self.assertEqual(p.inverse_gate({**self.falling, 'global_last_daily_change_pct': {}}, '코스피'), 'inverse_market_unknown')

    def test_missing_inverse_inputs_are_unknown_and_flat_readings_do_not_agree(self):
        # r3 E5: missing data was journaled as a market verdict (rebounding, not selling, US not falling); 0 passed
        for missing in ({**self.falling, 'index_intraday': {}}, self.view(program_net_100m_krw={}),
                        {k: v for k, v in self.falling.items() if k != 'global_last_daily_change_pct'}, self.view(regime=None),
                        self.view(regime='판단 불가')):
            with self.subTest(missing=missing):
                self.assertEqual(p.inverse_gate(missing, '코스피'), 'inverse_market_unknown')
        self.assertEqual(p.inverse_gate(self.view(program_net_100m_krw={'전체': 0.0}), '코스피'), 'inverse_flows_not_selling')
        self.assertEqual(p.inverse_gate({**self.falling, 'global_last_daily_change_pct': {'나스닥100': 0.0}}, '코스피'),
                         'inverse_us_not_falling')
        self.assertEqual(p.inverse_gate({**self.falling, 'index_intraday': {'코스피': {'change_30m_pct': 0.0}}}, '코스피'),
                         'inverse_index_not_falling_now')  # flat: not a rebound, but not falling now either (user 2026-11-20)

    def test_entry_block_refuses_an_inverse_pick_the_views_do_not_back(self):
        rows = [{'vol': '200'}] * 5 + [{'vol': '100'}] * 5
        with tempfile.TemporaryDirectory() as d, patch.object(p, 'market_view', return_value=self.view(regime='횡보장')):
            self.assertEqual(p.entry_block(Path(d), '114800', {}, 'jev', rows, {}, True), 'inverse_regime_not_falling')
            with patch.object(p, 'entry_confirmation', return_value=None):
                self.assertIsNone(p.entry_block(Path(d), '069500', {}, 'jev', rows, {}, False))  # longs untouched

    def test_an_inverse_bought_off_a_crash_day_takes_its_small_profit_whole(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(p, 'market_view', return_value=self.falling), patch.object(p, 'daily_stats', return_value=None):
                profile, target, basis = p.entry_plan(Path(d), None, '114800', True)
            self.assertEqual((profile, target, basis['index_change_pct']), ('inverse_quick', p.INVERSE['quick_tp_pct'], -1.2))
            plan = p.new_plan('114800', profile, target=target, basis=basis)
            levels = p.plan_levels(plan)
            self.assertEqual(p.exit_reason(1000, 1003, levels, False), 'take_profit_all')  # +0.3%: everything
            crash = self.view(today_change_pct=-2.0, today_tone='약세')
            with patch.object(p, 'market_view', return_value=crash), patch.object(p, 'daily_stats', return_value=None):
                self.assertNotEqual(p.entry_plan(Path(d), None, '114800', True)[0], 'inverse_quick')  # a crash day lets it run
        self.assertIn('inverse_quick', p.OWN_EXIT_PROFILES)  # never upgraded to a trend plan while held


# User 2026-11-19: a position carried overnight (종가베팅·이월·스윙) is judged at the 09:00 open, not after six bars.
def _opening_response(code, today_times, prior_times):
    rows = [{'bsop_date': '20261106', 'bsop_time': t, 'stck_oprc': '1000', 'stck_hgpr': '1002', 'stck_lwpr': '998',
             'stck_prpr': '1001', 'vol': '10'} for t in today_times]
    rows += [{'bsop_date': '20261105', 'bsop_time': t, 'stck_oprc': '990', 'stck_hgpr': '991', 'stck_lwpr': '989',
              'stck_prpr': '990', 'vol': '10'} for t in prior_times]
    return {'rsp_cd': '00000', 'Output_0': {'iem_cd': code}, 'Output_1': rows}


def test_opening_bars_put_todays_completed_bars_before_the_previous_session():
    import pytest
    at = datetime(2026, 11, 6, 9, 3, 10, tzinfo=ZoneInfo('Asia/Seoul'))
    data = _opening_response('069500', ['090200', '090100', '090000'], ['151900', '151800'])
    rows = p.opening_bars(data, '069500', at)
    # the current (09:03) and previous (09:02) minutes are not settled, as in valid_bars
    assert [(r['bsop_date'], r['bsop_time']) for r in rows] == [('20261106', '090100'), ('20261106', '090000'),
                                                                 ('20261105', '151900'), ('20261105', '151800')]
    with pytest.raises(ValueError):
        p.opening_bars({'rsp_cd': '00000', 'Output_0': {'iem_cd': '069500'}, 'Output_1': []}, '069500', at)


def test_opening_bars_count_as_fresh_only_inside_the_opening_window():
    rows = p.opening_bars(_opening_response('069500', [], ['151900']), '069500',
                          datetime(2026, 11, 6, 9, 0, 20, tzinfo=ZoneInfo('Asia/Seoul')))
    bars = {name: rows for name in p.ETF7_SYMBOLS}
    with patch('session_times.SESSION_OPEN', '09:00'):
        assert p.fresh_etf7_bars(bars, datetime(2026, 11, 6, 9, 0, 20, tzinfo=ZoneInfo('Asia/Seoul')))
        assert p.fresh_etf7_bars(bars, datetime(2026, 11, 6, 9, 7, 59, tzinfo=ZoneInfo('Asia/Seoul')))
        assert not p.fresh_etf7_bars(bars, datetime(2026, 11, 6, 9, 8, tzinfo=ZoneInfo('Asia/Seoul')))


def test_stock_holding_reviews_receive_symbol_news_flows_and_turnover(tmp_path, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    code = '090710'
    candle = {'bsop_time': '115800', 'stck_prpr': '5280'}
    quote = {'iem_cd': code, 'iem_nm': '#휴림로봇', 'acml_vol': 566367, 'acml_tr_pbmn': 2666,
             'hoga_bsop_hour': '11:59:58'}
    flows = {'today_provisional': {'date': '20261106', 'foreign': 120, 'institution': 45, 'individual': -165}}
    stock_news = [{'title': '휴림로봇 계약', 'published': (NOW - timedelta(minutes=12)).isoformat()},
                  {'title': '오래된 기사', 'published': (NOW - timedelta(hours=2)).isoformat()}]
    market_news = [{'title': '시장 약세', 'published': NOW.isoformat()}]
    jev = {'model': 'jev-1.14.0', 'chart_sha256': 'a' * 64, 'response_time': NOW.isoformat(),
           'holding': {'type': 'choice', 'choice': 'HOLD', 'confidence': 0.8}}
    monkeypatch.setattr(p, '_INVESTOR_CACHE', {})
    with patch.object(p, 'datetime', Clock), patch.object(p, 'minute_bars', return_value={'status': 'ok'}), \
         patch.object(p, 'valid_bars', return_value=[candle]), patch('etf_news.headlines7', return_value=market_news), \
         patch('watchlist.fresh_stock_news', return_value=stock_news) as news, \
         patch('watchlist.investor_days', return_value=flows) as investor, \
         patch.object(p, 'investor_flows', return_value=None), \
         patch.object(p, 'holding_decision7', return_value=('HOLD', 0.9, {'response_time': NOW.isoformat()})) as decision, \
         patch.object(p, 'jev_holding7', return_value=jev) as judge, patch.object(p, 'journal_event'):
        for _ in range(2):
            assert p.holding_action(None, tmp_path, code, 5260, 5280, etf7=True, position_quote=quote) is False
        context = judge.call_args.kwargs['extra_state']['held_stock_context']
        assert decision.call_args.kwargs['stock_context'] == context
        assert news.call_args.args[:2] == (code, '휴림로봇')
        assert context['headlines'] == [{'title': '휴림로봇 계약', 'age_min': 12}]
        assert context['investor_daily_net_qty'] == flows
        assert (context['session_volume_shares'], context['session_turnover_million_krw']) == (566367, 2666)
        investor.assert_called_once_with(None, code, '20261106')


def test_optional_holding_stock_data_marks_provider_failures_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(p, '_INVESTOR_CACHE', {})
    with patch('watchlist.fresh_stock_news', side_effect=TimeoutError), \
         patch('watchlist.investor_days', side_effect=TimeoutError):
        context = p.held_stock_context(None, tmp_path, '090710', {'iem_cd': 'different', 'acml_vol': 1}, NOW)
    assert context['headlines_status'] == 'unavailable'
    assert context['investor_daily_net_qty'] is None and context['session_volume_shares'] is None


def test_carried_position_gets_a_jev_review_in_the_first_minute():
    at = datetime(2026, 11, 6, 9, 0, 30, tzinfo=ZoneInfo('Asia/Seoul'))
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return at
    calls = []
    class Reader:
        def call_tool(self, tool, params):
            calls.append(params.get('input', {}).get('today_cls_code'))
            code = params.get('input', {}).get('iem_cd') or params.get('stock_code')
            if tool == 'get_stock_price':
                return {'status': 'ok', 'data': {'rsp_cd': '00000', 'Output_0': {'iem_cd': code, 'stck_oprc': '1010',
                                                                                  'stck_prpr': '1005', 'prdy_ctrt': '1.5'}}}
            return {'status': 'ok', 'data': _opening_response(code, [], ['151900', '151800'])}
    jev = {'model': 'jev-1.14.0', 'chart_sha256': 'a' * 64, 'response_time': at.isoformat(),
           'holding': {'type': 'choice', 'choice': 'SELL', 'confidence': 0.75}}
    with patch('session_times.SESSION_OPEN', '09:00'), patch.object(p, 'datetime', Clock), \
         patch('etf_news.headlines7', return_value=[{'title': 'market', 'published': at.isoformat()}]), \
         patch.object(p, 'holding_decision7', return_value=('SELL', 0.9, {'response_time': at.isoformat()})), \
         patch.object(p, 'investor_flows', return_value=None), \
         patch.object(p, 'jev_holding7', return_value=jev) as judge, patch.object(p, 'journal_event'):
        context = {}
        assert p.holding_action(Reader(), Path('/tmp'), '229200', 100, 100, etf7=True, signal_context=context)
    assert '0' in calls  # bars with the previous session
    state = judge.call_args.kwargs['extra_state']
    assert state['opening_review']['open'] == '1010' and state['opening_review']['change_pct'] == '1.5'
    assert context['opening'] is True


# Review of the 11-19 opening review (findings 1, 4, 5)
def _opening_reader(calls):
    class Reader:
        def call_tool(self, tool, params):
            calls.append(params.get('input', {}).get('today_cls_code'))
            code = params.get('input', {}).get('iem_cd') or params.get('stock_code')
            if tool == 'get_stock_price':
                return {'status': 'ok', 'data': {'rsp_cd': '00000', 'Output_0': {'iem_cd': code, 'stck_oprc': '1010'}}}
            return {'status': 'ok', 'data': _opening_response(code, ['090000'], ['151900', '151800'])}
    return Reader()


def test_opening_review_logs_only_todays_bars_as_market_bars():
    """The ledger keeps bsop_time only: yesterday's 15:19 row became today's 15:19 bar (position_paths, journal charts)."""
    at = datetime(2026, 11, 6, 9, 2, 30, tzinfo=ZoneInfo('Asia/Seoul'))
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return at
    jev = {'model': 'jev-1.14.0', 'chart_sha256': 'a' * 64, 'response_time': at.isoformat(),
           'holding': {'type': 'choice', 'choice': 'HOLD', 'confidence': 0.6}}
    with patch('session_times.SESSION_OPEN', '09:00'), patch.object(p, 'datetime', Clock), \
         patch('etf_news.headlines7', return_value=[{'title': 'm', 'published': at.isoformat()}]), \
         patch.object(p, 'holding_decision7', return_value=('HOLD', 0.9, {'response_time': at.isoformat()})), \
         patch.object(p, 'investor_flows', return_value=None), patch.object(p, 'jev_holding7', return_value=jev), \
         patch.object(p, 'journal_event') as events:
        p.holding_action(_opening_reader([]), Path('/tmp/none'), '229200', 100, 100, etf7=True)
    logged = next(c.kwargs for c in events.call_args_list if c.args[1] == 'v2_holding_decision')
    assert all(r['bsop_date'] == '20261106' for rows in logged['market_bars'].values() for r in rows)
    assert all(r['bsop_date'] == '20261105' for rows in logged['opening_prior_bars'].values() for r in rows)


def test_a_position_bought_today_is_not_given_the_opening_review(tmp_path):
    at = datetime(2026, 11, 6, 9, 2, 30, tzinfo=ZoneInfo('Asia/Seoul'))
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return at
    plan = {**p.new_plan('229200'), 'opened': '2026-11-06T09:00:40+09:00'}
    (tmp_path / 'exit-plans.json').write_text(json.dumps({'229200': plan}))
    calls = []
    with patch('session_times.SESSION_OPEN', '09:00'), patch.object(p, 'datetime', Clock), \
         patch.object(p, 'jev_holding7') as judge, patch.object(p, 'journal_event'):
        assert p.holding_action(_opening_reader(calls), tmp_path, '229200', 100, 100, etf7=True) is False
    judge.assert_not_called()
    assert '0' not in calls


def test_entry_reads_reuse_the_opening_prior_bars_of_the_same_cycle():
    """The prior read (today_cls_code 0) also holds today's bars; reading again doubled NH calls at the open."""
    cache, calls = {}, []
    class Reader:
        def call_tool(self, tool, params):
            calls.append(params['input']['today_cls_code'])
            return {'status': 'ok', 'data': {}}
    p.minute_bars(Reader(), '069500', cache, prior=True)
    p.minute_bars(Reader(), '069500', cache)
    assert calls == ['0']


def test_each_new_listing_keeps_its_own_verdict_and_base_and_a_price_verdict_reads_no_bars(tmp_path):
    """11-18: 에이엘로봇 (newest, offer 17,600) was quoted every cycle but the record kept only the last listing's
    reason, so its own verdict was lost; and a listing below its base still cost a minute-bar read."""
    import new_listings
    now = datetime(2026, 11, 18, 10, 0, tzinfo=p.KST)
    listings = [{'code': '471800', 'name': '에이엘로봇', 'listed': '20261118'}, {'code': '493260', 'name': '옵토닉스', 'listed': '20261117'}]
    quotes = {'471800': {'stck_prpr': 15000, 'stck_prdy_clpr': 17600, 'stck_oprc': 15100, 'stck_hgpr': 15500},
              '493260': {'stck_prpr': 100, 'stck_prdy_clpr': 90, 'stck_oprc': 95, 'stck_hgpr': 120}}
    bar_reads = []

    class Read:
        def call_tool(self, tool, params):
            code = params['stock_code']
            listed = next(item['listed'] for item in listings if item['code'] == code)
            return {'data': {'Output_0': {'iem_cd': code, 'stck_lstn_date': listed, **quotes[code]}}}
    with patch.object(new_listings, 'recent', return_value=listings), patch.object(p, 'IPO_START', '00:00'), \
         patch.object(p, 'IPO_DAYS', 2), patch.object(p, 'exit_plans', return_value={}), \
         patch.object(p, 'minute_bars', side_effect=lambda read, code: bar_reads.append(code) or {'data': {}}):
        assert p.ipo_candidate(Read(), tmp_path, {}, now) == (None, 'off_high', None)
        p.ipo_candidate(Read(), tmp_path, {}, now)  # same verdicts next cycle: not journaled again
        quotes['471800'].update(stck_prpr=17700, stck_oprc=15100, stck_hgpr=19000)  # above its offer, 6.8% off the high
        p.ipo_candidate(Read(), tmp_path, {}, now)
    seen = [e for e in map(json.loads, (tmp_path / 'events.jsonl').read_text().splitlines()) if e['event'] == 'ipo_seen']
    # every path that scans listings records them (the slot path too, 11-19 review): each listing's own verdict and base,
    # once per change of verdict a day
    assert [(e['symbol'], e['why'], e['price'], e['base']) for e in seen] == [
        ('471800', 'below_base', 15000, 17600), ('493260', 'off_high', 100, 90), ('471800', 'off_high', 17700, 17600)]
    assert bar_reads == []  # all settled by the quote alone


def test_entries_read_the_opening_bars_at_the_bell_and_six_bars_after():  # user 2026-11-20: 9:00~9:05 is the peak
    import pytest
    at = datetime(2026, 11, 6, 9, 0, 20, tzinfo=ZoneInfo('Asia/Seoul'))
    data = _opening_response('069500', [], ['151900', '151800'])
    rows = p.entry_rows(data, '069500', at, True)  # no settled bar of today yet: the previous session's
    assert [(r['bsop_date'], r['bsop_time']) for r in rows] == [('20261105', '151900'), ('20261105', '151800')]
    with pytest.raises(ValueError, match='insufficient bars'):
        p.entry_rows(data, '069500', at, False)  # outside the opening window entries still need six bars of today
    today, prior = p.opening_split({'K200': rows}, at)
    assert (today, list(prior)) == ({}, ['K200'])  # nothing of yesterday is logged as today's market_bars


def test_a_stock_opening_entry_charts_todays_nxt_premarket_instead_of_yesterday():  # user 2026-11-20 '키움으로 바꾸기'
    at = datetime(2026, 11, 6, 9, 0, 20, tzinfo=ZoneInfo('Asia/Seoul'))
    data = _opening_response('005930', [], ['151900', '151800'])
    pre = [{'bsop_date': '20261106', 'market': 'NXT', 'bsop_time': t, 'stck_oprc': '100', 'stck_hgpr': '101',
            'stck_lwpr': '99', 'stck_prpr': '100', 'vol': '5'} for t in ('084900', '084800')]
    rows = p.entry_rows(data, '005930', at, True, lambda code: pre)
    assert [r['bsop_time'] for r in rows] == ['084900', '084800']  # no previous-session rows
    assert p.entry_rows(data, '005930', at, True, lambda code: [])[0]['bsop_date'] == '20261105'  # Kiwoom failed: yesterday's
    today, prior = p.opening_split({'005930': rows}, at)
    assert (today, list(prior)) == ({}, ['005930'])  # NXT rows are never logged as today's KRX market_bars
    assert p.opening_series(rows, at)
    with patch('kiwoom_data.nxt_premarket_bars', side_effect=RuntimeError('no key')):
        p._NXT_PRE.clear()
        assert p.nxt_premarket('005930', at) == []


def test_opening_volume_check_compares_todays_minutes_with_the_rows_before():  # user 2026-11-20: strength at the bell
    def row(t, vol, day='20261106', market=None):
        r = {'bsop_date': day, 'bsop_time': t, 'stck_oprc': '1', 'stck_hgpr': '1', 'stck_lwpr': '1', 'stck_prpr': '1', 'vol': str(vol)}
        return {**r, 'market': market} if market else r
    nxt = [row(f'0849{i:02d}', 100, market='NXT') for i in range(5)]
    with patch.object(p, 'ENTRY_VOLUME_RATIO', 1.2):
        assert p.volume_check(nxt) is None  # 09:00: no settled KRX minute yet
        assert p.volume_check([row('090100', 500), row('090000', 300)] + nxt) is None  # 400 a minute vs 100
        assert p.volume_check([row('090000', 110)] + nxt) == 'volume_not_rising'
        assert p.volume_check([row('090000', 500)] + [row('1519', 1000, day='20261105')] * 5) == 'volume_not_rising'


def test_a_strength_pick_rides_its_own_trend_from_plus_one_percent(tmp_path):  # user 2026-11-20 '모의에 적용'
    import trading_ledger
    p.save_exit_plan(tmp_path, '180640', 'c1', 'defensive', source='watchlist_strength')
    p.save_exit_plan(tmp_path, '005930', 'c2', 'defensive', source='jev')
    plans = p.exit_plans(tmp_path)
    assert plans['180640']['entry_source'] == 'watchlist_strength' and 'entry_source' not in plans['005930']
    event = {'time': '2026-11-20T09:19:00+09:00', 'event': 'exit_plan', 'symbol': '180640', 'reason': 'strength_trend',
             'profile': 'trend', 'stop_pct': 2.0, 'half_pct': 1.0, 'all_pct': 2.0, 'trend_half_pct': 2.0, 'trend_trail_pct': 1.5}
    assert trading_ledger.sanitize(event)['reason'] == 'strength_trend'  # the ledger takes the new reason


def test_a_kiwoom_priced_ranking_row_never_replaces_the_nh_figures_jev_reads():
    # 2026-11-20 NH call plan P2 (option 1): only NH figures reach Jev
    details = {'price': 110, 'change_pct': 10.0, 'volume': 5, 'value_mil': 1}
    kiwoom = {'price': 109, 'change_pct': 9.0, 'volume': 4, 'value_mil': 1, 'source': 'kiwoom'}
    assert p.with_latest_figures(details, kiwoom, '11:10') == details
    nh = {'price': 111, 'change_pct': 11.0, 'volume': 6, 'value_mil': 2}
    assert p.with_latest_figures(details, nh, '11:10')['price'] == 111


def test_inverse_buys_only_early_in_a_fall_and_takes_a_moderate_profit():  # user 2026-11-20 (10:41 −4.4% late chase)
    def view(change, recent):
        return {'코스피': {'regime': '하락장', 'today_change_pct': change, 'program_net_100m_krw': {'전체': -1000}},
                'index_intraday': {'코스피': {'change_30m_pct': recent}}, 'global_last_daily_change_pct': {'나스닥100': -1.0}}
    assert p.inverse_gate(view(-1.2, -0.5), '코스피') is None
    assert p.inverse_gate(view(-4.38, -0.3), '코스피') == 'inverse_too_late'  # the fall is already done
    assert p.inverse_gate(view(-4.38, -0.6), '코스피') is None  # still falling fast: the strong-signal exception
    assert p.inverse_late(view(-4.38, -0.6), '코스피') and not p.inverse_late(view(-1.2, -0.6), '코스피')
    assert p.inverse_tape('INV', [], None, 0.7, 'jev', late=True) == 'inverse_low_confidence'  # 0.8 needed past −3%
    assert p.inverse_gate(view(-1.2, -0.1), '코스피') == 'inverse_index_not_falling_now'
    assert p.inverse_tape('INV', [], None, 0.5, 'jev') == 'inverse_low_confidence'
    plan = p.new_plan('114800', 'inverse_crash')
    assert (plan['half_pct'], plan['all_pct'], plan['stop_pct']) == (1.0, 1.0, 1.0) and 'inverse_crash' in p.OWN_EXIT_PROFILES
    up = {'index_intraday': {'코스피': {'change_30m_pct': 0.4}}}
    assert p.inverse_exit(plan, 1374.5, 1368, up, '코스피') == 'inverse_market_rebound'  # the index turns: out
    held = {**plan, 'peak': 1385}
    assert p.inverse_exit(held, 1374.5, 1370, {}, '코스피') == 'inverse_protect'  # was +0.76%, back at cost: out
    assert p.inverse_exit({**plan, 'peak': 1378}, 1374.5, 1370, {}, '코스피') is None
    assert p.inverse_exit({**plan, 'profile': 'trend'}, 1374.5, 1368, up, '코스피') is None
    import trading_ledger
    for reason in ('inverse_too_late', 'inverse_chase', 'inverse_low_confidence', 'inverse_index_not_falling_now', 'inverse_market_rebound', 'inverse_protect'):
        assert trading_ledger.sanitize({'time': '2026-11-20T11:00:00+09:00', 'event': 'v2_entry_gate', 'reason': reason})['reason'] == reason


def test_an_inverse_is_a_small_side_bet():  # user 2026-11-20: "인버스는 소액으로 들어가고 다른 종목도 함께"
    assert p.INVERSE['size_pct'] == 50.0


def test_a_gated_inverse_pick_hands_the_turn_to_the_strength_path():  # user 2026-11-20 12:27: Jev picked INV 36 times
    import inspect
    src = inspect.getsource(p.cycle)
    assert "candidate, reason = None, 'jev_wait'" in src  # the inverse gate refusal counts as a Jev WAIT for the fallback


def test_the_strength_reason_names_the_top_ranked_stock(tmp_path):  # 11-20: 180640 resting was reported as 005930's range
    import json as _j
    now = datetime(2026, 11, 20, 13, 0, tzinfo=ZoneInfo('Asia/Seoul'))
    (tmp_path / 'watchlist.json').write_text(_j.dumps({'time': now.isoformat(), 'probabilities': {'180640': 0.54, '005930': 0.43}}))
    up = [{'stck_prpr': str(240000 - i * 100)} for i in range(10)]
    down = [{'stck_prpr': str(184000 - i * 100)} for i in range(10)]
    movers = {'180640': {'price': 240000, 'change_pct': 10.0}, '005930': {'price': 184000, 'change_pct': -6.0}}
    with patch.object(p, 'cooling', side_effect=lambda code, now: code == '180640'):
        assert p.strength_candidate(tmp_path, movers, {'180640': up, '005930': down}, now) == (None, 'pick_cooldown')


def test_a_low_confidence_inverse_pick_also_hands_the_turn_on():  # 11-20 13:22: INV 0.55 kept 한진칼 from being looked at
    import inspect
    with patch.object(p, 'market_view', return_value={}), patch.object(p, 'symbol_index', return_value='코스피'), \
         patch.object(p, 'inverse_gate', return_value=None):
        assert p.inverse_refusal(Path('.'), '114800', [], 'INV', None, 0.55, 'jev') == 'inverse_low_confidence'
    with patch.object(p, 'market_view', return_value={}), patch.object(p, 'symbol_index', return_value='코스피'), \
         patch.object(p, 'inverse_gate', return_value='inverse_too_late'):
        assert p.inverse_refusal(Path('.'), '114800', [], 'INV', None, 0.9, 'jev') == 'inverse_too_late'
    src = inspect.getsource(p.cycle)
    assert "(gate := inverse_refusal(" in src and "candidate, reason = None, 'jev_wait'" in src


def test_every_ranked_strength_stock_gets_its_own_reason(tmp_path):  # user 11-20 "한진칼 왜 안 샀는지도 기록"
    import json as _j
    now = datetime(2026, 11, 20, 13, 30, tzinfo=ZoneInfo('Asia/Seoul'))
    (tmp_path / 'watchlist.json').write_text(_j.dumps({'time': now.isoformat(),
                                                      'probabilities': {'005930': 0.43, '180640': 0.25, '128940': 0.11}}))
    flat = [{'stck_prpr': str(240000)} for _ in range(10)]  # 한진칼 up 10% but not rising this minute
    down = [{'stck_prpr': str(184000 - i * 100)} for i in range(10)]
    movers = {'180640': {'price': 240000, 'change_pct': 10.0}, '005930': {'price': 184000, 'change_pct': -6.0}}
    with patch.object(p, 'cooling', return_value=False), patch.object(p, 'RISING_CHECK', True):
        assert p.strength_candidate(tmp_path, movers, {'180640': flat, '005930': down}, now)[0] is None
    whys = p.strength_whys()
    assert whys['005930'] == 'strength_change_out_of_range' and whys['180640'] == 'trend_not_rising'
    assert whys['128940'] == 'not_in_movers'
    assert p.strength_whys('180640', 'careful_reentry_chase')['180640'] == 'careful_reentry_chase'


class KiwoomBarsTests(unittest.TestCase):
    """User 2026-11-24 'NH 1분봉 우회': a chart NH does not give is read from Kiwoom (KRX); orders still need an NH quote."""
    class Reader:
        def __init__(self, status='error'): self.status, self.codes = status, []
        def call_tool(self, tool, params):
            self.codes.append(params['input']['iem_cd'])
            return {'status': self.status, 'data': {}}

    def setUp(self):
        import kiwoom_data
        self.rows = [{'bsop_date': '20261124', 'bsop_time': f'10{38 - n:02d}00', 'stck_oprc': '1000', 'stck_hgpr': '1010',
                      'stck_lwpr': '990', 'stck_prpr': '1005', 'vol': '100'} for n in range(10)]
        self.asked, self.clock = [], [1000.0]
        for target, name, value in ((p, 'KIWOOM_BARS', True), (p, '_NH_BARS_REST', [0.0]), (p.time, 'sleep', lambda s: None),
                                    (p.time, 'monotonic', lambda: self.clock[0]), (kiwoom_data, 'configured', lambda: True),
                                    (kiwoom_data, 'krx_minute_rows', lambda code, prior=False, now=None: self.asked.append((code, prior)) or self.rows)):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_failed_chart_comes_from_kiwoom_in_nhs_shape(self):
        read, cache = self.Reader(), {}
        got = p.minute_bars(read, '069500', cache)
        self.assertEqual((got['status'], got['source'], len(read.codes), self.asked), ('ok', 'kiwoom', 2, [('069500', False)]))
        now = datetime(2026, 11, 24, 10, 40, 41, tzinfo=p.KST)
        self.assertEqual(p.valid_bars(got['data'], '069500', now)[0]['bsop_time'], '103800')
        self.assertEqual(len(p.opening_bars(got['data'], '069500', now)), 10)
        self.assertIs(p.minute_bars(read, '069500', cache), got)  # kept for the cycle like an NH chart

    def test_the_other_charts_do_not_wait_for_nh_again_until_it_had_a_minute(self):
        read = self.Reader()
        for code in ('069500', '114800', '229200'):
            self.assertEqual(p.minute_bars(read, code, {})['source'], 'kiwoom')
        self.assertEqual(read.codes, ['069500', '069500'])  # seven charts at two 8 s starts each would cost two minutes
        self.clock[0] += 61
        read.status = 'ok'
        self.assertNotIn('source', p.minute_bars(read, '114800', {}))

    def test_kiwoom_failing_too_is_the_failure_it_was(self):
        import kiwoom_data
        with patch.object(kiwoom_data, 'krx_minute_rows', side_effect=OSError):
            self.assertEqual(p.minute_bars(self.Reader(), '069500', {})['status'], 'error')
        with patch.object(kiwoom_data, 'krx_minute_rows', return_value=[]):
            self.assertEqual(p.minute_bars(self.Reader(), '069500', {})['status'], 'error')
        with patch.object(kiwoom_data, 'configured', return_value=False):
            self.assertEqual(p.minute_bars(self.Reader(), '069500', {})['status'], 'error')
        p._NH_BARS_REST[0] = self.clock[0] + 60
        with patch.object(kiwoom_data, 'krx_minute_rows', side_effect=OSError):
            self.assertNotIn('source', p.minute_bars(self.Reader('ok'), '069500', {}))  # NH is asked after all

    def test_nh_answering_never_asks_kiwoom(self):
        p.minute_bars(self.Reader('ok'), '069500', {}, prior=True)
        self.assertEqual(self.asked, [])

    def test_the_record_names_the_charts_that_came_from_kiwoom(self):
        cache = {}
        p.minute_bars(self.Reader('ok'), '069500', cache)
        self.assertEqual(p.bars_source(cache), 'NH KRX 1-minute completed bars')
        p.minute_bars(self.Reader(), '114800', cache, prior=True)
        p.minute_bars(self.Reader(), '005930', cache)
        label = p.bars_source(cache)
        self.assertEqual(label, 'NH KRX 1-minute completed bars (Kiwoom KRX: 005930, 114800)')
        from trading_ledger import MARKET_SOURCE
        self.assertTrue(MARKET_SOURCE.fullmatch(label) and MARKET_SOURCE.fullmatch(p.bars_source({})))
        self.assertFalse(MARKET_SOURCE.fullmatch(label + ' sk-secret') or MARKET_SOURCE.fullmatch('Kiwoom'))

    def test_tests_and_imports_do_not_reach_kiwoom_by_default(self):
        import os
        import subprocess
        import sys
        out = subprocess.run([sys.executable, '-c', 'import pytest, nh_paper_isolated as p; print(p.KIWOOM_BARS)'], capture_output=True, text=True,
                             cwd=Path(p.__file__).parent, env={**os.environ, 'PYTHONPATH': f"{Path(p.__file__).parent}:{Path(p.__file__).parents[1] / 'agent'}"})
        self.assertEqual(out.stdout.strip(), 'False')
