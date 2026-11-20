"""Intraday market movers from NH read-only quotes (KOSPI 200 + KOSDAQ 150 constituents).

NH has no ranking API. With a Kiwoom key, Kiwoom's KRX rankings (trading value, volume, rise, fall) preselect which
constituents (KODEX 200 / KODEX KOSDAQ150 ETF component lists) to quote (user-approved 2026-11-17); without it, or when
Kiwoom fails, every constituent is quoted as before. Either way the figures are NH's own quotes, ranked by trading value,
volume, rise and fall. "Popular" is approximated by trading value.
Writes movers.json for the shared ETF paper runner and the dashboard. Never orders.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import research_store
from src.config.paths import get_runtime_root  # VIBE_TRADING_HOME, else ~/.vibe-trading (one rule for every tool)
from etf_universe import CODE_PATTERN
import api_calls  # noqa: F401,E402 - counts this process's external API calls (dashboard 'API 연동')

KST = ZoneInfo('Asia/Seoul')
ROOT = get_runtime_root() / 'nhplug' / 'shared-etf-paper'
INDEX_ETFS = {'069500': 'KOSPI200', '229200': 'KOSDAQ150'}
TOP_N = 3            # per ranking that become Jev candidates
MAX_CANDIDATES = 10


def constituents(read):
    result = {}
    for etf, index in INDEX_ETFS.items():
        response = read.call_tool('call_api', {'operationId': 'krstockQuoteEtfComponents', 'input': {'iem_cd': etf}})
        data = response.get('data') if isinstance(response, dict) else None
        if response.get('status') != 'ok' or not isinstance(data, dict) or data.get('rsp_cd') != '00000':
            raise ValueError('ETF components unavailable')
        for row in data.get('Output_0') or []:
            code = row.get('iem_cd') if isinstance(row, dict) else None
            if isinstance(code, str) and re.fullmatch(CODE_PATTERN, code):
                result[code] = {'name': str(row.get('iem_nm') or code), 'index': index}
    if len(result) < 100:
        raise ValueError('too few constituents')
    return result


# 11-19: the 60-stock burst every five minutes went past NH's 5 calls a second per app key (IGW42902) and also refused the
# runner, mirror and dashboard calls at that moment; a quote starts at most this often (2 a second, as the dashboard)
SCAN_GAP = 0.5
RATE_LIMIT_WAIT = 1.0  # one more ask after a rate-limit refusal, so the stock stays in the rankings


def scan(read, universe, gap=0.0, clock=time.monotonic, sleep=time.sleep):
    from nh_live_broker import rate_limited
    rows, last = [], None
    for code, meta in universe.items():
        try:
            for retry in (RATE_LIMIT_WAIT, None):
                if last is not None:
                    sleep(max(0.0, last + gap - clock()))
                last = clock()
                response = read.call_tool('get_stock_price', {'stock_code': code})
                if retry is None or not rate_limited(response):
                    break
                sleep(retry)
            quote = (response.get('data') or {}).get('Output_0') or {}
            if response.get('status') != 'ok' or quote.get('iem_cd') != code:
                continue
            price, previous, volume, value = (quote.get(k) for k in ('stck_prpr', 'stck_prdy_clpr', 'acml_vol', 'acml_tr_pbmn'))
            if (not all(type(v) in (int, float) for v in (price, previous, volume, value))
                    or price <= 0 or previous <= 0 or volume <= 0):
                continue
            # prdy_ctrt arrives unsigned and the sign field is empty, so derive the signed change from closes.
            rows.append({'code': code, 'name': meta['name'].lstrip('*'), 'index': meta['index'], 'price': int(price),
                         'change_pct': round((price / previous - 1) * 100, 2), 'volume': int(volume), 'value_mil': int(value)})
        except (AttributeError, TypeError, ValueError):
            continue
    return rows


def kiwoom_scan(universe):
    """scan()'s rows from Kiwoom ka10095 (2026-11-20 NH call plan P2, docs/nh-call-reduction-plan.md): one call per 100
    codes instead of one NH quote each. Same fields and units (value_mil in 백만원 like NH acml_tr_pbmn); a halted or
    unpriced code is dropped as scan() drops it. Raises when Kiwoom fails, so the caller can fall back to NH."""
    import kiwoom_data
    codes, rows = list(universe), []
    for i in range(0, len(codes), 100):
        for s in kiwoom_data.call('ka10095', {'stk_cd': '|'.join(codes[i:i + 100])}, 'atn_stk_infr', max_pages=1):
            code = str(s.get('stk_cd') or '').split('_')[0]  # '090430_AL' (KRX+NXT) -> '090430'
            price, previous, volume, value = (kiwoom_data.num(s.get(k)) for k in ('cur_prc', 'base_pric', 'trde_qty', 'trde_prica'))
            if code not in universe or None in (price, previous, volume, value):
                continue
            price, previous = abs(price), abs(previous)
            if price <= 0 or previous <= 0 or volume <= 0:
                continue
            meta = universe[code]
            rows.append({'code': code, 'name': meta['name'].lstrip('*'), 'index': meta['index'], 'price': int(price),
                         'change_pct': round((price / previous - 1) * 100, 2), 'volume': int(volume), 'value_mil': int(value)})
    return rows


KIWOOM_MIN_SHARE = 0.9  # Kiwoom must price this share of the codes, else the NH scan answers


def quote_rows(read, quoted, log=print):
    """(rows, 'kiwoom' | 'nh'): Kiwoom first (11-20 same-moment bench: four top-10 rankings and the ten candidates
    identical to NH's, 62 codes in one call, 0.1 s against 62 NH calls), NH scan() when Kiwoom fails or prices too few."""
    try:
        rows = kiwoom_scan(quoted)
        if len(rows) >= KIWOOM_MIN_SHARE * len(quoted):
            return rows, 'kiwoom'
        log(json.dumps({'kiwoom_quotes': 'too_few', 'rows': len(rows), 'codes': len(quoted)}))
    except Exception as exc:  # noqa: BLE001 - no key or Kiwoom down: NH as before
        log(json.dumps({'kiwoom_quotes_error': type(exc).__name__}))
    return scan(read, quoted, gap=SCAN_GAP), 'nh'


def nh_candidates(read, universe, candidates, by_code):
    """After a Kiwoom-priced rank(): the candidates (what Jev is offered) quoted again from NH, so every figure Jev reads
    stays NH's (2026-11-20 NH call plan P2, option 1; ~10 NH calls a round instead of ~60). The other rows keep Kiwoom's
    figures for the ranking tables, marked source 'kiwoom' (the runner never lays those over Jev's inputs)."""
    for code in by_code:
        by_code[code] = {**by_code[code], 'source': 'kiwoom'}
    for row in scan(read, {c: universe[c] for c in candidates if c in universe}, gap=SCAN_GAP):
        by_code[row['code']] = row
        candidates[row['code']] = {**row, 'reasons': candidates[row['code']]['reasons']}
    for code, entry in candidates.items():
        if by_code.get(code, {}).get('source') == 'kiwoom':
            candidates[code] = {**entry, 'source': 'kiwoom'}  # NH did not answer: Kiwoom's figures, marked


PRESELECT = 20  # universe codes from each Kiwoom ranking; rank() keeps the top 10 of each from NH's own quotes (15 missed
# a 10th riser at the cutoff on 2026-11-17: the order shifts between the Kiwoom read and the NH quotes)
_RISE_FALL = {'mrkt_tp': '000', 'trde_qty_cnd': '0000', 'stk_cnd': '16', 'crd_cnd': '0', 'updown_incls': '1', 'pric_cnd': '0',
              'trde_prica_cnd': '0', 'stex_tp': '1'}
KIWOOM_RANKINGS = (
    ('ka10032', {'mrkt_tp': '000', 'mang_stk_incls': '0', 'stex_tp': '1'}, 'trde_prica_upper'),
    ('ka10030', {'mrkt_tp': '000', 'sort_tp': '1', 'mang_stk_incls': '0', 'crd_tp': '0', 'trde_qty_tp': '0', 'pric_tp': '0',
                 'trde_prica_tp': '0', 'mrkt_open_tp': '0', 'stex_tp': '1'}, 'tdy_trde_qty_upper'),
    ('ka10027', {**_RISE_FALL, 'sort_tp': '1'}, 'pred_pre_flu_rt_upper'),  # 상승률
    ('ka10027', {**_RISE_FALL, 'sort_tp': '3'}, 'pred_pre_flu_rt_upper'),  # 하락률
)


def preselect(universe, pages=5):
    """Universe codes that lead Kiwoom's KRX market-wide rankings (trading value, volume, rise, fall), PRESELECT per
    ranking in Kiwoom's order. Only picks which codes NH quotes; every figure written still comes from NH (scan())."""
    import kiwoom_data

    picked = []
    for api_id, body, key in KIWOOM_RANKINGS:
        found = []
        for row in kiwoom_data.call(api_id, body, key, max_pages=pages):
            code = str(row.get('stk_cd') or '').split('_')[0]
            if code in universe and code not in found:
                found.append(code)
            if len(found) >= PRESELECT:
                break
        picked += [c for c in found if c not in picked]
    return picked


def targets(universe, log=print):
    """(codes to quote, source): Kiwoom-preselected universe codes when Kiwoom answers with enough of them, else the
    whole universe (the NH scan as before)."""
    import kiwoom_data
    if kiwoom_data.configured():
        try:
            picked = preselect(universe)
            if len(picked) >= 2 * PRESELECT:  # too few matches: something is off, scan everything
                return {c: universe[c] for c in picked}, 'kiwoom_preselect'
            log(json.dumps({'kiwoom_preselect': 'too_few', 'picked': len(picked)}))
        except Exception as exc:  # Kiwoom down or refused: the full NH scan
            log(json.dumps({'kiwoom_preselect_error': type(exc).__name__}))
    return universe, 'nh_scan'


def rank(rows):
    orders = {'trading_value': ('value_mil', True), 'volume': ('volume', True),
              'rise': ('change_pct', True), 'fall': ('change_pct', False)}
    rankings = {key: [r['code'] for r in sorted(rows, key=lambda r: r[field], reverse=desc)[:10]]
                for key, (field, desc) in orders.items()}
    labels = {'trading_value': '거래대금 상위', 'volume': '거래량 상위', 'rise': '급상승', 'fall': '급하락'}
    by_code = {r['code']: r for r in rows}
    candidates = {}
    for key in ('trading_value', 'rise', 'fall', 'volume'):
        for code in rankings[key][:TOP_N]:
            if code in candidates or len(candidates) < MAX_CANDIDATES:
                entry = candidates.setdefault(code, {**by_code[code], 'reasons': []})
                entry['reasons'].append(labels[key])
    return rankings, candidates, by_code


# 주도 테마 동반주 (user 2026-11-19, docs/theme-slot-plan-2026-11-19.md): 11-19 the rise slots went to one theme's
# +24~30% entertainment stocks all day, so the cosmetics group (#2) never reached Jev. After rank(), each of the top
# THEME_GROUPS_TOP theme groups that is up and has no candidate yet adds its busiest universe riser, up to THEME_SLOTS.
# Only Jev's choices change; every order rule stays. Sources as the API bench ranked them (docs/api-selection.md):
# members once a day (Toss sectors ∪ Naver themes), figures from one Kiwoom ka10095 call, Naver when Kiwoom fails.
THEME_SLOTS, THEME_GROUPS_TOP, THEME_MIN_UP, THEME_MAX_UP = 2, 3, 2.0, 25.0  # +2~25%: the strength path's own band
THEME_QUOTE_TRIES = 2  # NH quotes per group at most (the shared app key, 5 calls a second)
THEME_PICKS = ROOT / 'theme-picks.json'  # {day: {code: {group, first}}}: the live mirror does not follow these (user)


def theme_picks(groups, universe, candidates, members_of, figures, quote):
    """Candidate rows to add: per strong group (theme_groups rows, strongest first) whose members are not yet among
    `candidates`, the member up THEME_MIN_UP~THEME_MAX_UP % with the most trading value. `members_of(group)` gives the
    group's universe codes, `figures(codes, groups)` {code: (change %, trading value)}, `quote(codes)` NH scan() rows.
    A failed lookup leaves the slot empty, as before this rule."""
    eligible = []
    for row in groups[:THEME_GROUPS_TOP]:
        if not isinstance(row.get('change_pct'), (int, float)) or row['change_pct'] <= 0:
            continue
        try:
            codes = set(members_of(row.get('group'))) & set(universe)
        except Exception:  # noqa: BLE001 - this group only
            continue
        if codes and not codes & set(candidates):  # otherwise Jev already sees this group
            eligible.append((row['group'], codes))
    if not eligible:
        return []
    try:
        figs = figures(sorted(set().union(*(codes for _, codes in eligible))), [g for g, _ in eligible])
    except Exception:  # noqa: BLE001 - no figures: no slot this round
        return []
    picks = []
    for group, codes in eligible:
        if len(picks) >= THEME_SLOTS:
            break
        taken = {p['code'] for p in picks}
        risers = sorted(((value or 0, code) for code in codes - taken for change, value in [figs.get(code) or (None, None)]
                         if change is not None and THEME_MIN_UP <= change <= THEME_MAX_UP), reverse=True)
        for _, code in risers[:THEME_QUOTE_TRIES]:  # r5: a failed NH quote tries the group's next busiest riser
            rows = quote([code])
            if rows:
                picks.append({**rows[0], 'reasons': [f'주도 테마 동반주 ({group})'], 'theme_group': group})
                break
    return picks


def theme_figures(universe):
    """figures() for theme_picks: Kiwoom ka10095 for the codes, else each group's Naver theme members."""
    import theme_data

    def figures(codes, groups):
        try:
            got, _ = theme_data.kiwoom_quotes(codes)
        except Exception:  # noqa: BLE001 - Kiwoom down or no key: the Naver figures
            got = {}
            for group in groups:
                got.update(theme_data.naver_members(group, universe)[0])
        return {code: (f.get('change_pct'), f.get('value_krw')) for code, f in got.items()}
    return figures


def theme_quote(read, universe, sleep=time.sleep):
    """quote() for theme_picks: NH scan rows, each quote SCAN_GAP after the last (the shared app key, 5 calls a second;
    11-19 review: right after the paced scan the theme quotes went out back to back)."""
    def quote(codes):
        sleep(SCAN_GAP)
        return scan(read, {c: universe[c] for c in codes}, gap=SCAN_GAP)
    return quote


def theme_round(read, universe, candidates, now, log=print):
    """theme_picks() on the context thread's latest theme groups (market-context.json, MAX_AGE); [] on any failure."""
    try:
        import market_context as mc
        import theme_data
        context = json.loads((ROOT / 'market-context.json').read_text())
        if now - datetime.fromisoformat(context['time']) > mc.MAX_AGE:
            return []
        groups = (context.get('themes') or {}).get('groups') or []
        picks = theme_picks(groups, universe, candidates, lambda group: theme_data.daily_members(group, universe)[0],
                            theme_figures(universe), theme_quote(read, universe))
        if picks:
            note_theme_picks(THEME_PICKS, now, picks)
        return picks
    except Exception as exc:  # noqa: BLE001 - the ranking itself goes on without the slot
        log(json.dumps({'theme_picks_error': type(exc).__name__}))
        return []


def note_theme_picks(path, now, picks, keep_days=10):
    """Keep each day's theme picks with the first time seen, to the second (the live mirror compares it with fill times;
    user 2026-11-19)."""
    day, hhmm = now.date().isoformat(), now.isoformat(timespec='seconds')
    from config_store import write_json
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {}
    today = data.setdefault(day, {})
    for pick in picks:
        today.setdefault(pick['code'], {'group': pick.get('theme_group'), 'first': hhmm})
    write_json(path, {d: data[d] for d in sorted(data)[-keep_days:]}, indent=None)


def write(path, payload):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def collect_context():
    """Regime, flows, themes and global prices for the engines and the dashboard; a failure only skips this round."""
    try:
        import market_context
        context = market_context.collect(datetime.now(KST))
        market_context.write(context)
        research_store.record('market_context', market_context.research_copy(context))
    except Exception as exc:
        print(json.dumps({'market_context_error': type(exc).__name__}), flush=True)


def shadow_round(read, universe, log=print):
    """Issue / theme / hot stocks, outside the universe first, recorded for research (issue_candidates, user
    2026-11-18); never orders, and a failure only skips this round."""
    now = datetime.now(KST)
    try:
        import issue_candidates
        result = issue_candidates.observe(read, now, universe)
    except Exception as exc:
        result = {'recorded': 0, 'errors': {'round': type(exc).__name__}}
    log(json.dumps({'issue_candidates': result}, ensure_ascii=False))
    try:  # the audit's heartbeat (intraday_audit: shadow.silent / shadow.source_error)
        from config_store import write_json
        write_json(ROOT / 'shadow-status.json', {'time': now.isoformat(timespec='seconds'), 'recorded': result.get('recorded'),
                                                 'errors': result.get('errors') or {}}, indent=None)
    except OSError:
        pass


def context_watch(stop, interval=300):
    """Refresh market context independently of the potentially slow quote scan."""
    while not stop.is_set():
        collect_context()
        if stop.wait(max(30, interval)):
            break


def refresh_cases():
    """The case library's daily series with the final KRX close and the US night (oversold_cases.refresh_series)."""
    try:
        import oversold_cases
        print(json.dumps({'oversold_series': oversold_cases.refresh_series()}), flush=True)
    except Exception as exc:
        print(json.dumps({'oversold_series_error': type(exc).__name__}), flush=True)


def running(now):
    """Inside the ranking service's day: 09:00 to 15:05 on the day's exchange clock (16:05 on 수능 day)."""
    from session_times import shifted
    return now.weekday() < 5 and '09:00' <= now.strftime('%H:%M') < shifted('15:05', now.date())


def wait_for_open(now, sleep=time.sleep):
    """Started before 09:00 on a weekday: take a pre-open market snapshot (last US close), then wait for the open."""
    if now.weekday() >= 5 or now.strftime('%H:%M') >= '09:00':
        return False
    threading.Thread(target=refresh_cases, daemon=True).start()  # ~100 Yahoo reads; must never delay the open
    collect_context()
    sleep(max(0.0, (now.replace(hour=9, minute=0, second=0, microsecond=0) - datetime.now(KST)).total_seconds()))
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--interval', type=int, default=300)
    args = parser.parse_args()
    from src.config.loader import load_agent_config
    from src.tools.mcp import MCPServerAdapter
    read = MCPServerAdapter('nhplug_readonly', load_agent_config().mcp_servers['nhplug_readonly'])
    universe, universe_day = None, None
    if not args.once:
        wait_for_open(datetime.now(KST))  # the service starts at 08:50; without this the loop below exited at once
    stop = threading.Event()
    if not args.once:
        threading.Thread(target=context_watch, args=(stop, args.interval), daemon=True).start()
    while True:
        now = datetime.now(KST)
        if not args.once and not running(now):
            break
        if args.once:
            collect_context()
        try:
            if universe_day != now.date():
                universe, universe_day = constituents(read), now.date()
            started = time.time()
            quoted, source = targets(universe, lambda line: print(line, flush=True))
            rows, quote_source = quote_rows(read, quoted, lambda line: print(line, flush=True))
            rankings, candidates, by_code = rank(rows)
            if quote_source == 'kiwoom':
                nh_candidates(read, universe, candidates, by_code)
            picks = theme_round(read, universe, candidates, datetime.now(KST), lambda line: print(line, flush=True))
            for pick in picks:
                candidates[pick['code']] = pick
                by_code[pick['code']] = {k: v for k, v in pick.items() if k not in ('reasons', 'theme_group')}
            payload = {'time': datetime.now(KST).isoformat(), 'scanned': len(rows), 'source': source, 'quote_source': quote_source,
                       'universe': len(universe),
                       'seconds': round(time.time() - started, 1), 'rankings': rankings,
                       'candidates': candidates, 'theme_picks': [{'code': p['code'], 'group': p['theme_group']} for p in picks],
                       'rows': {c: by_code[c] for c in [*(c for r in rankings.values() for c in r), *(p['code'] for p in picks)]}}
            write(ROOT / 'movers.json', payload)
            research_store.record('movers', payload)
            print(json.dumps({'time': now.isoformat(timespec='seconds'), 'scanned': len(rows),
                              'candidates': list(candidates)}, ensure_ascii=False), flush=True)
            shadow_round(read, universe, lambda line: print(line, flush=True))
            import watchlist
            if candidates and watchlist.due(datetime.now(KST)):
                try:
                    record = watchlist.select(read, candidates, datetime.now(KST))
                    watchlist.write(record)
                    research_store.record('watchlist', record)
                    print(json.dumps({'watchlist': record['selected'], 'jev_choice': record['jev_choice']}), flush=True)
                except Exception as exc:  # the previous watchlist stays until it expires
                    print(json.dumps({'watchlist_error': type(exc).__name__}), flush=True)
        except Exception as exc:  # a failed scan only leaves the previous file to expire
            print(json.dumps({'time': now.isoformat(timespec='seconds'), 'error': type(exc).__name__,
                              'reason': str(exc) if str(exc) in ('ETF components unavailable', 'too few constituents') else 'scan failed'}), flush=True)
        if args.once:
            break
        time.sleep(max(30, args.interval - (time.time() - now.timestamp())))
    stop.set()


if __name__ == '__main__':
    main()
