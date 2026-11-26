"""Account and execution history in the dashboard."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from time import monotonic
from typing import NamedTuple
from sqlite3 import Error as DatabaseError
from uuid import uuid4
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st

import status_cards as sc
import trade_history_data as data
import trade_history_scroll as scroll
import trade_history_sessions as sessions

KST = ZoneInfo('Asia/Seoul')
LABELS = dict(zip(data.ACCOUNTS, ('모의', '실계좌 1', '실계좌 2')))
SCOPES = {
    '전체': data.ACCOUNTS,
    **{label: (key,) for key, label in LABELS.items()},
    '실계좌 합산': data.ACCOUNTS[1:],
}
PAGE = 50
LARGE_QUERY_ROWS = 10_000
LARGE_PAGE = 500
WIDGET = 'trade_history_ledger'
CURSOR = 'trade_history_cursor'
SNAPSHOT = 'trade_history_query_lease'
ORDER_FIELDS = ('side', 'qty', 'price', 'amount', 'pnl', 'return_pct', 'cost', 'order_no', 'time_basis')


class TradeQuerySnapshot(NamedTuple):
    filters: tuple
    lease: data.QueryLease
    query_id: str
    names: dict
    revision: tuple
    checked_at: datetime


def _won(value, signed=False):
    return '—' if value is None else f"{value:{'+' if signed else ''},.0f}원"


def _journal(day):
    st.session_state['journal_goto'] = day.isoformat()
    st.session_state['main_tab'] = '매매일지'


def release_snapshot():
    snapshot = st.session_state.pop(SNAPSHOT, None)
    if snapshot is not None:
        sessions.release(snapshot.lease)


def _date_range(today):
    preset = st.radio('조회 기간', ['오늘', '최근 7일', '이번 달', '전체 기록', '직접 지정'],
                      horizontal=True, key='trade_history_period', index=2)
    if preset == '직접 지정':
        start, end = st.columns(2)
        return (start.date_input('시작일', today.replace(day=1), max_value=today, key='trade_history_start'),
                end.date_input('종료일', today, max_value=today, key='trade_history_end'))
    starts = {'오늘': today, '최근 7일': today - timedelta(days=6), '이번 달': today.replace(day=1),
              '전체 기록': date(2000, 1, 1)}
    return starts[preset], today


def _display_rows(rows, names):
    def display(row):
        symbol = str(row['symbol'])
        stamp = row['time'] if row['time_known'] else row['time'][:10]
        name = names.get(symbol) or symbol
        return {**{key: row.get(key) for key in ORDER_FIELDS},
                'time': stamp.replace('T', ' ').removesuffix('+09:00'),
                'account': LABELS[row['account']], 'symbol': f'{name} ({symbol})' if name != symbol else symbol}
    return [display(row) for row in rows]


@st.fragment
def _ledger(token, names, query_id):
    try:
        query = data.get_query(token)
    except data.QueryExpired:
        st.info('조회 결과가 만료됐습니다. 새 기록 확인을 눌러 주세요.')
        return
    request = st.session_state.get(WIDGET, {}).get('next_offset')
    offset, request_id = st.session_state.get(CURSOR, (0, None))
    total = len(query)
    page_size = LARGE_PAGE if total >= LARGE_QUERY_ROWS else PAGE
    if isinstance(request, dict) and request.get('query_id') == query_id:
        requested = request.get('offset')
        requested_id = request.get('request_id')
        if (type(requested) is int and 0 <= requested < total
                and (requested == 0 or (requested >= PAGE and (requested - PAGE) % page_size == 0))):
            offset, request_id = requested, requested_id
            st.session_state[CURSOR] = (offset, request_id)
    scroll.render(key=WIDGET, query_id=query_id,
                  rows=_display_rows(query.page(offset, PAGE if offset == 0 else page_size), names),
                  offset=offset, total=total, page_size=page_size, first_page_size=PAGE, request_id=request_id)


@st.fragment(run_every=timedelta(seconds=30))
def _ledger_freshness(runtime, revision, checked_at):
    st.caption(f'원장 조회 {checked_at:%Y-%m-%d %H:%M:%S} KST')
    try:
        changed = data.freshness_revision(runtime) != revision
    except OSError:
        st.caption('원장의 변경 여부를 확인하지 못했습니다.')
        return
    if changed:
        st.info('체결 또는 주문 연결 기록이 변경됐습니다. 새 기록 확인을 누르면 다시 조회합니다.')


UP, DOWN, FLAT = (sc.TONES[tone][0] for tone in ('up', 'down', 'off'))


def _pnl_buckets(rows, hourly):
    buckets = defaultdict(float)
    for row in rows:
        if row['pnl'] is not None and (not hourly or row['time_known']):
            key = row['time'][11:13] + '시' if hourly else row['time'][:10]
            buckets[key] += row['pnl']
    if hourly or len(buckets) <= 24:
        return ('시간대' if hourly else '거래일'), sorted(buckets.items())
    months = defaultdict(float)
    for key, value in buckets.items():
        months[key[:7]] += value
    if len(months) <= 16:
        return '월', sorted(months.items())
    years = defaultdict(float)
    for key, value in months.items():
        years[key[:4]] += value
    return '연도', sorted(years.items())


def _analytics(rows, hourly):
    totals = data.summary(rows)
    known = totals['known_sells']
    losses = totals['losses']
    loss_rates = [row['return_pct'] for row in rows if row['pnl'] is not None and row['pnl'] < 0
                  and row['return_pct'] is not None]
    st.markdown('#### 체결·손익 요약')
    realized = totals['realized']
    sc.row([
        {'label': '매수 체결금액', 'value': _won(totals['buy_amount']), 'tone': 'up',
         'icon': 'south_west', 'sub': f"{totals['buys']:,}건"},
        {'label': '매도 체결금액', 'value': _won(totals['sell_amount']), 'tone': 'down',
         'icon': 'north_east', 'sub': f"{totals['sells']:,}건"},
        {'label': '실현손익', 'value': _won(realized, signed=True), 'tone': sc.signed(realized or 0),
         'icon': 'payments', 'sub': f"실현수익률 {sc.signed_percent(totals['return_pct'])}", 'signed_sub': True},
        {'label': '손실 매도 비중', 'value': f'{losses / known:.1%}' if known else '—',
         'tone': 'down' if losses else 'off', 'icon': 'donut_small', 'sub': f'원가 확인 매도 {known:,}건 기준'},
    ])
    if not known:
        if rows:
            st.caption('취득원가를 확인할 수 있는 매도 기록이 생기면 손익 차트가 표시됩니다.')
        return

    trend, outcomes = st.columns([2, 1], border=True)
    grain, points = _pnl_buckets(rows, hourly)
    title = '일별 실현손익' if grain == '거래일' else f'{grain}별 실현손익'
    trend.markdown(f'**{title}**')
    if points:
        if grain in ('월', '연도'):
            trend.caption(f'긴 조회 기간을 {grain} 단위로 합산했습니다.')
        frame = pd.DataFrame([{grain: period, '실현손익': value, '금액 표시': _won(value, signed=bool(value))}
                              for period, value in points])
        limit = max(abs(value) for _, value in points) or 1
        day_axis = alt.Y(f'{grain}:O', sort='descending', title=None,
                         axis=alt.Axis(labelAngle=0, labelOverlap=False, ticks=False, domain=False))
        pnl_axis = alt.X('실현손익:Q', title='실현손익 (원)', scale=alt.Scale(domain=[-1.6 * limit, 1.6 * limit]),
                         axis=alt.Axis(format=',.0f', tickCount=5, labelOverlap='greedy'))
        bars = alt.Chart(frame, mark=alt.MarkDef(type='bar', size=13, cornerRadiusEnd=3)).encode(
            y=day_axis, x=pnl_axis,
            color=alt.condition(alt.datum.실현손익 >= 0, alt.value(UP), alt.value(DOWN)),
            tooltip=[alt.Tooltip(f'{grain}:O'), alt.Tooltip('실현손익:Q', format='+,.0f')])
        right = alt.Chart(frame, mark=alt.MarkDef(type='text', align='left', dx=5, fontSize=11)).encode(
            y=day_axis, x=pnl_axis, text=alt.Text('금액 표시:N'),
            color=alt.condition(alt.datum.실현손익 > 0, alt.value(UP), alt.value(FLAT)),
        ).transform_filter(alt.datum.실현손익 >= 0)
        left = alt.Chart(frame, mark=alt.MarkDef(type='text', align='right', dx=-5, fontSize=11)).encode(
            y=day_axis, x=pnl_axis, text=alt.Text('금액 표시:N'), color=alt.value(DOWN),
        ).transform_filter(alt.datum.실현손익 < 0)
        trend.altair_chart(alt.layer(bars, right, left).properties(height=max(148, 22 * len(points))),
                           width='stretch', alt=f'{title}의 날짜·시각별 원화 금액')
    else:
        trend.caption('시각을 확인할 수 있는 매도 기록이 없습니다.')

    outcomes.markdown('**매도 결과 비중**')
    records = [{'결과': label, '건수': totals[key], '비중': totals[key] / known}
               for label, key in (('이익', 'wins'), ('손실', 'losses'), ('본전', 'flat'))]
    frame = pd.DataFrame(records)
    donut = alt.Chart(frame, mark=alt.MarkDef(type='arc', innerRadius=44, outerRadius=62)).encode(
        theta='건수:Q', color=alt.Color('결과:N', legend=None, scale=alt.Scale(
            domain=['이익', '손실', '본전'], range=[UP, DOWN, FLAT])),
        tooltip=['결과:N', '건수:Q', alt.Tooltip('비중:Q', format='.1%')])
    center = alt.Chart(pd.DataFrame({'표시': [f'{known:,}건']}),
                       mark=alt.MarkDef(type='text', fontSize=22, fontWeight=600)).encode(text='표시:N')
    outcomes.altair_chart(alt.layer(donut, center).properties(height=148), width='stretch',
                          alt='취득원가가 확인된 이익·손실·본전 매도 비중')
    for item, color in zip(records, ('red', 'blue', 'gray')):
        outcomes.markdown(f":{color}[●] {item['결과']}　**{item['건수']:,}건** · {item['비중']:.1%}")
    if loss_rates:
        outcomes.markdown(f":blue[손실 매도 평균 {sc.signed_percent(sum(loss_rates) / len(loss_rates))}]")

    with st.expander('손익 집계 기준'):
        st.caption('체결가 기준 · 수수료·세금 미반영 · 취득원가 미확인 매도는 손익과 수익률 집계에서 제외')
        if any(row['account'].startswith('live-') for row in rows):
            st.caption('실계좌 실현손익은 주문 순서와 주문별 평균체결가를 사용한 추정치입니다.')


def _trades(runtime, keys, start, end, load_names):
    controls = st.container(horizontal=True, vertical_alignment='bottom')
    day = controls.date_input('거래일 (선택)', value=None, min_value=start, max_value=end,
                              key=f'trade_history_day_{start}_{end}') if start != end else None
    if isinstance(day, date):
        start = end = day
    kind = controls.selectbox('구분', ['매수·매도 전체', '매수', '매도'], key='trade_history_side')
    intraday = controls.checkbox('시간 지정', key='trade_history_intraday')
    from_time, to_time = None, None
    if intraday:
        from_time = controls.time_input('시작 시각', time(9, 10), step=60, key='trade_history_from_time')
        to_time = controls.time_input('종료 시각', time(13, 30), step=60, key='trade_history_to_time')
        if from_time is None or to_time is None or from_time > to_time:
            st.warning('종료 시각을 시작 시각 이후로 지정하세요.')
            return
    refresh = controls.button('새 기록 확인', icon=':material/refresh:', key='trade_history_refresh')
    filters = (keys, start, end, from_time, to_time, {'매수': 'BUY', '매도': 'SELL'}.get(kind))
    snapshot = st.session_state.get(SNAPSHOT)
    if refresh or snapshot is None or snapshot.filters != filters:
        try:
            revision = data.freshness_revision(runtime)
            lease, query = data.acquire_query(runtime, filters)
        except (DatabaseError, OSError, ValueError):
            st.error('체결 원장을 읽지 못했습니다. 잠시 뒤 새 기록 확인을 눌러 주세요.')
            return
        snapshot = TradeQuerySnapshot(filters, lease, uuid4().hex, load_names() if len(query) else {}, revision,
                                      datetime.now(KST))
        previous = st.session_state.get(SNAPSHOT)
        st.session_state[SNAPSHOT] = snapshot
        sessions.watch(lease)
        if previous is not None:
            sessions.release(previous.lease)
        st.session_state[CURSOR] = (0, None)
    else:
        try:
            query = data.get_query(snapshot.lease.token)
        except data.QueryExpired:
            st.info('조회 결과가 만료됐습니다. 새 기록 확인을 눌러 주세요.')
            return
    _ledger_freshness(runtime, snapshot.revision, snapshot.checked_at)
    if intraday:
        st.caption('지정 시간은 각 거래일에 적용됩니다. 시각 미확인 거래는 시간 범위 없이 조회할 수 있습니다.')
    _analytics(query, hourly=start == end)
    st.markdown('#### 매수·매도 상세 내역')
    st.caption('한국시간 · 모의는 원장 기록 시각 · 실계좌는 주문 또는 제출 시각 기준')
    if len(query):
        _ledger(snapshot.lease.token, snapshot.names, snapshot.query_id)
        if start == end:
            st.button('이날 매매일지 열기', on_click=_journal, args=(start,), key='trade_history_to_journal')
    else:
        st.info('선택한 조건의 로컬 체결 기록이 없습니다.')


def _realized_overview(runtime, keys, start, end, combine_live):
    try:
        rows = data.iter_selected(data.load(runtime), keys, start, end)
        by_account = data.summary_by_account(rows)
    except (DatabaseError, OSError, ValueError):
        st.warning('계좌별 실현손익 원장을 읽지 못했습니다.')
        return
    scopes = [(LABELS[key], (key,)) for key in keys]
    if combine_live:
        scopes.append(('실계좌 합산', keys))
    values = []
    for label, scope in scopes:
        selected = [by_account[key] for key in scope if key in by_account]
        known = sum(item['known_sells'] for item in selected)
        profit = sum(item['realized'] or 0 for item in selected)
        cost = sum(item['sold_cost'] for item in selected)
        totals = {
            'buy_amount': sum(item['buy_amount'] for item in selected),
            'sell_amount': sum(item['sell_amount'] for item in selected),
            'realized': profit if known else None,
            'return_pct': profit / cost * 100 if cost else None,
            'known_sells': known,
            'sells': sum(item['sells'] for item in selected),
        }
        values.append({'계좌': label, '매수금액': _won(totals['buy_amount']), '매도금액': _won(totals['sell_amount']),
                       '실현손익': _won(totals['realized'], signed=True), '실현수익률': sc.signed_percent(totals['return_pct']),
                       '원가 확인 매도': f"{totals['known_sells']} / {totals['sells']}건"})
    st.subheader('계좌별 실현 성과')
    st.dataframe(pd.DataFrame(values).style.map(sc.cell_tone, subset=['실현손익', '실현수익률']),
                 hide_index=True, width='stretch', alt='계좌별 실현 성과')
    st.caption('기록된 체결가 기준 · 수수료·세금 및 취득원가 미확인 매도 제외')


def _holdings_loader(loader):
    refresh = st.button('보유주식 새로고침', key='trade_history_holdings_refresh', icon=':material/refresh:')

    def load(keys):
        timestamp = monotonic()
        cache = st.session_state.setdefault('trade_history_holdings_results', {})
        missing = tuple(key for key in keys if refresh or key not in cache or timestamp - cache[key][0] > 60)
        if missing:
            snapshots, errors = loader(missing)
            checked_at = monotonic()
            cache.update({key: (checked_at, snapshots.get(key), errors.get(key)) for key in missing})
        return ({key: cache[key][1] for key in keys if cache[key][1] is not None},
                {key: cache[key][2] for key in keys if cache[key][2]})

    return load


def render(runtime, load_names, holdings_loader):
    pending = st.session_state.pop('trade_history_open', None)
    if pending:
        account, _ = pending
        st.session_state['trade_history_scope'] = LABELS[account]
        st.session_state['trade_history_period'] = '오늘'
        st.session_state['trade_history_view'] = '체결·손익'
        st.session_state['trade_history_side'] = '매수·매도 전체'
        st.session_state['trade_history_intraday'] = False
        release_snapshot()
    today = datetime.now(KST).date()
    st.subheader('매매내역')
    view = st.segmented_control('보기', ['자산·성과', '보유주식', '체결·손익'], default='자산·성과',
                                required=True, key='trade_history_view', label_visibility='collapsed')
    if view != '체결·손익':
        release_snapshot()
    loader = None
    with st.container(border=True, horizontal=True, vertical_alignment='bottom'):
        scope = st.segmented_control('계좌', list(SCOPES), default='전체', required=True, key='trade_history_scope')
        if view == '보유주식':
            loader = _holdings_loader(holdings_loader)
            # Keep date widgets' session state while this view hides them.
            for key in ('trade_history_period', 'trade_history_start', 'trade_history_end'):
                if key in st.session_state:
                    st.session_state[key] = st.session_state[key]
            start = end = today
        else:
            start, end = _date_range(today)
    if start > end:
        st.warning('종료일을 시작일 이후로 지정하세요.')
        return
    keys = SCOPES[scope]
    if view == '체결·손익':
        _trades(runtime, keys, start, end, load_names)
    else:
        import trade_history_assets
        combine_live = scope == '실계좌 합산'
        trade_history_assets.render(runtime, keys, start, end, holdings_loader=loader, combine_live=combine_live)
        if view == '자산·성과':
            _realized_overview(runtime, keys, start, end, combine_live)
