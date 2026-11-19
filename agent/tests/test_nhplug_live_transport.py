"""Live NH requests remain blocked at the adapter and launcher boundary."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.config.schema import MCPServerConfig
from src.live.halt import trip_halt
from src.tools.mcp import MCPServerAdapter
from scripts.nhplug_mcp import build_environment

ACCOUNT = '12345678901'


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_HOME', str(tmp_path))


@pytest.mark.parametrize('operation', ['krstockOrderCashBuy', 'krstockOrderCashSell', 'krstockOrderCancel'])
@pytest.mark.parametrize('mode', ['readonly', 'live'])
@pytest.mark.parametrize('halted', [True, False])
def test_live_order_modes_never_reach_transport(operation, mode, halted):
    created = []

    def factory():
        created.append(True)
        raise AssertionError('transport must not be created')

    if halted:
        trip_halt('file', 'test live boundary')
    config = MCPServerConfig(command='python', args=['/local/nhplug_mcp.py', '--mode', mode, '--account', ACCOUNT],
                             env={'NHPLUG_ENABLE_TRADING': 'true', 'NHPLUG_BASE_URL': 'https://api.nhplug.com:8443'})
    result = MCPServerAdapter('renamed_broker', config, client_factory=factory).call_tool('call_api', {
        'operationId': operation, 'input': {'act_no': ACCOUNT, 'iem_cd': '114800', 'orr_qty': 1},
    })
    assert result['status'] == 'error'
    assert result['error_type'] == 'NHOrderBlocked'
    assert created == []


@pytest.mark.parametrize('args', [
    ['--mode', 'paper', '--mode', 'readonly'],
    ['--mode', 'readonly', '--mode', 'paper'],
    ['--mode', 'live'],
    ['--mode'],
    [],
])
def test_ambiguous_or_live_mode_rejected_before_transport(args):
    called = []
    config = MCPServerConfig(command='python', args=['/local/nhplug_mcp.py', *args, '--account', ACCOUNT])
    adapter = MCPServerAdapter('renamed_broker', config, client_factory=lambda: called.append(True))
    result = adapter.call_tool('call_api', {'operationId': 'krstockOrderCashBuy', 'input': {}})
    assert result['status'] == 'error'
    assert result['error_type'] == 'NHOrderBlocked'
    assert not called


def test_environment_tampering_cannot_make_readonly_trading_enabled(tmp_path):
    envfile = tmp_path / '.envrc'
    envfile.write_text('APP_KEY=test-key\nAPP_SECRET=test-secret\n')
    env = build_environment(envfile, 'readonly', ACCOUNT, {
        'PATH': '/usr/bin', 'NHPLUG_ENABLE_TRADING': 'true',
        'NHPLUG_BASE_URL': 'https://api.nhplug.com:8443', 'NHPLUG_ALLOW_HOSTS': '*',
    })
    assert env['NHPLUG_ENABLE_TRADING'] == 'false'
    assert env['NHPLUG_BASE_URL'] == 'https://api.nhplug.com:8443'
    assert env['NHPLUG_ALLOW_HOSTS'] == ''
    with pytest.raises(ValueError):
        build_environment(envfile, 'live', ACCOUNT, {})
