"""Market regime, flows, themes and global prices for the dashboard and the AI engines. Read-only; never orders.

Sources (public, may be delayed): Naver Finance mobile API for KOSPI/KOSDAQ (index history, investor and program
net buying, advance/decline counts, trading value), themes and the leading stocks of the strongest themes; Yahoo Finance
via yfinance for the daily closes of overseas indices, bitcoin, gold, oil, the dollar index and USD/KRW, and Yahoo's spark
endpoint for 5-minute US index futures, gold, oil, the dollar index and USD/KRW; Kiwoom REST (kiwoom_data, read-only) for today's program trading
by time and foreign-desk net buying; Toss Open API (toss_data) for today's KOSPI/KOSDAQ 1-minute path and the
net-buy leaders by investor (all payload only, not in summary()). NH Open API has no index, program-trading or market-wide investor endpoints.
The regime is a transparent moving-average rule, not a forecast.
"""
from __future__ import annotations

import functools
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import api_calls  # noqa: F401,E402 - counts this process's external API calls (dashboard 'API 연동')

KST = ZoneInfo('Asia/Seoul')
ROOT = Path.home() / '.vibe-trading/nhplug/shared-etf-paper'
FILE = ROOT / 'market-context.json'
NAVER = 'https://m.stock.naver.com/api'
HEADERS = {'User-Agent': 'Mozilla/5.0'}
INDICES = {'KOSPI': '코스피', 'KOSDAQ': '코스닥'}
GLOBAL = {'나스닥100': '^NDX', 'S&P500': '^GSPC', '다우지수': '^DJI', '필라델피아 반도체': '^SOX', 'VIX(변동성)': '^VIX',
          '비트코인': 'BTC-USD', '금 선물': 'GC=F', 'WTI유': 'CL=F', '브렌트유': 'BZ=F', '달러인덱스': 'DX-Y.NYB', '원/달러 환율': 'KRW=X'}
# Overseas prices now (user 2026-11-18): Yahoo's spark endpoint returns 5-minute closes and the previous session's close
# for all symbols in one request (yfinance's download gives neither that close nor, by default, CME's overnight bars).
SPARK = 'https://query1.finance.yahoo.com/v8/finance/spark'
GLOBAL_INTRADAY = {'나스닥100 선물': 'NQ=F', 'S&P500 선물': 'ES=F', '금 선물': 'GC=F', 'WTI유': 'CL=F', '달러인덱스': 'DX-Y.NYB',
                   '원/달러 환율': 'KRW=X'}
# CME quotes reach Yahoo about 10 minutes late (11/18 14:11: NQ/ES/GC/CL 10-13 min, DX/KRW 0-1 min behind); 30 minutes
# allows a quiet bar or a slow collect and drops a halted (07:00-08:00 KST) or dead feed.
GLOBAL_INTRADAY_MAX_LAG = 30  # minutes
THEME_LEADER_THEMES, THEME_LEADERS = 5, 3  # Naver member calls per collect, stocks kept per theme
# Major theme groups matched against Naver theme names; first match wins, so the order matters.
THEME_GROUPS = {
    '반도체': ('반도체', 'HBM', '소캠', 'CXL', '유리 기판', 'MLCC', 'S7'),
    '2차전지': ('2차전지', '리튬', '폐배터리', 'ESS', '전력저장'),
    '제약·바이오': ('제약', '바이오', '신약', '면역항암', '유전자', 'mRNA', '줄기세포', '비만치료', '오가노이드',
                 '보톡스', '백신', '치매', '탈모', '마이크로바이옴', '제대혈'),
    '로봇': ('로봇', '휴머노이드'),
    'AI·데이터센터': ('인공지능', 'AI', '챗봇', '데이터센터', '퓨리오사', '냉각', '클라우드'),
    '방산·우주': ('방위산업', '우주', '항공기부품', '드론', '스페이스X'),
    '조선·해운': ('조선', '해운'),
    '원전·전력': ('원자력', '핵융합', '전력설비', '전선', '스마트그리드'),
    '자동차': ('자동차', '전기차', '자율주행', '타이어', '스마트카'),
    '금융': ('은행', '증권', '보험', '지주사'),
    '엔터·게임': ('엔터', '게임', '음원', '웹툰', '영화', '영상콘텐츠'),
    '화장품·소비': ('화장품', '미용', '면세', '백화점', '편의점', '음식료'),
    '가상자산': ('가상화폐', '블록체인', '스테이블코인', '두나무', 'STO', 'NFT'),
}
MAX_AGE = timedelta(minutes=20)  # engines ignore an older snapshot


def num(text):
    """'+6,555' / '-1,119' / '5,375.36' -> float; None when missing."""
    try:
        return float(str(text).replace(',', '').replace('+', ''))
    except (TypeError, ValueError):
        return None


def naver(path):
    import requests
    response = requests.get(f'{NAVER}/{path}', headers=HEADERS, timeout=10)
    response.raise_for_status()
    return response.json()


def regime(closes):
    """상승장/하락장/횡보장 from daily closes (oldest first): price vs 20/60-day averages and the 20-day return."""
    if len(closes) < 61:
        raise ValueError('need 61 daily closes')
    last = closes[-1]
    ma20, ma60 = sum(closes[-20:]) / 20, sum(closes[-60:]) / 60
    ret20 = (last / closes[-21] - 1) * 100
    peak = max(closes[-120:])
    checks = [(last > ma20, last < ma20, f"종가가 20일선 {'위' if last > ma20 else '아래'}"),
              (ma20 > ma60, ma20 < ma60, f"20일선이 60일선 {'위' if ma20 > ma60 else '아래'}"),
              (ret20 > 3, ret20 < -3, f'20일 수익률 {ret20:+.1f}%')]
    score = sum(up for up, _, _ in checks) - sum(down for _, down, _ in checks)
    label = '상승장' if score >= 2 else '하락장' if score <= -2 else '횡보장'
    return {'label': label, 'score': score, 'close': last, 'ma20': round(ma20, 2), 'ma60': round(ma60, 2),
            'ret20_pct': round(ret20, 2), 'drawdown_pct': round((last / peak - 1) * 100, 2),
            'reasons': [text for _, _, text in checks]}


def today_flow(change_pct, rise, fall):
    """Intraday tone from the index change and advance/decline counts (separate from the daily regime)."""
    if change_pct is None or not rise or not fall:
        return '판단 불가'
    if change_pct >= 1 and rise >= 1.5 * fall:
        return '강세'
    if change_pct <= -1 and fall >= 1.5 * rise:
        return '약세'
    return '혼조'


def index_snapshot(code):
    data = naver(f'index/{code}/integration')
    info = {row.get('code'): row.get('value') for row in data.get('totalInfos') or []}
    basic = naver(f'index/{code}/basic')
    deal, program, breadth = data.get('dealTrendInfo') or {}, data.get('programTrendInfo') or {}, data.get('upDownStockInfo') or {}
    change = num(basic.get('fluctuationsRatio'))
    if change is not None and (basic.get('compareToPreviousPrice') or {}).get('name') == 'FALLING':
        change = -abs(change)
    days = []
    for page in (1, 2):  # 60 days per page, newest first
        days += [(row.get('localTradedAt'), num(row.get('closePrice'))) for row in naver(f'index/{code}/price?pageSize=60&page={page}')]
    days = [(d, c) for d, c in reversed(days) if d and c]
    closes = [c for _, c in days]
    rise, fall = num(breadth.get('riseCount')), num(breadth.get('fallCount'))
    return {'name': INDICES[code], 'close': num(basic.get('closePrice')), 'change_pct': change,
            'status': basic.get('marketStatus'), 'traded_at': basic.get('localTradedAt'),
            'value_100m': round(num(str(info.get('accumulatedTradingValue', '')).removesuffix('백만')) / 100)
            if num(str(info.get('accumulatedTradingValue', '')).removesuffix('백만')) else None,
            'investors_100m': {'개인': num(deal.get('personalValue')), '외국인': num(deal.get('foreignValue')),
                               '기관': num(deal.get('institutionalValue'))},
            'program_100m': {'차익': num(program.get('indexDifferenceReal')), '비차익': num(program.get('indexBiDifferenceReal')),
                             '전체': num(program.get('indexTotalReal'))},
            'breadth': {'상승': rise, '하락': fall, '보합': num(breadth.get('steadyCount')),
                        '상한': num(breadth.get('upperCount')), '하한': num(breadth.get('lowerCount'))},
            'high52': num(info.get('highPriceOf52Weeks')), 'low52': num(info.get('lowPriceOf52Weeks')),
            'flow': today_flow(change, rise, fall), 'regime': regime(closes),
            'history': [[d, round(c, 2)] for d, c in days[-120:]]}


def themes():
    groups = []
    for page in (1, 2, 3):
        rows = naver(f'stocks/theme?page={page}&pageSize=100').get('groups') or []
        groups += [{'no': g.get('no'), 'name': g.get('name'), 'change_pct': num(g.get('changeRate')), 'count': int(g.get('totalCount') or 0),
                    'rise': int(g.get('riseCount') or 0), 'fall': int(g.get('fallCount') or 0)}
                   for g in rows if g.get('name') and num(g.get('changeRate')) is not None]
        if len(rows) < 100:
            break
    return groups


def kiwoom_themes():
    """themes() rows from Kiwoom's theme groups (ka90001, 인포스탁 names) when Naver fails; same fields."""
    import kiwoom_data as k

    rows = k.call('ka90001', {'qry_tp': '0', 'stk_cd': '', 'date_tp': '1', 'thema_nm': '', 'flu_pl_amt_tp': '3', 'stex_tp': '1'},
                  'thema_grp', max_pages=5)
    return [{'name': r.get('thema_nm'), 'change_pct': k.num(r.get('flu_rt')), 'count': int(k.num(r.get('stk_num')) or 0),
             'rise': int(k.num(r.get('rising_stk_num')) or 0), 'fall': int(k.num(r.get('fall_stk_num')) or 0)}
            for r in rows if r.get('thema_nm') and k.num(r.get('flu_rt')) is not None]


def index_fallback(code, now, program=None):
    """index_snapshot() from other sources when Naver fails (user-approved 2026-11-17): Kiwoom 업종현재가 (ka20001: level,
    change, trading value, advancers/decliners, 52-week range) and program trading (ka90005), Toss daily index bars for
    the regime and Toss investor trading value. Toss counts unregistered foreigners too, so its investor figures differ
    from Naver's; `fallback` names every source. Raises when the level or the history is missing. `program`: a shared
    program_intraday() reading (collect() reads it once for both markets and its own section)."""
    import kiwoom_data as k
    import toss_data

    d = k.call('ka20001', {'mrkt_tp': {'KOSPI': '0', 'KOSDAQ': '1'}[code], 'inds_cd': {'KOSPI': '001', 'KOSDAQ': '101'}[code]}, None)
    close, change = abs(k.num(d.get('cur_prc')) or 0), k.num(d.get('flu_rt'))
    candles = (toss_data.get(f'/api/v1/market-indicators/{code}/candles', {'interval': '1d', 'count': '200'}) or {}).get('candles') or []
    days = sorted((c['timestamp'][:10], num(c.get('closePrice'))) for c in candles if num(c.get('closePrice')))
    if not close or change is None or len(days) < 70:
        raise ValueError('fallback index data incomplete')
    today, session = now.strftime('%Y-%m-%d'), now.weekday() < 5 and now.strftime('%H:%M') >= '09:00'
    if days[-1][0] == today:
        days[-1] = (today, close)  # today's bar at the level just read
    elif session:  # shortcut: a weekday holiday after 09:00 also lands here (one repeated close in the regime window)
        days.append((today, close))
    else:
        days[-1] = (days[-1][0], close)  # before the open Kiwoom's level is the last session's close
    traded = now if days[-1][0] == today else datetime.fromisoformat(f'{days[-1][0]}T15:30:00').replace(tzinfo=KST)
    rise, fall = k.num(d.get('rising')), k.num(d.get('fall'))
    sources = ['kiwoom:ka20001', 'toss:market-indicators candles 1d']
    try:
        detail = toss_data.market_investors(code, now.strftime('%Y-%m-%d')) or {}
        investors = {who: detail.get(who) for who in ('개인', '외국인', '기관')} if detail else {}
        sources += ['toss:investor-trading'] if detail else []
    except Exception:
        investors = {}
    try:
        last = (((program or (lambda: program_intraday(now)))()).get(code) or {}).get('rows') or []
        program = dict(zip(('차익', '비차익', '전체'), last[-1][1:4])) if last else {}
        sources += ['kiwoom:ka90005'] if last else []
    except Exception:
        program = {}
    value = k.num(d.get('trde_prica'))
    return {'name': INDICES[code], 'close': close, 'change_pct': change, 'status': 'FALLBACK', 'traded_at': traded.isoformat(timespec='seconds'),
            'value_100m': round(value / 100) if value else None, 'investors_100m': investors, 'program_100m': program,
            'breadth': {'상승': rise, '하락': fall, '보합': k.num(d.get('stdns')), '상한': k.num(d.get('upl')), '하한': k.num(d.get('lst'))},
            'high52': abs(k.num(d.get('52wk_hgst_pric')) or 0) or None, 'low52': abs(k.num(d.get('52wk_lwst_pric')) or 0) or None,
            'flow': today_flow(change, rise, fall), 'regime': regime([c for _, c in days]),
            'history': [[day, round(c, 2)] for day, c in days[-120:]],
            'fallback': {'source': ', '.join(sources), 'note': '네이버 조회 실패로 키움·토스 값 사용(투자자는 토스 기준, 조회 시각 기준)'}}


def group_of(name):
    """The major group (THEME_GROUPS) a theme or sector name belongs to, or None; first match wins."""
    return next((g for g, words in THEME_GROUPS.items() if any(w in name for w in words)), None)


def theme_groups(rows):
    """Stock-count-weighted average change per major group (themes overlap, so this is an approximation)."""
    result = {}
    for row in rows:
        group = group_of(row['name'])
        if group and row['count']:
            entry = result.setdefault(group, {'weighted': 0.0, 'count': 0, 'rise': 0, 'fall': 0, 'themes': []})
            entry['weighted'] += row['change_pct'] * row['count']
            entry['count'] += row['count']
            entry['rise'] += row['rise']
            entry['fall'] += row['fall']
            entry['themes'].append(row)
    return sorted(({'group': g, 'change_pct': round(e['weighted'] / e['count'], 2), 'rise': e['rise'], 'fall': e['fall'],
                    'leader': max(e['themes'], key=lambda t: t['change_pct'])['name'], 'themes': len(e['themes'])}
                   for g, e in result.items()), key=lambda r: r['change_pct'], reverse=True)


def theme_leaders(top):
    """The THEME_LEADERS biggest risers (change %, trading value in 100 million KRW) of the first THEME_LEADER_THEMES
    themes in `top` (themes() rows, strongest first), one Naver call each. Returns (themes, failures); a failed theme is
    left out. A theme's `as_of` is its newest member trade (HH:MM)."""
    out, failed = [], []
    for theme in top[:THEME_LEADER_THEMES]:
        try:
            stocks = naver(f"stocks/theme/{theme['no']}?page=1&pageSize=100").get('stocks') or []
            rows = []
            for s in stocks:
                change, value = num(s.get('fluctuationsRatio')), num(s.get('accumulatedTradingValueRaw'))  # signed %, KRW
                if change is not None:
                    rows.append({'code': s.get('itemCode'), 'name': s.get('stockName'), 'change_pct': change,
                                 'value_100m': round(value / 1e8, 1) if value else None})
            times = [str(s['localTradedAt'])[11:16] for s in stocks if s.get('localTradedAt')]
        except Exception as exc:  # network or schema: this theme only
            failed.append(f"{theme.get('name')}: {type(exc).__name__}")
            continue
        if rows:  # Naver lists members by change already (11/18); sorted again so a reorder cannot pick laggards
            out.append({'theme': theme['name'], 'change_pct': theme['change_pct'], 'as_of': max(times, default=None),
                        'leaders': sorted(rows, key=lambda r: r['change_pct'], reverse=True)[:THEME_LEADERS]})
    return out, failed


PROGRAM_MARKETS = {'KOSPI': 'P00101', 'KOSDAQ': 'P10102'}  # ka90005 mrkt_tp for KRX (stex_tp 1), like Naver's figures


def program_intraday(now):
    """Today's KRX program trading by time (Kiwoom ka90005, about every 2 minutes), oldest first, in 100 million KRW.

    Rows: [HH:MM, 차익, 비차익, 전체 net buying, reference index, basis]. Kiwoom calls the index field `kospi200`; for
    KOSDAQ it carries another index (likely KOSDAQ150, unverified). Kiwoom returns only today's rows whatever the date."""
    import kiwoom_data as k

    out = {}
    for code, market in PROGRAM_MARKETS.items():
        raw = k.call('ka90005', {'date': now.strftime('%Y%m%d'), 'amt_qty_tp': '1', 'mrkt_tp': market,
                                 'min_tic_tp': '1', 'stex_tp': '1'}, 'prm_trde_trnsn')
        hundred_m = lambda v: None if k.num(v) is None else round(k.num(v) / 100, 1)  # 백만원 -> 억원
        rows = sorted(([f"{r['cntr_tm'][:2]}:{r['cntr_tm'][2:4]}", hundred_m(r.get('dfrt_trde_netprps')),
                        hundred_m(r.get('ndiffpro_trde_netprps')), hundred_m(r.get('all_netprps')),
                        hundred_m(r.get('kospi200')), k.num(r.get('basis'))]  # index arrives ×100 (78089 = 780.89)
                       for r in raw if len(str(r.get('cntr_tm') or '')) >= 4), key=lambda row: row[0])
        if rows:
            out[code] = {'as_of': rows[-1][0], 'rows': rows}
    return out


FOREIGN_DESK_TOP = 20


def foreign_desks():
    """Today's top net buys and net sells through foreign brokers' desks on KRX (Kiwoom ka10037), in 100 million KRW.

    A desk-based intraday proxy for foreign flows, not the exchange's registered-foreigner figures."""
    import kiwoom_data as k

    out = {}
    for side, trde_tp in (('net_buy', '1'), ('net_sell', '2')):
        rows = k.call('ka10037', {'mrkt_tp': '000', 'dt': '0', 'trde_tp': trde_tp, 'sort_tp': '1', 'stex_tp': '1'},
                      'frgn_wicket_trde_upper', max_pages=1)
        out[side] = [{'code': r.get('stk_cd'), 'name': r.get('stk_nm'), 'change_pct': k.num(r.get('flu_rt')),
                      'net_100m': None if k.num(r.get('netprps_prica')) is None else round(k.num(r['netprps_prica']) / 100, 1)}
                     for r in rows[:FOREIGN_DESK_TOP]]  # 백만원 -> 억원
    return out


def index_intraday(now):
    """Today's KOSPI/KOSDAQ path from Toss 1-minute index bars: open/high/low/last, change vs the previous close and the
    open, the last 30 minutes, the place in today's range, and the last 30 closes. Markets with no bar today are left out.
    `as_of` is the newest bar's end minute; that bar may still be forming."""
    import toss_data

    pct = lambda a, b: round((a / b - 1) * 100, 2) if a and b else None  # noqa: E731
    out = {}
    for code in INDICES:
        got = toss_data.index_minutes(code, now)
        bars = got['bars']
        if not bars:
            continue
        last, high, low = bars[-1][4], max(b[2] for b in bars), min(b[3] for b in bars)
        ago = next((b[4] for b in reversed(bars) if b[0] <= bars[-1][0] - timedelta(minutes=30)), None)
        out[code] = {'as_of': bars[-1][0].strftime('%H:%M'), 'open': bars[0][1], 'high': high, 'low': low, 'last': last,
                     'prev_close': got['prev_close'], 'change_pct': pct(last, got['prev_close']), 'from_open_pct': pct(last, bars[0][1]),
                     'change_30m_pct': pct(last, ago), 'range_position_pct': round((last - low) / (high - low) * 100) if high > low else None,
                     'bars': len(bars), 'last_30': [[b[0].strftime('%H:%M'), b[4]] for b in bars[-30:]]}
    return out


def global_prices():
    import yfinance as yf
    with api_calls.track('yahoo', 'yfinance/download (해외 지수·원자재 일봉)'):
        frame = yf.download(list(GLOBAL.values()), period='10d', interval='1d', progress=False, auto_adjust=False)['Close']
    result = {}
    for name, ticker in GLOBAL.items():
        series = frame[ticker].dropna() if ticker in frame else []
        if len(series) >= 2:
            last, previous = float(series.iloc[-1]), float(series.iloc[-2])
            result[name] = {'ticker': ticker, 'price': round(last, 2), 'change_pct': round((last / previous - 1) * 100, 2),
                            'date': series.index[-1].strftime('%Y-%m-%d')}
    if len(result) < len(GLOBAL):  # yfinance drops failed tickers without raising
        api_calls.problem('yahoo', 'yfinance/download (해외 지수·원자재 일봉)', f'{len(GLOBAL) - len(result)}개 종목 없음')
    return result


def global_intraday():
    """US index futures, gold, oil, the dollar index and USD/KRW now: one Yahoo spark request (5-minute closes, 2 days).

    Per symbol: the last price and its time (`as_of`, ISO KST: overnight and weekend bars carry another date; CME futures
    are about 10 minutes delayed), `change_pct` against Yahoo's previousClose (the previous session's close: the futures
    settlement, the FX day close) and `change_60m_pct` against the last bar at least 60 minutes earlier. A symbol without
    bars or a previous close is left out (noted for the dashboard), never filled with zeros."""
    import requests
    response = requests.get(SPARK, params={'symbols': ','.join(GLOBAL_INTRADAY.values()), 'range': '2d', 'interval': '5m'},
                            headers=HEADERS, timeout=10)
    response.raise_for_status()
    data = response.json()
    pct = lambda a, b: round((a / b - 1) * 100, 2) if a and b else None  # noqa: E731
    out = {}
    for name, ticker in GLOBAL_INTRADAY.items():
        item = data.get(ticker) or {}
        bars = [(datetime.fromtimestamp(t, KST), c) for t, c in zip(item.get('timestamp') or [], item.get('close') or []) if c]
        previous = item.get('previousClose')
        if not bars or not previous:
            continue
        at, last = bars[-1]
        ago = next((c for t, c in reversed(bars) if t <= at - timedelta(minutes=60)), None)
        out[name] = {'ticker': ticker, 'price': last, 'previous_close': previous, 'as_of': at.isoformat(timespec='minutes'),
                     'change_pct': pct(last, previous), 'change_60m_pct': pct(last, ago)}
    if len(out) < len(GLOBAL_INTRADAY):
        api_calls.problem('yahoo', api_calls.endpoint_of(SPARK), f'{len(GLOBAL_INTRADAY) - len(out)}개 종목 없음')
    return out


def collect(now=None):
    """Every section is independent: a failed source is listed in errors, never filled with zeros."""
    now = now or datetime.now(KST)
    payload, errors = {'time': now.isoformat(timespec='seconds'), 'indices': {}}, []
    import kiwoom_data
    import toss_data
    previous = load() or {}
    program = functools.cache(lambda: program_intraday(now))  # one ka90005 reading for the fallback and its own section
    for code in INDICES:
        try:
            payload['indices'][code] = index_snapshot(code)
        except Exception as exc:  # network, schema or too little history
            errors.append(f'{INDICES[code]}: {type(exc).__name__}')
            if kiwoom_data.configured() and toss_data.configured():
                try:  # a fresh reading from Kiwoom and Toss beats reusing an older Naver one
                    payload['indices'][code] = index_fallback(code, now, program)
                    continue
                except Exception as fallback_exc:
                    errors.append(f'{INDICES[code]} 대체(키움·토스): {type(fallback_exc).__name__}')
            kept = (previous.get('indices') or {}).get(code)
            origin = (kept or {}).get('reused_from') or previous.get('time')  # the time it was really fetched
            try:
                fresh = timedelta(0) <= now - datetime.fromisoformat(origin) <= MAX_AGE
            except (TypeError, ValueError):
                fresh = False
            if kept and fresh:  # one failed fetch must not switch the engines' market view off
                payload['indices'][code] = {**kept, 'reused_from': origin}
    try:
        try:
            rows, theme_source = themes(), 'naver'
        except Exception as exc:
            if not kiwoom_data.configured():
                raise
            errors.append(f'테마(네이버): {type(exc).__name__}')
            rows, theme_source = kiwoom_themes(), 'kiwoom:ka90001'
        payload['themes'] = {'source': theme_source, 'groups': theme_groups(rows), 'top': sorted(rows, key=lambda r: r['change_pct'], reverse=True)[:10],
                             'bottom': sorted(rows, key=lambda r: r['change_pct'])[:5], 'total': len(rows)}
    except Exception as exc:
        errors.append(f'테마: {type(exc).__name__}')
    if (payload.get('themes') or {}).get('source') == 'naver':  # Kiwoom's theme rows carry no Naver theme number
        leaders, failed = theme_leaders(payload['themes']['top'])
        payload['theme_leaders'] = {'source': 'naver:stocks/theme/{no}', 'unit': '억원', 'themes': leaders}
        errors += [f'테마 주도주(네이버) {f}' for f in failed]
    try:
        payload['global'] = global_prices()
    except Exception as exc:
        errors.append(f'해외 가격: {type(exc).__name__}')
    try:
        quotes = global_intraday()
        payload['global_intraday'] = {'source': 'yahoo:v8/finance/spark 5m', 'markets': quotes}
        errors += [f'해외 선물·환율 5분봉: {name} 없음' for name in GLOBAL_INTRADAY if name not in quotes]
    except Exception as exc:
        errors.append(f'해외 선물·환율 5분봉: {type(exc).__name__}')
    if kiwoom_data.configured():  # no Kiwoom key: the section is simply absent, not an error
        try:
            payload['program_intraday'] = {'source': 'kiwoom:ka90005', 'unit': '억원',
                                           'columns': ['시각', '차익', '비차익', '전체', '기준지수', '베이시스'],
                                           'markets': program()}
        except Exception as exc:
            errors.append(f'프로그램 시계열(키움): {type(exc).__name__}')
        try:
            payload['foreign_desks'] = {'source': 'kiwoom:ka10037', 'unit': '억원', 'note': '외국계 증권사 창구 기준(등록외국인 집계 아님)',
                                        **foreign_desks()}
        except Exception as exc:
            errors.append(f'외국계창구(키움): {type(exc).__name__}')
    if toss_data.configured():  # no Toss key: the section is simply absent, not an error
        try:
            payload['index_intraday'] = {'source': 'toss:market-indicators', 'markets': index_intraday(now)}
        except Exception as exc:
            errors.append(f'지수 1분봉(토스): {type(exc).__name__}')
        try:  # 외국인·기관 장중 잠정, 개인은 정규장 뒤 (ranked_at 확인)
            payload['investor_rankings'] = {'source': 'toss:rankings/investor-trading', 'unit': '억원',
                                            'investors': toss_data.investor_rankings(20, now)}
        except Exception as exc:
            errors.append(f'투자자별 순매수 랭킹(토스): {type(exc).__name__}')
    payload['errors'] = errors
    payload['sources'] = ('네이버 금융(지수·투자자·프로그램·등락 종목수·테마·테마 상위 종목), Yahoo Finance(해외 지수·원자재·코인·달러·환율 일봉 종가, '
                          '미국 지수 선물·금·WTI·달러인덱스·원/달러 5분봉), '
                          '키움 REST(장중 프로그램매매 시간대별·외국계창구 순매수 상위), 토스증권 Open API(코스피·코스닥 1분봉·투자자별 순매수 랭킹)')
    return payload


def write(payload, path=FILE):
    from config_store import write_json

    write_json(path, payload, indent=None)


def research_copy(payload):
    """The research-store snapshot: without each index's 120-day close history, the same rows every 5 minutes (about a third
    of the bytes). market-context.json keeps it."""
    indices = payload.get('indices')
    if not isinstance(indices, dict):
        return payload
    return {**payload, 'indices': {code: {k: v for k, v in item.items() if k != 'history'} if isinstance(item, dict) else item
                                   for code, item in indices.items()}}


def load(path=FILE):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def summary(payload, now=None):
    """Compact view for Jev and vibe-trading; None when missing or older than MAX_AGE."""
    now = now or datetime.now(KST)
    try:
        at = datetime.fromisoformat(payload['time'])
        if not timedelta(0) <= now - at <= MAX_AGE:
            return None
    except (TypeError, KeyError, ValueError):
        return None
    view = {'as_of': payload['time'][:16], 'note': 'rule-based regime (20/60-day averages), not a forecast; '
            'flows in 100 million KRW (+ net buy); global = latest daily close vs previous close (US closes are the previous session); '
            'global_intraday = Yahoo 5-minute futures/FX at as_of KST (CME about 10 min delayed): change vs the previous session '
            'close (futures settlement) and over the last 60 minutes'}
    pre_open = at.astimezone(KST).strftime('%H:%M') < '09:00'  # the tone then is the previous session's, not today's
    for code, item in (payload.get('indices') or {}).items():
        view[item['name']] = {'regime': item['regime']['label'], 'regime_reasons': item['regime']['reasons'],
                              'drawdown_from_120d_high_pct': item['regime']['drawdown_pct'],
                              'today_change_pct': None if pre_open else item['change_pct'],
                              'today_tone': '장전(전일 흐름)' if pre_open else item['flow'],
                              'investor_net_100m_krw': item['investors_100m'], 'program_net_100m_krw': item['program_100m'],
                              'advancers_decliners': [item['breadth']['상승'], item['breadth']['하락']],
                              **({'stale_reused_from': item['reused_from']} if item.get('reused_from') else {}),
                              **({'fallback_source': item['fallback']['source']} if item.get('fallback') else {})}
    groups = (payload.get('themes') or {}).get('groups') or []
    if groups:
        view['theme_groups_today_pct'] = {g['group']: g['change_pct'] for g in groups}
    if payload.get('global'):
        view['global_last_daily_change_pct'] = {k: v['change_pct'] for k, v in payload['global'].items()}
    quotes = {}  # live overnight too, so not held back before the open; each quote's own time decides
    for name, quote in ((payload.get('global_intraday') or {}).get('markets') or {}).items():
        try:
            lag = (at - datetime.fromisoformat(quote['as_of'])) / timedelta(minutes=1)
        except (KeyError, TypeError, ValueError):
            continue
        if -5 <= lag <= GLOBAL_INTRADAY_MAX_LAG:
            quotes[name] = {'as_of': quote['as_of'][11:16], 'change_pct': quote.get('change_pct'), 'change_60m_pct': quote.get('change_60m_pct')}
    if quotes:
        view['global_intraday'] = quotes
    if not pre_open:
        view.update(intraday_view(payload))
        clock = _minutes(now.astimezone(KST).strftime('%H:%M'))
        for item in (view.get('index_intraday') or {}).values():  # the rest of the index path stays
            if clock - _minutes(item['as_of']) > CHANGE_30M_MAX_AGE:
                item['change_30m_pct'] = None
    return view


INTRADAY_MAX_LAG = 20  # minutes
# The inverse gate's "not rebounding over the last 30 minutes" needs a current reading: the payload may be MAX_AGE old and
# its Toss section INTRADAY_MAX_LAG more, so a 40-minute-old change passed (r3 E7).
CHANGE_30M_MAX_AGE = 10  # minutes


def _minutes(hhmm):
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])


def intraday_view(payload):
    """The intraday sections collected every few minutes that the engines did not see before (user 2026-11-18): each
    index's path today (Toss 1-minute bars: from the open, the high/low, 30-minute change, place in today's range), the
    program trading of the last 30 minutes (Kiwoom, change of the cumulative net), foreign-broker desks, the top
    investor net buys (Toss) and the leading stocks of today's strongest themes (Naver). Each part carries its own time;
    a missing part, or one more than INTRADAY_MAX_LAG minutes
    behind the payload (Toss's institution ranking stayed at 11:05 on 11/18 13:08), is left out."""
    out, names = {}, {code: item.get('name', code) for code, item in (payload.get('indices') or {}).items()}
    try:
        now = _minutes(datetime.fromisoformat(payload['time']).astimezone(KST).strftime('%H:%M'))
    except (KeyError, TypeError, ValueError):
        return {}
    fresh = lambda hhmm: isinstance(hhmm, str) and len(hhmm) >= 5 and -5 <= now - _minutes(hhmm) <= INTRADAY_MAX_LAG  # noqa: E731 (a section read after the payload stamp)
    index = {}
    for code, item in ((payload.get('index_intraday') or {}).get('markets') or {}).items():
        if isinstance(item, dict) and fresh(item.get('as_of')):
            index[names.get(code, code)] = {k: item.get(k) for k in ('as_of', 'change_pct', 'from_open_pct', 'change_30m_pct',
                                                                       'range_position_pct', 'open', 'high', 'low', 'last')}
    if index:
        out['index_intraday'] = index
    program = {}
    for code, item in ((payload.get('program_intraday') or {}).get('markets') or {}).items():
        rows = [r for r in (item or {}).get('rows') or [] if isinstance(r, list) and len(r) > 3 and isinstance(r[0], str)
                and type(r[3]) in (int, float)]
        if not rows or not fresh(rows[-1][0]):
            continue
        last = rows[-1]
        before = [r for r in rows if _minutes(r[0]) <= _minutes(last[0]) - 30]
        program[names.get(code, code)] = {'as_of': last[0], 'cumulative_net_100m': round(last[3], 1),
                                          **({'last_30m_net_100m': round(last[3] - before[-1][3], 1)} if before else {})}
    if program:
        out['program_intraday'] = program
    desks = [d for d in (payload.get('foreign_desks') or {}).get('net_buy') or [] if isinstance(d, dict)][:5]
    if desks:
        out['foreign_desks_net_buy_top'] = [{k: d.get(k) for k in ('name', 'change_pct', 'net_100m')} for d in desks]
    ranks = {}
    for who in ('외국인', '기관'):
        item = ((payload.get('investor_rankings') or {}).get('investors') or {}).get(who) or {}
        top = [r for r in item.get('net_buy') or [] if isinstance(r, dict)][:5]
        if top and fresh(str(item.get('ranked_at', ''))[11:16]):
            ranks[who] = {'ranked_at': str(item.get('ranked_at', ''))[11:16], 'net_buy_top': [{k: r.get(k) for k in ('name', 'net_100m')} for r in top]}
    if ranks:
        out['investor_net_buy_rankings'] = ranks
    leaders = [{'theme': t.get('theme'), 'change_pct': t.get('change_pct'), 'as_of': t['as_of'],
                'leaders': [{k: s.get(k) for k in ('name', 'change_pct', 'value_100m')} for s in t.get('leaders') or [] if isinstance(s, dict)]}
               for t in (payload.get('theme_leaders') or {}).get('themes') or [] if isinstance(t, dict) and fresh(t.get('as_of'))]
    if leaders:
        out['theme_leaders'] = leaders
    return out


def main():
    payload = collect()
    write(payload)
    import research_store
    research_store.record('market_context', research_copy(payload))
    print(json.dumps({k: v for k, v in summary(payload).items()}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
