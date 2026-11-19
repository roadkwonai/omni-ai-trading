"""Only in-memory simulations; no broker/network calls."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.live.halt import halt_path
from src.tools.nhplug_live_execution import SimulatedGuardedGateway, SimulatedTransport
from src.tools.nhplug_live_guard import LiveOrderBlocked

ACCOUNT = '12345678901'
NOW = datetime(2026, 11, 6, 11, 0, 10, tzinfo=ZoneInfo('Asia/Seoul'))
BUY = {'act_no': ACCOUNT, 'iem_cd':'114800', 'orr_qty':1, 'orr_pr':1400,
       'nmn_pr_tp_cd':'01', 'rmt_mkt_cd':'KRX', 'sor_mkt_sli_yn':'N',
       'orr_cnd_dit_cd':'00', 'ssl_nmn_pr_dit_cd':'00'}
ACK = {'rsp_cd':'00048', 'rsp_msg':'매수주문이 완료되었습니다', 'Output_0':{'mkt_orr_no':42}}


def quote(clock):
    return {'status':'ok', 'data':{'rsp_cd':'00000', 'Output_0':{'iem_cd':'114800',
            'market_status':'1', 'hoga_bsop_hour':clock, 'askp':1400}}}


def snapshots():
    return {'accounts':{'rsp_cd':'00000', 'Output_0':[{'acct_no':ACCOUNT,'acct_type':'01'}]},
            'pnl':{'rsp_cd':'00166','rsp_msg':'조회가 완료되었습니다.',
                   'Output_0':{'sby_pls_amt':0,'sdr_xps':0,'eal_pls_amt':0}},
            'history':{'_pagination_complete':True,'_account':ACCOUNT,'_day':'20261106',
                       'rsp_cd':'00166','rsp_msg':'조회가 완료되었습니다.','Output_0':[]},
            'balance':{'_pagination_complete':True,'_account':ACCOUNT,'rsp_cd':'00166',
                       'rsp_msg':'조회가 완료되었습니다.',
                       'Output_0':{'orr_pbl_amt1':1000000,'tot_aet_amt':1000000,'tot_byn_amt':0},'Output_1':[]},
            'previous_quote':quote('11:00:07'),'quote':quote('11:00:09')}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv('VIBE_TRADING_HOME', str(tmp_path / 'runtime'))


def test_only_in_memory_transport_is_accepted(tmp_path):
    with pytest.raises(LiveOrderBlocked, match='in-memory'):
        SimulatedGuardedGateway(ACCOUNT, tmp_path, object())


def test_limit_and_halt_block_before_send(tmp_path):
    snap = snapshots()
    snap['pnl']['Output_0']['sby_pls_amt'] = -20000
    transport = SimulatedTransport(snap, ACK)
    gateway = SimulatedGuardedGateway(ACCOUNT, tmp_path, transport)
    with pytest.raises(LiveOrderBlocked, match='latch'):
        gateway.submit('krstockOrderCashBuy', BUY, NOW)
    assert not transport.calls
    transport.snapshots = snapshots()
    with pytest.raises(LiveOrderBlocked, match='latch'):
        gateway.submit('krstockOrderCashBuy', BUY, NOW)
    assert not transport.calls


def test_halt_blocks_before_send(tmp_path):
    halt_path().parent.mkdir(parents=True)
    halt_path().touch()
    transport = SimulatedTransport(snapshots(), ACK)
    with pytest.raises(LiveOrderBlocked, match='kill switch'):
        SimulatedGuardedGateway(ACCOUNT, tmp_path, transport).submit('krstockOrderCashBuy', BUY, NOW)
    assert not transport.calls


def test_halt_after_reservation_blocks_send_and_keeps_intent(tmp_path, monkeypatch):
    import src.tools.nhplug_live_execution as module
    transport = SimulatedTransport(snapshots(), ACK)
    monkeypatch.setattr(module, 'halt_flag_set', lambda broker: True)
    with pytest.raises(LiveOrderBlocked, match='before simulated send'):
        SimulatedGuardedGateway(ACCOUNT, tmp_path, transport).submit('krstockOrderCashBuy', BUY, NOW)
    assert not transport.calls
    monkeypatch.setattr(module, 'halt_flag_set', lambda broker: False)
    with pytest.raises(LiveOrderBlocked):
        SimulatedGuardedGateway(ACCOUNT, tmp_path, transport).submit('krstockOrderCashBuy', {**BUY, 'orr_pr':1399}, NOW)
    assert not transport.calls


def test_failed_response_never_clears_intent(tmp_path):
    response = {'rsp_cd':'ERROR', 'rsp_msg':'거절', 'Output_0':{'mkt_orr_no':42}}
    transport = SimulatedTransport(snapshots(), response)
    gateway = SimulatedGuardedGateway(ACCOUNT, tmp_path, transport)
    with pytest.raises(LiveOrderBlocked, match='business'):
        gateway.submit('krstockOrderCashBuy', BUY, NOW)
    with pytest.raises(LiveOrderBlocked):
        gateway.submit('krstockOrderCashBuy', {**BUY, 'orr_pr':1399}, NOW)
    assert len(transport.calls) == 1


def test_disconnect_prevents_retransmission_after_restart(tmp_path):
    transport = SimulatedTransport(snapshots(), ACK, disconnect=True)
    with pytest.raises(TimeoutError):
        SimulatedGuardedGateway(ACCOUNT, tmp_path, transport).submit('krstockOrderCashBuy', BUY, NOW)
    with pytest.raises(LiveOrderBlocked):
        SimulatedGuardedGateway(ACCOUNT, tmp_path, transport).submit('krstockOrderCashBuy', {**BUY, 'orr_pr':1399}, NOW)
    assert len(transport.calls) == 1


def test_ack_is_not_fill_and_full_history_is_required(tmp_path):
    transport = SimulatedTransport(snapshots(), ACK)
    gateway = SimulatedGuardedGateway(ACCOUNT, tmp_path, transport)
    intent = gateway.submit('krstockOrderCashBuy', BUY, NOW)
    with pytest.raises(LiveOrderBlocked):
        gateway.reconcile(intent)
    row = {'itg_orr_no':42,'iem_cd':'114800','orr_qty':1,'tot_cns_qty':1,
           'ny_cns_qty':0,'can_qty':0,'sby_dit_cd_nm':'현금매수'}
    transport.snapshots['history']['Output_0'] = [row]
    assert gateway.reconcile(intent) == {'filled':1,'cancelled':0}
