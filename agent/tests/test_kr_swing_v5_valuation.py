import importlib.util
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import pytest

path = Path(__file__).resolve().parents[2] / 'scripts/kr_swing_v5_valuation.py'
spec = importlib.util.spec_from_file_location('kr_swing_v5_valuation', path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

@pytest.fixture
def fixture(tmp_path):
    data = tmp_path/'data'; data.mkdir()
    sha={}
    import hashlib
    for code in mod.CODES:
        f=data/f'{code}.csv';f.write_text('date,open,high,low,close,volume\n2026-11-09,100,101,99,100,10\n')
        sha[code]=hashlib.sha256(f.read_bytes()).hexdigest()
    (data/'manifest.json').write_text(json.dumps({'last_session':'2026-11-09','rows':1,'sha256':sha}))
    db=sqlite3.connect(tmp_path/'journal.sqlite3')
    db.executescript('''CREATE TABLE intents (day TEXT,symbol TEXT,side TEXT,qty INTEGER,price INTEGER,status TEXT,order_number INTEGER);
    CREATE TABLE fills (day TEXT,symbol TEXT,side TEXT,quantity INTEGER,price REAL,order_number INTEGER);
    CREATE TABLE marks (day TEXT PRIMARY KEY,observed_at TEXT,cash_gross REAL,holdings_value REAL,equity_gross REAL);
    CREATE TABLE cost_estimates (day TEXT PRIMARY KEY,assumption TEXT,total REAL,equity_estimated REAL);''')
    db.commit()
    prices={c:{'ok':True,'source':'KRX Open API','date':'2026-11-09','symbol':c,'adjustment':'raw','currency':'KRW','records':[{'BAS_DD':'20261109','ISU_CD':c[:6],'TDD_CLSPRC':'100'}]} for c in mod.CODES}
    balance={'rsp_cd':'00000','_pagination_complete':True,'Output_1':[{'iem_cd':'005930','rsdl_qty':0}]}
    orders={}
    return db,data,prices,balance,orders

def test_same_basis_complete_records_net_and_evidence(fixture):
    db,data,prices,balance,orders=fixture
    result=mod.record_close(db, data, date(2026,11,9), datetime(2026,11,10,9,tzinfo=ZoneInfo('Asia/Seoul')), prices,balance,orders)
    assert result['equity_estimated']==pytest.approx(30000000)
    assert db.execute('SELECT cash_gross, holdings_value FROM marks').fetchone()==(30000000,0)
    assert db.execute('SELECT source FROM verified_closes').fetchone()[0]=='validated_yahoo_adjusted_close'
    again=mod.record_close(db,data,date(2026,11,9),datetime(2026,11,10,9,tzinfo=ZoneInfo('Asia/Seoul')),prices,balance,orders)
    assert again==result
    db.close()

def test_historical_certificate_retry_after_later_fill_does_not_restate_history(fixture):
    db,data,prices,balance,orders=fixture
    now=datetime(2026,11,10,9,tzinfo=ZoneInfo('Asia/Seoul'))
    first=mod.record_close(db,data,date(2026,11,9),now,prices,balance,orders)
    db.execute("INSERT INTO intents VALUES ('20261110','005930.KS','BUY',1,105,'terminal',11)")
    db.execute("INSERT INTO fills VALUES ('20261110','005930.KS','BUY',1,105,11)");db.commit()
    balance['Output_1'][0]['rsdl_qty']=1
    assert mod.record_close(db,data,date(2026,11,9),now,prices,balance,orders)==first
    db.close()

@pytest.mark.parametrize('bad', ['mismatch','missing','unfinished','holdings','today','unmatched_fill','wrong_date'])
def test_unproven_closes_or_unreconciled_orders_fail(fixture,bad):
    db,data,prices,balance,orders=fixture
    now=datetime(2026,11,10,9,tzinfo=ZoneInfo('Asia/Seoul'))
    if bad=='mismatch': prices['005930.KS']['records'][0]['TDD_CLSPRC']='110'
    if bad=='missing': prices.pop('005930.KS')
    if bad=='unfinished': db.execute("INSERT INTO intents VALUES ('20261109','005930.KS','BUY',1,100,'acknowledged',10)");db.commit()
    if bad=='holdings': balance['Output_1'][0]['rsdl_qty']=1
    if bad=='today': now=datetime(2026,11,9,16,tzinfo=ZoneInfo('Asia/Seoul'))
    if bad=='unmatched_fill': db.execute("INSERT INTO intents VALUES ('20261109','005930.KS','BUY',1,100,'terminal',10)");db.execute("INSERT INTO fills VALUES ('20261109','005930.KS','BUY',1,100,10)");db.commit()
    if bad=='wrong_date': prices['005930.KS']['records'][0]['BAS_DD']='20261106'
    with pytest.raises(ValueError):mod.record_close(db,data,date(2026,11,9),now,prices,balance,orders)
    assert db.execute("SELECT count(*) FROM marks").fetchone()[0]==0
    db.close()


def test_filled_order_marks_explicit_estimated_cost_and_reconciles_broker(fixture):
    db,data,prices,balance,orders=fixture
    db.execute("INSERT INTO intents VALUES ('20261109','005930.KS','BUY',1,100,'terminal',10)")
    db.execute("INSERT INTO fills VALUES ('20261109','005930.KS','BUY',1,100,10)");db.commit()
    balance['Output_1'][0]['rsdl_qty']=1
    orders['20261109']={'rsp_cd':'00000','_pagination_complete':True,'Output_0':[{'itg_orr_no':10,'iem_cd':'005930','sby_dit_cd_nm':'현금매수','orr_qty':1,'tot_cns_qty':1,'ny_cns_qty':0,'can_qty':0,'cns_avg_uit_pr':100}]}
    result=mod.record_close(db,data,date(2026,11,9),datetime(2026,11,10,9,tzinfo=ZoneInfo('Asia/Seoul')),prices,balance,orders)
    assert result['equity_estimated']==pytest.approx(29999999.8)
    assumption, total, equity = db.execute('SELECT assumption,total,equity_estimated FROM cost_estimates').fetchone()
    assert assumption == mod.COST and 'actual fees unavailable' in assumption
    assert total==pytest.approx(.2) and equity==pytest.approx(29999999.8)
    orders['20261109']['Output_0'][0]['tot_cns_qty']=0
    # Existing certificate is immutable; a new day must recheck broker state.
    db.execute('DELETE FROM verified_closes');db.commit()
    with pytest.raises(ValueError):
        mod.record_close(db,data,date(2026,11,9),datetime(2026,11,10,9,tzinfo=ZoneInfo('Asia/Seoul')),prices,balance,orders)
    db.close()
