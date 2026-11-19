"""API 선정 실측 (user 2026-11-19): "필요한 데이터가 있으면 연동 API 키가 있는 API들 가이드를 검토하고 테스트해서 가장
성능·효율·조건이 좋은 API를 점수와 순위로 선정해서 쓴다".

For one data need, every candidate source answers the same question several times; the bench measures what each
really returned and scores it on one rubric (RUBRIC, 100 points), then ranks them. Read-only: only quote/list calls,
never an order. Results go to ~/.vibe-trading/nhplug/api-bench/<need>-<YYYYMMDD-HHMM>.json and the console.

    PYTHONPATH=agent:scripts .venv/bin/python scripts/api_bench.py theme_members --runs 3

A need is a NEEDS entry: what is asked (`ask`), the fields that answer it, and its candidates. A candidate is a function
(target, universe) -> (rows, calls) plus the facts no run can measure (CONDITIONS: contract, documented limit, terms).
Add a need or a candidate here before wiring a new source into a runner, and record the ranking in docs/api-selection.md.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
OUT = Path.home() / '.vibe-trading/nhplug/api-bench'

# What no single run shows. official: a published API under a contract with the account (an unofficial web endpoint
# can change or block without notice); limit: a documented per-second limit with room for the need; terms: 5 = no
# extra burden, lower for IP allow-lists, single-token rules or use restrictions that touch this use.
CONDITIONS = {
    'naver': {'official': False, 'limit': False, 'terms': 2,
              'note': '공식 계약 없는 모바일 웹 주소(m.stock.naver.com/api). 한도 비공개, 바뀌거나 막혀도 공지 없음. 키 불필요'},
    'kiwoom': {'official': True, 'limit': True, 'terms': 4,
               'note': '키움 REST 공식 API, TR별 초당 5회(문서). 실계좌 앱 키라 조회 TR 허용 목록으로만 호출. 시세 본인 용도·제3자 제공 금지'},
    'toss': {'official': True, 'limit': True, 'terms': 2,
             'note': '토스증권 Open API 공식, 그룹별 초당 한도(헤더 X-RateLimit). 허용 IP 등록·클라이언트당 토큰 1개. 본인 매매 목적만, 제3자 배포 금지'},
    'lists+kiwoom': {'official': True, 'limit': True, 'terms': 4,
                     'note': '구성종목 목록은 하루 한 번(토스 섹터 ∪ 네이버 테마), 장중 수치는 키움 ka10095 한 번(최대 100종목). 장중 호출은 공식 API 1번'},
}

# 100 points. fields: each required field the rows carry; coverage: share of the union of the target's universe
# members (all candidates together) this source found; speed: median seconds of one full answer; calls: API calls per
# answer; stable: share of runs that answered; conditions: CONDITIONS above.
# Qualification before points (a source that cannot answer the need is not ranked above one that can): every required
# field on at least 90% of its rows, and at least QUALIFY_COVERAGE of the union of members found.
QUALIFY_FIELDS, QUALIFY_COVERAGE = 0.9, 0.7
RUBRIC = {'fields': 25, 'coverage': 15, 'speed': 15, 'calls': 15, 'stable': 10, 'official': 10, 'limit': 5, 'terms': 5}


def speed_points(seconds):
    return 15 if seconds <= 1 else 10 if seconds <= 3 else 5 if seconds <= 6 else 0


def call_points(calls):
    return 15 if calls <= 3 else 10 if calls <= 8 else 5 if calls <= 20 else 0


from theme_data import (daily_members, group_of, kiwoom_members, kiwoom_quotes, lists_kiwoom_members,  # noqa: E402,F401
                        naver_members, toss_members)

NEEDS = {
    'theme_members': {
        'ask': "테마 묶음('화장품·소비' 등) 구성종목 중 후보 범위(코스피200·코스닥150) 종목의 오늘 등락률·거래대금",
        'fields': ('change_pct', 'value_krw', 'as_of'),
        'target': '화장품·소비',
        'candidates': {'naver': naver_members, 'kiwoom': kiwoom_members, 'toss': toss_members,
                       'lists+kiwoom': lists_kiwoom_members},
        'not_candidates': {'nh': '테마·업종 순위·구성종목 API 없음(현재가 31종)', 'krx': '전 거래일 확정 통계만, 장중 없음',
                           'opendart': '공시 전용', 'fred': '미국 거시 전용', 'openai': '모델', 'jev': '모델'},
    },
}


def bench(need, runs, universe, sleep=time.sleep):
    spec = NEEDS[need]
    found, raw = {}, {}
    for name, fn in spec['candidates'].items():
        if name == 'lists+kiwoom':
            daily_members(spec['target'], universe)  # the once-a-day list, outside the timed answers
        seconds, calls, ok, rows, errors = [], [], 0, {}, []
        for _ in range(runs):
            started = time.monotonic()
            try:
                got, n = fn(spec['target'], universe)
                seconds.append(time.monotonic() - started)
                calls.append(n)
                ok += 1
                rows = got or rows
            except Exception as exc:  # noqa: BLE001 - a failed run is what stability measures
                errors.append(type(exc).__name__)
            sleep(1)
        raw[name] = {'seconds': [round(s, 2) for s in seconds], 'calls': calls, 'ok': ok, 'runs': runs, 'errors': errors,
                     'rows': len(rows), 'sample': dict(list(rows.items())[:3])}
        found[name] = rows
    union = set().union(*map(set, found.values())) or {None}
    scored = []
    for name, r in raw.items():
        rows, cond = found[name], CONDITIONS[name]
        filled = {f: (sum(v.get(f) is not None for v in rows.values()) / len(rows)) if rows else 0 for f in spec['fields']}
        points = {'fields': round(RUBRIC['fields'] * sum(filled.values()) / len(filled), 1),
                  'coverage': round(RUBRIC['coverage'] * len(set(rows) & union) / len(union), 1),
                  'speed': speed_points(statistics.median(r['seconds'])) if r['seconds'] else 0,
                  'calls': call_points(statistics.median(r['calls'])) if r['calls'] else 0,
                  'stable': round(RUBRIC['stable'] * r['ok'] / r['runs'], 1),
                  'official': RUBRIC['official'] if cond['official'] else 0, 'limit': RUBRIC['limit'] if cond['limit'] else 0,
                  'terms': cond['terms']}
        share = len(set(rows) & union) / len(union)
        qualified = all(v >= QUALIFY_FIELDS for v in filled.values()) and share >= QUALIFY_COVERAGE
        scored.append({'source': name, 'score': round(sum(points.values()), 1), 'points': points, 'filled': filled,
                       'qualified': qualified, 'coverage': round(share, 2),
                       'median_s': round(statistics.median(r['seconds']), 2) if r['seconds'] else None,
                       'calls': statistics.median(r['calls']) if r['calls'] else None, **r, 'note': cond['note']})
    scored.sort(key=lambda s: (s['qualified'], s['score']), reverse=True)
    for rank, s in enumerate(scored, 1):
        s['rank'] = rank
    return {'need': need, 'ask': spec['ask'], 'target': spec['target'], 'time': datetime.now(KST).isoformat(timespec='seconds'),
            'union': len(union - {None}), 'ranking': scored, 'not_candidates': spec['not_candidates'], 'rubric': RUBRIC}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('need', choices=sorted(NEEDS))
    parser.add_argument('--runs', type=int, default=3)
    args = parser.parse_args()
    from market_movers import constituents
    from src.config.loader import load_agent_config
    from src.tools.mcp import MCPServerAdapter
    universe = constituents(MCPServerAdapter('nhplug_readonly', load_agent_config().mcp_servers['nhplug_readonly']))
    result = bench(args.need, args.runs, universe)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{args.need}-{datetime.now(KST):%Y%m%d-%H%M}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1))
    path.chmod(0o600)
    for s in result['ranking']:
        print(f"{s['rank']}. {s['source']:12} {'적격' if s['qualified'] else '부적격'} {s['score']:5.1f}점  {s['points']}  {s['median_s']}s  호출 {s['calls']}  종목 {s['rows']}/{result['union']}  "
              f"채움 {s['filled']}  성공 {s['ok']}/{s['runs']} {s['errors'] or ''}")
    print(path)


if __name__ == '__main__':
    main()
