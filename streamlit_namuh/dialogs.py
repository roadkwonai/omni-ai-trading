"""Reusable detail pop-ups (st.dialog) for every dashboard tab: wide on large screens, tables as tall as their rows.

open_dialog(title, fn, *args) shows any render function in a pop-up; table_dialog shows rows; the trade / realized /
holdings / alerts / events / services pop-ups are shared by 종합 현황, 운영 상태, 실계좌, AI·실행 기록 and the sidebar."""
from __future__ import annotations

import functools
import inspect
import traceback

import engine_compare as ec
import live_labels as L

WIDTH = 'large'
# Streamlit fixes the dialog's wrapper at 1280px: widen the wrapper too, and auto margins centre it vertically
# (they collapse to 0 when the pop-up is taller than the screen, so nothing is cut off).
CSS = """<style>
div[data-testid="stDialog"]>div{width:min(94vw,1760px)!important;max-width:94vw!important;margin:auto!important}
div[data-testid="stDialog"] [role="dialog"]{width:100%!important;max-width:100%!important;margin:0!important;min-height:min(72vh,900px);
  line-height:1.6;word-break:keep-all}
div[data-testid="stDialog"] [role="dialog"]>div:first-child{border-bottom:1px solid rgba(148,163,184,.25);padding-bottom:10px;margin-bottom:6px}
div[data-testid="stDialog"] [role="dialog"] h2{font-size:1.3rem;font-weight:700;letter-spacing:-.01em}
div[data-testid="stDialog"] [role="dialog"] div[data-baseweb="tab-list"]{gap:4px;flex-wrap:wrap;border-bottom:1px solid rgba(148,163,184,.25)}
div[data-testid="stDialog"] [role="dialog"] button[data-baseweb="tab"]{padding:6px 14px;border-radius:8px 8px 0 0;min-height:36px}
div[data-testid="stDialog"] [role="dialog"] button[data-baseweb="tab"][aria-selected="true"]{background:rgba(139,92,246,.12);font-weight:700}
div[data-testid="stDialog"] [role="dialog"] div[data-baseweb="tab-panel"]{padding-top:10px}
</style>"""


def wide():
    """Stretch the open pop-up to the screen (called inside each dialog)."""
    import streamlit as st
    st.markdown(CSS, unsafe_allow_html=True)


def table_frame(data, **kwargs):
    """st.dataframe sized to its rows (up to about 22 rows before it scrolls), full width by default."""
    import streamlit as st
    rows = len(getattr(data, 'data', data)) if data is not None else 0
    kwargs.setdefault('height', min(38 + 35 * max(rows, 1), 820))
    kwargs.setdefault('width', 'stretch')
    kwargs.setdefault('hide_index', True)
    return st.dataframe(data, **kwargs)


OPEN = 'dialog_open'  # draws the open pop-up again


def dialog(title, icon=None):
    """st.dialog for this dashboard. Streamlit closes a pop-up at the next full rerun, and timers rerun the whole app (a
    finished launchctl or broker read, an AI answer): keep_open() draws it again until the user closes it. st.rerun() in
    the body closes it, as with st.dialog; a widget callback calls close() first."""
    def decorate(fn):
        @functools.wraps(fn)
        def show(*args, **kwargs):
            import streamlit as st
            from streamlit.runtime.scriptrunner import RerunException

            @functools.wraps(fn)
            def body(*args, **kwargs):
                try:
                    fn(*args, **kwargs)
                except RerunException as stop:
                    if not stop.rerun_data.fragment_id_queue and _called_rerun(stop):
                        close()
                    raise

            def draw():
                st.dialog(title, width=WIDTH, icon=icon, on_dismiss=close)(body)(*args, **kwargs)
            st.session_state[OPEN] = draw
            if _drawn():  # one pop-up a run: the new one takes the place of the kept one
                st.rerun()
            draw()
        return show
    return decorate


def close():
    import streamlit as st
    st.session_state.pop(OPEN, None)


def _called_rerun(stop):
    """Streamlit stops a body with the same exception when another rerun is asked for: only st.rerun() has its own frame."""
    import streamlit as st
    rerun = inspect.unwrap(st.rerun).__code__
    return any(frame.f_code is rerun for frame, _ in traceback.walk_tb(stop.__traceback__))


def _drawn():
    from streamlit.runtime.scriptrunner import get_script_run_ctx
    ctx = get_script_run_ctx()
    return bool(ctx and ctx.has_dialog_opened)


def keep_open():
    """Draw the open pop-up again, early in a run (app.py): the browser hides it from the start of a run until it comes."""
    import streamlit as st
    draw = st.session_state.get(OPEN)
    if draw and not _drawn():
        draw()


def open_dialog(title, fn, *args, icon=None, **kwargs):
    """Show fn(*args, **kwargs) in a wide pop-up."""
    @dialog(title, icon=icon)
    def show():
        wide()
        fn(*args, **kwargs)
    show()


def table_dialog(title, rows, note=None, tone=(), config=None, icon=None, empty='표시할 기록이 없습니다.'):
    """A pop-up with one table (list of dicts or DataFrame); `tone` columns are coloured by sign."""
    import pandas as pd
    import streamlit as st

    def body():
        if rows is None or len(rows) == 0:
            st.info(empty)
            return
        frame = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
        cols = [c for c in tone if c in frame.columns]
        table_frame(frame.style.map(_tone, subset=cols) if cols else frame, column_config=config)
        if note:
            st.caption(note)
    open_dialog(title, body, icon=icon)


def buttons(specs, columns=None):
    """A row of '자세히' buttons: specs = [(label, key, callback, args)]; the clicked one opens its pop-up."""
    import streamlit as st
    for column, (label, key, callback, args) in zip(st.columns(columns or len(specs)), specs):
        if column.button(label, key=key, icon=':material/open_in_new:', width='stretch', type='tertiary'):
            callback(*args)


def _tone(v):
    return ('color:#ef4444' if str(v).startswith('+') and v not in ('+0원', '+0.00%')
            else 'color:#3b82f6' if str(v).startswith('-') else '')


def _trade_rows(rows, names):
    import pandas as pd
    return pd.DataFrame([{'시각': r['time'][11:19], '계좌': r['계좌'], '종목': names.get(r['symbol'], r['symbol']),
                          '구분': '매수 ▲' if r['side'] == 'BUY' else '매도 ▼', '수량': f"{r['qty']:,}주", '가격': f"{r['price']:,.0f}원",
                          '수익률': ec._pct(r['return_pct']) if r['return_pct'] is not None else '',
                          '실현손익': ec._won(r['pnl']) if r['pnl'] is not None else '', '판단 경로': r['path'],
                          '판단 근거': r.get('basis', ''), '기록': r.get('source', '원장')}
                         for r in sorted(rows, key=lambda r: r['time'], reverse=True)])


def realized_dialog(title, rows, names):
    import streamlit as st

    @dialog(title)
    def show():
        wide()
        import altair as alt
        import pandas as pd
        sells = [r for r in rows if r['pnl'] is not None]
        if not sells:
            st.info('오늘 매도한 거래가 없습니다.')
            return
        by_account = {}
        for r in sells:
            row = by_account.setdefault(r['계좌'], {'계좌': r['계좌'], '매도': 0, '이익': 0, '손실': 0, '실현손익': 0.0, '판 금액': 0.0})
            row['매도'] += 1
            row['이익'] += r['pnl'] > 0
            row['손실'] += r['pnl'] < 0
            row['실현손익'] += r['pnl']
            row['판 금액'] += r['cost']
        summary = pd.DataFrame([{**v, '수익률': ec._pct(v['실현손익'] / v['판 금액'] * 100 if v['판 금액'] else None),
                                 '실현손익': ec._won(v['실현손익'])} for v in by_account.values()]).drop(columns=['판 금액'])
        table_frame(summary.style.map(_tone, subset=['실현손익', '수익률']))
        frame = pd.DataFrame([{'종목': names.get(r['symbol'], r['symbol']), '손익': r['pnl'], '계좌': r['계좌']} for r in sells])
        frame = frame.groupby(['종목', '계좌'], as_index=False)['손익'].sum()
        st.altair_chart(alt.Chart(frame).mark_bar().encode(
            y=alt.Y('종목:N', sort='-x', title=None), yOffset='계좌:N', x=alt.X('손익:Q', title='원', axis=alt.Axis(format=',.0f')),
            color=alt.condition(alt.datum['손익'] >= 0, alt.value('#ef4444'), alt.value('#3b82f6')),
            tooltip=['계좌', '종목', alt.Tooltip('손익:Q', format='+,.0f')]
        ).properties(height=max(120, 34 * len(frame))), width='stretch')
        table_frame(_trade_rows(sells, names).style.map(_tone, subset=['수익률', '실현손익']))
        st.caption('체결가 기준, 수수료·세금 미반영. 실계좌는 실체결이 없는 날 미러 계산(가상)입니다.')
    show()


def trades_dialog(rows, names):
    import streamlit as st

    @dialog('오늘 거래 전체')
    def show():
        wide()
        if not rows:
            st.info('오늘 거래가 없습니다.')
            return
        accounts = sorted({r['계좌'] for r in rows})
        pick = st.pills('계좌', accounts, selection_mode='multi', default=accounts, key='ov_trades_accounts')
        side = st.segmented_control('구분', ['전체', '매수', '매도'], default='전체', key='ov_trades_side') or '전체'
        shown = [r for r in rows if r['계좌'] in (pick or []) and (side == '전체' or r['side'] == ('BUY' if side == '매수' else 'SELL'))]
        table_frame(_trade_rows(shown, names).style.map(_tone, subset=['수익률', '실현손익']),
                     column_config={'판단 근거': st.column_config.TextColumn(width='large')})
        st.caption(f'{len(shown)}건 · 최신순.')
    show()


def holdings_dialog(items, names):
    import streamlit as st

    @dialog('보유 현황')
    def show():
        wide()
        import pandas as pd
        rows = [{'계좌': i['label'], '종목': names.get(c, c), '수량': f"{h['qty']:,}주",
                 '평균가': f"{h['avg']:,.0f}원" if h['avg'] else '—', '현재가': f"{h['price']:,.0f}원" if h['price'] else '—',
                 '매입금액': ec._won((h['avg'] or 0) * h['qty']).lstrip('+'),
                 '평가손익': ec._won(h['pnl']) if h['pnl'] is not None else '—'} for i in items for c, h in i['holdings'].items()]
        if not rows:
            st.info('지금 보유한 종목이 없습니다.')
            return
        table_frame(pd.DataFrame(rows).style.map(_tone, subset=['평가손익']))
        st.caption('모의는 실행기의 최근 잔고(10분 안), 실계좌는 미러 원장 기준입니다. 증권사 잔고는 트레이딩 현황·실계좌에서 조회합니다.')
    show()


def alerts_dialog(watched, warnings, follow):
    import streamlit as st

    @dialog('실행기 · 주의 사항')
    def show():
        wide()
        import pandas as pd
        if warnings:
            for line in warnings:
                st.warning(line, icon=':material/warning:')
        else:
            st.success('확인이 필요한 항목이 없습니다.', icon=':material/check_circle:')
        st.markdown('**실행기·판단기 상태**')
        table_frame(pd.DataFrame([{'서비스': k, '상태': v} for k, v in sorted(watched.items())]))
        if follow:
            st.markdown('**실계좌 미러 마지막 기록**')
            table_frame(pd.DataFrame([{'계좌': k, '시각': str(e.get('time', ''))[11:19],
                                        '모드': '실주문' if e.get('mode') == 'execute' else '관찰(계획만)',
                                        '계획': L.ACTIONS.get(e.get('action'), e.get('action', '')), '사유': L.reason(e.get('reason')),
                                        '실행': L.EXECUTIONS.get(e.get('execution'), e.get('execution') or '')} for k, e in follow.items()]),
                         hide_index=True, width='stretch')
        st.caption('장 밖에는 실행기가 "종료"인 것이 정상입니다. 자세한 판단·실행 흐름은 "운영 → AI·실행 기록"에서 봅니다.')
    show()


EVENT_NAMES = {'jev_shadow': 'Jev 판단', 'jev_holding_decision': 'Jev 보유 판단', 'v2_entry_gate': '진입 판단', 'v2_execution_gate': '주문 판단',
               'submitted': '매수 주문', 'sell_submitted': '매도 주문', 'split_add': '분할 추가', 'v2_risk_exit': '보호 청산',
               'split_take_profit': '분할 익절', 'manual_exit': '수동 정리', 'market_context': '시장 기록', 'exit_plan': '청산 계획',
               'strategy_settings': '설정 적용', 'started': '시작', 'stopped': '종료', 'watchlist_strength': '관심종목 강도'}


OVERHEAT = {'1': '단기과열 예고', '2': '단기과열 지정', '3': '단기과열 연장'}
ALARM = {'1': '투자주의', '2': '투자경고', '3': '투자주의·위험예고', '4': '투자경고·위험예고', '5': '투자위험'}


def market_warnings(event):
    """Observations stored on a buy (nh_paper_isolated.entry_context): place in the day's range, VI distance, overheating, alarm."""
    out = [f"{label} {event[key]:+.2f}%" for key, label in (('high_gap_pct', '고가 대비'), ('open_gap_pct', '시가 대비'),
                                                           ('vi_up_gap_pct', 'VI 상단까지'), ('vi_down_gap_pct', 'VI 하단까지'))
           if type(event.get(key)) in (int, float)]
    out += [names[event[key]] for key, names in (('overheat_code', OVERHEAT), ('alarm_code', ALARM)) if event.get(key) in names]
    if event.get('vi_halt') in ('1', '2'):
        out.append('VI 발동 중 (' + ('KRX' if event['vi_halt'] == '1' else 'NXT') + ')')
    return out


def event_rows(events, limit=300):
    """Runner journal events as readable rows, newest first (judgments, gates, orders, exits, errors)."""
    from etf_universe import SYMBOLS
    rows = []
    for e in reversed([e for e in events if isinstance(e, dict)][-limit:]):
        kind = e.get('event') or ''
        candidate = ((e.get('jev') or {}).get('candidate') or {}) if kind == 'jev_shadow' else {}
        detail = []
        if candidate:
            detail.append(f"후보 {candidate.get('choice')} {candidate.get('confidence', 0):.2f}")
        for key, label in (('choice', '판단'), ('confidence', '신뢰도'), ('reason', '사유'), ('trigger', '사유'), ('source', '경로'),
                           ('qty', '수량'), ('limit', '지정가'), ('guard_detail', '가드'), ('error_type', '오류')):
            value = e.get(key)
            if value not in (None, '') and not (kind == 'jev_shadow' and key == 'choice'):
                detail.append(f'{label} {value:.2f}' if key == 'confidence' and isinstance(value, float) else f'{label} {value}')
        detail += market_warnings(e)
        if e.get('permitted') is not None:
            detail.insert(0, '허용' if e['permitted'] else '대기')
        rows.append({'시각': str(e.get('time', ''))[5:19].replace('T', ' '), '종류': EVENT_NAMES.get(kind, kind),
                     '종목': SYMBOLS.get(e.get('symbol'), e.get('symbol')) or '', '내용': ' · '.join(map(str, detail))})
    return rows


def events_dialog(title, events, kinds=None, limit=300):
    """Recent runner journal events (optionally only some kinds) in a pop-up."""
    import streamlit as st
    picked = [e for e in events if isinstance(e, dict) and (not kinds or e.get('event') in kinds)]

    def body():
        rows = event_rows(picked, limit)
        if not rows:
            st.info('기록이 없습니다.')
            return
        table_frame(rows, column_config={'내용': st.column_config.TextColumn(width='large')})
        st.caption(f'최근 {len(rows)}건 · 최신순. 원본은 실행기 기록(events.jsonl)입니다.')
    open_dialog(title, body, icon=':material/history:')


def services_dialog(states):
    """Every launchd service with its state and schedule."""
    import streamlit as st

    def body():
        try:
            from automation_status import LABELS
        except ImportError:
            LABELS = {}
        try:
            from launchd_services import schedule_text
        except ImportError:
            schedule_text = None
        rows = []
        for name, state in sorted(states.items()):
            service = str(LABELS.get(name, '')).replace('com.vibetrading.', '')
            when = ''
            if schedule_text and service:
                try:
                    when = schedule_text(service) or '계속 실행'
                except Exception:
                    when = ''
            rows.append({'서비스': name, '상태': state, '시작 시각': when, 'launchd': service})
        tone = lambda v: 'color:#22c55e' if v == '실행 중' else 'color:#f59e0b' if v == '미등록' else 'color:#f43f5e' if v not in ('종료', '') else ''
        import pandas as pd
        table_frame(pd.DataFrame(rows).style.map(tone, subset=['상태']))
        st.caption('시간에 맞춰 한 번 도는 작업은 끝나면 "종료"가 정상입니다. 미등록은 재부팅 뒤 자동으로 올라오지 않습니다.')
    open_dialog('자동 작업 상태', body, icon=':material/dns:')


def rules_dialog(settings, labels, helps=None):
    """Every strategy setting now in force, grouped, with its plain-language meaning."""
    import streamlit as st

    def body():
        table_frame([{'항목': labels.get(k, k), '지금 값': f'{v:g}' if isinstance(v, (int, float)) else v,
                      '뜻': ' '.join((helps or {}).get(k, ('', '')))} for k, v in settings.items() if k in labels],
                    column_config={'뜻': st.column_config.TextColumn(width='large')})
        st.caption('모의 실행기가 매 주기(약 1분) 다시 읽는 매매 기준입니다. 계좌·자동매매 → 모의계좌 탭에서 바꿉니다.')
    open_dialog('지금 적용 중인 매매 기준', body, icon=':material/rule:')


def body_dialog(title, fn, *args, icon=None):
    """Any existing panel function drawn inside a wide pop-up (reuses the same rendering as the page)."""
    def body():
        fn(*args)
    open_dialog(title, body, icon=icon)


def live_events_dialog(title, events, labels, limit=200):
    """Live mirror plans (nh_live_follow journal) in Korean, newest first."""
    import streamlit as st

    def body():
        rows = [{'시각': str(e.get('time', ''))[5:19].replace('T', ' '), '계좌': e.get('account', ''),
                 '모드': '실주문' if e.get('mode') == 'execute' else '계획만', '판단': labels.ACTIONS.get(e.get('action'), e.get('action', '')),
                 '종목': e.get('symbol') or '', '사유': labels.reason(e.get('reason')),
                 '실행': labels.EXECUTIONS.get(e.get('execution'), e.get('execution') or '')}
                for e in reversed([e for e in events if isinstance(e, dict)][-limit:])]
        if not rows:
            st.info('실행 기록이 없습니다.')
            return
        table_frame(rows, column_config={'사유': st.column_config.TextColumn(width='large')})
        st.caption(f'최근 {len(rows)}건 · 최신순. 원본은 live-follow.jsonl입니다. "계획만"은 주문을 내지 않은 기록입니다.')
    open_dialog(title, body, icon=':material/sync_alt:')


def account_dialog(key, record, mode=None, settings=None):
    """One account's saved values and its limit flow diagram."""
    import streamlit as st
    import settings_view as sv

    def body():
        left, right = st.columns([3, 2])
        with left:
            table_frame([{'항목': k, '값': v} for k, v in sv.account_rows(record, mode)])
            st.caption('계좌·자동매매 탭의 저장값입니다. 주문 가드와 실행기가 같은 값을 읽습니다.')
        with right:
            import theme as THEME
            THEME.graphviz(sv.limits_dot(record, settings, mode), width='stretch')
    open_dialog(f"{record.get('label', key)} 설정 자세히", body, icon=':material/tune:')
