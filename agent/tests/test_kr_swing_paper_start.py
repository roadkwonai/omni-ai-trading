import importlib.util
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import pytest

path = Path(__file__).resolve().parents[2] / 'scripts/start_kr_swing_paper.py'
spec = importlib.util.spec_from_file_location('paper_start', path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_snapshot_fails_closed():
    accounts = {'rsp_cd':'00000','Output_0':[{'acct_no':'123','acct_type':'03'}]}
    balance = {'rsp_cd':'00000','Output_0':{},'Output_1':[]}
    orders = {'rsp_cd':'XA102','Output_0':[]}
    mod.validate_snapshot('123',accounts,balance,orders)
    for bad in ({'Output_1':[{'rsdl_qty':1}]}, {'Output_1':[{}]}):
        with pytest.raises(ValueError):
            mod.validate_snapshot('123',accounts,{**balance,**bad},orders)
    with pytest.raises(ValueError):
        mod.validate_snapshot('123',accounts,balance,{'rsp_cd':'00000','Output_0':[{'ny_cns_qty':1}]})


def test_initialize_idempotent_and_never_overwrites(tmp_path, monkeypatch):
    protocol=tmp_path/'protocol.json';protocol.write_text('{}')
    metadata=tmp_path/'metadata.json';metadata.write_text('{}')
    monkeypatch.setattr(mod,'PROTOCOL',protocol);monkeypatch.setattr(mod,'METADATA',metadata)
    summary=tmp_path/'summary.json';monkeypatch.setattr(mod,'SUMMARY',summary)
    target=tmp_path/'forward.json';now=datetime(2026,11,6,11,tzinfo=ZoneInfo('Asia/Seoul'))
    accounts={'rsp_cd':'00000','Output_0':[{'acct_no':'123','acct_type':'03'}]}
    balance={'rsp_cd':'00000','Output_0':{},'Output_1':[]};orders={'rsp_cd':'XA102','Output_0':[]}
    with pytest.raises((ValueError, OSError)):
        mod.initialize(accounts,balance,orders,'123',target,now)
    summary.write_text(json.dumps({'protocol_sha256':'stale','metadata_sha256':mod.digest(metadata),'promotion_passed':False}))
    with pytest.raises(ValueError):
        mod.initialize(accounts,balance,orders,'123',target,now)
    summary.write_text(json.dumps({'protocol_sha256':mod.digest(protocol),'metadata_sha256':'stale','promotion_passed':False}))
    with pytest.raises(ValueError):
        mod.initialize(accounts,balance,orders,'123',target,now)
    summary.write_text(json.dumps({'protocol_sha256':mod.digest(protocol),'metadata_sha256':mod.digest(metadata),'promotion_passed':False}))
    state=mod.initialize(accounts,balance,orders,'123',target,now)
    assert state['status']=='research_rejected' and not state['forward_performance_validated']
    assert '123' not in target.read_text() and target.stat().st_mode & 0o777 == 0o600
    assert mod.initialize(accounts,balance,orders,'123',target,now)==state
    protocol.write_text('{"changed":true}')
    with pytest.raises(ValueError):mod.initialize(accounts,balance,orders,'123',target,now)
