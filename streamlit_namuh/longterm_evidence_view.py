"""장기투자 '왜 샀나·팔았나' (user request 2026-11-18): the evidence bundle of every virtual-book order (longterm_evidence) as
a filterable list and a pop-up card per order. The summary comes first, then the eight parts, each marked as used by the
order calculation (green) or reference only (grey). Read-only."""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

import streamlit as st

import longterm_evidence as E
import vibe_longterm as L

USED, REF = ':green-badge[주문 계산에 쓰임]', ':gray-badge[참고만 함]'
LINK = re.compile(r'https?://\S+')


def _bundle(row):
    try:
        return json.loads(row['bundle'] or '{}')
    except ValueError:
        return {}


def _masters(row):
    try:
        return json.loads(row['masters'] or '[]')
    except ValueError:
        return []


def reason_label(row):
    return '적립식' if row['kind'] == 'dca' else L.why_label(row['why'])


def master_label(m):
    if m in L.MODULES:
        return f'{L.MODULES[m][0]} · {L.MODULES[m][1]}'
    import longterm_dca as D
    p = D.PROFILES.get(m) or ({'label': '수동'} if str(m).startswith('manual') else {})
    return f"적립 {p.get('label', m)} · {p.get('title', '')}".rstrip(' ·')


def name(key):
    return L.asset_name(key)


def filtered(rows, keys=None, reasons=None, masters=None, start=None, end=None):
    return [r for r in rows if (not keys or r['key'] in keys) and (not reasons or reason_label(r) in reasons)
            and (not masters or set(masters) & set(_masters(r))) and (not start or r['day'] >= start) and (not end or r['day'] <= end)]


def table_rows(rows):
    return [{'날짜': r['day'], '종목': name(r['key']), '구분': E.KIND_LABELS.get(r['kind'], r['kind']),
             '매매': ('매수' if r['side'] == 'BUY' else '매도') + f" {r['qty']:,}주",
             '체결': '—' if not r['number'] else (f"{r['filled']:,}주 @ {r['fill_price']:,.0f}" if r['fill_price'] else '미체결 취소'),
             '사유': reason_label(r), '거장': ', '.join(master_label(m).split(' · ')[0] for m in _masters(r)) or '—',
             '재구성': '예' if r['reconstructed'] else '', '요약': r['summary']} for r in rows]


def _link(text):
    return LINK.sub(lambda m: f'[링크]({m.group(0)})', str(text))


def why(row):
    """The card body: one-sentence conclusion, then the eight parts."""
    b = _bundle(row)
    st.info(row['summary'], icon=':material/lightbulb:')
    if b.get('note'):
        st.warning(b['note'], icon=':material/history:')
    st.caption(f"{row['day']} · {E.KIND_LABELS.get(row['kind'], row['kind'])} · 가상 주문 번호 {row['number'] or '—'} · "
               '초록 = 주문 계산에 쓰임, 회색 = 참고만 함(쓰지 않음)')
    parts = list((b.get('parts') or {}).items())
    for i in range(0, len(parts), 2):
        for col, (key, p) in zip(st.columns(2), parts[i:i + 2]):
            with col.container(border=True):
                st.markdown(f"**{p['title']}** {USED if p['used'] else REF}")
                st.markdown(_link(p.get('text', '')))
                if p.get('how'):
                    st.caption(p['how'])
                if p.get('effect'):
                    st.caption(f"영향: {p['effect']}")
                if key == 'masters' and p.get('steps'):
                    st.dataframe([{'거장': s['label'], '상태': '켜짐' if s['active'] else '꺼짐',
                                   '전': '—' if s['before'] is None else f"{s['before']:.1%}", '후': '—' if s['after'] is None else f"{s['after']:.1%}",
                                   '이유': s['note']} for s in p['steps']], hide_index=True, width='stretch')
                elif p.get('items'):
                    st.markdown('\n'.join(f'- {_link(x)}' for x in p['items'][:8]))
                meta = ' · '.join(x for x in (f"출처: {p['source']}" if p.get('source') else '', f"시각: {p['at']}" if p.get('at') else '') if x)
                if meta:
                    st.caption(meta)


def open_why(row):
    import dialogs
    dialogs.open_dialog(f"왜 {'샀나' if row['side'] == 'BUY' else '팔았나'} · {name(row['key'])}", why, row, icon=':material/manage_search:')


def render(root, key='lt_ev', day=None, asset=None):
    """The evidence list with filters (period, ETF, reason, master) and a picker that opens the card; day / asset narrow it
    (the journal's day, the chart's ETF)."""
    rows = [r for r in E.rows(root) if (not day or r['day'] == day) and (not asset or r['key'] == asset)]
    if not rows:
        st.caption('아직 주문 근거 기록이 없습니다. 가상 주문·계획·적립식이 생기면 주문마다 근거가 남습니다.')
        return
    if not day:
        days = sorted({r['day'] for r in rows})
        a, b = st.columns(2)
        span = a.date_input('기간', (max(date.fromisoformat(days[0]), date.fromisoformat(days[-1]) - timedelta(days=30)), date.fromisoformat(days[-1])),
                            key=f'{key}_span')
        start, end = (span[0].isoformat(), span[-1].isoformat()) if isinstance(span, (tuple, list)) and span else (None, None)
        present = {r['kind'] for r in rows}
        default = [k for k in ('order', 'dca') if k in present] or sorted(present)  # real orders first; dry plans when there are none
        kinds = b.multiselect('구분', list(E.KIND_LABELS), default=default, format_func=E.KIND_LABELS.get, key=f'{key}_kinds')
        rows = [r for r in rows if not kinds or r['kind'] in kinds]
    else:
        start = end = None
    c, d, e = st.columns(3)
    keys = c.multiselect('종목', sorted({r['key'] for r in rows}), format_func=name, key=f'{key}_keys')
    reasons = d.multiselect('사유', sorted({reason_label(r) for r in rows}), key=f'{key}_reasons')
    masters = e.multiselect('거장', sorted({m for r in rows for m in _masters(r)}), format_func=master_label, key=f'{key}_masters')
    shown = filtered(rows, keys, reasons, masters, start, end)
    st.caption(f'{len(shown)}건 · 행을 고르거나 아래에서 골라 "왜 샀나·팔았나"를 엽니다.')
    picked = st.dataframe(table_rows(shown), hide_index=True, width='stretch', on_select='rerun', selection_mode='single-row', key=f'{key}_table')
    chosen = (getattr(picked, 'selection', None) or {}).get('rows') or []
    if chosen and st.session_state.get(f'{key}_last') != (shown[chosen[0]]['id']):
        st.session_state[f'{key}_last'] = shown[chosen[0]]['id']
        open_why(shown[chosen[0]])
    f, g = st.columns([4, 1], vertical_alignment='bottom')
    pick = f.selectbox('근거 볼 주문', range(len(shown)), key=f'{key}_pick',
                       format_func=lambda i: f"{shown[i]['day']} · {name(shown[i]['key'])} · {E.KIND_LABELS.get(shown[i]['kind'])} · {shown[i]['qty']:,}주")
    if g.button('왜 샀나·팔았나', key=f'{key}_open', icon=':material/manage_search:', width='stretch') and pick is not None and shown:
        open_why(shown[pick])
