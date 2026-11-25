"""NH MCP connection reuse (docs/audit/api-load-audit-2026-11-24.md proposal 1): reads share one server process, orders never do."""
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from src.config.schema import MCPServerConfig
from src.tools import mcp, nh_sessions as S

SERVER = '''
import asyncio, os, sys, time
from fastmcp import FastMCP
time.sleep(float(os.environ.get("FAKE_START_DELAY", "0")))
app = FastMCP("fake")
@app.tool
async def get_stock_price(stock_code: str = "") -> dict:
    await asyncio.sleep(float(os.environ.get("FAKE_CALL_DELAY", "0")))
    if stock_code == "die":
        os._exit(1)
    if stock_code == "auth":
        raise ValueError('{"category": "auth"}')
    return {"pid": os.getpid(), "code": stock_code}
@app.tool
async def call_api(operationId: str = "", input: dict = {}) -> dict:
    return {"pid": os.getpid(), "code": operationId}
app.run(show_banner=False)
'''


@pytest.fixture
def nh(tmp_path, monkeypatch):
    server, tokens = tmp_path / 'server.py', tmp_path / 'tokens'
    server.write_text(SERVER)
    tokens.mkdir()
    write_token(tokens)
    monkeypatch.setattr(S, 'ENABLED', True)
    monkeypatch.setattr(S, 'TOKENS', tokens)

    def adapter(name='nhplug_readonly', **kwargs):
        return mcp.MCPServerAdapter(name, MCPServerConfig.model_validate(
            {'type': 'stdio', 'command': sys.executable, 'args': [str(server)], 'toolTimeout': 40, 'initTimeout': 30, **kwargs}))
    adapter.tokens = tokens
    yield adapter
    S.close_all()


def write_token(folder, left=3600, name='a.json'):
    (folder / name).write_text(json.dumps({'token': 'x', 'expiresAt': (time.time() + left) * 1000}))


def read(adapter, code='005930', tool='get_stock_price', **arguments):
    result = mcp._run_sync(lambda: adapter._call_tool(tool, arguments or {'stock_code': code}))
    return result.structured_content if not result.is_error else result


def test_reads_share_one_process_also_across_adapters_and_threads(nh):
    pids, out = {read(nh())['pid'] for _ in range(3)}, []
    threads = [threading.Thread(target=lambda c=c: out.append((c, read(nh(), c)))) for c in ('1', '2', '3', '4')]
    [t.start() for t in threads], [t.join() for t in threads]
    assert len(pids | {r['pid'] for _, r in out}) == 1 and all(r['code'] == c for c, r in out)
    assert read(nh(), tool='call_api', operationId='krstockInquiryBalance')['pid'] in pids
    assert read(nh(env={'OTHER': '1'}))['pid'] not in pids  # another account, mode or host is another process


def test_orders_unknown_operations_and_other_servers_always_get_their_own_process(nh):
    shared = read(nh())['pid']
    for operation in ('krstockOrderCashSell', 'krstockOrderCancel', 'somethingNew'):
        assert read(nh(), tool='call_api', operationId=operation)['pid'] != shared
    assert read(nh('other_server'))['pid'] != read(nh('other_server'))['pid']
    result = mcp._run_sync(lambda: nh()._call_tool('get_stock_price', {'stock_code': 'x'}, pre_send_check=lambda: True))
    assert result.structured_content['pid'] != shared  # a call with a pre-send check is an order
    assert not S.reusable('nhplug_paper', nh().server_config, 'call_api_guarded', {'operationId': 'krstockInquiryBalance'}, None)
    assert not S.reusable('nhplug_paper', nh().server_config, 'get_stock_price', {}, lambda: None)  # a test's own client


def test_a_new_token_file_means_a_new_process_and_an_expiring_one_means_no_reuse(nh):
    first = read(nh())['pid']
    write_token(nh.tokens)
    os.utime(nh.tokens / 'a.json', (time.time() + 5, time.time() + 5))
    second = read(nh())['pid']
    assert first != second == read(nh())['pid']
    write_token(nh.tokens, left=60)  # inside EXPIRY_S: as before, every call reads the token file itself
    assert len({read(nh())['pid'] for _ in range(2)} | {second}) == 3
    (nh.tokens / 'a.json').write_text('broken')
    assert read(nh())['pid'] != read(nh())['pid']
    (nh.tokens / 'a.json').unlink()
    assert read(nh())['pid'] != read(nh())['pid']  # no token yet: the first call issues it


def test_a_dead_process_costs_no_read_and_is_not_reused_for_a_while(nh):
    first = read(nh())['pid']
    os.kill(first, signal.SIGKILL)
    time.sleep(0.3)
    after = read(nh())['pid']
    assert after != first and read(nh())['pid'] != after  # one process a call, as before 2026-11-24
    S._resting.clear()  # the minute has passed
    again = read(nh())['pid']
    assert again == read(nh())['pid']


def test_a_read_that_ran_into_its_time_limit_is_raised_not_sent_again(nh):
    slow = nh(env={'FAKE_CALL_DELAY': '30'}, toolTimeout=1)
    started = time.monotonic()
    with pytest.raises(Exception, match='(?i)timed out'):
        read(slow)
    assert time.monotonic() - started < 4


def test_a_login_error_gives_up_the_process(nh):
    first = read(nh())['pid']
    assert read(nh(), 'auth').is_error
    assert read(nh())['pid'] != first


def test_a_stalled_start_fails_reads_soon_and_together_but_leaves_orders_their_time(nh, monkeypatch):
    monkeypatch.setattr(S, 'READ_INIT_S', 1.0)
    slow, errors, started = nh(env={'FAKE_START_DELAY': '3'}), [], time.monotonic()

    def one():
        try:
            read(slow)
        except RuntimeError as exc:
            errors.append(str(exc))
    threads = [threading.Thread(target=one) for _ in range(4)]
    [t.start() for t in threads], [t.join() for t in threads]
    assert len(errors) == 4 and all('Failed to initialize server session' in e for e in errors) and time.monotonic() - started < 5
    assert read(slow, tool='call_api', operationId='krstockOrderCashBuy')['pid']  # 3 s start, 30 s allowed


def test_the_read_limit_never_widens_a_shorter_one(nh):
    assert nh()._build_client(init_limit=8)._init_timeout == 8
    assert nh(initTimeout=5)._build_client(init_limit=8)._init_timeout == 5
    assert nh()._build_client()._init_timeout == 30


def test_an_old_process_is_replaced(nh, monkeypatch):
    first = read(nh())['pid']
    monkeypatch.setattr(S, 'MAX_AGE_S', 0)
    assert read(nh())['pid'] != first


def test_the_server_process_ends_with_its_program(nh, tmp_path):
    code = ('import sys, time; from src.tools import mcp, nh_sessions as S; from src.config.schema import MCPServerConfig; from pathlib import Path\n'
            f'S.ENABLED, S.TOKENS = True, Path({str(nh.tokens)!r})\n'
            f'a = mcp.MCPServerAdapter("nhplug_readonly", MCPServerConfig.model_validate({{"type": "stdio", "command": sys.executable, "args": [{str(tmp_path / "server.py")!r}]}}))\n'
            'r = mcp._run_sync(lambda: a._call_tool("get_stock_price", {"stock_code": "1"}))\n'
            'print(r.structured_content["pid"], flush=True); time.sleep(float(sys.argv[1]))')
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
    for wait, kill in (('0', False), ('60', True)):  # a normal end and a kill -9
        program = subprocess.Popen([sys.executable, '-c', code, wait], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        child = int(program.stdout.readline())
        if kill:
            program.kill()
        assert program.wait(timeout=15) == (-9 if kill else 0)
        for _ in range(50):
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            pytest.fail('the server process outlived its program')
        assert kill or 'Traceback' not in program.stderr.read()


def test_pytest_runs_do_not_reuse_by_default():
    out = subprocess.run([sys.executable, '-c', 'import pytest; from src.tools import nh_sessions as S; print(S.ENABLED)'], text=True,
                         capture_output=True, env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])})
    assert out.stdout.strip() == 'False'


def test_a_fresh_import_keeps_using_the_same_process(nh):
    # 2026-11-25: Streamlit drops changed modules, and every edit of the code left one more loop and NH process behind
    import importlib
    adapter = nh()
    first = read(adapter)['pid']
    kept = sys.modules.pop('src.tools.nh_sessions')
    try:
        import src.tools
        again = importlib.import_module('src.tools.nh_sessions')
        assert again is not kept and again._sessions is kept._sessions and again._background_loop() is kept._background_loop()
        again.ENABLED, again.TOKENS = True, S.TOKENS
        assert read(adapter)['pid'] == first
    finally:
        sys.modules['src.tools.nh_sessions'] = kept
        src.tools.nh_sessions = kept
