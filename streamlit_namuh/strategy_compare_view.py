"""성과 비교 tab (was 엔진 비교, user-approved plan docs/dashboard-menu-plan-2026-11-19.md 5-5): Jev trading and the
long-term engine over the same days from nhplug/strategy-compare.sqlite3 (scripts/strategy_compare.py). Read-only;
the table is rebuilt from the source records when they changed (at most every REBUILD_MIN minutes)."""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

import status_cards as sc

MIN_DAYS = 20  # below this many days a correlation or a by-regime average says little: shown greyed with the count
REBUILD_MIN = 10
COLORS = {'Jev 트레이딩': '#a855f7', '장기투자': '#3b82f6', '기본 그림자': '#94a3b8', '자동 그림자': '#64748b',
          'KODEX 200': '#f59e0b', '나스닥100': '#10b981'}
UP, DOWN = '#ef4444', '#3b82f6'  # Korean convention: red rise, blue fall
PERIODS = ['이번 주', '이번 달', '전체', '직접']


def _load(runtime):
    """Stored rows, rebuilt first when a source changed and the last build is older than REBUILD_MIN."""
    import strategy_compare as S
    path = Path(runtime) / S.FILE
    old = not path.exists() or time.time() - path.stat().st_mtime > REBUILD_MIN * 60
    if old and S.stale(runtime):
        try:
            with st.spinner('성과 데이터를 다시 계산하는 중'):
                S.build(runtime)
        except Exception as exc:  # the last stored rows still show; say why they may be old
            st.warning(f'성과 데이터를 다시 계산하지 못해 마지막 기록을 보여 줍니다 ({type(exc).__name__}).')
    return S.rows(runtime)


def window(days, period, today, picked=None):
    """(first, last) day of the chosen period among the stored days."""
    if not days:
        return None, None
    if period == '이번 주':
        start = (today - timedelta(days=today.weekday())).isoformat()
    elif period == '이번 달':
        start = today.replace(day=1).isoformat()
    elif period == '직접' and picked and len(picked) == 2:
        return picked[0].isoformat(), picked[1].isoformat()
    else:
        start = days[0]
    return start, days[-1]


def returns(rows, book):
    """Daily returns for the period. Jev: P&L ÷ the period's average money in positions — each day's own base let a
    1,277원 day's −2% outweigh +75,990원 of P&L (11-11) — so the curve is the P&L curve; 장기투자: the stored day return."""
    if book != 'jev':
        return [r['ret_day'] or 0 for r in rows]
    used = [r['deployed'] for r in rows if r['deployed']]
    base = sum(used) / len(used) if used else 0
    return [(r['pnl_day'] or 0) / base if base else 0.0 for r in rows]


def curve(rows, book):
    """[(day, cumulative return, drawdown)]: Jev adds its days (one base), 장기투자 compounds its NAV changes."""
    out, index, peak = [], 1.0, 1.0
    for r, ret in zip(rows, returns(rows, book)):
        index = index + ret if book == 'jev' else index * (1 + ret)
        peak = max(peak, index)
        out.append((r['day'], index - 1, index / peak - 1))
    return out


def stats(rows, book):
    """Period figures of one book's rows (oldest first): P&L, return, worst fall, day win rate and trades."""
    if not rows:
        return None
    line = curve(rows, book)
    index, worst = 1 + line[-1][1], min(d for _, _, d in line)
    active = [r for r in rows if r['pnl_day'] and abs(r['pnl_day']) >= 1]
    trades = [r['trades'] for r in rows if r['trades'] is not None]
    return {'pnl': sum(r['pnl_day'] or 0 for r in rows), 'ret': index - 1, 'mdd': worst, 'days': len(rows),
            'win_days': sum(1 for r in active if r['pnl_day'] > 0), 'active': len(active),
            'trades': sum(trades) if trades else None, 'fees': sum(r['fees_est'] or 0 for r in rows)}


def relation(a, b):
    """Daily returns of two books on their shared days: (n, correlation or None, share of days both rose)."""
    shared = sorted(set(a) & set(b))
    if len(shared) < 2:
        return len(shared), None, None
    x, y = pd.Series([a[d] for d in shared]), pd.Series([b[d] for d in shared])
    corr = x.corr(y) if x.std() and y.std() else None
    return len(shared), (None if corr is None or pd.isna(corr) else float(corr)), sum(1 for d in shared if a[d] > 0 and b[d] > 0) / len(shared)


def _won(v):
    return f'{v:+,.0f}원'


def _cards(name, s, icon, sub):
    if not s:
        return [{'label': name, 'value': '기록 없음', 'tone': 'off', 'icon': icon, 'sub': sub}]
    win = f"{s['win_days']}/{s['active']}일" if s['active'] else '—'
    return [{'label': f'{name} · 손익', 'value': _won(s['pnl']), 'tone': sc.signed(s['pnl']), 'icon': icon,
             'sub': f"수익률 {s['ret'] * 100:+.2f}% · {sub}",
             'help': '기간 손익의 합입니다. 수익률은 Jev가 기간 손익 ÷ 기간 평균 투입금(그날 가장 많이 들고 있던 금액의 평균), 장기투자가 순자산 변화를 이어 붙인 값입니다.'},
            {'label': f'{name} · 위험', 'value': f"최대 낙폭 {s['mdd'] * 100:.2f}%", 'tone': 'down' if s['mdd'] < 0 else 'off',
             'icon': 'trending_down', 'sub': f"이긴 날 {win}" + (f" · 매매 {s['trades']}건" if s['trades'] is not None else ''),
             'help': '수익률 곡선의 고점 대비 가장 깊은 하락입니다. 이긴 날은 손익이 있었던 날 중 플러스로 끝난 날입니다.'}]


def _line(frame, y, title, fmt):
    sel = alt.selection_point(fields=['구분'], bind='legend')
    return alt.Chart(frame).mark_line(point=True).encode(
        x=alt.X('날짜:O', title=None, sort='ascending', axis=alt.Axis(labelExpr="slice(datum.value, 5)", labelAngle=0)),
        y=alt.Y(f'{y}:Q', title=title),
        color=alt.Color('구분:N', scale=alt.Scale(domain=list(COLORS), range=list(COLORS.values())), legend=alt.Legend(orient='bottom', title=None)),
        strokeDash=alt.condition(alt.FieldOneOfPredicate('구분', ['KODEX 200', '나스닥100', '기본 그림자', '자동 그림자']), alt.value([4, 3]), alt.value([1, 0])),
        opacity=alt.condition(sel, alt.value(1), alt.value(0.15)),
        tooltip=['날짜', '구분', alt.Tooltip(f'{y}:Q', format=fmt)]).add_params(sel)


def render(runtime):
    import guides
    import strategy_compare as S
    st.subheader('성과 비교 · Jev 트레이딩 vs 장기투자')
    all_rows = _load(runtime)
    if not all_rows:
        st.info('비교 기록이 아직 없습니다. 장 마감 뒤(학습 관찰기) 또는 이 화면을 다시 열 때 만들어집니다.', icon=':material/schedule:')
        return
    days = sorted({r['day'] for r in all_rows})
    left, mid, right = st.columns([2, 2, 1], vertical_alignment='bottom')
    period = left.segmented_control('기간', PERIODS, default='전체', key='cmp_period') or '전체'
    picked = mid.date_input('직접 고른 기간', (date.fromisoformat(days[0]), date.fromisoformat(days[-1])), key='cmp_range',
                            disabled=period != '직접')
    shadows = right.toggle('그림자 장부', key='cmp_shadows', help='장기투자의 비교 기준(기본 그림자: 기준 비중+달리오, 자동 그림자: 국면 추천 조합)을 겹쳐 봅니다.')
    start, end = window(days, period, datetime.now().astimezone().date(), picked)
    rows = [r for r in all_rows if start <= r['day'] <= end]
    by = {b: [r for r in rows if r['book'] == b] for b in S.BOOKS}
    jev, lt = stats(by['jev'], 'jev'), stats(by['longterm'], 'longterm')
    st.caption(f"{start} ~ {end} · Jev는 공유 모의 원장(수수료·세금 추정 차감), 장기투자는 1억 원 가상 계좌 순자산(수수료 포함, 적립금 제외) · "
               f"마지막 계산 {all_rows[-1]['built'][11:16]}")
    daily = {b: dict(zip((r['day'] for r in by[b]), returns(by[b], b))) for b in ('jev', 'longterm')}
    n, corr, both_up = relation(daily['jev'], daily['longterm'])
    enough = n >= MIN_DAYS
    sc.row(_cards('Jev', jev, 'psychology', '평균 투입금 대비') + _cards('장기투자', lt, 'savings', '순자산 대비')
           + [{'label': '두 방식 관계', 'value': f'상관 {corr:+.2f}' if corr is not None else '상관 —', 'icon': 'join_inner',
               'tone': 'ok' if enough else 'off',
               'sub': (f'함께 오른 날 {both_up * 100:.0f}% · ' if both_up is not None else '') + f"표본 {n}일" + ('' if enough else ' · 판단하기 이름'),
               'help': f'두 방식 일별 수익률의 상관입니다. −1에 가까울수록 서로 반대로 움직여 함께 들면 흔들림이 줄어듭니다. {MIN_DAYS}일보다 적으면 우연이 큽니다.'}])

    tabs = st.tabs(['성과 곡선', '위험', '국면별 성적', '두 방식 관계', '방식 한눈에', '일별 기록', '기준·데이터 흐름'])
    with tabs[0]:
        line = []
        for b, label in S.BOOKS.items():
            if b in ('longterm_base', 'longterm_auto') and not shadows:
                continue
            line += [{'날짜': d, '구분': label, '수익률': ret * 100} for d, ret, _ in curve(by[b], b)]
        for key, label in (('069500', 'KODEX 200'), ('NDX', '나스닥100')):
            index = 1.0
            for d in sorted({r['day'] for r in rows}):
                value = next((r['bench'].get(key) for r in rows if r['day'] == d), None)
                index *= 1 + (value or 0) / 100
                line.append({'날짜': d, '구분': label, '수익률': (index - 1) * 100})
        st.altair_chart(_line(pd.DataFrame(line), '수익률', '누적 수익률(%)', '+.2f').properties(height=300), width='stretch')
        band = pd.DataFrame([{'날짜': r['day'], '줄': '국내(코스피)', '국면': r['regime_kr'] or '미확인'} for r in by['jev']]
                            + [{'날짜': r['day'], '줄': '미국(장기 엔진)', '국면': _us(r['regime_us'])} for r in by['longterm']])
        if not band.empty:
            x = alt.X('날짜:O', title=None, sort='ascending', axis=alt.Axis(labelExpr="slice(datum.value, 5)", labelAngle=0))
            rect = alt.Chart(band).mark_rect().encode(
                x=x, y=alt.Y('줄:N', title=None),
                color=alt.Color('국면:N', legend=alt.Legend(orient='bottom', title='국면')), tooltip=['날짜', '줄', '국면'])
            label = alt.Chart(band).mark_text(fontSize=11).encode(x=x, y='줄:N', text='국면:N')
            st.altair_chart((rect + label).properties(height=80), width='stretch')
        st.caption('선을 누르면 범례 항목만 강조합니다. 점선은 비교 기준(벤치마크·그림자)입니다. 나스닥100은 그 한국 날짜 전 마지막 미국 거래일 값입니다.')
    with tabs[1]:
        under = [{'날짜': d, '구분': S.BOOKS[b], '낙폭': dd * 100} for b in ('jev', 'longterm') for d, _, dd in curve(by[b], b)]
        st.altair_chart(_line(pd.DataFrame(under), '낙폭', '고점 대비(%)', '.2f').properties(height=220), width='stretch')
        worst = sorted((r for r in rows if r['book'] in ('jev', 'longterm')), key=lambda r: r['pnl_day'] or 0)[:5]
        st.markdown('###### 가장 나빴던 날 5')
        st.dataframe([{'날짜': r['day'], '방식': S.BOOKS[r['book']], '손익': _won(r['pnl_day']), '그날 투입금 대비': f"{(r['ret_day'] or 0) * 100:+.2f}%",
                       '국내 국면': r['regime_kr'] or '—', '미국 국면': _us(r['regime_us'])} for r in worst], hide_index=True, width='stretch')
    with tabs[2]:
        regime_view(by)
    with tabs[3]:
        dots = pd.DataFrame([{'날짜': d, 'Jev': v * 100, '장기투자': daily['longterm'][d] * 100} for d, v in daily['jev'].items() if d in daily['longterm']])
        if dots.empty:
            st.info('두 방식이 함께 기록된 날이 아직 없습니다.')
        else:
            if not enough:
                st.warning(f'표본 {n}일 · {MIN_DAYS}일이 쌓이기 전에는 관계를 판단하지 않습니다. 점은 날짜별 참고입니다.', icon=':material/hourglass_top:')
            st.altair_chart(alt.Chart(dots).mark_circle(size=90, color='#8b5cf6').encode(
                x=alt.X('Jev:Q', title='Jev 일 수익률(%)'), y=alt.Y('장기투자:Q', title='장기투자 일 수익률(%)'),
                tooltip=['날짜', alt.Tooltip('Jev:Q', format='+.2f'), alt.Tooltip('장기투자:Q', format='+.2f')]).properties(height=280), width='stretch')
            mix = []
            for share in (0.5, 0.7):
                index = 1.0
                for _, r in dots.iterrows():
                    index *= 1 + (share * r['Jev'] + (1 - share) * r['장기투자']) / 100
                    mix.append({'날짜': r['날짜'], '구분': f'Jev {share * 100:.0f} : 장기 {100 - share * 100:.0f}', '수익률': (index - 1) * 100})
            st.markdown('###### 섞었을 때 (참고)')
            st.altair_chart(alt.Chart(pd.DataFrame(mix)).mark_line(point=True).encode(
                x=alt.X('날짜:O', title=None, sort='ascending', axis=alt.Axis(labelExpr="slice(datum.value, 5)", labelAngle=0)),
                y=alt.Y('수익률:Q', title='누적 수익률(%)'), color=alt.Color('구분:N', legend=alt.Legend(orient='bottom', title=None)),
                tooltip=['날짜', '구분', alt.Tooltip('수익률:Q', format='+.2f')]).properties(height=220), width='stretch')
            st.caption('두 방식의 일 수익률을 정한 비율로 섞어 이은 가상 곡선입니다. 실제로 자금을 나눈 결과가 아닙니다.')
    with tabs[4]:
        guides.engines_view(guides.values())
    with tabs[5]:
        st.dataframe([{'날짜': r['day'], '방식': S.BOOKS[r['book']], '손익': round(r['pnl_day'] or 0), '누적 손익': round(r['pnl_cum'] or 0),
                       '그날 수익률(%)': round((r['ret_day'] or 0) * 100, 3),
                       '투입금·기준': round(r['deployed'] or 0), '실현': None if r['realized_day'] is None else round(r['realized_day']),
                       '수수료·세금 추정': None if r['fees_est'] is None else round(r['fees_est']), '매매': r['trades'],
                       '평균 보유(분)': None if r['avg_hold_min'] is None else round(r['avg_hold_min']),
                       '국내 국면': r['regime_kr'], '미국 국면': _us(r['regime_us']),
                       'KODEX 200(%)': r['bench'].get('069500'), '나스닥100(%)': r['bench'].get('NDX')}
                      for r in reversed(rows)], hide_index=True, width='stretch')
        st.download_button('CSV 내려받기', pd.DataFrame(rows).drop(columns=['bench']).to_csv(index=False).encode('utf-8-sig'),
                           file_name=f'strategy-compare-{start}-{end}.csv', mime='text/csv', icon=':material/download:')
    with tabs[6]:
        flow_view()


def _us(key):
    import vibe_longterm as L
    return L.REGIMES[key][0] if key in L.REGIMES else '—'


def regime_view(by):
    """Average daily return of each book by the day's regime, with the day count on every bar."""
    rows = []
    for b, label, field in (('jev', 'Jev 트레이딩', 'regime_kr'), ('longterm', '장기투자', 'regime_us')):
        groups = {}
        for r, ret in zip(by[b], returns(by[b], b)):  # Jev on the period's average money in use, as on the cards
            name = r[field] if b == 'jev' else _us(r[field])
            groups.setdefault(name or '미확인', []).append(ret)
        rows += [{'방식': label, '국면': f'{name} ({len(v)}일)', '평균 일 수익률(%)': sum(v) / len(v) * 100, '표본': len(v)} for name, v in groups.items()]
    if not rows:
        st.info('기록이 없습니다.')
        return
    frame = pd.DataFrame(rows)
    values = frame['평균 일 수익률(%)']
    shared = alt.Scale(domain=[min(0.0, values.min()), max(0.0, values.max())])  # one x scale so bar lengths compare
    for label, part in frame.groupby('방식', sort=False):  # one chart each: the two use different regime lists
        st.markdown(f'###### {label}')
        st.altair_chart(alt.Chart(part).mark_bar().encode(
            y=alt.Y('국면:N', title=None, axis=alt.Axis(labelLimit=240)), x=alt.X('평균 일 수익률(%):Q', scale=shared),
            color=alt.condition(alt.datum['평균 일 수익률(%)'] >= 0, alt.value(UP), alt.value(DOWN)),
            opacity=alt.condition(alt.datum['표본'] >= MIN_DAYS, alt.value(1), alt.value(0.45)),
            tooltip=['국면', alt.Tooltip('평균 일 수익률(%):Q', format='+.3f'), '표본']).properties(height=alt.Step(34)), width='stretch')
    st.caption(f'국내는 코스피 일봉 국면(매매일지), 미국은 장기 엔진의 확정 국면입니다. 표본 {MIN_DAYS}일 미만인 막대는 흐리게 그립니다.')


def flow_spec():
    """Archify workflow: where the 성과 비교 numbers come from and who reads them."""
    import guide_ui as GU
    return GU.workflow('성과 비교 데이터 흐름', [('src', '원천 기록'), ('calc', '집계'), ('use', '쓰는 곳')],
        [('ledger', 'src', 0, 'Jev 모의 원장', 'orders.sqlite3 체결', 'database'),
         ('snap', 'src', 1, '모의 잔고 기록', 'account-snapshots.jsonl', 'database'),
         ('nav', 'src', 2, '장기투자 순자산', '15:40 기록 · 가상 체결', 'database'),
         ('ctx', 'src', 3, '국면 · 벤치마크', '코스피 국면 · KODEX 200 · 나스닥100', 'external'),
         ('build', 'calc', 1, '하루 단위 집계', '장 마감 뒤 · 원천이 바뀌면', 'backend'),
         ('table', 'calc', 2, 'compare_daily', '날짜 × 장부 한 줄', 'database'),
         ('screen', 'use', 2, '성과 비교 화면', '곡선 · 위험 · 국면별 · 관계', 'backend'),
         ('csv', 'use', 3, 'CSV · 다른 분석', '트레이딩 데이터로 활용', 'backend')],
        [('ledger', 'build', None, 'main'), ('snap', 'build'), ('nav', 'build'), ('ctx', 'build'),
         ('build', 'table', None, 'main'), ('table', 'screen', None, 'main'), ('table', 'csv')],
        main=['ledger', 'build', 'table', 'screen'])


def flow_dot():
    return ('digraph { rankdir=LR; node [shape=box, style="rounded"]; '
            'ledger [label="Jev 모의 원장"]; snap [label="모의 잔고 기록"]; nav [label="장기투자 순자산"]; ctx [label="국면·벤치마크"]; '
            'build [label="strategy_compare.build"]; table [label="compare_daily"]; screen [label="성과 비교 화면"]; csv [label="CSV"]; '
            'ledger -> build; snap -> build; nav -> build; ctx -> build; build -> table; table -> screen; table -> csv; }')


def flow_view():
    import guide_ui as GU
    GU.style()
    GU.flow('strategy-compare', flow_spec(), flow_dot())
    GU.table([('Jev 일 수익률', '(실현 + 평가손익 변화 − 수수료·세금 추정) ÷ 그날 최대 투입금. 포지션이 없던 날은 0'),
              ('장기투자 일 수익률', '(순자산 변화 − 그날 넣은 적립금) ÷ (전날 순자산 + 그날 적립금). 수수료는 순자산에 이미 반영'),
              ('수수료·세금 추정', '매수·매도 금액의 0.015% + 개별주 매도 금액의 0.20%(국내 ETF는 거래세 없음). 모의 체결에는 실제 비용이 없어 추정값'),
              ('최대 낙폭', '이어 붙인 수익률 곡선의 고점 대비 가장 깊은 하락'),
              ('저장', '~/.vibe-trading/nhplug/strategy-compare.sqlite3 · compare_daily(날짜, 장부)'),
              ('다시 계산', '평일 장 마감 뒤 학습 관찰기 · 이 화면을 열 때 원천이 바뀌었고 마지막 계산이 10분보다 오래됐으면')],
             head=('항목', '기준'))
