"""Read existing runner state without starting another trading process."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from collections import deque
from nh_live_buy_control import buy_allowed, pause as pause_live_buys
from tunables import setting

from launchd_services import SERVICES

# The screen name of every launchd service (launchd_services.SERVICES is the one list; a test keeps both in step).
NAMES = {'shared-etf-paper': '공유 모의 ETF 실행기', 'shared-etf-learning': '공유 모의 ETF 데이터 관찰기',
         'shared-etf-regime': '시황 국면 관찰기', 'shared-etf-dashboard': '대시보드', 'market-movers': '순위·관심종목 계산기',
         'premarket': '장전 자료 수집', 'trading-day-start': '거래일 시작', 'preflight': '장전 사전 점검',
         'vibe-longterm': '장기투자 실행기', 'longterm-watch': '장기투자 상시 감시', 'us-ideas': '미국 종목 아이디어', 'api-watch': 'API 부하 감사', 'trading-backup': '거래 데이터 백업',
         'state-sync': '상태 백업', 'discord-notify': '디스코드 알림', 'exit-replay': '청산 재현',
         'live-follow-1': '실계좌 1 미러 실행기', 'longterm-live-2': '실계좌 2 장기투자 실행기', 'nxt-virtual': 'NXT 가상 실행기'}
LABELS = {NAMES.get(name, name): f'com.vibetrading.{name}' for name in SERVICES}


_PROCESSES = (0.0, None)


PROCESS_CACHE_S = setting('process_cache_s')  # 운영 조정값


def processes(max_age=PROCESS_CACHE_S):
    """Service states from `launchctl list`, reused for `max_age` seconds (one rerun used to call it about seven times)."""
    global _PROCESSES
    if _PROCESSES[1] is not None and time.monotonic() - _PROCESSES[0] < max_age:
        return dict(_PROCESSES[1])
    try:
        result = subprocess.run(['launchctl', 'list'], check=True, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        states = {name: '확인 불가' for name in LABELS}
        _PROCESSES = (time.monotonic(), states)
        return dict(states)
    entries = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) == 3:
            entries[fields[2]] = fields[0]
    states = {name: ('미등록' if label not in entries else '종료' if entries[label] == '-' else '실행 중')
              for name, label in LABELS.items()}
    _PROCESSES = (time.monotonic(), states)
    return dict(states)


def peek_processes(max_age=PROCESS_CACHE_S):
    """Return a current cache snapshot without running launchctl."""
    stamp, states = _PROCESSES
    if states is None or time.monotonic() - stamp >= max_age:
        return None
    return dict(states)


def forget_processes():
    global _PROCESSES
    _PROCESSES = (0.0, None)


_COUNTS = {}  # (path, queries) -> (monotonic time, file stamp, counts)
COUNT_CACHE_S = 60


def _stamp(path):
    """Size and mtime of a database and its WAL (a WAL database changes there until a checkpoint)."""
    out = []
    for file in (path, path.with_name(path.name + '-wal')):
        try:
            info = file.stat()
            out.append((info.st_mtime_ns, info.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def counts(path, statements):
    """{label: count} of read-only queries, reused while the file is unchanged and for at most a minute after it
    changes: count(*) over the 170 MB ledger and the 371 MB model database cost ~0.6 s on every 운영 상태 rerun."""
    if not path.is_file():
        return None
    key, stamp = (str(path), tuple(statements.items())), _stamp(path)
    hit = _COUNTS.get(key)
    if hit and (hit[1] == stamp or time.monotonic() - hit[0] < COUNT_CACHE_S):
        return dict(hit[2])
    try:
        with sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True, timeout=1) as db:
            result = {label: db.execute(query).fetchone()[0] for label, query in statements.items()}
    except sqlite3.Error:
        return None
    _COUNTS[key] = (time.monotonic(), stamp, result)
    return dict(result)


def learning(root, *, shared=False):
    paper = root / 'nhplug' / ('shared-etf-paper' if shared else 'isolated-paper')
    return {
        'ledger': counts(paper / 'trading-ledger.sqlite3', {'이벤트': 'select count(*) from events_v2'}),
        'observations': counts(paper / 'paper-observations.sqlite3', {
            '전체': 'select count(*) from price_observations',
            '후속 가격 확인': "select count(*) from price_observations where status = 'observed'",
            '후속 가격 부족': "select count(*) from price_observations where status = 'insufficient'"}),
        'model': counts(paper / 'paper-model.sqlite3', {'평가 실행': 'select count(*) from runs',
                                                       '예측': 'select count(*) from predictions'}),
        'jev_model': counts(paper / 'paper-model.sqlite3', {'연구 실행': 'select count(*) from jev_runs',
            '서로 다른 예측 대상': 'select count(*) from (select distinct source, offset, symbol from jev_predictions_latest)'}),
        'orders': counts(paper / 'orders.sqlite3', {'주문 의도': 'select count(*) from intents',
                                                    '체결': 'select count(*) from fills'}),
    }


def recent_events(path, limit=20):
    try:
        with path.open(encoding='utf-8') as source:
            rows = []
            for line in deque(source, maxlen=limit):
                event = json.loads(line)
                if isinstance(event, dict):
                    rows.append({k: event.get(k) for k in ('time', 'event', 'choice', 'action', 'reason', 'execution')})
            return rows[::-1]
    except (OSError, ValueError):
        return None


def stop_shared_paper(root):
    forget_processes()  # the next screen must show the state after this action, not the 5-second copy
    path = root / 'nhplug' / 'shared-etf-paper' / 'STOP'
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('x', encoding='utf-8') as f:
            f.write('streamlit-shared-etf-stop-v1\n')
    except FileExistsError:
        pass


def stop_live(root):
    """Save the live buy pause the live executors read before every buy (nh_live_follow, nh_guarded_live); True once
    it is saved. Reported from the saved marker: the old check asked launchd about com.vibetrading.live-policy-auto,
    which is no longer registered, so a saved pause always looked like a failure."""
    forget_processes()  # the next screen must show the state after this action, not the 5-second copy
    pause_live_buys(root / 'nhplug')
    return not buy_allowed(root / 'nhplug')
