from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pytest
from src.tools.nhplug_loss import check_loss

NOW = datetime(2026, 11, 6, 10, tzinfo=ZoneInfo('Asia/Seoul'))

def snap(realized=0, fees=0, unrealized=0):
    return {'status': 'ok', 'data': {'rsp_cd': '00000', 'rsp_msg': '모의투자 마지막 자료입니다',
        'Output_0': {'sby_pls_amt': realized, 'sdr_xps': fees, 'eal_pls_amt': unrealized}}}

@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_HOME', str(tmp_path))

def test_threshold_latches_across_calls_and_resets_next_day():
    assert check_loss(snap(-299999), NOW, 'account')['allowed']
    hit = check_loss(snap(-299999, 1, 1000), NOW, 'account')
    assert not hit['allowed'] and hit['latched'] and hit['loss'] == 300000
    assert not check_loss(snap(), NOW, 'account')['allowed']
    assert check_loss(snap(), NOW + timedelta(days=1), 'account')['allowed']
    assert check_loss(snap(), NOW, 'other')['allowed']

@pytest.mark.parametrize('payload', [None, [], snap('NaN'), snap(fees=-1), snap(realized=None), {},
    {'status': 'error', 'data': snap()['data']},
    {'status': 'ok', 'data': {'rsp_cd':'010','rsp_msg':'error','Output_0': {}}}])
def test_invalid_blocks_without_latching(payload):
    result = check_loss(payload, NOW, 'account')
    assert not result['allowed'] and not result['latched']
    assert check_loss(snap(), NOW, 'account')['allowed']

def test_gains_do_not_offset_losses_and_naive_time_blocked():
    assert check_loss(snap(200000, 0, -300000), NOW, 'account')['latched']
    assert not check_loss(snap(), NOW.replace(tzinfo=None), 'other')['allowed']
