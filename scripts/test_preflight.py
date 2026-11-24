import json
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import preflight as pf

NOW = datetime(2026, 11, 18, 8, 25, tzinfo=ZoneInfo('Asia/Seoul'))


def checks(*results):
    return [(f'c{i}', f'점검{i}', (lambda r: (lambda now: r()))(r), due) for i, (r, due) in enumerate(results)]


def boom():
    raise ValueError('paper account list unavailable 12345678901')


def test_a_failure_or_an_error_fails_the_run_without_leaking_values():
    with patch('trading_day_start.trading_day', return_value=(True, 'trading_day')):
        report = pf.run(NOW, checks((lambda: (True, 'ok'), None), (boom, None)))
    assert report['ok'] is False
    assert report['results'][1] == {'id': 'c1', 'name': '점검1', 'ok': False, 'detail': '오류 ValueError'}
    assert '12345678901' not in pf.message(report) and '점검1' in pf.message(report)


def test_checks_wait_for_their_time_and_holidays_skip_everything():
    with patch('trading_day_start.trading_day', return_value=(True, 'trading_day')):
        report = pf.run(NOW, checks((boom, '08:52')))
    assert report['ok'] is True and report['results'][0]['ok'] is None
    with patch('trading_day_start.trading_day', return_value=(False, 'krx_holiday')):
        assert pf.run(NOW, checks((boom, None))) ['results'] == []


def test_every_check_has_a_korean_name_and_a_unique_id():
    ids = [c[0] for c in pf.CHECKS]
    assert len(ids) == len(set(ids)) and all(c[1] and not c[1].isascii() for c in pf.CHECKS)


def test_movers_before_the_open_only_needs_the_waiting_service():
    # 11-18 08:53: market-movers waits for 09:00 by design; yesterday's file then is normal
    with patch.object(pf, '_running', return_value=True), patch.object(pf, '_movers_time', return_value='2026-11-17T15:04:00+09:00'):
        assert pf.movers(NOW.replace(hour=8, minute=53))[0] is True
        assert pf.movers(NOW.replace(hour=9, minute=5))[0] is False
    with patch.object(pf, '_running', return_value=False), patch.object(pf, '_movers_time', return_value='2026-11-17T15:04:00+09:00'):
        assert pf.movers(NOW.replace(hour=8, minute=53))[0] is False
    with patch.object(pf, '_running', return_value=False), patch.object(pf, '_movers_time', return_value='2026-11-18T09:00:15+09:00'):
        assert pf.movers(NOW.replace(hour=9, minute=5))[0] is True


def test_the_premarket_report_must_be_written_and_pass_its_check():
    # 11-18: the Codex automation that writes it was paused and nothing noticed the missing report
    with patch('premarket.status', return_value={'written': False, 'checked': None}):
        assert pf.premarket_report(NOW)[0] is False
    with patch('premarket.status', return_value={'written': True, 'checked': ['5절 자리표시']}):
        assert pf.premarket_report(NOW)[0] is False
    with patch('premarket.status', return_value={'written': True, 'checked': []}):
        assert pf.premarket_report(NOW)[0] is True
    assert ('premarket_report' in [c[0] for c in pf.CHECKS])


def test_premarket_only_sections_stop_counting_once_the_market_opens(tmp_path, monkeypatch):
    import json
    stamp = NOW.replace(hour=9, minute=10).isoformat()
    (tmp_path / 'premarket-brief.json').write_text(json.dumps({'time': stamp, 'sections': {
        'market': {'status': 'ok'}, 'night_futures': {'status': 'unavailable'}, 'nxt_quotes': {'status': 'unavailable'}}}))
    monkeypatch.setattr(pf, 'PAPER', tmp_path)
    assert pf.premarket(NOW.replace(hour=9, minute=15))[0] is True
    (tmp_path / 'premarket-brief.json').write_text(json.dumps({'time': NOW.isoformat(), 'sections': {
        'market': {'status': 'ok'}, 'night_futures': {'status': 'unavailable'}}}))
    assert pf.premarket(NOW)[0] is False  # 08:25: the night session's close is missing


def test_every_ops_tool_reads_the_runtime_vibe_trading_home_names(tmp_path):
    # 11-18 review: six tools resolved ~/.vibe-trading by hand and ignored VIBE_TRADING_HOME (a dry run read and wrote the
    # real runtime); preflight read VIBE_TRADING_HOME='' as a relative 'nhplug' folder
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = ('import intraday_audit, audit_incidents, trading_day_start, market_movers, strategy_map, preflight; '
            'print(intraday_audit.NHPLUG, audit_incidents.DB, trading_day_start.ROOT, market_movers.ROOT, strategy_map.ROOT, '
            'preflight.NH, sep="\\n")')
    run = lambda home: subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True,  # noqa: E731
                                      env={**os.environ, 'VIBE_TRADING_HOME': home}).stdout.split('\n')[:6]
    nh = tmp_path / 'nhplug'
    assert run(str(tmp_path)) == [str(nh), str(nh / 'audit-incidents.sqlite3'), str(nh / 'shared-etf-paper'),
                                  str(nh / 'shared-etf-paper'), str(nh / 'shared-etf-paper'), str(nh)]
    assert run('')[-1] == str(Path.home() / '.vibe-trading' / 'nhplug')  # unset means the default home, not './nhplug'


def test_before_the_first_collection_yesterdays_premarket_brief_is_not_a_failure(tmp_path, monkeypatch):
    """11-24 06:13: yesterday's 09:10 brief was counted as a failure, an hour and a half before launchd's 07:40 collection."""
    import launchd_services
    due = next(c[3] for c in pf.CHECKS if c[0] == 'premarket')
    hour, minute, _ = launchd_services.SERVICES['premarket'][3][0]
    assert due and f'{hour:02}:{minute:02}' < due <= '08:20'  # after the first collection, before the first scheduled check
    monkeypatch.setattr(pf, 'PAPER', tmp_path)
    (tmp_path / 'premarket-brief.json').write_text(json.dumps({'time': '2026-11-17T09:10:05+09:00', 'sections': {'night_futures': {'status': 'unavailable'}}}))
    checks = [c for c in pf.CHECKS if c[0] == 'premarket']
    with patch('trading_day_start.trading_day', return_value=(True, 'trading_day')):
        assert pf.run(NOW.replace(hour=6, minute=13), checks)['results'][0]['ok'] is None
        assert pf.run(NOW.replace(hour=8, minute=20), checks)['results'][0]['ok'] is False  # really missing by then


def test_an_order_halt_of_nh_alone_is_seen():
    import src.live.halt as halt
    with patch.object(halt, 'halt_flag_set', side_effect=lambda broker=None: broker == 'nhplug'):
        assert pf.stop_files(NOW)[0] is False


def test_an_unregistered_service_fails_the_check():
    import os_services
    up = {name: {'registered': True, 'running': False} for name in pf.NEEDED}
    with patch.object(os_services, 'states', return_value=up):
        assert pf.services(NOW)[0] is True
    up.popitem()
    with patch.object(os_services, 'states', return_value=up):
        assert pf.services(NOW)[0] is False
    with patch.object(os_services, 'states', side_effect=os_services.Unknown('x')):
        assert pf.services(NOW)[0] is False
