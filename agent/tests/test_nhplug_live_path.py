"""Exercise the existing broker-reader and simulated gateway together; no writes."""
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from scripts.nh_live_broker import collect_snapshots
from src.live.halt import halt_path
from src.tools.nhplug_live_execution import SimulatedGuardedGateway, SimulatedTransport
from src.tools.nhplug_live_guard import LiveOrderBlocked

ACCOUNT = '12345678901'
NOW = datetime(2026, 11, 6, 11, 0, 10, tzinfo=ZoneInfo('Asia/Seoul'))
ORDER = {'act_no': ACCOUNT, 'iem_cd': '114800', 'orr_qty': 1, 'orr_pr': 1400,
         'nmn_pr_tp_cd': '01', 'rmt_mkt_cd': 'KRX', 'sor_mkt_sli_yn': 'N',
         'orr_cnd_dit_cd': '00', 'ssl_nmn_pr_dit_cd': '00'}
ACK = {'rsp_cd': '00048', 'rsp_msg': '매수주문이 완료되었습니다', 'Output_0': {'mkt_orr_no': 42}}


class Reader:
    server_name = 'nhplug_readonly'
    server_config = SimpleNamespace(args=['--mode', 'readonly'])

    def __init__(self, *, loss=0, complete=True):
        self.loss = loss
        self.complete = complete
        self.quote_count = 0

    def call_tool(self, name, args):
        if name == 'list_accounts':
            data = {'rsp_cd': '00000', 'Output_0': [{'acct_no': ACCOUNT, 'acct_type': '01'}]}
        elif name == 'get_stock_price':
            self.quote_count += 1
            data = {'rsp_cd': '00000', 'Output_0': {'iem_cd': '114800', 'market_status': '1',
                    'hoga_bsop_hour': '11:00:07' if self.quote_count == 1 else '11:00:09', 'askp': 1400}}
        elif args['operationId'] == 'krstockInquiryDailyOrderExecution':
            data = {'rsp_cd': '11512', 'rsp_msg': '데이터가 존재하지 않습니다.',
                    '_nh_pagination': {'complete': self.complete}}
        elif args['operationId'] == 'krstockInquiryBalance':
            data = {'rsp_cd': '00166', 'rsp_msg': '조회가 완료되었습니다.',
                    'Output_0': {'orr_pbl_amt1': 1000000, 'tot_aet_amt': 1000000, 'tot_byn_amt': 0}, 'Output_1': [],
                    '_nh_pagination': {'complete': self.complete}}
        elif args['operationId'] == 'krstockInquiryRealizedPnl':
            data = {'rsp_cd': '00166', 'rsp_msg': '조회가 완료되었습니다.',
                    'Output_0': {'sby_pls_amt': self.loss, 'sdr_xps': 0, 'eal_pls_amt': 0}}
        else:
            raise AssertionError('모의 조회 외 호출 금지')
        return {'status': 'ok', 'data': data}


def test_fake_read_through_simulated_send_and_reconciliation(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_HOME', str(tmp_path / 'runtime'))
    observed = collect_snapshots(Reader(), ACCOUNT, NOW, sleep=lambda _: None)
    transport = SimulatedTransport(observed, ACK)
    gateway = SimulatedGuardedGateway(ACCOUNT, tmp_path / 'guard', transport)
    intent = gateway.submit('krstockOrderCashBuy', ORDER, NOW)
    assert len(transport.calls) == 1
    with pytest.raises(LiveOrderBlocked):
        gateway.reconcile(intent)
    transport.snapshots['history'].update(rsp_cd='00166', rsp_msg='조회가 완료되었습니다.', Output_0=[{
        'itg_orr_no': 42, 'iem_cd': '114800', 'orr_qty': 1, 'tot_cns_qty': 1,
        'ny_cns_qty': 0, 'can_qty': 0, 'sby_dit_cd_nm': '현금매수'}])
    assert gateway.reconcile(intent) == {'filled': 1, 'cancelled': 0}


def test_incomplete_pages_prevent_simulated_submission():
    with pytest.raises(LiveOrderBlocked, match='completeness'):
        collect_snapshots(Reader(complete=None), ACCOUNT, NOW, sleep=lambda _: None)


def test_fake_loss_and_real_halt_prevent_simulated_send(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_HOME', str(tmp_path / 'runtime'))
    observed = collect_snapshots(Reader(loss=-20000), ACCOUNT, NOW, sleep=lambda _: None)
    transport = SimulatedTransport(observed, ACK)
    with pytest.raises(LiveOrderBlocked, match='latch'):
        SimulatedGuardedGateway(ACCOUNT, tmp_path / 'guard', transport).submit('krstockOrderCashBuy', ORDER, NOW)
    assert not transport.calls
    halt_path().parent.mkdir(parents=True, exist_ok=True)
    halt_path().touch()
    with pytest.raises(LiveOrderBlocked, match='kill switch'):
        SimulatedGuardedGateway(ACCOUNT, tmp_path / 'other', transport).submit('krstockOrderCashBuy', ORDER, NOW)
    assert not transport.calls
