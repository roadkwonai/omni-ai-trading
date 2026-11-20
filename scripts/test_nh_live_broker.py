from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from src.tools.nhplug_live_guard import LiveOrderBlocked, LiveOrderGuard
from nh_live_broker import collect_snapshots

A = '12345678901'
NOW = datetime(2026, 11, 6, 11, 0, 10, tzinfo=ZoneInfo('Asia/Seoul'))
FIELDS = {'act_no':A, 'iem_cd':'114800', 'orr_qty':1, 'orr_pr':1400, 'nmn_pr_tp_cd':'01',
          'rmt_mkt_cd':'KRX', 'sor_mkt_sli_yn':'N', 'orr_cnd_dit_cd':'00', 'ssl_nmn_pr_dit_cd':'00'}


class FakeReader:
    server_name = 'nhplug_readonly'
    server_config = SimpleNamespace(args=['--mode', 'readonly'])
    def __init__(self):
        self.quotes = 0
        self.calls = []
        self.meta = {'complete': True, 'cts':'x', 'cts_flag':'N'}
    def call_tool(self, tool, args):
        self.calls.append((tool, args))
        if tool == 'list_accounts':
            return {'status':'ok', 'data':{'rsp_cd':'00000','Output_0':[{'acct_no':A,'acct_type':'01'}]}}
        if tool == 'get_stock_price':
            self.quotes += 1
            stamp = '11:00:07' if self.quotes == 1 else '11:00:09'
            return {'status':'ok','data':{'rsp_cd':'00000','Output_0':{'iem_cd':'114800','market_status':'1','hoga_bsop_hour':stamp,'askp':1400,'bidp':1399}}}
        op = args['operationId']
        assert op.startswith('krstockInquiry')
        if op == 'krstockInquiryDailyOrderExecution':
            data={'rsp_cd':'11512','rsp_msg':'데이터가 존재하지 않습니다.', '_nh_pagination':self.meta}
        elif op == 'krstockInquiryBalance':
            data={'rsp_cd':'00166','rsp_msg':'조회가 완료되었습니다.','Output_0':{'orr_pbl_amt1':1000000,'tot_aet_amt':1000000,'tot_byn_amt':0},'Output_1':[],'_nh_pagination':self.meta}
        else:
            data={'rsp_cd':'00166','rsp_msg':'조회가 완료되었습니다.','Output_0':{'sby_pls_amt':0,'sdr_xps':0,'eal_pls_amt':0}}
        return {'status':'ok','data':data}


class FakeTransport:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def submit(self, operation, fields):
        self.calls.append((operation, fields))
        if self.fail:
            raise TimeoutError('mock transport disconnected')
        return {'rsp_cd':'00048','rsp_msg':'매수주문이 완료되었습니다', 'Output_0':{'mkt_orr_no':42}}


def test_readonly_collection_drives_fake_order_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_HOME', str(tmp_path / 'runtime'))
    reader = FakeReader()
    snapshots = collect_snapshots(reader, A, NOW, sleep=lambda _: None)
    guard = LiveOrderGuard(A, tmp_path / 'guard')
    fingerprint = guard.reserve('krstockOrderCashBuy', FIELDS, now=NOW, **snapshots)
    transport = FakeTransport()
    guard.acknowledge(fingerprint, transport.submit('krstockOrderCashBuy', FIELDS))
    row = {'itg_orr_no':42,'iem_cd':'114800','orr_qty':1,'tot_cns_qty':1,'ny_cns_qty':0,
           'can_qty':0,'sby_dit_cd_nm':'현금매수'}
    history = {**snapshots['history'], 'rsp_cd':'00166','rsp_msg':'조회가 완료되었습니다.','Output_0':[row]}
    assert guard.reconcile(fingerprint, history) == {'filled':1, 'cancelled':0}
    assert len(transport.calls) == 1
    assert all(not params.get('operationId','').startswith('krstockOrder') for _, params in reader.calls)


def test_collection_rejects_missing_continuation_and_non_readonly():
    reader = FakeReader()
    reader.meta = {'complete':None}
    with pytest.raises(LiveOrderBlocked, match='completeness'):
        collect_snapshots(reader,A,NOW,sleep=lambda _:None)
    reader.server_config = SimpleNamespace(args=['--mode','paper'])
    with pytest.raises(LiveOrderBlocked, match='readonly'):
        collect_snapshots(reader,A,NOW,sleep=lambda _:None)


def test_collection_rejects_repeated_cursor():
    reader = FakeReader()
    reader.meta = {'complete':False,'cts':'same','cts_flag':'Y'}
    with pytest.raises(LiveOrderBlocked, match='cursor'):
        collect_snapshots(reader,A,NOW,sleep=lambda _:None)


def test_linked_balance_reads_only_registered_live_account(monkeypatch):
    from accounts import digest
    from nh_live_broker import read_linked_balance

    monkeypatch.setattr('nh_live_broker.PAGE_GAP', 0)
    record = {'kind': 'live', 'hash': digest(A)}

    class PaginatedReader(FakeReader):
        def call_tool(self, tool, args):
            response = super().call_tool(tool, args)
            if tool == 'call_api':
                data = response['data']
                data['Output_1'] = [{'iem_cd': '005930' if 'cts' not in args else '114800', 'rsdl_qty': 1}]
                if 'cts' not in args:
                    data.update(rsp_cd='00218', rsp_msg='계속 조회시 다음(연속조회) 버튼을 누르시기 바랍니다.',
                                _nh_pagination={'complete': False, 'cts': 'next', 'cts_flag': 'Y'})
            return response

    reader = PaginatedReader()
    before = datetime.now(NOW.tzinfo)
    data, observed_at = read_linked_balance(reader, 'live-1', record)
    assert before <= observed_at <= datetime.now(NOW.tzinfo)
    assert [row['iem_cd'] for row in data['Output_1']] == ['005930', '114800']
    assert [tool for tool, _ in reader.calls] == ['list_accounts', 'call_api', 'call_api']
    assert all(args['operationId'] == 'krstockInquiryBalance' and args['input']['act_no'] == A for _, args in reader.calls[1:])
    assert reader.calls[-1][1]['cts'] == 'next'

    reader = FakeReader()
    with pytest.raises(ValueError, match='연결 확인 불가'):
        read_linked_balance(reader, 'live-1', {'kind': 'live', 'hash': digest('99999999999')})
    assert reader.calls == [('list_accounts', {})]

    reader = FakeReader()
    reader.server_config = SimpleNamespace(args=['--mode', 'paper'])
    with pytest.raises(LiveOrderBlocked, match='readonly'):
        read_linked_balance(reader, 'live-1', record)
    assert reader.calls == []


def test_lost_transport_ack_stays_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_HOME', str(tmp_path / 'runtime'))
    snapshots = collect_snapshots(FakeReader(),A,NOW,sleep=lambda _:None)
    guard = LiveOrderGuard(A,tmp_path/'guard')
    guard.reserve('krstockOrderCashBuy',FIELDS,now=NOW,**snapshots)
    transport = FakeTransport(fail=True)
    with pytest.raises(TimeoutError):
        transport.submit('krstockOrderCashBuy', FIELDS)
    with pytest.raises(LiveOrderBlocked, match='unresolved'):
        LiveOrderGuard(A,tmp_path/'guard').reserve('krstockOrderCashBuy',{**FIELDS,'orr_pr':1399},now=NOW,**snapshots)
    assert len(transport.calls) == 1


def test_cancel_and_reconciliation_with_fake_transport(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_HOME', str(tmp_path / 'runtime'))
    snapshots = collect_snapshots(FakeReader(), A, NOW, sleep=lambda _:None)
    guard = LiveOrderGuard(A, tmp_path / 'guard')
    buy = guard.reserve('krstockOrderCashBuy', FIELDS, now=NOW, **snapshots)
    guard.acknowledge(buy, {'rsp_cd':'00048','rsp_msg':'매수주문이 완료되었습니다', 'Output_0':{'mkt_orr_no':42}})
    cancel = {'act_no':A, 'iem_cd':'114800', 'org_mkt_orr_no':42, 'all_pat_dit_cd':'1'}
    snapshots['history'] = {**snapshots['history'], 'rsp_cd':'00166', 'rsp_msg':'조회가 완료되었습니다.',
        'Output_0':[{'itg_orr_no':42,'iem_cd':'114800','orr_qty':1,'tot_cns_qty':0,
                     'ny_cns_qty':1,'can_qty':0,'sby_dit_cd_nm':'현금매수'}]}
    cancel_id = guard.reserve('krstockOrderCancel', cancel, now=NOW, **snapshots)
    guard.acknowledge(cancel_id, {'rsp_cd':'00192','rsp_msg':'취소주문이 완료되었습니다', 'Output_0':{'mkt_orr_no':43}})
    row={'itg_orr_no':42,'iem_cd':'114800','orr_qty':1,'tot_cns_qty':0,'ny_cns_qty':0,
         'can_qty':1,'sby_dit_cd_nm':'현금매수'}
    history = {**snapshots['history'], 'rsp_cd':'00166','rsp_msg':'조회가 완료되었습니다.','Output_0':[row]}
    assert guard.reconcile(buy, history) == {'filled':0, 'cancelled':1}
    with pytest.raises(LiveOrderBlocked):
        guard.reserve('krstockOrderCashBuy', {**FIELDS,'orr_pr':1399}, now=NOW, **snapshots)

def test_continued_page_uses_final_broker_status():
    reader=FakeReader()
    original=reader.call_tool
    counter={'n':0}
    def read(tool,args):
        if tool=='call_api' and args.get('operationId')=='krstockInquiryDailyOrderExecution':
            counter['n']+=1
            if counter['n']==1:
                return {'status':'ok','data':{'rsp_cd':'00218','rsp_msg':'계속 조회시 다음(연속조회) 버튼을 누르시기 바랍니다.','Output_0':[], '_nh_pagination':{'complete':False,'cts':'cursor','cts_flag':'Y'}}}
            return {'status':'ok','data':{'rsp_cd':'00166','rsp_msg':'조회가 완료되었습니다.','Output_0':[], '_nh_pagination':{'complete':True}}}
        return original(tool,args)
    reader.call_tool=read
    result=collect_snapshots(reader,A,NOW,sleep=lambda _:None)
    assert result['history']['rsp_cd']=='00166' and result['history']['_pagination_complete']


def test_rate_limit_is_retried_and_other_errors_are_not():
    import pytest
    from nh_live_broker import _request
    from src.tools.nhplug_live_guard import LiveOrderBlocked

    class Flaky:
        def __init__(self, errors):
            self.errors, self.calls = list(errors), 0

        def call_tool(self, tool, params):
            self.calls += 1
            if self.errors:
                return {'status': 'error', 'error': self.errors.pop(0)}
            return {'status': 'ok', 'data': {'rsp_cd': '00000'}}
    waits = []
    flaky = Flaky(['IGW42902 APP 호출 거래건수를 초과하였습니다.'] * 2)
    assert _request(flaky, 'call_api', {}, sleep=waits.append)['status'] == 'ok'
    assert (flaky.calls, waits) == (3, [0.5, 1.0])
    with pytest.raises(LiveOrderBlocked, match='rate limit'):
        _request(Flaky(['IGW42902'] * 5), 'call_api', {}, sleep=lambda s: None)
    # the same-query limit (IGW42903, 11-19) is retried too: the mirror's minute read and the order's own re-read of the
    # same history and balance can fall within a second of each other
    waits = []
    assert _request(Flaky(['IGW42903 API 호출 거래건수를 초과하였습니다.']), 'call_api', {}, sleep=waits.append)['status'] == 'ok'
    assert waits == [0.5]
    with pytest.raises(LiveOrderBlocked, match='rate limit'):
        _request(Flaky(['IGW42903'] * 5), 'call_api', {}, sleep=lambda s: None)
    other = Flaky(['IGW40043 unauthorized'])
    with pytest.raises(LiveOrderBlocked, match='request failed'):
        _request(other, 'call_api', {}, sleep=lambda s: None)
    assert other.calls == 1


def test_the_mirrors_per_minute_read_skips_quotes_and_the_account_list():
    """symbol=None is the live mirror's every-minute read: it uses neither the quotes nor the account list (review 3 M12)."""
    reader = FakeReader()
    snapshots = collect_snapshots(reader, A, NOW, sleep=lambda _: None, symbol=None)
    assert sorted(snapshots) == ['balance', 'history', 'pnl']
    assert [tool for tool, _ in reader.calls if tool != 'call_api'] == []


def test_a_second_page_waits_for_nhs_one_request_a_second():
    """Same NH limit on a live account (measured 11-19 on paper: a page asked within ~1 s of the previous one is refused
    with IGW42903, which _request does not retry, so the live cycle would BLOCK once the account lists >10 rows)."""
    from nh_live_broker import _pages
    clock = {'t': 0.0}
    starts = []

    class NH:
        def call_tool(self, tool, args):
            now = clock['t']
            refused = bool(starts) and now - starts[-1] < 1.0
            starts.append(now)
            clock['t'] += 0.27
            if refused:
                return {'status': 'error', 'error': 'IGW42903 API 호출 거래건수를 초과하였습니다.'}
            if 'cts' not in args:
                return {'status': 'ok', 'data': {'rsp_cd': '00218', 'rsp_msg': '계속 조회시 다음(연속조회) 버튼을 누르시기 바랍니다.',
                                                 'Output_1': [{'n': 1}], '_nh_pagination': {'complete': False, 'cts': 'c', 'cts_flag': 'Y'}}}
            return {'status': 'ok', 'data': {'rsp_cd': '00000', 'rsp_msg': '조회가 완료되었습니다.', 'Output_1': [{'n': 2}],
                                             '_nh_pagination': {'complete': True}}}
    def sleep(seconds):
        clock['t'] += seconds

    data = _pages(NH(), 'krstockInquiryBalance', {}, 'Output_1', clock=lambda: clock['t'], sleep=sleep)
    assert data['Output_1'] == [{'n': 1}, {'n': 2}] and len(starts) == 2 and starts[1] - starts[0] >= 1.0


def test_snapshots_pass_their_sleep_to_the_page_gap():
    """collect_snapshots(sleep=...) paces continuation pages with the caller's sleep, not a real 1.1 s (review 11-19)."""
    reader = FakeReader()
    original = reader.call_tool
    served = {'n': 0}

    def read(tool, args):
        if tool == 'call_api' and args.get('operationId') == 'krstockInquiryDailyOrderExecution':
            served['n'] += 1
            if served['n'] == 1:
                return {'status': 'ok', 'data': {'rsp_cd': '00218', 'rsp_msg': '계속 조회시 다음(연속조회) 버튼을 누르시기 바랍니다.',
                                                 'Output_0': [], '_nh_pagination': {'complete': False, 'cts': 'c', 'cts_flag': 'Y'}}}
            return {'status': 'ok', 'data': {'rsp_cd': '00166', 'rsp_msg': '조회가 완료되었습니다.', 'Output_0': [],
                                             '_nh_pagination': {'complete': True}}}
        return original(tool, args)
    reader.call_tool = read
    waits = []
    collect_snapshots(reader, A, NOW, sleep=waits.append, symbol=None)
    assert any(w > 0.5 for w in waits)  # the page gap went through the given sleep
