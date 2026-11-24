"""'장기투자' tab (docs/vibe-ui-redesign.md 3): status line, today's decision in one sentence, KPIs, then sub-tabs for the
overview, strategy settings (mode, master modules), market regime, theme stories, portfolio and orders, the decision
log and the run settings of scripts/vibe_longterm.py. Colours always come with a text label; charts have a table."""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from report_figures import for_streamlit  # report callouts/highlights in st.markdown

import longterm_flow as LF
import longterm_watchlist
import status_cards as sc
import theme as THEME
import vibe_longterm as L
from nh_paper_isolated import is_trading_day as trading_day  # the runner's KRX holiday table, cached once per process
from vibe_longterm_replay import max_drawdown

REGIME_TONE = {'panic': 'alert', 'bear': 'down', 'correction': 'warn', 'recovery': 'ok', 'overheat': 'up', 'bull': 'up'}
REGIME_COLOR = {'panic': '#f43f5e', 'bear': '#3b82f6', 'correction': '#f59e0b', 'recovery': '#22c55e', 'overheat': '#ef4444', 'bull': '#a855f7'}
STORY_TONE = {'intact': 'ok', 'watch': 'warn', 'broken': 'alert'}
ROLES = {'core': '코어', 'sat': '위성', 'reservoir': '저수지'}
ORDER_KINDS = ('plan', 'order', 'dca')  # decisions that carry orders
GROUPS = [('기본', ('bogle_core', 'dalio_rebalance')), ('방어', ('yeo_reservoir', 'marks_pendulum', 'buffett_lowturn')),
          ('추세·기회', ('williams_trend', 'williams_rebound')), ('판단', ('lynch_story',))]
EXECUTE_PHRASE = '장기투자 가상 체결'
CARD_TUNING = {'dalio_rebalance': ('band',), 'buffett_lowturn': ('monthly_sell_share',)}  # engine numbers owned by a master (S1)
EXECUTION_TUNING = ('daily_buy_share', 'deploy_weeks')  # 집행 속도: no master's, the book's pace
AUTO_PHRASE = '자동 실제 적용'


def module_label(key):
    master, title = L.MODULES[key][:2]
    return f'{master} · {title}'


def pct(v, signed=False):
    return '—' if v is None else (f'{v * 100:+.1f}%' if signed else f'{v * 100:.1f}%')


def load(root):
    """(state, decisions rows as dicts, newest first) read-only; empty when the engine has not run yet."""
    path = Path(root) / 'longterm.sqlite3'
    if not path.exists():
        return {}, []
    from sqlite_ro import ro_connect
    with ro_connect(path) as db:
        cols = ('time', 'day', 'kind', 'regime', 'raw_regime', 'mode', 'modules', 'recommended', 'indicators', 'targets', 'orders', 'reason')
        decisions = [dict(zip(cols, r)) for r in db.execute(f"SELECT {', '.join(cols)} FROM decisions ORDER BY rowid DESC LIMIT 500")]
    return L.read_state(root), decisions


def nav_history(root, start='', end='9999-12-31'):
    """([(day, book, value)] of the books, {day: 적립식 money paid in by then}) between two days, oldest first. Every NAV
    reader goes through here so the engine's 'contributed' row never shows as a book."""
    path = Path(root) / 'longterm.sqlite3'
    if not path.exists():
        return [], {}
    from sqlite_ro import ro_connect
    with ro_connect(path) as db:
        rows = db.execute('SELECT day, book, value FROM nav WHERE day BETWEEN ? AND ? ORDER BY day', (start, end)).fetchall()
    return [r for r in rows if r[1] != L.CONTRIBUTED], {d: v for d, b, v in rows if b == L.CONTRIBUTED}


def holdings(root):
    """{code: (qty, avg)} from the virtual account's fills (vibe_longterm.journal_rows) and its cash, read-only."""
    last = {r['symbol']: (r['after'], r['avg']) for r in L.journal_rows(root)}
    path = Path(root) / 'virtual-account.sqlite3'
    cash = None
    if path.exists():
        from sqlite_ro import ro_connect
        with ro_connect(path) as db:
            row = db.execute("SELECT value FROM account WHERE name='cash'").fetchone()
            cash = row[0] if row else None
    return {s: v for s, v in last.items() if v[0] > 0}, cash


# The tabs (docs/longterm-strategy-settings-redesign.md S4, 2026-11-18): every view's (tab, sub-view); the old tab names
# stay the views' names, so a link or a test that named one still finds it (goto)
PLACES = {'개요': ('개요', None), '시황·국면': ('시장', '시황·국면'), '테마 스토리': ('시장', '테마 스토리'), '차트': ('시장', '차트'),
          '전략 설정': ('전략', '거장 모듈·비중'), '적립식': ('전략', '적립식'), '레시피 비교': ('전략', '레시피 비교'),
          '실행': ('계좌·실행', '실행'), '포트폴리오·주문': ('계좌·실행', '포트폴리오·주문'), '기록': ('계좌·실행', '기록'),
          '전략 리포트': ('리포트·감사', '전략 리포트'), '감사·개선안': ('리포트·감사', '감사·개선안'),
          '미국 종목 아이디어': ('미국 종목 아이디어', None), '변경 이력': ('변경 이력', None)}
PLACES['운용 설정'] = PLACES['실행']  # S1 split the old 운용 설정: its run switches are 실행, its numbers moved to 전략 and 시장


def goto(state, name):
    """Open view `name` (an old tab name or a sub-view) on the next run: sets the tab and its sub-view in session state."""
    tab, sub = PLACES[name]
    state['lt_tab'] = tab
    if sub:
        state[f'lt_sub_{tab}'] = sub


def save(root, settings, message):
    try:
        L.save_settings(root, settings)
    except (ValueError, OSError) as exc:  # a refused value, or the settings file could not be written
        st.error(f'저장하지 못했습니다: {exc}', icon=':material/error:')
        return
    st.toast(message, icon=':material/check_circle:')
    st.rerun()


def render(root=None, chart=None):
    """chart: the dashboard's candle chart function (app.tradingview_chart), passed in to avoid importing app."""
    root = Path(root or L.ROOT)
    settings = L.load_settings(root)
    state, decisions = load(root)
    nav, contributed = nav_history(root)
    plan = state.get('plan') or {}
    regime = plan.get('regime')
    recommended = plan.get('recommended') or L.recommended_set('bull')
    regime_state = state.get('regime') or {}
    history = settings_history()
    st.caption('KRX 상장 미국 지수·테마 ETF를 장기·스윙으로 운용하는 별도 가상 계좌입니다. Jev와 독립이며 Jev 자료는 하루 요약만 참고합니다. '
               '설계 docs/vibe-longterm-plan.md · 화면 docs/vibe-ui-redesign.md')
    status_line(settings, plan, regime, regime_state, datetime.now(L.KST))
    import longterm_inputs_guide as IG
    IG.button('lt_inputs_top', label='판단 근거 보기 · 장기 엔진은 무엇을 보고 사나')
    if plan.get('why'):
        today_sentence(plan, settings, state)
    else:
        st.info('첫 판단은 평일 08:45입니다. 그 전에는 "시황·국면" 탭에서 국면 기준을, "전략 설정"에서 모드와 거장 모듈을 정할 수 있습니다.',
                icon=':material/schedule:')
    kpis(nav, contributed)

    def dca():  # 2026-11-17: 적립식 per book (longterm_dca)
        import longterm_dca_view
        longterm_dca_view.render(root, settings)

    def ideas():  # 2026-11-18: US stock ideas, reference only (us_ideas)
        import us_ideas_view
        us_ideas_view.render(root, chart)
    def audit():  # 2026-11-18: weekly and monthly audit reports and today's check of the decision log (longterm_audit)
        import longterm_audit_view
        longterm_audit_view.render(root)
    views = {'개요': lambda: (overview(state, decisions, nav, contributed, settings), next_levels(plan)),
             '시황·국면': lambda: market(root, plan, regime_state, decisions, settings), '테마 스토리': lambda: stories(state, decisions),
             '차트': lambda: chart_tab(root, plan, chart, settings, state, decisions),
             '전략 설정': lambda: strategy(root, settings, plan, recommended, history), '적립식': dca,
             '레시피 비교': lambda: recipe_view(root, settings, plan, nav, contributed),
             '실행': lambda: run_panel(root, settings), '포트폴리오·주문': lambda: portfolio(root, state, decisions), '기록': lambda: record(decisions),
             '전략 리포트': lambda: report_tab(root), '감사·개선안': audit,
             '미국 종목 아이디어': ideas, '변경 이력': lambda: history_view(history)}
    groups = {}
    for name, view in views.items():
        groups.setdefault(PLACES[name][0], {})[PLACES[name][1]] = view
    for tab, (name, subs) in zip(st.tabs(list(groups), key='lt_tab', on_change='rerun'), groups.items()):
        if tab.open:  # only the open tab runs (each reads files, the chart fetches Yahoo), and in it only the chosen view
            with tab:
                if len(subs) == 1:
                    next(iter(subs.values()))()
                    continue
                st.session_state.setdefault(f'lt_sub_{name}', next(iter(subs)))
                import longterm_strategy_guide as G
                pick = st.segmented_control(f'{name} 보기', list(subs), key=f'lt_sub_{name}', label_visibility='collapsed',
                                            help=G.SUB_HELP.get(name)) or next(iter(subs))
                subs[pick]()


def status_line(settings, plan, regime, regime_state, now):
    pending = regime_state.get('pending')
    sc.row([
        {'label': '확정 국면', 'value': L.REGIMES[regime][0] if regime else '판정 전', 'tone': REGIME_TONE.get(regime, 'off'), 'icon': 'public',
         'sub': (f"{regime_state.get('since', '')}부터 · " if regime else '') +
                (f"{L.REGIMES[pending][0]} 확정 대기 {regime_state.get('pending_days', 0)}/{LF.confirm_need(pending, settings)}일" if pending else '대기 없음')},
        *LF.status({'settings': settings, 'plan': plan}, now)[:3],  # 운영 스위치 · 모드 · 실행 방식 (전략 구조 → 장기투자와 같은 카드)
        {'label': '마지막 판단', 'value': (plan.get('time') or '—')[5:16].replace('T', ' '), 'tone': 'off', 'icon': 'history',
         'sub': '평일 08:45 판단 · 09:40 주문 · 15:40 기록'},
    ], ask='longterm')


def today_sentence(plan, settings, state):
    regime = plan['regime']
    recommended, active = set(plan.get('recommended') or []), set(plan.get('active') or [])
    names = lambda keys: ', '.join(L.MODULES[m][0] for m in sorted(keys))  # noqa: E731
    applied = settings['mode'] == 'auto' and settings['auto_live']
    rec = '추천 모듈이 지금 켠 모듈과 같습니다' if recommended == active else ' · '.join(
        [f'{names(recommended - active)} 추천'] * bool(recommended - active) + [f'{names(active - recommended)} 추천 밖'] * bool(active - recommended)
        + ['자동 실제 적용 중' if applied else f"{L.MODES[settings['mode']]} 모드라 미적용"])
    last = state.get('last_plan') or {}
    plan_text = ''
    if last.get('day') == plan['day']:
        orders = last.get('orders') or []
        plan_text = f" · 오늘 계획: {L.why_label(last.get('why'))}" + (f", 주문 {len(orders)}건" if orders else ', 주문 없음')
    ind = plan.get('indicators') or {}
    st.info(f"**{plan['day']}** {L.REGIMES[regime][0]} 국면(나스닥100 낙폭 {pct(ind.get('dd'))} · 200일선 대비 {pct(ind.get('gap'), True)} · "
            f"VIX {ind.get('vix', 0):.1f}) — {rec}{plan_text}", icon=':material/lightbulb:')


def drawdowns(nav, contributed):
    """{book: deepest fall from its running peak}, measured on NAV less the 적립식 money paid in by that day, so a deposit
    is neither a peak nor a recovery (the shadows get the same contributions)."""
    series = {}
    for day, book, value in sorted(nav):
        series.setdefault(book, []).append(value - contributed.get(day, 0))
    return {book: max_drawdown(values) for book, values in series.items()}


def kpis(nav, contributed):
    if not nav:
        return
    frame = pd.DataFrame(nav, columns=['day', 'book', 'value'])
    latest = frame.groupby('book')['value'].last().to_dict()
    paid = contributed[max(contributed)] if contributed else 0  # written with the books at each close
    items = [{'label': f'순자산 · {L.BOOK_LABELS[b]}', 'value': f"{latest[b]:,.0f}원", 'icon': icon, 'tone': 'up' if L.profit(latest[b], paid) >= 0 else 'down',
              'sub': f"{L.profit(latest[b], paid) / (L.CAPITAL + paid) * 100:+.2f}%"}
             for b, icon in (('actual', 'account_balance_wallet'), ('base', 'shield'), ('auto', 'auto_mode')) if b in latest]
    if paid:
        items.append({'label': '적립 누계', 'value': f'{paid:,.0f}원', 'icon': 'savings', 'tone': 'off', 'sub': '적립식으로 넣은 돈 · 손익에서 뺌'})
    if 'actual' in latest and 'base' in latest:
        gap = latest['actual'] / latest['base'] - 1
        items.append({'label': '기본 그림자 대비', 'value': f'{gap * 100:+.2f}%p', 'tone': 'up' if gap >= 0 else 'down', 'icon': 'compare_arrows',
                      'sub': '실제가 기본보다 나으면 + · 수수료 같고 세금 제외 · 실제는 호가 체결, 그림자는 종가+0.05%'})
    if 'actual' in latest:
        items.append({'label': '최대 낙폭 · 실제', 'value': pct(drawdowns(nav, contributed)['actual']), 'tone': 'off', 'icon': 'trending_down'})
    sc.row(items, ask='longterm_kpi')


# ---------- 개요 ----------

def weights_frame(state, band=None):
    """Current vs target weight per ETF with the rebalancing band (target ± band, %p) and whether it is outside."""
    last = state.get('last_plan') or {}
    want, weights = last.get('want') or {}, last.get('weights') or {}
    if not want:
        return None
    band = (band if band is not None else ((state.get('plan') or {}).get('tuning') or {}).get('band', L.TUNING['band'][0])) * 100
    rows = []
    for k in list(L.ASSETS) + [k for k in {**weights, **want} if k not in L.ASSETS]:  # 여운봉 매매법 ETF (0261H0 등)
        now, target = round((weights.get(k) or 0) * 100, 1), round((want.get(k) or 0) * 100, 1)
        rows.append({'종목': L.asset_name(k), '역할': ROLES[L.ASSETS[k][2]] if k in L.ASSETS else '여운봉 매매법', '현재': now, '목표': target,
                     '밴드 하단': max(0.0, round(target - band, 1)), '밴드 상단': round(target + band, 1),
                     '밴드': '밖 ▲' if now > target + band else '밖 ▼' if now < target - band else '안'})
    return pd.DataFrame(rows)


def dca_cards(settings, state):
    """The virtual book's 적립식 at a glance (v3): on/off with its way, when it buys next, the money paid in and the 적립식's
    pool (the contributions it has not bought yet, kept out of the engine's money)."""
    import longterm_dca as D
    import longterm_dca_view as DV
    cfg = D.clean(settings['dca'].get('actual') or {})
    dca = (state or {}).get('dca:actual') or {}
    p = D.rule(cfg)
    return [{'label': '적립식 · 가상 계좌', 'value': '켜짐' if cfg['on'] else '꺼짐', 'tone': 'ok' if cfg['on'] else 'off', 'icon': 'savings',
             'sub': DV.way_label(cfg)},
            {'label': '다음 적립일', 'value': D.next_day(cfg, date.today()).isoformat() if cfg['on'] else '—', 'icon': 'event', 'tone': 'off',
             'sub': f"{cfg['amount_krw']:,}원 들어옴 · {D.when_text(cfg, p)} · 휴장이면 다음 거래일"},
            {'label': '적립 누계', 'value': f"{dca.get('contributed_krw', 0):,.0f}원", 'icon': 'add_card', 'tone': 'off'},
            {'label': '적립식 몫(아직 안 산 적립금)', 'value': f"{dca.get('waiting_krw', 0):,.0f}원", 'icon': 'hourglass_top', 'tone': 'off',
             'sub': f"이번 회차 {D.size_text(cfg, p, None)} · 마지막 매수 {dca.get('last_buy') or '—'} · 엔진 리밸런싱이 안 씀"
                    + (f" · 1주 미만 이월 {sum(dca['carry'].values()):,.0f}원" if sum((dca.get('carry') or {}).values()) >= 1 else '')
                    + (f" · 미체결 재매수 대기 {sum(dca['retry'].values()):,.0f}원" if sum((dca.get('retry') or {}).values()) >= 1 else '')}]


def overview(state, decisions, nav, contributed, settings=None):
    try:  # live accounts set to 장기투자 (계좌·자동매매, user 2026-11-17)
        from accounts import load as load_accounts
        import longterm_account_view
        linked = longterm_account_view.linked_accounts(load_accounts())
    except (ImportError, OSError, ValueError):
        linked = []
    every = settings or L.load_settings(L.ROOT)
    st.caption('연결 실계좌: ' + (' · '.join(f'{label} ({mode} · 전략 {L.book_strategy_label(every, k)})' for k, label, mode in linked) if linked else
               '없음 — 계좌·자동매매에서 실계좌의 운용 방식을 장기투자로 고르면 이 엔진의 계획을 그 계좌 운용금으로 따릅니다.'))
    if settings:
        sc.row(dca_cards(settings, state))
    with st.container(border=True):
        temperature_gauge(state.get('plan') or {}, detail=False)
    left, right = st.columns(2)
    with left:
        st.markdown('**목표 대비 현재 비중**')
        frame = weights_frame(state)
        if frame is None:
            st.caption('첫 주문 계획(09:40) 뒤에 보입니다.')
        else:
            y = alt.Y('종목:N', sort=None, title=None)
            tip = ['종목', '역할', '현재', '목표', '밴드 하단', '밴드 상단', '밴드']
            zone = alt.Chart(frame).mark_bar(color='#94a3b8', opacity=0.25, size=22).encode(  # bullet: the band behind
                x=alt.X('밴드 하단:Q', title='비중(%)'), x2='밴드 상단:Q', y=y, tooltip=tip)
            bars = alt.Chart(frame).mark_bar(color='#3b82f6', size=10).encode(x='현재:Q', y=y, tooltip=tip)
            ticks = alt.Chart(frame).mark_tick(color='#f59e0b', thickness=3, size=22).encode(x='목표:Q', y=y, tooltip=tip)
            labels = alt.Chart(frame).mark_text(align='left', dx=4, color=THEME.ink('#cbd5e1'), fontSize=11).encode(
                x='현재:Q', y=y, text=alt.Text('label:N'))
            st.altair_chart((zone + bars + ticks + labels.transform_calculate(label="datum['현재'] + '% · 밴드 ' + datum['밴드']"))
                            .properties(height=250), width='stretch')
            st.caption('파란 막대 = 현재 · 주황 눈금 = 목표 · 회색 띠 = 리밸런싱 밴드(목표 ± 밴드). 띠 밖이면 다음 월 밴드 리밸런싱 대상입니다.')
            with st.expander('표로 보기'):
                st.dataframe(frame, hide_index=True, width='stretch')
    with right:
        st.markdown('**순자산 비교** (같은 1억 원 · 같은 적립 · 비용·세금 포함)')
        if not nav:
            st.caption('그림자 비교는 장 마감(15:40) 기록부터 쌓입니다.')
        else:
            frame = pd.DataFrame(nav, columns=['day', 'book', 'value'])
            frame['구분'] = frame['book'].map(L.BOOK_LABELS)
            chart = alt.Chart(frame).mark_line(point=True).encode(
                x=alt.X('day:N', title=None), y=alt.Y('value:Q', title='원', scale=alt.Scale(zero=False)),
                color=alt.Color('구분:N', legend=alt.Legend(orient='bottom')), strokeDash='구분:N', tooltip=['day', '구분', alt.Tooltip('value:Q', format=',.0f')])
            st.altair_chart(chart.properties(height=230), width='stretch')
            with st.expander('표로 보기'):
                st.dataframe(frame.pivot(index='day', columns='구분', values='value').round(0), width='stretch')
            if contributed:
                st.caption(f"적립 누계 {contributed[max(contributed)]:,.0f}원 · 세 장부 모두 같은 날 같은 금액을 받습니다(순자산에 포함).")
    st.markdown('**최근 결정**')
    if not decisions:
        st.caption('아직 결정 기록이 없습니다.')
    for d in decisions[:5]:
        st.markdown(f"- `{d['time'][5:16].replace('T', ' ')}` **{L.DECISION_KINDS.get(d['kind'], d['kind'])}** · "
                    f"{L.REGIMES[d['regime']][0] if d['regime'] in L.REGIMES else ''} — {(d['reason'] or '')[:160]}")


# ---------- 전략 설정 ----------

def settings_history():
    """This engine's settings changes, newest first (read once per render: strategy cards and the run settings use it)."""
    try:
        import config_store
        return config_store.history(500, kind='vibe-longterm')
    except Exception:
        return []


def strategy(root, settings, plan, recommended, history):
    import longterm_account_view as A
    from accounts import load as load_accounts, longterm_accounts
    live = longterm_accounts(load_accounts())
    import longterm_strategy_guide as G
    books = {'actual': '가상 계좌(엔진)', **{k: f"{r.get('label', k)} · 실계좌" for k, r in live.items()}}
    head, guide = st.columns([4, 1], vertical_alignment='center')
    head.info('**사용법** ① 설정할 장부를 고르고 ② 운용 방식과 거장 모듈을 켠 뒤 ③ 기준 비중·수치를 정하고 ④ 미리보기를 확인해 **전략 저장**. '
              '다음 날 08:45부터 적용됩니다.', icon=':material/route:')
    with guide:
        G.guide_button('lt_strategy_guide')
    st.session_state.setdefault('lt_book_pick', 'actual')
    pick = st.segmented_control('설정할 장부', list(books), format_func=books.get, key='lt_book_pick', help=G.HELP['book_pick']) or 'actual'
    if pick != 'actual':  # the same editor as 계좌·자동매매 → 장기투자 계좌 '전략' (one component, docs S4)
        A.strategy_panel(pick, 'ltv_', root, capital=(live[pick].get('budget') or {}).get('capital_krw'))
        st.caption('같은 설정이 계좌·자동매매 → 이 계좌 "전략"에도 보입니다. 국면·테마 스토리·지표, 거장 고유 수치(수치 조정), 기준 비중·운용 수치(밴드·월 매도 상한·집행 속도)는 모든 장부가 엔진 것을 씁니다.')
        return
    book = settings['books'].get('actual') or L.clean_book({})
    st.markdown('##### ① 운용 방식 · 무엇으로 운용할지')
    way = st.radio('가상 계좌 운용 방식', ['modules', 'yeo_method'], index=1 if book['strategy'] == 'yeo_method' else 0, horizontal=True,
                   key='lt_actual_way', label_visibility='collapsed',
                   format_func={'modules': '거장 모듈 조합 (② ~ ⑤에서 설정)', 'yeo_method': '여운봉 저수지 매매법 (단독 규칙)'}.get,
                   help=G.HELP['way'] + ' 그림자 장부는 그대로 거장 모듈로 돌아 비교됩니다.')
    st.caption(G.WAY_NOTE)
    A.reservoir_panel('actual', 'lt_', root, L.CAPITAL, holdings(root)[0])  # 저수지 ETF (2026-11-19): both ways use it
    if way == 'yeo_method':
        cfg = A.yeo_panel('actual', 'lt_', root, book['yeo'])
        if st.button('운용 방식 저장', type='primary', key='lt_save_way', icon=':material/save:',
                     disabled=book['strategy'] == 'yeo_method' and cfg == book['yeo']):
            save(root, {'books': {'actual': {'strategy': 'yeo_method', 'yeo': cfg}}}, '가상 계좌를 여운봉 저수지 매매법으로 저장했습니다.')
        st.info('이 장부는 **여운봉 저수지 매매법 하나로** 운용합니다. 아래 ② 모듈 선택 방식 · ③ 거장 모듈 · ④ 집행 속도는 이 장부에 쓰지 않습니다. '
                '그 설정은 비교용 그림자 장부와 "엔진과 같음"을 고른 실계좌가 계속 씁니다.', icon=':material/info:')
        if not st.toggle('거장 모듈 설정도 보기 (그림자·"엔진과 같음" 실계좌용)', key='lt_show_modules'):
            return
    elif book['strategy'] == 'yeo_method':
        if st.button('거장 모듈로 되돌리기', type='primary', key='lt_save_way_modules'):
            save(root, {'books': {'actual': {'strategy': 'engine'}}}, '가상 계좌를 거장 모듈 운용으로 되돌렸습니다.')
    st.markdown('##### ② 모듈 선택 방식(운용 모드) · 켤 거장 모듈을 누가 정하나', help=G.HELP['mode'])
    st.caption('운용 모드는 투자법이 아니라 ③의 거장 모듈 스위치를 **내가 정할지(수동), 추천을 보며 내가 정할지(추천), 국면에 맞춰 자동으로 바꿀지(자동)**를 고르는 것입니다.')
    mode = st.segmented_control('운용 모드', list(L.MODES), format_func=L.MODES.get, default=settings['mode'], key='lt_mode',
                                label_visibility='collapsed', help=G.HELP['mode']) or settings['mode']
    st.caption({'manual': '수동: 켠 모듈로만 운용합니다. 추천은 배지로 보여 주기만 합니다.',
                'recommend': '추천: 켠 모듈로 운용하고, 추천과 다르면 알려 줍니다. "추천대로 맞추기"로 채운 뒤 직접 저장합니다(자동으로 바꾸지 않음).',
                'auto': '자동: 시황 판정이 고른 조합을 매일 그림자로 운용합니다. "자동 실제 적용"을 켜면 가상 계좌도 그 조합으로 운용합니다.'}[mode])
    auto_live = settings['auto_live']
    if mode == 'auto':
        auto_live = st.toggle('자동 조합을 실제(가상 계좌)에 적용', value=settings['auto_live'], key='lt_auto_live',
                              help='권장: 그림자 3개월 기록에서 자동 조합이 기본(기준 비중+달리오)보다 나을 때 켜세요.')
    if (mode, auto_live) != (settings['mode'], settings['auto_live']):
        needs_phrase = auto_live and not settings['auto_live']
        phrase = st.text_input(f'확인 문구 "{AUTO_PHRASE}"를 입력하세요', key='lt_auto_phrase') if needs_phrase else AUTO_PHRASE
        if st.button('모드 저장', type='primary', key='lt_save_mode', disabled=phrase != AUTO_PHRASE):
            save(root, {**settings, 'mode': mode, 'auto_live': auto_live}, f"모드를 {L.MODES[mode]}(으)로 저장했습니다.")
    on = {k for k, v in settings['modules'].items() if v}
    rec = set(recommended)
    regime = plan.get('regime')
    auto = settings['mode'] == 'auto' and settings['auto_live']
    for k in L.MODULES:  # the toggles' values live in session state, so "추천대로 맞추기" only prefills them
        st.session_state.setdefault(f'lt_mod_{k}', settings['modules'][k])

    def fill():
        for k in L.MODULES:
            st.session_state[f'lt_mod_{k}'] = k in rec
    with st.container(border=True):
        label = L.REGIMES[regime][0] if regime in L.REGIMES else '국면 미정'
        st.markdown(f'**추천** · {label} 국면 기준 ' + ' '.join(f"{L.GRADE_BADGE[g]}" for g in L.GRADES)
                    + ' :gray[· 추천은 안내이고, 켠 모듈대로 운용합니다]')
        if on != rec:
            st.dataframe(pd.DataFrame([{'모듈': module_label(k), '지금': '켬' if k in on else '끔', '추천': '켬' if k in rec else '끔',
                                        '등급': L.module_grade(k, regime)[0]} for k in L.MODULES if (k in on) != (k in rec)]),
                         hide_index=True, width='stretch')
            st.caption('저장된 모듈이 지금 국면 추천과 다릅니다. 국면이 바뀌어 추천이 달라져도 자동으로 바꾸지 않습니다'
                       + (' (자동 실제 적용 중에는 시황 판정 조합으로 운용).' if auto else '.'))
        else:
            st.caption('켠 모듈이 지금 추천과 같습니다.')
        st.button('추천대로 맞추기', key='lt_apply', icon=':material/auto_fix_high:', on_click=fill, disabled=auto,
                  help='아래 모듈 스위치를 지금 국면 추천 조합으로 채웁니다. "모듈 설정 저장"을 눌러야 저장됩니다.')
    import longterm_inputs_guide as IG
    IG.button('lt_inputs_modules', '기능별 판단', label='거장마다 무엇을 보고 판단하나')
    st.markdown('##### ③ 거장 모듈 · 켜고 끄기와 각 거장의 숫자', help='켠 거장 모듈의 규칙이 겹쳐 그날 목표 비중을 만듭니다. 기준 비중 모듈이 비중을 들고, '
                '다른 거장들이 상황에 따라 비중을 줄이거나(스토리·추세·과열) 늘립니다(하락 단계·급락 반등).')
    if settings['mode'] == 'auto' and settings['auto_live']:
        st.warning('자동 실제 적용 중에는 시황 판정이 모듈을 고릅니다. 아래 켜짐은 자동을 끌 때 쓰는 수동 설정입니다.', icon=':material/auto_mode:')
    impacts, chosen, changes = plan.get('impacts') or {}, {}, {}
    weights, tuning = dict(settings['weights']), dict(settings['tuning'])  # each master's numbers sit in its card (S1)
    params = {m: dict(v) for m, v in settings['params'].items()}  # and its own rule numbers (S3)
    for time_, _, key, *_ in history:
        changes.setdefault(key, time_)
    for title, keys in GROUPS:
        st.markdown(f'**{title}**')
        cols = st.columns(2)
        for i, key in enumerate(keys):
            master, name, rule, evidence, _default, locked = L.MODULES[key]
            with cols[i % 2].container(border=True):
                head, toggle = st.columns([5, 1], vertical_alignment='center')
                grade, why, _ = L.module_grade(key, regime)
                head.markdown(f'**{master}** · {name} {L.GRADE_BADGE[grade]}')
                chosen[key] = toggle.toggle('켬', key=f'lt_mod_{key}', label_visibility='collapsed',
                                            help=f'{module_label(key)} 켜기/끄기 · {grade}: {why}'
                                            + (f'  \n{L.BOGLE_OFF_NOTE}' if key == 'bogle_core' else ''))
                st.caption(f'{grade}: {why}')
                if key == 'bogle_core':
                    rule = '아래 기준 비중을 들고 갑니다. 다른 거장 모듈은 이 비중에서 출발해 그날 목표를 조정합니다.'
                st.caption(f'규칙: {rule}')
                if key == 'marks_pendulum':
                    temperature_gauge(plan, detail=False)
                if key == 'yeo_reservoir':
                    st.caption(':violet[①의 "여운봉 저수지 매매법"과 다릅니다.] ' + G.RESERVOIR_VS_METHOD)
                if key == 'bogle_core':  # 기준 비중 (was 운용 설정): the starting point every other module adjusts
                    grid = st.columns(3)
                    for i, (k, a) in enumerate(L.ASSETS.items()):
                        weights[k] = grid[i % 3].number_input(a[1].split(' ', 1)[-1], 0.0, 60.0, round(settings['weights'][k] * 100, 1), step=0.5,
                                                              key=f'lt_w_{k}', help=f"{ROLES[a[2]]} · 재현 기준 {a[3] * 100:g}%") / 100
                    st.caption(f"합계 {sum(weights.values()) * 100:.1f}% · 위성 {sum(weights[k] for k in L.SATS) * 100:.1f}% (합계 100%, 위성 30% 이하)")
                if key in L.MODULE_PARAMS:
                    params[key] = param_inputs(key, settings['params'][key])
                for t in CARD_TUNING.get(key, ()):
                    tuning[t] = tuning_input(st, t, settings['tuning'][t])
                    if note := off_default(t, tuning[t]):
                        st.caption(note.removeprefix(' · '))
                effect = impacts.get(key)
                st.caption('오늘 영향: ' + ('기본 비중 그대로' if key == 'bogle_core' else '; '.join(effect) if effect
                                         else '없음' if effect is not None else '첫 판단 뒤 표시'))
                st.caption(f'재현 근거: {evidence}')
                if changed := changes.get(f'modules.{key}'):
                    st.caption(f'마지막 변경: {changed[:16].replace("T", " ")}')
    if not chosen.get('bogle_core'):
        st.warning('기준 비중 모듈(강력 추천)가 꺼져 있습니다. ' + L.BOGLE_OFF_NOTE, icon=':material/warning:')
        if need := L.base_dependent([k for k, v in chosen.items() if v]):
            st.caption('기준 비중 모듈 없이 영향이 거의 없는 모듈: ' + ', '.join(module_label(k) for k in need) + ' (안내만 하고 막지는 않습니다.)')
    st.markdown('##### ④ 집행 속도 · 목표까지 얼마나 빨리 살지', help='전략이 정한 목표까지 하루·한 주에 얼마나 빨리 사는지입니다. 어느 거장에도 속하지 않는 장부 운용값입니다.')
    nav_now = L.CAPITAL
    st.caption(f"예: 순자산 {nav_now:,}원에 하루 매수 상한 {tuning['daily_buy_share']:.0%}이면 하루 최대 {nav_now * tuning['daily_buy_share']:,.0f}원까지 삽니다. "
               f"초기 투입 {tuning['deploy_weeks']}주이면 처음 목표까지 남은 금액을 {tuning['deploy_weeks']}주에 나눠 매주 한 번 삽니다"
               + ('(0이면 하루 상한만큼 매일).' if tuning['deploy_weeks'] else ' — 지금 0이라 하루 상한만큼 매일 삽니다.'))
    with st.container(border=True):
        cols = st.columns(len(EXECUTION_TUNING))
        for col, t in zip(cols, EXECUTION_TUNING):
            tuning[t] = tuning_input(col, t, settings['tuning'][t])
            col.caption(off_default(t, tuning[t]).removeprefix(' · ') or '재현 기준값')
    problems = L.weight_problems(weights)
    for problem in problems:
        st.warning(problem, icon=':material/warning:')
    changed = (chosen != settings['modules'] or tuning != settings['tuning'] or params != settings['params']
               or any(abs(weights[k] - settings['weights'][k]) > 1e-9 for k in L.ASSETS))
    if L.clean_params(params) != params:
        problems = [*problems, '저수지·투매 2단계 낙폭은 1단계보다 깊어야 합니다.']
    st.markdown('##### ⑤ 미리보기 · 오늘 목표 비중', help=G.HELP['preview'])
    saved_modules = list(plan.get('active') or []) if auto else [k for k, on in settings['modules'].items() if on]  # what the next plan uses
    rows = target_preview(plan, L.read_state(root, ('story', 'book:actual')), saved_modules, settings['weights'], chosen, weights,
                          settings['params'], params)
    if rows is None:
        st.caption('첫 판단(08:45) 뒤에 오늘 목표를 계산해 보여 줍니다.')
    else:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
        st.caption('밴드·매도 상한·집행 속도는 목표 비중이 아니라 언제·얼마나 사고파는지를 바꿉니다.' + (' 자동 실제 적용 중에는 저장 값 대신 시황 판정 조합으로 계산됩니다.' if auto else ''))
    if st.button('전략 저장', type='primary', key='lt_save_modules', icon=':material/save:', disabled=not changed or bool(problems),
                 help=G.HELP['save']):
        save(root, {**settings, 'modules': chosen, 'weights': weights, 'tuning': tuning, 'params': params},
             '전략(모듈·비중·수치)을 저장했습니다. 다음 판단(08:45)부터 적용됩니다.')
    st.caption(f"저장하면: 가상 계좌는 다음 날 08:45 판단부터 이 전략으로 목표를 정하고, 가상 체결이 "
               f"{'**켜져 있어** 09:40부터 그대로 가상 매매합니다' if settings['execute'] else '**꺼져 있어** 계획만 기록합니다(계좌·실행 → 실행에서 켬)'}. "
               '실계좌는 맨 위 "설정할 장부"에서 그 계좌 전략을 따로 정하며, 실주문 스위치와 실행기 --execute 등록이 모두 있어야 실제로 주문합니다.')
    st.caption('모듈·모드 변경은 다음 아침 판단(08:45)부터 계획에 반영됩니다. 참고 지표(규칙 아님): VIX, 이번 달 계절성 평균, Jev 하루 요약, 매매일지. COT는 다음 단계에서 붙입니다.')


# ---------- 시황·국면 ----------

# risk to the book, highest first (user request 2026-11-18): level 1~5 and the word; the table sorts and tints by it
REGIME_RISK = {'panic': (5, '매우 높음'), 'bear': (4, '높음'), 'correction': (3, '주의'), 'overheat': (3, '주의 · 과열'),
               'recovery': (2, '보통'), 'bull': (1, '낮음')}
REGIME_EMOJI = {'panic': '🚨', 'bear': '📉', 'correction': '⚠️', 'overheat': '🔥', 'recovery': '🌱', 'bull': '📈'}


def regime_rows(current):
    """The six regimes by risk (highest first) with the order the engine tests them, the rule, the recommended modules and the
    master the 적립 regime shadow follows; the current one carries '▶ 지금'."""
    import longterm_dca as D
    order = {k: i + 1 for i, k in enumerate(L.REGIMES)}
    rows = []
    for k in sorted(L.REGIMES, key=lambda k: (-REGIME_RISK[k][0], order[k])):
        label, rule, mods = L.REGIMES[k]
        level, word = REGIME_RISK[k]
        rows.append({'key': k, '지금': '▶ 지금' if k == current else '', '위험도': '■' * level + '□' * (5 - level) + f' {word}',
                     '국면': f'{REGIME_EMOJI[k]} {label}', '판정 순서': f'{order[k]}번째', '조건': rule,
                     '추천 모듈': ', '.join(L.MODULES[m][0] for m in mods),
                     '강력 추천': ', '.join(f'{L.MODULES[m][0]} {L.MODULES[m][1]}' for m in L.MODULES if L.module_grade(m, k)[0] == '강력 추천'),
                     '비권장': ', '.join(f'{L.MODULES[m][0]} {L.MODULES[m][1]}' for m in L.MODULES if L.module_grade(m, k)[0] == '비권장') or '—',
                     '적립 시황 자동': D.PROFILES[D.AUTO[k]]['label']})
    return rows


def regime_table(current):
    """regime_rows as a table tinted by each regime's colour, the current row stronger and bold."""
    frame = pd.DataFrame(regime_rows(current))
    rgba = lambda hex_, a: f"rgba({int(hex_[1:3], 16)},{int(hex_[3:5], 16)},{int(hex_[5:7], 16)},{a})"  # noqa: E731

    def tint(row):
        now = row['지금'] != ''
        return [f"background-color: {rgba(REGIME_COLOR[row['key']], .32 if now else .10)}" + ('; font-weight: 700' if now else '')] * len(row)
    st.dataframe(frame.style.apply(tint, axis=1), hide_index=True, width='stretch', column_order=[c for c in frame if c != 'key'])


REGIME_ICON = {'bull': 'trending_up', 'overheat': 'local_fire_department', 'correction': 'trending_down', 'bear': 'south',
               'panic': 'warning', 'recovery': 'north_east'}


def indicator_cards(ind):
    """The engine's US-market facts as cards (장기투자 → 시황·국면 and 시장 상황 share them)."""
    season = ind.get('season_month_mean')
    return [
        {'label': '나스닥100 낙폭(252일 고점)', 'value': pct(ind.get('dd')), 'icon': 'south_east', 'tone': 'down' if (ind.get('dd') or 0) <= -0.1 else 'off'},
        {'label': '200일선 대비', 'value': pct(ind.get('gap'), True), 'icon': 'show_chart', 'tone': 'up' if (ind.get('gap') or 0) >= 0 else 'down',
         'sub': f"200일선 20일 기울기 {pct(ind.get('slope'), True)}"},
        {'label': 'VIX', 'value': f"{ind.get('vix') or 0:.1f}", 'icon': 'bolt',
         'tone': 'alert' if (ind.get('vix') or 0) >= 30 else 'warn' if (ind.get('vix') or 0) >= 20 else 'ok'},
        {'label': '원/달러', 'value': f"{ind.get('usdkrw') or 0:,.0f}", 'icon': 'currency_exchange', 'tone': 'off'},
        {'label': '이번 달 계절성(참고)', 'value': pct(season, True) if season is not None else '—', 'icon': 'calendar_month', 'tone': 'off',
         'sub': '최근 10년 같은 달 평균'},
    ]


TEMP_ZONES = ((0, 20, '극단적 공포', '#1d4ed8'), (20, 40, '공포', '#60a5fa'), (40, 60, '중립', '#94a3b8'), (60, 80, '탐욕', '#fb923c'),
              (80, 100, '극단적 탐욕', '#ef4444'))


def temperature_rows(temp):
    fmt = {'vix': lambda v: f'{v:.1f}', 'gap': lambda v: pct(v, True), 'dd': pct, 'hy': lambda v: f'{v:.2f}%p'}
    ends = {k: (fmt[k](fear), fmt[k](greed)) for k, _, fear, greed in L.TEMPERATURE}
    return [{'항목': p['label'], '오늘 값': fmt[p['key']](p['value']), '온도(0 공포 · 100 탐욕)': f"{p['score']:.0f}",
             '기준': f"{ends[p['key']][0]} → 0 · {ends[p['key']][1]} → 100"} for p in temp['parts']]


def temperature_gauge(plan, detail=True):
    """막스식 공포·탐욕 온도 (user request 2026-11-18): today's mark on five named bands. Reference only, never a rule."""
    temp = L.temperature(plan.get('indicators'), plan.get('macro'))
    if not temp:
        st.caption('시장 온도(막스식 공포·탐욕): 첫 아침 판단(08:45) 뒤에 보입니다.')
        return
    st.markdown(f"**시장 온도 · 막스식 공포·탐욕** — **{temp['score']:.0f}** · {temp['label']} "
                f"<span style='opacity:.7'>(지표 {len(temp['parts'])}/{len(L.TEMPERATURE)}개 평균 · 참고)</span>", unsafe_allow_html=True)
    zones = pd.DataFrame([{'from': a, 'to': b, 'mid': (a + b) / 2, '구간': n} for a, b, n, _ in TEMP_ZONES])
    x = alt.X('from:Q', scale=alt.Scale(domain=[0, 100]), title=None, axis=alt.Axis(values=[0, 20, 40, 60, 80, 100], grid=False))
    band = alt.Chart(zones).mark_rect(opacity=0.9).encode(
        x=x, x2='to:Q', y=alt.value(4), y2=alt.value(30), color=alt.Color('구간:N', scale=alt.Scale(domain=[z[2] for z in TEMP_ZONES], range=[z[3] for z in TEMP_ZONES]), legend=None),
        tooltip=['구간'])
    names = alt.Chart(zones).mark_text(color='white', fontSize=11, fontWeight='bold').encode(x='mid:Q', y=alt.value(17), text='구간:N')
    now = pd.DataFrame([{'score': temp['score'], 'text': f"▼ {temp['score']:.0f}"}])
    rule = alt.Chart(now).mark_rule(color=THEME.ink('#0f172a'), strokeWidth=4).encode(x='score:Q', y=alt.value(0), y2=alt.value(34), tooltip=[alt.Tooltip('score:Q', title='온도')])
    st.altair_chart((band + names + rule).properties(height=34), width='stretch')
    if detail:
        st.dataframe(temperature_rows(temp), hide_index=True, width='stretch')
        st.caption('하워드 막스의 시계추: 0에 가까울수록 시장이 두려워하는 때(싸게 살 기회를 찾는 쪽), 100에 가까울수록 탐욕(위험을 덜어내는 쪽)입니다. '
                   '엔진이 이미 받은 지표(VIX·200일선 대비·고점 대비 낙폭·하이일드 스프레드)를 0~100으로 옮겨 평균한 참고 지표이며, '
                   '엔진 규칙·타점·주문은 이 값을 읽지 않습니다. 막스 시계추 모듈은 국면(과열·약세·투매)으로 움직입니다.')


def regime_card(regime_state):
    """The official regime with a pending one, in words and an icon (not colour alone)."""
    now, pending = regime_state.get('regime'), regime_state.get('pending')
    if now not in L.REGIMES:
        return {'label': '장기 국면', 'value': '판정 전', 'tone': 'off', 'icon': 'help'}
    tone, icon = REGIME_TONE[now], REGIME_ICON[now]
    sub = (f"{L.REGIMES[pending][0]} 확정 대기 {regime_state.get('pending_days', 0)}일째" if pending in L.REGIMES
           else f"{regime_state.get('since', '')}부터")
    return {'label': '장기 국면', 'value': f'● {L.REGIMES[now][0]}', 'tone': tone, 'icon': icon, 'sub': sub,
            'help': L.REGIMES[now][1]}


def market_summary(root):
    """시장 상황 탭 '미국 장기 지표': the long-term engine's regime and US facts with the macro background (read-only)."""
    state = L.read_state(root, ('plan', 'regime'))
    plan = state.get('plan') or {}
    st.markdown('### 미국 장기 지표 · 장기투자 엔진')
    import longterm_inputs_guide as IG
    IG.button('lt_inputs_summary', '데이터 전체', label='장기 엔진은 무엇을 보고 판단하나')
    ind = plan.get('indicators') or {}
    if not ind:
        st.caption('장기 엔진의 첫 판단(평일 08:45) 뒤에 보입니다.')
        return
    sc.row([regime_card(state.get('regime') or {}), *indicator_cards(ind)[:3]])
    st.caption(f"Yahoo 일봉 · 미국 종가 {ind.get('as_of', '—')} · 판단 {(plan.get('time') or '')[5:16].replace('T', ' ')} · "
               '국면 기준·스토리·타점은 "장기투자" 탭, 구조는 전략 구조 → 장기투자에서 봅니다.')
    macro_card(plan.get('macro'))


def freshness_cards(root, service, now):
    """운영 상태 '장기투자 엔진': the launchd service, the last decision, the last NAV, macro data and today's report."""
    import longterm_report as R
    root = Path(root)
    state = L.read_state(root, ('plan', 'nav_latest'))
    plan, nav = state.get('plan') or {}, state.get('nav_latest') or {}
    today = now.strftime('%Y-%m-%d')
    enabled = L.load_settings(root)['enabled']
    working = enabled and trading_day(now.date())  # a KRX holiday or the switched-off engine expects nothing today
    due = working and now.strftime('%H:%M') >= '09:00'  # the decision runs 08:40~08:45
    decided = plan.get('day') == today
    macro = plan.get('macro')
    report = R.paths(today, root / 'reports')[0]
    idle = '운영 꺼짐' if not enabled else '휴장일' if not working else '판단 시각 전'
    paid = nav.get(L.CONTRIBUTED)
    tone, icon, pulse = sc.service(service)
    return [
        {'label': '장기투자 실행기', 'value': service, 'tone': tone if service == '실행 중' or due else 'off', 'icon': icon,
         'sub': '평일 08:40 시작 · 판단·주문·마감 뒤 종료', 'help': '하루에 한 번 도는 서비스라 장 시간 밖에는 "종료"가 정상입니다.'},
        {'label': '마지막 판단', 'value': (plan.get('time') or '')[5:16].replace('T', ' ') or '없음', 'icon': 'event_available',
         'tone': 'ok' if decided else 'alert' if due else 'off', 'sub': '오늘 판단 있음' if decided else '오늘 판단 없음' if due else idle,
         'help': '평일 09:00이 지났는데 오늘 판단이 없으면 빨간색입니다(기록 → 건너뜀 사유 확인).'},
        {'label': '마지막 순자산', 'value': f"{nav['actual'] / 1e8:,.4f}억" if nav.get('actual') else '없음', 'icon': 'account_balance',
         'tone': 'off', 'sub': f"{nav.get('day', '')} 마감 기록" + (f" · 적립 누계 {paid:,.0f}원" if paid else '')},
        {'label': '거시 자료(FRED·연준)', 'value': '기록 전' if not macro else '정상' if not macro.get('errors') else f"빠짐 {len(macro['errors'])}",
         'icon': 'percent', 'tone': 'off' if not macro else 'ok' if not macro.get('errors') else 'warn',
         'sub': ', '.join((macro or {}).get('errors') or [])[:40] or None},
        {'label': '오늘 전략 리포트', 'value': '작성됨' if report.exists() else '없음', 'icon': 'description',
         'tone': 'ok' if report.exists() else 'warn' if working and now.strftime('%H:%M') >= '09:30' else 'off',
         'sub': '예약 작업 평일 08:50(장기투자 운영 꺼짐이면 쓰지 않음)'},
    ]


def market(root, plan, regime_state, decisions, settings):
    import longterm_inputs_guide as IG
    IG.button('lt_inputs_market', '국면 판정', label='국면은 어떻게 판정하나 · 보는 데이터 전체')
    ind = plan.get('indicators') or {}
    if not ind:
        st.caption('첫 판단(08:45) 뒤에 지표가 보입니다.')
    else:
        sc.row(indicator_cards(ind))
        st.caption(f"자료 기준일 {ind.get('as_of', '—')} (미국 종가) · 판단 {(plan.get('time') or '')[:16].replace('T', ' ')}")
        if pending := regime_state.get('pending'):
            need = LF.confirm_need(pending, settings)
            st.progress(min(1.0, regime_state.get('pending_days', 0) / need),
                        text=f"{L.REGIMES[pending][0]} 확정까지 {regime_state.get('pending_days', 0)}/{need}일")
    with st.container(border=True):
        temperature_gauge(plan)
    macro_card(plan.get('macro'))
    history = [d for d in decisions if d['kind'] == 'regime']
    if history:
        st.markdown('**국면 이력**')
        frame = pd.DataFrame([{'날짜': d['day'], '확정': L.REGIMES[d['regime']][0], '원시': L.REGIMES[d['raw_regime']][0] if d['raw_regime'] in L.REGIMES else '—',
                               'key': d['regime']} for d in reversed(history)])
        band = alt.Chart(frame).mark_rect().encode(
            x=alt.X('날짜:N', title=None), color=alt.Color('key:N', scale=alt.Scale(domain=list(REGIME_COLOR), range=list(REGIME_COLOR.values())), legend=None),
            tooltip=['날짜', '확정', '원시'])
        text = alt.Chart(frame).mark_text(color='white', fontSize=11).encode(x='날짜:N', text='확정:N')
        st.altair_chart((band + text).properties(height=48), width='stretch')
        with st.expander('표로 보기'):
            st.dataframe(frame.drop(columns='key'), hide_index=True, width='stretch')
    st.markdown('**국면 기준** · 위험이 큰 국면부터 · 진한 줄이 지금 국면')
    regime_table((regime_state or {}).get('regime'))
    with st.container(border=True):  # 국면 확정 일수 (was 운용 설정): one regime for every book, so it lives here
        days, button = st.columns([3, 1], vertical_alignment='bottom')
        value = tuning_input(days, 'confirm_days', settings['tuning']['confirm_days'])
        if button.button('확정 일수 저장', key='lt_save_confirm', type='primary',
                         disabled=value == settings['tuning']['confirm_days'], width='stretch'):
            save(root, {**settings, 'tuning': {**settings['tuning'], 'confirm_days': value}}, f'국면 확정 일수를 {value}일로 저장했습니다. 다음 판단부터 적용됩니다.')
        st.caption('새 국면이 이 일수만큼 이어져야 바꿉니다(투매는 2거래일). 모든 장부가 같은 국면을 씁니다.' + off_default('confirm_days', value))
    st.caption('위험도 ■ 다섯 칸 = 계좌가 가장 크게 흔들리는 국면. 엔진은 "판정 순서" 열의 순서(투매 → 약세 → 조정 → 회복 → 과열 → 강세 안정)로 먼저 맞는 '
               '국면을 고르고, 같은 새 국면이 확정 일수(기본 10거래일, 투매 2거래일)만큼 이어져야 바꿉니다. 기준 비중 모듈 보유는 모든 국면에 포함됩니다. '
               '"적립 시황 자동"은 그 국면에서 그림자 적립이 따르는 거장입니다(실제 적용 전 기록용).')


def macro_card(macro):
    """Rates, credit and the release calendar (FRED, the Fed): a reference the story check and the report read, not a rule."""
    if not macro:
        return
    st.markdown('**거시 배경** · 참고 자료(엔진 규칙은 쓰지 않음)')
    if series := macro.get('series'):
        sc.row([{'label': s['label'], 'value': f"{s['value']:.2f}{s['unit']}", 'icon': 'percent', 'tone': 'warn' if s.get('stale') else 'off',
                 'sub': (f"1개월 {s['chg_1m']:+.2f} · 1년 {s['low_1y']:.2f}~{s['high_1y']:.2f}" if s.get('chg_1m') is not None else '')
                        + f" · {s['date']}{' · 오래됨' if s.get('stale') else ''}"} for s in series.values()])
    events = ', '.join(f"{e['label']} {e['date'][5:]}" for e in macro.get('events') or []) or '없음'
    st.caption(f"다가오는 미국 발표(30일): {events} · 출처 {macro.get('source', 'FRED')}"
               + (f" · 빠진 자료: {', '.join(macro['errors'])}" if macro.get('errors') else ''))


# ---------- 테마 스토리 ----------

def stories(state, decisions):
    story = state.get('story') or {}
    current = story.get('stories') or {}
    if story.get('market_view'):
        st.info(f"장기 시황 판단: {story['market_view']}", icon=':material/auto_stories:')
    st.caption(f"출처 {story.get('source', '—')} · {(story.get('time') or '')[:16].replace('T', ' ')} · 주의 = 절반, 훼손 = 0, 기울기 ±25%(피터 린치 모듈이 켜졌을 때)")
    now = {k: current.get(k) or {} for k in L.SATS}
    sc.row([{'label': L.ASSETS[k][1], 'value': L.STORY_LABELS.get(v.get('status'), '판단 전') + (f" · 기울기 {v['tilt']:+d}" if v.get('tilt') else ''),
             'tone': STORY_TONE.get(v.get('status'), 'off'), 'icon': 'auto_stories', 'sub': v.get('reason')} for k, v in now.items()])
    rows = []
    for d in decisions:
        if d['kind'] != 'story' or not d['indicators']:
            continue
        for k, v in json.loads(d['indicators']).items():
            if k in L.ASSETS and isinstance(v, dict):
                rows.append({'시각': d['time'][:16].replace('T', ' '), '테마': L.ASSETS[k][1], '판정': L.STORY_LABELS.get(v.get('status'), '—'),
                             '기울기': v.get('tilt'), '이유': v.get('reason')})
    if rows:
        st.markdown('**스토리 이력**')
        st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')


# ---------- 포트폴리오·주문 ----------

def portfolio(root, state, decisions):
    held, cash = holdings(root)
    code_name = {**{c: f'{n} · 관심목록' for c, n in longterm_watchlist.load(root).items()}, **{a[0]: a[1] for a in (*L.ASSETS.values(), *L.yeo_method.EXTRA.values())}}
    st.markdown('**가상 계좌 보유** (매입 기준 · 시세 평가는 개요의 순자산)')
    if held:
        st.dataframe(pd.DataFrame([{'종목': code_name.get(c, c), '코드': c, '수량': q, '평균가': round(avg, 1), '매입금액': round(q * avg)}
                                   for c, (q, avg) in held.items()]), hide_index=True, width='stretch')
    else:
        st.caption('보유 없음 · 실행 방식이 dry-run이면 주문 계획만 기록됩니다.')
    if cash is not None:
        st.caption(f'현금 {cash:,.0f}원')
    last = state.get('last_plan') or {}
    if last:
        st.markdown(f"**{last.get('day')} 계획** — {L.why_label(last.get('why'))}")
        notes = last.get('notes') or []
        if notes:
            st.caption('모듈 영향: ' + '; '.join(n[1] if isinstance(n, (list, tuple)) else str(n) for n in notes))
        if last.get('orders'):
            st.dataframe(pd.DataFrame([{'종목': L.asset_name(o['key']), '방향': '매수' if o['side'] == 'BUY' else '매도', '수량': o['qty'],
                                        '가격': o['price'], '금액': o['qty'] * o['price'], '결과': o.get('status') or 'dry-run'}
                                       for o in last['orders']]), hide_index=True, width='stretch')
        else:
            st.caption('오늘 주문 없음')
    sold = (state.get('book:actual') or {}).get('sold') or {}
    if sold and last.get('nav'):
        limit = (state.get('plan') or {}).get('tuning', {}).get('monthly_sell_share', L.MONTHLY_SELL_SHARE)
        st.progress(min(1.0, sold['krw'] / (limit * last['nav'])), text=f"{sold['month']} 저수지 뺀 매도 {sold['krw']:,.0f}원 / 상한 {limit:.0%}")
    st.markdown('**왜 샀나·팔았나** · 주문마다 매매 사유·국면·거장 판단·거시·스토리·뉴스·트레이딩 데이터·참고 자료를 남깁니다')
    import longterm_evidence_view
    longterm_evidence_view.render(root, key='lt_pf_ev')
    rows = [{'시각': d['time'][:16].replace('T', ' '), '종류': L.DECISION_KINDS.get(d['kind'], d['kind']), **o}
            for d in decisions if d['kind'] in ORDER_KINDS and d['orders'] for o in json.loads(d['orders'])]
    if rows:
        st.markdown('**주문 기록**')
        frame = pd.DataFrame(rows)
        frame['종목'] = frame['key'].map(L.asset_name)
        st.dataframe(frame[['시각', '종류', '종목', 'side', 'qty', 'price'] + (['status'] if 'status' in frame else [])].rename(
            columns={'side': '방향', 'qty': '수량', 'price': '가격', 'status': '결과'}), hide_index=True, width='stretch')


# ---------- 기록 ----------

def record(decisions):
    if not decisions:
        st.caption('아직 기록이 없습니다. 실행기가 평일 08:45부터 국면·스토리·계획을 남깁니다.')
        return
    kinds = st.multiselect('종류', list(L.DECISION_KINDS), default=list(L.DECISION_KINDS), format_func=L.DECISION_KINDS.get, key='lt_log_kinds')
    rows = [d for d in decisions if d['kind'] in kinds]
    frame = pd.DataFrame([{'시각': d['time'][:16].replace('T', ' '), '종류': L.DECISION_KINDS.get(d['kind'], d['kind']),
                           '국면': L.REGIMES[d['regime']][0] if d['regime'] in L.REGIMES else '—', '모드': L.MODES.get(d['mode'], d['mode'] or '—'),
                           '운용 모듈': ', '.join(L.MODULES[m][0] for m in json.loads(d['modules'] or '[]') if m in L.MODULES),
                           '주문': len(json.loads(d['orders'])) if d['orders'] else 0, '이유': d['reason']} for d in rows])
    picked = st.dataframe(frame, hide_index=True, width='stretch', on_select='rerun', selection_mode='single-row',
                          key=f"lt_log_table_{'-'.join(kinds)}_{rows[0]['time'] if rows else ''}_{len(rows)}")
    st.download_button('CSV 내려받기', frame.to_csv(index=False).encode('utf-8-sig'), 'longterm-decisions.csv', 'text/csv',
                       icon=':material/download:', key='lt_log_csv')
    selected = [i for i in (picked.selection.rows if picked and picked.selection else []) if i < len(rows)]
    if selected:
        d = rows[selected[0]]
        with st.container(border=True):
            st.markdown(f"**{d['time'][:16].replace('T', ' ')} · {L.DECISION_KINDS.get(d['kind'], d['kind'])}**")
            st.write(d['reason'])
            for title, field in (('지표', 'indicators'), ('목표', 'targets'), ('주문', 'orders'), ('추천 모듈', 'recommended')):
                if d[field]:
                    with st.expander(title):
                        st.json(json.loads(d[field]))


# ---------- 운용 설정 ----------

def service_state():
    try:
        from automation_status import processes
        return processes().get('장기투자 실행기', '확인 불가')
    except Exception:
        return '확인 불가'


def run_panel(root, settings):
    """계좌·실행 → 실행: the virtual book's run switches only (S1, 2026-11-18). The strategy numbers moved to 전략 (each
    master's card, 집행 속도) and 시장 (국면 확정 일수)."""
    service = service_state()
    stop = root / 'STOP'
    sc.row([{'label': '실행기(launchd)', 'value': service, 'tone': 'ok' if service == '실행 중' else 'off', 'icon': 'schedule',
             'sub': '평일 08:40 시작 · 15:40 기록 뒤 종료'},
            {'label': '가상 계좌', 'value': f'{L.CAPITAL:,}원', 'tone': 'off', 'icon': 'account_balance', 'sub': f'수수료 {L.FEE * 100:.3f}% · 세금은 그림자만 반영'},
            {'label': '중지 요청', 'value': '있음' if stop.exists() else '없음', 'tone': 'alert' if stop.exists() else 'off', 'icon': 'pause_circle',
             'sub': '다음 단계에서 멈춤' if stop.exists() else None}])
    with st.container(border=True):
        st.markdown('**가상 계좌 실행 방식**')
        st.caption('가상 체결을 켜면 엔진이 가상 계좌(1억 원)에 주문을 넣어 실시간 호가로 체결합니다. 끄면 주문 계획만 기록합니다. '
                   '어느 쪽도 실제 증권사 주문이 아닙니다. 실계좌 주문은 계좌·자동매매의 실주문 스위치와 실행기 --execute 등록이 정합니다.')
        execute = st.toggle('가상 체결', value=settings['execute'], key='lt_execute')
        if execute != settings['execute']:
            phrase = st.text_input(f'확인 문구 "{EXECUTE_PHRASE}"를 입력하세요', key='lt_execute_phrase') if execute else EXECUTE_PHRASE
            if st.button('실행 방식 저장', type='primary', key='lt_save_execute', disabled=phrase != EXECUTE_PHRASE):
                save(root, {**settings, 'execute': execute}, '가상 체결로 바꿨습니다. 다음 09:40 주문부터 적용됩니다.' if execute else 'dry-run으로 바꿨습니다.')
        c1, c2 = st.columns(2)
        if not stop.exists():
            if c1.button('실행기 중지 요청', key='lt_stop', icon=':material/pause:', help='STOP 파일을 만들어 실행기가 다음 단계에서 멈춥니다.'):
                stop.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                stop.write_text(datetime.now().isoformat(), encoding='utf-8')
                st.toast('중지 요청을 남겼습니다.', icon=':material/pause_circle:')
                st.rerun()
        elif c1.button('중지 요청 해제', key='lt_unstop', icon=':material/play_arrow:'):
            stop.unlink(missing_ok=True)
            st.toast('중지 요청을 지웠습니다. 다음 실행부터 정상으로 돌아갑니다.', icon=':material/check_circle:')
            st.rerun()
    st.caption('비중·밴드·매도 상한·집행 속도는 "전략" 탭의 거장 카드에서, 국면 확정 일수는 "시장" 탭에서 정합니다.')


def history_view(history):
    """변경 이력: every long-term setting change, newest first."""
    rows = history[:200]
    if rows:
        st.dataframe(pd.DataFrame([{'시각': r[0][:16].replace('T', ' '), '항목': r[2], '이전': r[3], '이후': r[4], '누가': r[5]} for r in rows]),
                     hide_index=True, width='stretch')
    else:
        st.caption('아직 변경이 없습니다.')


def tuning_input(container, key, value, widget_key=None):
    """One engine number (vibe_longterm.TUNING) as an input in its range; shares are shown in % and read back rounded, so an
    unedited value equals the saved one (the save buttons compare them)."""
    import longterm_strategy_guide as G
    default, low, high, label = L.TUNING[key]
    meaning, widget_key = G.TUNING_MEANING.get(key, ''), widget_key or f'lt_t_{key}'
    if isinstance(default, int):
        return container.number_input(label, low, high, int(value), step=1, key=widget_key, help=f'{meaning}  \n재현 기준값 {default} · 범위 {low}~{high}')
    return round(container.number_input(label + ' (%)', low * 100, high * 100, round(value * 100, 6), step=0.5, key=widget_key,
                                        help=f'{meaning}  \n재현 기준값 {default * 100:g}% · 범위 {low * 100:g}~{high * 100:g}%') / 100, 6)


def recipe_rows(root, settings, plan, nav, contributed):
    """[{key, 레시피, 모듈, 재현, 시작, 순자산, 손익, 최대 낙폭, 지금 가상 계좌, series}] of every recipe's paper book (S5)."""
    import longterm_account_view as A
    import yeo_method as Y
    book = settings['books'].get('actual') or L.clean_book({})
    actual = set(L.active_modules(settings, L.recommended_set(plan.get('regime')))) if plan.get('regime') else set()
    rows = []
    for name, (label, modules) in L.RECIPES.items():
        s = A.shadow_summary(L.account_nav(root, f'recipe:{name}'), nav, contributed)
        same = (book['strategy'] == 'yeo_method') if name == 'yeo_method' else (book['strategy'] != 'yeo_method' and set(modules) == actual)
        rows.append({'key': name, '레시피': label, '모듈': ' · '.join(f'{L.MODULES[m][0]} {L.MODULES[m][1]}' for m in modules) or '여운봉 규칙',
                     '재현': L.BOOK_PRESET_RESEARCH.get(name) or Y.RESEARCH.split(' (')[0], '지금 가상 계좌': '● 같음' if same else '',
                     '시작': s['start'] if s else '—', '순자산': f"{s['value']:,.0f}원" if s else '—',
                     '손익': f"{s['gain']:+,.0f}원 ({s['pct']:+.2f}%)" if s else '—', '최대 낙폭': f"{s['mdd'] * 100:.2f}%" if s else '—',
                     'series': (s or {}).get('series') or []})
    return rows


def recipe_view(root, settings, plan, nav, contributed):
    """전략 → 레시피 비교 (S5): every recipe run on paper from the same day with the same 1억 and closes, beside its replay."""
    from dialogs import _tone, table_frame
    import longterm_strategy_guide as G
    rows = recipe_rows(root, settings, plan, nav, contributed)
    head, guide = st.columns([4, 1], vertical_alignment='center')
    head.markdown('##### 레시피 비교', help=G.HELP['recipes'])
    with guide:
        G.guide_button('lt_recipe_guide')
    st.caption('레시피마다 가상 장부(1억 원)를 하나씩 두고 매일 15:40 마감 때 같은 종가로 1주 단위 가상 체결합니다(수수료 포함, 세금·적립식 없음). '
               '같은 날 시작해 같은 기간을 비교하며 실제 계좌가 아닙니다. "재현"은 과거 재현 결과로 기간·조건이 달라 직접 비교하지 않습니다.')
    if not any(r['series'] for r in rows):
        st.info('아직 레시피 비교 기록이 없습니다. 다음 장 마감(15:40)부터 모든 레시피가 같은 날 시작합니다.', icon=':material/hourglass_top:')
    frame = pd.DataFrame([{k: v for k, v in r.items() if k not in ('key', 'series')} for r in rows])
    table_frame(frame.style.map(_tone, subset=['손익']))
    lines = pd.DataFrame([{'날짜': d, '레시피': r['레시피'], '수익률(%)': a} for r in rows for d, a, _ in r['series']]
                         + [{'날짜': d, '레시피': '엔진 가상 계좌(실제)', '수익률(%)': e}
                            for d, _, e in next((r['series'] for r in rows if r['series']), []) if e is not None])
    if not lines.empty:
        st.altair_chart(alt.Chart(lines).mark_line(point=True).encode(
            x=alt.X('날짜:T', title=None), y=alt.Y('수익률(%):Q', title='시작일 대비 수익률(%)'),
            color=alt.Color('레시피:N', legend=alt.Legend(orient='bottom', title=None, columns=2)),
            tooltip=['날짜:T', '레시피:N', alt.Tooltip('수익률(%):Q', format='+.2f')]).properties(height=300), width='stretch')
    st.caption('가상 계좌를 다른 레시피로 바꾸려면 "거장 모듈·비중"에서 모듈을, 계좌별로는 계좌·자동매매 → 장기투자 계좌 "전략"에서 고릅니다.')


def param_inputs(module, saved):
    """A master's own rule numbers (S3) inside its card: folded '수치 조정' with each number in its range, and which ones
    differ from the researched default. Shares and drawdowns are shown in %."""
    import longterm_strategy_guide as G
    out = dict(saved)
    differ = [L.MODULE_PARAMS[module][k][3] for k, v in saved.items() if v != L.MODULE_PARAMS[module][k][0]]
    with st.expander('수치 조정' + (f' · 재현 기준값과 다름 {len(differ)}개' if differ else ' · 재현 기준값'), icon=':material/tune:'):
        for k, (default, low, high, label) in L.MODULE_PARAMS[module].items():
            if isinstance(default, int):
                out[k] = st.number_input(label, low, high, int(saved[k]), step=1, key=f'lt_p_{module}_{k}',
                                         help=f"{G.HELP['params']}  \n재현 기준값 {default} · 범위 {low}~{high}")
            else:
                out[k] = st.number_input(label + ' (%)', low * 100, high * 100, round(saved[k] * 100, 2), step=0.5,
                                         key=f'lt_p_{module}_{k}',
                                         help=f"{G.HELP['params']}  \n재현 기준값 {default * 100:g}% · 범위 {low * 100:g}~{high * 100:g}%") / 100
                out[k] = round(out[k], 6)
        if any(out[k] != L.MODULE_PARAMS[module][k][0] for k in out):
            st.caption(':orange[재현 기준값과 다른 값은 재현 근거가 없습니다.] 카드의 규칙 문구는 기준값으로 쓰여 있고, 바꾼 값은 변경 이력에 남습니다.')
    return out


def off_default(key, value):
    """' · 재현 기준값과 다름' when an engine number is not its researched default."""
    default = L.TUNING[key][0]
    return '' if abs(value - default) < 1e-9 else ' · :orange[재현 기준값과 다름]'


def target_preview(plan, state, saved_modules, saved_weights, modules, weights, saved_params=None, params=None):
    """[{종목, 기준 비중, 오늘 목표(저장), 오늘 목표(바꾸면), 차이}]: today's targets after the masters' adjustment, with the
    saved strategy and with the one on screen (the engine's own targets(), no order); None before the first decision."""
    if not plan.get('indicators') or plan.get('regime') not in L.REGIMES:
        return None
    stories = (state.get('story') or {}).get('stories') or {}
    book = state.get('book:actual') or {}
    day = plan.get('day') or ''
    now = L.targets(plan['indicators'], saved_modules, plan['regime'], book, stories, day, saved_weights, saved_params)[0]
    new = L.targets(plan['indicators'], [k for k, on in modules.items() if on], plan['regime'], book, stories, day, weights, params)[0]
    return [{'종목': a[1], '기준 비중': f'{weights[k]:.1%}', '오늘 목표(저장)': f'{now[k]:.1%}', '오늘 목표(바꾸면)': f'{new[k]:.1%}',
             '차이': '—' if abs(new[k] - now[k]) < 5e-4 else f'{(new[k] - now[k]) * 100:+.1f}%p'} for k, a in L.ASSETS.items()]


# ---------- 타점 지도 · 차트 · 전략 리포트 (docs/longterm-strategy-plan.md 4·5·8) ----------

SIDE = {'buy': '분할 매수', 'sell': '분할 매도', 'check': '점검'}
SIDE_COLOR = {'buy': '#22d3ee', 'sell': '#f87171', 'check': '#f59e0b'}
DCA_COLOR = '#10b981'  # 적립식 매수 타점: green, wide dashes (line style 3), apart from the engine's cyan buy lines
HORIZONS = ('장기', '스윙', '단기')


def levels_frame(items):
    import longterm_cases as C
    evidence = C.evidence()
    return pd.DataFrame([{'종목': L.ASSETS[x['asset']][1], '시간축': x['horizon'], '거장': L.MODULES[x['master']][0], '구분': SIDE[x['side']],
                          '가격': round(x['price']), '현재가 대비': pct(x.get('distance'), True), '크기(원)': x['size']['krw'],
                          '조건': x['condition'], '규칙/제안': '규칙' if x['kind'] == 'rule' else '제안',
                          '상태': {'waiting': '대기', 'near': '근접', 'hit': '닿음', 'filled': '집행됨'}.get(x['status'], x['status']),
                          '과거 사례': describe_case(evidence, x)} for x in items])


def describe_case(evidence, level):
    import longterm_cases as C
    item = evidence.get((level['asset'], level.get('case')))
    return C.describe(item, evidence.get((level['asset'], 'baseline'))) if item else '계좌 규칙(사례 없음)'


def nearest(levels, **match):
    """The not-yet-filled level closest to the current price among those matching every given field, or None."""
    return min((x for x in levels if x['status'] != 'filled' and all(x[k] == v for k, v in match.items())),
               key=lambda x: abs(x['distance']), default=None)


def next_levels(plan):
    items = plan.get('levels') or []
    st.markdown('**다음 타점까지** (현재가 대비 가장 가까운 매수·매도 타점)')
    if not items:
        st.caption('타점 지도는 아침 판단(08:45) 때 계산됩니다.')
        return
    rows = []
    for k, a in L.ASSETS.items():
        buy, sell = nearest(items, asset=k, side='buy'), nearest(items, asset=k, side='sell')
        if buy or sell:
            rows.append({'종목': a[1], '다음 매수': f"{buy['price']:,.0f} ({pct(buy['distance'], True)}) · {L.MODULES[buy['master']][0]}" if buy else '—',
                         '다음 매도': f"{sell['price']:,.0f} ({pct(sell['distance'], True)}) · {L.MODULES[sell['master']][0]}" if sell else '—'})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')


def daily_bars(code):
    """Two years of daily OHLCV of a KRX ETF (Yahoo), oldest first; [] when unavailable."""
    return yahoo_bars(f'{code}.KS')


def yahoo_bars(symbol):
    """Two years of daily OHLCV of any Yahoo symbol (a KRX ETF as '379810.KS', a US stock as 'NVDA'), oldest first; []
    when unavailable (asked again on the next run: only answers are cached)."""
    try:
        return _yahoo_bars(symbol)
    except Exception:
        return []


@st.cache_data(ttl=3600, show_spinner=False)
def _yahoo_bars(symbol):
    import urllib.request
    req = urllib.request.Request(f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=2y&interval=1d',
                                 headers={'User-Agent': 'Mozilla/5.0'})
    r = json.load(urllib.request.urlopen(req, timeout=20))['chart']['result'][0]
    q = r['indicators']['quote'][0]
    return [{'time': t, 'open': o, 'high': h, 'low': lo, 'close': c, 'volume': v or 0}
            for t, o, h, lo, c, v in zip(r['timestamp'], q['open'], q['high'], q['low'], q['close'], q['volume'])
            if None not in (o, h, lo, c)]


def weekly(bars):
    out = {}
    for b in bars:
        week = datetime.fromtimestamp(b['time']).isocalendar()[:2]
        w = out.get(week)
        out[week] = {**b} if w is None else {**w, 'high': max(w['high'], b['high']), 'low': min(w['low'], b['low']),
                                              'close': b['close'], 'volume': w['volume'] + b['volume']}
    return list(out.values())


def moving(bars, n):
    closes = [b['close'] for b in bars]
    return [{'time': b['time'], 'value': sum(closes[i - n + 1:i + 1]) / n} for i, b in enumerate(bars) if i >= n - 1]


YEO_COLOR = '#a78bfa'  # 여운봉 저수지 매매법 단계: violet, dotted (style 1), apart from the engine and the 적립식


def plan_books(root, settings, plan):
    """[{key, label, yeo setting or None, dca setting, dca_state, value, want (today's strategy target), yeo_state}] of every
    long-term book: the virtual book and the live accounts set to 장기투자 (user request 2026-11-18: every set buy plan on the
    chart). Each book's own records: the virtual book's in longterm.sqlite3, a live account's in its day state file."""
    import longterm_dca as D
    import longterm_dca_view as DV
    import longterm_live
    from src.config.paths import get_runtime_root
    try:
        from accounts import load as load_accounts
        records = load_accounts()
    except (ImportError, OSError, ValueError):
        records = {}
    engine = L.read_state(root, ('last_plan', 'dca:actual', 'book:actual'))
    last = engine.get('last_plan') or {}
    out = []
    for key, label in DV.books(records):
        if key == 'actual':
            dca_state, book = engine.get('dca:actual') or {}, engine.get('book:actual') or {}
            want, value = last.get('want') if last.get('day') == plan.get('day') else None, last.get('nav')
        else:
            day = longterm_live.load_state(get_runtime_root() / 'nhplug', key)
            info = day.get('info') or {}
            dca_state, book = day.get('dca') or {}, day.get('book') or {}
            want = info.get('want') if day.get('day') == plan.get('day') else None
            value = ((records.get(key) or {}).get('budget') or {}).get('capital_krw')
        out.append({'key': key, 'label': label.split(' (')[0], 'yeo': L.yeo_settings(settings or {}, key),
                    'dca': (settings or {}).get('dca', {}).get(key) or D.clean({}), 'dca_state': dca_state, 'value': value or 0,
                    'want': L.today_want(plan, want), 'yeo_state': book.get('yeo') or {}})
    return out


def dca_chart(key, bars, plan, decisions, bucket, bar_of, books):
    """Every book's 적립식 on the chart (v3, user request 2026-11-18): the next buy as a price line with its master, size and day
    (down-day ways at yesterday's close, cycle ways at today's price, 윌리엄스 −3%), past contribution days as markers, and
    one caption line per book."""
    import longterm_dca as D
    ind, regime = plan.get('indicators') or {}, plan.get('regime')
    close = (ind.get('krx_close') or {}).get(key)
    import longterm_dca_view as DV
    lines, notes = [], []
    for b in books:
        cfg, label, yeo = D.clean(b['dca']), b['label'], b['yeo']
        buys = key in D.universe(cfg, L.dca_targets(b['want'], yeo))  # this book's own strategy target
        way = DV.way_label(cfg)
        if not cfg['on']:
            notes.append(f'{label}: 적립식 꺼짐 · {way}')
            continue
        if not buys:
            notes.append(f'{label}: {way} · 이 종목은 적립식이 사지 않음')
            continue
        for x in D.buy_levels(cfg, ind, regime, b['dca_state'], bars[-1]['close'], close, value=b['value'], yeo=yeo is not None):
            lines.append({'price': x['price'], 'color': DCA_COLOR, 'style': 3, 'label': f"{label} · {x['label']}"})
        notes.append(f'{label}: {way} · {D.when_text(cfg, D.rule(cfg, regime))}')
    markers = []
    for d in decisions or []:
        sent = json.loads(d['orders']) if d['kind'] == 'dca' and d['orders'] else []
        if any(o.get('key') == key for o in sent if isinstance(o, dict)) and (k := bucket(date.fromisoformat(d['day']))) in bar_of:
            markers.append({'time': bar_of[k], 'position': 'belowBar', 'color': DCA_COLOR, 'shape': 'circle',
                            'text': '적립 계획' if 'dry-run' in (d['reason'] or '') else '적립'})
    return lines, markers, '적립식 · ' + ' / '.join(notes)


def yeo_chart(key, bars, plan, books):
    """The 여운봉 저수지 매매법 steps of every book that runs it, on its risk ETF's chart: each step's drawdown from the index's
    all-time high moved to this ETF with today's index/ETF ratio (the morning decision's ath, else the 2-year high of the
    index from Yahoo), marked 집행됨 once used. Returns (lines, caption)."""
    import yeo_method as Y
    ath = (plan.get('indicators') or {}).get('ath') or {}
    lines, notes, fallback = [], [], {}
    for b in books:
        label, yeo, used = b['label'], b['yeo'], b['yeo_state']  # each book's own used steps, a live account's too
        if yeo is None:
            continue
        for index, risk, _, symbol, steps, _ in Y.pairs(yeo):
            if risk != key:
                continue
            dd = (ath.get(index) or {}).get('dd')
            if dd is None:
                if index not in fallback:
                    series = yahoo_bars(Y.INDEX[index][2])
                    fallback[index] = series[-1]['close'] / max(b['high'] for b in series) - 1 if series else None
                dd = fallback[index]
            if dd is None:
                notes.append(f'{label}: {symbol} 전고점 자료 없음')
                continue
            done = (used.get(index) or {}).get('used') or []
            for n, (level, use) in enumerate(steps, 1):
                lines.append({'price': bars[-1]['close'] * (1 + level) / (1 + dd), 'color': YEO_COLOR, 'style': 1,
                              'label': f"{label} · 여운봉 {n}단계 {level * 100:.0f}% · 저수지 {use:.0%}" + (' · 집행됨' if level in done else '')})
            notes.append(f"{label}: 여운봉 저수지 매매법 · {symbol} 전고점 대비 {dd * 100:.1f}%".replace('-', '−')
                         + ('' if (ath.get(index) or {}).get('dd') is not None else ' (아침 판단 전이라 2년 고점 기준)'))
    return lines, ('여운봉 저수지 매매법 · ' + ' / '.join(notes)) if notes else ''


def chart_tab(root, plan, chart, settings=None, state=None, decisions=()):
    names = {k: a[1] for k, a in L.ASSETS.items()}
    c1, c2, c3, c4 = st.columns([2, 1, 1.6, 2.4], vertical_alignment='bottom')
    key = c1.selectbox('종목', list(L.ASSETS), format_func=names.get, key='lt_chart_asset')
    unit = c2.segmented_control('기간', ['일', '주'], default='일', key='lt_chart_unit') or '일'
    horizons = c3.multiselect('시간축', list(HORIZONS), default=['장기', '스윙'], key='lt_chart_horizons')
    kinds = c4.segmented_control('타점', ['rule', 'advice', 'yeo', 'dca'], default=['rule', 'advice', 'yeo', 'dca'], selection_mode='multi',
                                 key='lt_chart_kinds', format_func={'rule': '엔진 규칙', 'advice': '제안', 'yeo': '여운봉 단계', 'dca': '적립식'}.get,
                                 help='엔진 규칙 = 거장 모듈 타점 지도 · 여운봉 단계 = 여운봉 저수지 매매법 장부의 −20·−30·−40% 매수 · 적립식 = 장부마다 다음 적립 매수') or []
    bars = daily_bars(L.ASSETS[key][0])
    if not bars:
        st.warning('일봉을 불러오지 못했습니다(Yahoo). 잠시 뒤 다시 열어 주세요.', icon=':material/cloud_off:')
        return
    if unit == '주':
        bars = weekly(bars)
    items = [x for x in plan.get('levels') or [] if x['asset'] == key and x['horizon'] in horizons and x['kind'] in kinds]
    lines = [{'price': x['price'], 'color': SIDE_COLOR[x['side']], 'style': 0 if x['kind'] == 'rule' else 2,
              'label': f"{L.MODULES[x['master']][0]}·{x['horizon']}·{SIDE[x['side']]} {x['size']['krw'] / 1e4:,.0f}만"} for x in items]
    high = max(b['high'] for b in bars[-(52 if unit == '주' else 252):])
    lines.append({'price': high, 'color': '#94a3b8', 'style': 1, 'label': '252일 고점' if unit == '일' else '52주 고점'})
    bucket = (lambda d: d.isocalendar()[:2]) if unit == '주' else (lambda d: d)  # noqa: E731 - a weekly bar holds its whole ISO week
    bar_of = {bucket(datetime.fromtimestamp(b['time']).date()): b['time'] for b in bars}
    code_fills = [r for r in L.journal_rows(root) if r['symbol'] == L.ASSETS[key][0]]
    markers = [{'time': next((t for k, t in bar_of.items() if k >= bucket(date.fromisoformat(r['time'][:10]))), bars[-1]['time']),
                'position': 'belowBar' if r['side'] == 'BUY' else 'aboveBar', 'color': '#22c55e' if r['side'] == 'BUY' else '#ef4444',
                'shape': 'arrowUp' if r['side'] == 'BUY' else 'arrowDown', 'text': f"{'매수' if r['side'] == 'BUY' else '매도'} {r['qty']}주"}
               for r in code_fills]
    books = plan_books(root, settings, plan)
    dca_lines, dca_marks, dca_note = dca_chart(key, bars, plan, decisions, bucket, bar_of, books)
    if 'dca' in kinds:
        lines += dca_lines
        markers += dca_marks
    yeo_lines, yeo_note = yeo_chart(key, bars, plan, books)
    if 'yeo' in kinds:
        lines += yeo_lines
    import longterm_cases as C
    labels = {k: v[0] for k, v in {**C.KINDS, **C.INDEX_KINDS}.items()}
    case = st.selectbox('과거 사례 표시', ['없음'] + sorted({x['case'] for x in items if x['case'] in labels}), key='lt_chart_case',
                        format_func=lambda c: '없음' if c == '없음' else labels[c], help='같은 타점에 과거에 처음 닿은 날을 차트에 표시합니다(대용 자료 기준).')
    if case != '없음':
        markers += [{'time': bar_of[k], 'position': 'belowBar', 'color': '#a78bfa', 'shape': 'circle', 'text': '과거 사례'}
                    for d in C.events(key, case) if (k := bucket(date.fromisoformat(d))) in bar_of]
    if chart is None:
        st.caption('차트 컴포넌트가 없어 표만 보여 줍니다.')
    else:
        periods = (20, 50, 200) if unit == '일' else (10, 40)
        chart(bars, sorted(markers, key=lambda m: m['time']), None, averages={n: moving(bars, n) for n in periods},
              intraday=False, visible=260 if unit == '일' else 104, levels=lines, alt=f'{names[key]} 일봉과 타점')
    st.caption('실선 = 엔진 규칙 타점(거장 모듈 장부가 집행) · 점선 = 제안 타점(주문 안 함) · 보라 점선 = 여운봉 매매법 단계 매수 · '
               '초록 긴 점선 = 적립식 다음 매수 · 하늘 매수 · 빨강 매도 · 주황 점검 · 회색 252일 고점 · ▲▼ 가상 체결 · 초록 ● 적립 · 보라 ● 과거 사례. '
               '지수 기준 타점은 오늘의 지수/ETF 비율로 옮긴 근사입니다.')
    uses = [b['label'] for b in books if b['yeo'] is None]
    if 'rule' in kinds:
        st.caption('엔진 규칙 타점을 따르는 장부: ' + (', '.join(uses) if uses else '없음 (모든 장부가 여운봉 저수지 매매법 · 그림자 비교용으로만 표시)'))
    if 'yeo' in kinds and yeo_note:
        st.caption(yeo_note)
    if 'dca' in kinds:
        st.caption(dca_note)
    if items:
        st.dataframe(levels_frame(items), hide_index=True, width='stretch')
    else:
        st.caption('이 종목·시간축에 표시할 타점이 없습니다.')
    with st.expander(f'{names[key]} 매수·매도 근거 (차트의 ▲▼ 체결 · ● 적립)', icon=':material/manage_search:'):
        import longterm_evidence_view
        longterm_evidence_view.render(root, key='lt_chart_ev', asset=key)


SCENARIO_KINDS = (  # first match wins: a crash before a plain fall, overheat before a rise
    (('급락', '투매', '폭락', '패닉'), ('급락', 'red', ':material/bolt:')),
    (('과열', '버블'), ('과열', 'violet', ':material/local_fire_department:')),
    (('조정', '약세', '하락', '심화'), ('하락', 'orange', ':material/trending_down:')),
    (('횡보', '박스'), ('횡보', 'gray', ':material/trending_flat:')),
    (('상승', '회복', '반등', '강세'), ('상승', 'green', ':material/trending_up:')),
)


def scenario_kind(name):
    """(label, Streamlit color, icon) of a report scenario by its name, so different scenarios read differently."""
    name = str(name or '')
    return next((kind for words, kind in SCENARIO_KINDS if any(w in name for w in words)), ('기타', 'blue', ':material/help:'))


VIEW_COLORS = {'사기': 'red', '기다리기': 'gray', '유지': 'blue', '덜기': 'orange', '리밸런싱': 'violet'}


def master_cards(views):
    """거장별 관점 의견 at a glance (the report's 3절 has the full text): each master's own-principle action and whether our
    engine/accounts do the same. Reference only."""
    import longterm_report as R
    st.markdown('**거장별 관점 의견** :gray[· 거장 자신의 원칙으로 본 할 일 · 참고 의견 · 주문 없음 · 전체 글은 아래 3절]')
    cols = st.columns(4)
    for i, (k, m) in enumerate(R.MASTER_VIEWS.items()):
        v = views.get(k) if isinstance(views.get(k), dict) else {}
        same = str(v.get('vs_engine') or '').startswith('같음')
        with cols[i % 4].container(border=True):
            st.markdown(f"**{m['name']}**")
            st.badge(v.get('action') or '—', color=VIEW_COLORS.get(v.get('action'), 'gray'))
            st.badge('엔진·계좌와 같음' if same else '엔진·계좌와 다름', color='green' if same else 'orange',
                     icon=':material/check:' if same else ':material/compare_arrows:')
            st.caption(str(v.get('view') or ''), help=str(v.get('vs_engine') or '') or None)


# words a report's scenario action uses for each module (longterm-strategy-writer writes the master's name with the rule)
SCENARIO_MODULES = {'williams_rebound': ('윌리엄스 단기', '단기 반등', '윌리엄스 반등', '급락 반등'),
                    'williams_trend': ('윌리엄스 추세', '추세 점검'), 'marks_pendulum': ('막스',),
                    'yeo_reservoir': ('여운봉 1단계', '여운봉 2단계', '저수지 분할', '저수지 단계'),
                    'dalio_rebalance': ('달리오', '월 밴드'), 'lynch_story': ('린치', '스토리 점검'), 'buffett_lowturn': ('버핏',)}


def scenario_status(action, settings):
    """[(badge text, colour, icon, tooltip)] for one report scenario by the settings now (not the report's wording): each
    module the action names, on (it acts) or off (it is only recorded), and the virtual fill switch when it says 가상 체결."""
    out = []
    yeo_book = (settings.get('books', {}).get('actual') or {}).get('strategy') == 'yeo_method'
    for key, words in SCENARIO_MODULES.items():
        if any(w in action for w in words):
            on = settings['modules'].get(key) and not yeo_book
            name = f'{L.MODULES[key][0]} {L.MODULES[key][1]}'
            out.append((f"{name} {'켜짐 → 실행' if on else '꺼짐 → 기록만'}", 'green' if on else 'gray',
                        ':material/check_circle:' if on else ':material/block:',
                        '지금 가상 계좌에서 이 모듈이 켜져 있어 시나리오가 오면 엔진이 이 대응을 합니다.' if on else
                        ('가상 계좌가 여운봉 저수지 매매법으로 운용 중이라 거장 모듈을 쓰지 않습니다.' if yeo_book else
                         '지금 가상 계좌에서 이 모듈이 꺼져 있어, 시나리오가 와도 기록만 남기고 매매하지 않습니다. 전략 탭에서 켤 수 있습니다.')))
    if '가상 체결' in action or '계획만' in action:
        on = settings['execute']
        out.append((f"가상 체결 {'켜짐 → 가상 매매' if on else '꺼짐 → 계획만'}", 'green' if on else 'gray',
                    ':material/check_circle:' if on else ':material/block:', '계좌·실행 → 실행의 "가상 체결" 스위치 기준(지금 설정)입니다.'))
    return out


def report_tab(root):
    import longterm_report as R
    settings = L.load_settings(root)
    folder = Path(root) / 'reports'
    import longterm_template as T
    from report_pages import sort_date
    days = sorted((p.stem.removeprefix('strategy-') for p in folder.glob('strategy-*.md')),
                  key=lambda k: (sort_date(k), T.kind_of(k) == 'day'), reverse=True)  # newest first, a day's daily above its week
    if not days:
        st.info('아직 전략 리포트가 없습니다. 평일 08:50 예약 작업(longterm-strategy-writer 스킬)이 매일 씁니다. '
                '지금 쓰려면 Claude에게 "오늘 장기투자 전략 리포트 써줘"라고 요청하세요.', icon=':material/description:')
        return
    day = st.selectbox('리포트', days, key='lt_report_day')
    from longterm_journal_view import read_report  # the journal's long-term tab reads the same two files
    text, obj = read_report(day, folder)
    problems = R.check(day, root, folder)
    if problems:
        st.warning('템플릿 검사: ' + ' / '.join(problems[:3]), icon=':material/rule:')
    if obj.get('summary'):
        st.info(obj['summary'], icon=':material/lightbulb:')
    if obj.get('scenarios'):
        st.markdown('**시나리오별 대응** · 언제(트리거) → 어떻게(대응)')
        cols = st.columns(min(len(obj['scenarios']), 5))
        for col, s in zip(cols * 3, obj['scenarios']):
            label, color, icon = scenario_kind(s.get('name'))
            action = str(s.get('action') or '')
            with col.container(border=True):
                st.badge(label, color=color, icon=icon)
                st.markdown(f"**:{color}[{s.get('name')}]**")
                st.markdown(f":gray[**언제**] · {s.get('trigger')}")
                st.markdown(f":{color}-background[**어떻게**] {action}")
                for badge, tone, mark, why in scenario_status(action, settings):
                    st.badge(badge, color=tone, icon=mark, help=why)
        st.caption('카드 문장은 리포트를 쓴 시점(평일 08:50) 설정 기준입니다. 아래 배지는 **지금 설정** 기준이며, 꺼진 모듈은 그 시나리오가 와도 '
                   '엔진이 기록만 하고 매매하지 않습니다. 켜려면 전략 → 거장 모듈·비중에서 켜고 저장합니다.')
    if isinstance(obj.get('master_views'), dict):
        master_cards(obj['master_views'])
    st.download_button('리포트 .md 내려받기', text.encode('utf-8'), R.paths(day, folder)[0].name, 'text/markdown', icon=':material/download:', key='lt_report_dl')
    st.markdown(for_streamlit(text))

