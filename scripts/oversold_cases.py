"""낙폭과대 사례집 (case library): past sharp falls of large KRX stocks and ETFs, with what happened next.

User request 2026-11-13: review real past charts and trading data before designing the oversold-rebound strategy, and
let Jev and vibe-trading always see how similar past falls turned out. RESEARCH ONLY: no orders, no accounts.

`collect()` (weekly, or by hand) fetches, for ~85 large KRX stocks and ETFs:
- Yahoo daily bars for 10 years (plus KOSPI, KOSDAQ, Nasdaq, SOX and VIX);
- 60-minute bars for 2 years and 5-minute bars for 60 days (the shape of the day of the fall);
- Naver investor flows around each fall (foreign / institution / individual net buys, stocks only);
- Naver broker reports written two weeks before and after each fall (titles and their tone).
Every day whose low fell far below the previous close (stock 5%, 1x ETF 3%, 2x ETF 6%) becomes a case with the facts
known that day (fall, ATR multiple, market fall, Nasdaq the night before, prior trend, volume) and what followed
(1/3/5/10/20-day returns, best and worst point in 5 days, whether the low broke, the replayed intraday rule).
Stored in oversold-cases.sqlite3 next to the runner (cases + the daily series the live lookup needs).

`similar(code, drop_pct, atr_mult, ...)` returns how the nearest past cases of the same kind turned out, for the
engines' prompts and the dashboard. `CLI --collect | --report | --similar CODE DROP`.
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import sqlite3
import statistics
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import api_calls  # noqa: F401,E402 - counts this process's external API calls (dashboard 'API 연동')
from sqlite_ro import ro_connect  # noqa: E402

KST = ZoneInfo('Asia/Seoul')
ROOT = Path.home() / '.vibe-trading/nhplug/shared-etf-paper'
DB = Path(os.environ.get('VIBE_TRADING_OVERSOLD_CASES') or ROOT / 'oversold-cases.sqlite3')
THRESHOLD = {'stock': 5.0, 'etf': 3.0, 'lev': 6.0, 'overseas_etf': 3.0,  # % below the previous close at the day low
             'kr_index': 3.0, 'us_index': 2.5, 'oil': 5.0, 'crypto': 7.0}
KR_KINDS = ('stock', 'etf', 'lev')  # judged against KOSPI/KOSDAQ, replayed intraday with the live rule
INDICES = {'kospi': '^KS11', 'kosdaq': '^KQ11', 'nasdaq': '^IXIC', 'sox': '^SOX', 'vix': '^VIX'}
KOSDAQ_ETFS = {'229200', '233740'}
# (code, name, market, kind); names checked against Yahoo 2026-11-13
UNIVERSE = [tuple(row.split(' ', 3)) for row in """\
005930 삼성전자 KS stock|000660 SK하이닉스 KS stock|373220 LG에너지솔루션 KS stock|207940 삼성바이오로직스 KS stock|\
005380 현대차 KS stock|000270 기아 KS stock|068270 셀트리온 KS stock|005490 POSCO홀딩스 KS stock|035420 NAVER KS stock|\
035720 카카오 KS stock|051910 LG화학 KS stock|006400 삼성SDI KS stock|105560 KB금융 KS stock|055550 신한지주 KS stock|\
012330 현대모비스 KS stock|028260 삼성물산 KS stock|066570 LG전자 KS stock|003550 LG KS stock|096770 SK이노베이션 KS stock|\
034730 SK KS stock|017670 SK텔레콤 KS stock|030200 KT KS stock|032830 삼성생명 KS stock|086790 하나금융지주 KS stock|\
316140 우리금융지주 KS stock|009150 삼성전기 KS stock|018260 삼성에스디에스 KS stock|011200 HMM KS stock|010130 고려아연 KS stock|\
329180 HD현대중공업 KS stock|012450 한화에어로스페이스 KS stock|042660 한화오션 KS stock|009540 HD한국조선해양 KS stock|\
047810 한국항공우주 KS stock|034020 두산에너빌리티 KS stock|267260 HD현대일렉트릭 KS stock|010140 삼성중공업 KS stock|\
015760 한국전력 KS stock|033780 KT&G KS stock|003670 포스코퓨처엠 KS stock|259960 크래프톤 KS stock|352820 하이브 KS stock|\
251270 넷마블 KS stock|036570 엔씨소프트 KS stock|323410 카카오뱅크 KS stock|377300 카카오페이 KS stock|024110 기업은행 KS stock|\
000810 삼성화재 KS stock|011170 롯데케미칼 KS stock|042700 한미반도체 KS stock|064350 현대로템 KS stock|000100 유한양행 KS stock|\
128940 한미약품 KS stock|302440 SK바이오사이언스 KS stock|011070 LG이노텍 KS stock|034220 LG디스플레이 KS stock|\
090430 아모레퍼시픽 KS stock|097950 CJ제일제당 KS stock|271560 오리온 KS stock|004020 현대제철 KS stock|196170 알테오젠 KQ stock|\
247540 에코프로비엠 KQ stock|086520 에코프로 KQ stock|028300 HLB KQ stock|403870 HPSP KQ stock|058470 리노공업 KQ stock|\
141080 리가켐바이오 KQ stock|145020 휴젤 KQ stock|263750 펄어비스 KQ stock|293490 카카오게임즈 KQ stock|357780 솔브레인 KQ stock|\
039030 이오테크닉스 KQ stock|214150 클래시스 KQ stock|277810 레인보우로보틱스 KQ stock|112040 위메이드 KQ stock|\
240810 원익IPS KQ stock|069500 KODEX200 KS etf|102110 TIGER200 KS etf|229200 KODEX코스닥150 KS etf|091160 KODEX반도체 KS etf|\
091170 KODEX은행 KS etf|305720 KODEX2차전지산업 KS etf|122630 KODEX레버리지 KS lev|233740 KODEX코스닥150레버리지 KS lev|\
494310 KODEX반도체레버리지 KS lev""".split('|')]
# User request 2026-11-13: the sharp falls of the markets themselves too (indices, oil, crypto; Yahoo symbols) and the
# KRX-listed ETFs that track them, so dip-buys across markets can be replayed and compared.
GLOBAL = [('^KS11', '코스피', '', 'kr_index'), ('^KQ11', '코스닥', '', 'kr_index'), ('^GSPC', 'S&P500', '', 'us_index'),
          ('^IXIC', '나스닥', '', 'us_index'), ('^NDX', '나스닥100', '', 'us_index'), ('^SOX', '필라델피아반도체', '', 'us_index'),
          ('CL=F', 'WTI원유', '', 'oil'), ('BZ=F', '브렌트유', '', 'oil'), ('BTC-USD', '비트코인', '', 'crypto'),
          ('ETH-USD', '이더리움', '', 'crypto'), ('SOL-USD', '솔라나', '', 'crypto'),
          ('133690', 'TIGER미국나스닥100', 'KS', 'overseas_etf'), ('360750', 'TIGER미국S&P500', 'KS', 'overseas_etf'),
          ('261220', 'KODEX WTI원유선물(H)', 'KS', 'overseas_etf')]
NAMES = {code: name for code, name, _, _ in UNIVERSE + GLOBAL}
KINDS = {code: kind for code, _, _, kind in UNIVERSE + GLOBAL}
MARKETS = {code: market for code, _, market, _ in UNIVERSE + GLOBAL}


def symbol_of(code):
    """Yahoo symbol: KRX codes get .KS/.KQ, the global ones are already symbols."""
    market = MARKETS.get(code, 'KS')
    return f'{code}.{market}' if market else code


def yahoo_symbols(code, kosdaq=()):
    """Yahoo symbols to try in order: the library's checked market when it knows the code (207940 trades on KOSPI
    though it starts with 2), .KQ for a code in `kosdaq` (kosdaq_codes()), else .KS then .KQ."""
    if code in MARKETS:
        return [symbol_of(code)]
    return [f'{code}.KQ'] if code in kosdaq else [f'{code}.KS', f'{code}.KQ']


def kosdaq_codes():
    """Active KOSDAQ stock codes from the Toss stock master (one request), or an empty set when Toss is not set up or
    fails: then yahoo_symbols() asks .KS first as before. 11-19: 21 KOSDAQ issue candidates each cost a Yahoo 404."""
    try:
        import toss_data
        if not toss_data.configured():
            return set()
        rows = toss_data.get('/api/v1/stocks/all', {'market': 'KOSDAQ', 'status': 'ACTIVE', 'securityType': 'STOCK'}) or []
        return {r['symbol'] for r in rows if isinstance(r, dict) and isinstance(r.get('symbol'), str)}
    except Exception:  # noqa: BLE001 - a lookup aid: without it the old order still finds the bars
        return set()
# similarity weights: the fall itself and the market's fall first (the case study: market-wide crashes rebound,
# company-specific falls do not), then the night before and the prior trend
FEATURES = {'drop_pct': 1.5, 'mkt_low_pct': 1.2, 'atr_mult': 1.0, 'rel_drop': 0.7, 'nasdaq_prev': 0.7, 'prior_20d': 0.5,
            'dist_60d_high': 0.5, 'down_streak': 0.3}


def _get(url, timeout=12):
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(2_000_000).decode('utf-8', 'replace'))


def pct(new, old):
    return None if new is None or not old else round((new / old - 1) * 100, 2)


def prev_session(daily):
    """f(kr_date) -> (change %, close) of the last session of `daily` that ended before that date (US: the night before)."""
    days = [b[0].date() for b in daily]
    changes = [(None, daily[0][4])] + [((daily[i][4] / daily[i - 1][4] - 1) * 100, daily[i][4]) for i in range(1, len(daily))]

    def at(d):
        i = bisect.bisect_left(days, d) - 1
        return changes[i] if i >= 0 else (None, None)
    return at


KIWOOM_DAYS = 400  # ka10080 answered a year back (2026-11-17 check); older days keep the 60-minute fallback


def kiwoom_bars(code, day, today=None):
    """[(dt, o, h, l, c)] Kiwoom 1-minute 수정주가 bars of a fall day too old for Yahoo 5-minute; [] when unavailable."""
    from zoneinfo import ZoneInfo
    import kiwoom_data
    if ((today or datetime.now(ZoneInfo('Asia/Seoul')).date()) - day).days > KIWOOM_DAYS or not kiwoom_data.configured():
        return []
    try:
        return [b[:5] for b in kiwoom_data.day_bars(code, day, adjusted=True)['bars']]
    except Exception:  # Kiwoom down or refused: the 60-minute fallback
        return []


def by_day(bars):
    out = {}
    for b in bars:
        out.setdefault(b[0].date(), []).append(b)
    return out


def market_of(code, market):
    return 'kosdaq' if market == 'KQ' or code in KOSDAQ_ETFS else 'kospi'


def daily_features(daily, i, atr):
    """Facts known on day i (the fall day) from daily bars [(dt, o, h, l, c, v)]."""
    dt, o, h, lo, c, v = daily[i]
    pc = daily[i - 1][4]
    closes = [b[4] for b in daily[:i]]
    streak = 0
    for a, b in zip(reversed(closes[1:]), reversed(closes[:-1])):
        if a >= b:
            break
        streak += 1
    vols = [b[5] for b in daily[max(0, i - 20):i] if b[5]]
    a = atr.get(dt.date())
    drop = (lo / pc - 1) * 100
    return {'drop_pct': round(drop, 2), 'atr_pct': round(a, 2) if a else None, 'atr_mult': round(-drop / a, 2) if a else None,
            'open_gap_pct': pct(o, pc), 'close_pct': pct(c, pc), 'close_from_low_pct': pct(c, lo),
            'gap_recovered_pct': round((c - lo) / (pc - lo) * 100, 1) if pc > lo else None,
            'prior_5d': pct(pc, closes[-6]) if len(closes) >= 6 else None,
            'prior_20d': pct(pc, closes[-21]) if len(closes) >= 21 else None,
            'dist_60d_high': pct(pc, max(b[2] for b in daily[max(0, i - 60):i])) if i >= 20 else None,
            'down_streak': streak, 'vol_ratio': round(v / statistics.mean(vols), 2) if vols and v else None}


def outcomes(daily, i):
    """What followed day i, from its close (and from its low, the best possible entry)."""
    c, lo = daily[i][4], daily[i][3]
    nxt = daily[i + 1:i + 21]
    out = {f'r{n}': pct(nxt[n - 1][4], c) if len(nxt) >= n else None for n in (1, 3, 5, 10, 20)}
    out['next_open'] = pct(nxt[0][1], c) if nxt else None
    out['r3_from_low'] = pct(nxt[2][4], lo) if len(nxt) >= 3 else None
    five = nxt[:5]
    if len(five) == 5:
        floor = min(b[3] for b in five)
        out.update({'max_up_5': pct(max(b[2] for b in five), c), 'max_dn_5': pct(floor, c), 'low_broken_5': floor < lo,
                    'below_low_5': pct(floor, lo)})
        out['after'] = ('further_fall' if floor <= lo * 0.97 else 'retest' if floor < lo
                        else 'rebound' if out['r5'] >= 3 else 'flat')
    else:
        out['after'] = 'pending'
    prev = daily[i - 1][4]
    out['days_to_prev_close'] = next((n + 1 for n, b in enumerate(nxt) if b[4] >= prev), None)
    return out


def intraday(bars, pc, atr_a, next_close, kind, minutes):
    """Low time and the current rule replayed on that day's bars (5- or 60-minute)."""
    import oversold_outcomes
    import oversold_research
    from strategy_settings import DEFAULTS
    low_bar = min(bars, key=lambda b: b[3])
    params = oversold_outcomes.rule_params(DEFAULTS, kind)
    params['n'] = max(1, math.ceil(DEFAULTS['oversold_calm_min'] / minutes))
    sim = oversold_research.simulate(bars, pc, atr_a or 0.0, next_close, params)
    return {'res': f'{minutes}m', 'low_time': low_bar[0].strftime('%H:%M'),
            'rule': None if not sim else {'return_pct': round(sim[0], 2), 'exit': sim[1], 'adverse_pct': round(sim[2], 2)}}


def flows(code, day):
    """Net buys (억 원, approx. quantity x close) by investor around `day` from Naver; {} when unavailable."""
    try:
        rows = _get(f'https://m.stock.naver.com/api/stock/{code}/trend?pageSize=12&bizdate={(day + timedelta(days=8)):%Y%m%d}')
    except Exception:
        return {}
    num = lambda v: float(str(v).replace(',', '').replace('+', '') or 0)  # noqa: E731
    table = {}
    for r in rows:
        try:
            close = num(r['closePrice'])
            table[datetime.strptime(r['bizdate'], '%Y%m%d').date()] = {
                k: round(num(r[f'{k}PureBuyQuant']) * close / 1e8, 1) for k in ('foreigner', 'organ', 'individual')}
        except (KeyError, ValueError):
            continue
    days = sorted(table)
    if day not in table:
        return {}
    i = days.index(day)
    prior = [table[d] for d in days[max(0, i - 3):i]]
    out = {f'{k}_day': table[day][k] for k in ('foreigner', 'organ', 'individual')}
    out.update({f'{k}_prior3': round(sum(p[k] for p in prior), 1) for k in ('foreigner', 'organ')})
    if i + 1 < len(days):
        out.update({f'{k}_next1': table[days[i + 1]][k] for k in ('foreigner', 'organ')})
    return out


def reports(code, since, pages=12):
    """Broker reports [(date, broker, title, tone)] back to `since` from Naver (newest first)."""
    import oversold
    out = []
    for page in range(1, pages + 1):
        try:
            rows = _get(f'https://m.stock.naver.com/api/research/stock/{code}?page={page}&pageSize=50')
        except Exception:
            break
        for r in rows or []:
            try:
                d = date.fromisoformat(r['writeDate'])
            except (KeyError, ValueError):
                continue
            text = f"{r.get('title', '')} {r.get('previewContent', '')[:300]}"
            out.append((d, r.get('brokerName'), str(r.get('title', ''))[:60], oversold.report_tone(text)))
        if not rows or len(rows) < 50 or (out and out[-1][0] < since):
            break
        time.sleep(0.2)
    return out


def report_window(items, day):
    before = [x for x in items if day - timedelta(days=14) <= x[0] < day]
    after = [x for x in items if day <= x[0] <= day + timedelta(days=14)]
    tone = lambda xs: {t: sum(x[3] == t for x in xs) for t in ('up', 'down', 'neutral') if any(x[3] == t for x in xs)}  # noqa: E731
    return {'before': len(before), 'before_tone': tone(before), 'after': len(after), 'after_tone': tone(after),
            'titles': [f"{x[0]} {x[1]}: {x[2]}" for x in (before + after)[:4]]}


def collect(db=DB, universe=UNIVERSE + GLOBAL, years=10, with_flows=True, with_reports=True, log=print, recent_days=None):
    """Fetch everything and rebuild the case table; returns {'cases': n, ...}. `recent_days`: only (re)write the
    cases of the last N days (the daily refresh: new falls and the outcomes of recent ones), and the series."""
    cutoff = (datetime.now(KST).date() - timedelta(days=recent_days)).isoformat() if recent_days else ''
    import oversold_replay
    import oversold_research as research
    index = {name: research.fetch(symbol, '1d', f'{years}y', volume=True) for name, symbol in INDICES.items()}
    index_at = {name: {b[0].date(): b for b in bars} for name, bars in index.items()}
    us = {name: prev_session(index[name]) for name in ('nasdaq', 'sox', 'vix') if index[name]}
    series, cases, refreshed = {}, [], set()
    for code, name, market, kind in universe:
        symbol = f'{code}.{market}' if market else code
        daily = [b for b in research.fetch(symbol, '1d', f'{years}y', volume=True)
                 if (b[5] or kind != 'stock') and min(b[1:5]) > 0]  # WTI settled below zero on 2020-04-20: no such bars
        if len(daily) < 30:
            log(f'skip {code} {name}: {len(daily)} days')
            continue
        series[symbol] = daily
        refreshed.add(code)
        atr = research.atr_pct([b[:5] for b in daily])
        mkt = index_at[market_of(code, market)] if kind in KR_KINDS else {}
        mkt_days = sorted(mkt)
        found = []
        last_event = None
        for i in range(21, len(daily)):
            pc, lo = daily[i - 1][4], daily[i][3]
            if pc <= 0 or (lo / pc - 1) * 100 > -THRESHOLD[kind]:
                continue
            d = daily[i][0].date()
            if d.isoformat() < cutoff:
                last_event = d
                continue
            row = {'code': code, 'name': name, 'kind': kind, 'day': d.isoformat(), **daily_features(daily, i, atr), **outcomes(daily, i)}
            m = mkt.get(d)
            j = bisect.bisect_left(mkt_days, d)
            mprev = mkt[mkt_days[j - 1]][4] if j > 0 else None
            row['mkt_low_pct'] = pct(m[3], mprev) if m and mprev else None
            row['mkt_close_pct'] = pct(m[4], mprev) if m and mprev else None
            row['rel_drop'] = round(row['drop_pct'] - row['mkt_low_pct'], 2) if row['mkt_low_pct'] is not None else None
            for key, f in us.items():
                change, close = f(d)
                row[f'{key}_prev'] = round(change, 2) if change is not None else None
                if key == 'vix':
                    row['vix_level'] = round(close, 1) if close else None
            row['days_since_prev_case'] = (d - last_event).days if last_event else None
            last_event = d
            row['replay'] = oversold_replay.replay(daily, i, kind, atr.get(d))
            found.append((i, row))
        if found and kind in KR_KINDS:  # the shape of each fall day: Yahoo 5-minute (60 days), Kiwoom 1-minute (a year), 60-minute (2 years)
            h60, m5 = by_day(research.fetch(symbol, '60m', '2y')), by_day(research.fetch(symbol, '5m', '60d'))
            for i, row in found:
                d = daily[i][0].date()
                bars, minutes = (m5.get(d), 5) if m5.get(d) else (kiwoom_bars(code, d), 1)
                if not bars:
                    bars, minutes = h60.get(d), 60
                if bars:
                    row['intraday'] = intraday(bars, daily[i - 1][4], atr.get(d), daily[i + 1][4] if i + 1 < len(daily) else None,
                                               kind, minutes)
        found = [row for _, row in found]
        if found and kind == 'stock' and with_flows:
            for row in found:
                if row['day'] >= '2019-01-01' and (row['atr_mult'] or 0) >= 1.5:  # keep Naver requests modest
                    row['flows'] = flows(code, date.fromisoformat(row['day']))
                    time.sleep(0.15)
        if found and kind == 'stock' and with_reports:
            items = reports(code, date.fromisoformat(found[0]['day']) - timedelta(days=30))
            for row in found:
                row['reports'] = report_window(items, date.fromisoformat(row['day']))
        cases += found
        log(f'{code} {name}: {len(daily)} days, {len(found)} cases')
    for name, symbol in INDICES.items():
        series[symbol] = index[name]
    write(db, cases, series, replace=(refreshed, cutoff))
    return {'cases': len(cases), 'codes': len({c['code'] for c in cases}), 'db': str(db)}


def refresh_series(db=DB, universe=UNIVERSE + GLOBAL, rng='1mo'):
    """Rewrite the last month of every daily series before the open (market_movers at 08:50): the after-close collect
    runs before the closing auction and the US session, so the last KRX bar it stores is not the close and the night's
    Nasdaq is missing (prev_closes, market_prior5, live_features). Series only; returns the bars written."""
    import oversold_research as research
    series = {symbol_of(code): [b for b in research.fetch(symbol_of(code), '1d', rng, volume=True)
                                if (b[5] or kind != 'stock') and min(b[1:5]) > 0] for code, _, _, kind in universe}
    series.update({s: research.fetch(s, '1d', rng, volume=True) for s in INDICES.values() if s not in series})
    series = {s: bars for s, bars in series.items() if bars}
    write(db, [], series)
    return sum(map(len, series.values()))


def fill_index_series(now, db=DB):
    """The KOSPI and KOSDAQ series that lack the previous session, fetched again (market_movers, every round until they
    have it); the symbols completed. 11-24: Yahoo had no 11-23 index bar at 08:50 and market_prior5 refuses a series that
    missed a session. Today's forming bar is left out."""
    import oversold_research as research
    if not Path(db).exists():
        return []
    today, filled = now.date(), []
    day = previous_session(today)
    for symbol in (INDICES['kospi'], INDICES['kosdaq']):
        with ro_connect(db) as conn:
            last = conn.execute('SELECT max(day) FROM series WHERE symbol = ? AND day < ?', (symbol, today.isoformat())).fetchone()[0]
        if last == day.isoformat():
            continue
        bars = [b for b in research.fetch(symbol, '1d', '1mo', volume=True) if b[0].date() < today]
        if any(b[0].date() == day for b in bars):
            write(db, [], {symbol: bars})
            filled.append(symbol)
    return filled


def write(db, cases, series, replace=None):
    """Store cases and daily series. `replace`: (codes, from_day) whose earlier cases are removed first, so a day that
    no longer qualifies (a corrected bar, a changed threshold) does not linger."""
    db = Path(db)
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db, timeout=10) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS cases (key TEXT PRIMARY KEY, code TEXT, day TEXT, kind TEXT, data TEXT)')
        conn.execute('CREATE TABLE IF NOT EXISTS series (symbol TEXT, day TEXT, o REAL, h REAL, l REAL, c REAL, v REAL, PRIMARY KEY (symbol, day))')
        conn.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)')
        if replace:
            codes, since = replace
            conn.executemany('DELETE FROM cases WHERE code = ? AND day >= ?', [(code, since or '') for code in codes])
        conn.executemany('INSERT OR REPLACE INTO cases VALUES (?, ?, ?, ?, ?)',
                         [(f"{c['code']}:{c['day']}", c['code'], c['day'], c['kind'], json.dumps(c, ensure_ascii=False, default=str)) for c in cases])
        conn.executemany('INSERT OR REPLACE INTO series VALUES (?, ?, ?, ?, ?, ?, ?)',
                         [(symbol, b[0].date().isoformat(), *b[1:5], b[5] if len(b) > 5 else None) for symbol, bars in series.items()
                          for b in bars if min(b[1:5]) > 0])
        conn.execute('DELETE FROM series WHERE o <= 0 OR h <= 0 OR l <= 0 OR c <= 0')
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('collected', ?)", (datetime.now(KST).isoformat(timespec='seconds'),))
    db.chmod(0o600)


def rereplay(db=DB):
    """Recompute every case's dip-buy replay from the stored daily series (no download): after the strategies change,
    or to fill outcomes as days pass. Returns the number of cases updated."""
    import oversold_replay
    import oversold_research as research
    db = Path(db)
    with sqlite3.connect(db, timeout=10) as conn:
        rows = conn.execute('SELECT key, code, day, data FROM cases').fetchall()
        by_code = {}
        for key, code, day, data in rows:
            by_code.setdefault(code, []).append((key, day, json.loads(data)))
        updates = []
        for code, items in by_code.items():
            bars = conn.execute('SELECT day, o, h, l, c, v FROM series WHERE symbol = ? ORDER BY day', (symbol_of(code),)).fetchall()
            daily = [(datetime.fromisoformat(d), o, h, lo, c, v) for d, o, h, lo, c, v in bars]
            index = {d: n for n, (d, *_) in enumerate(bars)}
            atr = research.atr_pct([b[:5] for b in daily])
            for key, day, case in items:
                i = index.get(day)
                if i is None or i < 1:
                    continue
                case['replay'] = oversold_replay.replay(daily, i, case['kind'], atr.get(date.fromisoformat(day)))
                updates.append((json.dumps(case, ensure_ascii=False, default=str), key))
        conn.executemany('UPDATE cases SET data = ? WHERE key = ?', updates)
    return len(updates)


def refresh_v2_versioned(db=DB, settings=None):
    """Archive previous v2 results and atomically refresh only v2 using stored daily bars."""
    import oversold_replay
    run_id = datetime.now(KST).isoformat(timespec='microseconds')
    with sqlite3.connect(Path(db), timeout=30) as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('CREATE TABLE IF NOT EXISTS v2_replay_history (run_id TEXT NOT NULL, case_key TEXT NOT NULL, previous TEXT, updated TEXT NOT NULL, PRIMARY KEY(run_id, case_key))')
        rows = conn.execute('SELECT key,code,day,data FROM cases').fetchall()
        by_code = {}
        for key, code, day, data in rows:
            by_code.setdefault(code, []).append((key, day, json.loads(data)))
        updates, archive = [], []
        for code, items in by_code.items():
            daily = conn.execute('SELECT day,o,h,l,c,v FROM series WHERE symbol=? ORDER BY day', (symbol_of(code),)).fetchall()
            index = {row[0]: n for n, row in enumerate(daily)}
            for key, day, case in items:
                i = index.get(day)
                if i is None or i < 1:
                    continue
                replay = case.setdefault('replay', {})
                old = replay.get('v2_plan')
                new = oversold_replay.run_v2(daily, i, case['kind'], settings)
                archive.append((run_id, key, json.dumps(old, ensure_ascii=False), json.dumps(new, ensure_ascii=False)))
                replay['v2_plan'] = new
                updates.append((json.dumps(case, ensure_ascii=False, default=str), key))
        conn.executemany('INSERT INTO v2_replay_history VALUES (?,?,?,?)', archive)
        conn.executemany('UPDATE cases SET data=? WHERE key=?', updates)
    return {'run_id': run_id, 'updated': len(updates), 'rule_version': oversold_replay.V2_RULE_VERSION}


_CACHE = {}


def load(db=DB):
    """All cases (list of dicts), cached until the file changes; [] without a library."""
    try:
        stamp = Path(db).stat().st_mtime_ns
    except OSError:
        return []
    if _CACHE.get('stamp') != (str(db), stamp):
        from sqlite_ro import ro_connect
        with ro_connect(Path(db)) as conn:
            _CACHE.update(stamp=(str(db), stamp), cases=[json.loads(d) for (d,) in conn.execute('SELECT data FROM cases ORDER BY day')])
    return _CACHE['cases']


def live_features(code, drop_pct, atr_mult=None, today=None, db=DB, rel_drop=None, mkt_low_pct=None):
    """The similarity features for a fall happening now: the stored daily series gives the prior trend and the
    Nasdaq of the night before; the live quote gives the fall."""
    from sqlite_ro import ro_connect
    today = today or datetime.now(KST).date()
    us_day = today - timedelta(days=1)  # the cases' nasdaq_prev: the US session of the night before (prev_session)
    while us_day.weekday() >= 5:  # shortcut: no US holiday table, so the day after one has no Nasdaq feature; add one if it matters
        us_day -= timedelta(days=1)
    today = today.isoformat()
    if rel_drop is None and mkt_low_pct is not None and drop_pct is not None:
        rel_drop = round(drop_pct - mkt_low_pct, 2)
    out = {'drop_pct': drop_pct, 'atr_mult': atr_mult, 'rel_drop': rel_drop, 'mkt_low_pct': mkt_low_pct}
    try:
        with ro_connect(Path(db)) as conn:
            rows = conn.execute('SELECT h, c FROM series WHERE symbol = ? AND day < ? ORDER BY day DESC LIMIT 61',
                                (symbol_of(code), today)).fetchall()
            nq = conn.execute("SELECT day, c FROM series WHERE symbol = '^IXIC' AND day < ? ORDER BY day DESC LIMIT 2", (today,)).fetchall()
    except (OSError, sqlite3.Error):
        return out
    if len(rows) >= 21:
        closes = [c for _, c in rows]
        out['prior_20d'] = pct(closes[0], closes[20])
        out['dist_60d_high'] = pct(closes[0], max(h for h, _ in rows[:60]))
        out['down_streak'] = next((n for n in range(len(closes) - 1) if closes[n] >= closes[n + 1]), 0)
    if len(nq) == 2 and nq[0][0] == us_day.isoformat():  # a series without last night's session would show the night before
        out['nasdaq_prev'] = pct(nq[0][1], nq[1][1])
    return out


def similar(code, features, k=40, db=DB, cases=None):
    """How the k nearest past cases of the same kind turned out. `features`: live_features() (any subset)."""
    kind = KINDS.get(code)
    if kind is None:
        import oversold
        kind = oversold.kind(code)
    pool = [c for c in (cases if cases is not None else load(db)) if c['kind'] == kind and c.get('r5') is not None
            and c['day'] < (features.get('today') or '9999')]
    if len(pool) < 10:
        return {'n': 0, 'pool': len(pool), 'kind': kind}
    used = {f: w for f, w in FEATURES.items() if features.get(f) is not None}
    scale = {}
    for f in used:
        values = [c[f] for c in pool if c.get(f) is not None]
        scale[f] = statistics.pstdev(values) or 1.0 if len(values) > 2 else 1.0

    def distance(c):
        parts = [(w, ((c[f] - features[f]) / scale[f]) ** 2) for f, w in used.items() if c.get(f) is not None]
        return math.sqrt(sum(w * d for w, d in parts) / sum(w for w, _ in parts)) if parts else 9.9
    near = sorted(pool, key=distance)[:k]
    med = lambda key: round(statistics.median(v), 2) if (v := [c[key] for c in near if c.get(key) is not None]) else None  # noqa: E731
    share = lambda test: round(sum(1 for c in near if test(c)) / len(near) * 100)  # noqa: E731
    rules = [c['intraday']['rule']['return_pct'] for c in near if (c.get('intraday') or {}).get('rule')]
    return {'n': len(near), 'pool': len(pool), 'kind': kind, 'same_code': sum(c['code'] == code for c in near),
            'r1_med': med('r1'), 'r3_med': med('r3'), 'r5_med': med('r5'), 'r10_med': med('r10'),
            'r3_from_low_med': med('r3_from_low'), 'max_up_5_med': med('max_up_5'), 'max_dn_5_med': med('max_dn_5'),
            'win_r3_pct': share(lambda c: (c.get('r3') or 0) > 0), 'low_broken_5_pct': share(lambda c: c.get('low_broken_5')),
            'further_fall_pct': share(lambda c: c.get('after') == 'further_fall'),
            'rebound_pct': share(lambda c: c.get('after') == 'rebound'),
            'rule_n': len(rules), 'rule_mean_pct': round(statistics.mean(rules), 2) if rules else None,
            'examples': [f"{c['day']} {c['name']} 저가 {c['drop_pct']:+.1f}% → 3일 {c['r3']:+.1f}% ({AFTER.get(c['after'], c['after'])})"
                         for c in near[:3] if c.get('r3') is not None]}


def stats(rows):
    """Pooled outcome of cases: medians from the close, win and further-fall shares (cases with 5 days after)."""
    done = [c for c in rows if c.get('r5') is not None]
    if not done:
        return {'n': 0}
    med = lambda key: round(statistics.median(v), 2) if (v := [c[key] for c in done if c.get(key) is not None]) else None  # noqa: E731
    share = lambda test: round(sum(1 for c in done if test(c)) / len(done) * 100)  # noqa: E731
    return {'n': len(done), 'r1': med('r1'), 'r3': med('r3'), 'r5': med('r5'), 'r10': med('r10'), 'r20': med('r20'),
            'r3_from_low': med('r3_from_low'), 'max_up_5': med('max_up_5'), 'max_dn_5': med('max_dn_5'),
            'win_r3': share(lambda c: (c.get('r3') or 0) > 0), 'win_r5': share(lambda c: c['r5'] > 0),
            'further_fall': share(lambda c: c.get('after') == 'further_fall'), 'rebound': share(lambda c: c.get('after') == 'rebound')}


DROP_BUCKETS = {'stock': (5, 7, 10, 15), 'etf': (3, 4, 6), 'lev': (6, 8, 12), 'overseas_etf': (3, 4, 6),
                'kr_index': (3, 4, 6), 'us_index': (2.5, 3.5, 5), 'oil': (5, 7, 10), 'crypto': (7, 10, 15)}


def breakdown(cases, kind):
    """[(label, stats)] of one kind by depth of the fall (low vs previous close)."""
    mine = [c for c in cases if c['kind'] == kind]
    edges = DROP_BUCKETS[kind] + (999,)
    out = [('전체', stats(mine))]
    for lo, hi in zip(edges, edges[1:]):
        out.append((f"−{lo}~{hi}%" if hi < 999 else f"−{lo}% 이상", stats([c for c in mine if lo <= -c['drop_pct'] < hi])))
    return out


AFTER = {'rebound': '반등', 'flat': '횡보', 'retest': '저가 재시험', 'further_fall': '추가 하락', 'pending': '진행 중'}


def brief(code, drop_pct, atr_mult=None, mkt_low_pct=None):
    """similar() with live features, compact for a prompt; {} without a library."""
    try:
        return compact(similar(code, live_features(code, drop_pct, atr_mult, mkt_low_pct=mkt_low_pct)))
    except Exception:  # a missing or locked library only means no history in the prompt
        return {}


ABOUT = ('Instruments down at least half of the oversold threshold today (stock 2.5%, 1x ETF 1.5%, 2x ETF 3%) and how '
         'the nearest past falls of the same kind went on over 10 years of KRX data: median 1/3/5-day returns from the '
         'close, the share whose low broke 3% further (further_fall_pct) or that rebounded, with examples. A base rate '
         'for judging whether a fall is a chance or a warning, not a forecast; it does not change any trading rule.')
NOTICE = {k: v / 2 for k, v in THRESHOLD.items()}  # half the case threshold: worth a look at the past, not a trigger
INVERSE = {'114800', '251340', '252670'}


def compact(view):
    keep = ('n', 'same_code', 'r1_med', 'r3_med', 'r5_med', 'r3_from_low_med', 'win_r3_pct', 'further_fall_pct',
            'rebound_pct', 'max_dn_5_med', 'examples')
    return {k: view[k] for k in keep if view.get(k) is not None} if view.get('n') else {}


def prev_closes(codes, today=None, db=DB):
    """{code: last stored close before today} from the daily series (refreshed after every close)."""
    from sqlite_ro import ro_connect
    today = today or datetime.now(KST).date()
    expected, today = previous_session(today).isoformat(), today.isoformat()
    out = {}
    try:
        with ro_connect(Path(db)) as conn:
            for code in codes:
                row = conn.execute('SELECT c, day FROM series WHERE symbol = ? AND day < ? ORDER BY day DESC LIMIT 1',
                                   (symbol_of(code), today)).fetchone()
                if row and row[1] == expected:  # a series that missed a refresh would show a false fall
                    out[code] = row[0]
    except (OSError, sqlite3.Error):
        pass
    return out


def market_of_code(code, hint=None):
    """'kospi' / 'kosdaq' for a KRX code the library knows (or a hint such as a scanner's 'KOSDAQ'); None otherwise,
    so an unknown stock is never judged against the wrong market."""
    import oversold
    if code in KOSDAQ_ETFS or MARKETS.get(code) == 'KQ':
        return 'kosdaq'
    if MARKETS.get(code) == 'KS' or code in oversold.ETFS:
        return 'kospi'
    hint = str(hint or '').upper()
    return 'kosdaq' if 'KOSDAQ' in hint or '코스닥' in hint else 'kospi' if 'KOSPI' in hint or '코스피' in hint else None


_SESSIONS = {}


def previous_session(today):
    """The KRX session before `today` (weekends and, when the holiday table loads, holidays skipped)."""
    if today not in _SESSIONS:
        try:
            from kr_swing_v5_daily_data import krx_holidays
            holidays = krx_holidays({today.year, today.year - 1})
        except Exception:
            holidays = set()
        day = today - timedelta(days=1)
        while day.weekday() >= 5 or day in holidays:
            day -= timedelta(days=1)
        _SESSIONS.clear()
        _SESSIONS[today] = day
    return _SESSIONS[today]


def context(changes, lows=None, names=None, limit=5, db=DB, market=None, hints=None):
    """'How did similar past falls go' for every instrument that is down at least half the case threshold today.
    `changes`: {code: % vs the previous close now}; `lows`: {code: % of today's low so far} when known;
    `market`: {'kospi': %, 'kosdaq': %} the market's low so far (KODEX 200 / KODEX 코스닥150 as proxies).
    {} on a calm day, so the engines' prompts only grow when there is a fall to read."""
    import oversold
    rows = []
    for code, change in (changes or {}).items():
        if code in INVERSE or change is None:
            continue
        low = min(change, (lows or {}).get(code, change))
        kind = KINDS.get(code) or oversold.kind(code)
        if low <= -NOTICE[kind]:
            rows.append((low, code, change))
    out = {}
    for low, code, change in sorted(rows)[:limit]:
        try:
            mkt = (market or {}).get(market_of_code(code, (hints or {}).get(code)))
            view = compact(similar(code, live_features(code, round(low, 2), db=db, mkt_low_pct=mkt), db=db))
        except Exception:
            view = {}
        if view:
            out[code] = {'name': (names or {}).get(code) or NAMES.get(code, code), 'now_pct': round(change, 2),
                         'low_pct': round(low, 2), 'market_low_pct': None if mkt is None else round(mkt, 2), 'past_similar_falls': view}
    return out


def bars_context(bars, extra_changes=None, names=None, today=None, db=DB, hints=None):
    """context() from the runner's 1-minute bars ({key or code: rows}) and the stored previous closes;
    `extra_changes`: {code: % now} for instruments without a stored series (scanner stocks)."""
    try:  # reference only: a failure here must never stop a decision
        from etf_universe import SYMBOLS
        codes = {SYMBOLS.get(key, key): rows for key, rows in (bars or {}).items() if rows}
        prev = prev_closes(codes, today, db)
        changes, lows = {c: v for c, v in (extra_changes or {}).items() if isinstance(v, (int, float))}, {}
        day = (today or datetime.now(KST).date()).strftime('%Y%m%d')
        for code, rows in codes.items():
            rows = [r for r in rows if r.get('bsop_date', day) == day]  # opening-review rows add the previous session
            if code not in prev or not rows:
                continue
            try:
                latest = max(rows, key=lambda r: r['bsop_time'])
                changes[code] = (float(latest['stck_prpr']) / prev[code] - 1) * 100
                lows[code] = (min(float(r['stck_lwpr']) for r in rows) / prev[code] - 1) * 100
            except (KeyError, TypeError, ValueError):
                continue
        market = {name: min(changes[code], lows.get(code, changes[code]))
                  for name, code in (('kospi', '069500'), ('kosdaq', '229200')) if code in changes}
        falls = context(changes, lows, names, db=db, market=market, hints=hints)
    except Exception:
        return {}
    return {'about': ABOUT, 'falls': falls} if falls else {}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--collect', action='store_true')
    parser.add_argument('--years', type=int, default=10)
    parser.add_argument('--no-flows', action='store_true')
    parser.add_argument('--similar', nargs=2, metavar=('CODE', 'DROP'))
    parser.add_argument('--global-only', action='store_true', help='with --collect: only the indices, oil, crypto and overseas ETFs')
    parser.add_argument('--rereplay', action='store_true', help='recompute every replay from the stored series')
    args = parser.parse_args()
    if args.collect:
        print(json.dumps(collect(universe=GLOBAL if args.global_only else UNIVERSE + GLOBAL, years=args.years,
                                 with_flows=not args.no_flows, with_reports=not args.no_flows), ensure_ascii=False))
    if args.rereplay:
        print(json.dumps({'replayed': rereplay()}))
    if args.similar:
        print(json.dumps(brief(args.similar[0], float(args.similar[1])), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
