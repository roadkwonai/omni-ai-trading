from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import importlib.util
import numpy as np
import pandas as pd
import pytest

path = Path(__file__).resolve().parents[2] / 'scripts/kr_swing_v5_forward.py'
spec = importlib.util.spec_from_file_location('kr_swing_v5_forward', path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_incomplete_pagination_cannot_be_treated_as_empty():
    with pytest.raises(ValueError, match='pagination'):
        mod.complete_rows({'Output_1': []}, 'balance')
    with pytest.raises(ValueError, match='pagination'):
        mod.complete_rows({'Output_0': []}, 'orders')
    assert mod.complete_rows({'_pagination_complete': True, 'Output_1': []}, 'balance') == []


def test_broker_error_reports_only_safe_error_type():
    class Broker:
        def call_tool(self, tool, args):
            return {'status':'error', 'error_type':'TimeoutError', 'error':'secret account token'}
    with pytest.raises(ValueError, match='TimeoutError') as exc:
        mod.read_response(Broker(), 'call_api', {})
    assert 'secret' not in str(exc.value)
    class UnsafeBroker:
        def call_tool(self, tool, args):
            return {'status':'error', 'error_type':'secret account token'}
    with pytest.raises(ValueError) as exc:
        mod.read_response(UnsafeBroker(), 'call_api', {})
    assert 'secret' not in str(exc.value)


def test_mark_uses_actual_valuation_day_not_signal_day(tmp_path):
    db = mod.connect_state(tmp_path / 'state.sqlite3', 'account-a')
    now = datetime(2026, 11, 6, 10, tzinfo=ZoneInfo('Asia/Seoul'))
    mod.record_mark(db, now, 30_000_000, 0)
    assert db.execute('SELECT day, observed_at FROM marks').fetchone() == ('2026-11-06', now.isoformat())
    assert db.execute('SELECT day FROM cost_estimates').fetchone()[0] == '2026-11-06'
    db.close()


def test_bars_reject_stale_missing_and_bad_prices():
    now = datetime(2026, 11, 6, 10, tzinfo=ZoneInfo('Asia/Seoul'))
    index = pd.bdate_range(end='2026-11-05', periods=125)
    frame = pd.DataFrame({'open': 1., 'high': 2., 'low': 1., 'close': 1., 'volume': 10.}, index=index)
    assert mod.validate_bars({'x': frame}, ['x'], now, holidays=set()) == '2026-11-05'
    for data in ({'x':frame.iloc[:-1]}, {}, {'x':frame.assign(close=np.nan)}):
        with pytest.raises(ValueError):
            mod.validate_bars(data, ['x'], now, holidays=set())


def test_journal_is_bound_to_account_and_private(tmp_path):
    db = mod.connect_state(tmp_path / 'state.sqlite3', 'account-a')
    db.execute("INSERT INTO intents VALUES ('20261106','005930','BUY',1,100,'uncertain',NULL)")
    db.commit(); db.close()
    db = mod.connect_state(tmp_path / 'state.sqlite3', 'account-a')
    assert db.execute("SELECT status FROM intents").fetchone()[0] == 'uncertain'
    db.close()
    with pytest.raises(ValueError, match='account or strategy'):
        mod.connect_state(tmp_path / 'state.sqlite3', 'account-b')
    assert (tmp_path / 'state.sqlite3').stat().st_mode & 0o777 == 0o600


def test_pagination_accumulates_and_rejects_unknown_or_repeated():
    class Adapter:
        def __init__(self, pages): self.pages = iter(pages); self.calls = []
        def call_tool(self, tool, args):
            self.calls.append(args)
            return {'status':'ok', 'data':next(self.pages)}
    def page(rows, complete, cts=None, flag=None):
        return {'rsp_cd':'00000','Output_0':rows, '_nh_pagination':{'complete':complete,'cts':cts,'cts_flag':flag}}
    adapter = Adapter([page([1],False,'a','Y'),page([2],True)])
    result = mod.read_paginated(adapter,'read',{},'Output_0')
    assert result['Output_0'] == [1,2] and result['_pagination_complete']
    assert adapter.calls[1]['cts'] == 'a'
    for pages in ([page([],None)], [page([],False,'a','Y'),page([],False,'a','Y')]):
        with pytest.raises(ValueError): mod.read_paginated(Adapter(pages),'read',{},'Output_0')


def test_reconciliation_persists_exact_terminal_fill_and_never_repeats(tmp_path):
    db = mod.connect_state(tmp_path / 'state.sqlite3', 'account-a')
    db.execute("INSERT INTO intents VALUES ('20261106','005930.KS','BUY',1,100,'acknowledged',17)")
    db.commit()
    class Broker:
        def call_tool(self, tool, args):
            row={'itg_orr_no':17,'iem_cd':'005930','sby_dit_cd_nm':'현금매수','tot_cns_qty':1,'ny_cns_qty':0,'can_qty':0,'cns_avg_uit_pr':100}
            return {'status':'ok','data':{'rsp_cd':'00000','Output_0':[row], '_nh_pagination':{'complete':True}}}
    assert mod.reconcile_order(db,Broker(),'account-a',('20261106','005930.KS','BUY',1,17)) == 1
    assert db.execute('SELECT status FROM intents').fetchone()[0] == 'terminal'
    assert db.execute('SELECT quantity,price FROM fills').fetchone() == (1,100.0)
    assert mod.reconcile_order(db,Broker(),'account-a',('20261106','005930.KS','BUY',1,17)) == 1
    assert db.execute('SELECT COUNT(*) FROM fills').fetchone()[0] == 1
    with pytest.raises(ValueError, match='unknown'):
        mod.reconcile_order(db,Broker(),'account-a',('20261106','005930.KS','BUY',1,None))
    db.close()


def test_partial_fill_cancels_once_and_reconciles_terminal(tmp_path):
    db = mod.connect_state(tmp_path / 'state.sqlite3', 'account-a')
    db.execute("INSERT INTO intents VALUES ('20261106','005930.KS','BUY',3,100,'acknowledged',17)")
    db.commit()
    class Broker:
        def __init__(self): self.calls = []; self.cancelled = False
        def call_tool(self, tool, args):
            self.calls.append(args['operationId'])
            if args['operationId'] == 'krstockOrderCancel':
                self.cancelled = True
                return {'status':'ok','data':{'Output_0':{'mkt_orr_no':18}}}
            row = {'itg_orr_no':17,'iem_cd':'005930','sby_dit_cd_nm':'현금매수','tot_cns_qty':1,
                   'ny_cns_qty':0 if self.cancelled else 2,'can_qty':2 if self.cancelled else 0,'cns_avg_uit_pr':100}
            return {'status':'ok','data':{'rsp_cd':'00000','Output_0':[row], '_nh_pagination':{'complete':True}}}
    broker = Broker()
    assert mod.reconcile_order(db, broker, 'account-a', ('20261106','005930.KS','BUY',3,17)) == 1
    assert db.execute('SELECT status FROM intents').fetchone()[0] == 'terminal'
    assert db.execute('SELECT quantity,price FROM fills').fetchone() == (1,100.0)
    assert broker.calls.count('krstockOrderCancel') == 1
    assert mod.reconcile_order(db, broker, 'account-a', ('20261106','005930.KS','BUY',3,17)) == 1
    assert broker.calls.count('krstockOrderCancel') == 1
    db.close()


def test_cancel_timeout_never_retries_and_terminal_later_recovers(tmp_path):
    db = mod.connect_state(tmp_path / 'state.sqlite3', 'account-a')
    db.execute("INSERT INTO intents VALUES ('20261106','005930.KS','BUY',2,100,'acknowledged',17)")
    db.commit()
    class Broker:
        def __init__(self): self.cancels = 0; self.terminal = False
        def call_tool(self, tool, args):
            if args['operationId'] == 'krstockOrderCancel':
                self.cancels += 1
                return {'status':'error','error':'timeout'}
            row={'itg_orr_no':17,'iem_cd':'005930','sby_dit_cd_nm':'현금매수','tot_cns_qty':1,
                 'ny_cns_qty':0 if self.terminal else 1,'can_qty':1 if self.terminal else 0,'cns_avg_uit_pr':100}
            return {'status':'ok','data':{'rsp_cd':'00000','Output_0':[row], '_nh_pagination':{'complete':True}}}
    broker=Broker(); intent=('20261106','005930.KS','BUY',2,17)
    with pytest.raises(ValueError): mod.reconcile_order(db,broker,'account-a',intent)
    db.close(); db=mod.connect_state(tmp_path/'state.sqlite3','account-a')
    with pytest.raises(ValueError): mod.reconcile_order(db,broker,'account-a',intent)
    assert broker.cancels == 1
    broker.terminal=True
    assert mod.reconcile_order(db,broker,'account-a',intent)==1
    assert broker.cancels == 1
    db.close()


def test_unknown_submission_only_recovers_exact_guard_acknowledgement(tmp_path, monkeypatch):
    import hashlib, json
    from src.tools.nhplug_order_guard import NHOrderGuard
    monkeypatch.setenv('VIBE_TRADING_HOME', str(tmp_path))
    db = mod.connect_state(tmp_path / 'forward.sqlite3', 'account-a')
    db.execute("INSERT INTO intents VALUES ('20261106','005930.KS','BUY',1,100,'uncertain',NULL)")
    db.commit()
    class Config:
        args = ['nhplug_mcp.py','--mode','paper','--account','account-a']
    class Broker:
        server_config = Config()
    broker = Broker()
    with pytest.raises(ValueError, match='unknown'):
        mod.recover_order_number(db, broker, 'account-a', ('20261106','005930.KS','BUY',1,None))
    guard = NHOrderGuard(Config.args)
    fields = {'act_no':'account-a','iem_cd':'005930','orr_qty':1,'orr_pr':100,'nmn_pr_tp_cd':'01',
              'orr_cnd_dit_cd':'00','ssl_nmn_pr_dit_cd':'00','rmt_mkt_cd':'KRX','sor_mkt_sli_yn':'N'}
    fingerprint = hashlib.sha256(json.dumps([guard.scope,'20261106','krstockOrderCashBuy',fields],sort_keys=True,allow_nan=False).encode()).hexdigest()
    with guard.database() as gdb:
        gdb.execute('INSERT INTO intents VALUES (?,?,?,?,?,?,?,?)',
                    (fingerprint,guard.scope,'20261106','krstockOrderCashBuy','005930',1,17,'acknowledged'))
    assert mod.recover_order_number(db,broker,'account-a',('20261106','005930.KS','BUY',1,None)) == 17
    assert db.execute('SELECT order_number,status FROM intents').fetchone() == (17,'acknowledged')
    db.close()


def test_reused_daily_order_number_never_silently_drops_fill(tmp_path):
    db = mod.connect_state(tmp_path / 'state.sqlite3', 'account-a')
    db.execute("INSERT INTO fills VALUES ('20261105','005930.KS','BUY',1,100,17)")
    db.execute("INSERT INTO intents VALUES ('20261106','005930.KS','BUY',1,100,'acknowledged',17)")
    db.commit()
    class Broker:
        def call_tool(self, tool, args):
            row={'itg_orr_no':17,'iem_cd':'005930','sby_dit_cd_nm':'현금매수','tot_cns_qty':1,
                 'ny_cns_qty':0,'can_qty':0,'cns_avg_uit_pr':100}
            return {'status':'ok','data':{'rsp_cd':'00000','Output_0':[row], '_nh_pagination':{'complete':True}}}
    assert mod.reconcile_order(db,Broker(),'account-a',('20261106','005930.KS','BUY',1,17)) == 1
    assert db.execute('SELECT COUNT(*) FROM fills').fetchone()[0] == 2
    assert db.execute("SELECT status FROM intents").fetchone()[0] == 'terminal'
    db.execute("UPDATE fills SET price=101 WHERE day='20261106' AND order_number=17")
    db.commit()
    with pytest.raises(ValueError, match='conflict'):
        mod.reconcile_order(db,Broker(),'account-a',('20261106','005930.KS','BUY',1,17))
    db.close()


def test_live_quote_rejects_stale_and_wrong_market():
    now = datetime(2026,11,6,10,0,tzinfo=ZoneInfo('Asia/Seoul'))
    def quote(t='09:59:59', status='1'):
        return {'rsp_cd':'00000', 'Output_0':{'iem_cd':'005930','market_status':status,'hoga_bsop_hour':t}}
    assert mod.fresh_quote(quote(),'005930',now)['iem_cd'] == '005930'
    for item in (quote(t='09:57:59'),quote(status='X'),quote(t='10:00:06')):
        with pytest.raises(ValueError): mod.fresh_quote(item,'005930',now)


def test_revised_prior_history_blocks_future_orders(tmp_path):
    ix = pd.bdate_range(end='2026-11-05', periods=125)
    frame = pd.DataFrame({'open':1.,'high':2.,'low':1.,'close':1.,'volume':10.},index=ix)
    db = mod.connect_state(tmp_path / 'state.sqlite3','account-a')
    mod.save_or_check_history(db,{'x':frame},'2026-11-05')
    revised = frame.copy(); revised.loc[ix[-1], 'close'] = 1.5
    with pytest.raises(ValueError, match='revised'):
        mod.save_or_check_history(db,{'x':revised},'2026-11-05')
    db.close()


def test_float_noise_preserves_original_snapshot_but_signal_flip_is_blocked(tmp_path):
    ix = pd.bdate_range(end='2026-11-05', periods=125)
    frame = pd.DataFrame({'open':100.,'high':101.,'low':99.,'close':100.,'volume':10.},index=ix)
    db = mod.connect_state(tmp_path / 'state.sqlite3','account-a')
    mod.save_or_check_history(db,{'x':frame},'2026-11-05')
    original = db.execute('SELECT payload FROM data_prefix').fetchone()[0]
    noisy = frame.copy(); noisy[['open','high','low','close']] *= 1.0000001
    mod.save_or_check_history(db,{'x':noisy},'2026-11-05')
    assert db.execute('SELECT payload FROM data_prefix').fetchone()[0] == original
    assert db.execute('SELECT payload FROM data_prefix_archive').fetchone()[0] == original
    flip = frame.copy(); flip.loc[ix[-1], 'close'] += .00001
    with pytest.raises(ValueError, match='signal changed'):
        mod.save_or_check_history(db,{'x':flip},'2026-11-05')
    vol = frame.copy(); vol.loc[ix[-1], 'volume'] += 1
    with pytest.raises(ValueError, match='revised'):
        mod.save_or_check_history(db,{'x':vol},'2026-11-05')
    db.close()


def test_recorded_signal_must_remain_identical(tmp_path):
    ix = pd.bdate_range(end='2026-11-05', periods=140)
    close = pd.Series(np.arange(1.,141.),index=ix)
    frame = pd.DataFrame({'open':close,'high':close,'low':close,'close':close,'volume':10.})
    db = mod.connect_state(tmp_path/'state.sqlite3','account-a')
    db.execute('INSERT INTO observations VALUES (?,?,?,?,?)',('2026-11-05','now','{"x": 0.025}','{}','observed'))
    db.commit()
    mod.check_recorded_signal(db,{'x':frame})
    frame.loc[ix[-1], 'close'] = 1
    with pytest.raises(ValueError, match='signal changed'):
        mod.check_recorded_signal(db,{'x':frame})
    db.close()


def test_postfill_mark_reconciles_complete_broker_balance_and_estimated_cost(tmp_path):
    db = mod.connect_state(tmp_path / 'state.sqlite3', 'account-a')
    db.execute("INSERT INTO fills VALUES ('20261106','005930.KS','BUY',2,100,17)")
    db.commit()
    now = datetime(2026, 11, 6, 10, tzinfo=ZoneInfo('Asia/Seoul'))
    class Broker:
        def __init__(self, qty): self.qty = qty
        def call_tool(self, tool, args):
            return {'status':'ok','data':{'rsp_cd':'00000','Output_1':[{'iem_cd':'005930','rsdl_qty':self.qty}], '_nh_pagination':{'complete':True}}}
    class Read:
        def call_tool(self, tool, args):
            return {'status':'ok','data':{'rsp_cd':'00000','Output_0':{'iem_cd':'005930','market_status':'1','hoga_bsop_hour':'09:59:59','stck_prpr':110}}}
    mod.refresh_mark(db, Broker(2), Read(), 'account-a', ['005930.KS'], now)
    assert db.execute('SELECT cash_gross,holdings_value,equity_gross FROM marks').fetchone() == (29999800,220,30000020)
    assert db.execute('SELECT total,equity_estimated FROM cost_estimates').fetchone() == (0.4,30000019.6)
    db.execute("INSERT INTO fills VALUES ('20261106','005930.KS','SELL',2,110,18)")
    db.commit()
    mod.refresh_mark(db, Broker(0), Read(), 'account-a', ['005930.KS'], now)
    assert db.execute('SELECT holdings_value,equity_gross FROM marks').fetchone() == (0,30000020)
    with pytest.raises(ValueError, match='positions differ'):
        mod.refresh_mark(db, Broker(1), Read(), 'account-a', ['005930.KS'], now)
    db.close()


def test_intent_and_mark_invalidation_commit_together(tmp_path):
    db = mod.connect_state(tmp_path / 'state.sqlite3', 'account-a')
    now = datetime(2026, 11, 6, 10, tzinfo=ZoneInfo('Asia/Seoul'))
    mod.record_mark(db, now, 30_000_000, 0)
    mod.record_intent(db, '20261106', '005930.KS', 'BUY', 1, 100)
    assert db.execute('SELECT status FROM intents').fetchone() == ('uncertain',)
    assert db.execute('SELECT COUNT(*) FROM marks').fetchone()[0] == 0
    assert db.execute('SELECT COUNT(*) FROM cost_estimates').fetchone()[0] == 0
    db.close()


def test_snapshot_loader_rejects_tampered_manifest(tmp_path):
    import hashlib, json
    import sys
    sys.path.insert(0, str(path.parent))
    from kr_swing_v5_daily_data import CODES
    idx = pd.bdate_range(end='2026-11-05', periods=125)
    frame = pd.DataFrame({'open':1., 'high':2., 'low':1., 'close':1., 'volume':10.}, index=idx)
    hashes = {}
    for code in CODES:
        file = tmp_path / f'{code}.csv'
        frame.to_csv(file, index_label='date')
        hashes[code] = hashlib.sha256(file.read_bytes()).hexdigest()
    (tmp_path / 'manifest.json').write_text(json.dumps({'sha256':hashes,'last_session':'2026-11-05','rows':125}))
    loader = mod.SnapshotLoader(tmp_path)
    assert len(loader.fetch([CODES[0]], '2026-01-01', '2026-11-05')[CODES[0]]) == 125
    with (tmp_path / f'{CODES[0]}.csv').open('a') as f:
        f.write('\n')
    with pytest.raises(ValueError, match='hash mismatch'):
        mod.SnapshotLoader(tmp_path)


def test_new_order_waits_for_fill_before_cancelling(tmp_path, monkeypatch):
    db = mod.connect_state(tmp_path / 'state.sqlite3', 'account-a')
    db.execute("INSERT INTO intents VALUES ('20261106','005930.KS','BUY',1,100,'acknowledged',17)")
    db.commit()
    monkeypatch.setattr(mod.time, 'sleep', lambda _: None)
    class Broker:
        def __init__(self): self.reads = 0; self.cancels = 0
        def call_tool(self, tool, args):
            if args['operationId'] == 'krstockOrderCancel':
                self.cancels += 1
                raise AssertionError('filled order should not be cancelled')
            self.reads += 1
            row = {'itg_orr_no':17,'iem_cd':'005930','sby_dit_cd_nm':'현금매수',
                   'tot_cns_qty':int(self.reads > 1), 'ny_cns_qty':int(self.reads == 1),
                   'can_qty':0,'cns_avg_uit_pr':100}
            return {'status':'ok','data':{'rsp_cd':'00000','Output_0':[row], '_nh_pagination':{'complete':True}}}
    broker = Broker()
    assert mod.reconcile_order(db,broker,'account-a',('20261106','005930.KS','BUY',1,17),wait_before_cancel=3) == 1
    assert broker.reads == 2 and broker.cancels == 0
    db.close()


def test_legacy_fills_migration_preserves_rows_across_restart(tmp_path):
    import sqlite3
    file = tmp_path / 'legacy.sqlite3'
    with sqlite3.connect(file) as db:
        db.execute('CREATE TABLE fills (day TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL, quantity INTEGER NOT NULL, price REAL NOT NULL, order_number INTEGER NOT NULL UNIQUE)')
        db.execute("INSERT INTO fills VALUES ('20261105','005930.KS','BUY',1,100,17)")
    db = mod.connect_state(file, 'account-a')
    assert db.execute('SELECT * FROM fills').fetchall() == [('20261105','005930.KS','BUY',1,100.0,17)]
    db.execute("INSERT INTO fills VALUES ('20261106','005930.KS','SELL',1,110,17)")
    db.commit(); db.close()
    db = mod.connect_state(file, 'account-a')
    assert db.execute('SELECT COUNT(*) FROM fills').fetchone()[0] == 2
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO fills VALUES ('20261106','000660.KS','BUY',1,120,17)")
    db.close()


def test_holiday_and_year_boundary_use_official_calendar():
    from datetime import date
    now = datetime(2027, 1, 4, 10, tzinfo=ZoneInfo('Asia/Seoul'))
    index = pd.bdate_range(end='2026-12-30', periods=125)
    frame = pd.DataFrame({'open':1.,'high':2.,'low':1.,'close':1.,'volume':10.},index=index)
    assert mod.validate_bars({'x':frame}, ['x'], now, holidays={date(2026,12,31), date(2027,1,1)}) == '2026-12-30'
    with pytest.raises(ValueError, match='stale'):
        mod.validate_bars({'x':frame}, ['x'], now, holidays=set())


def test_legacy_fills_with_custom_index_is_not_mutated(tmp_path):
    import sqlite3
    file = tmp_path / 'legacy.sqlite3'
    with sqlite3.connect(file) as db:
        db.execute('CREATE TABLE fills (day TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL, quantity INTEGER NOT NULL, price REAL NOT NULL, order_number INTEGER NOT NULL UNIQUE)')
        db.execute('CREATE INDEX custom_fill_symbol ON fills(symbol)')
        db.execute("INSERT INTO fills VALUES ('20261105','005930.KS','BUY',1,100,17)")
    with pytest.raises(ValueError, match='unexpected fills schema'):
        mod.connect_state(file, 'account-a')
    with sqlite3.connect(file) as db:
        assert db.execute('SELECT COUNT(*) FROM fills').fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='custom_fill_symbol'").fetchone()[0] == 1
