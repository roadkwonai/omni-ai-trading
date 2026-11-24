"""A live account run by the long-term engine (docs/longterm-live-account-plan.md): its settings panel (계좌·자동매매,
own layout since 2026-11-18: docs/longterm-account-settings-plan.md 1절) and its status card (트레이딩 현황 · 실계좌).
Read-only: the engine's plan of the day, the executor's day state, its launchd registration and the 적립식 setting."""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

import live_labels
import status_cards as sc
import vibe_longterm as L

# scripts/longterm_live.py's words in Korean: order status, event action and execution, and its own reasons (the guard's
# refusals it shares with the Jev mirror fall back to live_labels.reason)
STATUS = {'planned': '보낼 차례', 'sent': '접수(체결 대조 중)', 'dry_run': '계획만(실주문 꺼짐)', 'blocked': '보호 장치가 거절',
          'skipped': '건너뜀'}
ACTIONS = {'PLAN': '그날 주문 계획', 'DCA': '적립식 계획', 'BUY': '매수 주문', 'SELL': '매도 주문', 'HOLD': '대기', 'WAIT': '대기',
           'SKIP': '건너뜀', 'BLOCK': '보호 장치 차단'}
EXECUTIONS = {'RETRY': '다시 시도', 'BLOCKED': '보호 장치가 거절', 'PLANNED': '실주문', 'DRY_RUN': '계획만', 'ACKNOWLEDGED_NOT_FILLED': '접수'}
REASONS = {'share price above the account price cap': '1주 가격이 계좌 1주 가격 상한보다 높음',
           'held shares are not in the guard ledger (adopting them needs operator approval)': '보유분이 보호 원장에 없음(편입은 운영자 승인 필요)',
           'no whole share at the current price or nothing held': '1주 미만이거나 보유 없음',
           'no engine plan today (08:45 decision missing)': '오늘 엔진 계획 없음(08:45 판단 빠짐)',
           'dca: not enough free money': '적립식: 여유 현금 부족',
           'dca: automatic time waits for a price at or under yesterday close': '적립식: 매수 시각 자동 — 전일 종가 이하로 내려올 때까지 대기(14:20엔 매수)', 'today done': '오늘 할 일 끝', 'plan only (dry run)': '계획만(실주문 꺼짐)',
           'automation off': '자동매매 꺼짐', 'live kill switch active': '주문 보호(HALT) 켜짐', 'new live buys are paused': '신규 매수 일시정지',
           'broker has outstanding orders': '다른 미체결 주문이 있음', 'unresolved order blocks submission': '확인 안 된 주문이 있어 대기',
           'market data stale or market closed': '시세가 오래됐거나 장이 닫힘', 'sellable unavailable': '매도 가능 수량 조회 실패'}


def reason(text):
    """A longterm_live reason in Korean; 'before 09:40', 'order window closed (14:30)' and 'quote unavailable <code>' carry a value."""
    text = str(text or '')
    for prefix, label in (('before ', '시작 전 '), ('order window closed ', '주문 창 닫힘 '), ('quote unavailable ', '시세 조회 실패 ')):
        if text.startswith(prefix):
            return label + text.removeprefix(prefix)
    return REASONS.get(text) or live_labels.reason(text)


def event_text(event):
    """One longterm-live.jsonl event as words: action · reason · execution · order count."""
    return ' · '.join(part for part in (ACTIONS.get(event.get('action'), event.get('action', '')), reason(event.get('reason')),
                                         EXECUTIONS.get(event.get('execution'), event.get('execution')) if event.get('execution') else '',
                                         f"{len(event['orders'])}건" if event.get('orders') else '') if part)
PROTECTION = ('종목 손절 없음(엔진 규칙대로 리밸런싱·저수지 매수) · 하루 손실 래치는 오늘 실현 손실+수수료 기준(평가손 제외) · '
              '운용금·1주 가격 상한·회당 한도·하루 새 종목 5(첫 투입일 6)·미체결 차단·전역 중단은 그대로')
LAUNCH_AGENTS = None  # where the definitions are registered; None = this OS's own place (os_services)
FIRST_DAY_NEW_SYMBOLS = len(L.ASSETS)  # user 2026-11-18: the guard lets a long-term account buy all its ETFs on its first day
# 보호 한도(장기 전용): (label, who checks it, what it means on a long-term account); the inputs and the 제한 규칙 table share it
GUARD = {
    'order_max_krw': ('회당 매수 한도', '실계좌 가드',
                      '주문 1건의 금액(가격 × 수량) 상한입니다. 장기 실행기는 이를 넘는 주문을 거절당하지 않게 수량을 줄여 내고, 남는 몫은 그날 사지 '
                      '않습니다(첫 투입·리밸런싱·적립식 모두). 운용금보다 크게 잡아도 운용금이 우선합니다.'),
    'daily_new_symbols': ('하루 새 종목 수', '실계좌 가드',
                          f'그날 처음 사는 종목 수 상한입니다(1~5). 이 계좌가 처음 사는 날(첫 투입일)에는 이 값이 5이면 엔진 종목 수 '
                          f'{FIRST_DAY_NEW_SYMBOLS}개까지 허용합니다(2026-11-18 승인). 더 좁게 정하면 그 값이 첫날에도 그대로입니다.'),
    'order_price_max_krw': ('1주 가격 상한', '실계좌 가드',
                            '이보다 비싼 ETF는 사지 않고 그날 건너뜁니다(매수에만 적용, 이미 가진 ETF는 팔 수 있음). 장기 ETF의 1주 가격은 위 '
                            '포트폴리오 표에 있습니다.'),
    'daily_loss_krw': ('하루 실현 손실 한도', '실계좌 가드',
                       '장기 계좌는 평가손을 세지 않고 오늘 실현 손실+수수료만 셉니다(최대 2만 원). 거의 팔지 않아 리밸런싱 매도에서만 닿을 수 '
                       '있고, 닿으면 그날 새 매수를 막습니다.'),
}


def registered_args(service, folder=None):
    """The command the service com.vibetrading.<service> was registered with, or None when it is not registered."""
    import os_services
    try:
        return os_services.registered_args(service, folder or LAUNCH_AGENTS)
    except os_services.Unknown:
        return None  # shown as not armed, as an unreadable definition always was


def checklist(args, record):
    """[(step, done)] still needed before a real order: the executor registered with --execute, then the live-order switch."""
    return [('실행기 실주문 등록(--execute)', isinstance(args, list) and '--execute' in args),
            ('실주문 스위치', record.get('live_orders') is True)]


def checklist_text(items):
    return '실주문까지: ' + ' · '.join(f"{'①②③'[i]} {label} {'✓' if ok else '✗'}" for i, (label, ok) in enumerate(items))


def latest_prices(today, decisions):
    """{asset: (price, source)}: each ETF's newest recorded order price, from this account's day state or the engine's
    decisions (newest first); the account's own record wins a tie. No broker call.
    shortcut: a price is only as fresh as the last plan that ordered that ETF, so it can be days old; show its source."""
    import yeo_method as Y
    records = [((today or {}).get('day') or '', 1, '이 계좌', (today or {}).get('orders'))]
    for row in decisions:
        try:
            records.append((row.get('day') or '', 0, '엔진', json.loads(row['orders']) if row.get('orders') else None))
        except (TypeError, ValueError):
            continue
    out = {}
    for day, _, who, orders in sorted(records, key=lambda r: (r[0], r[1]), reverse=True):
        for o in orders if isinstance(orders, list) else []:
            if isinstance(o, dict) and (o.get('key') in L.ASSETS or o.get('key') in Y.EXTRA) and type(o.get('price')) in (int, float) and o['price'] > 0:
                out.setdefault(o['key'], (o['price'], f'{who} {day[5:]} 계획'))
    return out


def affordability(capital, weights, prices, price_cap, dca=None):
    """목표 비중 × 운용금 rows: the target money, one share's price (with its source), the whole shares that money buys after
    the fee, and whether it can be bought; an ETF the 적립식 (when on) does not buy is marked."""
    import longterm_dca
    bought_by_dca = set(longterm_dca.universe(longterm_dca.clean(dca), L.dca_targets(weights))) if dca and dca.get('on') else None
    rows = []
    for k, a in L.ASSETS.items():
        weight, (price, source) = weights.get(k, 0), prices.get(k, (None, '—'))
        target = capital * weight
        shares = math.floor(target / (price * (1 + L.FEE)) + 1e-9) if price else 0
        state = ('목표 0%' if weight <= 0 else '가격 기록 없음' if not price else '1주 가격 상한보다 비쌈' if price > price_cap else
                 '살 수 있음' if shares >= 1 else '1주 미만이라 못 삼')
        if bought_by_dca is not None and weight > 0 and k not in bought_by_dca:
            state += ' · 적립식 제외'
        rows.append({'ETF': f'{a[1]} ({a[0]})', '목표 비중': f'{weight:.0%}', '목표 금액': f'{target:,.0f}원',
                     '1주 가격': f'{price:,.0f}원' if price else '—', '살 수 있는 주식 수': shares if price and price <= price_cap else 0,
                     '상태': state, '가격 기준': source})
    return rows


def min_capital(weights, prices):
    """(capital, n, names without a price): the smallest capital, rounded up to 10,000 won, whose target buys at least one
    share (fee included) of each of the n weighted ETFs with a recorded price."""
    weighted = [k for k in L.ASSETS if weights.get(k, 0) > 0]
    known = [k for k in weighted if prices.get(k)]
    need = max((prices[k][0] * (1 + L.FEE) / weights[k] for k in known), default=0)
    return math.ceil(need / 10_000 - 1e-9) * 10_000, len(known), [L.ASSETS[k][1] for k in weighted if k not in known]


def protection_rows(record, capital, settings):
    """보호 한도(장기 전용) as rows (항목, 지금 값, 확인, 장기 계좌에서): the guard's limits plus the engine's monthly sell cap."""
    from accounts import live_limits
    budget, limits = record.get('budget') or {}, live_limits(record=record)
    share = settings['tuning']['monthly_sell_share']
    sell = (f"{capital * share:,.0f}원/월 (운용금의 {share:.0%})" if settings['modules'].get('buffett_lowturn')
            else "꺼짐 ('저회전' 모듈이 꺼져 있음)")
    values = {'order_max_krw': f"{limits['order_max_krw']:,}원",
              'daily_new_symbols': f"{limits['daily_new_symbols']}개" + (f' (첫 투입일 {FIRST_DAY_NEW_SYMBOLS}개)' if limits['daily_new_symbols'] == 5 else ''),
              'order_price_max_krw': f"{budget.get('order_price_max_krw', 0):,}원", 'daily_loss_krw': f"{budget.get('daily_loss_krw', 0):,}원"}
    rows = [{'항목': '월 매도 한도', '지금 값': sell, '확인': '엔진(가드 아님)',
             '장기 계좌에서': '저수지(KOFR)를 뺀 매도는 한 달에 순자산의 이 비율까지만 합니다(워런 버핏·필립 피셔 저회전 모듈). 리밸런싱 매도가 '
                         '이를 넘으면 다음 달로 미룹니다. 비율은 장기투자 → 전략의 버핏·피셔 카드에서 바꿉니다.'}]
    return rows + [{'항목': label, '지금 값': values[field], '확인': who, '장기 계좌에서': text} for field, (label, who, text) in GUARD.items()]


def status(key, record, nhplug, engine_root=None):
    """{plan, weights, today, service, checklist, mode} for one long-term account; empty parts when not available yet.
    The weights are this account's targets of the day (after the story check) when its plan is today's, else the engine's
    virtual book's of the same day, else the saved target weights."""
    import longterm_live as LL
    from launchd_services import SERVICES
    state = L.read_state(engine_root or L.ROOT, ('plan', 'last_plan'))
    plan, last = state.get('plan') or {}, state.get('last_plan') or {}
    today = LL.load_state(nhplug, key)
    same_day = lambda s: plan.get('day') and s.get('day') == plan.get('day')
    weights = L.today_want(plan, ((today.get('info') or {}).get('want') if same_day(today) else None)
                           or (last.get('want') if same_day(last) else None))
    service = f'longterm-{key}' if f'longterm-{key}' in SERVICES else None
    basis = ('이 계좌 오늘 계획의 목표(스토리 반영)' if same_day(today) and (today.get('info') or {}).get('want') else
             '엔진 오늘 목표(스토리 반영)' if same_day(last) and last.get('want') else '저장된 목표 비중')
    return {'plan': plan, 'weights': weights, 'basis': basis, 'today': today, 'service': service,
            'checklist': checklist(registered_args(service) if service else None, record), 'mode': mode_of(record)}


def mode_of(record):
    """실주문 needs the automation and the live-order switch (and the executor's --execute, shown by checklist())."""
    armed = record.get('auto') is True and record.get('live_orders') is True
    return '실주문' if armed else '계획만 기록' if record.get('auto') is True else '멈춤(자동매매 꺼짐)'


def card_rows(record, mode=None):
    """The long-term account's card rows (계좌·자동매매 한눈에 보기 · 계좌 탭): no Jev mirror field."""
    from accounts import live_limits
    from settings_view import won
    budget, limits = record.get('budget') or {}, live_limits(record=record)
    return [('운용 방식', '장기투자 · 미국 지수·테마 ETF'), ('운용금', won(budget.get('capital_krw'))),
            ('회당 매수 한도', won(limits['order_max_krw'])), ('1주 가격 상한', won(budget.get('order_price_max_krw'))),
            ('하루 실현 손실 한도', won(budget.get('daily_loss_krw'))),
            ('하루 새 종목', f"{limits['daily_new_symbols']}개" + (f' (첫 투입일 {FIRST_DAY_NEW_SYMBOLS}개)' if limits['daily_new_symbols'] == 5 else '')),
            ('실행', mode or mode_of(record))]


def asset_names_by_code():
    from yeo_method import EXTRA

    return {code: name for code, name, *_ in (*L.ASSETS.values(), *EXTRA.values())} | L.RESERVOIRS


def fill_rows(key, nhplug, *, fills=None):
    """The account's recorded broker fills, newest first, with the moving-average realized P&L (실계좌 탭 체결 이력)."""
    import engine_compare as ec
    import live_fills
    names = asset_names_by_code()
    held, avg, out = {}, {}, []
    for r in live_fills.rows(nhplug, key) if fills is None else fills:
        h, a, _, pnl, ret = ec.apply_fill(held.get(r['symbol'], 0), avg.get(r['symbol'], 0.0), r['side'], r['qty'], r['price'])
        held[r['symbol']], avg[r['symbol']] = h, a
        code = r['symbol']
        out.append({'시각': r['time'][:16].replace('T', ' '), '종목': f'{names[code]} ({code})' if code in names else code,
                    '매수/매도': '매수' if r['side'] == 'BUY' else '매도', '수량': r['qty'], '가격': f"{r['price']:,.0f}원",
                    '체결 금액': f"{r['price'] * r['qty']:,.0f}원",
                    '실현손익': '' if pnl is None else f"{pnl:+,.0f}원", '보유 후': h})
    return out[::-1]


def order_rows(today):
    return [{'순서': i + 1, '종목': f"{L.asset_name(o['key'])} ({o['code']})", '매수/매도': '매수' if o['side'] == 'BUY' else '매도',
             '수량': o['qty'], '가격': f"{o['price']:,}원", '금액': f"{o['qty'] * o['price']:,}원",
             '상태': STATUS.get(o.get('status'), o.get('status')) + (f" · {reason(o['reason'])}" if o.get('reason') else '')}
            for i, o in enumerate(today.get('orders') or [])]


def summary_cards(data):
    plan, today = data['plan'], data['today']
    day = datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()
    regime = L.REGIMES.get(plan.get('regime'), ('판단 전',))[0]
    has_today = today.get('day') == day
    info = (today.get('info') or {}) if has_today else {}
    sc.row([{'label': '운용 방식', 'value': '장기투자', 'tone': 'ai', 'icon': 'savings', 'sub': '미국 지수·테마 ETF · 거장 모듈'},
            {'label': '실행 모드', 'value': data['mode'], 'icon': 'bolt' if data['mode'] == '실주문' else 'edit_note',
             'tone': 'warn' if data['mode'] == '실주문' else 'off',
             'sub': checklist_text(data['checklist']),
             'help': f"실행기 {data['service'] or '미정의'} · 평일 09:35~14:40. ① launchd 등록 인자(ProgramArguments)에 --execute가 있어야 "
                     '하고(재등록은 별도 승인) ② 이 계좌의 실주문 스위치(accounts.json live_orders)가 켜져야 실제 주문이 실행됩니다.'},
            {'label': '엔진 판단', 'value': regime, 'tone': 'ai', 'icon': 'insights',
             'sub': f"{plan.get('day') or '—'} · {L.MODES.get(plan.get('mode'), plan.get('mode') or '—')} 모드"},
            {'label': '오늘 계좌 계획', 'value': f"{len(today.get('orders') or [])}건" if has_today else '아직 없음',
             'tone': 'off', 'icon': 'receipt_long', 'sub': info.get('why') or '09:40 첫 회차에 계산'}])


def target_compare(plan, stories, book_state, modules):
    """Today's target weights: the virtual book (the engine's modules) beside this account (its own modules, the engine's
    base weights)."""
    day = plan.get('day') or ''
    engine = L.targets(plan['indicators'], plan['active'], plan['regime'], {}, stories, day, plan.get('weights'), plan.get('params'))[0]
    mine = L.targets(plan['indicators'], modules, plan['regime'], book_state, stories, day, plan.get('weights'), plan.get('params'))[0]
    return [{'종목': a[1], '가상 계좌(엔진)': f'{engine[k]:.1%}', '이 계좌': f'{mine[k]:.1%}',
             '차이': '—' if abs(mine[k] - engine[k]) < 5e-4 else f'{(mine[k] - engine[k]) * 100:+.1f}%p'} for k, a in L.ASSETS.items()]


def target_cuts(plan, stories, book_state, before, after):
    """[(ETF name, before, after)] whose target weight a strategy change lowers by more than 0.5%p (the reservoir aside):
    what the account may sell at its next order plan when it holds them (review 2026-11-18)."""
    day = plan.get('day') or ''
    weight = lambda mods: L.targets(plan['indicators'], mods, plan['regime'], book_state, stories, day, plan.get('weights'),
                                    plan.get('params'))[0]
    old, new = weight(before), weight(after)
    return [(a[1], old[k], new[k]) for k, a in L.ASSETS.items() if k != 'RES' and old[k] - new[k] > 0.005]


def module_toggles(prefix, saved, regime, disabled=False):
    """One toggle per master module with its grade badge for the regime (강력 추천·추천·선택·비권장), a recommendation fill and
    the 보글-off warning; returns {module: on}. Values live in session state (set once from `saved`), so the fill buttons only
    prefill and nothing is saved here."""
    keys = {m: f'{prefix}_{m}' for m in L.MODULES}
    for m, k in keys.items():
        st.session_state.setdefault(k, bool(saved.get(m)))
    rec = L.recommended_set(regime)

    def fill(modules):
        for m, k in keys.items():
            st.session_state[k] = m in modules
    label = L.REGIMES[regime][0] if regime in L.REGIMES else '국면 미정'
    a, b = st.columns([1, 2], vertical_alignment='center')
    a.button('추천대로 맞추기', key=f'{prefix}_fill', icon=':material/auto_fix_high:', on_click=fill, args=(rec,), disabled=disabled,
             help=f'지금 {label} 국면의 추천 조합으로 아래를 채웁니다. 저장은 "저장"을 눌러야 합니다.')
    b.caption(f'배지는 지금 {label} 국면 기준입니다. 추천은 안내일 뿐이고, 고른 대로 운용합니다.')
    chosen = {}
    for m, info in L.MODULES.items():
        grade, why, evidence = L.module_grade(m, regime)
        with st.container(border=True):
            left, right = st.columns([3, 2], vertical_alignment='center')
            chosen[m] = left.toggle(f'{info[0]} · {info[1]}', key=keys[m], disabled=disabled,
                                    help=f'{info[2]}  \n재현: {evidence}' + (f'  \n{L.BOGLE_OFF_NOTE}' if m == 'bogle_core' else ''))
            right.markdown(f'{L.GRADE_BADGE[grade]} :gray[{why}]')
    if not disabled:
        off = set(rec) - {m for m, on in chosen.items() if on}
        extra = {m for m, on in chosen.items() if on} - set(rec)
        if off or extra:
            st.info(f'지금 {label} 국면 추천과 다릅니다. ' + (f"추천인데 꺼짐: {', '.join(L.MODULES[m][0] + ' ' + L.MODULES[m][1] for m in sorted(off))}. " if off else '')
                    + (f"추천 밖인데 켜짐: {', '.join(L.MODULES[m][0] + ' ' + L.MODULES[m][1] for m in sorted(extra))}." if extra else ''),
                    icon=':material/recommend:')
        if not chosen.get('bogle_core'):
            st.warning('기준 비중 모듈(강력 추천)가 꺼져 있습니다. ' + L.BOGLE_OFF_NOTE, icon=':material/warning:')
            if need := L.base_dependent([m for m, on in chosen.items() if on]):
                st.caption('기준 비중 모듈 없이 영향이 거의 없는 모듈: ' + ', '.join(f'{L.MODULES[m][0]} {L.MODULES[m][1]}' for m in need)
                           + ' (기준 비중이 있어야 비중을 조절합니다. 막지는 않습니다.)')
    return chosen


def nh_price(code):
    """The current price of `code` from the dashboard's read-only NH adapter, or None (no adapter, a failed read). Kept
    60 s for every session (2026-11-20 NH call audit: every rerun of the account desk re-read the same codes)."""
    return _nh_price(code) if '_read_adapter' in st.session_state else None


@st.cache_data(ttl=60, show_spinner=False)
def _nh_price(code):
    try:
        response = st.session_state['_read_adapter'].call_tool('get_stock_price', {'stock_code': code})
        row = ((response or {}).get('data') or {}).get('Output_0') or {}
    except Exception:  # noqa: BLE001 - display only
        return None
    price = row.get('stck_prpr')
    return price if row.get('iem_cd') == code and type(price) is int and price > 0 else None


@st.cache_data(ttl=300, show_spinner=False)
def _reservoir_prices():
    """{code: today's price or None} of the reservoir ETFs (display only, five minutes)."""
    return {c: nh_price(c) for c in L.RESERVOIRS}


def reservoir_panel(key, prefix, root, capital=None, held=None):
    """저수지(대기 현금) ETF of book `key` (user 2026-11-19): where the money the strategy is not investing waits. 'auto'
    keeps KOFR from L.RESERVOIR_AUTO_KRW up and the CD-rate ETF (half the share price) below. One at a time: a book still
    holding another reservoir ETF switches once that is sold out. Saved in vibe-longterm settings books.<key>.reservoir."""
    settings = L.load_settings(root)
    book = settings['books'].get(key) or L.clean_book({})
    now = L.reservoir_code(settings, key, capital, held)
    st.markdown('##### 저수지 ETF · 투자할 종목이 없을 때 현금을 두는 곳')
    prices = _reservoir_prices()
    st.dataframe(pd.DataFrame([{'': '▶ 지금' if c == now else '', '종목': n, '코드': c,
                                '1주 가격': f'{prices[c]:,}원' if prices.get(c) else '조회 안 됨', '성격': RESERVOIR_NOTES[c]}
                               for c, n in L.RESERVOIRS.items()]), hide_index=True, width='stretch')
    choice = st.radio('저수지 ETF 고르기', list(L.RESERVOIR_CHOICES), format_func=L.RESERVOIR_CHOICES.get, horizontal=True,
                      index=list(L.RESERVOIR_CHOICES).index(book['reservoir']), key=f'{prefix}lt_res_{key}',
                      help=f"자동: 운용금 {L.RESERVOIR_AUTO_KRW // 10_000:,}만 원 이상이면 KOFR, 그보다 작으면 1주 가격이 절반인 CD금리. "
                           '1주 가격이 낮을수록 작은 계좌에서 남는 현금이 줄어듭니다. 주문은 1주 단위입니다.')
    picked = L.reservoir_code({'books': {key: {'reservoir': choice}}}, key, capital)
    if picked != now:
        st.info((f'지금 {L.RESERVOIRS[now]}를 들고 있습니다. 다음 매매(평일 09:40)에 그것을 전량 팔고 그 돈을 {L.RESERVOIRS[picked]}로 옮깁니다'
                 '(가상 체결이 꺼져 있으면 계획에만 적습니다).' if key == 'actual' else
                 f'지금 {L.RESERVOIRS[now]}를 들고 있어 그 종목을 다 판 뒤부터 {L.RESERVOIRS[picked]}로 바뀝니다.')
                + ' 한 장부는 저수지 ETF를 한 종목만 들어 평가 가격이 섞이지 않게 합니다.', icon=':material/swap_horiz:')
    if st.button('저수지 ETF 저장', key=f'{prefix}lt_res_save_{key}', type='primary', icon=':material/save:', disabled=choice == book['reservoir']):
        try:
            L.save_settings(root, {'books': {key: {'reservoir': choice}}})
        except (OSError, ValueError) as exc:
            st.error(f'저장하지 못했습니다: {exc}')
        else:
            st.toast('저수지 ETF를 저장했습니다. 다음 주문 계획부터 적용합니다.', icon=':material/check_circle:')


RESERVOIR_NOTES = {'449170': 'KOFR(무위험 지표금리) 추종 · 매일 이자가 쌓임 · 1주가 비쌈',
                   '357870': 'CD 91일물 금리 추종 · 매일 이자가 쌓임 · 거래량 많음',
                   '272580': '만기 짧은 국공채·우량채 운용 · 금리 변화에 조금 흔들림 · 거래량 적음'}


def yeo_instruments(cfg, res=None):
    """[{역할, 종목, 코드, 비율, 기준 지수, 저수지 몫}]: the ETFs a 여운봉 저수지 매매법 setting buys and holds, by name
    (res: the book's reservoir ETF, L.reservoir_code)."""
    import yeo_method as Y
    rows = []
    for index, risk, safe, _, steps, share in Y.pairs(cfg):
        when = ' · '.join(f'{lv:.0%} {u:.0%}' for lv, u in steps).replace('-', '−')
        for role, key, part in (('위험(성장)', risk, cfg['risk']), ('안전(채권 혼합)', safe, 1 - cfg['risk'])):
            rows.append({'역할': role, '종목': L.asset_name(key), '코드': L.code_of(key), '비율': f'{part:.0%}',
                         '기준 지수': f'{Y.INDEX[index][3]} 전고점 대비 낙폭 (단계: {when})', '저수지 몫': f'{share:.0%}'})
    code = L.code_of(Y.RESERVOIR, res)
    rows.append({'역할': '저수지(대기)', '종목': L.RESERVOIRS.get(code, L.asset_name(Y.RESERVOIR)), '코드': code, '비율': '—',
                 '기준 지수': '다음 단계 몫은 현금, 나머지는 저수지 ETF로 대기' if cfg['park'] else '단계 전까지 현금으로 대기(저수지 ETF를 사지 않음)', '저수지 몫': '—'})
    return rows


def yeo_panel(key, prefix, root, saved, capital=None):
    """여운봉 저수지 매매법 settings (every number adjustable) and what they do now: the drawdown from the all-time high, the
    distance to the next step, each step's KRW and whole shares at 70 : 30, the reservoir and the next periodic rebalance.
    Returns the setting to save (yeo_method.clean)."""
    import yeo_method as Y
    p = f'{prefix}yeo_{key}'
    a, b = st.columns(2)
    market = a.selectbox('시장', list(Y.MARKETS), index=list(Y.MARKETS).index(saved['market']), format_func=Y.MARKETS.get, key=f'{p}_market',
                         help='지금은 미국 지수만 씁니다(사용자 확정). 코스피는 ^KS11 전고점 대비 −30/−45/−55%에서 30/40/30%, 짝은 069500 + 183700.')
    us = b.selectbox('미국 지수', list(Y.US_INDEX), index=list(Y.US_INDEX).index(saved['us_index']), format_func=Y.US_INDEX.get,
                     key=f'{p}_us', help='나스닥100: 379810 + 0261H0(혼합50). S&P500: 379800 + 438080(후보 0080X0·0057H0).')
    c, d, e = st.columns(3)
    risk = c.slider('위험 비중(%)', 50, 90, int(round(saved['risk'] * 100)), key=f'{p}_risk',
                    help='단계 매수와 리밸런싱의 위험 : 안전 비율입니다. 기본 70 : 30(379810 : 0261H0).')
    band = d.slider('즉시 리밸런싱 밴드(%p)', 2, 10, int(round(saved['band'] * 100)), key=f'{p}_band',
                    help='보유가 생긴 뒤 위험 비중이 목표에서 이만큼 벗어나면 그날 되돌립니다. 기본 5%p.')
    e.markdown('**정기 리밸런싱**')
    e.caption('매월·분기 첫 확인일마다(분기 초도 매월에 포함) 70 : 30으로 되돌립니다. 밴드 밖이면 그날 바로 맞춥니다.')
    st.markdown('**저수지 단계** :gray[· 전고점 대비 낙폭에서 그 하락 주기 1단계 때 저수지의 몇 %를 쓸지]')
    steps = st.data_editor(pd.DataFrame([{'낙폭(%)': round(dd * 100), '저수지 사용(%)': round(u * 100)} for dd, u in saved['steps']]),
                           key=f'{p}_steps', hide_index=True, width='stretch', num_rows='dynamic',
                           column_config={'낙폭(%)': st.column_config.NumberColumn(min_value=-60, max_value=-5, step=1),
                                          '저수지 사용(%)': st.column_config.NumberColumn(min_value=0, max_value=100, step=1)})
    rows = [[r['낙폭(%)'] / 100, r['저수지 사용(%)'] / 100] for r in steps.to_dict('records')
            if pd.notna(r['낙폭(%)']) and pd.notna(r['저수지 사용(%)'])]
    park = st.toggle('다음 단계에 쓰지 않는 저수지 현금을 저수지 ETF로', value=saved['park'], key=f'{p}_park',
                     help='다음 단계 몫(예: 1단계 40%)은 늘 현금으로 남기고, 그보다 많은 현금만 저수지 ETF를 삽니다. 단계를 쓰고 나면 그다음 단계 몫을 '
                          '미리 팔아 현금으로 만듭니다. 전날 산 ETF를 판 돈은 그날 주문가능금액에 잡히지 않아 단계 당일에 팔면 매수가 막히기 때문입니다. '
                          '하루에 두 단계를 건너뛰면 두 번째 몫은 매도 대금이 들어온 뒤(약 2거래일)에 삽니다.'
                          + ('' if key == 'actual' else ' 실주문이 켜져 있으면 실제 주문이 나갑니다.'))
    cfg = Y.clean({**saved, 'market': market, 'us_index': us, 'risk': risk / 100, 'band': band / 100, 'steps': rows, 'park': park})
    st.markdown('**이 설정으로 사는 종목** :gray[· 단계가 오면 저수지 돈을 위험 : 안전 비율로 나눠 삽니다]')
    import vibe_longterm_view as VV
    held_now, cash_now = VV.holdings(root) if key == 'actual' else ({}, None)
    res = L.reservoir_code(L.load_settings(root), key, L.CAPITAL if key == 'actual' else capital, held_now)
    st.dataframe(pd.DataFrame(yeo_instruments(cfg, res)), hide_index=True, width='stretch')
    # what it does now
    plan = L.read_state(root, ('plan',)).get('plan') or {}
    ath = (plan.get('indicators') or {}).get('ath') or {}
    today = {}
    if key != 'actual':
        try:
            import longterm_live
            from src.config.paths import get_runtime_root
            today = longterm_live.load_state(get_runtime_root() / 'nhplug', key)
        except Exception:  # noqa: BLE001 - display only
            today = {}
    info = today.get('info') or {}
    if key == 'actual':  # the reservoir the rule spends: the virtual book's cash less the 적립식's own money, plus its KOFR
        import longterm_dca
        res_qty = (held_now.get(res) or (0,))[0]
        res_price = (latest_prices({}, VV.load(root)[1]).get('RES') or (None,))[0] or nh_price(res) or 0
        waiting = longterm_dca.reserved(L.read_state(root, ('dca:actual',)).get('dca:actual') or {})
        invested = 0
        reservoir_base = max(0, (cash_now if cash_now is not None else L.CAPITAL) - waiting) + res_qty * res_price
    else:
        invested = (info.get('invested') or 0) - round(((info.get('weights') or {}).get('RES') or 0) * (info.get('nav') or 0))  # the reservoir ETF is reservoir
        reservoir_base = max(0, (capital or 0) - invested)
    ystate = ((today.get('book') if key != 'actual' else L.read_state(root, ('book:actual',)).get('book:actual')) or {}).get('yeo') or {}
    lines = []
    for index, _, _, symbol, steps_, _ in Y.pairs(cfg):
        a_ = ath.get(index) or {}
        used = (ystate.get(index) or {}).get('used') or []
        nxt = next((lv for lv, _ in steps_ if lv not in used), None)
        dd = a_.get('dd')
        lines.append(f"{symbol} 전고점 {a_.get('peak', 0):,.0f}({a_.get('peak_day', '—')}) 대비 **{dd:.1%}**" if type(dd) in (int, float) else f'{symbol} 전고점 자료 없음(다음 아침 판단부터)')
        if nxt is not None and type(dd) in (int, float):
            lines[-1] += (f" · 다음 단계 {nxt:.0%}까지 {(dd - nxt) * 100:.1f}%p" if dd > nxt else f' · {nxt:.0%} 단계 도달')
        if used:
            lines[-1] += f" · 쓴 단계 {', '.join(f'{u:.0%}' for u in used)}"
    held = (any((held_now.get(L.code_of(k)) or (0,))[0] for k in Y.keys(cfg)) if key == 'actual' else
            info.get('weights') and any(k in (info.get('weights') or {}) and info['weights'][k] for k in Y.keys(cfg)))
    st.markdown('**지금 적용** · ' + ' / '.join(lines))
    recorded = latest_prices(today, VV.load(root)[1])  # the newest recorded plan prices (no broker call), else an NH quote
    prices = {k: (recorded.get(k, (None,))[0] if k in L.ASSETS else None) or nh_price(L.code_of(k)) for k in Y.keys(cfg)}
    plan_rows = Y.share_plan(cfg, reservoir_base, prices, L.FEE)
    first = Y.pairs(cfg)[0]
    table = [{'단계': f"{i}단계 {r['level']:.0%}".replace('-', '−'), '저수지 사용': f"{r['use']:.0%}", '금액': f"{r['krw']:,}원",
              f"{L.asset_name(first[1])} 금액": f"{r[first[1]]['krw']:,}원", f"{L.asset_name(first[1])} 주 수": r[first[1]]['qty'],
              f"{L.asset_name(first[2])} 금액": f"{r[first[2]]['krw']:,}원", f"{L.asset_name(first[2])} 주 수": r[first[2]]['qty']}
             for i, r in enumerate(plan_rows, 1)]
    st.dataframe(pd.DataFrame(table), hide_index=True, width='stretch')
    price_text = ' · '.join(f'{L.asset_name(k)} {v:,.0f}원' if v else f'{L.asset_name(k)} 가격 없음' for k, v in prices.items())
    nxt_reb = ('보유가 생긴 뒤부터(지금은 리밸런싱할 보유가 없음)' if not held else
               f"다음 달 첫 확인일(매월·분기) · 즉시 기준 ±{cfg['band'] * 100:.0f}%p")
    st.caption(f"저수지(기준 금액) {reservoir_base:,.0f}원" + (f' · 운용금 {capital:,}원' if capital else '') + f' · 정기 리밸런싱: {nxt_reb}'
               + f' · 가격: {price_text}. 주 수는 1주 단위(수수료 포함)입니다.')
    import longterm_dca
    dca = longterm_dca.clean(L.load_settings(root)['dca'].get(key))
    st.info('**위 금액은 지금 저수지로 계산한 예시입니다.** 실제 단계 금액은 그 하락 주기의 1단계가 오는 날의 저수지(현금 + 저수지 ETF)로 정해지고, '
            '2·3단계도 그 1단계 금액을 기준으로 나눕니다. 그 전에 적립식으로 돈이 들어오거나 잔고가 바뀌면 금액도 달라집니다.'
            + (" 지금 이 장부는 적립식이 '입금할 때'로 켜져 있어, 운용금을 올린 만큼만 적립식이 나눠 삽니다(그 몫은 저수지에 넣지 않음)."
               if dca['on'] and dca['frequency'] == 'deposit' else
               f" 지금 이 장부는 적립식이 켜져 있어 {longterm_dca.FREQUENCIES[dca['frequency']]} "
               f"{'종목별 ' + format(dca['shares_n'], ',') + '주' if dca['size'] == 'shares' else format(dca['amount_krw'], ',') + '원'}이 들어옵니다"
               "(적립식이 산 몫과 대기 자금은 저수지에 넣지 않음)." if dca['on'] else ''), icon=':material/info:')
    return cfg


def strategy_panel(key, prefix='', root=None, capital=None):
    """계좌별 전략 (user 2026-11-18): '엔진(가상 계좌)과 같음' (follows the engine's modules, nothing copied) or '이 계좌 전략'
    (its own master modules; the first time it starts from the engine's). Either way the chosen set is kept, so the two can be
    switched any time. The regime, stories and indicators stay the engine's; saved in vibe-longterm settings books.<key>."""
    root = root or L.ROOT
    settings = L.load_settings(root)
    book = settings['books'].get(key) or L.clean_book({})
    plan = L.read_state(root, ('plan',)).get('plan') or {}
    regime = plan.get('regime')
    engine = plan.get('active') or L.active_modules(settings, L.recommended_set(regime))
    import longterm_strategy_guide as G
    head, guide = st.columns([4, 1], vertical_alignment='center')
    head.markdown('#### 전략 · 계좌 운용금의 매매')
    with guide:
        G.guide_button(f'{prefix}lt_book_guide_{key}')
    st.caption(f'지금 {L.book_strategy_label(settings, key)} · 거장 모듈(저수지 매매법 등)이 이 계좌 운용금을 사고팝니다. '
               '들어오는 적립금은 아래 "적립식"에서 따로 정합니다.')
    choice = st.radio('이 계좌가 따를 거장 모듈', list(L.BOOK_STRATEGIES), index=list(L.BOOK_STRATEGIES).index(book['strategy']),
                      format_func=L.BOOK_STRATEGIES.get, horizontal=True, key=f'{prefix}lt_book_strategy_{key}',
                      help='엔진과 같음: 가상 계좌가 그날 쓰는 거장 모듈을 그대로 따라갑니다(값을 복사해 두지 않아 엔진을 바꾸면 이 계좌도 바뀜). '
                           '이 계좌 전략: 이 계좌만 다른 거장 조합으로 주문을 계산합니다(처음엔 지금 엔진 모듈로 시작). 둘 사이를 언제든 바꿀 수 있고 '
                           '고른 조합은 저장해 둡니다. 국면·테마 스토리·지표는 어느 쪽이든 엔진 것을 씁니다.')
    start = book['modules'] if book['modules'] is not None else {m: m in engine for m in L.MODULES}
    yeo_cfg = None
    if choice == 'yeo_method':
        st.caption('여운봉 저수지 매매법: 거장 모듈을 쓰지 않고 이 규칙만 씁니다. 보유 0에서 시작해 나스닥100 전고점 대비 −20/−30/−40%에서 저수지 '
                   '40/40/20%를 379810 70 : 0261H0 30으로 사고, 보유가 생긴 뒤 매월·분기 첫 확인일과 5%p 이탈 즉시 70:30으로 되돌립니다. 산 몫을 팔아 저수지를 '
                   '채우지 않습니다. 12주 분할과 버핏 월 매도 상한은 걸지 않습니다.')
        import yeo_method as Y
        st.caption(f'재현(실제 규칙 코드, 보유 0·현금 시작): {Y.RESEARCH}')
        yeo_cfg = yeo_panel(key, prefix, root, book['yeo'], capital)
        modules = {m: False for m in L.MODULES}
    elif choice == 'custom':
        def apply_preset():
            picked = st.session_state.get(f'{prefix}lt_book_preset_{key}')
            if picked in L.BOOK_PRESETS:
                for m in L.MODULES:
                    st.session_state[f'{prefix}lt_book_mod_{key}_{m}'] = m in L.BOOK_PRESETS[picked][1]
        st.selectbox('빠른 프리셋', list(L.BOOK_PRESETS), index=None, placeholder='프리셋을 고르면 아래 모듈이 채워집니다',
                     format_func=lambda p: f'{L.BOOK_PRESETS[p][0]} · 재현 {L.BOOK_PRESET_RESEARCH.get(p, "재현 전")}',
                     key=f'{prefix}lt_book_preset_{key}', on_change=apply_preset)
        modules = module_toggles(f'{prefix}lt_book_mod_{key}', start, regime)
        if book['modules'] is None:
            st.caption('처음 고르는 전략이라 지금 엔진(가상 계좌) 모듈로 채워 두었습니다.')
    else:
        modules = start
        st.caption('엔진 모듈: ' + ', '.join(f'{L.MODULES[m][0]} {L.MODULES[m][1]}' for m in engine)
                   + (' · 이 계좌 전략으로 고른 조합은 저장돼 있어 다시 바꾸면 그대로 돌아옵니다.' if book['modules'] is not None else ''))
    if plan.get('indicators') and choice != 'yeo_method':
        stories = (L.read_state(root, ('story',)).get('story') or {}).get('stories') or {}
        try:
            import longterm_live
            from src.config.paths import get_runtime_root
            book_state = longterm_live.load_state(get_runtime_root() / 'nhplug', key).get('book') or {}
        except Exception:  # noqa: BLE001 - the comparison is for display; a missing state means a fresh account
            book_state = {}
        chosen = sorted(m for m, on in modules.items() if on) if choice == 'custom' else engine
        with st.expander(f"오늘({plan.get('day')}) 목표 비중 비교 · 가상 계좌 대 이 계좌", icon=':material/compare_arrows:', expanded=choice == 'custom'):
            st.dataframe(target_compare(plan, stories, book_state, chosen), hide_index=True, width='stretch')
            st.caption('저장 전 화면의 선택으로 계산합니다. 첫 투입·월 밴드·매도 상한 같은 주문 크기는 이 비중을 이 계좌 운용금에 맞춰 정합니다.')
    changed = (choice != book['strategy'] or (choice == 'custom' and (book['modules'] is None or modules != book['modules']))
               or (choice == 'yeo_method' and yeo_cfg != book['yeo']))
    confirmed = True
    if changed and plan.get('indicators') and choice != 'yeo_method':  # a lower target means sells at the next 09:40 plan for what the account holds
        current = engine if book['strategy'] != 'custom' or book['modules'] is None else sorted(m for m, on in book['modules'].items() if on)
        cuts = target_cuts(plan, stories, book_state, current, chosen)
        if cuts:
            st.warning('이 변경으로 목표 비중이 줄어드는 종목: ' + ', '.join(f'{n} {b:.1%} → {a:.1%}' for n, b, a in cuts)
                       + '. 이 종목을 갖고 있으면 다음 주문 계획(평일 09:40)에서 그만큼 팔 수 있습니다'
                       + ('(월 매도 상한은 버핏 저회전이 켜져 있을 때만 걸림).' if 'buffett_lowturn' not in chosen else '.'),
                       icon=':material/warning:')
            confirmed = st.checkbox('줄어드는 비중만큼 팔 수 있음을 확인했습니다', key=f'{prefix}lt_book_sell_ok_{key}')
    if changed and choice == 'yeo_method' and book['strategy'] != 'yeo_method':  # the method rebalances what it holds 70 : 30 (review5)
        import yeo_method as Y
        st.warning(Y.switch_note(yeo_cfg), icon=':material/warning:')
        confirmed = st.checkbox('보유한 위험·혼합 ETF를 정한 비율로 맞추려고 팔 수 있음을 확인했습니다', key=f'{prefix}lt_book_yeo_ok_{key}')
    if st.button('전략 저장', key=f'{prefix}lt_book_save_{key}', icon=':material/save:', type='primary',
                 disabled=not confirmed):
        try:
            L.save_settings(root, {'books': {key: {'strategy': choice, **({'modules': modules} if choice == 'custom' else {}),
                                                   **({'yeo': yeo_cfg} if yeo_cfg else {})}}})
        except (OSError, ValueError) as exc:
            st.error(f'저장하지 못했습니다: {exc}')
        else:
            st.toast('계좌 전략을 저장했습니다. 다음 주문 계획(평일 09:40)부터 적용합니다.', icon=':material/check_circle:')
    reservoir_panel(key, prefix, root, capital)


def settings_panel(key, record, capital, prefix=''):
    """계좌·자동매매 → 장기투자를 고른 실계좌 (docs/longterm-account-settings-plan.md 1절): 운용 상태 · 포트폴리오 · 적립식, then the
    head of 보호 한도 (장기 전용) with the engine's monthly sell cap; app.account_form adds that section's inputs below."""
    import longterm_dca as D
    import longterm_dca_view as DV
    import vibe_longterm_view as VV
    from src.config.paths import get_runtime_root
    record = {**record, 'budget': {**(record.get('budget') or {}), 'capital_krw': capital}}
    data = status(key, record, get_runtime_root() / 'nhplug')
    settings = L.load_settings(L.ROOT)
    st.info('이 계좌는 **장기투자 엔진**을 따릅니다. 엔진이 08:45에 정한 국면·목표 비중을 이 계좌 운용금에 맞춰 09:40에 하루치 주문을 '
            '계산하고(이 계좌 전략의 거장 모듈 · 첫 투입은 엔진의 초기 투입 주 수만큼 주 1회 분할 · 월 매도 상한), 실계좌 보호 경로로 한 건씩 냅니다.', icon=':material/savings:')
    strategy_panel(key, prefix, capital=capital)

    st.markdown('**운용 상태**')
    summary_cards(data)
    if not data['service']:
        st.warning(f'이 계좌({key})의 장기투자 실행기 서비스가 정의돼 있지 않습니다. 저장해도 주문 계획이 만들어지지 않습니다.',
                   icon=':material/warning:')

    st.markdown(f"**포트폴리오** :gray[· 목표 비중 × 운용금 · {data['basis']} 기준]")
    prices = latest_prices(data['today'], VV.load(L.ROOT)[1])
    dca = settings['dca'].get(key) or D.clean({})
    rows = affordability(capital, data['weights'], prices, record['budget'].get('order_price_max_krw') or 0, dca)
    st.dataframe(rows, hide_index=True, width='stretch')
    need, count, missing = min_capital(data['weights'], prices)
    can = sum(r['상태'].startswith('살 수 있음') for r in rows)
    if count:
        st.markdown(f"운용금이 **{need:,}원** 이상이면 {count}종 모두 1주 이상 살 수 있습니다 "
                    f":gray[· 지금 운용금 {capital:,}원으로는 {can}종]")
    if missing:
        st.caption(f"가격 기록이 없어 계산에서 뺀 ETF: {', '.join(missing)}")
    st.caption('1주 가격은 이 계좌·엔진이 기록한 가장 최근 주문 계획의 가격입니다(증권사를 새로 조회하지 않음, 날짜는 "가격 기준" 열). '
               f"주식 수는 목표 금액을 매수 가격 + 수수료 {L.FEE:.3%}로 나눈 1주 단위입니다. 첫 투입 기간에는 하루 매수 상한"
               f"(순자산의 {settings['tuning']['daily_buy_share']:.0%})이 있어 하루에 사는 몫은 이보다 작습니다.")

    st.markdown('#### 적립식 · 들어오는 적립금')
    st.caption('적립금을 언제·얼마나(금액 또는 종목별 N주) 사는지입니다. 계좌 운용금의 매매(위 "전략")와 따로 설정합니다.')
    with st.container(border=True):
        st.markdown((f':green[켜짐] · {DV.schedule_text(dca)}' if dca['on'] else ':gray[꺼짐]') + f" :gray[· 장부 {record.get('label', key)}]")
        go, note = st.columns([1, 3], vertical_alignment='center')
        go.button('장기투자 → 전략 → 적립식', key=f'{prefix}lt_dca_go_{key}', type='tertiary', icon=':material/open_in_new:', on_click=open_dca)
        note.caption('적립식 설정 원본은 장기투자 → 전략 → 적립식 한 곳입니다. 여기서는 보기만 하고, 켜기·금액·방식·매수 제한은 그 탭에서 바꿉니다.')

    st.markdown('**보호 한도 (장기 전용)** :gray[· 저장값·저장 경로는 그대로(accounts.json, 설정 변경 이력). 아래 네 칸은 실계좌 가드가 '
                '주문마다 확인하고, 월 매도 한도는 엔진이 주문을 계산할 때 지킵니다.]')
    sell = protection_rows(record, capital, settings)[0]
    st.markdown(f"월 매도 한도 :gray[(엔진 · 읽기 전용)] **{sell['지금 값']}**")
    st.caption(sell['장기 계좌에서'] + ' 첫 투입일 예외: 이 계좌가 처음 사는 날에는 하루 새 종목을 '
               f'{FIRST_DAY_NEW_SYMBOLS}개까지 허용합니다(2026-11-18 승인, 하루 새 종목 수가 5일 때).')
    st.caption('Jev 트레이딩으로 산 보유 종목은 장기투자 실행기가 사거나 팔지 않습니다. 정리하려면 트레이딩 현황 → 즉시 매도를 쓰세요.')


def status_card(key, record, nhplug, *, data=None):
    """트레이딩 현황 → 실계좌: the long-term account's day in one card."""
    if data is None:
        data = status(key, record, nhplug)
    with st.container(border=True):
        st.markdown(f"**{record.get('label', key)}** · 장기투자")
        summary_cards(data)
        today = datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()
        rows = order_rows(data['today']) if data['today'].get('day') == today else []
        if rows:
            st.dataframe(rows, hide_index=True, width='stretch')
        elif data['today'].get('day') == today:
            st.caption('오늘 계좌 판단을 기록했습니다. 주문 계획은 0건입니다.')
        else:
            st.caption('오늘 계좌 주문 계획이 아직 기록되지 않았습니다. 첫 주문 판단은 09:40부터입니다.')
        st.caption('보호 장치: ' + PROTECTION)


def open_dca():
    import vibe_longterm_view
    st.session_state['main_tab'] = '장기투자'
    vibe_longterm_view.goto(st.session_state, '적립식')


def linked_accounts(records):
    """[(key, label, mode)] of the live accounts set to 장기투자 (장기투자 탭의 연결 실계좌)."""
    from accounts import longterm_accounts
    return [(k, r.get('label', k), '실주문' if r.get('auto') is True and r.get('live_orders') is True else
             '계획만' if r.get('auto') is True else '멈춤') for k, r in longterm_accounts(records).items()]



# ---------- 실계좌 데스크 (user 2026-11-18: 실계좌 2 as full as 실계좌 1, in long-term terms) ----------

def shadow_summary(rows, nav, contributed):
    """The account's strategy run on paper (vibe_longterm.account_nav rows [(day, value, capital)]) at a glance:
    {value, gain, pct, mdd, days, start, series [(day, account %, engine %)]}, or None before its first close. Both
    curves start at the account's first day; the engine's 적립 paid in since then is taken out of its curve."""
    if not rows:
        return None
    from vibe_longterm_replay import max_drawdown
    start, capital = rows[0][0], rows[0][2]
    engine = {d: v for d, b, v in nav if b == 'actual'}
    base_day = next((d for d in sorted(engine) if d >= start), None)
    paid = lambda d: max((v for k, v in contributed.items() if k <= d), default=0)
    curve = lambda d: (round(((engine[d] - (paid(d) - paid(base_day))) / engine[base_day] - 1) * 100, 2)
                       if base_day and d in engine and d >= base_day else None)
    value = rows[-1][1]
    return {'value': value, 'gain': value - capital, 'pct': round((value / capital - 1) * 100, 2), 'capital': capital,
            'mdd': max_drawdown([v for _, v, _ in rows]), 'days': len(rows), 'start': start,
            'series': [(d, round((v / capital - 1) * 100, 2), curve(d)) for d, v, _ in rows]}


def flow_dot(data, strategy, regime, service):
    """The account's day as a graph: the engine's 08:45 call → this account's strategy → its 09:40 plan → the live guard
    → live order or a plan only (the way taken today drawn bold) → the 15:40 virtual run of the same strategy."""
    live = data['mode'] == '실주문'
    q = lambda s: str(s).replace('"', "'")
    bold, faint = 'penwidth=2.4, color="#a855f7"', 'style=dashed, color="#94a3b8"'
    return f'''digraph G {{
  rankdir=LR; bgcolor="transparent"; node [shape=box, style="rounded,filled", fillcolor="#f1f5f9", fontname="Pretendard", fontsize=11];
  engine [label="장기 엔진 08:45\\n국면 {q(regime)}"]; strategy [label="이 계좌 전략\\n{q(strategy)}"];
  plan [label="09:40 계좌 계획\\n목표 비중 × 운용금 − 보유"]; guard [label="실계좌 가드\\n운용금 · 1주 상한 · 회당 한도 · 새 종목"];
  order [label="NH 주문\\n체결 대조"]; record [label="계획만 기록\\n(실주문 꺼짐)"]; shadow [label="15:40 가상 운용\\n같은 전략 · 운용금 · 1주 단위"];
  runner [label="실행기 {q(service)}\\n평일 09:35~14:40", shape=note];
  engine -> strategy -> plan -> guard; runner -> plan [style=dotted];
  guard -> order [{bold if live else faint}, label="실주문"]; guard -> record [{faint if live else bold}, label="{q(data['mode'])}"];
  strategy -> shadow [style=dotted, label="매일 마감"];
}}'''


def flow_spec(data, strategy, regime, service):
    """flow_dot as an Archify workflow: today's way (실주문 or 계획만) is the main path."""
    import guide_ui as GU
    live = data['mode'] == '실주문'
    return GU.workflow('이 계좌는 어떻게 움직이나', [('plan', '판단·계획'), ('guard', '보호'), ('out', '결과'), ('paper', '가상 운용')],
        [('engine', 'plan', 0, '장기 엔진 08:45', f'국면 {regime}', 'security'), ('strategy', 'plan', 1, '이 계좌 전략', str(strategy), 'frontend'),
         ('plan', 'plan', 2, '09:40 계좌 계획', '목표 비중 × 운용금 − 보유'),
         ('runner', 'guard', 1, f'실행기 {service}', '평일 09:35~14:40', 'messagebus'),
         ('guard', 'guard', 3, '실계좌 가드', '운용금·1주 상한·회당 한도·새 종목', 'security'),
         ('order', 'out', 4, 'NH 주문', '체결 대조', 'external'), ('record', 'out', 5, '계획만 기록', '실주문 꺼짐', 'database'),
         ('shadow', 'paper', 4, '15:40 가상 운용', '같은 전략·운용금·1주 단위', 'database')],
        [('engine', 'strategy', None, 'main'), ('strategy', 'plan', None, 'main'), ('plan', 'guard', None, 'main'), ('runner', 'plan', None, 'async'),
         ('guard', 'order', '실주문', 'main' if live else 'branch'), ('guard', 'record', data['mode'], 'branch' if live else 'main'),
         ('strategy', 'shadow', '매일 마감', 'async')],
        main=['engine', 'strategy', 'plan', 'guard', 'order' if live else 'record'])


def daily_balance_history(path):
    """Latest valid broker observation per KST date, including the current partial day."""
    from overview import balance_records

    days = {}
    for row in balance_records(path):
        try:
            stamp = datetime.fromisoformat(row['time'])
        except ValueError:
            continue
        if stamp.tzinfo is None or not math.isfinite(row['total_assets']) or row['total_assets'] < 0:
            continue
        stamp = stamp.astimezone(ZoneInfo('Asia/Seoul'))
        day = stamp.date().isoformat()
        if day not in days or stamp >= days[day][0]:
            days[day] = stamp, row
    return [(d, *days[d]) for d in sorted(days)]


def asset_chart(history):
    import altair as alt

    if not history:
        st.caption('저장된 증권사 잔고 기록이 없습니다.')
        return
    if len(history) == 1:
        st.caption('현재 1거래일 기록이 있습니다. 다음 거래일 기록부터 자산 변화를 비교할 수 있습니다.')
    frame = pd.DataFrame([{'거래일': d, '총자산': row['total_assets'], '기록 시각': stamp.strftime('%H:%M:%S')}
                          for d, stamp, row in history[-60:]])
    chart = alt.Chart(frame).mark_line(point=True, color='#16a34a').encode(
        x=alt.X('거래일:O', title=None, axis=alt.Axis(labelAngle=0, labelOverlap='greedy')),
        y=alt.Y('총자산:Q', title='원', scale=alt.Scale(zero=False), axis=alt.Axis(format=',.0f')),
        tooltip=['거래일', '기록 시각', alt.Tooltip('총자산:Q', format=',.0f')]).properties(height=210)
    st.altair_chart(chart, width='stretch', alt='실계좌의 거래일별 총자산 추이')
    st.caption('최근 60일치의 일별 마지막 증권사 잔고 기록입니다. 오늘 값은 장중 기록이며, 총자산 추이에는 입출금이 반영됩니다.')


def holding_chart(key, holdings, fills, names, chart):
    """Display the selected ETF's daily or weekly candles and recorded fills."""
    from vibe_longterm_view import daily_bars, moving, weekly

    code = st.selectbox('보유 ETF', sorted(holdings), format_func=lambda c: names[c], key=f'lt_real_chart_symbol_{key}')
    unit = st.segmented_control('봉 단위', ['일', '주'], default='일', key=f'lt_real_chart_unit_{key}') or '일'
    daily = daily_bars(code)
    if not daily:
        st.warning('일봉 기록을 불러오지 못했습니다. 체결은 주문·체결 탭에서 확인할 수 있습니다.')
        return
    bars = weekly(daily) if unit == '주' else daily
    kst = ZoneInfo('Asia/Seoul')
    dates = {datetime.fromtimestamp(b['time'], kst).date(): b['time'] for b in daily}
    weekly_times = {datetime.fromtimestamp(b['time'], kst).date().isocalendar()[:2]: b['time'] for b in bars}
    markers = []
    for fill in fills:
        if fill.get('symbol') != code:
            continue
        try:
            day = datetime.fromisoformat(fill['time']).astimezone(kst).date()
        except (ValueError, TypeError, KeyError):
            continue
        if day not in dates:
            continue
        stamp = weekly_times.get(day.isocalendar()[:2]) if unit == '주' else dates[day]
        if stamp is None:
            continue
        buy = fill['side'] == 'BUY'
        markers.append({'time': stamp, 'position': 'belowBar' if buy else 'aboveBar',
                        'color': '#22c55e' if buy else '#ef4444', 'shape': 'arrowUp' if buy else 'arrowDown',
                        'text': f"{'매수' if buy else '매도'} {fill['qty']}주 · {day:%m/%d}"})
    if chart is None:
        st.caption('TradingView 차트 컴포넌트를 사용할 수 없습니다.')
        return
    periods = (20, 50, 200) if unit == '일' else (10, 40)
    chart(bars, sorted(markers, key=lambda m: m['time']), None,
          averages={period: moving(bars, period) for period in periods}, volume=True, intraday=False,
          visible=120 if unit == '일' else 104, alt=f'{names[code]} {unit}봉과 실계좌 체결')
    st.caption('TradingView 차트 · Yahoo Finance 일봉 · ▲ 매수 / ▼ 매도는 증권사 체결 원장 기준입니다. 날짜가 일봉과 일치하는 체결만 표시합니다.')


def virtual_comparison(key):
    import altair as alt
    import vibe_longterm_view as VV

    nav, contributed = VV.nav_history(L.ROOT)
    summary = shadow_summary(L.account_nav(L.ROOT, key), nav, contributed)
    if summary is None:
        st.caption('이 계좌 전략의 가상 운용 기록이 아직 없습니다.')
        return
    st.caption(f"{summary['days']}거래일 · {summary['start']}부터 · 가상 순자산 {summary['value']:,.0f}원 · "
               f"시작일 대비 {summary['pct']:+.2f}% · 최대 낙폭 {summary['mdd'] * 100:.2f}%")
    frame = pd.DataFrame([{'거래일': day, '장부': name, '수익률(%)': value} for day, account, engine in summary['series']
                          for name, value in (('이 계좌 전략(가상)', account), ('장기 엔진(가상)', engine)) if value is not None])
    chart = alt.Chart(frame).mark_line(point=True).encode(
        x=alt.X('거래일:O', title=None, axis=alt.Axis(labelAngle=0, labelOverlap='greedy')),
        y=alt.Y('수익률(%):Q', title='시작일 대비 수익률(%)'),
        color=alt.Color('장부:N', legend=alt.Legend(orient='top', title=None)),
        tooltip=['거래일', '장부', alt.Tooltip('수익률(%):Q', format='+.2f')]).properties(height=220)
    st.altair_chart(chart, width='stretch', alt='가상 운용 두 장부의 시작일 대비 수익률')
    st.caption('가상 마감 평가 기록을 비교합니다. 실제 자산 추이는 자산·보유 탭에서 확인할 수 있습니다.')


def assets_snapshot(key, load_assets, identity=None):
    """Keep the last successful balance in this session, including across a failed refresh."""
    if load_assets is None:
        return None
    cache_key = f'lt_assets_snapshot_{key}_{identity or ""}'
    status_key = f'{cache_key}_failed'

    def fetch_balance(refresh):
        try:
            st.session_state[cache_key] = load_assets(refresh)
            st.session_state[status_key] = False
        except Exception:
            st.session_state.setdefault(cache_key, None)
            st.session_state[status_key] = True

    if st.button('보유·평가 새로고침', key=f'lt_asset_refresh_{key}', type='primary', icon=':material/refresh:'):
        with st.spinner('증권사 잔고 확인 중'):
            fetch_balance(True)
    elif cache_key not in st.session_state:
        st.caption('증권사 잔고를 불러오는 중입니다. 저장된 기록을 먼저 표시합니다.')
        armed = False

        @st.fragment(run_every=0.3)
        def initial_balance():
            nonlocal armed
            if not armed:
                armed = True
                return
            if cache_key not in st.session_state:
                fetch_balance(False)
                st.rerun()

        initial_balance()
    kept = st.session_state.get(cache_key)
    if st.session_state.get(status_key):
        st.warning('증권사 잔고 조회에 실패했습니다. ' + ('마지막 성공 조회 결과를 표시합니다.' if kept else '평가 수치는 확인 불가입니다.'))
    return kept


def display_amount(value, signed=False, pct=False):
    if type(value) not in (int, float) or not math.isfinite(value):
        return '—'
    if pct:
        return f'{value:+.2f}%'
    return f'{value:+,.0f}원' if signed else f'{value:,.0f}원'


def allocation_comparison(weights, holdings, capital, names, reservoir):
    """Compare on the configured capital base and keep unavailable valuations unknown."""
    targets = {L.code_of(asset, reservoir): weight for asset, weight in weights.items() if weight > 0}
    positions = holdings or {}
    rows = []
    for code in dict.fromkeys([*targets, *positions]):
        position = positions.get(code)
        evaluation = position.get('평가금액') if position is not None else (0 if holdings is not None else None)
        if type(evaluation) not in (int, float) or not math.isfinite(evaluation) or evaluation < 0:
            evaluation = None
        target = targets.get(code, 0) * 100 if targets else None
        actual = evaluation / capital * 100 if evaluation is not None and capital > 0 else None
        rows.append({'종목': names.get(code, code), '목표 비중': target, '실계좌 비중': actual,
                     '비중 차이': actual - target if actual is not None and target is not None else None,
                     '목표 금액': target * capital / 100 if target is not None and capital > 0 else None,
                     '평가금액': evaluation})
    return rows


def allocation_chart(rows):
    """One fixed-height lane per ETF. The target is a tick on the actual bar's scale."""
    import altair as alt

    base = alt.Chart(pd.DataFrame(rows)).encode(
        y=alt.Y('종목:N', title=None, sort=[row['종목'] for row in rows],
                axis=alt.Axis(labelLimit=235), scale=alt.Scale(paddingInner=0.25, paddingOuter=0.25)),
        tooltip=['종목:N', alt.Tooltip('목표 비중:Q', title='목표 (%)', format='.1f'),
                 alt.Tooltip('실계좌 비중:Q', title='실계좌 (%)', format='.1f'),
                 alt.Tooltip('비중 차이:Q', title='실계좌 − 목표 (%p)', format='+.1f')])
    axis = alt.Axis(title='운용금 대비 (%)', format='.0f', tickCount=7)
    actual = base.mark_bar(color='#16a34a', size=14, cornerRadiusEnd=3).encode(
        x=alt.X('실계좌 비중:Q', axis=axis, scale=alt.Scale(zero=True)))
    target = base.mark_tick(color='#64748b', thickness=3, size=26, orient='vertical').encode(
        x=alt.X('목표 비중:Q', axis=axis, scale=alt.Scale(zero=True)))
    return (actual + target).properties(height=alt.Step(42))


def portfolio_panel(rows, capital, basis):
    from dialogs import table_frame

    st.markdown('##### 목표 비중과 실계좌 평가 비중')
    if not rows or capital <= 0:
        st.caption('전략 목표나 보유 종목, 운용금이 확인되면 비중을 비교할 수 있습니다.')
        return
    st.caption(f'운용금 {capital:,}원 기준 · {basis} · 막대는 실계좌 평가, 세로선은 전략 목표')
    st.altair_chart(allocation_chart(rows), width='stretch', alt='종목별 실계좌 평가 비중과 전략 목표 비중')
    table_frame(pd.DataFrame(rows)[['종목', '목표 비중', '실계좌 비중', '비중 차이', '목표 금액', '평가금액']],
                alt='종목별 목표와 실계좌 비중 차이 및 평가금액',
                column_config={'목표 비중': st.column_config.NumberColumn('목표 비중', format='%.1f %%'),
                               '실계좌 비중': st.column_config.NumberColumn('실계좌 비중', format='%.1f %%'),
                               '비중 차이': st.column_config.NumberColumn('실계좌 − 목표', format='%+.1f %%p'),
                               '목표 금액': st.column_config.NumberColumn('목표 금액', format='localized'),
                               '평가금액': st.column_config.NumberColumn('평가금액', format='localized')})
    if any(row['실계좌 비중'] is None for row in rows):
        st.caption('증권사 평가금액이 확인되지 않은 종목은 실계좌 비중을 비워 둡니다.')


def activity_panel(plan, state, fills, events, dca):
    import longterm_dca as D
    from longterm_dca_view import schedule_text

    with st.container(border=True):
        activity, savings = st.columns(2)
    with activity:
        st.markdown('**오늘 운용**')
        bought = sum(f['side'] == 'BUY' for f in fills)
        sold = sum(f['side'] == 'SELL' for f in fills)
        if plan:
            orders = plan.get('orders') or []
            progress = f"계획 {len(orders)}건 · 접수 기록 {sum(o.get('status') == 'sent' for o in orders)}건"
        else:
            progress = '계획 기록 대기'
        st.markdown(f'{progress} · 확인 체결 **매수 {bought} / 매도 {sold}건**')
        if why := (plan.get('info') or {}).get('why'):
            st.caption(why)
        if events:
            event = events[-1]
            st.caption(f"{event['time'][11:19]} KST · {event_text(event)}")
    with savings:
        st.markdown('**적립식**')
        if not dca['on']:
            st.caption('매수 꺼짐 · 알림 전용' if dca.get('alert') else '꺼짐')
        else:
            st.caption(schedule_text(dca))
            dca_state = state.get('dca')
            if dca_state is not None:
                st.markdown(f'적립 대기 자금 **{D.reserved(dca_state):,.0f}원**')
            st.caption('오늘 적립 판단 기록됨' if plan.get('dca_check') else '오늘 적립 판단 대기')


def first_step_estimate(pair, risk_ratio, account_plan, snapshot, events):
    """Estimate the stage using the account plan's free cash and any reservoir ETF valuation."""
    _, risk, safe, _, steps, share = pair
    info = account_plan.get('info') or {}
    nav, invested = info.get('nav'), info.get('invested')
    if not all(type(v) in (int, float) and math.isfinite(v) for v in (nav, invested)):
        return None, []
    reserve = 0
    if (info.get('weights') or {}).get('RES', 0) > 0:
        holdings = (snapshot or {}).get('holdings') or []
        reservoir = next((row for row in holdings if row.get('종목코드') == info.get('reservoir')), {})
        reserve = reservoir.get('평가금액')
        if type(reserve) not in (int, float) or not math.isfinite(reserve) or reserve <= 0:
            return None, []
    base = max(0, nav - invested) + reserve
    total = base * share * steps[0][1]
    quotes = {}
    for event in events:
        if event.get('action') != 'DCA_ALERT' or not event.get('time'):
            continue
        for order in event.get('orders') or []:
            if type(order.get('price')) in (int, float) and math.isfinite(order['price']) and order['price'] > 0:
                quotes[order.get('code')] = order['price'], f"{event['time'][5:16].replace('T', ' ')} 매수호가 기록"
    holdings = {row.get('종목코드'): row for row in (snapshot or {}).get('holdings') or []}
    rows = []
    for asset, fraction in ((risk, risk_ratio), (safe, 1 - risk_ratio)):
        code = L.code_of(asset)
        price, source = quotes.get(code, (None, '가격 미확인'))
        if price is None and snapshot:
            price = holdings.get(code, {}).get('현재가')
            if type(price) in (int, float) and math.isfinite(price) and price > 0:
                source = f"{snapshot['time']:%m/%d %H:%M} 증권사 현재가"
            else:
                price = None
        allocated = total * fraction
        rows.append({'종목': f'{L.asset_name(asset)} ({code})', '예상 배정액': display_amount(allocated),
                     '기준 가격': display_amount(price), '가격 근거': source,
                     '예상 수량': f'{int(allocated // (price * (1 + L.FEE))):,}주' if price else '—'})
    return base, rows


def ndx_observation_panel(root, engine_close_day, threshold):
    """Show the independent US observation alongside the domestic engine's close-based decision."""
    from ndx_intraday_alert import latest_record

    observation = latest_record(Path(root) / 'vibe-longterm' / 'ndx-intraday') or {}
    intraday, closing = st.columns(2)
    with intraday:
        st.markdown('**미국 장중 −20% 모니터**')
        session = observation.get('session') or '미확인'
        if crossing := observation.get('crossing'):
            stamp = datetime.fromisoformat(crossing['crossed_at']).astimezone(ZoneInfo('Asia/Seoul'))
            st.caption(f"장중 도달 {crossing['crossed_drawdown']:.2%} · {stamp:%m/%d %H:%M} KST · 미국 거래일 {session}")
        elif observation.get('status') in ('watching', 'finished'):
            st.caption(f"완료 1분봉에서 아직 미도달 · 미국 거래일 {session}")
        else:
            st.caption(f"{observation.get('reason') or '장중 관측 기록 대기'} · 미국 거래일 {session}")
    with closing:
        st.markdown('**미국 종가 확정 · 1단계 조건**')
        close = observation.get('close') or {}
        drawdown = close.get('drawdown')
        if type(drawdown) in (float, int) and math.isfinite(drawdown):
            close_day = close.get('session') or observation.get('session')
            verdict = '충족' if drawdown <= threshold else '미충족'
            st.caption(f"{drawdown:.2%} · {threshold:.2%} 기준 {verdict} · 미국 거래일 {close_day}")
            st.caption('국내 엔진에 같은 종가일 반영' if close_day == engine_close_day else '국내 엔진 반영 대기')
        else:
            st.caption(f"{close.get('reason') or '미국 종가 확인 대기'} · 국내 엔진 적용 종가 {engine_close_day}")


def quote_gap_panel(events):
    from dialogs import table_frame

    audited = [(e, e.get('quote_gap_audit')) for e in events if isinstance(e.get('quote_gap_audit'), dict)]
    if not audited:
        st.caption('오늘 국내 매수 호가 감사 기록이 없습니다.')
        return
    catalog = asset_names_by_code()
    rows = []
    for event, audit in reversed(audited):
        code = audit.get('symbol') or event.get('symbol') or '—'
        rows.append({'종목': f'{catalog[code]} ({code})' if code in catalog else code,
                     '확인 시각(KST)': str(audit.get('observed_at') or event.get('time', ''))[:19].replace('T', ' '),
                     '매도 1호가': display_amount(audit.get('ask')),
                     '전일 종가': display_amount(audit.get('previous_close')),
                     '전일 종가 대비': display_amount(audit.get('gap_pct'), pct=True),
                     '주문 처리': event_text(event), '호가 출처': audit.get('source') or '—',
                     '근거': audit.get('note') or '—'})
    table_frame(rows, alt='국내 장기투자 매수 직전 호가와 전일 종가의 차이')


def first_step_panel(cfg, engine_plan, account_plan, snapshot, events, fills, day, nhplug):
    if cfg is None:
        return
    import yeo_method as Y
    from dialogs import table_frame
    from longterm_step_execution import read_step_intents, step_order_rows

    pair = next((p for p in Y.pairs(cfg) if p[0] == 'ndx'), None)
    if pair is None:
        return
    threshold, use = pair[4][0]
    ath = ((engine_plan.get('indicators') or {}).get('ath') or {}).get('ndx') or {}
    drawdown = ath.get('dd')
    valid = type(drawdown) in (int, float) and math.isfinite(drawdown)
    book = ((account_plan.get('book') or {}).get('yeo') or {}).get('ndx') or {}
    used = threshold in (book.get('used') or [])
    plan_today = account_plan.get('day') == day
    engine_today = engine_plan.get('day') == day
    with st.container(border=True):
        st.markdown('##### 나스닥100 · 저수지 1단계')
        cols = st.columns(3)
        cols[0].metric('종가 기준 낙폭', f'{drawdown:.2%}' if valid else '확인 불가')
        cols[1].metric(f'1단계 기준 {threshold:.0%}까지', f'{max(0, drawdown - threshold) * 100:.2f}%p' if valid else '—')
        state = ('1단계 사용 기록 있음' if used else '판단 자료 대기' if not valid or not engine_today else
                 '계좌 계획 기록됨' if drawdown <= threshold and plan_today else
                 '조건 충족 · 계획 대기' if drawdown <= threshold else '조건 미충족')
        cols[2].metric('1단계 상태', state)
        close_day = ath.get('as_of') or '미확인'
        st.caption(f'미국 종가 기준일 {close_day} · 국내 계좌 첫 주문 판단 09:40 이후 · 1단계 저수지 {use:.0%} 사용')
        ndx_observation_panel(nhplug, close_day, threshold)
        st.caption('계좌 단계 판단은 미국 확정 종가와 과거 최고 종가를 비교합니다.')
        if not engine_today and valid:
            st.caption('최근 저장된 엔진 판정입니다. 오늘 엔진 판단을 기다립니다.')
        today_orders = step_order_rows(account_plan, events, fills, read_step_intents(nhplug, events),
                                       codes={L.code_of(a) for a in pair[1:3]}, index=pair[3]) if plan_today else []
        if today_orders:
            st.markdown('**오늘 1단계 주문·체결**')
            catalog = asset_names_by_code()
            shown = {row['종목코드'] for row in today_orders}
            for row in today_orders:
                code = row.pop('종목코드')
                row['종목'] = f"{catalog.get(code, code)} ({code})"
                row['계획 배정액'] = display_amount(row['계획 배정액'])
                row['평균 체결가'] = display_amount(row['평균 체결가'])
                row['사유'] = reason(row['사유'])
            table_frame(today_orders, column_order=['종목', '구분', '상태', '계획 배정액', '계획 수량', '주문 수량',
                                                      '확인 체결 수량', '평균 체결가', '사유'],
                        alt='저수지 1단계 계획과 증권사 주문·체결 대조')
            st.caption('증권사 주문번호를 대조해 체결을 확인합니다. 같은 날 여러 단계나 적립식이 합산된 경우에는 전체 주문 수량을 표시합니다.')
            missing = [f'{L.asset_name(asset)} ({L.code_of(asset)})' for asset in pair[1:3] if L.code_of(asset) not in shown]
            if missing:
                st.caption('오늘 단계 주문 행을 확인하지 못한 종목: ' + ', '.join(missing))
        elif not used:
            if plan_today and engine_today and valid and drawdown <= threshold:
                st.caption('오늘 계획에서 확인된 1단계 주문이 없습니다. 기록된 회차 판단은 위 오늘 운용에서 확인할 수 있습니다.')
            base, rows = first_step_estimate(pair, cfg['risk'], account_plan, snapshot, events)
            if rows:
                st.caption(f"저수지 추정 기준액 {base:,.0f}원 · {account_plan.get('day') or '시각 미확인'} 계좌 계획의 가용 현금 기준. "
                           '보유 저수지 ETF는 조회 평가금액을 더합니다. 수량은 수수료를 고려한 추정입니다.')
                table_frame(rows, alt='저수지 1단계 종목별 예상 배정액과 수량')
            else:
                st.caption('가용 현금 또는 저수지 ETF 평가 기록이 확인되면 예상 배정액을 표시합니다.')


def desk(key, record, nhplug, control, service_state, *, broker_view=None, load_assets=None, chart=None):
    """Long-term live account dashboard backed by broker snapshots, confirmed fills and runner records."""
    import engine_compare as ec
    from dialogs import table_frame
    from live_fills import balance_file, rows as read_fills
    from longterm_live import LOG
    from longterm_dca import clean as dca_config

    nhplug = Path(nhplug)
    data = status(key, record, nhplug)
    settings = L.load_settings(L.ROOT)
    strategy = L.book_strategy_label(settings, key)
    regime = L.REGIMES.get(data['plan'].get('regime'), ('판단 전',))[0]
    day = datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()
    plan = data['today'] if data['today'].get('day') == day else {}
    fills = read_fills(nhplug, key)
    journal = ec.real_journal(fills, [])
    positions = ec.positions(journal)
    catalog = asset_names_by_code()
    names = {code: f'{catalog[code]} ({code})' if code in catalog else code for code in positions}
    events = [r for r in ec.recent_events(nhplug / LOG) if r.get('account') == key]
    today_fills = [f for f in fills if f['time'].startswith(day)]
    today_events = [r for r in events if str(r.get('time') or '').startswith(day)]
    capital = (record.get('budget') or {}).get('capital_krw') or 0

    st.markdown(f"#### {record.get('label', key)} · 장기투자")
    mode = data['mode']
    if mode == '실주문' and not all(done for _, done in data['checklist']):
        mode = '실주문 설정 · 실행 조건 미확인'
    tone, icon, pulse = sc.service(service_state)
    sc.row([{'label': '장기투자 실행기', 'value': service_state, 'tone': tone, 'icon': icon, 'pulse': pulse, 'sub': mode},
            {'label': '계좌 전략', 'value': strategy, 'tone': 'ai', 'icon': 'account_tree',
             'sub': f"엔진 국면 {regime} · {data['plan'].get('day') or '판단 기록 없음'}"}, *control])

    assets, orders, virtual, ops = st.tabs(['자산·보유', '주문·체결', '가상 비교', '증권사 조회·보호'],
                                         key=f'lt_desk_tabs_{key}', on_change='rerun')
    if assets.open:
        with assets:
            snapshot = assets_snapshot(key, load_assets, record.get('hash'))
            if snapshot is not None:
                st.caption(f"마지막 증권사 조회 {snapshot['time']:%Y-%m-%d %H:%M:%S} KST")
            history = daily_balance_history(balance_file(nhplug, key))
            latest = history[-1][2] if history else {}
            account = snapshot['account'] if snapshot is not None else {}
            total, cash = (account.get('총자산'), account.get('주문가능금액')) if snapshot is not None else (latest.get('total_assets'), latest.get('cash'))
            principal = record.get('principal_krw')
            gain = total - principal if total is not None and principal else None
            balance_source = ('증권사 조회' if snapshot is not None else f"증권사 기록 {history[-1][1]:%m/%d %H:%M}"
                              if history else '잔고 기록 없음')
            aggregate = (snapshot.get('aggregate') or {}) if snapshot is not None else {}
            pnl, rate = aggregate.get('평가손익'), aggregate.get('수익률(%)')
            sc.row([{'label': '실계좌 총자산', 'value': display_amount(total), 'tone': 'off', 'icon': 'savings', 'sub': balance_source},
                    {'label': '원금 대비 손익', 'value': f'{gain:+,.0f}원' if gain is not None else '—',
                     'tone': sc.signed(gain) if gain is not None else 'off', 'icon': 'percent',
                     'sub': f'{gain / principal * 100:+.2f}% · 원금 {principal:,.0f}원' if gain is not None else '총자산과 원금 기록 필요',
                     'help': '총자산과 설정 원금의 차이입니다. 입출금은 총자산에 반영됩니다.'},
                    {'label': '보유 평가손익', 'value': display_amount(pnl, signed=True), 'tone': sc.signed(pnl) if pnl is not None else 'off',
                     'icon': 'monitoring', 'sub': f"보유 수익률 {display_amount(rate, pct=True)}"},
                    {'label': '주문가능금액', 'value': display_amount(cash), 'tone': 'off', 'icon': 'account_balance_wallet', 'sub': balance_source}])
            holdings = {}
            if snapshot is not None:
                holdings = {p['종목코드']: p for p in snapshot['holdings'] if p.get('종목코드')}
                for code, p in holdings.items():
                    name = p.get('종목') or catalog.get(code) or code
                    names[code] = f'{name} ({code})' if name != code and not name.endswith(f'({code})') else name
            held = ({code: row['보유수량'] for code, row in holdings.items()} if snapshot is not None
                    else {code: row['qty'] for code, row in positions.items()})
            res = L.reservoir_code(settings, key, capital, held)
            realized = ec.summary(journal)
            sold = sum(fill['side'] == 'SELL' for fill in journal)
            sc.row([{'label': '설정 운용금', 'value': display_amount(capital) if capital else '—',
                     'tone': 'warn', 'icon': 'account_balance_wallet',
                     'sub': f'운용금 대비 보유 평가 {aggregate["평가금액"] / capital:.1%}'
                     if capital and type(aggregate.get('평가금액')) in (int, float) else None},
                    {'label': '기록 실현손익', 'value': display_amount(realized['pnl'], signed=True) if realized['sells'] else '—',
                     'tone': sc.signed(realized['pnl']), 'icon': 'monitoring',
                     'sub': f"매입가 확인 매도 {realized['sells']} / {sold}건 · 비용 제외" if sold else '매도 체결 기록 없음'},
                    {'label': '저수지 ETF', 'value': f'{held.get(res, 0):,}주' if snapshot is not None or fills else '—',
                     'tone': 'off', 'icon': 'savings', 'sub': catalog.get(res, res)}])
            activity_panel(plan, data['today'], today_fills, today_events, dca_config(settings['dca'].get(key)))
            first_step_panel(L.yeo_settings(settings, key), data['plan'], data['today'], snapshot, today_events, today_fills, day, nhplug)

            plan_reservoir = (plan.get('info') or {}).get('reservoir') or res
            target_names = {code: f'{name} ({code})' for code, name in catalog.items()} | names
            allocation = allocation_comparison(data['weights'], holdings if snapshot is not None else None,
                                               capital, target_names, plan_reservoir)
            basis = data['basis']
            engine_day = data['plan'].get('day')
            if engine_day and engine_day != day:
                basis = basis.replace('오늘', engine_day)
            portfolio_panel(allocation, capital, basis)

            st.markdown('##### 실보유 종목')
            if snapshot is not None:
                if holdings:
                    holdings_table = [{'종목': names[code], '보유 수량': p['보유수량'], '평균 매입가': display_amount(p.get('평균매입가')),
                                       '현재가': display_amount(p.get('현재가')), '평가금액': display_amount(p.get('평가금액')),
                                       '평가손익': display_amount(p.get('평가손익'), signed=True),
                                       '수익률': display_amount(p.get('수익률(%)'), pct=True)}
                                      for code, p in holdings.items()]
                    table_frame(holdings_table, alt='증권사 조회 기준 실보유 종목과 평가손익')
                else:
                    st.caption('증권사 조회 기준 보유 종목이 없습니다.')
            elif positions:
                table_frame([{'종목': names[code], '보유 수량': p['qty'], '평균 매입가': display_amount(p.get('avg')),
                              '매입 금액': display_amount(p['cost']) if p.get('avg') is not None else '—'}
                             for code, p in sorted(positions.items())], alt='실체결 원장 기준 보유 종목과 매입 금액')
                st.caption('체결 원장 기준 보유 수량과 평균 매입가입니다.')
            else:
                st.caption('표시할 체결 원장 보유 기록이 없습니다.')

            st.markdown('##### 실계좌 자산 추이')
            if snapshot is not None and total is not None:
                stamp = snapshot['time']
                by_day = {d: (ts, row) for d, ts, row in history}
                d = stamp.date().isoformat()
                if d not in by_day or stamp >= by_day[d][0]:
                    by_day[d] = stamp, {'total_assets': total}
                history = [(d, *by_day[d]) for d in sorted(by_day)]
            asset_chart(history)
            if held and chart is not None:
                holding_details = st.expander('보유 종목 차트 · TradingView', key=f'lt_holding_chart_{key}', on_change='rerun')
                if holding_details.open:
                    with holding_details:
                        holding_chart(key, held, fills, names, chart)

    if orders.open:
        with orders:
            st.markdown('##### 오늘 계좌 주문 계획')
            if planned := order_rows(plan):
                table_frame(planned, alt='오늘 장기투자 주문 계획')
            else:
                st.caption('오늘 기록된 주문 계획이 없습니다.' if not plan else '오늘 계좌 판단을 기록했습니다. 주문 계획은 0건입니다.')
            st.markdown('##### 증권사 체결 이력')
            if recorded := fill_rows(key, nhplug, fills=fills):
                table_frame(recorded, alt='실계좌 증권사 체결 이력')
            else:
                st.caption('아직 저장된 증권사 체결이 없습니다.')
            with st.expander('국내 매수 직전 호가 감사'):
                quote_gap_panel(today_events)
            with st.expander('장기투자 실행 기록'):
                if events:
                    table_frame([{'시각': str(r.get('time', ''))[:19].replace('T', ' '), '내용': event_text(r)}
                                 for r in reversed(events[-20:])], alt='해당 실계좌의 최근 장기투자 실행 기록')
                else:
                    st.caption('장기투자 실행 기록이 없습니다.')

    if virtual.open:
        with virtual:
            virtual_comparison(key)

    if ops.open:
        with ops:
            if broker_view is not None:
                broker_view()
            with st.expander('계좌 보호 한도와 실행 조건'):
                st.caption(checklist_text(data['checklist']))
                st.caption(PROTECTION)
                capital = (record.get('budget') or {}).get('capital_krw') or 0
                table_frame(protection_rows(record, capital, settings), alt='장기투자 계좌 보호 한도')
            flow = st.expander('장기투자 주문 흐름', key=f'lt_desk_flow_{key}', on_change='rerun')
            if flow.open:
                with flow:
                    import guide_ui as GU
                    GU.flow(f'lt-account-{key}', flow_spec(data, strategy, regime, service_state),
                            flow_dot(data, strategy, regime, service_state))
                    st.caption('설정 기준 주문 흐름입니다. 실제 주문과 체결은 주문·체결 탭에서 확인할 수 있습니다.')
