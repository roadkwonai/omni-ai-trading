"""KIS market data for local analytics, without an account number or order routes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

import api_calls
import broker_token
from config_store import write_json

HOSTS = {'live': 'https://openapi.koreainvestment.com:9443', 'mock': 'https://openapivts.koreainvestment.com:29443'}
ROUTES = {
    'quote': ('inquire-price', 'FHKST01010100'),
    'daily': ('inquire-daily-itemchartprice', 'FHKST03010100'),
    'minute': ('inquire-time-dailychartprice', 'FHKST03010230'),
    'news': ('news-title', 'FHKST01011800'),
}
ROOT = Path(os.environ.get('VIBE_TRADING_HOME') or Path.home() / '.vibe-trading').expanduser() / 'kis'
KST = broker_token.KST
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST = {}


class KISError(RuntimeError):
    pass


def _keys(mode):
    from dotenv import dotenv_values
    from local_env import ENV

    prefix = {'live': 'KIS_', 'mock': 'KIS_MOCK_'}[mode]
    env = dotenv_values(ENV, interpolate=False)
    return tuple((env.get(prefix + name) or '').strip() for name in ('APP_KEY', 'APP_SECRET'))


def configured(mode='live'):
    return all(_keys(mode))


def _request(method, url, **kwargs):
    try:
        # Pace this process, including token issuance, before making another call to the same environment.
        host = url.split('/', 3)[2]
        with _REQUEST_LOCK:
            time.sleep(max(0, _LAST_REQUEST.get(host, 0) + 0.6 - time.monotonic()))
            _LAST_REQUEST[host] = time.monotonic()
        response = requests.request(method, url, allow_redirects=False, timeout=(3, 5), **kwargs)
    except requests.RequestException as exc:
        raise KISError(f'KIS {type(exc).__name__}') from None
    try:
        data = response.json()
    except ValueError:
        data = None
    return response.status_code, data if isinstance(data, dict) else {}


def _error(status, data):
    code = data.get('msg_cd') or data.get('error_code')
    suffix = f' {code}' if isinstance(code, str) and re.fullmatch(r'EG[WAM][0-9]{5}|[A-Z]{3}[0-9]{5}', code) else ''
    return f'KIS HTTP {status}{suffix}'


def _token(mode, key, secret, stale=None):
    fingerprint = hashlib.sha256((key + '\0' + secret).encode()).hexdigest()

    def issue():
        status, data = _request('POST', HOSTS[mode] + '/oauth2/tokenP', json={
            'grant_type': 'client_credentials', 'appkey': key, 'appsecret': secret,
        })
        value = data.get('access_token')
        try:
            ttl = int(data.get('expires_in') or 0)
        except (ValueError, TypeError):
            ttl = 0
        if status != 200 or not isinstance(value, str) or not value or not 0 < ttl <= 86400:
            raise KISError('token: ' + _error(status, data))
        return value, datetime.now(KST) + timedelta(seconds=ttl)

    try:
        return broker_token.token(ROOT / f'{mode}-{fingerprint}.json', issue, stale)
    except OSError as exc:
        raise KISError(f'KIS token cache: {type(exc).__name__}') from None


def _require_code(code):
    if not isinstance(code, str) or not re.fullmatch(r'[0-9]{6}', code):
        raise ValueError('expected a six-digit KRX stock code')


def _get(kind, code, *, mode, **params):
    if kind != 'news' or code:
        _require_code(code)
    endpoint, tr = ROUTES[kind]
    if kind == 'minute' and mode != 'live':
        raise ValueError('historical KIS minute bars require live market-data credentials')
    key, secret = _keys(mode)
    if not (key and secret):
        raise KISError(f'KIS {mode} credentials missing')
    url = HOSTS[mode] + '/uapi/domestic-stock/v1/quotations/' + endpoint
    token = _token(mode, key, secret)
    for attempt in range(2):
        status, data = _request('GET', url, headers={
            'authorization': 'Bearer ' + token, 'appkey': key, 'appsecret': secret, 'tr_id': tr, 'custtype': 'P',
        }, params={'FID_INPUT_ISCD': code, **({'FID_COND_MRKT_DIV_CODE': 'J'} if kind != 'news' else {}), **params})
        if status == 200 and str(data.get('rt_cd')) == '0':
            return data
        detail = _error(status, data)
        if status == 200:
            api_calls.problem('kis', api_calls.endpoint_of(url), detail)
        if attempt:
            raise KISError(detail)
        if status == 429 or data.get('msg_cd') == 'EGW00201':
            time.sleep(1)
        elif data.get('msg_cd') == 'EGW00123':
            token = _token(mode, key, secret, stale=token)
        else:
            raise KISError(detail)


def _row_int(row, key):
    value = row.get(key)
    if not re.fullmatch(r'[0-9]+', str(value)):
        raise KISError(f'invalid KIS field: {key}')
    return int(value)


def news_titles(code, now):
    """KIS domestic news/disclosure headline feed, with an optional stock-code filter."""
    data = _get('news', code, mode='live', FID_NEWS_OFER_ENTP_CODE='', FID_COND_MRKT_CLS_CODE='',
                FID_TITL_CNTT='', FID_INPUT_DATE_1='', FID_INPUT_HOUR_1='',
                FID_RANK_SORT_CLS_CODE='', FID_INPUT_SRNO='')
    rows = data.get('output')
    if not isinstance(rows, list):
        raise KISError('KIS news list missing')
    out = []
    from etf_news import RESTRICTED_PUBLISHERS
    for row in rows:
        if not isinstance(row, dict) or (code and code not in [row.get(f'iscd{i}') for i in range(1, 6)]):
            continue
        try:
            at = datetime.strptime(str(row.get('data_dt')) + str(row.get('data_tm')), '%Y%m%d%H%M%S').replace(tzinfo=KST)
        except ValueError:
            continue
        title, publisher = str(row.get('hts_pbnt_titl_cntt') or '').strip(), str(row.get('dorg') or '한국투자증권')
        if title and timedelta(0) <= now - at <= timedelta(hours=24) and not any(
                p in f'{title} {publisher}'.casefold() for p in RESTRICTED_PUBLISHERS):
            out.append({'title': title[:200], 'published': at.isoformat(), 'publisher': publisher[:80]})
    return out


def quote(code, *, mode='live'):
    """Snapshot for display or inspection. KIS current-price REST does not prove freshness."""
    row = _get('quote', code, mode=mode).get('output')
    if not isinstance(row, dict) or row.get('stck_shrn_iscd') != code:
        raise KISError('KIS quote symbol mismatch')
    price = _row_int(row, 'stck_prpr')
    if price <= 0:
        raise KISError('invalid KIS quote price')
    return {'code': code, 'price': price, 'market': 'KRX', 'source': 'kis', 'mode': mode,
            'retrieved_at': datetime.now(KST).isoformat(timespec='seconds')}


def daily_bars(code, start, end, *, mode='live', adjusted=False):
    """A single KIS page, up to 100 KRX daily bars, oldest first."""
    if start > end or (end - start).days >= 100:
        raise ValueError('daily date range must span at most 100 calendar days')
    data = _get('daily', code, mode=mode, FID_INPUT_DATE_1=f'{start:%Y%m%d}', FID_INPUT_DATE_2=f'{end:%Y%m%d}',
                FID_PERIOD_DIV_CODE='D', FID_ORG_ADJ_PRC='0' if adjusted else '1')
    rows = data.get('output2')
    if not isinstance(rows, list):
        raise KISError('KIS daily bars missing')
    bars = []
    for row in rows:
        if not isinstance(row, dict) or not row.get('stck_bsop_date'):
            continue
        day = _parse_time(row['stck_bsop_date'], '%Y%m%d').date()
        if not start <= day <= end:
            raise KISError('KIS daily bar outside requested dates')
        values = _ohlcv(row, 'acml_vol', close='stck_clpr')
        bars.append((day, *values))
    if len({bar[0] for bar in bars}) != len(bars):
        raise KISError('duplicate KIS daily bars')
    return sorted(bars)


def _parse_time(value, fmt):
    try:
        return datetime.strptime(value, fmt).replace(tzinfo=KST)
    except (ValueError, TypeError):
        raise KISError('invalid KIS bar timestamp') from None


def _ohlcv(row, volume, *, close='stck_prpr'):
    o, h, low, c, v = (_row_int(row, key) for key in ('stck_oprc', 'stck_hgpr', 'stck_lwpr', close, volume))
    if not (0 < low <= min(o, c) <= max(o, c) <= h):
        raise KISError('invalid KIS bar prices')
    return o, h, low, c, v


def day_bars(code, day):
    """Completed KRX regular-session raw minute bars for the trade journal."""
    from session_times import shifted

    _require_code(code)
    opens = _parse_time(f'{day:%Y%m%d}' + shifted('09:00', day).replace(':', '') + '00', '%Y%m%d%H%M%S')
    closes = opens + timedelta(hours=6, minutes=30)
    now = datetime.now(KST)
    if now < closes or day.weekday() >= 5:
        return {'bars': [], 'prev_close': None}
    cache = ROOT / 'minute' / f'{code}-{day:%Y%m%d}.json'
    try:
        saved = json.loads(cache.read_text(encoding='utf-8'))
        age = now - datetime.fromisoformat(saved['fetched_at'])
        if saved['day'] == day.isoformat() and timedelta(0) <= age < timedelta(days=1):
            cached_bars = [(datetime.fromisoformat(stamp).astimezone(KST), o, h, low, c, v)
                           for stamp, o, h, low, c, v in saved['bars']]
            if (len(cached_bars) >= 300 and saved['prev_close'] > 0
                    and opens <= cached_bars[0][0] <= opens + timedelta(minutes=1)
                    and closes - timedelta(minutes=1) <= cached_bars[-1][0] <= closes
                    and all(bar[0].date() == day for bar in cached_bars)):
                return {'bars': cached_bars, 'prev_close': saved['prev_close']}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    bars, cursor = {}, closes
    for _ in range(4):  # 120 bars per call, 391 timestamps at most
        data = _get('minute', code, mode='live', FID_INPUT_DATE_1=f'{day:%Y%m%d}', FID_INPUT_HOUR_1=f'{cursor:%H%M%S}',
                    FID_PW_DATA_INCU_YN='N', FID_FAKE_TICK_INCU_YN='N')
        rows = data.get('output2')
        if not isinstance(rows, list):
            raise KISError('KIS minute bars missing')
        page = []
        for row in rows:
            if not isinstance(row, dict) or row.get('stck_bsop_date') != f'{day:%Y%m%d}':
                continue
            stamp = _parse_time(row['stck_bsop_date'] + str(row.get('stck_cntg_hour')), '%Y%m%d%H%M%S')
            if opens <= stamp <= cursor:
                bars[stamp] = (stamp, *_ohlcv(row, 'cntg_vol'))
                page.append(stamp)
        if not page:
            break
        cursor = min(page) - timedelta(seconds=1)
        if cursor < opens:
            break
    # A partial session does not replace a journal chart. Late listings/halted issues can fail this check.
    if len(bars) < 300 or min(bars) > opens + timedelta(minutes=1) or max(bars) < closes - timedelta(minutes=1):
        return {'bars': [], 'prev_close': None}
    previous = daily_bars(code, day - timedelta(days=14), day - timedelta(days=1))
    if not previous:
        return {'bars': [], 'prev_close': None}
    out = {'bars': sorted(bars.values()), 'prev_close': previous[-1][4]}
    if now >= closes + timedelta(minutes=10):
        try:
            write_json(cache, {'day': day.isoformat(), 'fetched_at': now.isoformat(), 'prev_close': out['prev_close'],
                               'bars': [[stamp.isoformat(), *values] for stamp, *values in out['bars']]}, indent=None)
        except OSError:
            pass  # The chart is still usable if the local cache cannot be updated.
    return out


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('code')
    parser.add_argument('--mode', choices=HOSTS, default='live')
    parser.add_argument('--daily', metavar=('START', 'END'), nargs=2, type=date.fromisoformat)
    args = parser.parse_args()
    try:
        if args.daily:
            bars = daily_bars(args.code, *args.daily, mode=args.mode)
            print(json.dumps({'source': 'kis', 'mode': args.mode, 'market': 'KRX', 'adjusted': False,
                              'bars': [[day.isoformat(), *values] for day, *values in bars]}))
        else:
            print(json.dumps(quote(args.code, mode=args.mode)))
    except (KISError, ValueError) as exc:
        parser.exit(1, str(exc) + '\n')
