"""낙폭과대 반등 (oversold rebound) — shared by the Jev runner, the vibe-trading advisor/runner and the dashboard.

User request 2026-11-13: when a solid large-cap stock or a broad ETF suddenly falls far more than its usual range
(often on a US shock such as the Nasdaq falling 5% overnight), buy a large size once the fall stops and sell as soon
as the overshoot unwinds. This module only measures; each engine decides with its own judgment:

- `scan(read, now, settings, universe)`: per watched code, today's fall from the previous close against its 20-day
  average true range, whether it is oversold for its kind (stock / 1x ETF / 2x ETF), whether the fall has stopped
  (no new low for N minutes and a bounce off the low), and the take-profit / stop prices of a rebound trade.
- `analyst(code)`: the broker consensus (mean opinion, mean target price) and the latest research titles from Naver.
- `universe()` / `save_universe()`: the watched codes (oversold-universe.json, edited on the dashboard).
Research and paper trading only; nothing here places orders.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo
import api_calls  # noqa: F401,E402 - counts this process's external API calls (dashboard 'API 연동')
from etf_universe import is_code

KST = ZoneInfo('Asia/Seoul')
ROOT = Path.home() / '.vibe-trading/nhplug/shared-etf-paper'
DEFAULT_UNIVERSE = {'069500': 'KODEX 200', '122630': 'KODEX 레버리지', '494310': 'KODEX 반도체레버리지',
                    '005930': '삼성전자', '000660': 'SK하이닉스', '196170': '알테오젠'}
LEVERAGED = {'122630', '233740', '494310'}
ETFS = {'069500', '122630', '114800', '229200', '233740', '251340', '494310'}


def kind(code):
    return 'lev' if code in LEVERAGED else 'etf' if code in ETFS else 'stock'


def universe(root=ROOT):
    """{code: name} watched for oversold rebounds; the defaults when the file is missing or unreadable."""
    try:
        data = json.loads((Path(root) / 'oversold-universe.json').read_text(encoding='utf-8'))
        codes = {c: str(n)[:40] for c, n in (data.get('codes') or {}).items() if is_code(str(c))}
        return codes or dict(DEFAULT_UNIVERSE)
    except (OSError, ValueError, AttributeError):
        return dict(DEFAULT_UNIVERSE)


def save_universe(codes, root=ROOT):
    import config_store
    clean = {c: str(n)[:40] for c, n in codes.items() if is_code(str(c))}
    if not clean:
        raise ValueError('낙폭과대 감시 종목이 하나 이상 있어야 합니다.')
    before = universe(root)
    config_store.write_json(Path(root) / 'oversold-universe.json', {'version': 1, 'codes': clean})
    config_store.record('oversold_universe', before, clean, saved=Path(root) / 'oversold-universe.json')
    return clean


def atr_pct(rows):
    """20-day average true range in % of the previous close from NH daily rows (newest first); None if under 10 days."""
    bars = []
    for row in rows or []:
        try:
            bars.append((str(row['bsop_date']), float(row['stck_hgpr']), float(row['stck_lwpr']), float(row['stck_prpr'])))
        except (KeyError, TypeError, ValueError):
            continue
    bars.sort()  # oldest first
    ranges = []
    for (_, high, low, _), (_, _, _, prev_close) in zip(bars[1:], bars[:-1]):
        if prev_close > 0 and high >= low > 0:
            ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)) / prev_close * 100)
    ranges = ranges[-21:-1] if len(ranges) > 20 else ranges[:-1]  # past sessions only, not today
    return round(sum(ranges) / len(ranges), 3) if len(ranges) >= 10 else None


_ATR = {}


def daily_atr(read, code, now):
    """atr_pct() of NH daily bars, cached for the day."""
    key = (code, now.astimezone(KST).date())
    if key not in _ATR:
        try:
            response = read.call_tool('call_api', {'operationId': 'krstockQuotePeriod', 'input': {
                'market_cd': 'KRX', 'iem_cd': code, 'gubun': '1', 'array_cnt': '30', 'view_main_yn': 'Y', 'today_cls_code': '0'}})
            data = (response.get('data') or {}) if response.get('status') == 'ok' else {}
            _ATR[key] = atr_pct(data.get('Output_1')) if data.get('rsp_cd') == '00000' else None
        except Exception:  # a failed read only means no ATR: the % threshold alone then decides
            _ATR[key] = None
    return _ATR[key]


def evaluate(code, quote, bars, atr, settings, now):
    """The oversold facts of one code. `quote`: NH Output_0 (stck_prpr, stck_prdy_clpr, stck_lwpr, askp1/bidp1);
    `bars`: completed 1-minute bars, newest first ({'bsop_time', 'stck_lwpr', 'stck_prpr'}). Returns a dict with
    'triggered' (fell far enough), 'stabilized' (the fall stopped) and the plan prices, or None without data."""
    try:
        price, prev, low = (float(quote[k]) for k in ('stck_prpr', 'stck_prdy_clpr', 'stck_lwpr'))
    except (KeyError, TypeError, ValueError):
        return None
    if min(price, prev, low) <= 0 or low > price:
        return None
    k = kind(code)
    if k == 'lev' and not settings.get('oversold_lev_on'):
        return None  # research: no edge for 2x ETFs, so they are not watched unless switched on
    floor = settings[f'oversold_{k}_drop_pct']
    need = max(floor, settings['oversold_atr_mult'] * atr) if atr else floor
    drop = (low / prev - 1) * 100            # deepest point today
    now_drop = (price / prev - 1) * 100
    calm = int(settings['oversold_calm_min'])
    lows = []
    for row in (bars or [])[:max(calm, 1)]:
        try:
            lows.append(float(row['stck_lwpr']))
        except (KeyError, TypeError, ValueError):
            pass
    no_new_low = len(lows) >= calm and min(lows) > low  # the day's low is older than the last `calm` minutes
    bounce = (price / low - 1) * 100
    gap = prev - low
    # the lower of a fixed take-profit and the price that recovers recover_pct of the gap (research: only ~14% of the
    # gap comes back by the close, so the fixed +1.5% usually decides)
    target = min(price * (1 + settings['oversold_tp_pct'] / 100), low + gap * settings['oversold_recover_pct'] / 100)
    stop = low * (1 - settings['oversold_stop_pct'] / 100)
    return {'code': code, 'kind': k, 'price': price, 'prev_close': prev, 'low': low, 'drop_pct': round(drop, 2),
            'now_drop_pct': round(now_drop, 2), 'atr_pct': atr, 'atr_multiple': round(-drop / atr, 2) if atr else None,
            'need_pct': round(need, 2), 'triggered': -drop >= need, 'no_new_low_min': calm if no_new_low else 0,
            'bounce_pct': round(bounce, 2), 'stabilized': no_new_low and bounce >= settings['oversold_rebound_pct'],
            'target_price': round(target), 'target_pct': round((target / price - 1) * 100, 2),
            'stop_price': round(stop), 'stop_pct': round((1 - stop / price) * 100, 2), 'time': now.isoformat(timespec='seconds')}


def scan(read, now, settings, codes=None, held=(), bars_of=None):
    """evaluate() for every watched code not held; codes that fell far enough are checked for a stop of the fall with
    `bars_of(code)` (completed 1-minute bars), the others skip that read. Deepest (in ATR) first."""
    out = []
    for code in (codes or universe()):
        if code in held:
            continue
        try:
            quote = (read.call_tool('get_stock_price', {'stock_code': code}).get('data') or {}).get('Output_0') or {}
        except Exception:
            continue
        if quote.get('iem_cd') != code:
            continue
        atr = daily_atr(read, code, now)
        first = evaluate(code, quote, [], atr, settings, now)
        if not first:
            continue
        if first['triggered'] and bars_of:
            try:
                first = evaluate(code, quote, bars_of(code), atr, settings, now) or first
            except Exception:
                pass
        out.append(first)
    if any(r['triggered'] for r in out):  # the market's own fall: a market-wide crash rebounds, a lone fall often does not
        market = {}
        for name, proxy in (('kospi', '069500'), ('kosdaq', '229200')):
            try:
                q = (read.call_tool('get_stock_price', {'stock_code': proxy}).get('data') or {}).get('Output_0') or {}
                market[name] = round((float(q['stck_lwpr']) / float(q['stck_prdy_clpr']) - 1) * 100, 2)
            except Exception:
                continue
        import oversold_cases
        for r in out:
            r['market_low_pct'] = market.get(oversold_cases.market_of_code(r['code']))
    return sorted(out, key=lambda r: -(r['atr_multiple'] or 0))


_ANALYST = {}


def market_ok(row, settings):
    """User 2026-11-17: the intraday rebound buys only on a market-wide fall — the code's market (KODEX 200 / 코스닥150
    low vs the previous close, scan's market_low_pct) at least oversold_market_drop_pct down; 0 turns the condition off.
    Replay of the last six months: the rule made -0.34%/day on every stock fall (40% up days) and +0.53% on crash days
    only. An unknown market does not pass."""
    need = settings.get('oversold_market_drop_pct', 3.0)
    if not need:
        return True
    low = row.get('market_low_pct')
    return type(low) in (int, float) and low <= -need


def report_tone(text):
    """'up' / 'down' / 'neutral' from report titles (or previews): raised or beat vs cut, missed or sell."""
    if re.search(r'상향|상회|매수 유지|서프라이즈|최대', text) and not re.search(r'하향|하회|매도', text):
        return 'up'
    return 'down' if re.search(r'하향|하회|매도|쇼크', text) else 'neutral'


def analyst(code, timeout=6):
    """{'recomm_mean' (1 sell .. 5 strong buy), 'target_mean', 'reports': [{date, broker, title}]} from Naver's
    stock summary, cached for an hour; {} when unavailable."""
    hit = _ANALYST.get(code)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    result = {}
    try:
        request = urllib.request.Request(f'https://m.stock.naver.com/api/stock/{code}/integration', headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read(500_000).decode('utf-8', 'replace'))
        consensus = data.get('consensusInfo') or {}
        number = lambda v: float(str(v).replace(',', '')) if v not in (None, '') else None
        result = {'recomm_mean': number(consensus.get('recommMean')), 'target_mean': number(consensus.get('priceTargetMean')),
                  'consensus_date': consensus.get('createDate'),
                  'reports': [{'date': r.get('wdt'), 'broker': r.get('bnm'), 'title': str(r.get('tit', ''))[:80], 'nid': r.get('id')}
                              for r in (data.get('researches') or [])[:5] if isinstance(r, dict)]}
        result['report_tone'] = report_tone(' '.join(r['title'] for r in result['reports']))
    except (OSError, ValueError, AttributeError, TypeError):
        result = {}
    _ANALYST[code] = (time.time(), result)
    return result


def brief(row, name=None, with_analyst=True):
    """Compact facts for an engine prompt (Jev extra_state / vibe context)."""
    out = {k: row.get(k) for k in ('drop_pct', 'now_drop_pct', 'atr_pct', 'atr_multiple', 'need_pct', 'triggered',
                                   'stabilized', 'bounce_pct', 'target_pct', 'stop_pct', 'market_low_pct')}
    if name:
        out['name'] = name
    import oversold_cases
    out['past_similar_falls'] = oversold_cases.brief(row['code'], row.get('drop_pct'), row.get('atr_multiple'), row.get('market_low_pct'))
    if with_analyst:
        info = analyst(row['code'])
        if info.get('target_mean') and row.get('price'):
            info = {**info, 'target_upside_pct': round((info['target_mean'] / row['price'] - 1) * 100, 1)}
        out['analyst'] = info
    return out


# 선별 v2 (user 2026-11-13: "일단 진입하고 모의매매로 데이터를 쌓자"). The replay of every past KRX fall
# (oversold_replay.gate_v2, docs/oversold-case-study.md 2-1) found an edge only on market crashes: the index down 8%,
# or 5% after already falling 5% over five sessions, not after a rising Nasdaq night; bought near the close and held up
# to five sessions with a stop 5% under the day's low. Paper trading only; the hypothesis is still being tested.
_V2_GATE, _NASDAQ = {}, {}
NASDAQ_RETRY = 300  # seconds before a failed Nasdaq read is tried again


def nasdaq_prev_night(today):
    """Nasdaq % change of the last US session before `today` (KST date), from Yahoo; a value is kept for the day, a
    failed read is retried after NASDAQ_RETRY seconds (one blip must not block the day). None if unavailable."""
    hit = _NASDAQ.get(today)
    if hit and (hit[1] is not None or time.time() - hit[0] < NASDAQ_RETRY):
        return hit[1]
    import oversold_research
    bars = oversold_research.fetch('^IXIC', '1d', '10d')
    days = [b for b in bars if b[0].date() < today]
    value = round((days[-1][4] / days[-2][4] - 1) * 100, 2) if len(days) >= 2 else None
    _NASDAQ.clear()
    _NASDAQ[today] = (time.time(), value)
    return value


def market_prior5(name, today):
    """KOSPI / KOSDAQ % change over the five sessions before today, from the case library's daily series."""
    import oversold_cases
    from sqlite_ro import ro_connect
    try:
        with ro_connect(oversold_cases.DB) as conn:
            rows = conn.execute('SELECT day, c FROM series WHERE symbol = ? AND day < ? ORDER BY day DESC LIMIT 6',
                                ({'kospi': '^KS11', 'kosdaq': '^KQ11'}[name], today.isoformat())).fetchall()
    except Exception:
        return None
    if len(rows) < 6 or rows[0][0] != oversold_cases.previous_session(today).isoformat():
        return None  # a series that missed a refresh would shift the five sessions
    return round((rows[0][1] / rows[5][1] - 1) * 100, 2)


def v2_gate(read, now, settings):
    """{'kospi'|'kosdaq': {'low_pct', 'prior5_pct', 'pass'}, 'nasdaq_prev', 'time'} for this minute (cached).
    KODEX 200 and KODEX 코스닥150 stand in for the indices; an unknown Nasdaq night never passes."""
    key = now.strftime('%Y-%m-%d %H:%M')
    if key in _V2_GATE:
        return _V2_GATE[key]
    today = now.date()
    out = {'nasdaq_prev': None, 'time': now.isoformat(timespec='seconds')}
    crashes = {}
    for name, proxy in (('kospi', '069500'), ('kosdaq', '229200')):
        try:
            q = (read.call_tool('get_stock_price', {'stock_code': proxy}).get('data') or {}).get('Output_0') or {}
            low_px, prev_px = float(q['stck_lwpr']), float(q['stck_prdy_clpr'])
            if low_px <= 0 or prev_px <= 0 or q.get('iem_cd') not in (None, proxy):
                raise ValueError('market quote unusable')
            low = round((low_px / prev_px - 1) * 100, 2)
        except Exception:
            out[name] = {'low_pct': None, 'prior5_pct': None, 'pass': False}
            continue
        prior5 = market_prior5(name, today) if low <= -settings['oversold_v2_capit_drop_pct'] else None
        crashes[name] = low <= -settings['oversold_v2_index_drop_pct'] or (
            low <= -settings['oversold_v2_capit_drop_pct'] and prior5 is not None and prior5 <= -settings['oversold_v2_prior5_pct'])
        out[name] = {'low_pct': low, 'prior5_pct': prior5, 'pass': False}
    if any(crashes.values()):  # the Nasdaq night is read only on a crash day
        out['nasdaq_prev'] = nasdaq = nasdaq_prev_night(today)
        for name, crash in crashes.items():
            out[name]['pass'] = bool(crash and nasdaq is not None and nasdaq <= 0)
    _V2_GATE.clear()
    _V2_GATE[key] = out
    return out


def sent_today(events_path, day, lines=20000):
    """{(source, symbol)} of 선별 v2 orders the runner journaled `day` (its events.jsonl tail): what was already sent
    stays sent after a restart."""
    out = set()
    try:
        tail = Path(events_path).read_text(encoding='utf-8').splitlines()[-lines:]
    except OSError:
        return out
    for line in tail:
        if f'"{day}' not in line[:40] or '"oversold_v2' not in line or '"submitted"' not in line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get('event') == 'submitted' and e.get('source') in ('oversold_v2', 'oversold_v2_add'):
            out.add((e['source'], e.get('symbol')))
    return out


def v2_candidates(read, gate, settings, codes, held=()):
    """Watched codes on a passing market that fell at least the case threshold themselves and not more than
    oversold_v2_rel_max_pct beyond their market (a 2x ETF against twice it); deepest first. Each row carries the
    stop (oversold_v2_stop_pct under the buy price; user 2026-11-17) and the holding sessions."""
    import oversold_cases
    out = []
    for code in codes:
        if code in held or code in oversold_cases.INVERSE:
            continue
        k = kind(code)
        if k == 'lev' and not settings.get('oversold_lev_on'):
            continue
        market = oversold_cases.market_of_code(code)  # None for a code whose exchange is not known: never guessed
        facts = gate.get(market) or {}
        if not facts.get('pass'):
            continue
        try:
            q = (read.call_tool('get_stock_price', {'stock_code': code}).get('data') or {}).get('Output_0') or {}
            price, prev, low = (float(q[f]) for f in ('stck_prpr', 'stck_prdy_clpr', 'stck_lwpr'))
        except Exception:
            continue
        if min(price, prev, low) <= 0 or q.get('iem_cd') != code:
            continue
        drop = (low / prev - 1) * 100
        rel = drop - facts['low_pct'] * (2 if k == 'lev' else 1)
        if drop > -oversold_cases.THRESHOLD[k] or rel <= -settings['oversold_v2_rel_max_pct']:
            continue
        stop = price * (1 - settings['oversold_v2_stop_pct'] / 100)
        out.append({'code': code, 'kind': k, 'price': price, 'prev_close': prev, 'low': low, 'drop_pct': round(drop, 2),
                    'market': market, 'market_low_pct': facts['low_pct'], 'rel_drop': round(rel, 2),
                    'stop_price': round(stop), 'stop_pct': round((1 - stop / price) * 100, 2),
                    'half_tp_pct': float(settings['oversold_v2_half_tp_pct']), 'target_pct': float(settings['oversold_v2_tp_pct']),
                    'hold_days': int(settings['oversold_v2_hold_days'])})
    return sorted(out, key=lambda r: r['drop_pct'])
