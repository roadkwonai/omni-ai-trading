"""Exercise the normal MCP adapter with a fake broker and real durable state."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastmcp.client.client import CallToolResult

from src.config.schema import MCPServerConfig
from src.tools.mcp import MCPServerAdapter
from src.tools.nhplug_order_guard import NHOrderGuard

ACCOUNT = 'paper-test-account'
BUY = {'operationId': 'krstockOrderCashBuy', 'input': {
    'act_no': ACCOUNT, 'iem_cd': '005930', 'orr_qty': 1, 'orr_pr': 180000,
    'nmn_pr_tp_cd': '01', 'orr_cnd_dit_cd': '00', 'ssl_nmn_pr_dit_cd': '00',
    'rmt_mkt_cd': 'KRX', 'sor_mkt_sli_yn': 'N',
}}


def result(data):
    return CallToolResult(content=[], structured_content=data, data=data, is_error=False, meta=None)


class Broker:
    def __init__(self, outcome):
        self.outcome = outcome
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def call_tool(self, name, arguments, **kwargs):
        self.sent.append((name, arguments))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        if callable(self.outcome):
            return self.outcome()
        return self.outcome


def adapter(broker, mode='paper', name=None):
    config = MCPServerConfig(command='python', args=[
        '/local/scripts/nhplug_mcp.py', '--mode', mode, '--account', ACCOUNT,
    ])
    return MCPServerAdapter(name or 'nhplug_' + mode, config, client_factory=lambda: broker)


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_HOME', str(tmp_path))


@pytest.mark.parametrize('failure', [TimeoutError('response lost'), ConnectionError('connection reset')])
def test_response_loss_blocks_recall_and_restart_even_with_changed_price(failure):
    broker = Broker(failure)
    assert adapter(broker).call_tool('call_api', BUY)['status'] == 'error'
    changed = {**BUY, 'input': {**BUY['input'], 'orr_pr': 180100}}
    retry = adapter(broker).call_tool('call_api', changed)
    assert retry['status'] == 'error'
    assert retry['error_type'] == 'NHOrderBlocked'
    assert len(broker.sent) == 1


def test_acknowledgement_does_not_allow_duplicate_after_restart():
    broker = Broker(result({'Output_0': {'mkt_orr_no': 42}, 'rsp_msg': '모의투자 매수주문이완료되었습니다.'}))
    first = adapter(broker).call_tool('call_api', BUY)
    assert first['order_safety']['state'] == 'acknowledged'
    second = adapter(broker).call_tool('call_api', BUY)
    assert second['error_type'] == 'NHOrderBlocked'
    assert len(broker.sent) == 1


def test_distinct_explicit_intents_allow_only_reconciled_repeats():
    broker = Broker(result({'Output_0': {'mkt_orr_no': 42}}))
    client = adapter(broker)
    first_id, second_id = 'a' * 32, 'b' * 32
    assert client.call_tool('call_api', BUY, order_intent_id=first_id)['status'] == 'ok'
    assert client.call_tool('call_api', BUY, order_intent_id=second_id)['error_type'] == 'NHOrderBlocked'
    from src.tools.nhplug_order_guard import nh_guard
    with nh_guard(client.server_name, client.server_config).database() as db:
        db.execute("UPDATE intents SET state='terminal'")
    assert client.call_tool('call_api', BUY, order_intent_id=second_id)['status'] == 'ok'
    changed = {**BUY, 'input': {**BUY['input'], 'orr_pr': 180100}}
    assert client.call_tool('call_api', changed, order_intent_id=first_id)['error_type'] == 'NHOrderBlocked'
    assert len(broker.sent) == 2


def test_simultaneous_calls_only_submit_once():
    entered, release = Event(), Event()

    def slow_ack():
        entered.set()
        assert release.wait(5)
        return result({'Output_0': {'mkt_orr_no': 42}})

    broker = Broker(slow_ack)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(adapter(broker).call_tool, 'call_api', BUY)
        assert entered.wait(5)
        try:
            second = pool.submit(adapter(broker).call_tool, 'call_api', BUY).result(timeout=5)
            assert second['error_type'] == 'NHOrderBlocked'
        finally:
            release.set()
        assert first.result(timeout=5)['status'] == 'ok'
    assert len(broker.sent) == 1


def test_account_owner_blocks_other_runners_but_allows_cancel_and_reads(tmp_path):
    import json
    cfg = MCPServerConfig(command='python', args=['nhplug_mcp.py', '--mode', 'paper', '--account', ACCOUNT])
    scope = NHOrderGuard(cfg.args).scope
    root = tmp_path / 'nhplug'
    root.mkdir()
    (root / 'paper-account-owners.json').write_text(json.dumps({'version': 1, 'accounts': {scope: 'nhplug_paper_isolated'}}))
    blocked_broker = Broker(result({'Output_0': {'mkt_orr_no': 42}}))
    assert adapter(blocked_broker, name='nhplug_paper').call_tool('call_api', BUY)['error_type'] == 'NHOrderBlocked'
    assert not blocked_broker.sent
    owner_broker = Broker(result({'Output_0': {'mkt_orr_no': 42}}))
    assert adapter(owner_broker, name='nhplug_paper_isolated').call_tool('call_api', BUY)['status'] == 'ok'
    cancel = {'operationId': 'krstockOrderCancel', 'input': {
        'act_no': ACCOUNT, 'iem_cd': '005930', 'org_mkt_orr_no': 42, 'all_pat_dit_cd': '1',
    }}
    assert adapter(blocked_broker, name='nhplug_paper').call_tool('call_api', cancel)['status'] == 'ok'
    assert query(blocked_broker, [execution_row()])['status'] == 'ok'


@pytest.mark.parametrize('contents', ['not json', '{}', '{"version":true,"accounts":{}}',
                                     '{"version":1,"accounts":{"bad":"nhplug_paper_isolated"}}',
                                     '{"version":1,"accounts":{"' + 'a' * 64 + '":"other"}}'])
def test_malformed_owner_registry_fails_closed_for_new_orders(tmp_path, contents):
    root = tmp_path / 'nhplug'
    root.mkdir()
    (root / 'paper-account-owners.json').write_text(contents)
    broker = Broker(result({'Output_0': {'mkt_orr_no': 42}}))
    assert adapter(broker, name='nhplug_paper_isolated').call_tool('call_api', BUY)['error_type'] == 'NHOrderBlocked'
    assert broker.sent == []


def test_owner_registry_does_not_restrict_unregistered_accounts(tmp_path):
    import json
    root = tmp_path / 'nhplug'
    root.mkdir()
    (root / 'paper-account-owners.json').write_text(json.dumps({'version': 1, 'accounts': {'a' * 64: 'nhplug_paper_isolated'}}))
    broker = Broker(result({'Output_0': {'mkt_orr_no': 42}}))
    assert adapter(broker, name='nhplug_paper').call_tool('call_api', BUY)['status'] == 'ok'


def test_signal_expiring_during_client_connection_aborts_without_transport(tmp_path):
    import sqlite3
    class SlowConnectBroker(Broker):
        async def __aenter__(self):
            clock[0] += 2
            return self
    clock = [59]
    broker = SlowConnectBroker(result({'Output_0': {'mkt_orr_no': 42}}))
    check = lambda: clock[0] < 60
    response = adapter(broker).call_tool('call_api', BUY, order_intent_id='a' * 32,
                                         pre_reserve_check=check, pre_send_check=check, order_guard_deadline_ms=2000000000000)
    assert response['error_type'] == 'NHPreSendCheckFailed'
    assert response['order_safety']['state'] == 'aborted_no_send'
    assert broker.sent == []
    with sqlite3.connect(tmp_path / 'nhplug' / 'orders.sqlite3') as db:
        assert db.execute('select state from intents').fetchone() == ('terminal',)
        assert db.execute('select count(*) from pre_send_aborts').fetchone() == (1,)
    next_broker = Broker(result({'Output_0': {'mkt_orr_no': 43}}))
    assert adapter(next_broker).call_tool('call_api', BUY, order_intent_id='b' * 32)['status'] == 'ok'


def test_signal_expiring_during_buy_preflight_never_reserves_or_sends(monkeypatch, tmp_path):
    clock = [59]
    def slow_preflight(*args):
        clock[0] += 2
    monkeypatch.setattr(MCPServerAdapter, '_nh_buy_preflight', slow_preflight)
    broker = Broker(result({'Output_0': {'mkt_orr_no': 42}}))
    check = lambda: clock[0] < 60
    response = adapter(broker).call_tool('call_api', BUY, order_intent_id='a' * 32,
                                         pre_reserve_check=check, pre_send_check=check, order_guard_deadline_ms=2000000000000)
    assert response['order_safety']['state'] == 'aborted_no_send'
    assert broker.sent == []
    assert not (tmp_path / 'nhplug' / 'orders.sqlite3').exists()


@pytest.mark.parametrize('checks', [{'pre_reserve_check': lambda: True}, {'pre_send_check': lambda: True}])
def test_signal_checks_must_be_paired(checks):
    broker = Broker(result({'Output_0': {'mkt_orr_no': 42}}))
    response = adapter(broker).call_tool('call_api', BUY, **checks)
    assert response['error_type'] == 'ValueError'
    assert broker.sent == []


def test_transport_failure_even_if_same_exception_type_stays_uncertain(tmp_path):
    import sqlite3
    from src.tools.mcp import NHPreSendCheckFailed
    broker = Broker(NHPreSendCheckFailed('remote failure'))
    response = adapter(broker).call_tool('call_api', BUY, order_intent_id='a' * 32,
                                         pre_reserve_check=lambda: True, pre_send_check=lambda: True, order_guard_deadline_ms=2000000000000)
    assert response['order_safety']['state'] == 'uncertain'
    assert len(broker.sent) == 1
    with sqlite3.connect(tmp_path / 'nhplug' / 'orders.sqlite3') as db:
        assert db.execute('select state from intents').fetchone() == ('uncertain',)


def test_failed_abort_record_stays_uncertain(monkeypatch, tmp_path):
    import sqlite3
    def fail_abort(*args):
        raise OSError('journal unavailable')
    monkeypatch.setattr(NHOrderGuard, 'abort_before_send', fail_abort)
    broker = Broker(result({'Output_0': {'mkt_orr_no': 42}}))
    response = adapter(broker).call_tool('call_api', BUY, order_intent_id='a' * 32,
                                         pre_reserve_check=lambda: True, pre_send_check=lambda: False, order_guard_deadline_ms=2000000000000)
    assert response['order_safety']['state'] == 'uncertain'
    assert broker.sent == []
    with sqlite3.connect(tmp_path / 'nhplug' / 'orders.sqlite3') as db:
        assert db.execute('select state from intents').fetchone() == ('uncertain',)


@pytest.mark.parametrize('code', ['NH_SIGNAL_EXPIRED', 'NH_STOP_REQUESTED'])
def test_guarded_server_proven_no_send_marker_is_audited(tmp_path, code):
    import sqlite3
    class GuardedBroker(Broker):
        async def call_tool(self, name, arguments, **kwargs):
            self.sent.append((name, arguments))
            marker = {'version': 1, 'code': code, 'no_send': True, **arguments['order_guard']}
            return result({'_nh_order_guard': marker})
    broker = GuardedBroker(None)
    response = adapter(broker).call_tool('call_api', BUY, order_intent_id='a' * 32,
        pre_reserve_check=lambda: True, pre_send_check=lambda: True, order_guard_deadline_ms=2000000000000)
    assert response['error_type'] == 'NHPreSendCheckFailed'
    assert response['order_safety']['state'] == 'aborted_no_send'
    assert len(broker.sent) == 1 and broker.sent[0][0] == 'call_api_guarded'
    assert broker.sent[0][1]['input'] == BUY['input']
    assert broker.sent[0][1]['order_guard']['intent_id'] == 'a' * 32
    with sqlite3.connect(tmp_path / 'nhplug' / 'orders.sqlite3') as db:
        assert db.execute('SELECT state FROM intents').fetchone() == ('terminal',)
        assert db.execute('SELECT COUNT(*) FROM pre_send_aborts').fetchone() == (1,)


@pytest.mark.parametrize('alter', ['intent_id', 'payload_sha256', 'expires_at_epoch_ms', 'code', 'no_send', 'version', 'extra', 'order_number'])
def test_unverified_guarded_marker_leaves_order_uncertain(tmp_path, alter):
    import sqlite3
    class InvalidMarkerBroker(Broker):
        async def call_tool(self, name, arguments, **kwargs):
            self.sent.append((name, arguments))
            marker = {'version': 1, 'code': 'NH_SIGNAL_EXPIRED', 'no_send': True, **arguments['order_guard']}
            if alter == 'extra':
                marker['extra'] = 'untrusted'
            elif alter == 'order_number':
                return result({'_nh_order_guard': marker, 'Output_0': {'mkt_orr_no': 42}})
            else:
                marker[alter] = {'intent_id': 'f' * 32, 'payload_sha256': 'f' * 64,
                    'expires_at_epoch_ms': 1, 'code': 'unknown', 'no_send': False, 'version': True}[alter]
            return result({'_nh_order_guard': marker})
    broker = InvalidMarkerBroker(None)
    response = adapter(broker).call_tool('call_api', BUY, order_intent_id='a' * 32,
        pre_reserve_check=lambda: True, pre_send_check=lambda: True, order_guard_deadline_ms=2000000000000)
    assert response['status'] == 'error'
    assert response['order_safety']['state'] == 'uncertain'
    with sqlite3.connect(tmp_path / 'nhplug' / 'orders.sqlite3') as db:
        assert db.execute('SELECT state FROM intents').fetchone() == ('uncertain',)
        assert db.execute('SELECT COUNT(*) FROM pre_send_aborts').fetchone() == (0,)


def test_unpatched_mcp_server_never_receives_unguarded_order(tmp_path):
    import sqlite3
    broker = Broker(RuntimeError('tool not found'))
    response = adapter(broker).call_tool('call_api', BUY, order_intent_id='a' * 32,
        pre_reserve_check=lambda: True, pre_send_check=lambda: True, order_guard_deadline_ms=2000000000000)
    assert [call[0] for call in broker.sent] == ['call_api_guarded']
    assert response['order_safety']['state'] == 'uncertain'
    with sqlite3.connect(tmp_path / 'nhplug' / 'orders.sqlite3') as db:
        assert db.execute('SELECT state FROM intents').fetchone() == ('uncertain',)


@pytest.mark.parametrize('op', ['krstockOrderCashBuy', 'krstockOrderCashSell', 'krstockOrderCancel'])
def test_readonly_orders_blocked_before_transport(op):
    broker = Broker(result({}))
    response = adapter(broker, mode='readonly').call_tool('call_api', {**BUY, 'operationId': op})
    assert response['status'] == 'error'
    assert response['error_type'] == 'NHOrderBlocked'
    assert broker.sent == []


def execution_row(**changes):
    return {'itg_orr_no': 42, 'iem_cd': '005930', 'orr_qty': 1,
            'tot_cns_qty': 1, 'ny_cns_qty': 0, 'can_qty': 0, **changes}


def query(broker, rows, day=None, account=ACCOUNT):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    broker.outcome = result({'Output_0': rows, 'rsp_msg': '모의투자 마지막 자료입니다'})
    return adapter(broker).call_tool('call_api', {'operationId': 'krstockInquiryDailyOrderExecution', 'input': {
        'act_no': account, 'orr_dt': day or datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y%m%d'),
        'orr_mkt_cd': '00', 'ost_cns_dit': '0',
    }})


def test_terminal_execution_unlocks_next_order_but_never_replays_same_intent():
    broker = Broker(result({'Output_0': {'mkt_orr_no': 42}}))
    adapter(broker).call_tool('call_api', BUY)
    assert query(broker, [execution_row()])['status'] == 'ok'
    assert adapter(broker).call_tool('call_api', BUY)['error_type'] == 'NHOrderBlocked'
    broker.outcome = result({'Output_0': {'mkt_orr_no': 43}})
    sell = {**BUY, 'operationId': 'krstockOrderCashSell'}
    assert adapter(broker).call_tool('call_api', sell)['order_safety']['state'] == 'acknowledged'
    assert len(broker.sent) == 3


@pytest.mark.parametrize('rows,day,account', [
    ([], None, ACCOUNT),
    ([execution_row(tot_cns_qty=0, ny_cns_qty=1)], None, ACCOUNT),
    ([execution_row(iem_cd='000660')], None, ACCOUNT),
    ([execution_row(itg_orr_no=999)], None, ACCOUNT),
    ([execution_row(tot_cns_qty=0)], None, ACCOUNT),
    ([execution_row()], '19990101', ACCOUNT),
    ([execution_row()], None, 'different-account'),
    ([execution_row(), execution_row()], None, ACCOUNT),
])
def test_incomplete_or_unrelated_query_never_releases_order(rows, day, account):
    broker = Broker(result({'Output_0': {'mkt_orr_no': 42}}))
    adapter(broker).call_tool('call_api', BUY)
    query(broker, rows, day, account)
    changed = {**BUY, 'input': {**BUY['input'], 'orr_pr': 180100}}
    assert adapter(broker).call_tool('call_api', changed)['error_type'] == 'NHOrderBlocked'
    assert len(broker.sent) == 2


@pytest.mark.parametrize('data', [{}, {'rsp_cd': 'ERR', 'rsp_msg': '주문 거부'}, {'Output_0': {'mkt_orr_no': 0}}])
def test_malformed_or_rejected_ack_keeps_unknown_submission_blocked(data):
    broker = Broker(result(data))
    first = adapter(broker).call_tool('call_api', BUY)
    assert first['status'] == 'error'
    assert first['order_safety']['state'] == 'uncertain'
    assert adapter(broker).call_tool('call_api', BUY)['error_type'] == 'NHOrderBlocked'
    assert len(broker.sent) == 1


def test_cancellation_allowed_while_pending_and_reconciles_original_order():
    broker = Broker(result({'Output_0': {'mkt_orr_no': 42}}))
    adapter(broker).call_tool('call_api', BUY)
    cancel = {'operationId': 'krstockOrderCancel', 'input': {
        'act_no': ACCOUNT, 'iem_cd': '005930', 'org_mkt_orr_no': 42, 'all_pat_dit_cd': '1',
    }}
    broker.outcome = result({'Output_0': {'mkt_orr_no': 44}})
    assert adapter(broker).call_tool('call_api', cancel)['status'] == 'ok'
    query(broker, [execution_row(tot_cns_qty=0, can_qty=1)])
    changed = {**BUY, 'input': {**BUY['input'], 'orr_pr': 180100}}
    broker.outcome = result({'Output_0': {'mkt_orr_no': 45}})
    assert adapter(broker).call_tool('call_api', changed)['status'] == 'ok'


def test_crash_after_broker_accepts_before_response_survives_process_restart(tmp_path):
    import json
    import os
    import subprocess
    import sys
    code = '''
import os
from pathlib import Path
from src.config.schema import MCPServerConfig
from src.tools.mcp import MCPServerAdapter
import json
class Broker:
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def call_tool(self, *args, **kwargs):
        Path(os.environ['BROKER_MARKER']).write_text('accepted once')
        os._exit(23)
config = MCPServerConfig(command='python', args=['nhplug_mcp.py', '--mode', 'paper', '--account', 'paper-test-account'])
MCPServerAdapter('nhplug_paper', config, client_factory=Broker).call_tool('call_api', json.loads(os.environ['ORDER']))
'''
    marker = tmp_path / 'broker-marker'
    env = {**os.environ, 'BROKER_MARKER': str(marker), 'ORDER': json.dumps(BUY)}
    process = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, timeout=20)
    assert process.returncode == 23, process.stderr.decode()
    assert marker.read_text() == 'accepted once'
    broker = Broker(result({'Output_0': {'mkt_orr_no': 42}}))
    assert adapter(broker).call_tool('call_api', BUY)['error_type'] == 'NHOrderBlocked'
    assert broker.sent == []
    assert ACCOUNT.encode() not in (tmp_path / 'nhplug/orders.sqlite3').read_bytes()


def test_storage_failure_blocks_before_transport(tmp_path):
    folder = tmp_path / 'nhplug'
    folder.mkdir()
    (folder / 'orders.sqlite3').write_bytes(b'broken database')
    broker = Broker(result({}))
    assert adapter(broker).call_tool('call_api', BUY)['status'] == 'error'
    assert broker.sent == []


def test_renamed_nh_launcher_still_guarded():
    broker = Broker(TimeoutError('lost'))
    adapter(broker, name='renamed_namuh').call_tool('call_api', BUY)
    assert adapter(broker, name='renamed_namuh').call_tool('call_api', BUY)['error_type'] == 'NHOrderBlocked'
    assert len(broker.sent) == 1


def test_a_continued_paper_page_still_settles_its_rows():
    # 11/13: the day's history passed one page; the filled order sat on the XA109 page and the guard kept it open
    from datetime import datetime
    from zoneinfo import ZoneInfo
    broker = Broker(result({'Output_0': {'mkt_orr_no': 42}}))
    adapter(broker).call_tool('call_api', BUY)
    broker.outcome = result({'Output_0': [execution_row()], 'rsp_cd': 'XA109', 'rsp_msg': '모의투자 조회가 계속 됩니다. 다음 버튼을 누르세요'})
    adapter(broker).call_tool('call_api', {'operationId': 'krstockInquiryDailyOrderExecution', 'input': {
        'act_no': ACCOUNT, 'orr_dt': datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y%m%d'),
    }})
    changed = {**BUY, 'input': {**BUY['input'], 'orr_pr': 180100}}
    broker.outcome = result({'Output_0': {'mkt_orr_no': 43}})
    assert adapter(broker).call_tool('call_api', changed)['order_safety']['state'] == 'acknowledged'


@pytest.mark.parametrize('mode,message', [('readonly', '모의투자 마지막 자료입니다'), ('paper', '계좌 조회 오류'), ('paper', '조회가 완료되지 않았습니다')])
def test_nonpaper_or_failed_query_cannot_unlock_paper_order(mode, message):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    broker = Broker(result({'Output_0': {'mkt_orr_no': 42}}))
    adapter(broker).call_tool('call_api', BUY)
    broker.outcome = result({'Output_0': [execution_row()], 'rsp_msg': message})
    adapter(broker, mode=mode).call_tool('call_api', {'operationId': 'krstockInquiryDailyOrderExecution', 'input': {
        'act_no': ACCOUNT, 'orr_dt': datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y%m%d'),
    }})
    changed = {**BUY, 'input': {**BUY['input'], 'orr_pr': 180100}}
    assert adapter(broker).call_tool('call_api', changed)['error_type'] == 'NHOrderBlocked'
    assert len(broker.sent) == 2


def test_uncertain_intent_does_not_expire_at_midnight(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from unittest.mock import Mock
    import src.tools.nhplug_order_guard as guard
    clock = Mock()
    clock.now.return_value = datetime(2026, 11, 6, 15, tzinfo=ZoneInfo('Asia/Seoul'))
    monkeypatch.setattr(guard, 'datetime', clock)
    broker = Broker(TimeoutError('lost'))
    adapter(broker).call_tool('call_api', BUY)
    clock.now.return_value = datetime(2026, 11, 9, 9, tzinfo=ZoneInfo('Asia/Seoul'))
    assert adapter(broker).call_tool('call_api', BUY)['error_type'] == 'NHOrderBlocked'
    assert len(broker.sent) == 1


def test_agent_tool_wrapper_preserves_guard_across_new_wrapper_instances():
    import json
    from src.tools.mcp import MCPRemoteTool, MCPRemoteToolSpec
    broker = Broker(result({'Output_0': {'mkt_orr_no': 42}}))
    spec = MCPRemoteToolSpec(server_name='nhplug_paper', remote_name='call_api',
        local_name='mcp_nhplug_paper_call_api', description='test', parameters={
            'type': 'object', 'properties': {'operationId': {'type': 'string'}, 'input': {'type': 'object'}},
        })
    first = json.loads(MCPRemoteTool(adapter(broker), spec).execute(**BUY))
    assert first['order_safety']['state'] == 'acknowledged'
    second = json.loads(MCPRemoteTool(adapter(broker), spec).execute(**BUY))
    assert second['error_type'] == 'NHOrderBlocked'
    assert len(broker.sent) == 1


def test_swing_policy_blocks_oversized_buy_before_transport(tmp_path, monkeypatch):
    import json
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from unittest.mock import Mock
    import src.tools.nhplug_order_guard as guard
    folder = tmp_path / 'nhplug'
    folder.mkdir()
    (folder / 'swing-policy.json').write_text(json.dumps({'enabled': True}))
    clock = Mock()
    clock.now.return_value = datetime(2026, 11, 6, 10, tzinfo=ZoneInfo('Asia/Seoul'))
    monkeypatch.setattr(guard, 'datetime', clock)
    broker = Broker(result({'Output_0': {'mkt_orr_no': 42}}))
    too_big = {**BUY, 'input': {**BUY['input'], 'orr_qty': 20}}
    assert adapter(broker).call_tool('call_api', too_big)['error_type'] == 'NHOrderBlocked'
    assert not broker.sent


def test_swing_limits_remain_after_terminal_order(tmp_path, monkeypatch):
    import json
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from unittest.mock import Mock
    import src.tools.nhplug_order_guard as guard
    folder = tmp_path / 'nhplug'
    folder.mkdir()
    (folder / 'swing-policy.json').write_text(json.dumps({'enabled': True}))
    clock = Mock()
    clock.now.return_value = datetime(2026, 11, 6, 10, tzinfo=ZoneInfo('Asia/Seoul'))
    monkeypatch.setattr(guard, 'datetime', clock)
    broker = Broker(result({'Output_0': {'mkt_orr_no': 42}}))
    buy = {**BUY, 'input': {**BUY['input'], 'orr_qty': 15, 'orr_pr': 200000}}
    assert adapter(broker).call_tool('call_api', buy)['status'] == 'ok'
    query(broker, [execution_row(orr_qty=15, tot_cns_qty=15)], day='20261106')
    broker.outcome = result({'Output_0': {'mkt_orr_no': 43}})
    second = {**buy, 'input': {**buy['input'], 'iem_cd': '000660'}}
    assert adapter(broker).call_tool('call_api', second)['status'] == 'ok'
    query(broker, [execution_row(itg_orr_no=43, iem_cd='000660', orr_qty=15, tot_cns_qty=15)], day='20261106')
    third = {**buy, 'input': {**buy['input'], 'iem_cd': '035420', 'orr_qty': 1}}
    assert adapter(broker).call_tool('call_api', third)['error_type'] == 'NHOrderBlocked'


def test_swing_symbol_cap_counts_only_todays_new_symbols(tmp_path, monkeypatch):
    import json
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from unittest.mock import Mock
    import src.tools.nhplug_order_guard as guard
    folder = tmp_path / 'nhplug'
    folder.mkdir()
    (folder / 'swing-policy.json').write_text(json.dumps({'enabled': True}))
    clock = Mock()
    monkeypatch.setattr(guard, 'datetime', clock)
    broker = Broker(result({'Output_0': {'mkt_orr_no': 1}}))
    def buy(code, number, day, price=1000):
        clock.now.return_value = datetime(2026, 11, day, 10, tzinfo=ZoneInfo('Asia/Seoul'))
        broker.outcome = result({'Output_0': {'mkt_orr_no': number}})
        order = {**BUY, 'input': {**BUY['input'], 'iem_cd': code, 'orr_qty': 1, 'orr_pr': price}}
        response = adapter(broker).call_tool('call_api', order)
        if response['status'] == 'ok':
            query(broker, [execution_row(itg_orr_no=number, iem_cd=code, orr_qty=1, tot_cns_qty=1)], day=f'202611{day:02d}')
        return response
    codes = ['000001', '000002', '000003', '000004', '000005']
    for number, code in enumerate(codes, 1):
        assert buy(code, number, 6)['status'] == 'ok'
    assert buy('000006', 6, 6)['error_type'] == 'NHOrderBlocked'
    assert buy('000001', 7, 6, price=1001)['status'] == 'ok'  # already bought today (distinct order fields)
    assert buy('000006', 8, 9)['status'] == 'ok'  # a new day starts a new count


def test_proven_no_send_does_not_consume_swing_buy_limit(tmp_path, monkeypatch):
    import json
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from unittest.mock import Mock
    import src.tools.nhplug_order_guard as guard
    folder = tmp_path / 'nhplug'
    folder.mkdir()
    (folder / 'swing-policy.json').write_text(json.dumps({'enabled': True}))
    clock = Mock()
    clock.now.return_value = datetime(2026, 11, 6, 10, tzinfo=ZoneInfo('Asia/Seoul'))
    monkeypatch.setattr(guard, 'datetime', clock)
    broker = Broker(result({'Output_0': {'mkt_orr_no': 42}}))
    buy = {**BUY, 'input': {**BUY['input'], 'orr_qty': 15, 'orr_pr': 200000}}
    aborted = adapter(broker).call_tool('call_api', buy, order_intent_id='a' * 32,
                                        pre_reserve_check=lambda: True, pre_send_check=lambda: False, order_guard_deadline_ms=2000000000000)
    assert aborted['order_safety']['state'] == 'aborted_no_send'
    assert broker.sent == []
    assert adapter(broker).call_tool('call_api', buy, order_intent_id='b' * 32)['status'] == 'ok'
    query(broker, [execution_row(orr_qty=15, tot_cns_qty=15)], day='20261106')
    broker.outcome = result({'Output_0': {'mkt_orr_no': 43}})
    second = {**buy, 'input': {**buy['input'], 'iem_cd': '000660'}}
    assert adapter(broker).call_tool('call_api', second, order_intent_id='c' * 32)['status'] == 'ok'
    query(broker, [execution_row(itg_orr_no=43, iem_cd='000660', orr_qty=15, tot_cns_qty=15)], day='20261106')
    third = {**buy, 'input': {**buy['input'], 'iem_cd': '035420', 'orr_qty': 1}}
    assert adapter(broker).call_tool('call_api', third, order_intent_id='d' * 32)['error_type'] == 'NHOrderBlocked'
    with guard.NHOrderGuard(adapter(broker).server_config.args).database() as db:
        assert db.execute('SELECT COUNT(*) FROM pre_send_aborts').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM swing_buys').fetchone()[0] == 3


def test_swing_policy_rejects_market_orders(tmp_path, monkeypatch):
    import json
    folder = tmp_path / 'nhplug'
    folder.mkdir()
    (folder / 'swing-policy.json').write_text(json.dumps({'enabled': True}))
    broker = Broker(result({'Output_0': {'mkt_orr_no': 42}}))
    market = {**BUY, 'input': {**BUY['input'], 'nmn_pr_tp_cd': '05'}}
    assert adapter(broker).call_tool('call_api', market)['error_type'] == 'NHOrderBlocked'
    assert not broker.sent


def test_paper_halt_blocks_buy_but_not_sell_or_cancel(tmp_path):
    import json
    folder = tmp_path / 'nhplug'
    folder.mkdir()
    (folder / 'swing-policy.json').write_text(json.dumps({'enabled': True}))
    (folder / 'PAPER_HALT').touch()
    broker = Broker(result({'Output_0': {'mkt_orr_no': 42}}))
    assert adapter(broker).call_tool('call_api', BUY)['error_type'] == 'NHOrderBlocked'
    sell = {**BUY, 'operationId': 'krstockOrderCashSell', 'input': {**BUY['input'], 'nmn_pr_tp_cd': '05'}}
    assert adapter(broker).call_tool('call_api', sell)['status'] == 'ok'
    cancel = {'operationId': 'krstockOrderCancel', 'input': {
        'act_no': ACCOUNT, 'iem_cd': '005930', 'org_mkt_orr_no': 42, 'all_pat_dit_cd': '1'}}
    assert adapter(broker).call_tool('call_api', cancel)['status'] == 'ok'


def test_preflight_failure_blocks_before_order_intent(tmp_path, monkeypatch):
    import json
    folder = tmp_path / 'nhplug'
    folder.mkdir()
    (folder / 'swing-policy.json').write_text(json.dumps({'enabled': True, 'krx_session_check': True, 'auto_loss_stop': True}))
    broker = Broker(result({'rsp_cd': 'bad'}))
    response = adapter(broker).call_tool('call_api', BUY)
    assert response['error_type'] == 'NHOrderBlocked'
    assert len(broker.sent) == 2
    assert broker.sent[0][0] == 'get_stock_price'
    assert not (folder / 'orders.sqlite3').exists()


def test_buy_preflight_reads_fresh_quote_and_loss_before_order(tmp_path, monkeypatch):
    import json
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from unittest.mock import Mock
    import src.tools.nhplug_order_guard as guard
    import src.tools.nhplug_calendar as calendar
    import src.tools.nhplug_loss as loss
    folder = tmp_path / 'nhplug'
    folder.mkdir()
    (folder / 'swing-policy.json').write_text(json.dumps({'enabled': True, 'krx_session_check': True, 'auto_loss_stop': True}))
    clock = Mock()
    clock.now.return_value = datetime(2026, 11, 6, 10, tzinfo=ZoneInfo('Asia/Seoul'))
    monkeypatch.setattr(guard, 'datetime', clock)
    monkeypatch.setattr(calendar, 'check_session', lambda now, snap, previous: {'allowed': True})
    monkeypatch.setattr(loss, 'check_loss', lambda snap, now, scope: {'allowed': True})
    class MultiBroker(Broker):
        async def call_tool(self, name, arguments, **kwargs):
            self.sent.append((name, arguments))
            if name == 'get_stock_price': return result({'Output_0': {'iem_cd': '005930'}})
            if arguments['operationId'] == 'krstockInquiryRealizedPnl': return result({'Output_0': {}})
            return result({'Output_0': {'mkt_orr_no': 42}})
    broker = MultiBroker(None)
    assert adapter(broker).call_tool('call_api', BUY)['status'] == 'ok'
    assert [name for name, _ in broker.sent] == ['get_stock_price', 'get_stock_price', 'call_api', 'call_api']
    assert broker.sent[2][1]['input']['act_no'] == ACCOUNT


def test_guarded_paper_buy_preflight_launches_readonly_without_guarded_flag(tmp_path, monkeypatch):
    import json
    import subprocess
    import sys
    from pathlib import Path
    import src.tools.nhplug_calendar as calendar
    import src.tools.nhplug_loss as loss

    folder = tmp_path / 'nhplug'
    folder.mkdir()
    (folder / 'swing-policy.json').write_text(json.dumps({'enabled': True, 'krx_session_check': True, 'auto_loss_stop': True}))
    credentials = tmp_path / 'credentials'
    credentials.write_text("APP_KEY='fake-key'\nAPP_SECRET='fake-secret'\n")
    server = tmp_path / 'integrations' / 'nhplug-mcp' / 'dist' / 'index.js'
    server.parent.mkdir(parents=True)
    server.touch()
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    node = bin_dir / 'node'
    node.write_text('#!/bin/sh\n[ "$NHPLUG_BASE_URL" = "https://api.nhplug.com:8443" ] && '
                    '[ "$NHPLUG_ENABLE_TRADING" = "false" ] && '
                    '[ -z "${NHPLUG_PAPER_ETF_STOP_PATH+x}" ]\n')
    node.chmod(0o755)
    monkeypatch.setenv('PATH', str(bin_dir))
    monkeypatch.setattr('time.sleep', lambda _: None)
    checks = []
    monkeypatch.setattr(calendar, 'check_session', lambda *args: checks.append('session') or {'allowed': True})
    monkeypatch.setattr(loss, 'check_loss', lambda *args: checks.append('loss') or {'allowed': True})

    async def fake_call(self, name, arguments):
        if name == 'get_stock_price':
            assert self.server_name == 'nhplug_readonly'
            completed = subprocess.run([sys.executable, *self.server_config.args], capture_output=True, timeout=10)
            assert completed.returncode == 0, completed.stderr.decode()
            assert '--guarded-paper' not in self.server_config.args
            assert self.server_config.args.count('--mode') == 1
            assert self.server_config.args[self.server_config.args.index('--mode') + 1] == 'readonly'
            return result({'Output_0': {'iem_cd': '005930'}})
        assert name == 'call_api' and arguments['operationId'] == 'krstockInquiryRealizedPnl'
        assert '--guarded-paper' in self.server_config.args
        return result({'Output_0': {}})

    monkeypatch.setattr(MCPServerAdapter, '_call_tool', fake_call)
    launcher = Path(__file__).resolve().parents[2] / 'scripts' / 'nhplug_mcp.py'
    args = [str(launcher), '--mode', 'paper', '--env-file', str(credentials), '--account', ACCOUNT, '--guarded-paper']
    config = MCPServerConfig(command=sys.executable, args=args)
    client = MCPServerAdapter('nhplug_paper_isolated', config)
    client._nh_buy_preflight(NHOrderGuard(args), BUY)
    assert checks == ['session', 'loss']
    assert '--guarded-paper' in client.server_config.args
    assert client.server_config.args[client.server_config.args.index('--mode') + 1] == 'paper'


def test_loss_preflight_denial_prevents_submission(tmp_path, monkeypatch):
    import json
    import src.tools.nhplug_loss as loss
    folder = tmp_path / 'nhplug'
    folder.mkdir()
    (folder / 'swing-policy.json').write_text(json.dumps({'enabled': True, 'auto_loss_stop': True}))
    monkeypatch.setattr(loss, 'check_loss', lambda snapshot, now, scope: {'allowed': False, 'reason': 'daily loss limit reached'})
    broker = Broker(result({'Output_0': {}}))
    response = adapter(broker).call_tool('call_api', BUY)
    assert response['error_type'] == 'NHOrderBlocked'
    assert len(broker.sent) == 1 and broker.sent[0][1]['operationId'] == 'krstockInquiryRealizedPnl'
    assert not (folder / 'orders.sqlite3').exists()


def test_real_loss_latch_blocks_buy_after_restart_even_if_loss_recovers(tmp_path):
    import json
    folder = tmp_path / 'nhplug'
    folder.mkdir()
    (folder / 'swing-policy.json').write_text(json.dumps({'enabled': True, 'auto_loss_stop': True}))
    def snapshot(realized):
        return result({'rsp_cd': '00000', 'rsp_msg': '조회가 완료되었습니다',
                       'Output_0': {'sby_pls_amt': realized, 'sdr_xps': 0, 'eal_pls_amt': 0}})
    broker = Broker(snapshot(-300000))
    first = adapter(broker).call_tool('call_api', BUY)
    assert first['error_type'] == 'NHOrderBlocked'
    assert len(broker.sent) == 1 and broker.sent[0][1]['operationId'] == 'krstockInquiryRealizedPnl'
    assert not (folder / 'orders.sqlite3').exists()
    broker.outcome = snapshot(0)
    second = adapter(broker).call_tool('call_api', BUY)
    assert second['error_type'] == 'NHOrderBlocked'
    assert len(broker.sent) == 2
    assert not (folder / 'orders.sqlite3').exists()


def _nxt_clock(monkeypatch, day=12, hour=16, minute=5):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from unittest.mock import Mock
    import src.tools.nhplug_order_guard as guard
    clock = Mock()
    clock.now.return_value = datetime(2026, 11, day, hour, minute, tzinfo=ZoneInfo('Asia/Seoul'))
    monkeypatch.setattr(guard, 'datetime', clock)
    return guard


def _nxt_setup(tmp_path, max_price=160000):
    import json
    folder = tmp_path / 'nhplug'
    folder.mkdir(exist_ok=True)
    (folder / 'swing-policy.json').write_text(json.dumps({'enabled': True}))  # the 09:10-15:00 window applies
    if max_price:
        (folder / 'paper-nxt-test.json').write_text(json.dumps({'day': '20261112', 'symbol': '005930', 'max_price': max_price}))


NXT_BUY = {**BUY, 'input': {**BUY['input'], 'orr_pr': 150000, 'rmt_mkt_cd': 'NXT'}}


def test_nxt_test_order_is_allowed_once_with_the_token_in_the_window(tmp_path, monkeypatch):
    _nxt_clock(monkeypatch)
    _nxt_setup(tmp_path)
    broker = Broker(result({'Output_0': {'mkt_orr_no': 7}}))
    assert adapter(broker).call_tool('call_api', NXT_BUY)['status'] == 'ok'
    assert len(broker.sent) == 1
    assert not (tmp_path / 'nhplug' / 'paper-nxt-test.json').exists()  # the token is spent
    again = {**NXT_BUY, 'input': {**NXT_BUY['input'], 'orr_pr': 149000}}
    assert adapter(broker).call_tool('call_api', again)['error_type'] == 'NHOrderBlocked'
    assert len(broker.sent) == 1


@pytest.mark.parametrize('day,hour,price,token', [(13, 16, 150000, 160000), (12, 9, 150000, 160000),
                                                  (12, 16, 170000, 160000), (12, 16, 150000, None)])
def test_nxt_order_blocked_on_another_day_time_higher_price_or_without_token(tmp_path, monkeypatch, day, hour, price, token):
    _nxt_clock(monkeypatch, day, hour, 5)
    _nxt_setup(tmp_path, token)
    broker = Broker(result({'Output_0': {'mkt_orr_no': 7}}))
    order = {**NXT_BUY, 'input': {**NXT_BUY['input'], 'orr_pr': price}}
    assert adapter(broker).call_tool('call_api', order)['error_type'] == 'NHOrderBlocked'
    assert not broker.sent


def test_rejected_nxt_test_is_released_only_when_the_broker_has_no_such_order(tmp_path, monkeypatch):
    import sqlite3
    guard_module = _nxt_clock(monkeypatch)
    _nxt_setup(tmp_path)
    broker = Broker(result({'rsp_cd': '40880', 'rsp_msg': '주문 거부'}))  # no order number: stays uncertain
    adapter(broker).call_tool('call_api', NXT_BUY)
    config = MCPServerConfig(command='python', args=['/local/scripts/nhplug_mcp.py', '--mode', 'paper', '--account', ACCOUNT])
    guard = guard_module.NHOrderGuard(config.args)
    with sqlite3.connect(guard.path) as db:
        fingerprint, state = db.execute("SELECT fingerprint, state FROM intents").fetchone()
    assert state == 'uncertain'
    with pytest.raises(guard_module.NHOrderBlocked):
        guard.release_rejected_nxt_test(fingerprint, [{'iem_cd': '005930'}])
    guard.release_rejected_nxt_test(fingerprint, [{'iem_cd': '069500'}])
    with sqlite3.connect(guard.path) as db:
        assert db.execute("SELECT state FROM intents").fetchone()[0] == 'terminal'


def test_session_check_is_skipped_only_for_the_nxt_test_order(tmp_path, monkeypatch):
    import json
    _nxt_clock(monkeypatch)
    _nxt_setup(tmp_path)
    (tmp_path / 'nhplug' / 'swing-policy.json').write_text(json.dumps({'enabled': True, 'krx_session_check': True}))
    broker = Broker(result({'Output_0': {'mkt_orr_no': 7}}))
    assert adapter(broker).call_tool('call_api', NXT_BUY)['status'] == 'ok'
    assert [name for name, _ in broker.sent] == ['call_api']  # no quote read for the session check
    krx = Broker(result({'rsp_cd': 'bad'}))
    assert adapter(krx).call_tool('call_api', BUY)['error_type'] == 'NHOrderBlocked'
    assert krx.sent[0][0] == 'get_stock_price'  # a regular order at 16:05 still goes through the session check


def test_swing_cap_of_21m_counts_what_is_held_not_every_past_buy(tmp_path, monkeypatch):
    """User decision 2026-11-13: buying and selling the same 3,000,000 KRW day after day never reaches the 21,000,000
    KRW cap; holding 21,000,000 KRW of open positions does."""
    import json
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from unittest.mock import Mock
    import src.tools.nhplug_order_guard as guard
    folder = tmp_path / 'nhplug'
    folder.mkdir()
    (folder / 'swing-policy.json').write_text(json.dumps({'enabled': True}))
    clock = Mock()
    monkeypatch.setattr(guard, 'datetime', clock)
    broker = Broker(result({'Output_0': {'mkt_orr_no': 1}}))
    number = [100]

    def order(op, code, day, qty=15, price=200000):
        clock.now.return_value = datetime(2026, 12, day, 10, tzinfo=ZoneInfo('Asia/Seoul'))
        number[0] += 1
        broker.outcome = result({'Output_0': {'mkt_orr_no': number[0]}})
        response = adapter(broker).call_tool('call_api', {'operationId': op, 'input': {
            **BUY['input'], 'iem_cd': code, 'orr_qty': qty, 'orr_pr': price}})
        if response['status'] == 'ok':
            query(broker, [execution_row(itg_orr_no=number[0], iem_cd=code, orr_qty=qty, tot_cns_qty=qty)], day=f'202612{day:02d}')
        return response

    weekdays = [1, 2, 3, 4, 7, 8, 9, 10, 11, 14, 15, 16, 17, 18, 21, 22]  # December 2026 trading days
    for day in weekdays[:8]:  # 8 days x 3,000,000 KRW bought and sold: 24,000,000 KRW of buys, nothing held
        assert order('krstockOrderCashBuy', '005930', day)['status'] == 'ok'
        assert order('krstockOrderCashSell', '005930', day)['status'] == 'ok'
    for day in weekdays[8:15]:  # now hold 3,000,000 KRW more each day: 21,000,000 KRW after 7 days
        assert order('krstockOrderCashBuy', f'0000{day:02d}', day)['status'] == 'ok'
    last = weekdays[15]
    assert order('krstockOrderCashBuy', '000099', last, qty=1, price=1000)['error_type'] == 'NHOrderBlocked'
    assert order('krstockOrderCashSell', f'0000{weekdays[8]:02d}', last)['status'] == 'ok'  # selling one frees its cost
    assert order('krstockOrderCashBuy', '000099', last)['status'] == 'ok'


def test_dashboard_limits_replace_the_defaults(tmp_path, monkeypatch):
    import json
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from unittest.mock import Mock
    import src.tools.nhplug_order_guard as guard
    folder = tmp_path / 'nhplug'
    folder.mkdir()
    (folder / 'swing-policy.json').write_text(json.dumps({'enabled': True}))
    (folder / 'accounts.json').write_text(json.dumps({'version': 1, 'accounts': {'paper-shared': {
        'limits': {'order_max_krw': 500_000, 'daily_buy_krw': 20_000_000, 'daily_new_symbols': 99}}}}))
    assert guard.paper_limits() == {'order_max_krw': 500_000, 'daily_buy_krw': 20_000_000, 'held_max_krw': 21_000_000,
                                    'daily_new_symbols': 5, 'daily_loss_krw': 300_000}  # 99 is out of bounds
    clock = Mock()
    clock.now.return_value = datetime(2026, 12, 1, 10, tzinfo=ZoneInfo('Asia/Seoul'))
    monkeypatch.setattr(guard, 'datetime', clock)
    broker = Broker(result({'Output_0': {'mkt_orr_no': 7}}))
    big = {**BUY, 'input': {**BUY['input'], 'orr_qty': 3, 'orr_pr': 180000}}  # 540,000 KRW > 500,000
    assert adapter(broker).call_tool('call_api', big)['error_type'] == 'NHOrderBlocked'
    assert adapter(broker).call_tool('call_api', {**BUY, 'input': {**BUY['input'], 'orr_qty': 2}})['status'] == 'ok'


def test_paper_buy_window_opens_at_0900(tmp_path, monkeypatch):
    """User 2026-11-19: the shared paper account orders from the 09:00 open, not 09:10."""
    import json
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from unittest.mock import Mock
    import src.tools.nhplug_order_guard as guard
    (tmp_path / 'nhplug').mkdir()
    (tmp_path / 'nhplug' / 'swing-policy.json').write_text(json.dumps({'enabled': True}))
    clock = Mock()
    monkeypatch.setattr(guard, 'datetime', clock)
    clock.now.return_value = datetime(2026, 11, 6, 8, 59, tzinfo=ZoneInfo('Asia/Seoul'))
    broker = Broker(result({'Output_0': {'mkt_orr_no': 42}}))
    assert adapter(broker).call_tool('call_api', BUY)['error_type'] == 'NHOrderBlocked' and not broker.sent
    clock.now.return_value = datetime(2026, 11, 6, 9, 0, 30, tzinfo=ZoneInfo('Asia/Seoul'))
    adapter(broker).call_tool('call_api', BUY)
    assert len(broker.sent) == 1


def test_paper_buy_window_moves_with_a_shifted_exchange_day(tmp_path, monkeypatch):
    """수능 day (user 2026-11-19): KRX 10:00~16:30, so paper buys are allowed 10:00~16:00 that day."""
    import json
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from unittest.mock import Mock
    import src.tools.nhplug_calendar as cal
    import src.tools.nhplug_order_guard as guard
    monkeypatch.setattr(cal, 'SHIFTED_DAYS', {'2026-11-19': 60})
    (tmp_path / 'nhplug').mkdir()
    (tmp_path / 'nhplug' / 'swing-policy.json').write_text(json.dumps({'enabled': True}))
    clock = Mock()
    monkeypatch.setattr(guard, 'datetime', clock)
    broker = Broker(result({'Output_0': {'mkt_orr_no': 42}}))
    for h, m, allowed in ((9, 30, False), (15, 30, True), (16, 0, False)):
        clock.now.return_value = datetime(2026, 11, 19, h, m, tzinfo=ZoneInfo('Asia/Seoul'))
        before = len(broker.sent)
        adapter(broker).call_tool('call_api', BUY)
        assert (len(broker.sent) > before) is allowed, (h, m)
