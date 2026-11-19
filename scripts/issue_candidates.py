"""Shadow candidates: today's issue / theme / hot stocks, recorded with how they went afterwards. Never traded.

User 2026-11-18 ("1단계 진행"): the runner's universe (KOSPI 200 + KOSDAQ 150 constituents, market_movers) never sees
stocks such as 한진칼 in a management dispute, a theme leader or a full-market turnover leader. This records them,
never orders, so that after 5-10 trading days the data can decide whether the runner may buy such stocks. Read-only
rankings, disclosures and quotes only.

Reasons a code is a candidate (merged over the day):
- rank:trading_value / rank:rise: Kiwoom's full-KRX rankings with ETF/ETN excluded, the TOP_VALUE by trading value
  (ka10030 sorted by value) and the TOP_RISE risers that traded 100억 or more (ka10027);
- dart:<keyword>: an Open DART filing whose title holds one of DART_KEYWORDS, read market-wide every DART_EVERY
  (the first read of the day takes the newest DART_PAGES x 100 filings, about the day so far; later reads the new ones);
- theme:<name>: market-context.json `theme_leaders` (market_context.theme_leaders, top themes' leading stocks), only while
  market_context.summary would show them to the engines (file within MAX_AGE, theme within INTRADAY_MAX_LAG of it).
Each round quotes at most MAX_QUOTES candidates not seen yet on NH (ranked codes, then theme, then DART-only codes; outside
the universe first within each) and records the ones
that pass check(): 100억 traded, no 관리/정리매매/거래정지, no 투자경고·위험, no 단기과열 지정, at least 2% under the
upper limit, no ETF/ETN/SPAC/preferred share. A recorded code keeps its first sighting (time, price, would_buy);
later sightings only add reasons. would_buy applies the entry checks that need no model: the strength path's change
band and the careful re-entry chase limits (strategy settings). The session VWAP is NH's 가중평균가 and the session range
is the quote's high and low: the same measures careful_tape takes from 1-minute bars, without another call.
After the close update_outcomes() settles r30 / r_close / max / min from Yahoo 5-minute bars (blocked_outcomes).
Stored in market-research.sqlite3, table shadow_candidates (key day:code).

  PYTHONPATH=agent:scripts .venv/bin/python scripts/issue_candidates.py --once [--db PATH]
  PYTHONPATH=agent:scripts .venv/bin/python scripts/issue_candidates.py --outcomes [--day YYYY-MM-DD] | --summary [--days 10]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from etf_universe import CODE_PATTERN

KST = ZoneInfo('Asia/Seoul')
ROOT = Path.home() / '.vibe-trading/nhplug/shared-etf-paper'
TOP_VALUE, TOP_RISE = 40, 30  # full-market rank cuts (한진칼 was 32nd by trading value on 2026-11-18 at 14:10)
MAX_QUOTES = 15  # NH quotes per 5-minute round
RECHECK = timedelta(minutes=30)  # a dropped code is quoted again after this
MIN_VALUE_MIL = 10_000  # 100억 원 traded (NH acml_tr_pbmn is in 백만 원)
UPPER_GAP_PCT = 2.0
DART_EVERY = timedelta(minutes=15)
DART_PAGES = 5  # newest-first pages of 100 per read, stopping at a page with a filing already seen
# shortcut: a busy hour can hold more than DART_PAGES x 100 filings since the previous read; filter by type if it does
DART_KEYWORDS = ('최대주주변경', '경영권', '공개매수', '대량보유상황보고서(일반)', '주주총회소집', '자기주식취득')
# 대량보유 (일반) is the form for 경영참가 목적; 경영권 covers 소송등의제기ㆍ신청(경영권분쟁소송); 주주총회소집 also
# matches March's regular meetings.
_RANK = {'mrkt_tp': '000', 'stex_tp': '1'}
VALUE_RANK = ('ka10030', {**_RANK, 'sort_tp': '3', 'mang_stk_incls': '16', 'crd_tp': '0', 'trde_qty_tp': '0',
                          'pric_tp': '0', 'trde_prica_tp': '0', 'mrkt_open_tp': '0'}, 'tdy_trde_qty_upper')  # 3 value, 16 no ETF/ETN
RISE_RANK = ('ka10027', {**_RANK, 'sort_tp': '1', 'trde_qty_cnd': '0000', 'stk_cnd': '16', 'crd_cnd': '0', 'updown_incls': '1',
                         'pric_cnd': '0', 'trde_prica_cnd': '1000'}, 'pred_pre_flu_rt_upper')  # 1000: 100억 or more (checked 2026-11-18)
NOT_TRADED = ('관리', '정리매매', '거래정지')  # in the quote's market1..6 texts
TIER = {'rank': 0, 'theme': 1, 'dart': 2}  # quote order: DART alone yields dozens of codes a day (62 on 2026-11-18)
SCHEMA = ('CREATE TABLE IF NOT EXISTS shadow_candidates (key TEXT PRIMARY KEY, day TEXT, code TEXT, name TEXT, market TEXT, '
          'first_seen TEXT, reasons TEXT, in_universe INTEGER, price REAL, change_pct REAL, value_mil INTEGER, warnings TEXT, '
          'would_buy TEXT, base_price REAL, r30 REAL, r_close REAL, max_pct REAL, min_pct REAL)')
_STATE = {}  # the movers process's day: DART cadence and filings seen, when each dropped code was quoted


def _db(path):
    db = sqlite3.connect(path, timeout=5)
    db.execute(SCHEMA)
    return db


def _add(found, raw, reason, name=None):
    code = str(raw or '').split('_')[0]  # Kiwoom appends _AL / _NX
    if re.fullmatch(CODE_PATTERN, code):
        entry = found.setdefault(code, {'name': None, 'reasons': set()})
        entry['reasons'].add(reason)
        entry['name'] = entry['name'] or (str(name).strip() if name else None)


def rankings(found):
    import kiwoom_data
    if not kiwoom_data.configured():
        return
    for (api_id, body, key), reason, top in ((VALUE_RANK, 'rank:trading_value', TOP_VALUE), (RISE_RANK, 'rank:rise', TOP_RISE)):
        for row in kiwoom_data.call(api_id, body, key, max_pages=1)[:top]:
            _add(found, row.get('stk_cd'), reason, row.get('stk_nm'))


def dart_hits(now, state):
    """{code: [name, {keyword}]} of keyword filings seen today; Open DART is read at most every DART_EVERY."""
    from dotenv import dotenv_values
    import local_env

    dart = state.setdefault('dart', {'at': None, 'seen': set(), 'hits': {}})
    if dart['at'] and now - dart['at'] < DART_EVERY:
        return dart['hits']
    dart['at'] = now  # a failed read also waits for the next interval
    key = dotenv_values(local_env.ENV, interpolate=False).get('OPENDART_API_KEY')
    if not key:
        return dart['hits']
    for page in range(1, DART_PAGES + 1):
        response = requests.get('https://opendart.fss.or.kr/api/list.json', timeout=15, allow_redirects=False, params={
            'crtfc_key': key, 'bgn_de': (now - timedelta(days=3)).strftime('%Y%m%d'), 'end_de': now.strftime('%Y%m%d'),
            'page_no': page, 'page_count': 100})
        if response.status_code != 200:
            raise ValueError(f'Open DART HTTP {response.status_code}')  # never the URL: it holds the key
        data = response.json()
        if data.get('status') == '013':  # no filings
            break
        if data.get('status') != '000':
            raise ValueError(f"Open DART status {data.get('status')}")
        known = False
        for row in data.get('list') or []:
            number = str(row.get('rcept_no') or '')
            known |= number in dart['seen']
            dart['seen'].add(number)
            title, code = re.sub(r'\s', '', str(row.get('report_nm') or '')), str(row.get('stock_code') or '').strip()
            if row.get('corp_cls') not in ('Y', 'K') or not code:  # KOSPI / KOSDAQ listed only
                continue
            for word in DART_KEYWORDS:
                if word in title:
                    dart['hits'].setdefault(code, [row.get('corp_name'), set()])[1].add(word)
        if known or page >= int(data.get('total_page') or 1):
            break
    return dart['hits']


def theme_leaders(context):
    """[(code, name, theme)] from market-context.json `theme_leaders` (market_context: {'themes': [{'theme', 'leaders':
    [{'code', 'name', ...}]}]}); [] when absent."""
    out = []
    for item in ((context.get('theme_leaders') or {}).get('themes') or []) if isinstance(context.get('theme_leaders'), dict) else []:
        for row in (item.get('leaders') or []) if isinstance(item, dict) and item.get('theme') else []:
            if isinstance(row, dict) and row.get('code'):
                out.append((row['code'], row.get('name'), str(item['theme'])))
    return out


def check(quote, code):
    """(price, change %, warnings) of a quote that passes the filters, or the reason it is dropped."""
    price, previous, value, volume = (quote.get(k) for k in ('stck_prpr', 'stck_prdy_clpr', 'acml_tr_pbmn', 'acml_vol'))
    if quote.get('iem_cd') != code or not all(type(v) in (int, float) and v > 0 for v in (price, previous, value, volume)):
        return 'quote_failed'
    upper = quote.get('stck_mxpr')
    upper_gap = round((upper / price - 1) * 100, 2) if type(upper) in (int, float) and upper > 0 else None
    texts = ' '.join(str(quote.get(f'market{i}') or '') for i in range(1, 7))
    over, alarm = str(quote.get('short_over_code') or '0'), str(quote.get('mrkt_alrm_code') or '0')
    for reason, failed in (('not_common', quote.get('scrt_grp_isnm') in ('ETF', 'ETN')), ('value', value < MIN_VALUE_MIL),
                           ('managed', any(w in texts for w in NOT_TRADED)),
                           ('alarm', alarm in ('2', '4', '5')),  # 투자경고, 경고>위험예고, 투자위험 (1 주의 and 3 주의>위험예고 stay)
                           ('overheat', over in ('2', '3')),  # 단기과열 지정, 연장 (1 예고 stays)
                           ('upper_limit', upper_gap is not None and upper_gap < UPPER_GAP_PCT)):
        if failed:
            return reason
    return price, round((price / previous - 1) * 100, 2), {'overheat_code': over, 'alarm_code': alarm, 'upper_gap_pct': upper_gap}


def would_buy(quote, price, change, settings):
    """The runner's data-only entry checks at first sight: the strength path's change band, then not chasing."""
    vwap, high, low = (quote.get(k) for k in ('wghn_avrg_prc', 'stck_hgpr', 'stck_lwpr'))
    vs_vwap = round((price / vwap - 1) * 100, 2) if type(vwap) in (int, float) and vwap > 0 else None
    place = (round((price - low) / (high - low) * 100) if high > low else 50) if all(type(v) in (int, float) for v in (high, low)) else None
    blocked = ('change_band' if not settings['strength_change_min'] <= change <= settings['strength_change_max']
               else 'chase_unmeasured' if vs_vwap is None or place is None
               else 'above_vwap' if vs_vwap > settings['careful_reentry_vwap_pct']
               else 'near_range_top' if place >= settings['careful_reentry_range_pct'] else None)
    return {'ok': blocked is None, 'blocked': blocked, 'vs_vwap_pct': vs_vwap, 'range_position_pct': place}


def observe(read, now, universe, root=ROOT, db=None, state=None):
    """One round: gather today's candidates, quote the unseen ones and record those that pass. Returns counts."""
    from strategy_settings import read_settings

    state = _STATE if state is None else state
    day, root = now.strftime('%Y-%m-%d'), Path(root)
    if state.get('day') != day:
        state.clear()
        state.update(day=day, checked={})
    found, errors, dropped, recorded = {}, {}, {}, []
    try:
        rankings(found)
    except Exception as exc:  # Kiwoom down or refused: the other sources still count
        errors['rankings'] = type(exc).__name__
    try:
        for code, (name, words) in dart_hits(now, state).items():
            for word in words:
                _add(found, code, f'dart:{word}', name)
    except Exception as exc:
        errors['dart'] = type(exc).__name__
    try:
        import market_context
        context = json.loads((root / 'market-context.json').read_text())
        # the engines' own view: no file older than MAX_AGE, no theme INTRADAY_MAX_LAG behind it (yesterday's leaders)
        current = {str(t['theme']) for t in (market_context.summary(context, now) or {}).get('theme_leaders') or []}
        for code, name, theme in theme_leaders(context):
            if theme in current:
                _add(found, code, f'theme:{theme}', name)
    except Exception as exc:
        errors['themes'] = type(exc).__name__
    settings = read_settings(root)[0]
    path = Path(db or root / 'market-research.sqlite3')
    with _db(path) as conn:
        seen = dict(conn.execute('SELECT code, reasons FROM shadow_candidates WHERE day = ?', (day,)))
        pending = []
        for code, entry in found.items():
            if code in seen:
                merged = sorted(set(json.loads(seen[code])) | entry['reasons'])
                if merged != json.loads(seen[code]):
                    conn.execute('UPDATE shadow_candidates SET reasons = ? WHERE key = ?', (json.dumps(merged, ensure_ascii=False), f'{day}:{code}'))
            elif code[-1] != '0' or '스팩' in (entry['name'] or ''):  # preferred shares end in 5/7/9/K..., SPACs by name
                dropped[code] = 'not_common'
            elif not (at := state['checked'].get(code)) or now - at >= RECHECK:
                pending.append(code)
        pending.sort(key=lambda c: (min(TIER[r.split(':')[0]] for r in found[c]['reasons']), c in universe, -len(found[c]['reasons'])))
        for code in pending[:MAX_QUOTES]:
            try:
                response = read.call_tool('get_stock_price', {'stock_code': code})
                quote = (response.get('data') or {}).get('Output_0') or {} if response.get('status') == 'ok' else {}
            except Exception:
                quote = {}
            passed = check(quote, code)
            if isinstance(passed, str):
                dropped[code] = passed
                if passed != 'quote_failed':  # a failed read is retried next round (NH fails now and then under load)
                    state['checked'][code] = now
                continue
            price, change, warnings = passed
            conn.execute('INSERT OR IGNORE INTO shadow_candidates (key, day, code, name, market, first_seen, reasons, in_universe, '
                         'price, change_pct, value_mil, warnings, would_buy) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (f'{day}:{code}', day, code, str(quote.get('iem_nm') or found[code]['name'] or code).lstrip('*#'),
                          quote.get('mrkt_div_isnm'), now.isoformat(timespec='seconds'),
                          json.dumps(sorted(found[code]['reasons']), ensure_ascii=False), int(code in universe), price, change,
                          int(quote['acml_tr_pbmn']), json.dumps(warnings), json.dumps(would_buy(quote, price, change, settings))))
            recorded.append(code)
    path.chmod(0o600)
    return {'found': len(found), 'quoted': min(len(pending), MAX_QUOTES), 'recorded': recorded, 'dropped': dropped, 'errors': errors}


def update_outcomes(day=None, root=ROOT, db=None, fetch=None):
    """Recompute the day's outcomes after each first sighting from Yahoo 5-minute bars; returns counts."""
    from blocked_outcomes import settle, yahoo_bars

    day = day or datetime.now(KST).strftime('%Y-%m-%d')
    path = Path(db or Path(root) / 'market-research.sqlite3')
    settled, cache = 0, {}
    with _db(path) as conn:
        rows = conn.execute('SELECT key, code, first_seen FROM shadow_candidates WHERE day = ?', (day,)).fetchall()
        for key, code, first_seen in rows:
            if code not in cache:
                try:
                    cache[code] = yahoo_bars(code, day, fetch)
                except Exception:
                    cache[code] = []
            out = settle(first_seen, cache[code])
            conn.execute('UPDATE shadow_candidates SET base_price = ?, r30 = ?, r_close = ?, max_pct = ?, min_pct = ? WHERE key = ?',
                         (out['price'], out['r30'], out['r_last'], out['mfe'], out['mae'], key))
            settled += out['r_last'] is not None
    path.chmod(0o600)
    return {'day': day, 'rows': len(rows), 'settled': settled}


def summary(days=10, root=ROOT, db=None):
    """{reason type: {n, avg_r30, avg_r_close, up_share, would_buy: same}} over the settled rows of the last `days`
    recorded days. Types are the ranking and DART reasons as recorded and 'theme' for every theme; a type ends in
    ' (universe)' for codes inside the universe. Research only."""
    with _db(Path(db or Path(root) / 'market-research.sqlite3')) as conn:
        picked = [d for (d,) in conn.execute('SELECT DISTINCT day FROM shadow_candidates ORDER BY day DESC LIMIT ?', (days,))]
        rows = conn.execute('SELECT reasons, in_universe, would_buy, r30, r_close FROM shadow_candidates '
                            'WHERE r_close IS NOT NULL AND day >= ?', (picked[-1],)).fetchall() if picked else []
    groups = {}
    for reasons, inside, buy, r30, close in rows:
        for kind in {'theme' if r.startswith('theme:') else r for r in json.loads(reasons)}:
            group = groups.setdefault(kind + (' (universe)' if inside else ''), ([], []))
            group[0].append((r30, close))
            if json.loads(buy).get('ok'):
                group[1].append((r30, close))

    def stats(items):
        r30s, closes = [a for a, _ in items if a is not None], [b for _, b in items]
        mean = lambda xs: round(sum(xs) / len(xs), 3) if xs else None  # noqa: E731
        return {'n': len(items), 'avg_r30': mean(r30s), 'avg_r_close': mean(closes),
                'up_share': round(sum(c > 0 for c in closes) / len(closes), 3) if closes else None}
    return {kind: {**stats(every), 'would_buy': stats(buys)} for kind, (every, buys) in sorted(groups.items())}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--once', action='store_true', help='one observation round now (read-only quotes and rankings)')
    parser.add_argument('--outcomes', action='store_true', help='settle --day from Yahoo 5-minute bars')
    parser.add_argument('--summary', action='store_true', help='outcomes per reason type over --days')
    parser.add_argument('--day', help='YYYY-MM-DD (Asia/Seoul); default today')
    parser.add_argument('--days', type=int, default=10)
    parser.add_argument('--db', help='SQLite path; default market-research.sqlite3')
    args = parser.parse_args()
    if args.once:
        import market_movers
        from src.config.loader import load_agent_config
        from src.tools.mcp import MCPServerAdapter
        read = MCPServerAdapter('nhplug_readonly', load_agent_config().mcp_servers['nhplug_readonly'])
        now = datetime.now(KST)
        print(json.dumps(observe(read, now, market_movers.constituents(read), db=args.db), ensure_ascii=False))
        with _db(Path(args.db or ROOT / 'market-research.sqlite3')) as conn:
            conn.row_factory = sqlite3.Row
            for row in conn.execute('SELECT * FROM shadow_candidates WHERE day = ? ORDER BY first_seen', (now.strftime('%Y-%m-%d'),)):
                print(json.dumps({k: row[k] for k in row.keys() if row[k] is not None}, ensure_ascii=False))
    if args.outcomes:
        print(json.dumps(update_outcomes(args.day, db=args.db)))
    if args.summary:
        print(json.dumps(summary(args.days, db=args.db), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
