"""Paper account policy separation without broker connections."""
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
import src.config.loader as loader
import src.tools.nhplug_order_guard as module
from src.tools.nhplug_order_guard import NHOrderGuard, NHOrderBlocked


def guard(account):
    return NHOrderGuard(['/local/nhplug_mcp.py','--mode','paper','--account',account])

@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_HOME',str(tmp_path))
    root=tmp_path/'nhplug';root.mkdir()
    (root/'swing-policy.json').write_text(json.dumps({'enabled':True}))
    monkeypatch.setattr(loader,'load_agent_config',lambda: SimpleNamespace(mcp_servers={'nhplug_paper':SimpleNamespace(args=['--mode','paper','--account','swing'])}))
    clock=Mock();clock.now.return_value=datetime(2026,11,6,10,tzinfo=ZoneInfo('Asia/Seoul'));monkeypatch.setattr(module,'datetime',clock)
    return root


def reg(root,g,**entry):
    (root/'paper-policy-scopes.json').write_text(json.dumps({'version':1,'accounts':{g.scope:entry}}))


def order(account):
    return {'operationId':'krstockOrderCashBuy','input':{'act_no':account,'iem_cd':'005930','orr_qty':21,'orr_pr':200000,'nmn_pr_tp_cd':'01','rmt_mkt_cd':'KRX','sor_mkt_sli_yn':'N'}}


def test_independent_scope_keeps_default_swing_limits(setup):
    independent=guard('independent'); default=guard('swing'); reg(setup,independent,profile='independent',krx_session_check=True,auto_loss_stop=False)
    assert independent.paper_policy()['krx_session_check'] is True
    assert independent.paper_policy()['auto_loss_stop'] is False
    with pytest.raises(NHOrderBlocked,match='single order'):default.before('call_api',order('swing'))
    fp=independent.before('call_api',order('independent'),'a'*32)
    with independent.database() as db:
        assert db.execute('SELECT COUNT(*) FROM swing_buys WHERE scope=?',(independent.scope,)).fetchone()[0]==0
    with pytest.raises(NHOrderBlocked):independent.before('call_api',order('independent'),'b'*32)
    assert fp


def test_global_policy_cannot_opt_out_of_swing_limits(setup):
    (setup/'swing-policy.json').write_text(json.dumps({'enabled':True,'profile':'independent','_account_independent':True}))
    with pytest.raises(NHOrderBlocked,match='single order'):
        guard('swing').before('call_api',order('swing'))


def test_invalid_registry_and_default_override_fail_closed(setup):
    default=guard('swing');reg(setup,default,profile='independent',krx_session_check=True,auto_loss_stop=False)
    with pytest.raises(NHOrderBlocked):default.paper_policy()
    other=guard('other');reg(setup,other,profile='independent',krx_session_check=False,auto_loss_stop=False)
    with pytest.raises(NHOrderBlocked):other.paper_policy()
    (setup/'paper-policy-scopes.json').write_text('{')
    with pytest.raises(NHOrderBlocked):default.paper_policy()


def test_independent_scope_keeps_halt_and_price_checks(setup):
    g=guard('independent');reg(setup,g,profile='independent',krx_session_check=True,auto_loss_stop=False)
    (setup/'PAPER_HALT').touch()
    with pytest.raises(NHOrderBlocked,match='halted'):g.before('call_api',order('independent'))


def test_adapter_checks_quotes_but_not_swing_loss_before_independent_order(setup,monkeypatch):
    import time
    import src.tools.nhplug_calendar as calendar
    import src.tools.nhplug_loss as loss
    from fastmcp.client.client import CallToolResult
    from src.config.schema import MCPServerConfig
    from src.tools.mcp import MCPServerAdapter
    g=guard('independent');reg(setup,g,profile='independent',krx_session_check=True,auto_loss_stop=False)
    monkeypatch.setattr(time,'sleep',lambda _:None)
    monkeypatch.setattr(calendar,'check_session',lambda *a:{'allowed':True})
    monkeypatch.setattr(loss,'check_loss',lambda *a:{'allowed':False,'reason':'daily loss limit reached'})
    class Fake:
        def __init__(self):self.calls=[]
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def call_tool(self,name,arguments,**kwargs):
            self.calls.append((name,arguments))
            data={'Output_0':{'iem_cd':'005930'}} if name=='get_stock_price' else {'Output_0':{'mkt_orr_no':42}}
            return CallToolResult(content=[],structured_content=data,data=data,is_error=False,meta=None)
    fake=Fake();cfg=MCPServerConfig(command='python',args=['/local/nhplug_mcp.py','--mode','paper','--account','independent'])
    result=MCPServerAdapter('nhplug_independent',cfg,client_factory=lambda:fake).call_tool('call_api',order('independent'))
    assert result['status']=='ok', result
    assert [name for name,_ in fake.calls]==['get_stock_price','get_stock_price','call_api']
    assert fake.calls[-1][1]['operationId']=='krstockOrderCashBuy'
