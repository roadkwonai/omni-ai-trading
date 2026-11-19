"""Compact market snapshot for the overview and shared data labels."""
from datetime import datetime, timedelta
from math import isfinite

from market_context import INDICES, KST, MAX_AGE


def number(value, spec):
    return format(value, spec) if type(value) in (int, float) and isfinite(value) else '—'


def timestamp(value):
    try:
        at = datetime.fromisoformat(value)
        return at.astimezone(KST) if at.tzinfo else None
    except (TypeError, ValueError):
        return None


def breadth(item):
    counts = item.get('breadth') or {}
    up, down = counts.get('상승'), counts.get('하락')
    if any(type(n) not in (int, float) or not isfinite(n) or n < 0 for n in (up, down)):
        return None
    return (up, down) if up + down else None


def index_display(item):
    at = timestamp(item.get('traded_at'))
    history = item.get('history') or []
    if at and at.strftime('%H:%M') < '09:00':
        if history:
            day, close = history[-1]
            return str(day)[:10], close, None
        return '', None, None
    return str(item.get('traded_at') or '')[:10], item.get('close'), item.get('change_pct')


def render_summary(data, now=None):
    import status_cards as sc
    import streamlit as st

    now = now or datetime.now(KST)
    st.markdown('##### 시장 요약')
    if not data:
        st.caption('저장된 시장 자료가 없습니다.')
        return
    indices = data.get('indices') or {}
    cards, counts = [], []
    for key, name in INDICES.items():
        item = indices.get(key) or {}
        regime = (item.get('regime') or {}).get('label', '미확인')
        tone, icon = sc.regime(regime)
        count = breadth(item)
        if count and str(item.get('traded_at') or '')[:10] == now.date().isoformat() and not item.get('reused_from'):
            counts.append(count)
        day, close, delta = index_display(item)
        price, change = number(close, ',.2f'), number(delta, '+.2f')
        day = day or '기준일 미확인'
        cards.append(dict(label=name + ' 일봉 국면', value=regime, tone=tone, icon=icon,
                          sub=f"{price} · {change + '%' if change != '—' else change} · {day}"))
    if len(counts) == len(INDICES):
        up, down = (sum(side) for side in zip(*counts))
        value, tone = f'{up:,.0f} / {down:,.0f}', sc.signed(up - down)
    else:
        value, tone = '미집계', 'off'
    cards.append(dict(label='오늘 상승 / 하락 종목', value=value, tone=tone, icon='stacked_bar_chart'))
    at = timestamp(data.get('time'))
    fresh = at is not None and timedelta(0) <= now - at <= MAX_AGE
    cards.append(dict(label='시장 자료 수집', value=f'{at:%H:%M}' if at else '미확인', icon='update',
                      tone='ok' if fresh else 'warn' if at else 'off',
                      sub=f"{at:%m/%d} KST · {'최신 수집' if fresh else '갱신 확인 필요'}" if at else None))
    sc.row(cards)
