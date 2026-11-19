from datetime import date
from pathlib import Path
import importlib.util
import json
import subprocess
import pytest

spec = importlib.util.spec_from_file_location('cycle', Path(__file__).resolve().parents[2] / 'scripts/kr_swing_v5_cycle.py')
cycle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cycle)

@pytest.fixture
def runtime(tmp_path):
    (tmp_path / 'benchmark.json').write_text(json.dumps({'baseline':'2026-11-05','registered_date_kst':'2026-11-06','last_session':'2026-11-05'}))
    snap = tmp_path/'data'/'versions'/'one'; snap.mkdir(parents=True)
    (tmp_path/'data'/'current').symlink_to(snap)
    return tmp_path

def test_future_cycle_prices_verified_before_trade(runtime, monkeypatch):
    calls=[]
    def fake(cmd, **kw):
        name=Path(cmd[1]).name;calls.append(cmd)
        result = {'kr_swing_v5_daily_data.py':{'status':'published','last_session':'2026-11-09'},
                  'kr_swing_v5_forward.py':{'status':'wait','day':'2026-11-09'},
                  'kr_swing_v5_benchmark.py':{'status':'observing'},
                  'kr_swing_v5_valuation.py':{'status':'verified'},
                  'kr_swing_v5_compare.py':{'status':'observing'}}[name]
        return subprocess.CompletedProcess(cmd,0,stdout=json.dumps(result),stderr='')
    monkeypatch.setattr(cycle.subprocess,'run',fake)
    monkeypatch.setattr(cycle,'session_days',lambda runtime,start,end:['2026-11-09'])
    assert cycle.run_cycle(date(2026,11,9),date(2026,11,10),runtime,execute=True)=='wait'
    assert [Path(c[1]).name for c in calls] == ['kr_swing_v5_daily_data.py','kr_swing_v5_valuation.py','kr_swing_v5_benchmark.py','kr_swing_v5_compare.py','kr_swing_v5_forward.py']
    assert not any('--execute' in c for c in calls) # wait means no actual order

def test_data_failure_prevents_subsequent_stages(runtime, monkeypatch):
    calls=[]
    def fake(cmd,**kw):
        calls.append(cmd);return subprocess.CompletedProcess(cmd,1,stdout='secret',stderr='secret')
    monkeypatch.setattr(cycle.subprocess,'run',fake)
    with pytest.raises(RuntimeError,match='data') as e:
        cycle.run_cycle(date(2026,11,9),date(2026,11,10),runtime,execute=True)
    assert len(calls)==1 and 'secret' not in str(e.value)

def test_bad_close_or_stale_signal_cannot_place_order(runtime, monkeypatch):
    calls=[]
    def fake(cmd,**kw):
        name=Path(cmd[1]).name;calls.append(cmd)
        output={'kr_swing_v5_daily_data.py':{'status':'published','last_session':'2026-11-09'},'kr_swing_v5_valuation.py':{'status':'unverified'}}[name]
        return subprocess.CompletedProcess(cmd,0,stdout=json.dumps(output),stderr='')
    monkeypatch.setattr(cycle.subprocess,'run',fake)
    monkeypatch.setattr(cycle,'session_days',lambda *args:['2026-11-09'])
    with pytest.raises(RuntimeError,match='valuation'):
        cycle.run_cycle(date(2026,11,9),date(2026,11,10),runtime,execute=True)
    assert not any('--execute' in cmd for cmd in calls)

def test_current_or_future_asof_blocked(runtime, monkeypatch):
    monkeypatch.setattr(cycle.subprocess,'run',lambda *a,**k: pytest.fail('network invoked'))
    with pytest.raises(ValueError,match='completed'):
        cycle.run_cycle(date(2026,11,10),date(2026,11,10),runtime,execute=True)

def test_ready_executes_once_only_after_readonly_comparison(runtime, monkeypatch):
    calls=[]
    def fake(cmd,**kw):
        name=Path(cmd[1]).name;calls.append(cmd)
        if name=='kr_swing_v5_forward.py':
            output={'status':'terminal'} if '--execute' in cmd else {'status':'ready','day':'2026-11-09'}
        else:
            output={'kr_swing_v5_daily_data.py':{'status':'published','last_session':'2026-11-09'},
                    'kr_swing_v5_valuation.py':{'status':'verified'},
                    'kr_swing_v5_benchmark.py':{'status':'observing'},
                    'kr_swing_v5_compare.py':{'status':'observing'}}[name]
        return subprocess.CompletedProcess(cmd,0,stdout=json.dumps(output),stderr='')
    monkeypatch.setattr(cycle.subprocess,'run',fake)
    monkeypatch.setattr(cycle,'session_days',lambda *args:['2026-11-09'])
    assert cycle.run_cycle(date(2026,11,9),date(2026,11,10),runtime,execute=True)=='terminal'
    assert len([cmd for cmd in calls if '--execute' in cmd])==1
    assert Path(calls[-1][1]).name=='kr_swing_v5_forward.py'


def test_prior_baseline_does_not_run_future_valuation(runtime, monkeypatch):
    calls=[]
    def fake(cmd,**kw):
        name=Path(cmd[1]).name;calls.append(cmd)
        output={'kr_swing_v5_daily_data.py':{'status':'published','last_session':'2026-11-05'},
                'kr_swing_v5_compare.py':{'status':'awaiting_future_sessions'},
                'kr_swing_v5_forward.py':{'status':'wait','day':'2026-11-05'}}[name]
        return subprocess.CompletedProcess(cmd,0,stdout=json.dumps(output),stderr='')
    monkeypatch.setattr(cycle.subprocess,'run',fake)
    monkeypatch.setattr(cycle,'session_days',lambda *args:[])
    assert cycle.run_cycle(date(2026,11,5),date(2026,11,6),runtime,execute=True)=='wait'
    assert [Path(c[1]).name for c in calls]==['kr_swing_v5_daily_data.py','kr_swing_v5_compare.py','kr_swing_v5_forward.py']


def test_step_accepts_pretty_printed_json(monkeypatch):
    payload = {'status':'awaiting_future_sessions', 'comparisons': []}
    monkeypatch.setattr(cycle.subprocess, 'run', lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload, indent=2), stderr=''))
    assert cycle._step('comparison', ['kr_swing_v5_compare.py']) == payload
