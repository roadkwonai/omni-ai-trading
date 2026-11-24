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


def mover_agreement(read, universe, codes, sleep=time.sleep):
    """need mover_quotes (2026-11-20 NH call plan P2): the ranking service's figures from NH (one quote per code) or Kiwoom
    (ka10095, 100 codes a call). Each code is asked of NH and then of Kiwoom at once, so the two answers are the same
    moment; both row sets go through market_movers.rank() and the rankings and candidates are compared. Also times one
    whole answer of each source."""
    from market_movers import SCAN_GAP, kiwoom_scan, rank, scan
    nh, kw = {}, {}
    for code in codes:
        one = {code: universe[code]}
        for row in scan(read, one):
            nh[code] = row
        for row in kiwoom_scan(one):
            kw[code] = row
        sleep(SCAN_GAP)
    both = sorted(set(nh) & set(kw))
    diff = lambda f, rel: [abs(kw[c][f] - nh[c][f]) / (abs(nh[c][f]) or 1) if rel else abs(kw[c][f] - nh[c][f]) for c in both]  # noqa: E731
    fields = {'price_equal': round(sum(kw[c]['price'] == nh[c]['price'] for c in both) / max(len(both), 1), 3),
              'change_pct_max_diff': round(max(diff('change_pct', False), default=0), 3),
              'volume_max_rel_diff': round(max(diff('volume', True), default=0), 4),
              'value_max_rel_diff': round(max(diff('value_mil', True), default=0), 4)}
    a, b = rank([nh[c] for c in both]), rank([kw[c] for c in both])
    top = {k: {'same': a[0][k] == b[0][k], 'overlap': len(set(a[0][k]) & set(b[0][k]))} for k in a[0]}
    started = time.monotonic()
    kiwoom_scan({c: universe[c] for c in codes})
    kiwoom_s = round(time.monotonic() - started, 2)
    return {'need': 'mover_quotes', 'time': datetime.now(KST).isoformat(timespec='seconds'), 'codes': len(codes),
            'nh_rows': len(nh), 'kiwoom_rows': len(kw), 'compared': len(both), 'fields': fields, 'top10': top,
            'candidates_same': list(a[1]) == list(b[1]), 'candidates_overlap': len(set(a[1]) & set(b[1])),
            'whole_answer': {'kiwoom_seconds': kiwoom_s, 'kiwoom_calls': -(-len(codes) // 100),
                             'nh_seconds_at_scan_gap': round(len(codes) * SCAN_GAP, 1), 'nh_calls': len(codes)},
            'only_nh': sorted(set(nh) - set(kw)), 'only_kiwoom': sorted(set(kw) - set(nh))}


def bar_agreement(nh_rows, kw_bars, today):
    """Compare NH 1-minute rows (krstockQuotePeriod Output_1) with Kiwoom bars (kiwoom_data.day_bars) of the same moment.
    The two may stamp a bar by its start or its end minute, so each offset of -1/0/+1 minute is tried and the best kept.
    Only bars both have and NH calls finished (not its newest minute) are compared."""
    nh = {}
    for r in nh_rows or []:
        if isinstance(r, dict) and r.get('bsop_date') == today and r.get('bsop_time'):
            nh[r['bsop_time'][:4]] = tuple(float(r[k]) for k in ('stck_oprc', 'stck_hgpr', 'stck_lwpr', 'stck_prpr', 'vol'))
    if nh:
        nh.pop(max(nh))  # NH's newest minute is still forming
    kw = {}
    for t, o, h, l, c, v in kw_bars or []:
        kw[f'{t:%H%M}'] = (float(o), float(h), float(l), float(c), float(v))
    best = None
    for offset in (-1, 0, 1):
        def shifted(hhmm):
            m = int(hhmm[:2]) * 60 + int(hhmm[2:]) + offset
            return f'{m // 60:02d}{m % 60:02d}'
        pairs = [(nh[k], kw[shifted(k)]) for k in nh if shifted(k) in kw]
        if not pairs:
            continue
        price = sum(a[:4] == b[:4] for a, b in pairs) / len(pairs)
        close = sum(a[3] == b[3] for a, b in pairs) / len(pairs)
        volume = max(abs(a[4] - b[4]) / (a[4] or 1) for a, b in pairs)
        got = {'offset_min': offset, 'bars': len(pairs), 'ohlc_equal': round(price, 3), 'close_equal': round(close, 3),
               'volume_max_rel_diff': round(volume, 3)}
        if best is None or (got['ohlc_equal'], got['bars']) > (best['ohlc_equal'], best['bars']):
            best = got
    return best or {'offset_min': None, 'bars': 0}


def minute_agreement(read, codes, sleep=time.sleep):
    """need minute_bars (2026-11-20 NH call audit): the runner's NH 1-minute bars (755 calls in 85 minutes) against
    Kiwoom ka10080 for the same codes at the same moment, with each source's seconds per code."""
    import kiwoom_data
    from nh_paper_isolated import minute_bars
    now = datetime.now(KST)
    out, nh_s, kw_s = {}, [], []
    for code in codes:
        started = time.monotonic()
        response = minute_bars(read, code)
        nh_s.append(time.monotonic() - started)
        started = time.monotonic()
        try:
            kw = kiwoom_data.day_bars(code, now.date(), now=now)['bars']
        except Exception as exc:  # noqa: BLE001 - recorded as this code's answer
            kw = None
            out[code] = {'kiwoom_error': type(exc).__name__}
        kw_s.append(time.monotonic() - started)
        rows = ((response.get('data') or {}).get('Output_1') if response.get('status') == 'ok' and response.get('source') != 'kiwoom' else None)
        if kw is not None:
            out[code] = {**bar_agreement(rows, kw, f'{now:%Y%m%d}'), 'kiwoom_bars_today': len(kw),
                         'nh_rows': len(rows or [])}
        sleep(0.6)
    median = lambda xs: round(sorted(xs)[len(xs) // 2], 3) if xs else None  # noqa: E731
    return {'need': 'minute_bars', 'time': now.isoformat(timespec='seconds'), 'codes': out,
            'nh_median_s': median(nh_s), 'kiwoom_median_s': median(kw_s)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('need', choices=sorted([*NEEDS, 'mover_quotes', 'minute_bars']))
    parser.add_argument('--runs', type=int, default=3)
    args = parser.parse_args()
    from market_movers import constituents
    from src.config.loader import load_agent_config
    from src.tools.mcp import MCPServerAdapter
    universe = constituents(MCPServerAdapter('nhplug_readonly', load_agent_config().mcp_servers['nhplug_readonly']))
    if args.need == 'minute_bars':
        from nh_paper_isolated import SYMBOLS
        from watchlist import account_watchlist
        read = MCPServerAdapter('nhplug_readonly', load_agent_config().mcp_servers['nhplug_readonly'])
        codes = list(dict.fromkeys([*SYMBOLS.values(), *(c for c, _, _ in account_watchlist('paper-shared'))]))
        result = minute_agreement(read, codes)
        OUT.mkdir(parents=True, exist_ok=True)
        path = OUT / f"minute_bars-{datetime.now(KST):%Y%m%d-%H%M}.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=1))
        path.chmod(0o600)
        print(json.dumps(result, ensure_ascii=False))
        print(path)
        return
    if args.need == 'mover_quotes':
        from market_movers import targets
        read = MCPServerAdapter('nhplug_readonly', load_agent_config().mcp_servers['nhplug_readonly'])
        result = mover_agreement(read, universe, list(targets(universe)[0]))
        OUT.mkdir(parents=True, exist_ok=True)
        path = OUT / f"mover_quotes-{datetime.now(KST):%Y%m%d-%H%M}.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=1))
        path.chmod(0o600)
        print(json.dumps({k: v for k, v in result.items() if k not in ('only_nh', 'only_kiwoom')}, ensure_ascii=False))
        print(path)
        return
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
