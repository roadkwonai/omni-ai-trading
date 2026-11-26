"""Read-only account balances and holdings for the trade-history dashboard."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st
import status_cards as sc

from trade_history_data import balances

LABELS = {'paper-shared': '모의매매', 'live-1': '실계좌 1', 'live-2': '실계좌 2'}
KST = ZoneInfo('Asia/Seoul')


def _kst_naive(value):
    stamp = datetime.fromisoformat(value)
    return stamp.astimezone(KST).replace(tzinfo=None) if stamp.tzinfo else stamp


def balance_history(runtime, keys, start, end, *, intraday=False):
    rows = []
    for key in keys:
        observations = [(_kst_naive(row['time']).date().isoformat(), row) for row in balances(runtime, key)]
        if not intraday:
            observations = list(dict(observations).items())
        for day, row in observations:
            if start.isoformat() <= day <= end.isoformat():
                rows.append({'계좌': LABELS.get(key, key), '시각': row['time'], '날짜': day,
                             '총자산': row['total_assets'], '보유 평가액': row.get('evaluation')})
    return rows


def combined_live(rows):
    """Combine same-day observations at most five minutes apart."""
    return _combined(rows, ('실계좌 1', '실계좌 2'), '실계좌 합산')


def _combined(rows, names, label):
    by_day = {}
    for row in rows:
        if row['계좌'] in names:
            by_day.setdefault(row['날짜'], []).append(row)
    result = []
    for day, observations in sorted(by_day.items()):
        latest = {}
        ordered = sorted(observations, key=lambda row: _kst_naive(row['시각']))
        for row in ordered:
            latest[row['계좌']] = row
            if len(latest) != len(names):
                continue
            selected = [latest[name] for name in names]
            times = [_kst_naive(item['시각']) for item in selected]
            if (max(times) - min(times)).total_seconds() > 300:
                continue
            evaluation = [_number(item.get('보유 평가액')) for item in selected]
            combined = {'계좌': label, '날짜': day, '시각': row['시각'],
                        '총자산': sum(item['총자산'] for item in selected),
                        '보유 평가액': sum(evaluation) if all(value is not None for value in evaluation) else None}
            if result and result[-1]['날짜'] == day and _kst_naive(result[-1]['시각']) == _kst_naive(row['시각']):
                result[-1] = combined
            else:
                result.append(combined)
    return result


def _money(value):
    return f'{value:,.0f}원' if value is not None else '—'


def assets(runtime, keys, start, end, combine_live=False):
    single_day = start == end
    combine_all = set(keys) == set(LABELS)
    combine = combine_live or combine_all
    try:
        observations = balance_history(runtime, keys, start, end, intraday=single_day or combine)
    except OSError:
        st.error('잔고 기록을 읽지 못했습니다. 잠시 뒤 다시 조회해 주세요.')
        return
    rows = observations if single_day or not combine else list(
        {(row['계좌'], row['날짜']): row for row in observations}.values()
    )
    if combine_live:
        pairs = combined_live(observations)
        rows.extend(pairs if single_day else {row['날짜']: row for row in pairs}.values())
    if combine_all:
        pairs = _combined(observations, tuple(LABELS.values()), '전체 합계 (모의·실계좌)')
        rows.extend(pairs if single_day else {row['날짜']: row for row in pairs}.values())
    st.subheader('자산·성과')
    interval = '시간대별 관측값' if single_day else '일별 마지막 관측값'
    st.caption(f'선택 기간의 {interval}입니다. 입출금 내역이 확인되지 않아 투자 수익률은 산출하지 않습니다.')
    if not rows:
        st.info('선택 기간에 저장된 잔고 기록이 없습니다.')
        if combine_all:
            st.caption('전체 합계 (모의·실계좌)는 세 계좌의 같은 날 5분 이내 잔고가 있어야 산출합니다.')
        return
    table = []
    names = [LABELS.get(key, key) for key in keys]
    if combine_live:
        names.append('실계좌 합산')
    if combine_all:
        names.append('전체 합계 (모의·실계좌)')
    for name in names:
        values = [r for r in rows if r['계좌'] == name]
        if not values:
            table.append({'계좌': name, '시작 총자산': None, '마지막 총자산': None, '잔고 변동': None,
                          '잔고 변동률(%)': None, '시작 기준': '기록 없음', '마지막 기준': '기록 없음'})
            continue
        first, last = values[0], values[-1]
        change = last['총자산'] - first['총자산']
        table.append({'계좌': name, '시작 총자산': first['총자산'], '마지막 총자산': last['총자산'],
                      '잔고 변동': change, '잔고 변동률(%)': change / first['총자산'] * 100 if first['총자산'] else None,
                      '시작 기준': first['시각'], '마지막 기준': last['시각']})
    with st.grid('auto', min_column_width=255, gap='small') as cards:
        for row in table:
            rate = row['잔고 변동률(%)']
            change = row['잔고 변동']
            stamp = row['마지막 기준']
            when = f"{_kst_naive(stamp):%m/%d %H:%M} KST" if stamp != '기록 없음' else stamp
            delta = f"{change:+,.0f}원 · {sc.signed_percent(rate)}" if change is not None else '확인 불가'
            with cards.cell():
                sc.card(st, label=f"{row['계좌']} · 마지막 총자산", value=_money(row['마지막 총자산']),
                        tone=sc.signed(change) if change is not None else 'off', icon='account_balance_wallet',
                        sub=f'잔고 변동 {delta} · {when}', signed_sub=True)
    with st.expander('시작·종료 잔고 대조'):
        details = pd.DataFrame(table)
        details_style = details.style.map(_numeric_tone, subset=['잔고 변동', '잔고 변동률(%)'])
        st.dataframe(details_style, hide_index=True, width='stretch', alt='선택 기간의 계좌별 총자산')
        st.caption('잔고 변동에는 입출금과 평가손익이 포함됩니다. 기록이 없는 날짜는 추정하지 않습니다.')
    frame = pd.DataFrame(rows)
    frame['날짜'] = pd.to_datetime(frame['날짜'])
    frame['관측시각'] = frame['시각'].map(_kst_naive)
    axis = alt.X('관측시각:T', title='관측 시각 (한국 시간)', axis=alt.Axis(format='%H:%M')) if single_day else alt.X('날짜:T', title='관측일')
    chart = alt.Chart(frame, mark=alt.MarkDef(type='line', point=True)).encode(
        x=axis, y=alt.Y('총자산:Q', title='총자산 (원)', scale=alt.Scale(zero=False)),
        color=alt.Color('계좌:N', title=None),
        tooltip=['계좌:N', '시각:N', alt.Tooltip('총자산:Q', format=',.0f')]).properties(height=280)
    latest = {row['계좌']: row for row in rows}
    composition = latest.get('실계좌 합산') if combine_live else latest.get(names[0]) if len(names) == 1 else None
    invested = _number(composition.get('보유 평가액')) if composition else None
    if composition and invested is not None and 0 <= invested <= composition['총자산'] and composition['총자산'] > 0:
        trend, mix = st.columns([2, 1])
        trend.altair_chart(chart, width='stretch', alt='계좌별 일자별 총자산 추이')
        allocation = pd.DataFrame({'구분': ['보유 평가액', '현금 등'],
                                   '금액': [invested, composition['총자산'] - invested]})
        donut = alt.Chart(allocation, mark=alt.MarkDef(type='arc', innerRadius=48)).encode(
            theta='금액:Q', color='구분:N', tooltip=['구분:N', alt.Tooltip('금액:Q', format=',.0f')]
        ).properties(title=f"{composition['계좌']} 자산 구성", height=200)
        mix.altair_chart(donut, width='stretch', alt='총자산 중 보유 평가액과 나머지 비중')
        mix.caption(f"{composition['시각']} · 현금 등은 총자산과 보유 평가액의 차이")
    else:
        if frame['계좌'].nunique() > 1:
            separate = alt.FacetChart(
                data=frame, spec=chart.properties(height=100),
                facet=alt.FacetMapping(row=alt.Row('계좌:N', title=None, header=alt.Header(labelAngle=0))),
                resolve=alt.Resolve(scale=alt.ScaleResolveMap(y='independent')),
            )
            st.altair_chart(separate, width='stretch', alt='계좌마다 세로축을 따로 쓴 총자산 추이')
        else:
            st.altair_chart(chart, width='stretch', alt='계좌별 총자산 추이')
    mode = st.radio('추이 분석', ['잔고 변동률', '관측 고점 대비 낙폭'], horizontal=True, key='history_balance_mode')
    frame = frame.sort_values(['계좌', '관측시각']).copy()
    grouped = frame.groupby('계좌')['총자산']
    if mode == '잔고 변동률':
        base = grouped.transform('first').replace(0, float('nan'))
        frame['변동률(%)'] = (frame['총자산'] / base - 1) * 100
    else:
        peak = grouped.cummax().replace(0, float('nan'))
        frame['변동률(%)'] = (frame['총자산'] / peak - 1) * 100
    percent_data = frame.dropna(subset=['변동률(%)'])
    percent_data = percent_data.assign(
        상태=percent_data['변동률(%)'].map(lambda value: '증가' if value > 0 else '감소' if value < 0 else '변동 없음'),
        표시=percent_data['변동률(%)'].map(sc.signed_percent))
    percent = alt.Chart(percent_data, mark='line').encode(
        x=axis, y=alt.Y('변동률(%):Q', title=mode + ' (%)'),
        color=alt.Color('계좌:N', title=None),
        tooltip=['계좌:N', '시각:N', alt.Tooltip('표시:N', title=mode)]).properties(height=220)
    points = alt.Chart(percent_data, mark=alt.MarkDef(type='point', filled=True, size=55)).encode(
        x=axis, y=alt.Y('변동률(%):Q'),
        color=alt.Color('상태:N', legend=None, scale=alt.Scale(domain=['증가', '감소', '변동 없음'],
                                                              range=[sc.UP_COLOR, sc.DOWN_COLOR, '#64748b'])),
        tooltip=['계좌:N', '시각:N', alt.Tooltip('표시:N', title=mode)])
    trend = alt.LayerChart(layer=[percent, points], resolve=alt.Resolve(scale=alt.ScaleResolveMap(color='independent')))
    st.altair_chart(trend, width='stretch', alt='계좌별 잔고 변동률 또는 관측 고점 대비 낙폭')
    st.caption('입출금이 포함된 잔고의 관측값 기준이며, 관측되지 않은 시간대의 고점은 반영되지 않습니다.')
    if combine_live:
        st.caption('실계좌 합산은 같은 날 두 계좌 관측 시각 차이가 5분 이내인 기록으로 계산합니다.')
    if combine_all:
        st.caption('전체 합계는 모의매매와 실계좌 1·2를 합산하며, 같은 날 세 계좌 관측 시각 차이가 5분 이내인 기록만 사용합니다.')


def _number(value):
    try:
        number = float(str(value).replace(',', ''))
        return number if pd.notna(number) and abs(number) != float('inf') else None
    except (ValueError, TypeError):
        return None


def _numeric_tone(value):
    number = _number(value)
    if number is None or number == 0:
        return ''
    return f"color:{sc.UP_COLOR if number > 0 else sc.DOWN_COLOR}"


def holding_rows(snapshots):
    """Normalize the broker snapshot while retaining missing prices and costs as missing."""
    result = []
    for key, snapshot in snapshots.items():
        total = _number((snapshot.get('account') or {}).get('총자산'))
        for row in snapshot.get('holdings') or []:
            qty = _number(row.get('보유수량'))
            if qty is None or qty <= 0:
                continue
            avg, current = _number(row.get('평균매입가')), _number(row.get('현재가'))
            value, pnl = _number(row.get('평가금액')), _number(row.get('평가손익'))
            rate = _number(row.get('수익률(%)'))
            result.append({'계좌': LABELS.get(key, key), '종목': row.get('종목') or row.get('종목코드') or '이름 없음',
                           '종목코드': row.get('종목코드'), '수량': qty, '평균매입가': avg, '현재가': current,
                           '평가금액': value, '평가손익': pnl, '수익률(%)': rate,
                           '총자산 비중(%)': value / total * 100 if value is not None and total is not None and total > 0 else None,
                           '기준시각': snapshot.get('time').astimezone(KST).strftime('%m/%d %H:%M:%S') if isinstance(snapshot.get('time'), datetime) and snapshot['time'].tzinfo else str(snapshot.get('time') or '알 수 없음')})
    return result


def holdings(keys, loader):
    st.subheader('보유주식')
    st.caption('증권사 잔고의 현재 관측값입니다. 기간 필터와 별개로 조회 시각을 따릅니다.')
    snapshots, errors = loader(keys)
    for error in errors.values():
        st.warning(str(error))
    missing = [LABELS.get(key, key) for key in keys if key not in snapshots]
    if missing:
        st.info('조회되지 않은 계좌: ' + ', '.join(missing))
    rows = holding_rows(snapshots)
    if snapshots:
        with st.grid('auto', min_column_width=300, gap='small') as cards:
            for key, snapshot in snapshots.items():
                account = snapshot.get('account') or {}
                total, evaluation = _number(account.get('총자산')), _number(account.get('총평가금액'))
                invested = _number(account.get('총매입금액'))
                gain = evaluation - invested if evaluation is not None and invested is not None else None
                rate = gain / invested * 100 if gain is not None and invested and invested > 0 else None
                with cards.cell():
                    stamp = snapshot.get('time')
                    if isinstance(stamp, datetime):
                        stamp = stamp.astimezone(KST) if stamp.tzinfo else stamp.replace(tzinfo=KST)
                        stamp = stamp.strftime('%m/%d %H:%M')
                    sc.card(st, label=f"{LABELS.get(key, key)} · 평가손익",
                            value=f'{gain:+,.0f}원' if gain is not None else '확인 불가',
                            tone=sc.signed(gain) if gain is not None else 'off', icon='monitoring',
                            sub=f"보유 수익률 {sc.signed_percent(rate)} · 기준 {stamp or '알 수 없음'} KST",
                            signed_sub=True)
                    st.markdown(f"총자산 **{_money(total)}**")
                    left, right = st.columns(2)
                    left.caption(f'보유 평가액 {_money(evaluation)}')
                    right.caption(f'매입금액 {_money(invested)}')
                    if total is not None and evaluation is not None and 0 <= evaluation <= total and total > 0:
                        fraction = evaluation / total
                        st.progress(fraction, text=f'보유 비중 {fraction:.2%} · 현금 등 {1 - fraction:.2%}')
                    else:
                        st.caption('자산 구성 확인 불가')
        st.caption('현금 등은 총자산에서 보유 평가액을 뺀 금액이며 주문가능금액과 다를 수 있습니다.')
    if not rows:
        st.info('조회된 보유 종목이 없습니다.' if snapshots else '보유 종목을 조회하지 못했습니다.')
        return
    frame = pd.DataFrame(rows)
    st.markdown('##### 계좌별 보유 종목')
    choice = st.selectbox('정렬', ['평가금액 큰 순', '평가손익 낮은 순', '수익률 높은 순'], key='history_holdings_sort')
    column, ascending = {'평가금액 큰 순': ('평가금액', False), '평가손익 낮은 순': ('평가손익', True),
                         '수익률 높은 순': ('수익률(%)', False)}[choice]
    frame = frame.sort_values(column, ascending=ascending, na_position='last')
    holding_style = frame.style.map(_numeric_tone, subset=['평가손익', '수익률(%)'])
    holding_style = holding_style.format({'평가손익': '{:+,.0f}원', '수익률(%)': sc.signed_percent,
                                          '수량': '{:g}',
                                          '평균매입가': '{:,.0f}원', '현재가': '{:,.0f}원',
                                          '평가금액': '{:,.0f}원', '총자산 비중(%)': '{:.2f}%'},
                                         na_rep='—')
    st.dataframe(holding_style, hide_index=True, width='stretch', alt='계좌별 보유 종목과 평가손익')
    known = frame.dropna(subset=['평가손익'])
    if not known.empty:
        by_stock = known.groupby(['계좌', '종목'], as_index=False)['평가손익'].sum()
        by_stock['계좌 · 종목'] = by_stock['계좌'] + ' · ' + by_stock['종목']
        by_stock['표시'] = by_stock['평가손익'].map(lambda value: f'{value:+,.0f}원')
        limit = max(abs(value) for value in by_stock['평가손익']) or 1
        chart = alt.Chart(by_stock).encode(
            y=alt.Y('계좌 · 종목:N', sort='-x', title=None, axis=alt.Axis(labelLimit=300, labelOverlap=False)),
            x=alt.X('평가손익:Q', title='평가손익 (원)', scale=alt.Scale(domain=[-1.5 * limit, 1.5 * limit]),
                    axis=alt.Axis(tickCount=5)),
            color=alt.condition(alt.datum['평가손익'] >= 0, alt.value('#ef4444'), alt.value('#3b82f6')),
            tooltip=['계좌:N', '종목:N', alt.Tooltip('평가손익:Q', title='평가손익', format=',.0f')])
        bars = chart.properties(mark='bar')
        positive = chart.encode(text='표시:N').properties(mark=alt.MarkDef(type='text', align='left', dx=4)).transform_filter(alt.datum['평가손익'] >= 0)
        negative = chart.encode(text='표시:N').properties(mark=alt.MarkDef(type='text', align='right', dx=-4)).transform_filter(alt.datum['평가손익'] < 0)
        height = max(180, len(by_stock) * 32 + 65)
        st.altair_chart((bars + positive + negative).properties(height=height),
                        width='stretch', height=height, alt='종목별 보유 평가손익')
    st.caption('평가손익과 수익률은 증권사 응답 기준입니다. 비용 포함 여부는 증권사 응답을 확인해야 합니다.')


def render(runtime, account_keys, start, end, holdings_loader=None, *, combine_live=False):
    if holdings_loader is None:
        assets(runtime, account_keys, start, end, combine_live=combine_live)
    else:
        holdings(account_keys, holdings_loader)
