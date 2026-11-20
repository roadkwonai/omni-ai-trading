"""장기투자 → 전략 → 적립식 (user request 2026-11-17; v3 2026-11-18, docs/longterm-dca-v3-plan.md): turn 적립식 on per book,
choose 거장 투자 (a master, and for 언제 · 얼마나 whether the master or I decide) or 수동 투자, the amount, the day and time;
see what each book has paid in and how much of it the 적립식 still holds. The 적립식 never sells: a master's selling side is
its engine module bundle, which this tab offers to save. Saved through vibe_longterm.save_settings (history kind vibe-longterm)."""
from __future__ import annotations

import streamlit as st

import longterm_dca as D
import longterm_dca_effective as EV
import longterm_dca_guide as G
import longterm_watchlist as W
import vibe_longterm as L


def all_auto(key):
    """'모두 거장 자동': 거장 투자 · 언제 · 얼마나 · 몇 시를 거장에게, 무엇을 계좌 전략에 (nothing is saved)."""
    st.session_state.update({f'dca_style_{key}': 'master', f'dca_whenby_{key}': 'master', f'dca_sizeby_{key}': 'master', f'dca_size_{key}': 'amount',
                             f'dca_timemode_{key}': 'auto', f'dca_pick_on_{key}': False})


def auto_manual_rows(cfg, yeo=False, value=None):
    """[{항목, 방식, 내용}] of one 적립식 setting: which part the master decides and which part is mine (yeo / value: a 여운봉
    book's master size reads its value)."""
    p = D.rule(cfg)
    what = (('수동 · 내가', f"고른 {len(cfg['picks'])}종목") if cfg['pick_on'] else ('자동 · 계좌 전략', '전략이 사는 종목(여운봉 계좌는 70:30 짝, 거장 모듈 계좌는 KOFR 뺀 목표)'))
    at = (('자동 · 거장', f'장중 전일 종가 이하일 때 · 늦어도 {D.TIMES[1]}') if cfg['time_mode'] == 'auto' else ('수동 · 내가', cfg['time']))
    size = D.size_text(cfg, p, None, value=value, yeo=yeo) if cfg['size_by'] == 'master' or cfg['frequency'] == 'deposit' else (f"종목별 {cfg['shares_n']}주" if cfg['size'] == 'shares' else f"1회 {cfg['amount_krw']:,}원")
    return [{'항목': name, '방식': way, '내용': text} for name, (way, text) in
            (('언제 사나', (D.BY[cfg['when_by']], D.when_text(cfg, p))), ('얼마나', (D.BY[cfg['size_by']], size)), ('무엇을', what), ('몇 시에', at))]


def master_table(key, regime):
    """The chosen master's multipliers by regime (today's marked), its day and floor, as one row per regime."""
    p = D.PROFILES[key]
    return [{'국면': ('▶ ' if r == regime else '') + L.REGIMES[r][0], '배수': f"{p['mult'][r]:g}배",
             '기본 25만 원이면': f"{250_000 * p['mult'][r]:,.0f}원"} for r in D.REGIMES]


def profile_label(key):
    p = D.PROFILES[key]
    return f"{p['label']} · {p['title']}"


def bundle_text(key):
    return ' · '.join(L.MODULES[m][0] + ' ' + L.MODULES[m][1] for m in D.PROFILES[key]['modules'])


def books(records):
    """[(key, label)] of the books that can run 적립식: the virtual book and the live accounts set to 장기투자."""
    from accounts import longterm_accounts
    return [('actual', '장기투자 가상 계좌 (1억 원 가상)')] + [(k, f"{r.get('label', k)} (실계좌 · 장기투자)") for k, r in longterm_accounts(records).items()]


def way_label(cfg):
    """The book's way in words: '거장 투자 · 하워드 막스 · 언제 거장 · 얼마나 거장' or '수동 투자'."""
    cfg = D.clean(cfg)
    if cfg['style'] == 'manual':
        return D.STYLES['manual']
    return (f"{D.STYLES['master']} · {D.PROFILES[cfg['profile']]['label']} · 언제 {'거장' if cfg['when_by'] == 'master' else '내가'}"
            f" · 얼마나 {'거장' if cfg['size_by'] == 'master' else '내가'}")


def schedule_text(cfg):
    """'매주 수요일 · 하락일 우선(정한 날 뒤 첫 하락일) · 10:30 · 500,000원 · 수동 투자' (and the buy limits that are on)."""
    cfg = D.clean(cfg)
    p = D.rule(cfg)
    at = '시각 자동' if cfg.get('time_mode') == 'auto' else cfg['time']
    return f"{D.when_text(cfg, p)} · {at} · {D.size_text(cfg, p, None)} · {way_label(cfg)}" + (f" · {limits_text(cfg)}" if limits_text(cfg) else '')


def limits_text(cfg):
    """The buy limits that are on, in words ('' when none): '2종목만 · 최대 3종목 · 종목당 40주 · 전체 500주'."""
    parts = ([f"{len(cfg['picks'])}종목만"] if cfg['pick_on'] else []) + ([f"최대 {cfg['max_symbols']}종목"] if cfg['max_symbols_on'] else [])
    parts += ([f"종목당 {cfg['max_per_symbol']:,}주"] if cfg['max_per_symbol_on'] else []) + ([f"전체 {cfg['max_total']:,}주"] if cfg['max_total_on'] else [])
    return ' · '.join(parts)


def nh_name(code):
    """The KRX name NH gives `code` (the dashboard's read-only adapter), or None when NH does not know it."""
    try:
        response = st.session_state['_read_adapter'].call_tool('get_stock_price', {'stock_code': code})
        row = ((response or {}).get('data') or {}).get('Output_0') or {}
    except Exception:  # no adapter or a failed read: the code cannot be checked now
        return None
    return str(row.get('iem_nm') or code).lstrip('*') if row.get('iem_cd') == code else None


def watchlist_editor(root):
    """장기투자 관심목록 (user approval 2026-11-18, 2-A): KRX codes the 적립식 may buy as its own sleeve, checked with NH."""
    listed = W.load(root)
    with st.expander(f'장기투자 관심목록 · {len(listed)}종목', icon=':material/bookmark:', expanded=False):
        st.caption('엔진 ETF 6종 밖의 국내 상장 종목을 적립식 전용으로 모읍니다. 아래 장부의 "적립 종목 선택"에서 고르면 그 종목은 고른 주식 '
                   'ETF의 평균 목표 비중만큼 받습니다(관심목록만 고르면 같은 금액씩). 엔진은 이 종목을 리밸런싱·매도하지 않고, 순자산·그림자·'
                   '실계좌 운용금에는 포함합니다. 목록에서 빼면 새로 사지 않을 뿐 보유는 그대로입니다.')
        a, b = st.columns([3, 1], vertical_alignment='bottom')
        code = a.text_input('추가할 종목코드', max_chars=6, placeholder='예: 005930', key='lt_watch_code', help=G.tip('watchlist')).strip().upper()
        if b.button('관심목록 추가', key='lt_watch_add', width='stretch'):
            issue = W.problem(code, listed)
            name = None if issue else nh_name(code)
            if issue or not name:
                st.error(issue or 'NH 시세에서 종목을 찾지 못했습니다.')
            else:
                W.save(root, {**listed, code: name})
                st.toast(f'{name} 추가', icon=':material/check_circle:')
                st.rerun()
        if listed:
            c, d = st.columns([3, 1], vertical_alignment='bottom')
            gone = c.selectbox('뺄 종목', list(listed), format_func=lambda k: f'{listed[k]} ({k})', index=None, key='lt_watch_remove')
            if d.button('관심목록에서 빼기', key='lt_watch_remove_go', width='stretch', disabled=not gone):
                W.save(root, {k: v for k, v in listed.items() if k != gone})
                st.rerun()
    return listed


def master_picks(root):
    """{'master:ticker': (label, [engine ETF keys])} of the latest masters' US picks with a domestic ETF to buy (3-A)."""
    import us_ideas as U
    try:
        picks = U.status(root).get('picks') or []
    except Exception:  # reference data only: the 적립식 tab works without it
        picks = []
    out = {}
    for p in picks:
        if keys := list(U.domestic_etfs(p)):
            who = U.MASTERS.get(p['master'], p['master']).split(' · ')[0]
            out[f"{p['master']}:{p['ticker']}"] = (f"{p['ticker']}({who})", keys,
                                                    f"{p['ticker']} · {who} → " + ' · '.join(L.ASSETS[k][1] for k in keys))
    return out


def limits_form(cfg, key, listed=None, ideas=None):
    """적립식 매수 제한 (user 2026-11-18): every limit has its own switch and is off by default. Returns the values to save.
    listed: the 관심목록 {code: name}; ideas: master_picks(), whose domestic ETFs are added to the picks on save."""
    listed, ideas = listed or {}, ideas or {}
    on = any(cfg[k] for k in ('pick_on', 'max_symbols_on', 'max_per_symbol_on', 'max_total_on'))
    out = {}
    with st.expander('매수 종목·수량 제한 (선택)' + (f' · {limits_text(cfg)}' if on else ''), expanded=on, icon=':material/filter_alt:'):
        st.caption('적립식이 사는 몫에만 적용합니다. 엔진의 리밸런싱 매수와 매도는 따로입니다. 제한 때문에 사지 못한 돈은 현금으로 남고, '
                   '고른 ETF끼리는 엔진 목표 비중의 비율대로 나눕니다. 관심목록 종목은 고른 주식 ETF의 평균 비중을 받습니다. '
                   '고른 여운봉 ETF(0261H0 등)는 이 스위치가 켜져 있을 때만 삽니다.')
        a, b = st.columns([1, 3], vertical_alignment='bottom')
        out['pick_on'] = a.toggle('적립 종목 선택', value=cfg['pick_on'], key=f'dca_pick_on_{key}', help=G.tip('pick_on'))
        import yeo_method as Y
        mixed = {code: name for key, (code, name) in Y.EXTRA.items() if key in ('NDXMIX', 'SPXMIX')}  # 여운봉 매매법의 주식·채권 혼합 ETF
        options = list(L.ASSETS) + list(mixed) + [c for c in listed if c not in mixed]
        label = lambda k: (f'{L.ASSETS[k][1]} ({L.ASSETS[k][0]})' if k in L.ASSETS else f'{mixed[k]} ({k}) · 여운봉 매매법 ETF' if k in mixed
                           else f'{listed[k]} ({k}) · 관심목록')
        picks = b.multiselect('적립할 종목', options, default=[k for k in cfg['picks'] if k in options] or list(L.ASSETS),
                              key=f'dca_picks_{key}', format_func=label, disabled=not out['pick_on'], help=G.tip('picks'))
        chosen = st.multiselect('거장 추천종목으로 더하기 (국내 상장 ETF로 노출)', list(ideas), key=f'dca_master_picks_{key}',
                                format_func=lambda i: ideas[i][2], disabled=not out['pick_on'],
                                help=G.tip('master_picks'))
        came = {k: v for k, v in cfg['pick_from'].items() if k in picks}
        for i in chosen:
            for k in ideas[i][1]:
                came[k] = ', '.join(dict.fromkeys([*filter(None, came.get(k, '').split(', ')), ideas[i][0]]))
        out['picks'] = picks + [k for i in chosen for k in ideas[i][1] if k not in picks]
        out['pick_from'] = came
        if cfg['pick_from']:
            st.caption('거장 추천에서 온 ETF: ' + ' · '.join(f'{L.ASSETS[k][1]} ← {v}' for k, v in cfg['pick_from'].items())
                       + ' · 참고 정보이며 미국 개별주는 주문하지 않습니다.')
        for name, label, unit, step in (('max_symbols', '종목 수 제한', '최대 종목 수', 1), ('max_per_symbol', '종목당 주식 수 제한', '종목당 최대 보유 주식 수', 10),
                                        ('max_total', '전체 주식 수 제한', '모든 ETF 합계 최대 보유 주식 수', 50)):
            c, d = st.columns([1, 3], vertical_alignment='bottom')
            out[f'{name}_on'] = c.toggle(label, value=cfg[f'{name}_on'], key=f'dca_{name}_on_{key}', help=G.tip(name))
            low, high = D.LIMITS[name]
            out[name] = int(d.number_input(unit, low, high, cfg[name], step=step, key=f'dca_{name}_{key}', disabled=not out[f'{name}_on'],
                                           help=G.tip(name)))
        if out['pick_on'] and not out['picks']:
            st.warning('적립할 ETF를 하나 이상 고르세요. 비워 두면 적립식이 아무것도 사지 않습니다.')
    return out


def flow_dot():
    """One contribution's path (v3, 2026-11-18): into the 적립식's pool, then bought on the master's (or my) day at the master's
    small size (or my amount); the strategy decides what it buys and does all selling. The Graphviz fallback of flow_spec."""
    return """digraph G { rankdir=LR; bgcolor="transparent"; node [shape=box style="rounded,filled" fillcolor="#1E293B" color="#334155"
fontcolor="#E2E8F0" fontname="Pretendard" fontsize=11]; edge [color="#94A3B8" fontcolor="#94A3B8" fontname="Pretendard" fontsize=10];
day [label="적립일\\n매일 / 매주 요일 / 매월 n일"]; pool [label="적립식 몫\\n아직 안 산 적립금"];
when [label="언제\\n거장: 매일 확인 · 주기 또는 하락일 우선\\n내가: 정한 날 · 계속 또는 하락일 우선" fillcolor="#0E1223" color="#C4B5FD" fontcolor="#C4B5FD"];
size [label="얼마나\\n거장: 기본 × 국면 배수 0.5~1.5 · 상한 1.5배\\n내가: 1회 금액 / 종목별 N주" fillcolor="#0E1223" color="#C4B5FD" fontcolor="#C4B5FD"];
buy [label="매수\\n계좌 전략이 사는 종목\\n(또는 고른 종목)"]; eng [label="파는 쪽 · 현금 비중 = 계좌 전략" fillcolor="#0E1223" color="#93C5FD" fontcolor="#93C5FD"];
day -> pool; pool -> when; when -> size; size -> buy; buy -> eng [style=dashed label="보유"]; size -> pool [style=dashed label="덜 산 몫 = 따라잡기"];
}"""


def flow_spec():
    import guide_ui as GU
    return GU.workflow('적립금의 길 (v3)', [('in', '적립금'), ('decide', '거장 또는 나'), ('buy', '매수'), ('sell', '파는 쪽 · 계좌 전략')],
        [('day', 'in', 0, '적립일', '매일·매주 요일·매월 n일', 'external'), ('pool', 'in', 1, '적립식 몫', '아직 안 산 적립금', 'database'),
         ('when', 'decide', 2, '언제', '거장: 주기/하락일 우선 · 내가: 계속/하락일 우선', 'security'),
         ('size', 'decide', 3, '얼마나', '거장: 기본 × 국면 배수 · 내가: 금액/N주', 'security'),
         ('buy', 'buy', 4, '매수', '계좌 전략이 사는 종목 · 1주 미만 이월'),
         ('eng', 'sell', 5, '계좌 전략', '익절·리밸런싱 · 저수지', 'cloud')],
        [('day', 'pool', None, 'main'), ('pool', 'when', None, 'main'), ('when', 'size', '사는 날', 'main'), ('size', 'buy', None, 'main'),
         ('size', 'pool', '덜 산 몫', 'return'), ('buy', 'eng', '보유', 'async')],
        main=['day', 'pool', 'when', 'size', 'buy'])


def book_state(root, key):
    """The 적립식 state of one book: the virtual book's in longterm.sqlite3, a live account's in its day state file."""
    if key == 'actual':
        return L.read_state(root, ('dca:actual',)).get('dca:actual') or {}
    import longterm_live
    from src.config.paths import get_runtime_root
    return longterm_live.load_state(get_runtime_root() / 'nhplug', key).get('dca') or {}


def carry_text(state, listed=None):
    """'1주 미만 이월 6,316원(SOL 미국양자컴퓨팅TOP10 6,316원) · 다음 적립 때 합쳐 삼' or '' (4-B②)."""
    carry = {k: v for k, v in (state.get('carry') or {}).items() if v >= 1}
    name = lambda k: L.ASSETS[k][1] if k in L.ASSETS else (listed or {}).get(k, k)
    return (f"1주 미만 이월 {sum(carry.values()):,.0f}원(" + ', '.join(f'{name(k)} {v:,.0f}원' for k, v in carry.items())
            + ') · 다음 적립 때 합쳐 삼') if carry else ''


def render(root, settings):
    regime = (L.read_state(root, ('plan',)).get('plan') or {}).get('regime')  # for the 추천 등급 badges (vibe_longterm.GRADES)
    st.markdown('**적립식** · 켠 계좌만 들어오는 적립금을 소액으로 꾸준히 모읍니다. **거장 투자**는 고른 거장이 시황(국면)에 따라 사는 날과 '
                '속도를 0.5~1.5배로 조금 바꾸고, **수동 투자**는 내가 정한 날·금액대로 삽니다. 언제·얼마나·무엇을·몇 시를 항목마다 거장에게 맡기거나 '
                '내가 정할 수 있습니다. **무엇을**의 기본은 계좌 전략이 사는 종목입니다(여운봉 계좌는 70:30 짝, 거장 모듈 계좌는 KOFR 뺀 목표).')
    if st.button('적립식 상세 가이드', key='dca_guide', icon=':material/menu_book:',
                 help='흐름 · 언제·얼마나 정하기 · 거장 비교(국면 배수·재현) · 설정값 사전 · 상황별 추천 · 주의를 팝업으로 봅니다.'):
        G.open_guide()
    st.caption('입력 옆 (?)에 마우스를 올리면 그 설정이 무엇을 바꾸는지, 범위와 기본값이 나옵니다.')
    st.info(G.STEADY, icon=':material/savings:')
    import guide_ui as GU
    GU.flow('lt-dca', flow_spec(), flow_dot())
    with st.expander('거장 비교 · 국면 배수와 재현 V1 (2026-11-18)', icon=':material/science:'):
        st.dataframe(G.master_rows(regime), hide_index=True, width='stretch')
        st.caption(G.V1 + ' 재현: scripts/longterm_dca_v3_replay.py · docs/longterm-dca-v3-plan.md 7장.')
    listed, ideas = watchlist_editor(root), master_picks(root)
    from accounts import load as load_accounts
    records = load_accounts()
    for key, label in books(records):
        cfg = settings['dca'].get(key) or D.clean({})
        state = book_state(root, key)
        size_note = None  # the master's size caption of this card (a placeholder, filled once 주기 is known)
        with st.container(border=True):
            head, switch = st.columns([3, 1], vertical_alignment='center')
            head.markdown(f"**{label}** · " + (f":green[켜짐] · {schedule_text(cfg)}" if cfg['on'] else
                                               f":orange[꺼짐 · 적립 알림만] · {schedule_text(cfg)}" if cfg['alert'] else ':gray[꺼짐]'))
            on = switch.toggle('적립식 켜기', value=cfg['on'], key=f'dca_on_{key}', help=G.tip('on'))
            alert = cfg['alert']
            if key != 'actual':  # 적립 알림 (2026-11-20): a live account only (the virtual book needs no reminder)
                alert = switch.toggle('꺼져도 적립 알림', value=cfg['alert'], key=f'dca_alert_{key}', disabled=on, help=G.tip('alert'))
                if alert and not on:
                    st.info('적립식은 주문하지 않고, 아래 설정대로 거장이 사는 날마다 Discord 장기투자 채널로 **무엇을·몇 주·현재가·약 얼마**를 알립니다. '
                            '다른 계좌에서 참고해 직접 사세요. 금액은 아래 1회 적립 금액(또는 들어오는 적립금) 기준이고, 입금할 때는 매일 그 금액으로 알립니다.',
                            icon=':material/notifications_active:')
            st.caption(f"누적 적립 {state.get('contributed_krw', 0):,.0f}원 · 적립식 몫(아직 안 산 적립금) {state.get('waiting_krw', 0):,.0f}원 · "
                       f"마지막 적립 {state.get('last_contribution') or '—'} · 마지막 매수 {state.get('last_buy') or '—'}"
                       + (f" · {carry}" if (carry := carry_text(state, listed)) else '')
                       + (f" · 미체결 재매수 대기 {sum(state['retry'].values()):,.0f}원(다음 확인 때 먼저 삼)" if sum((state.get('retry') or {}).values()) >= 1 else '')
                       + (' · 실계좌는 계좌의 여유 현금과 운용금 안에서만 사며, 실주문 스위치가 꺼져 있으면 계획만 기록합니다' if key != 'actual' else
                          ' · 가상 계좌는 적립일에 가상 입금 뒤 삽니다(가상 체결 스위치가 꺼져 있으면 계획만)'))
            if key == 'actual':
                engine = L.active_modules(settings, L.recommended_set(regime))
                now_label, where = ', '.join(f'{L.MODULES[m][0]} {L.MODULES[m][1]}' for m in engine), '장기투자 → 전략'
            else:
                now_label, where = L.book_strategy_label(settings, key), '계좌·자동매매 → 이 계좌 위쪽 전략 칸'
            st.info(f"적립식은 **들어오는 적립금만** 다룹니다. 계좌 운용금의 매매(저수지 매매법 등 거장 모듈)는 **{where}**에서 따로 정합니다 "
                    f"— 지금: {now_label}", icon=':material/route:')
            summary_box = st.container(border=True)  # filled at the end of the card from the values chosen below (unsaved)
            yeo_book = L.yeo_settings(settings, key) is not None
            style = st.segmented_control('투자 방식', list(D.STYLES), default=cfg['style'], format_func=D.STYLES.get, key=f'dca_style_{key}',
                                         help=G.tip('style')) or cfg['style']
            size = st.segmented_control('적립 크기', list(D.SIZES), default=cfg['size'], format_func=D.SIZES.get, key=f'dca_size_{key}',
                                        help=G.tip('size')) or cfg['size']
            profile, when_by, size_by, every, tune = cfg['profile'], 'me', 'me', cfg['buy_every'], {}
            if style == 'master':
                profile = st.selectbox('거장', list(D.PROFILES), index=list(D.PROFILES).index(cfg['profile']),
                                       format_func=lambda k: f'{profile_label(k)} · {D.profile_grade(k, regime)[0]}', key=f'dca_profile_{key}',
                                       help=G.tip('profile'))
                p = D.PROFILES[profile]
                grade, why = D.profile_grade(profile, regime)
                (st.warning if p.get('caution') else st.info)(
                    f"{L.GRADE_BADGE[grade]} {why} · 추천은 안내이고 고른 거장대로 적립합니다  \n{p['rule']}  \n"
                    f"재현 V1(여운봉 계좌 · 2021-11 시작): {p['evidence']}  \n파는 쪽(엔진 모듈 묶음): {bundle_text(profile)}", icon=':material/person:')
                left, right = st.columns([3, 2])
                left.dataframe(master_table(profile, regime), hide_index=True, width='stretch')
                right.caption(f"사는 날: {'하락일 우선(최대 ' + str(p['wait']) + '일)' if p['when'] == 'dip' else '정한 주기마다'}"
                              + (' · 급락일 1.5배' if p.get('crash') else '') + f" · 여운봉 계좌의 현금 하한 {p['floor']:.0%}. "
                              '배수는 기본 금액에 곱하며 0.5~1.5배로 묶여, 전부나 0이 되지 않습니다. ▶ = 지금 국면.')
                c1, c2 = st.columns(2)
                when_by = c1.segmented_control('언제 정하기', list(D.BY), default=cfg['when_by'], format_func=D.BY.get, key=f'dca_whenby_{key}',
                                               help=G.tip('when_by')) or cfg['when_by']
                size_by = c2.segmented_control('얼마나 정하기', list(D.BY), default=cfg['size_by'], format_func=D.BY.get, key=f'dca_sizeby_{key}',
                                               help=G.tip('size_by')) or cfg['size_by']
                if size == 'shares' and size_by == 'master':
                    c2.caption('수량 · 거장: 거장이 아래 적립금과 국면으로 회당 금액을 정하고, 고른 종목을 정수 주로 나눠 삽니다'
                               '(1주 미만 몫은 다음 매수로). 내가 정한 N주는 얼마나 = 내가일 때만 씁니다.')
                if when_by == 'master':
                    every = st.segmented_control('매수 빈도(최대)', list(D.BUY_EVERY), default=cfg['buy_every'], format_func=D.BUY_EVERY.get,
                                                 key=f'dca_every_{key}', help=G.tip('buy_every')) or cfg['buy_every']
                    import longterm_inputs_guide as IG
                    IG.button(f'dca_inputs_{key}', '기능별 판단', label='거장은 무엇을 보나(국면·하락일·급락일·매수 시각)')
                size_note = st.empty()  # filled once 주기 is chosen below (입금할 때 sizes differently)
                with st.expander('거장 값 미세 조정' + (f' · {D.tune_text(cfg["tune"])}' if cfg['tune'] else ''), expanded=bool(cfg['tune']),
                                 icon=':material/tune:'):
                    tune = EV.tune_editor(f'dca_tune_{key}', D.clean({**cfg, 'profile': profile, 'tune': cfg['tune'] if profile == cfg['profile'] else {}}),
                                          regime, yeo_book)
            me_when = cfg['me_when']
            if when_by == 'me':
                me_when = st.segmented_control('정한 날 방식', list(D.ME_WHENS), default=cfg['me_when'], format_func=D.ME_WHENS.get,
                                               key=f'dca_mewhen_{key}', help=G.tip('me_when')) or cfg['me_when']
            b, c = st.columns(2)
            amount, shares_n = cfg['amount_krw'], cfg['shares_n']
            spread, share_pct = cfg['spread_weeks'], cfg['deposit_pct']
            on_signal = size == 'shares' and when_by == 'master' and size_by == 'me'  # my N shares come in on the master's buy days
            # 입금할 때 (2026-11-20): a live account only (the virtual book has no real deposit)
            freqs = [f for f in D.FREQUENCIES if f != 'deposit' or key != 'actual']
            if on_signal:
                frequency = cfg['frequency']
                c.caption('주기: 거장이 살 때마다(위 매수 빈도에 한 번까지)')
            else:
                frequency = c.segmented_control('주기', freqs, default=cfg['frequency'] if cfg['frequency'] in freqs else 'monthly',
                                                format_func=D.FREQUENCIES.get, key=f'dca_freq_{key}', help=G.tip('frequency')) or cfg['frequency']
            if frequency == 'deposit':
                spread = b.number_input('입금 몫을 나눠 사는 기간(주)', *D.SPREAD_WEEKS, cfg['spread_weeks'], step=1, key=f'dca_spread_{key}',
                                        help=G.tip('spread_weeks'))
                share_pct = b.number_input('입금 중 적립식으로 살 비율(%)', *D.DEPOSIT_PCT, cfg['deposit_pct'], step=10, key=f'dca_deppct_{key}',
                                           help=G.tip('deposit_pct'))
                seen = (state or {}).get('capital_seen')
                b.caption('입금한 뒤 계좌·자동매매에서 운용금을 올리면, 올린 금액의 위 비율이 적립식 몫이 됩니다(나머지는 계좌 전략의 저수지). '
                          '지금 있는 돈과 매도 대금은 적립식이 쓰지 않습니다. '
                          + (f'기준 운용금 {seen:,.0f}원 · 남은 입금 몫 {(state or {}).get("waiting_krw", 0):,.0f}원.' if type(seen) in (int, float)
                             else '처음 확인 때 지금 운용금을 기준으로 잡습니다.'))
            elif size == 'shares' and size_by == 'me':
                shares_n = b.number_input('종목별 주식 수', D.SHARES_N[0], D.SHARES_N[1], cfg['shares_n'], step=1, key=f'dca_shares_{key}',
                                          help=G.tip('shares_n'))
            else:
                amount = b.number_input('1회 적립 금액(원)' if size == 'amount' else '들어오는 적립금(원) · 거장이 이 안에서 수량을 정함',
                                        D.AMOUNT[0], D.AMOUNT[1], cfg['amount_krw'], step=100_000, key=f'dca_amount_{key}', help=G.tip('amount'))
            if size_by == 'master' and size_note is not None:
                if frequency == 'deposit':
                    size_note.caption('얼마나 · 거장 · 입금할 때: 입금 몫 ÷ (나눠 사는 주 수 × 매수 빈도)가 기본이고 국면 배수를 곱합니다. 입금 몫 안에서만 쓰고'
                                      + (f", 현금 하한 {p['floor']:.0%}도 지킵니다(여운봉 계좌)." if yeo_book else '.') + ' 한 번에 기본의 1.5배를 넘지 않습니다.')
                else:
                    size_note.caption("얼마나 · 거장: " + (f"장부 가치 × 주 {D.RATE:.2%}와 적립금 속도 × {D.PACE:.0%} 중 큰 값이 기본이고, 현금 하한 {p['floor']:.0%}까지만 씁니다(여운봉 계좌)." if yeo_book else
                                      f"들어오는 적립금 속도 × {D.PACE:.0%}가 기본입니다. 남는 적립금은 적립식 몫에 쌓이고, 적립 한 번 몫을 넘는 부분은 "
                                      "매수마다 1/8씩 더 씁니다(상한 기본의 1.5배). 그래서 국면 배수가 큰 날·거장일수록 더 빨리 들어갑니다.")
                                      + ' 한 번에 기본의 1.5배를 넘지 않습니다.')
            d, e = st.columns(2)
            day, weekday = cfg['day'], cfg['weekday']
            if on_signal:
                pass
            elif frequency == 'deposit':
                d.caption('입금할 때: 정한 날이 없습니다. 입금 몫이 있는 동안 위 언제 규칙대로 삽니다.')
            elif frequency == 'monthly':
                day = d.number_input('매월 며칠', 1, 28, cfg['day'], key=f'dca_day_{key}', help=G.tip('day'))
            elif frequency == 'weekly':
                weekday = d.selectbox('요일', range(5), index=cfg['weekday'], format_func=lambda i: D.WEEKDAYS[i] + '요일', key=f'dca_wd_{key}', help=G.tip('weekday'))
            else:
                d.caption('매일: 장이 열리는 날마다 한 번')
            time_mode = e.segmented_control('매수 시각', list(D.TIME_MODES), default=cfg['time_mode'], format_func=D.TIME_MODES.get,
                                            key=f'dca_timemode_{key}', help=G.tip('time_mode')) or cfg['time_mode']
            import longterm_inputs_guide as IG
            IG.time_button(f'dca_time_guide_{key}', e)
            hours = [f'{h:02d}:{m:02d}' for h in range(9, 15) for m in (0, 10, 20, 30, 40, 50) if D.TIMES[0] <= f'{h:02d}:{m:02d}' <= D.TIMES[1]]
            if time_mode == 'fixed':
                at = e.selectbox('시각', hours, index=hours.index(cfg['time']) if cfg['time'] in hours else 0, key=f'dca_time_{key}', help=G.tip('time'))
            else:
                at = cfg['time']
                e.caption(f'거장 자동: {D.TIMES[0]}부터 {D.AUTO_CHECK_MIN}분마다 보고, 사려는 ETF가 전일 종가 이하로 내려온 첫 순간에 삽니다. '
                          f'그런 때가 없으면 {D.TIMES[1]}에 삽니다(오르는 날 비싸게 사지 않기).')
            limits = limits_form(cfg, key, listed, ideas)
            new = {'on': on, 'style': style, 'profile': profile, 'when_by': when_by, 'size_by': size_by, 'me_when': me_when, 'buy_every': every,
                   'amount_krw': int(amount), 'size': size, 'shares_n': int(shares_n), 'frequency': frequency,
                   'day': int(day), 'weekday': int(weekday), 'time': at, 'time_mode': time_mode, 'tune': tune,
                   'spread_weeks': int(spread), 'deposit_pct': int(share_pct), 'alert': bool(alert), **limits}
            with summary_box:  # what the card's values (saved or not) do: longterm_dca.effective, the same rule as the engine
                head_, fill_ = st.columns([4, 1], vertical_alignment='center')
                head_.markdown('**자동 · 수동 한눈에** :gray[(아래에서 항목마다 정함)]',
                               help='언제·얼마나·무엇을·몇 시에를 각각 거장(자동)에게 맡기거나 내가(수동) 정합니다. 섞어 쓸 수 있습니다: '
                                    '예) 거장이 매일 확인하되 내가 정한 종목·수량만, 내가 정한 시각에.')
                fill_.button('모두 거장 자동', key=f'dca_all_auto_{key}', icon=':material/auto_mode:', on_click=all_auto, args=(key,),
                             help='거장 투자 · 언제 거장 · 얼마나 거장 · 무엇을 계좌 전략 · 매수 시각 거장 자동으로 채웁니다. 저장은 직접 누릅니다.')
                record = records.get(key) or {}
                value = ((L.read_state(root, ('last_plan',)).get('last_plan') or {}).get('nav') if key == 'actual'
                         else ((record.get('budget') or {}).get('capital_krw'))) or 0  # a 여운봉 book's master size reads the book's value
                st.dataframe(auto_manual_rows(D.clean(new), yeo_book, value), hide_index=True, width='stretch')
                regime_now, ind, want, prices = EV.example_inputs(root, key)
                plan_now = L.read_state(root, ('plan',)).get('plan') or {}
                book_mods = (L.active_modules(settings, L.recommended_set(regime_now)) if key == 'actual'
                             else L.book_modules(settings, key, {'active': plan_now.get('active') or L.active_modules(settings, L.recommended_set(regime_now))}))
                record = records.get(key) or {}
                live = None
                if key != 'actual':
                    from accounts import live_limits
                    budget = record.get('budget') or {}
                    live = {'capital': budget.get('capital_krw') or 0, 'price_cap': budget.get('order_price_max_krw') or 0,
                            'order_max': (live_limits(record=record) or {}).get('order_max_krw') or 0}
                EV.summary(D.clean(new), regime_now, ind, want, prices, state, book_mods, live, yeo=yeo_book, value=value)
            if st.button('저장', key=f'dca_save_{key}', type='primary'):
                try:
                    L.save_settings(root, {'dca': {key: new}})
                except (OSError, ValueError) as exc:
                    st.error(f'저장하지 못했습니다: {exc}')
                else:
                    st.toast('저장했습니다. 다음 확인(평일, 정한 시각 이후)부터 적용합니다.', icon=':material/check_circle:')
                    st.rerun()
    bundle(root, settings)


def module_rows(now, want, history, impacts, prefix):
    """One row per master module: who it is, what it does (MODULES rule), on now / after the bundle, its last change
    (time and who, from the settings history keys prefix + module) and today's effect (the morning plan's impacts)."""
    last = {}
    for row in history or []:  # newest first
        last.setdefault(row[2], row)
    out = []
    for m, info in L.MODULES.items():
        change = last.get(prefix + m)
        out.append({'key': m, '모듈': f'{info[0]} · {info[1]}', '지금': '켬' if now.get(m) else '끔', '반영 후': '켬' if want.get(m) else '끔',
                    '바뀜': '◀ 바뀜' if bool(now.get(m)) != bool(want.get(m)) else '', '하는 일': info[2],
                    '마지막 변경': f"{change[0][:16].replace('T', ' ')} · {change[5]}" if change else '기록 없음(기본값)',
                    '오늘 영향': '; '.join((impacts or {}).get(m) or []) or ('기본 비중' if m == 'bogle_core' else '없음')})
    return out


def approved_off(now, want, history, prefix):
    """Modules the bundle would turn off that were last turned on with the user's approval in chat (who chat:user-approved…)."""
    last = {}
    for row in history or []:
        last.setdefault(row[2], row)
    return [m for m in L.MODULES if now.get(m) and not want.get(m) and (last.get(prefix + m) or ('',) * 6)[5].startswith('chat:user-approved')]


def bundle(root, settings):
    """Save one profile's engine module bundle (its selling side): as the virtual book's engine modules (which the live
    accounts set to '엔진과 같음' follow) or as one live account's own strategy (books.<key>, user 2026-11-18). Explains what
    it is, shows every module (on now / after, who turned it on, today's effect), warns when it turns off a module the user
    approved in chat or lowers a target weight, and asks for a check (user request 2026-11-18)."""
    import longterm_account_view as A
    from accounts import load as load_accounts, longterm_accounts
    head, guide = st.columns([4, 1], vertical_alignment='center')
    head.markdown('**엔진 모듈 묶음 반영** · 거장의 익절·리밸런싱(파는 쪽)은 엔진 모듈입니다. 적립식 설정과 따로 저장됩니다.')
    if guide.button('엔진 모듈 묶음 가이드', key='dca_bundle_guide', icon=':material/menu_book:'):
        G.open_bundle_guide()
    st.caption('이게 뭔가요: 적립식의 거장 프로필은 **사는 쪽**(적립금을 언제·얼마나 살지)만 정합니다. 거장마다 짝으로 권하는 **파는 쪽·비중 조절** 엔진 '
               f"모듈 묶음이 있습니다(예: {D.PROFILES['dalio']['label']} = {bundle_text('dalio')}). 이 버튼은 그 묶음을 엔진(또는 그 계좌 전략)의 "
               '모듈 스위치에 그대로 복사합니다. 반드시 할 필요는 없고, 적립식은 이 버튼 없이도 동작합니다.')
    lives = longterm_accounts(load_accounts())
    targets = ['engine'] + list(lives)
    a, b = st.columns(2)
    key = a.selectbox('반영할 거장', list(D.PROFILES), format_func=profile_label, key='dca_bundle_profile', help=G.tip('bundle'))
    where = b.selectbox('어디에 반영', targets, key='dca_bundle_target',
                        format_func=lambda t: '가상 계좌 엔진(장기투자 → 전략)' if t == 'engine' else f"{lives[t].get('label', t)} 계좌 전략",
                        help=G.tip('bundle_target'))
    plan = L.read_state(root, ('plan',)).get('plan') or {}
    engine_active = plan.get('active') or L.active_modules(settings, L.recommended_set(plan.get('regime')))
    if where != 'engine' and L.yeo_settings(settings, where) is not None:
        st.info(f"{lives[where].get('label', where)}은(는) **여운봉 저수지 매매법** 계좌 전략이라 거장 모듈을 쓰지 않습니다. 모듈 묶음이 적용되지 않습니다. "
                "모듈로 운용하려면 계좌·자동매매 → 이 계좌 '전략'에서 다른 전략을 먼저 고르세요.", icon=':material/block:')
        return
    if where == 'engine':
        if settings['mode'] == 'auto' and settings['auto_live']:
            st.caption('자동 모드(실제 적용)에서는 엔진이 국면 추천 조합을 씁니다. 가상 계좌 엔진 반영은 수동·추천 모드에서 합니다.')
            return
        now = {m: bool(settings['modules'].get(m)) for m in L.MODULES}
        prefix = 'modules.'
        same = {k: L.book_strategy_label(settings, k) == L.BOOK_STRATEGIES['engine'] for k in lives}
        follow = [lives[k].get('label', k) for k, v in same.items() if v]
        own = [lives[k].get('label', k) for k, v in same.items() if not v]
        note = ('저장 위치: 가상 계좌 엔진 모듈(vibe-longterm settings modules). 따라가는 실계좌: ' + (', '.join(follow) or '없음')
                + (' · 자기 전략이라 안 바뀌는 실계좌: ' + ', '.join(own) if own else ''))
        if L.yeo_settings(settings, 'actual') is not None:
            note += ' · 가상 계좌는 지금 여운봉 저수지 매매법이라 이 모듈은 그림자 장부와 "엔진과 같음" 실계좌만 씁니다'
        st.caption(note)
    else:
        mods = L.book_modules(settings, where, {'active': engine_active})
        now = {m: m in mods for m in L.MODULES}
        prefix = f'books.{where}.modules.'
        st.caption(f"저장 위치: {lives[where].get('label', where)} 계좌 전략(books.{where})을 '이 계좌 전략'으로 바꾸고 이 묶음으로 저장합니다. "
                   '가상 계좌 엔진과 다른 계좌는 바뀌지 않습니다.')
    want = {m: m in D.PROFILES[key]['modules'] for m in L.MODULES}
    history = []
    try:
        import config_store
        history = config_store.history(500, kind='vibe-longterm')
    except Exception:  # noqa: BLE001 - the table still shows without the history
        pass
    rows = module_rows(now, want, history, plan.get('impacts'), prefix)
    st.markdown('**지금 켜진 모듈과 반영 뒤** :gray[· 바뀌는 줄에 ◀ · 하는 일은 각 모듈의 규칙, 마지막 변경은 설정 이력, 오늘 영향은 아침 판단]')
    st.dataframe([{k: v for k, v in r.items() if k != 'key'} for r in rows], hide_index=True, width='stretch',
                 column_order=['모듈', '지금', '반영 후', '바뀜', '하는 일', '마지막 변경', '오늘 영향'])
    diff = [m for m in L.MODULES if now[m] != want[m]]
    if not diff:
        st.caption('지금 모듈이 이 묶음과 같습니다. 반영할 것이 없습니다.')
        return
    if approved := approved_off(now, want, history, prefix):
        st.warning('반영하면 꺼지는 모듈 중 대화에서 사용자 승인으로 켠 것이 있습니다: '
                   + ', '.join(f'{L.MODULES[m][0]} · {L.MODULES[m][1]}' for m in approved)
                   + '. 그 승인을 되돌리는 셈이니 의도한 것인지 확인하세요.', icon=':material/gpp_maybe:')
    cuts = []
    if plan.get('indicators'):
        stories = (L.read_state(root, ('story',)).get('story') or {}).get('stories') or {}
        cuts = A.target_cuts(plan, stories, {}, [m for m in L.MODULES if now[m]], [m for m in L.MODULES if want[m]])
    if cuts:
        st.warning('목표 비중이 줄어드는 종목: ' + ', '.join(f'{n} {b:.1%} → {a:.1%}' for n, b, a in cuts)
                   + '. 그 종목을 들고 있으면 다음 주문 계획에서 팔 수 있습니다.', icon=':material/trending_down:')
    elif plan.get('indicators'):
        st.caption('오늘 목표 비중으로 보면 반영해도 줄어드는 종목은 없습니다.')
    sure = st.checkbox('바뀌는 모듈과 그 장부의 다음 주문 계획이 바뀌는 것을 확인했습니다', key='dca_bundle_ok')
    if st.button('엔진 모듈 묶음 반영', key='dca_bundle_save', disabled=not sure):
        change = {'modules': want} if where == 'engine' else {'books': {where: {'strategy': 'custom', 'modules': want}}}
        try:
            L.save_settings(root, change)
        except (OSError, ValueError) as exc:
            st.error(f'저장하지 못했습니다: {exc}')
        else:
            st.toast('저장했습니다. ' + ('다음 아침 판단(08:45)부터 적용합니다.' if where == 'engine' else '그 계좌의 다음 주문 계획(평일 09:40)부터 적용합니다.'),
                     icon=':material/check_circle:')
            st.rerun()
