"""Read-only detail for the selected instrument in the trading desk."""
import json
import math
import sqlite3
from datetime import datetime

import altair as alt
import streamlit as st

from sqlite_ro import ro_connect

QUESTIONS = {'candidate': '정규 진입 판단', 'watchlist': '관심종목 선정', 'holding': '보유 판단'}
EVENTS = {'watchlist_strength': '강도 통과', 'v2_entry_gate': '진입 확인',
          'v2_execution_gate': '주문 차단', 'submitted': '매수 주문',
          'v2_risk_exit': '보호 청산 판단', 'sell_submitted': '매도 주문'}
WAIT = {'WAIT': '대기', 'NONE': '선정 없음', 'HOLD': '보유', 'SELL': '매도'}
BAR_FIELDS = ('stck_oprc', 'stck_hgpr', 'stck_lwpr', 'stck_prpr', 'vol')


def replay_chart(rows, vwap, order_price):
    bars = {}
    for row in rows:
        try:
            stamp = datetime.strptime(row['bsop_time'], '%H%M%S').strftime('%H:%M')
            opened, high, low, close, volume = (float(row[field]) for field in BAR_FIELDS)
        except (KeyError, TypeError, ValueError):
            continue
        if 0 < low <= min(opened, close) <= max(opened, close) <= high and all(map(math.isfinite, (high, volume))) and volume >= 0:
            bars[stamp] = {'시각': stamp, '시가': opened, '고가': high, '저가': low, '종가': close, '거래량': volume,
                           '방향': '상승' if close >= opened else '하락'}
    if not bars:
        return None
    colors = alt.Color('방향:N', scale=alt.Scale(domain=['상승', '하락'], range=['#dc2626', '#2563eb']), legend=None)
    x = alt.X('시각:O', sort=sorted(bars), title=None)
    base = alt.Chart(alt.Data(values=list(bars.values()))).encode(
        x=x, color=colors, tooltip=['시각:O', *[alt.Tooltip(f'{field}:Q', format=',.0f')
                                              for field in ('시가', '고가', '저가', '종가', '거래량')]])
    price_scale = alt.Scale(zero=False)
    wicks = base.mark_rule().encode(y=alt.Y('저가:Q', title='가격(원)', scale=price_scale), y2='고가:Q')
    bodies = base.mark_bar(size=9).encode(y=alt.Y('시가:Q', scale=price_scale), y2='종가:Q')
    price = wicks + bodies
    references = [{'기준': label, '가격': value} for label, value in (('당시 VWAP', vwap), ('주문 지정가', order_price))
                  if type(value) in (int, float) and math.isfinite(value) and value > 0]
    if references:
        price += alt.Chart(alt.Data(values=references)).mark_rule(strokeDash=[5, 4]).encode(
            y=alt.Y('가격:Q', scale=price_scale), color=alt.Color('기준:N', legend=alt.Legend(title=None, orient='top')),
            tooltip=['기준:N', alt.Tooltip('가격:Q', format=',.2f')])
        price = price.resolve_scale(color='independent')
    volume = base.mark_bar(size=9).encode(y=alt.Y('거래량:Q', title='거래량(주)'))
    return alt.vconcat(price.properties(height=205), volume.properties(height=70), spacing=8).resolve_scale(x='shared')


def read_inputs(path, day, symbol, group):
    """Find stored model calls that actually included this instrument."""
    with ro_connect(path) as db:
        return db.execute("""
            SELECT digest, time, question
            FROM model_inputs
            WHERE time >= ? AND time < date(?, '+1 day') AND engine='jev'
              AND CASE question
                WHEN 'candidate' THEN EXISTS (
                  SELECT 1 FROM json_each(model_inputs.inputs, '$.criteria') WHERE key IN (?, ?))
                WHEN 'watchlist' THEN EXISTS (
                  SELECT 1 FROM json_each(model_inputs.inputs, '$.state.stocks') WHERE key = ?)
                WHEN 'holding' THEN json_extract(inputs, '$.state.position.symbol') = ?
              END
            ORDER BY time DESC
        """, (day, day, group, symbol, symbol, symbol)).fetchall()


def read_detail(path, digest):
    with ro_connect(path) as db:
        inputs, response = db.execute(
            'SELECT inputs, response FROM model_inputs WHERE digest=?', (digest,)).fetchone()
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='model_evidence'").fetchone()
        proof = db.execute('SELECT evidence FROM model_evidence WHERE digest=?', (digest,)).fetchone() if exists else None
    return json.loads(inputs), json.loads(response), json.loads(proof[0]) if proof else {}


def order_results(path, cycle, symbol):
    with ro_connect(path) as db:
        return db.execute("""
            SELECT i.side, i.qty, i.status, f.quantity, f.price, f.time
            FROM decision_order_links l JOIN intents i ON i.id=l.order_id
            LEFT JOIN fills f ON f.order_id=i.id WHERE l.cycle_id=? AND i.symbol=?
        """, (cycle, symbol)).fetchall()


def cycle_records(events, symbol, group):
    """Join by digest and cycle ID. An unrelated order in a candidate call is excluded."""
    calls, market, decisions = {}, {}, {}
    for row in events:
        cycle = row.get('cycle_id')
        if not cycle:
            continue
        kind = row.get('event')
        if kind in ('jev_shadow', 'jev_holding_decision'):
            call = (row.get('jev') or {}) if kind == 'jev_shadow' else row
            if digest := call.get('input_digest'):
                calls[digest] = cycle
        if kind == 'market_context':
            bars = row.get('market_bars') or {}
            if group in bars or symbol in bars:
                market[cycle] = bars.get(group) or bars.get(symbol)
        if kind in EVENTS and (row.get('symbol') == symbol or row.get('choice') in (symbol, group)
                               or symbol in (row.get('strength_reasons') or {})):
            decisions.setdefault(cycle, []).append(row)
    return calls, market, decisions


def _section(label, value):
    st.markdown(f'**{label}**')
    if value:
        st.json(value, expanded=False)
    else:
        st.caption('저장된 자료 없음')


def render(root, symbol, group, name, events, fills, now, key, reasons, names):
    st.markdown(f'##### {name} · Jev 판단 상세')
    with st.expander('판단 당시 뉴스·수급·거래 데이터', expanded=True):
        reset = st.session_state.pop(f'{key}_detail_reset', None) == symbol
        if reset:
            st.session_state.pop(f'{key}_detail_day', None)
            st.session_state.pop(f'{key}_detail_tab', None)
        date_column, round_column = st.columns([1, 3])
        day = date_column.date_input('판단 날짜', value=now.date(), max_value=now.date(), key=f'{key}_detail_day')
        path = root / 'market-research.sqlite3'
        try:
            records = read_inputs(path, day.isoformat(), symbol, group)
        except (OSError, sqlite3.Error):
            st.warning('Jev 판단 원장을 읽을 수 없습니다.')
            return
        if not records:
            st.info('선택한 날짜에 이 종목이 포함된 Jev 판단 기록이 없습니다.')
            return

        calls, market, decisions = cycle_records(events, symbol, group)
        labels, buy_calls = {}, set()
        for digest, stamp, question in records:
            orders = decisions.get(calls.get(digest), [])
            if any(row['event'] == 'submitted' for row in orders):
                buy_calls.add(digest)
            action = ' · 이 종목 매수 주문' if digest in buy_calls else ''
            labels[digest] = f'{stamp[11:19]} · {QUESTIONS[question]}{action}'
        default = next((i for i, rec in enumerate(records) if rec[0] in buy_calls), 0)
        picker_key = f'{key}_detail_{symbol}_{day}'
        if reset:
            st.session_state.pop(picker_key, None)
        digest = round_column.selectbox('판단 회차', list(labels), index=default, format_func=labels.__getitem__, key=picker_key)
        try:
            inputs, answer, proof = read_detail(path, digest)
        except (OSError, sqlite3.Error, ValueError, TypeError):
            st.warning('이 회차의 저장된 입력을 읽을 수 없습니다.')
            return
        state = inputs.get('state') or {}
        stock = ((state.get('watchlist_details') or {}).get(symbol)
                 or (state.get('stocks') or {}).get(symbol)
                 or state.get('held_stock_context') or {})
        score = (answer.get('probabilities') or {}).get(symbol, (answer.get('probabilities') or {}).get(group))
        metrics = st.columns(3)
        choice = answer.get('choice')
        linked = decisions.get(calls.get(digest), [])
        if digest in buy_calls:
            status = '강도 경로 매수 주문' if any(row.get('source') == 'watchlist_strength' for row in linked) else '매수 주문'
        elif inputs.get('question') == 'holding' or choice in WAIT:
            status = WAIT.get(choice, '미확인')
        else:
            status = 'Jev 후보로 선택' if choice in (symbol, group) else '후보 미선택' if choice else '미확인'
        metrics[0].metric('이 종목의 판단·주문', status, border=True)
        metrics[1].metric('회차 응답 신뢰도', answer.get('confidence', '미확인'), border=True)
        metrics[2].metric('이 종목의 선택 확률', score if score is not None else '미확인', border=True)
        if choice and choice not in (symbol, group) and choice not in WAIT:
            st.caption(f"같은 회차의 Jev 선택 · {names.get(choice, choice)}")
        order_tab, news_tab, data_tab, context_tab, raw_tab = st.tabs(
            ['판단·주문', '뉴스', '가격·거래량·수급', '시황·거래 복기', '저장된 입력'],
            key=f'{key}_detail_tab', on_change='rerun')
        cycle = calls.get(digest)
        bars = market.get(cycle) or []
        summary = state.get('intraday_summary') or {}
        intraday = summary.get(group) or summary.get(symbol) or {}
        if order_tab.open:
            with order_tab:
                st.markdown('**판단 당시 가격·거래량**')
                order_price = next((row.get('limit') for row in linked if row['event'] in ('submitted', 'sell_submitted')), None)
                chart = replay_chart(bars, intraday.get('vwap'), order_price)
                if chart is not None:
                    st.altair_chart(chart, width='stretch', alt='선택 회차의 종목 분봉과 거래량, VWAP 및 주문 지정가')
                    st.caption('실행기가 같은 회차에 기록한 완료 분봉입니다. 주문 지정가는 가격 기준선으로 표시합니다.')
                else:
                    st.caption('이 판단 회차에 연결된 분봉이 없습니다.')
                st.markdown('**주문 흐름**')
                if not linked:
                    st.caption('이 회차에 연결된 해당 종목의 주문·차단 기록이 없습니다.')
                timeline = []
                timing = None
                for row in linked:
                    detail = (row.get('strength_reasons') or {}).get(symbol) or row.get('reason') or row.get('trigger')
                    note = reasons(detail) if detail else ''
                    if row['event'] == 'watchlist_strength' and row.get('confidence') is not None:
                        note = f"관심종목 선정 확률 {row['confidence']:.2f}"
                    timeline.append({'시각': row['time'][11:19], '단계': EVENTS[row['event']], '가격(원)': row.get('limit'),
                                     '수량(주)': row.get('qty'), '근거·결과': note})
                    timing = row.get('entry_timing') or timing
                if cycle:
                    try:
                        settled = order_results(root / 'orders.sqlite3', cycle, symbol)
                    except (OSError, sqlite3.Error):
                        settled = []
                    for side, qty, status, filled, price, stamp in settled:
                        result = (f'{filled}/{qty}주 체결 · {price:,}원 · {stamp[11:19]}' if filled and price and stamp else
                                  '종결 · 체결 기록 없음' if status == 'terminal' else '체결 대조 중')
                        timeline.append({'시각': stamp[11:19] if stamp else '—',
                                         '단계': f"공유 모의 {'매수' if side == 'BUY' else '매도'} 결과",
                                         '가격(원)': price, '수량(주)': filled, '근거·결과': result})
                if timeline:
                    st.dataframe(timeline, hide_index=True, alt='선택 회차의 조건 통과부터 주문 결과까지')
                if timing:
                    fields = {'decision_price': '판단 분봉 종가(원)', 'order_ask': '주문 당시 매도호가(원)',
                              'ask_vs_decision_pct': '판단가 대비(%)', 'ask_vs_recent_low_pct': '최근 저가 대비(%)'}
                    st.dataframe([{label: timing.get(field) for field, label in fields.items()}],
                                 hide_index=True, alt='판단가와 주문 호가 비교')
                confirmed = [row for row in fills if row.get('종목') == symbol
                             and str(row.get('시각', '')).startswith(day.isoformat())]
                if confirmed:
                    st.caption('선택한 계좌의 해당 날짜 체결')
                    st.dataframe(confirmed, hide_index=True, alt='이 종목의 계좌 체결 내역')
                _section('Jev가 응답한 판단 근거', proof)
        if news_tab.open:
            with news_tab:
                for label, articles in (('종목 뉴스', stock.get('headlines') or stock.get('news')),
                                        ('시장 뉴스', state.get('recent_news_headlines'))):
                    st.markdown(f'**{label}**')
                    if articles:
                        st.dataframe(articles, hide_index=True, alt=label)
                    else:
                        st.caption('이 회차에 저장된 기사 없음')
        if data_tab.open:
            with data_tab:
                criterion = (inputs.get('criteria') or {}).get(group) or (inputs.get('criteria') or {}).get(symbol)
                if isinstance(criterion, str):
                    st.text(criterion)
                if intraday:
                    st.markdown('**당일 분봉 요약**')
                    fields = {'to': '마지막 분봉', 'last': '종가(원)', 'high': '고가(원)', 'low': '저가(원)',
                              'vwap': 'VWAP(원)', 'vs_vwap_pct': 'VWAP 대비(%)', 'range_position_pct': '당일 범위 위치(%)'}
                    st.dataframe([{label: intraday.get(field) for field, label in fields.items()}],
                                 hide_index=True, alt='Jev에 전달된 당일 분봉 가격과 VWAP 요약')
                _section('종목 시세·거래대금·기업 정보', {k: v for k, v in stock.items()
                         if k not in ('headlines', 'news', 'investor_daily_net_qty')})
                flows = stock.get('investor_daily_net_qty') or (state.get('investor_daily_net_qty') or {}).get(group) or {}
                st.markdown('**종목 투자자별 순매수(주)**')
                if flows:
                    periods = {'last_close_day': '직전 거래일', 'today_provisional': '당일 잠정'}
                    flow_rows = [{'구분': label, '거래일': row.get('date'), '외국인': row.get('foreign'),
                                  '기관': row.get('institution'), '개인': row.get('individual')}
                                 for field, label in periods.items() if (row := flows.get(field))]
                    if flow_rows:
                        st.dataframe(flow_rows, hide_index=True, alt='종목 투자자별 순매수 수량')
                    if 'today_provisional' not in flows:
                        st.caption('당일 잠정 수급은 이 회차 입력에서 확인되지 않습니다.')
                else:
                    st.caption('이 회차에 저장된 종목 수급 없음')
                if bars:
                    volumes = [float(row['vol']) for row in bars[:10] if str(row.get('vol', '')).isdigit()]
                    if len(volumes) == 10 and sum(volumes[5:]) > 0:
                        st.caption(f'저장된 최근 5개 분봉 거래량은 직전 5개의 {sum(volumes[:5]) / sum(volumes[5:]):.2f}배입니다.')
                    st.dataframe([
                        dict(zip(('시각', '시가', '고가', '저가', '종가', '거래량'),
                                 (row.get(field) for field in ('bsop_time', 'stck_oprc', 'stck_hgpr',
                                                               'stck_lwpr', 'stck_prpr', 'vol'))))
                        for row in bars
                    ], hide_index=True, alt='실행기 같은 회차의 분봉 가격과 거래량')
                else:
                    st.caption('이 회차에 연결된 실행기 분봉이 없습니다.')
        if context_tab.open:
            with context_tab:
                _section('시황·테마·시장 수급', state.get('market_regime_themes_global') or state.get('market_etf_change_pct'))
                _section('장전 자료', state.get('premarket_context') or state.get('overnight_material'))
                _section('과거 매매 성과', state.get('own_track_record'))
                _section('매매일지', state.get('trading_journal'))
        if raw_tab.open:
            with raw_tab:
                st.caption('저장된 구조화 입력과 응답입니다. 차트 이미지의 식별값은 입력에 포함됩니다.')
                st.json({'입력': inputs, '응답': answer}, expanded=False)
