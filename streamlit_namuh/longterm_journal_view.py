"""매매일지 → 장기투자 (user 2026-11-17, docs/longterm-live-account-plan.md 3): the masters' macro view of the market,
the long-term trade journal (engine decisions, NAV of the virtual book and its shadows, the long-term accounts' fills)
and the day's strategy report. Read live from the engine's folder, so a revised report shows at once."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import streamlit as st

from report_figures import for_streamlit  # report callouts/highlights in st.markdown

import vibe_longterm as L


def report_keys(day):
    """The report keys that cover a day: the day, its ISO week and its month."""
    d = date.fromisoformat(day)
    year, week, _ = d.isocalendar()
    return [day, f'{year}-W{week:02d}', day[:7]]


def read_report(key, folder):
    """(md text, json dict) of one strategy report, or None."""
    from longterm_report import paths
    md, js = paths(key, folder)
    if not md.exists():
        return None
    try:
        doc = json.loads(js.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        doc = {}
    return md.read_text(encoding='utf-8'), doc if isinstance(doc, dict) else {}


def _rows(root, sql, args):
    path = Path(root) / 'longterm.sqlite3'
    if not path.exists():
        return []
    from sqlite_ro import ro_connect
    with ro_connect(path) as db:
        return db.execute(sql, args).fetchall()


def day_data(day, root=L.ROOT):
    """{reports: {key: (md, json)}, decisions: [...], nav: {book: (value, previous)}, contributed, contributed_before, macro}
    of one day; contributed is the 적립식 money paid in by that day (and by the day before), not a book."""
    folder = Path(root) / 'reports'
    reports = {k: r for k in report_keys(day) if (r := read_report(k, folder))}
    cols = ('time', 'kind', 'regime', 'mode', 'modules', 'reason')
    decisions = [dict(zip(cols, r)) for r in _rows(root, f"SELECT {', '.join(cols)} FROM decisions WHERE day=? ORDER BY rowid", (day,))]
    nav = {}
    for book, value, previous in _rows(root, """SELECT book, value, (SELECT value FROM nav p WHERE p.book=n.book AND p.day<n.day
                                                 ORDER BY p.day DESC LIMIT 1) FROM nav n WHERE day=?""", (day,)):
        nav[book] = (value, previous)
    paid, paid_before = nav.pop(L.CONTRIBUTED, (0, 0))
    found = _rows(root, "SELECT indicators FROM decisions WHERE day=? AND kind='macro' ORDER BY rowid DESC LIMIT 1", (day,))
    return {'reports': reports, 'decisions': decisions, 'nav': nav, 'contributed': paid, 'contributed_before': paid_before or 0,
            'macro': json.loads(found[0][0]) if found and found[0][0] else None}


def day_change(data, book):
    """A book's NAV change from its previous record less the 적립식 money paid in between (None without a previous one)."""
    value, previous = data['nav'][book]
    return None if not previous else value - previous - (data['contributed'] - data['contributed_before'])


def period_data(start, end, root=L.ROOT):
    """{nav: [(day, book, value)], contributed: {day: paid in by then}, regimes: [(day, regime)] changes only,
    reports: [key]} between two days."""
    from vibe_longterm_view import nav_history
    nav, contributed = nav_history(root, start, end)
    regimes, last = [], None
    for day, regime in _rows(root, "SELECT day, regime FROM decisions WHERE day BETWEEN ? AND ? AND regime IS NOT NULL ORDER BY rowid", (start, end)):
        if regime != last:
            regimes.append((day, regime))
            last = regime
    folder = Path(root) / 'reports'
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    keys = {k for n in range((last - first).days + 1) for k in report_keys((first + timedelta(days=n)).isoformat())[1:]}
    return {'nav': nav, 'contributed': contributed, 'regimes': regimes,
            'reports': sorted(k for k in keys if (folder / f'strategy-{k}.md').exists())}


def _modules(text):
    try:
        keys = json.loads(text) if text else []
    except ValueError:
        return str(text)
    return ', '.join(L.MODULES[k][0] for k in keys if k in L.MODULES) if isinstance(keys, list) else ''


def masters_view(md, doc):
    """거장의 거시 시황: the report's macro section, its conclusion and one card per master."""
    import longterm_template as T
    if doc.get('summary'):
        st.info(doc['summary'], icon=':material/lightbulb:')
    macro = (doc.get('sections') or {}).get('거시 시황') or T.section_body(md, '거시 시황')
    if macro:
        st.markdown('##### 거시 시황')
        st.markdown(for_streamlit(macro))
    masters = doc.get('masters') or {}
    if masters:
        st.markdown('##### 거장별 시각')
        cols = st.columns(2)
        for i, (key, text) in enumerate(masters.items()):
            name, title = (L.MODULES.get(key) or (key, ''))[:2]
            with cols[i % 2].container(border=True):
                st.markdown(f'**{name}** :gray[· {title}]')
                st.caption(text)


def journal_view(data, books):
    """장기투자 매매일지: the engine's decisions, NAV of the books and the long-term accounts' fills of the day."""
    nav, paid = data['nav'], data.get('contributed') or 0
    if nav:
        cols = st.columns(len(nav) + bool(paid))
        for col, (book, (value, _)) in zip(cols, nav.items()):
            change = day_change(data, book)
            col.metric(L.BOOK_LABELS.get(book, book), f'{value:,.0f}원', f'{change:+,.0f}원' if change is not None else None,
                       help='전날 대비 · 적립식으로 넣은 돈은 뺀 변화')
        if paid:
            cols[-1].metric('적립 누계', f'{paid:,.0f}원', f"{paid - data['contributed_before']:+,.0f}원")
    else:
        st.caption('이날 기록된 순자산이 없습니다(장기 엔진은 15:40에 순자산을 남깁니다).')
    if data['decisions']:
        st.dataframe([{'시각': str(r['time'])[11:16], '종류': L.DECISION_KINDS.get(r['kind'], r['kind']),
                       '국면': L.REGIMES.get(r['regime'], (r['regime'] or '—',))[0], '모드': L.MODES.get(r['mode'], r['mode'] or '—'),
                       '모듈': _modules(r['modules']), '사유': r['reason']} for r in data['decisions']], hide_index=True, width='stretch')
    else:
        st.caption('이날 장기 엔진 결정 기록이 없습니다.')
    traded = [b for k, b in books.items() if (k == 'longterm' or '장기투자' in b.get('label', '')) and b.get('fills')]
    names = {a[0]: a[1] for a in L.ASSETS.values()}
    for book in traded:
        st.markdown(f"**{book['label']}** · 체결 {len(book['fills'])}건")
        st.dataframe([{'시각': str(f['time'])[11:16], '종목': f"{names.get(f['symbol'], f['symbol'])} ({f['symbol']})", '매수/매도': '매수' if f['side'] == 'BUY' else '매도',
                       '수량': f['qty'], '가격': f"{f['price']:,.0f}"} for f in book['fills']], hide_index=True, width='stretch')


def render_day(record, root=L.ROOT):
    day = record['day']
    data = day_data(day, root)
    daily = data['reports'].get(day)
    left, right = st.columns([3, 2], gap='medium')
    with left:
        st.markdown('#### 거장의 거시 시황')
        if data['macro']:  # what the engine itself received that morning (FRED · 연준), beside the report's reading
            import longterm_macro
            st.caption('그날 아침 엔진이 받은 거시 배경 · ' + longterm_macro.brief(data['macro']).replace('\n', ' · '))
        if daily:
            masters_view(*daily)
        else:
            st.caption('이날 장기투자 전략 리포트가 없습니다(평일 08:50 예약 작업이 씁니다).')
    with right:
        st.markdown('#### 장기투자 매매일지')
        journal_view(data, record.get('books') or {})
        view = ((record.get('perspective') or {}).get('views') or {}).get('longterm')
        if view:
            st.markdown('##### 매매일지 작성자의 장기 관점')
            st.markdown(for_streamlit(view))
    st.markdown('#### 이날 매수·매도 근거')
    import longterm_evidence_view
    longterm_evidence_view.render(root, key=f'ltj_ev_{day}', day=day)
    for key, (md, doc) in data['reports'].items():
        with st.expander(f"전략 리포트 · {key}", expanded=False, icon=':material/description:'):
            if doc.get('scenarios'):
                st.markdown('**시나리오 대응**')
                st.dataframe([{'시나리오': s.get('name'), '트리거': s.get('trigger'), '대응': s.get('action')} for s in doc['scenarios']],
                             hide_index=True, width='stretch')
            st.download_button('리포트 .md 내려받기', md.encode('utf-8'), f'strategy-{key}.md', 'text/markdown',
                               icon=':material/download:', key=f'ltj_dl_{day}_{key}')
            st.markdown(for_streamlit(md))


def render_period(start, end, root=L.ROOT):
    import altair as alt
    import pandas as pd
    data = period_data(start, end, root)
    if data['nav']:
        frame = pd.DataFrame([{'날짜': d, '장부': L.BOOK_LABELS.get(b, b), '순자산': v} for d, b, v in data['nav']])
        st.altair_chart(alt.Chart(frame).mark_line(point=True).encode(
            x=alt.X('날짜:O', title=None), y=alt.Y('순자산:Q', title='순자산 (원)', scale=alt.Scale(zero=False)),
            color=alt.Color('장부:N', title=None), tooltip=['날짜', '장부', alt.Tooltip('순자산:Q', format=',')]).properties(height=260),
            width='stretch')
        paid = data['contributed']
        st.caption('가상 계좌와 두 그림자 장부의 순자산(수수료 반영, 세금 미반영).'
                   + (f" 적립 누계 {paid[max(paid)]:,.0f}원이 순자산에 들어 있습니다(세 장부 같은 금액)." if paid else ''))
    else:
        st.caption('이 기간에 기록된 장기투자 순자산이 없습니다.')
    if data['regimes']:
        st.markdown('**국면 변화** · ' + ' → '.join(f"{d[5:]} {L.REGIMES.get(r, (r,))[0]}" for d, r in data['regimes']))
    folder = Path(root) / 'reports'
    for key in data['reports']:
        found = read_report(key, folder)
        if found:
            with st.expander(f'기간 전략 리포트 · {key}', icon=':material/description:'):
                st.markdown(for_streamlit(found[0]))
    if not data['reports']:
        st.caption('이 기간의 주간·월간 장기투자 리포트가 없습니다(금 16:50 주간 · 매월 1일 월간 예약 작업이 씁니다).')
