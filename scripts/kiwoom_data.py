"""Read-only Kiwoom REST market data (https://openapi.kiwoom.com). Never orders.

The `.env` key (KIWOOM_APP_KEY / KIWOOM_APP_SECRET) is a live-account app key, so only the TRs listed in
`ALLOWED` can be called; order, credit and account TRs (kt*) are refused before any request is made.
The token is cached in one 0600 file shared by every process (broker_token) and never printed or logged.
Kiwoom terms (오픈 API 서비스 이용약관 §5③): quote data is for the customer's own use, not for third parties.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import broker_token
from config_store import write_json
import api_calls  # noqa: F401,E402 - counts this process's external API calls (dashboard 'API 연동')

KST = ZoneInfo('Asia/Seoul')
BASE = 'https://api.kiwoom.com'
TOKEN_FILE = Path.home() / '.vibe-trading/kiwoom/token.json'
ALLOWED = {'ka90005': '/api/dostk/mrkcond',  # 프로그램매매추이 시간대별
           'ka10080': '/api/dostk/chart',  # 주식분봉차트 (about a year back)
           'ka10037': '/api/dostk/rkinfo',  # 외국계창구매매상위
           'ka11370': '/api/dostk/rkinfo',  # KRX/NXT괴리율순위
           'ka10032': '/api/dostk/rkinfo',  # 거래대금상위
           'ka10030': '/api/dostk/rkinfo',  # 당일거래량상위
           'ka10027': '/api/dostk/rkinfo',  # 전일대비등락률상위
           'ka20001': '/api/dostk/sect',  # 업종현재가 (Naver fallback)
           'ka90001': '/api/dostk/thme'}  # 테마그룹별 (Naver fallback); add read-only TRs here only
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


def _post(path, body, headers=None):
    import requests
    import time

    # shortcut: paces this process only; parallel processes could still exceed 5/s together (add a file lock if they do)
    time.sleep(max(0.0, _last[0] + GAP - time.monotonic()))
    _last[0] = time.monotonic()
    response = requests.post(BASE + path, json=body, timeout=10,
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


def call(api_id, body, list_key, max_pages=5):
    """Rows of `list_key` across continuation pages (cont-yn / next-key); with `list_key` None, the first answer itself.

    Only an authentication failure (return_code 3, '8005 Token이 유효하지 않습니다', checked 2026-11-17) renews the
    shared token, once, and repeats the same page; any other code raises without touching other processes' token."""
    if api_id not in ALLOWED:
        raise KiwoomError(f'{api_id} is not an allowed read-only TR')
    endpoint = f'api.kiwoom.com{ALLOWED[api_id]}#{api_id}'
    token, renewed, rows, cont, pages = _token(), False, [], {}, 0
    while pages < max_pages:
        headers, data = _post(ALLOWED[api_id], body, {'api-id': api_id, 'authorization': 'Bearer ' + token, **cont})
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


def day_bars(code, day, scope=1, adjusted=False, now=None):
    """{'bars': [(datetime KST, o, h, l, c, volume)] oldest first, 'prev_close'} of one KRX regular session (ka10080).

    One page (900 rows) covers the whole day for 1-minute and longer bars. Finished days are cached as files;
    an empty answer (holiday, too old, unknown code) is not cached. `adjusted`: 수정주가 (research), else raw prices
    (fills compare with raw prices)."""
    now = now or datetime.now(KST)
    path = BARS_DIR / f"{code}-{day:%Y%m%d}-{scope}m{'-adj' if adjusted else ''}.json"
    try:
        cached = json.loads(path.read_text())
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
        bars.append((datetime.strptime(t, '%Y%m%d%H%M%S').replace(tzinfo=KST), *values, num(r.get('trde_qty')) or 0))
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
