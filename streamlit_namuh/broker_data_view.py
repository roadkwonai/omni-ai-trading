"""Dashboard views of the Kiwoom / Toss data (2026-11-17): market extras, journal flow extras, pre-market sections.

Read-only rendering of what market_context, trading_journal and premarket already stored; nothing here calls a broker.
"""
import altair as alt
import pandas as pd
import streamlit as st

from dialogs import table_frame
from market_view import number, timestamp

MARKETS = {'KOSPI': '코스피', 'KOSDAQ': '코스닥'}


def _rank_rows(rows, value_label='순매수 (억 원)'):
    return [{'종목': f"{r.get('name') or r.get('code')} ({r.get('code')})", value_label: r.get('net_100m'),
             **({'등락률 (%)': r['change_pct']} if 'change_pct' in r else {})} for r in rows or []]


def stamp(text):
    at = timestamp(text)
    return f'{at:%m-%d %H:%M}' if at else '미확인'


def _table(rows, alt_text):
    if rows:
        table_frame(rows, alt=alt_text, column_config={'순매수 (억 원)': st.column_config.NumberColumn(format='%+,.1f'),
                                                       '등락률 (%)': st.column_config.NumberColumn(format='%+.2f')})
    else:
        st.caption('표시할 자료가 없습니다.')


def _buy_sell(side, label):
    """Net-buy and net-sell top 10 side by side."""
    for column, key, title in zip(st.columns(2), ('net_buy', 'net_sell'), ('순매수', '순매도')):
        with column:
            st.markdown(f'**{title}**')
            _table(_rank_rows((side.get(key) or [])[:10]), f'{label} {title} 상위')


def market_extras(data):
    """Sections for the market page from market_context's Kiwoom / Toss parts; absent parts are skipped. Which source
    the indices and themes came from is shown at those sections (app.market_panel)."""
    paths = (data.get('index_intraday') or {}).get('markets') or {}
    if paths:
        st.markdown('### 장중 지수 흐름')
        st.caption('토스증권 지수 1분봉 · 봉은 끝나는 분으로 표시 · 마지막 봉은 아직 만들어지는 중일 수 있음')
        for col, (code, p) in zip(st.columns(len(paths)), paths.items()):
            with col, st.container(border=True):
                st.markdown(f"**{MARKETS.get(code, code)} {number(p.get('last'), ',.2f')}** · {p.get('as_of')}")
                st.caption(f"전일 대비 {number(p.get('change_pct'), '+.2f')}% · 시가 대비 {number(p.get('from_open_pct'), '+.2f')}% · "
                           f"30분 전 대비 {number(p.get('change_30m_pct'), '+.2f')}% · 오늘 범위의 {number(p.get('range_position_pct'), '.0f')}% 위치")
                if p.get('last_30'):
                    frame = pd.DataFrame(p['last_30'], columns=['시각', '지수'])
                    st.altair_chart(alt.Chart(frame).mark_line().encode(
                        x=alt.X('시각:N', title=None, axis=alt.Axis(labelAngle=0, labelOverlap=True)),
                        y=alt.Y('지수:Q', title=None, scale=alt.Scale(zero=False)), tooltip=['시각', alt.Tooltip('지수:Q', format=',.2f')]
                    ).properties(height=140), width='stretch')

    program = (data.get('program_intraday') or {}).get('markets') or {}
    if program:
        st.markdown('### 프로그램매매 장중 추이')
        st.caption('키움 REST ka90005 · KRX · 누적 순매수 (억 원) · 약 2분 간격')
        frame = pd.DataFrame([{'시각': r[0], '시장': MARKETS.get(code, code), '구분': name, '순매수': value}
                              for code, m in program.items() for r in m.get('rows') or []
                              for name, value in (('차익', r[1]), ('비차익', r[2]), ('전체', r[3])) if value is not None])
        if not frame.empty:
            st.altair_chart(alt.Chart(frame).mark_line().encode(
                x=alt.X('시각:N', title=None, axis=alt.Axis(labelAngle=0, labelOverlap=True)), y=alt.Y('순매수:Q', title='억 원'),
                color=alt.Color('구분:N', legend=alt.Legend(orient='top', title=None)), strokeDash='시장:N',
                tooltip=['시장', '시각', '구분', alt.Tooltip('순매수:Q', format='+,.1f')]).properties(height=220), width='stretch')
            last = {MARKETS.get(code, code): m['rows'][-1] for code, m in program.items() if m.get('rows')}
            st.caption(' · '.join(f"{name} {row[0]} 전체 {number(row[3], '+,.0f')}억 · 베이시스 {number(row[5], '+.2f')}"
                                  for name, row in last.items()))

    desks, leaders = data.get('foreign_desks') or {}, (data.get('investor_rankings') or {}).get('investors') or {}
    if desks or leaders:
        st.markdown('### 투자자별 순매수 상위')
        tabs = st.tabs([*(['외국계 창구 (키움)'] if desks else []), *[f'{who} (토스)' for who in leaders]])
        i = 0
        if desks:
            with tabs[0]:
                st.caption('키움 ka10037 · KRX 외국계 증권사 창구 기준 · 등록외국인 집계와 다름')
                _buy_sell(desks, '외국계 창구')
            i = 1
        for who, side in leaders.items():
            with tabs[i]:
                st.caption(f"토스증권 · KRX·NXT 통합, ETF 포함 · 기준 시각 {stamp(side.get('ranked_at'))}"
                           + (' · 개인은 정규장 종료 뒤 갱신(장중에는 전 거래일)' if who == '개인' else ' · 장중 잠정'))
                _buy_sell(side, who)
            i += 1


def journal_extras(flows, names):
    """Journal flows: short selling / credit / lending of the traded codes and the day's investor leaders."""
    from trading_journal import pressure_parts
    pressure = flows.get('pressure') or {}
    if pressure:
        st.markdown('**매매 종목 공매도·신용·대차** (토스증권)')
        rows = []
        for code, f in pressure.items():
            short, credit, lend = pressure_parts(f)
            rows.append({'종목': f"{names.get(code, code)} ({code})", '공매도 (억)': short.get('amount_100m'),
                         '공매도 비중 (%)': short.get('amount_rate_pct'), '신용융자 잔고 (주)': credit.get('margin_balance'),
                         '신용 전일 대비 (주)': credit.get('margin_balance_change'), '신용 잔고율 (%)': credit.get('margin_balance_rate_pct'),
                         '대차 잔고 (주)': lend.get('balance'), '대차 전일 대비 (주)': lend.get('balance_change'),
                         '기준일': ' · '.join(sorted({x['date'] for x in (short, credit, lend) if x.get('date')})) or '—'})
        st.dataframe(rows, hide_index=True, width='stretch', alt='매매 종목 공매도·신용·대차', column_config={
            '공매도 (억)': st.column_config.NumberColumn(format='%,.1f'), '공매도 비중 (%)': st.column_config.NumberColumn(format='%.2f'),
            '신용융자 잔고 (주)': st.column_config.NumberColumn(format='%,.0f'), '신용 전일 대비 (주)': st.column_config.NumberColumn(format='%+,.0f'),
            '신용 잔고율 (%)': st.column_config.NumberColumn(format='%.2f'), '대차 잔고 (주)': st.column_config.NumberColumn(format='%,.0f'),
            '대차 전일 대비 (주)': st.column_config.NumberColumn(format='%+,.0f')})
        st.caption('신용은 다음 영업일 새벽(T+1)에 반영돼 장 마감 당일 일지에는 전 영업일 값입니다.')
    leaders = flows.get('investor_rankings') or {}
    if leaders:
        st.markdown('**투자자별 순매수·순매도 상위** (토스증권, 억 원)')
        st.dataframe([{'투자자': who, '순매수 상위': ', '.join(f"{r.get('name') or r['code']} {number(r.get('net_100m'), '+,.0f')}" for r in side.get('net_buy', [])[:5]),
                       '순매도 상위': ', '.join(f"{r.get('name') or r['code']} {number(r.get('net_100m'), '+,.0f')}" for r in side.get('net_sell', [])[:5]),
                       '기준 시각': stamp(side.get('ranked_at'))} for who, side in leaders.items()],
                     hide_index=True, width='stretch', alt='투자자별 순매수·순매도 상위')


def premarket_listings(data):
    rows = [{'종목': f"{r.get('name')} ({r.get('code')})", '상장 예정일': f"{r['listed'][:4]}-{r['listed'][4:6]}-{r['listed'][6:]}",
             '시장': r.get('market') or '미확인'} for r in data or [] if isinstance(r, dict) and len(str(r.get('listed'))) == 8]
    if rows:
        st.dataframe(rows, hide_index=True, width='stretch', alt='상장 예정 종목')
        st.caption('상장일은 토스증권 종목 정보 기준이며 거래소 공시로 확정된 값이 아닙니다. 신규주 매수는 기존처럼 NH 상장일 확인 뒤에만 합니다.')
    else:
        st.caption('상장 예정 종목이 없습니다.')


def premarket_nxt_gap(data):
    if not isinstance(data, dict) or not (data.get('top') or data.get('bottom')):
        st.caption('표시할 괴리율 자료가 없습니다.')
        return
    gap = lambda rows: [{'종목': f"{r.get('name')} ({r.get('code')})", '괴리율 (%)': r.get('gap_pct'), 'NXT 등락률 (%)': r.get('nxt_change_pct'),  # noqa: E731
                         'KRX 등락률 (%)': r.get('krx_change_pct'), 'NXT 거래대금 (백만 원)': r.get('nxt_value_mil')} for r in rows or []]
    config = {k: st.column_config.NumberColumn(format='%+.2f') for k in ('괴리율 (%)', 'NXT 등락률 (%)', 'KRX 등락률 (%)')}
    if data.get('watched'):
        st.markdown('**관심 종목**')
        st.dataframe(gap(data['watched']), hide_index=True, width='stretch', alt='관심 종목 KRX/NXT 괴리율', column_config=config)
    top, bottom = st.columns(2)
    with top:
        st.markdown('**NXT가 높은 종목**')
        st.dataframe(gap(data.get('top')), hide_index=True, width='stretch', alt='괴리율 상위', column_config=config)
    with bottom:
        st.markdown('**NXT가 낮은 종목**')
        st.dataframe(gap(data.get('bottom')), hide_index=True, width='stretch', alt='괴리율 하위', column_config=config)
    st.caption('거래대금이 작은 종목의 괴리는 호가 공백일 수 있습니다.')
