"""Read-only Kiwoom REST market data (https://openapi.kiwoom.com). Never orders.

The `.env` key (KIWOOM_APP_KEY / KIWOOM_APP_SECRET) is a live-account app key, so only the TRs listed in
`ALLOWED` can be called; order, credit and account TRs (kt*) are refused before any request is made.
The token is cached in one 0600 file shared by every process (broker_token) and never printed or logged.
Kiwoom terms (오픈 API 서비스 이용약관 §5③): quote data is for the customer's own use, not for third parties.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import broker_token
from config_store import write_json
import api_calls  # noqa: F401,E402 - counts this process's external API calls (dashboard 'API 연동')

KST = ZoneInfo('Asia/Seoul')
BASE = 'https://api.kiwoom.com'
TOKEN_FILE = Path.home() / '.vibe-trading/kiwoom/token.json'
ALLOWED = {'ka90005': '/api/dostk/mrkcond',  # 프로그램매매추이 시간대별
           'ka40010': '/api/dostk/etf',  # ETF 시간대별 수급
           'ka90008': '/api/dostk/mrkcond',  # 종목시간별 프로그램매매추이
           'ka10080': '/api/dostk/chart',  # 주식분봉차트 (about a year back)
           'ka10046': '/api/dostk/mrkcond',  # 체결강도추이 시간별 (fade_shadow, after the close)
           'ka10059': '/api/dostk/stkinfo',  # 종목별투자자기관별 (the closed day's foreign net quantity)
           'ka10037': '/api/dostk/rkinfo',  # 외국계창구매매상위
           'ka11370': '/api/dostk/rkinfo',  # KRX/NXT괴리율순위
           'ka10032': '/api/dostk/rkinfo',  # 거래대금상위
           'ka10030': '/api/dostk/rkinfo',  # 당일거래량상위
           'ka10027': '/api/dostk/rkinfo',  # 전일대비등락률상위
           'ka20001': '/api/dostk/sect',  # 업종현재가 (Naver fallback)
           'ka90001': '/api/dostk/thme',  # 테마그룹별 (Naver fallback)
           'ka90002': '/api/dostk/thme',  # 테마구성종목 (api_bench theme_members)
           'ka10095': '/api/dostk/stkinfo'}  # 관심종목정보: up to 100 codes a call (theme slot figures, 2026-11-19); read-only TRs only
AUTH_FAILED = 3
BARS_DIR = Path.home() / '.vibe-trading/kiwoom/minute'
GAP = 0.25  # seconds between requests: the TRs allow 5 per second
_last = [0.0]


class KiwoomError(RuntimeError):
    pass


def configured():
    return all(_keys())


def _keys():
    from dotenv import dotenv_values
    from local_env import ENV

    values = dotenv_values(ENV, interpolate=False)
    return values.get('KIWOOM_APP_KEY') or '', values.get('KIWOOM_APP_SECRET') or ''


def _post(path, body, headers=None, timeout=10):
    import requests
    import time

    # shortcut: paces this process only; parallel processes could still exceed 5/s together (add a file lock if they do)
    time.sleep(max(0.0, _last[0] + GAP - time.monotonic()))
    _last[0] = time.monotonic()
    response = requests.post(BASE + path, json=body, timeout=timeout,
                             headers={'Content-Type': 'application/json;charset=UTF-8', **(headers or {})})
    response.raise_for_status()
    return response.headers, response.json()


def _token(stale=None):
    """The shared token (broker_token: one locked file for every process)."""
    def issue():
        key, secret = _keys()
        if not (key and secret):
            raise KiwoomError('KIWOOM_APP_KEY / KIWOOM_APP_SECRET missing')
        _, data = _post('/oauth2/token', {'grant_type': 'client_credentials', 'appkey': key, 'secretkey': secret})
        if data.get('return_code') != 0 or not data.get('token'):
            raise KiwoomError(f"token: return_code {data.get('return_code')}")  # never the message body: it may echo input
        return data['token'], datetime.strptime(data['expires_dt'], '%Y%m%d%H%M%S').replace(tzinfo=KST)
    return broker_token.token(TOKEN_FILE, issue, stale)


def call(api_id, body, list_key, max_pages=5, timeout=10):
    """Rows of `list_key` across continuation pages (cont-yn / next-key); with `list_key` None, the first answer itself.

    Only an authentication failure (return_code 3, '8005 Token이 유효하지 않습니다', checked 2026-11-17) renews the
    shared token, once, and repeats the same page; any other code raises without touching other processes' token."""
    if api_id not in ALLOWED:
        raise KiwoomError(f'{api_id} is not an allowed read-only TR')
    endpoint = f'api.kiwoom.com{ALLOWED[api_id]}#{api_id}'
    token, renewed, rows, cont, pages = _token(), False, [], {}, 0
    while pages < max_pages:
        headers, data = _post(ALLOWED[api_id], body, {'api-id': api_id, 'authorization': 'Bearer ' + token, **cont},
                              timeout)
        code = data.get('return_code')
        if code != 0:
            api_calls.problem('kiwoom', endpoint, f'return_code {code}')
            if code == AUTH_FAILED and not renewed:
                token, renewed = _token(stale=token), True
                continue
            raise KiwoomError(f"{api_id}: return_code {code} {data.get('return_msg')}")
        if list_key is None:
            return data
        pages += 1
        rows += data.get(list_key) or []
        if headers.get('cont-yn') != 'Y' or not headers.get('next-key'):
            break
        cont = {'cont-yn': 'Y', 'next-key': headers['next-key']}
    return rows


def stock_program_rows(code, day, *, now=None):
    """Read one page of today's KRX stock program trading quantities (ka90008)."""
    now = now or datetime.now(KST)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('now must be timezone-aware')
    if not isinstance(code, str) or not re.fullmatch(r'[0-9]{6}', code):
        raise ValueError('invalid KRX stock code')
    if isinstance(day, datetime) or day != now.astimezone(KST).date():
        raise ValueError('program trading is available for today only')
    return call('ka90008', {'amt_qty_tp': '2', 'stk_cd': code, 'date': day.strftime('%Y%m%d')},
                'stk_tm_prm_trde_trnsn', max_pages=1, timeout=2)


def day_bars(code, day, scope=1, adjusted=False, now=None):
    """{'bars': [(datetime KST, o, h, l, c, volume)] oldest first, 'prev_close'} of one KRX regular session (ka10080).

    One page (900 rows) covers the whole day for 1-minute and longer bars. Finished days are cached as files;
    an empty answer (holiday, too old, unknown code) is not cached. `adjusted`: 수정주가 (research), else raw prices
    (fills compare with raw prices)."""
    now = now or datetime.now(KST)
    path = BARS_DIR / f"{code}-{day:%Y%m%d}-{scope}m{'-adj' if adjusted else ''}.json"
    try:
        cached = json.loads(path.read_text(encoding='utf-8'))
        return {'bars': [(datetime.fromisoformat(t), *v) for t, *v in cached['bars']], 'prev_close': cached['prev_close']}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    raw = call('ka10080', {'stk_cd': code, 'tic_scope': str(scope), 'upd_stkpc_tp': '1' if adjusted else '0',
                           'base_dt': f'{day:%Y%m%d}'}, 'stk_min_pole_chart_qry', max_pages=1)
    bars, prev_close = [], None
    for r in raw:
        t = str(r.get('cntr_tm') or '')
        values = [abs(num(r.get(f)) or 0) for f in ('open_pric', 'high_pric', 'low_pric', 'cur_prc')]  # sign = direction
        if t[:8] != f'{day:%Y%m%d}' or not all(values):
            continue
        if num(r.get('pred_pre')) is not None:
            prev_close = values[3] - num(r['pred_pre'])  # every row carries close - previous close
        if not '090000' <= t[8:] <= '153000':  # NXT pre/after-market rows (08:00~, ~20:00) come in the same answer
            continue
        try:
            stamp = datetime.strptime(t, '%Y%m%d%H%M%S').replace(tzinfo=KST)
        except ValueError:
            raise KiwoomError('invalid Kiwoom minute bar timestamp') from None
        bars.append((stamp, *values, num(r.get('trde_qty')) or 0))
    bars.sort(key=lambda b: b[0])
    out = {'bars': bars, 'prev_close': prev_close}
    finished = day < now.date() or now.strftime('%H:%M') >= '15:40'
    if bars and finished:
        write_json(path, {'bars': [[b[0].isoformat(), *b[1:]] for b in bars], 'prev_close': prev_close}, indent=None)
    return out


def num(text):
    """Kiwoom signed strings: '+32997' 32997, '--196599' -196599 (direction sign + signed value), '-1.49' -1.49."""
    s = str(text or '').strip().replace(',', '')
    if len(s) > 1 and s[0] in '+-' and s[1] in '+-':
        s = s[1:]
    try:
        return float(s)
    except ValueError:
        return None


def foreign_net(code, day):
    """Foreign net buying (shares) of one closed day 'YYYYMMDD' (ka10059), or None when there is no row of that day."""
    for r in call('ka10059', {'dt': day, 'stk_cd': code, 'amt_qty_tp': '2', 'trde_tp': '0', 'unit_tp': '1'}, 'stk_invsr_orgn',
                  max_pages=1, timeout=2):
        if r.get('dt') == day:
            value = num(r.get('frgnr_invsr'))
            return None if value is None else int(value)
    return None


def krx_minute_rows(code, prior=False, now=None):
    """Up to 30 KRX regular-session 1-minute rows (ka10080, the plain KRX code), newest first, in the shape of NH's
    krstockQuotePeriod Output_1: today's only, or with `prior` the sessions before it too. NH stamps a bar with the minute
    it ends and Kiwoom with the minute it starts, so a minute is added (11-24, six codes: highs and lows then agree, closes
    within a tick or two, volume within a few percent; the two cut the minute about a second apart)."""
    now = now or datetime.now(KST)
    out = []
    for r in call('ka10080', {'stk_cd': code, 'tic_scope': '1', 'upd_stkpc_tp': '0'}, 'stk_min_pole_chart_qry', max_pages=1):
        t = str(r.get('cntr_tm') or '')
        values = [abs(num(r.get(f)) or 0) for f in ('open_pric', 'high_pric', 'low_pric', 'cur_prc')]
        if not '090000' <= t[8:] <= '153000' or not all(values) or (t[:8] != f'{now:%Y%m%d}' and not prior):
            continue  # NXT pre/after-market rows come in the same answer
        ends = min(f'{datetime.strptime(t, "%Y%m%d%H%M%S") + timedelta(minutes=1):%H%M%S}', '153000')
        out.append({'bsop_date': t[:8], 'bsop_time': ends,
                    **{k: str(int(v)) for k, v in zip(('stck_oprc', 'stck_hgpr', 'stck_lwpr', 'stck_prpr'), values)},
                    'vol': str(int(abs(num(r.get('trde_qty')) or 0)))})
    return sorted(out, key=lambda r: (r['bsop_date'], r['bsop_time']), reverse=True)[:30]


def nxt_premarket_bars(code, now=None):
    """Today's NXT pre-market (08:00~08:50) 1-minute bars of a stock from ka10080 with the `_NX` code, newest first:
    [{'bsop_time', 'stck_oprc', 'stck_hgpr', 'stck_lwpr', 'stck_prpr', 'vol'}] (time = the bar's start minute, prices
    without the direction sign). User 2026-11-20: the opening entry chart of a stock (NH Namuh Plug has no NXT pre-market
    bars; checked 11-20). [] when none."""
    now = now or datetime.now(KST)
    day = f'{now:%Y%m%d}'
    out = []
    for r in call('ka10080', {'stk_cd': f'{code}_NX', 'tic_scope': '1', 'upd_stkpc_tp': '0'}, 'stk_min_pole_chart_qry', max_pages=1):
        t = str(r.get('cntr_tm') or '')
        values = [abs(num(r.get(f)) or 0) for f in ('open_pric', 'high_pric', 'low_pric', 'cur_prc')]
        if t[:8] != day or not '080000' <= t[8:] < '085000' or not all(values):
            continue
        out.append({'bsop_time': t[8:], **{k: str(int(v)) for k, v in zip(('stck_oprc', 'stck_hgpr', 'stck_lwpr', 'stck_prpr'), values)},
                    'vol': str(int(abs(num(r.get('trde_qty')) or 0)))})
    return sorted(out, key=lambda r: r['bsop_time'], reverse=True)
