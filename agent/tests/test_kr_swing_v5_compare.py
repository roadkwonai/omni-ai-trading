import importlib.util
import hashlib
import json
import sqlite3
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('v5compare', Path(__file__).resolve().parents[2] / 'scripts/kr_swing_v5_compare.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

@pytest.fixture
def journal(tmp_path):
    path = tmp_path / 'forward.sqlite3'
    db = sqlite3.connect(path)
    db.executescript('''CREATE TABLE observations (day TEXT PRIMARY KEY,observed_at TEXT,signals TEXT,closes TEXT,state TEXT);
    CREATE TABLE marks (day TEXT PRIMARY KEY,observed_at TEXT,cash_gross REAL,holdings_value REAL,equity_gross REAL);
    CREATE TABLE cost_estimates (day TEXT PRIMARY KEY,assumption TEXT,total REAL,equity_estimated REAL);
    CREATE TABLE verified_closes (day TEXT PRIMARY KEY, source TEXT, input_sha256 TEXT, evidence TEXT);
    CREATE TABLE fills (day TEXT,symbol TEXT,side TEXT,quantity INTEGER,price REAL,order_number INTEGER);
    CREATE TABLE intents (day TEXT,symbol TEXT,side TEXT,qty INTEGER,price INTEGER,status TEXT,order_number INTEGER);''')
    db.execute("INSERT INTO observations VALUES ('2026-11-05','2026-11-06T11:00:00+09:00','{}','{}','observed')")
    db.execute("INSERT INTO marks VALUES ('2026-11-05','2026-11-06T11:00:00+09:00',30000000,0,30000000)")
    db.execute("INSERT INTO cost_estimates VALUES ('2026-11-05','estimated only: buy 0.20%, sell 0.40%; actual fees unavailable',0,30000000)")
    db.commit();db.close();return path


def verified(db, day, observed="2026-11-06T16:00:00+09:00"):
    evidence = json.dumps({"day":day, "source":"validated_yahoo_adjusted_close", "observed_at":observed, "cash_gross_krw":29900000, "holdings_krw":120000, "estimated_cost_krw":1000, "broker_snapshot_verified":True, "snapshot_manifest_sha256":"a"*64, "prices":{c:{"yahoo_close":100, "krx_close":100} for c in ("005930.KS","000660.KS","035420.KS","005380.KS","105560.KS")}, "positions":{c:(1200 if c=="005930.KS" else 0) for c in ("005930.KS","000660.KS","035420.KS","005380.KS","105560.KS")}, "fills":[]}, sort_keys=True)
    db.execute("INSERT INTO verified_closes VALUES (?,?,?,?)", (day, "validated_yahoo_adjusted_close", hashlib.sha256(evidence.encode()).hexdigest(), evidence))


def benchmark(obs=None):
    return {'baseline':'2026-11-05','registered_date_kst':'2026-11-05','last_session':(obs or [{'date':'2026-11-05'}])[-1]['date'],
            'observations':obs or [],'nav':(obs[-1]['nav'] if obs else 30000000),
            'target_annual_vol':0.059378,'gross_weight':0.1584,
            'cost_note':'0.20% buy commission and 0.05% buy slippage; 0.20% sell tax + 0.20% sell commission if liquidation is later modeled'}


def test_baseline_has_no_forward_performance(journal):
    report=mod.compare(journal,benchmark())
    assert report['status']=='awaiting_future_sessions' and report['comparisons']==[]
    assert report['baseline']=='2026-11-05' and report['comparison_effective_start']=='2026-11-05'


def test_matching_sessions_net_cost_and_return(journal):
    db=sqlite3.connect(journal)
    db.execute("INSERT INTO observations VALUES ('2026-11-06','2026-11-06T16:00:00+09:00','{}','{}','observed')")
    db.execute("INSERT INTO marks VALUES ('2026-11-06','2026-11-06T16:00:00+09:00',29900000,120000,30020000)")
    db.execute("INSERT INTO cost_estimates VALUES ('2026-11-06','estimated only: buy 0.20%, sell 0.40%; actual fees unavailable',1000,30019000)")
    verified(db, '2026-11-06')
    db.commit();db.close()
    report=mod.compare(journal,benchmark([{'date':'2026-11-06','nav':30012000,'return':.0004}]), ['2026-11-06'])
    assert report['status']=='observing' and report['comparisons'][0]['strategy_return']==pytest.approx(19000/30000000)
    assert report['comparisons'][0]['difference']==pytest.approx(7000/30000000)


def test_mismatched_dates_and_missing_fees_fail_closed(journal):
    with pytest.raises(ValueError,match='dates'):
        mod.compare(journal,benchmark([{'date':'2026-11-06','nav':30000000,'return':0}]))
    db=sqlite3.connect(journal)
    db.execute("INSERT INTO observations VALUES ('2026-11-06','2026-11-07T10:00:00+09:00','{}','{}','observed')")
    db.execute("INSERT INTO marks VALUES ('2026-11-06','2026-11-07T10:00:00+09:00',29900000,120000,30020000)")
    db.commit();db.close()
    with pytest.raises(ValueError,match='cost'):
        mod.compare(journal,benchmark([{'date':'2026-11-06','nav':30000000,'return':0}]), ['2026-11-06'])


def test_next_day_intraday_marks_are_not_previous_close(journal):
    db=sqlite3.connect(journal)
    db.execute("INSERT INTO observations VALUES ('2026-11-06','2026-11-09T10:00:00+09:00','{}','{}','observed')")
    db.execute("INSERT INTO marks VALUES ('2026-11-06','2026-11-09T10:00:00+09:00',29900000,120000,30020000)")
    db.execute("INSERT INTO cost_estimates VALUES ('2026-11-06','estimated only: buy 0.20%, sell 0.40%; actual fees unavailable',1000,30019000)")
    db.commit();db.close()
    with pytest.raises(ValueError,match='provenance'):
        mod.compare(journal,benchmark([{'date':'2026-11-06','nav':30000000,'return':0}]), ['2026-11-06'])


def test_future_mark_requires_close_provenance(journal):
    db=sqlite3.connect(journal)
    db.execute("INSERT INTO observations VALUES ('2026-11-06','2026-11-06T16:00:00+09:00','{}','{}','observed')")
    db.execute("INSERT INTO marks VALUES ('2026-11-06','2026-11-06T16:00:00+09:00',29900000,120000,30020000)")
    db.execute("INSERT INTO cost_estimates VALUES ('2026-11-06','estimated only: buy 0.20%, sell 0.40%; actual fees unavailable',1000,30019000)")
    db.commit();db.close()
    with pytest.raises(ValueError,match='provenance'):
        mod.compare(journal,benchmark([{'date':'2026-11-06','nav':30000000,'return':0}]), ['2026-11-06'])


def test_duplicate_and_nonchronological_benchmark_rejected(journal):
    with pytest.raises(ValueError,match='chronological'):
        mod.compare(journal,benchmark([{'date':'2026-11-06','nav':30000000},{'date':'2026-11-06','nav':30000000}]))


def test_unknown_cost_basis_rejected(journal):
    item = benchmark()
    item['cost_note'] = 'unknown'
    with pytest.raises(ValueError, match='cost'):
        mod.compare(journal, item)


def test_cannot_compare_registration_day_open(journal):
    item=benchmark([{'date':'2026-11-06','nav':30000000}])
    item['registered_date_kst']='2026-11-06'
    with pytest.raises(ValueError,match='registration'):
        mod.compare(journal,item,['2026-11-06'])


def test_valuation_day_is_independent_of_signal_day(journal):
    db=sqlite3.connect(journal)
    db.execute("INSERT INTO observations VALUES ('2026-11-06','2026-11-09T10:00:00+09:00','{}','{}','observed')")
    db.execute("INSERT INTO marks VALUES ('2026-11-09','2026-11-09T16:00:00+09:00',29900000,120000,30020000)")
    db.execute("INSERT INTO cost_estimates VALUES ('2026-11-09','estimated only: buy 0.20%, sell 0.40%; actual fees unavailable',1000,30019000)")
    verified(db, '2026-11-09', '2026-11-09T16:00:00+09:00')
    db.commit();db.close()
    report=mod.compare(journal,benchmark([{'date':'2026-11-09','nav':30012000}]), ['2026-11-09'])
    assert report['comparisons'][0]['date']=='2026-11-09'


def test_next_day_verified_close_keeps_real_observation_time(journal):
    db=sqlite3.connect(journal)
    db.execute("INSERT INTO marks VALUES ('2026-11-06','2026-11-09T10:00:00+09:00',29900000,120000,30020000)")
    db.execute("INSERT INTO cost_estimates VALUES ('2026-11-06','estimated only: buy 0.20%, sell 0.40%; actual fees unavailable',1000,30019000)")
    verified(db, '2026-11-06', '2026-11-09T10:00:00+09:00');db.commit();db.close()
    result=mod.compare(journal,benchmark([{'date':'2026-11-06','nav':30012000}]), ['2026-11-06'])
    assert result['comparisons'][0]['strategy_nav_estimated']==30019000


def test_close_evidence_hash_must_match(journal):
    db=sqlite3.connect(journal)
    db.execute("INSERT INTO marks VALUES ('2026-11-06','2026-11-09T10:00:00+09:00',29900000,120000,30020000)")
    db.execute("INSERT INTO cost_estimates VALUES ('2026-11-06','estimated only: buy 0.20%, sell 0.40%; actual fees unavailable',1000,30019000)")
    verified(db, '2026-11-06', '2026-11-09T10:00:00+09:00')
    db.execute("UPDATE verified_closes SET evidence='{}'");db.commit();db.close()
    with pytest.raises(ValueError, match='evidence'):
        mod.compare(journal,benchmark([{'date':'2026-11-06','nav':30012000}]), ['2026-11-06'])


def test_same_basis_prices_and_positions_must_support_nav(journal):
    db=sqlite3.connect(journal)
    db.execute("INSERT INTO marks VALUES ('2026-11-06','2026-11-06T16:00:00+09:00',29900000,120000,30020000)")
    db.execute("INSERT INTO cost_estimates VALUES ('2026-11-06','estimated only: buy 0.20%, sell 0.40%; actual fees unavailable',1000,30019000)")
    verified(db,'2026-11-06')
    evidence=json.loads(db.execute('SELECT evidence FROM verified_closes').fetchone()[0])
    evidence['prices']['005930.KS']['krx_close']=103
    encoded=json.dumps(evidence,sort_keys=True)
    db.execute('UPDATE verified_closes SET evidence=?,input_sha256=?',(encoded,hashlib.sha256(encoded.encode()).hexdigest()))
    db.commit();db.close()
    with pytest.raises(ValueError,match='basis'):
        mod.compare(journal,benchmark([{'date':'2026-11-06','nav':30012000}]), ['2026-11-06'])


def test_close_mark_before_close_is_denied_even_with_hashed_evidence(journal):
    db=sqlite3.connect(journal)
    observed='2026-11-06T15:29:59+09:00'
    db.execute("INSERT INTO marks VALUES ('2026-11-06',?,29900000,120000,30020000)",(observed,))
    db.execute("INSERT INTO cost_estimates VALUES ('2026-11-06','estimated only: buy 0.20%, sell 0.40%; actual fees unavailable',1000,30019000)")
    verified(db,'2026-11-06',observed);db.commit();db.close()
    with pytest.raises(ValueError,match='earlier'):
        mod.compare(journal,benchmark([{'date':'2026-11-06','nav':30012000}]), ['2026-11-06'])
