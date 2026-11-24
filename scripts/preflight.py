#!/usr/bin/env python3
"""장전 사전 점검 (user 2026-11-18): every check a trading day needs before 09:00, read-only.

  preflight.py            run the checks due by now, print JSON, append to nhplug/preflight/<day>.jsonl,
                          and post the failures to Discord (DISCORD_WEBHOOK_REALTIME)
  preflight.py --no-send  the same without Discord

Each check returns (ok, detail); a check that raises fails with the exception type only (no values, no account
numbers). Checks with a `due` time run only from that time (data that arrives at 07:40/08:45/08:50). launchd runs it at
08:20 and 08:53; the scheduled task 'trading-preflight' (08:25) reads the result and fixes what it can before 09:00.
Exit code 1 when any check failed.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlite_ro import ro_connect
from src.config.paths import get_runtime_root  # VIBE_TRADING_HOME, else ~/.vibe-trading (one rule for every tool)

KST = ZoneInfo('Asia/Seoul')
NH = get_runtime_root() / 'nhplug'
PAPER = NH / 'shared-etf-paper'
LONGTERM = NH / 'vibe-longterm'
OUT = NH / 'preflight'
# services a trading day needs registered (launchd_services.SERVICES keys)
NEEDED = ('shared-etf-paper', 'shared-etf-learning', 'shared-etf-regime', 'trading-day-start', 'market-movers', 'premarket',
          'live-follow-1', 'longterm-live-2', 'vibe-longterm', 'discord-notify', 'shared-etf-dashboard')


def _age_min(stamp, now):
    return (now - datetime.fromisoformat(stamp)).total_seconds() / 60


def services(now):
    missing = [n for n in NEEDED if subprocess.run(['launchctl', 'list', f'com.vibetrading.{n}'], capture_output=True).returncode]
    return not missing, f'미등록: {", ".join(missing)}' if missing else f'{len(NEEDED)}개 등록됨'


def stop_files(now):
    found = [str(p.relative_to(NH)) for p in (PAPER / 'STOP', LONGTERM / 'STOP', NH / 'STOP') if p.exists()]
    from src.live.halt import halt_flag_set
    if halt_flag_set('nh'):
        found.append('NH HALT')
    return not found, f'켜짐: {", ".join(found)}' if found else '없음'


def paper_account(now):
    """The shared paper runner's own start check (policy, owner, NH account list); it stopped on this 11-18 08:12."""
    from src.config.loader import load_agent_config
    from nh_paper_isolated import shared_default_config
    shared_default_config(load_agent_config().mcp_servers, NH.parent)
    return True, '모의 계좌·정책·계좌 목록 확인'


def unfinished(now):
    import trading_day_start
    n = trading_day_start.unfinished_intents(PAPER)
    return n == 0, f'미확인 주문 {n}건'


def settings(now):
    import accounts, strategy_settings
    bad = [k for k, r in accounts.load().items() if r.get('invalid')]
    status = strategy_settings.read_settings(PAPER)[1]
    issues = ([f'계좌 설정 무효: {", ".join(bad)}'] if bad else []) + (['매매 기준 파일 무효(기본값 사용)'] if status == 'invalid' else [])
    return not issues, '; '.join(issues) or '계좌·매매 기준 유효'


def premarket(now):
    brief = json.loads((PAPER / 'premarket-brief.json').read_text())
    age = _age_min(brief['time'], now)
    # the night futures and NXT pre-market quotes exist only before the open; from 09:00 they are gone by design
    preopen = {'night_futures', 'nxt_quotes'} if now.strftime('%H:%M') >= '09:00' else set()
    down = [k for k, v in (brief.get('sections') or {}).items()
            if k not in preopen and (v or {}).get('status') in ('unavailable', 'error')]
    ok = age <= 30 and not down
    return ok, f'{age:.0f}분 전 수집' + (f', 실패 절: {", ".join(down)}' if down else '')


def market_context(now):
    stamp = json.loads((PAPER / 'market-context.json').read_text()).get('time')
    age = _age_min(stamp, now)
    return age <= 24 * 60, f'{age:.0f}분 전'


def _running(name):
    result = subprocess.run(['launchctl', 'list', f'com.vibetrading.{name}'], capture_output=True, text=True)
    return result.returncode == 0 and '"PID" =' in result.stdout


def _movers_time():
    return json.loads((PAPER / 'movers.json').read_text()).get('time')


def movers(now):
    """market-movers starts at 08:50 and waits for the 09:00 open (by design), so before 09:00 the service must be up
    and waiting; from 09:00 today's ranking must exist."""
    stamp = _movers_time() or ''
    if stamp[:10] == now.date().isoformat():
        return True, f'{stamp[11:16]} 생성'
    if now.strftime('%H:%M') < '09:00':
        up = _running('market-movers')
        return up, '개장 대기 중(09:00에 생성)' if up else '순위 서비스가 떠 있지 않음'
    return False, f'오늘 순위 없음(마지막 {stamp[:16] or "없음"})'


def longterm_decided(now):
    with ro_connect(LONGTERM / 'longterm.sqlite3') as db:
        kinds = {k for (k,) in db.execute('SELECT kind FROM decisions WHERE day=?', (now.date().isoformat(),))}
    settings_on = json.loads((LONGTERM / 'settings.json').read_text()).get('enabled', True)
    if not settings_on:
        return True, '장기투자 꺼짐(운영 엔진)'
    return 'regime' in kinds, f'오늘 기록: {", ".join(sorted(kinds)) or "없음"}'


def env_keys(now):
    from dotenv import dotenv_values
    from local_env import ENV
    values = dotenv_values(ENV, interpolate=False)
    need = ('JEV_API_KEY', 'DISCORD_WEBHOOK_REALTIME', 'FRED_API_KEY')
    missing = [k for k in need if not (values.get(k) or '').strip()]
    return not missing, f'없음: {", ".join(missing)}' if missing else '필수 키 있음'


def premarket_report(now):
    """The detailed premarket analysis (reviews/premarket-DAY.md), written by the 08:31 task 'premarket-analysis-daily' and template-checked."""
    import premarket
    state = premarket.status(now=now)
    if not state.get('written'):
        return False, '장전 상세 분석 없음(08:31 작업 확인)'
    problems = state.get('checked') or []
    return not problems, f'템플릿 문제 {len(problems)}건' if problems else '작성·검사 통과'


# (id, 한글 이름, check, due HH:MM or None)
CHECKS = [('services', '서비스 등록', services, None), ('stop', '중단 파일·HALT', stop_files, None),
          ('paper_account', 'NH 모의 계좌·계좌 목록', paper_account, None), ('unfinished', '미확인 주문', unfinished, None),
          ('settings', '계좌·매매 기준 설정', settings, None), ('env', '.env 키', env_keys, None),
          ('premarket', '장전 자료 수집', premarket, '07:45'), ('market_context', '시황 자료', market_context, None),
          ('longterm', '장기 엔진 아침 판단', longterm_decided, '08:47'), ('movers', '순위 후보(movers)', movers, '08:51'),
          ('premarket_report', '장전 상세 분석 작성', premarket_report, '08:52')]


def run(now=None, checks=CHECKS):
    now = now or datetime.now(KST)
    import trading_day_start
    day_ok, note = trading_day_start.trading_day(now)
    if not day_ok:
        return {'time': now.isoformat(timespec='seconds'), 'trading_day': False, 'note': note, 'results': [], 'ok': True}
    results = []
    for ident, name, check, due in checks:
        if due and now.strftime('%H:%M') < due:
            results.append({'id': ident, 'name': name, 'ok': None, 'detail': f'{due} 뒤 점검'})
            continue
        try:
            ok, detail = check(now)
        except Exception as exc:
            ok, detail = False, f'오류 {type(exc).__name__}'
        results.append({'id': ident, 'name': name, 'ok': bool(ok), 'detail': detail})
    return {'time': now.isoformat(timespec='seconds'), 'trading_day': True, 'note': note, 'results': results,
            'ok': all(r['ok'] is not False for r in results)}


def message(report):
    failed = [r for r in report['results'] if r['ok'] is False]
    lines = [f"⚠️ 장전 사전 점검 {report['time'][11:16]} · 실패 {len(failed)}건"]
    lines += [f"- {r['name']}: {r['detail']}" for r in failed]
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--no-send', action='store_true')
    args = parser.parse_args()
    report = run()
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / f"{report['time'][:10]}.jsonl").open('a', encoding='utf-8') as log:
        log.write(json.dumps(report, ensure_ascii=False) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if not report['ok'] and not args.no_send:
        from discord_notify import send_everywhere, urls_from_env
        send_everywhere(urls_from_env().get('realtime'), message(report))  # Discord, Telegram, Slack: whichever is set
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
