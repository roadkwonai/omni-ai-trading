"""테마 묶음 구성종목과 장중 수치 (user 2026-11-19, docs/api-selection.md): the sources the API bench ranked for the
주도 테마 동반주 slot. The ranked choice is daily_members() (Toss sectors ∪ Naver themes, once a day) with
kiwoom_quotes() (Kiwoom ka10095, up to 100 codes a call); naver_members() is the fallback for the live figures.
Read-only."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from market_context import group_of  # noqa: F401 - one keyword rule for every theme list (Naver, Kiwoom, Toss)

KST = ZoneInfo('Asia/Seoul')
LIST_RETRY = timedelta(minutes=30)  # a list that failed is asked again after this (11-19 review: 18 Naver calls a round)
_NAVER_THEMES = {}  # day -> [(theme no, name)]: Naver's theme list, read once a day


def now():
    return datetime.now(KST)


def naver_themes():
    """[(no, name)] of Naver's theme list, once a day (three pages); not kept when the read fails."""
    import market_context as mc
    day = now().date()
    if day not in _NAVER_THEMES:
        listed = [(t['no'], t['name']) for t in mc.themes()]
        if not listed:  # r5: an empty answer kept all day made daily_members count Naver as answered and never ask again
            raise ValueError('naver theme list empty')
        _NAVER_THEMES.clear()
        _NAVER_THEMES[day] = listed
    return _NAVER_THEMES[day]


# ---- need: theme_members — a theme group's universe members with today's change and trading value ----------------

def naver_members(group, universe):
    import market_context as mc
    calls, rows = 0, {}  # the theme list itself is read once a day (naver_themes)
    nos = [no for no, name in naver_themes() if group_of(name) == group]
    for no in nos:
        calls += 1
        for s in mc.naver(f'stocks/theme/{no}?page=1&pageSize=100').get('stocks') or []:
            code = s.get('itemCode')
            if code in universe:
                rows[code] = {'change_pct': mc.num(s.get('fluctuationsRatio')), 'value_krw': mc.num(s.get('accumulatedTradingValueRaw')),
                              'as_of': s.get('localTradedAt')}
    return rows, calls


def kiwoom_members(group, universe):
    import kiwoom_data as k
    from market_context import num
    themes = k.call('ka90001', {'qry_tp': '0', 'stk_cd': '', 'date_tp': '1', 'thema_nm': '', 'flu_pl_amt_tp': '3', 'stex_tp': '1'},
                    'thema_grp', max_pages=10)
    calls, rows = max(1, -(-len(themes) // 100)), {}
    for theme in themes:
        if group_of(theme.get('thema_nm') or '') != group:
            continue
        calls += 1
        answer = k.call('ka90002', {'date_tp': '1', 'thema_grp_cd': theme['thema_grp_cd'], 'stex_tp': '1'}, None)
        for s in answer.get('thema_comp_stk') or []:
            code = s.get('stk_cd')
            price, qty = num(s.get('cur_prc')), num(s.get('acc_trde_qty'))
            if code in universe:
                rows[code] = {'change_pct': num(s.get('flu_rt')), 'value_krw': abs(price) * qty if price and qty else None,
                              'as_of': None}  # ka90002 carries no time; the answer is the moment of the call
    return rows, calls


def toss_members(group, universe):
    import toss_data as t
    calls, rows = 1, {}
    sectors = t.get('/api/v1/sectors')['sectors']
    for sector in sectors:
        if group_of(sector['name']) != group:
            continue
        calls += 1
        for s in (t.get(f"/api/v1/sectors/{sector['sectorId']}/stocks", {'marketCountry': 'KR'}) or {}).get('stocks') or []:
            if s.get('symbol') in universe:
                rows[s['symbol']] = {'change_pct': None, 'value_krw': float(s['tradingAmount']) if s.get('tradingAmount') else None,
                                     'as_of': None}  # no change rate: /prices has no previous close, /rankings only the top 100
    return rows, calls


_LISTS = {}  # (day, group) -> (codes, complete, when)


def daily_members(group, universe):
    """(codes, calls): the group's universe members from Toss sectors and Naver themes. Kept for the day once both
    answered; when one failed, what did answer is kept and the lists are asked again after LIST_RETRY (membership barely
    moves intraday, and a failing source must not be asked every round)."""
    at = now()
    key = (at.date(), group)
    kept = _LISTS.get(key)
    if kept and (kept[1] or at - kept[2] < LIST_RETRY):
        return kept[0], 0
    codes, calls, complete = set(), 0, True
    for fn in (toss_members, naver_members):
        try:
            rows, n = fn(group, universe)
            codes |= set(rows)
            calls += n
        except Exception:  # noqa: BLE001 - the other list stands
            complete = False
    for old in [k for k in _LISTS if k[0] != key[0]]:  # yesterday's lists go
        del _LISTS[old]
    _LISTS[key] = (sorted(codes), complete, at)
    return _LISTS[key][0], calls


def kiwoom_quotes(codes):
    """{code: {change_pct, value_krw, as_of}} from Kiwoom ka10095 (관심종목정보, up to 100 codes a call)."""
    import kiwoom_data as k
    from market_context import num
    out = {}
    for i in range(0, len(codes), 100):
        for s in k.call('ka10095', {'stk_cd': '|'.join(codes[i:i + 100])}, 'atn_stk_infr', max_pages=1):
            value = num(s.get('trde_prica'))  # 백만원
            code = str(s.get('stk_cd') or '').split('_')[0]  # '090430_AL' (KRX+NXT) -> '090430'
            out[code] = {'change_pct': num(s.get('flu_rt')), 'value_krw': value * 1e6 if value is not None else None,
                         'as_of': f"{s.get('dt')} {s.get('cntr_tm')}" if s.get('cntr_tm') else None}
    return out, -(-len(codes) // 100)


def lists_kiwoom_members(group, universe):
    codes, _ = daily_members(group, universe)  # daily calls are not this answer's
    return kiwoom_quotes(codes)
