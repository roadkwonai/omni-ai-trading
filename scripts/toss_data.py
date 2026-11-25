"""Read-only Toss Securities Open API market data (https://developers.tossinvest.com). Never orders.

Only GET requests under `ALLOWED` path prefixes are made, without the account header, so account, asset and order
APIs cannot be reached. Toss keeps ONE valid token per client and a new issue revokes the previous one, so every
process shares one locked 0600 token file (broker_token); a revoked token is replaced once. Never printed.
Toss data policy (FAQ 데이터 이용 정책): for the investor's own trading only; no distribution to third parties.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import broker_token
import api_calls  # noqa: F401,E402 - counts this process's external API calls (dashboard 'API 연동')

KST = ZoneInfo('Asia/Seoul')
BASE = 'https://openapi.tossinvest.com'
TOKEN_FILE = Path.home() / '.vibe-trading/toss/token.json'
ALLOWED = ('/api/v1/stocks', '/api/v1/market-indicators', '/api/v1/rankings',  # stock master, index/bond quotes, rankings
           '/api/v1/candles',  # stock 1-minute candles incl. the NXT pre/after-market (2026-11-19)
           '/api/v1/sectors')  # sector list and members (theme slot membership, 2026-11-19); all read-only
GAP = 0.25  # seconds between requests (most groups allow 5+ per second)
GAP_SLOW = {'/api/v1/stocks/all': 1.0}  # STOCK_ALL allows 1 per second
_last = [0.0]


class TossError(RuntimeError):
    pass


def _keys():
    from dotenv import dotenv_values
    from local_env import ENV

    values = dotenv_values(ENV, interpolate=False)
    return values.get('TOSS_CLIENT_ID') or '', values.get('TOSS_CLIENT_SECRET') or ''


def configured():
    return all(_keys())


def _token(stale=None):
    """The shared token (broker_token: one locked file, so a reissue by one process cannot race another's)."""
    def issue():
        import requests

        client_id, secret = _keys()
        if not (client_id and secret):
            raise TossError('TOSS_CLIENT_ID / TOSS_CLIENT_SECRET missing')
        response = requests.post(BASE + '/oauth2/token', timeout=10, data={
            'grant_type': 'client_credentials', 'client_id': client_id, 'client_secret': secret})
        if response.status_code != 200:
            raise TossError(f'token: HTTP {response.status_code}')  # never the body: it may echo input
        data = response.json()
        return data['access_token'], datetime.now(KST) + timedelta(seconds=int(data.get('expires_in') or 0))
    return broker_token.token(TOKEN_FILE, issue, stale)


def _retry_after(response):
    try:
        return max(0.0, float(response.headers.get('Retry-After') or 1))
    except (TypeError, ValueError):
        return 1.0


def get(path, params=None):
    """`result` of a GET under an allowed prefix; a 401 (revoked by another issue, or expired) or a short 429 retries once."""
    import requests

    if not path.startswith(ALLOWED):
        raise TossError(f'{path} is not an allowed read-only path')
    token = _token()
    for attempt in range(2):
        # shortcut: paces this process only; parallel processes could exceed the group limit together (429 then)
        gap = next((g for prefix, g in GAP_SLOW.items() if path.startswith(prefix)), GAP)
        time.sleep(max(0.0, _last[0] + gap - time.monotonic()))
        _last[0] = time.monotonic()
        response = requests.get(BASE + path, params=params, timeout=10, headers={'Authorization': 'Bearer ' + token})
        if response.status_code == 401 and attempt == 0:
            token = _token(stale=token)
            continue
        if response.status_code == 429 and attempt == 0 and (wait := _retry_after(response)) <= 3:
            time.sleep(wait)
            continue
        if response.status_code != 200:
            raise TossError(f'{path}: HTTP {response.status_code}')
        return response.json().get('result')
    raise TossError(f'{path}: unauthorized')


def _common_stocks(symbols, keep):
    rows = []
    for i in range(0, len(symbols), 200):
        for s in get('/api/v1/stocks', {'symbols': ','.join(symbols[i:i + 200])}) or []:
            listed = str(s.get('listDate') or '').replace('-', '')
            if (s.get('securityType') in ('STOCK', 'FOREIGN_STOCK') and s.get('isCommonShare') and len(listed) == 8
                    and '스팩' not in str(s.get('name')) and keep(listed)):
                rows.append({'name': s.get('name'), 'code': s['symbol'], 'listed': listed, 'market': s.get('market')})
    return rows


def recent_listings(now=None, days=14):
    """[{'name', 'code', 'listed': YYYYMMDD, 'market'}] of KOSPI/KOSDAQ common stocks (no SPACs) listed in the last `days`
    days up to today, newest first: new_listings.recent()'s fallback when KIND fails (about 16 requests: every active
    common stock's master record). `listed` is Toss's date; the runner still confirms it with NH."""
    now = now or datetime.now(KST)
    symbols = []
    for market in ('KOSPI', 'KOSDAQ'):
        symbols += [r['symbol'] for r in get('/api/v1/stocks/all', {'market': market, 'status': 'ACTIVE', 'securityType': 'STOCK',
                                                                   'commonShare': 'true'}) or []]
    start, today = (now - timedelta(days=days)).strftime('%Y%m%d'), now.strftime('%Y%m%d')
    rows = _common_stocks(sorted(set(symbols)), lambda listed: start <= listed <= today)
    return sorted(rows, key=lambda r: (r['listed'], r['code']), reverse=True)


def scheduled_listings(now=None):
    """[{'name', 'code', 'listed': YYYYMMDD, 'market'}] of KOSPI/KOSDAQ common stocks Toss lists as SCHEDULED, by date.

    SPACs and rows without a listing date are skipped; `listed` is Toss's date, not an exchange confirmation."""
    now = now or datetime.now(KST)
    symbols = []
    for market in ('KOSPI', 'KOSDAQ'):
        symbols += [row['symbol'] for row in get('/api/v1/stocks/all', {'market': market, 'status': 'SCHEDULED',
                                                                       'commonShare': 'true'}) or []]
    rows = _common_stocks(symbols, lambda listed: listed >= now.strftime('%Y%m%d'))
    return sorted(rows, key=lambda r: (r['listed'], r['code']))


def _n(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct(value):
    return None if _n(value) is None else round(_n(value) * 100, 3)


def stock_pressure(code, day):
    """Short selling, credit (margin loans / stock loans) and securities lending of one KR code as of `day` (YYYY-MM-DD):
    the latest record on or before it, with the balance change from the record before. Toss's timeliness: short
    selling and lending final that evening, credit the next business day's early morning (T+1). A failed part is
    {'error': name}; a part with no record is None."""
    def records(kind):
        rows = (get(f'/api/v1/stocks/{code}/{kind}', {'count': '5', 'until': day}) or {}).get('records') or []
        return [r for r in rows if str(r.get('date')) <= day][:2]

    def part(kind, shape):
        try:
            rows = records(kind)
        except Exception as exc:  # one part failing must not hide the others
            return {'error': type(exc).__name__}
        return shape(rows[0], rows[1] if len(rows) > 1 else None) if rows else None

    def change(now, before):
        return None if now is None or before is None else now - before

    short = lambda r, _: {'date': r['date'], 'updated_at': r.get('updatedAt'), 'volume': _n(r.get('shortSellingVolume')),  # noqa: E731
                          'amount_100m': None if _n(r.get('shortSellingAmount')) is None else round(_n(r['shortSellingAmount']) / 1e8, 1),
                          'volume_rate_pct': _pct(r.get('shortSellingVolumeRate')), 'amount_rate_pct': _pct(r.get('shortSellingAmountRate'))}

    def credit(r, prev):
        margin, before, loan = r.get('marginLoan') or {}, (prev or {}).get('marginLoan') or {}, r.get('stockLoan') or {}
        return {'date': r['date'], 'updated_at': r.get('updatedAt'), 'margin_balance': _n(margin.get('balanceQuantity')),
                'margin_balance_change': change(_n(margin.get('balanceQuantity')), _n(before.get('balanceQuantity'))),
                'margin_balance_rate_pct': _pct(margin.get('balanceRate')), 'margin_trading_rate_pct': _pct(margin.get('tradingRate')),
                'stock_loan_balance': _n(loan.get('balanceQuantity'))}

    def lending(r, prev):
        return {'date': r['date'], 'updated_at': r.get('updatedAt'), 'balance': _n(r.get('balanceQuantity')),
                'balance_change': change(_n(r.get('balanceQuantity')), _n((prev or {}).get('balanceQuantity'))),
                'balance_amount_100m': None if _n(r.get('balanceAmount')) is None else round(_n(r['balanceAmount']) / 1e8, 1)}

    return {'short': part('short-selling', short), 'credit': part('credit-trades', credit),
            'lending': part('securities-lending', lending), 'source': 'Toss Open API'}


def stock_minutes(code, count=120):
    """The newest `count` 1-minute candles of one stock, oldest first, as [(datetime KST, o, h, l, c, volume)]. Toss
    merges KRX and NXT, so the NXT pre-market (08:00~08:50) and after-market bars are included; a bar is stamped with
    its end minute (08:01 = 08:00~08:01). Malformed candles are skipped."""
    page = get('/api/v1/candles', {'symbol': code, 'interval': '1m', 'count': str(count)}) or {}
    bars = []
    for c in page.get('candles') or []:
        try:
            at = datetime.fromisoformat(c['timestamp']).astimezone(KST)
        except (KeyError, TypeError, ValueError):
            continue
        values = [_n(c.get(k)) for k in ('openPrice', 'highPrice', 'lowPrice', 'closePrice')]
        if None not in values:
            bars.append((at, *values, _n(c.get('volume')) or 0))
    return sorted(bars, key=lambda b: b[0])


def index_minutes(symbol, now=None, max_pages=3):
    """Today's 1-minute bars of KOSPI or KOSDAQ (Toss market indicators), oldest first, as
    {'bars': [(datetime KST, o, h, l, c, volume)], 'prev_close'}; the previous session's last close comes from the
    first earlier bar in the pages. Toss stamps a bar with its end minute (09:01 = 09:00~09:01), and the newest bar may
    still be forming (seen 11:14:52 -> an 11:15 bar)."""
    now = now or datetime.now(KST)
    today, bars, prev_close, before = now.date(), [], None, None
    for _ in range(max_pages):
        page = get(f'/api/v1/market-indicators/{symbol}/candles', {'interval': '1m', 'count': '200',
                                                                  **({'before': before} if before else {})}) or {}
        for c in page.get('candles') or []:  # newest first
            at = datetime.fromisoformat(c['timestamp']).astimezone(KST)
            values = [_n(c.get(k)) for k in ('openPrice', 'highPrice', 'lowPrice', 'closePrice')]
            if None in values:
                continue
            if at.date() == today:
                bars.append((at, *values, _n(c.get('volume')) or 0))
            elif at.date() < today:
                prev_close = values[3]
                break
        before = page.get('nextBefore')
        if prev_close is not None or not before:
            break
    return {'bars': sorted(bars, key=lambda b: b[0]), 'prev_close': prev_close}



INSTITUTION_KINDS = {'financialInvestment': '금융투자', 'insurance': '보험', 'trust': '투신', 'privateEquityFund': '사모',
                     'bank': '은행', 'otherFinancialInstitution': '기타금융', 'pensionFund': '연기금'}


def market_investors(symbol, day):
    """KOSPI/KOSDAQ net buying by investor on `day` (YYYY-MM-DD) from Toss (KRX investor trading value), in 100 million
    KRW: 개인·외국인 (registered and unregistered)·기관·기타법인 and the institution's seven kinds. None when Toss has no
    record for that day. The day's record is provisional until the close (`updated_at` says when it was last updated)."""
    rows = (get(f'/api/v1/market-indicators/{symbol}/investor-trading', {'interval': '1d', 'count': '3', 'until': day}) or {}).get('records') or []
    row = next((r for r in rows if r.get('date') == day), None)
    if not row:
        return None

    def net(item):
        buy, sell = _n((item or {}).get('buyAmount')), _n((item or {}).get('sellAmount'))
        return None if buy is None or sell is None else round((buy - sell) / 1e8, 1)
    institution = row.get('institution') or {}
    return {'date': row['date'], 'updated_at': row.get('updatedAt'),
            **{name: net(row.get(key)) for key, name in (('individual', '개인'), ('foreigner', '외국인'),
                                                         ('institution', '기관'), ('otherCorporation', '기타법인'))},
            'institution_detail': {name: net((institution.get('breakdown') or {}).get(key)) for key, name in INSTITUTION_KINDS.items()}}


INVESTORS = {'foreigner': '외국인', 'institution': '기관', 'individual': '개인'}


def investor_rankings(count=20, now=None):
    """Toss's KR net-buy and net-sell leaders by investor (KRX+NXT, ETFs included, latest business day only), in 100
    million KRW with names: {'외국인'|'기관'|'개인': {'ranked_at', 'net_buy': [{'code', 'name', 'net_100m'}], 'net_sell'}}.
    Foreigners and institutions are provisional during the session; individuals appear after the regular close (before it
    they are the previous business day) - check `ranked_at`. Net sells are negative."""
    data = get('/api/v1/rankings/investor-trading', {'count': str(count)}) or {}
    out, codes = {}, set()
    for key, label in INVESTORS.items():
        side = data.get(key) or {}
        rows = {'ranked_at': side.get('rankedAt'),
                'net_buy': [{'code': r['symbol'], 'net_100m': None if _n(r.get('netBuyAmount')) is None else round(_n(r['netBuyAmount']) / 1e8, 1)}
                            for r in side.get('netBuyRankings') or [] if r.get('symbol')],
                'net_sell': [{'code': r['symbol'], 'net_100m': None if _n(r.get('netSellAmount')) is None else -round(_n(r['netSellAmount']) / 1e8, 1)}
                             for r in side.get('netSellRankings') or [] if r.get('symbol')]}
        codes |= {r['code'] for r in rows['net_buy'] + rows['net_sell']}
        out[label] = rows
    names = {}
    codes = sorted(codes)
    for i in range(0, len(codes), 200):
        names.update({s['symbol']: s.get('name') for s in get('/api/v1/stocks', {'symbols': ','.join(codes[i:i + 200])}) or []})
    for rows in out.values():
        for r in rows['net_buy'] + rows['net_sell']:
            r['name'] = names.get(r['code'])
    return out

