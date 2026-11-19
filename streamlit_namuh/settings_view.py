"""계좌·자동매매 설정 화면 부품: 설정 현황 대시보드(계좌 카드 · 매매 기준 요약 · 한도 흐름 도식)와 색으로 구분한 프리셋 카드.
Read-only except the preset cards' apply buttons (strategy_settings.write_settings, same as the old selectbox)."""
from __future__ import annotations

from html import escape

# Preset tone: 보수적/엄격 = 방어(파랑), 기본 = 초록, 적극적/완화 = 주황, 끄기 = 회색. The name is always written too.
TONES = {'보수적': ('#3b82f6', 'shield'), '엄격': ('#3b82f6', 'shield'), '기본': ('#22c55e', 'check_circle'),
         '적극적': ('#f59e0b', 'bolt'), '완화': ('#f59e0b', 'bolt'), '끄기': ('#64748b', 'block')}

CSS = """
<style>
.sv-card{border:1px solid rgba(148,163,184,.22);border-top:4px solid var(--c);border-radius:12px;padding:12px 14px;
         background:linear-gradient(180deg,rgba(255,255,255,.035),var(--om-card-end,rgba(14,18,35,.55)));margin-bottom:6px;min-height:100%}
.sv-card.sv-on{box-shadow:0 0 0 2px var(--c) inset;background:linear-gradient(180deg,color-mix(in srgb,var(--c) 16%,transparent),var(--om-card-end,rgba(14,18,35,.55)))}
.sv-title{display:flex;align-items:center;gap:6px;font-weight:700;color:var(--c);font-size:1.02rem}
.sv-title .ms{font-size:1.2rem}
.sv-badge{margin-left:auto;font-size:.72rem;font-weight:700;color:#0b1020;background:var(--c);border-radius:999px;padding:2px 8px}
.sv-badge.sv-off{background:transparent;color:var(--om-muted,#94a3b8);border:1px solid rgba(148,163,184,.35)}
.sv-grid{display:grid;grid-template-columns:auto 1fr;gap:3px 10px;margin-top:8px;font-size:.84rem}
.sv-grid .k{color:var(--om-muted,#94a3b8)}.sv-grid .v{color:var(--om-ink,#e2e8f0);font-weight:600;text-align:right;word-break:keep-all}
.sv-wide .sv-grid .v{text-align:left;font-weight:500}
.sv-note{color:var(--om-muted,#94a3b8);font-size:.78rem;margin-top:8px;line-height:1.45;word-break:keep-all}
.sv-chip{display:inline-block;font-size:.75rem;font-weight:700;border-radius:999px;padding:2px 9px;margin:2px 4px 2px 0;
         border:1px solid var(--c);color:var(--c)}
</style>
"""


def inject():
    import streamlit as st
    st.markdown(CSS, unsafe_allow_html=True)


def card_html(title, rows, color='#a855f7', icon=None, badge=None, active=False, note=None, chips=(), wide=False):
    """One card: coloured top edge, title (+ icon, badge), label/value rows, chips and a note. All text escaped.
    wide=True left-aligns values (for sentences rather than numbers)."""
    mark = f'<span class="ms" aria-hidden="true">{escape(icon)}</span>' if icon else ''
    tag = (f'<span class="sv-badge{"" if active else " sv-off"}">{escape(badge)}</span>' if badge else '')
    grid = ''.join(f'<span class="k">{escape(str(k))}</span><span class="v">{escape(str(v))}</span>' for k, v in rows)
    chip = ''.join(f'<span class="sv-chip" style="--c:{c}">{escape(t)}</span>' for t, c in chips)
    return (f'<div class="sv-card{" sv-on" if active else ""}{" sv-wide" if wide else ""}" style="--c:{color}" role="group" aria-label="{escape(title)}">'
            f'<div class="sv-title">{mark}<span>{escape(title)}</span>{tag}</div>'
            + (f'<div style="margin-top:6px">{chip}</div>' if chip else '')
            + (f'<div class="sv-grid">{grid}</div>' if grid else '')
            + (f'<div class="sv-note">{escape(note)}</div>' if note else '') + '</div>')


def current_preset(settings, presets):
    """The preset whose values all match the settings, else '직접 설정'."""
    for name, values in presets.items():
        if isinstance(values, dict):
            if all(abs(settings[k] - v) < 1e-9 for k, v in values.items()):
                return name
        elif abs(settings['entry_volume_ratio'] - values) < 1e-9:
            return name
    return '직접 설정'


def preset_cards(title, caption, presets, rows_of, notes, settings, apply, prefix):
    """Presets as side-by-side colour cards with their values; the active one is outlined, each has its own button.
    rows_of(values) -> [(label, value)]; apply(name) saves it."""
    import streamlit as st
    current = current_preset(settings, presets)
    with st.container(border=True):
        head, now = st.columns([3, 1], vertical_alignment='center')
        head.markdown(f'**{title}**')
        tone = TONES.get(current, ('#a855f7', 'tune'))
        now.markdown(card_html(f'지금: {current}', [], tone[0], tone[1]), unsafe_allow_html=True)
        if caption:
            st.caption(caption)
        for column, (name, values) in zip(st.columns(len(presets)), presets.items()):
            color, icon = TONES.get(name, ('#a855f7', 'tune'))
            on = name == current
            column.markdown(card_html(name, rows_of(values), color, icon, '적용 중' if on else '선택 가능', on, notes.get(name)),
                            unsafe_allow_html=True)
            if column.button('적용 중' if on else f'{name} 적용', key=f'{prefix}{title}_{name}', disabled=on,
                             width='stretch', type='secondary' if on else 'primary'):
                apply(name)


# ── settings overview ──────────────────────────────────────────────────────
def krw_words(value):
    """21000000 -> '2,100만 원', 150000000 -> '1억 5,000만 원', 12345 -> '1만 2,345원' (Korean money reading)."""
    if type(value) not in (int, float):
        return '—'
    value = int(round(value))
    eok, rest = divmod(abs(value), 100_000_000)
    man, won_ = divmod(rest, 10_000)
    parts = [f'{eok:,}억' if eok else '', f'{man:,}만' if man else '', f'{won_:,}' if won_ else '']
    text = ' '.join(p for p in parts if p) or '0'
    return ('-' if value < 0 else '') + text + ('원' if won_ else ' 원')


def won(value):
    return krw_words(value) if type(value) in (int, float) else '—'


LIVE_STOP_GUIDE = ('표시된 값은 계좌 손절 설정입니다. 일반 전략은 전략 경로와 시황이 확인되고 시황이 불리하면(롱 횡보장·하락장, 인버스 상승장·횡보장) '
                   '실계좌 평균가 기준 계좌 설정과 1% 중 더 좁은 값을 적용합니다. 전략이나 시황이 미확인이면 계좌 설정을 씁니다. '
                   '낙폭과대는 전용 청산 계획을 따르며 계좌 손절·하루 손실 보호도 유지됩니다. 모의 청산도 미러합니다.')


def account_rows(record, follow_mode=None):
    """Label/value rows of one account's money settings in Korean money reading."""
    from accounts import live_limits, live_rules, paper_limits
    budget = record.get('budget') or {}
    if record.get('kind') == 'live' and record.get('engine') == 'longterm':  # 2026-11-18: no Jev mirror field on it
        from longterm_account_view import card_rows
        return card_rows(record, follow_mode)
    if record.get('kind') == 'live':
        rules, limits = live_rules(record), live_limits(record=record)
        size = '1주씩' if rules['sizing'] != 'amount' else f"{won(rules['tranche_krw'])}씩"
        return [('운용금', won(budget.get('capital_krw'))), ('매수 크기(분할 1회)', size),
                ('회당 매수 한도', won(limits['order_max_krw'])), ('1주 가격 상한', won(budget.get('order_price_max_krw'))),
                ('하루 손실 한도', won(budget.get('daily_loss_krw'))), ('하루 새 종목', f"{limits['daily_new_symbols']}개"),
                ('계좌 손절 설정', f"−{rules['stop_loss_pct']:g}%"), ('가격 추격', f"{rules['chase_pct']:g}%"
                                                         + (' · 근거 추격' if rules['evidence_chase'] else '')),
                ('미러 실행', follow_mode or '관찰(dry-run)')]
    limits = paper_limits(record)
    return [('분할 1회 매수', won(budget.get('first_buy_krw'))), ('종목당 최대', won(budget.get('per_symbol_krw'))),
            ('회당 매수 한도', won(limits['order_max_krw'])), ('하루 매수 한도', won(limits['daily_buy_krw'])),
            ('보유 금액 한도', won(limits['held_max_krw'])), ('하루 새 종목', f"{limits['daily_new_symbols']}개"),
            ('하루 손실 한도', won(limits['daily_loss_krw']))]


def limits_dot(record, settings=None, follow_mode=None):
    """Graphviz: the checks one buy passes on this account, with the current numbers, in order."""
    from accounts import live_limits, live_rules, paper_limits
    budget = record.get('budget') or {}
    node = lambda name, text, color: f'{name} [label="{text}", color="{color}", fontcolor="#e2e8f0"];'
    head = ('digraph{rankdir=TB; bgcolor="transparent"; nodesep=0.25; ranksep=0.22; node[shape=box, style="rounded,filled", fillcolor="#0E1223", fontname="Helvetica", fontsize=10, penwidth=1.4, width=2.2, height=0.5];'
            ' edge[color="#64748b", arrowsize=0.7];')
    if record.get('kind') == 'live' and record.get('engine') == 'longterm':  # 2026-11-18: the engine's day plan, not the mirror
        from longterm_account_view import FIRST_DAY_NEW_SYMBOLS, mode_of
        limits = live_limits(record=record)
        first = f'\\n(첫 투입일 {FIRST_DAY_NEW_SYMBOLS}개)' if limits['daily_new_symbols'] == 5 else ''
        parts = [node('s', '장기투자 엔진 08:45 판단\\n(국면 · 목표 비중)', '#a855f7'),
                 node('a', f"목표 비중 × 운용금\\n{won(budget.get('capital_krw'))} · 09:40 계산", '#ef4444'),
                 node('b', f"회당 매수 한도\\n{won(limits['order_max_krw'])}", '#f59e0b'),
                 node('c', f"1주 가격 상한\\n{won(budget.get('order_price_max_krw'))}", '#f59e0b'),
                 node('d', f"하루 새 종목 {limits['daily_new_symbols']}개{first}\\n하루 실현 손실 {won(budget.get('daily_loss_krw'))}", '#f59e0b'),
                 node('e', f'주문\\n{follow_mode or mode_of(record)}', '#22c55e')]
        return head + ''.join(parts) + 's->a->b->c->d->e;}'
    if record.get('kind') == 'live':
        rules, limits = live_rules(record), live_limits(record=record)
        size = '1주' if rules['sizing'] != 'amount' else won(rules['tranche_krw'])
        parts = [node('s', '엔진 원장 매수\\n(모의 분할 1회)', '#a855f7'),
                 node('a', f'매수 크기\\n분할 1회 = {size}', '#ef4444'),
                 node('b', f"회당 매수 한도\\n{won(limits['order_max_krw'])}", '#f59e0b'),
                 node('c', f"1주 가격 상한\\n{won(budget.get('order_price_max_krw'))}", '#f59e0b'),
                 node('d', f"운용금\\n{won(budget.get('capital_krw'))}", '#f59e0b'),
                 node('e', f"하루 새 종목 {limits['daily_new_symbols']}개\\n하루 손실 {won(budget.get('daily_loss_krw'))}", '#f59e0b'),
                 node('f', f"주문\\n{follow_mode or '관찰(dry-run): 계획만'}", '#22c55e')]
        return head + ''.join(parts) + 's->a->b->c->d->e->f;}'
    limits = paper_limits(record)
    from tunables import setting
    parts = [node('s', f"매수 신호\\n(Jev ≥ {(settings or {}).get('entry_confidence', 0.5):.2f}\\n· 관심종목 강도 · 신규주)", '#a855f7'),
             node('a', f"분할 1회 매수\\n{won(budget.get('first_buy_krw'))}\\n" + (f"(일반 주문당 최대 {setting('paper_max_order_qty'):,}주)" if setting('paper_max_order_qty') else '(일반 주문 주수 제한 없음)'), '#ef4444'),
             node('b', f"회당 매수 한도\\n{won(limits['order_max_krw'])}", '#f59e0b'),
             node('c', f"종목당 최대\\n{won(budget.get('per_symbol_krw'))}", '#f59e0b'),
             node('d', f"하루 매수 {won(limits['daily_buy_krw'])}\\n새 종목 {limits['daily_new_symbols']}개", '#f59e0b'),
             node('e', f"보유 금액 한도\\n{won(limits['held_max_krw'])}", '#f59e0b'),
             node('f', f"하루 손실 {won(limits['daily_loss_krw'])}\\n도달 시 신규 매수 중단", '#f59e0b'),
             node('g', '모의 주문\\n(주문 가드 통과)', '#22c55e')]
    return head + ''.join(parts) + 's->a->b->c->d->e->f->g;}'


def strategy_rows(settings):
    return [('Jev 매수 신뢰도', f"{settings['entry_confidence']:.2f} 이상"), ('Jev 매도 신뢰도', f"{settings['sell_confidence']:.2f} 이상"),
            ('1차 · 2차 익절', f"+{settings['take_profit_half_pct']:g}% · +{settings['take_profit_all_pct']:g}%"),
            ('손절', f"−{settings['stop_loss_pct']:g}%"), ('추가 매수 간격', f"−{settings['add_step_pct']:g}%마다"),
            ('추세형 절반 · 추적', f"+{settings['trend_half_pct']:g}% · −{settings['trend_trail_pct']:g}%"),
            ('이월 한도 · 스윙', f"{settings['overnight_max_manwon']:g}만 원 · 최대 {settings['swing_max_days']:g}일")]


def account_card(key, record, mode=None):
    """One account's settings as a card (engine, auto, mirror mode chips + money rows)."""
    live = record.get('kind') == 'live'
    from accounts import ENGINE_LABELS
    engine = ENGINE_LABELS.get(record.get('engine'), record.get('engine'))
    chips = [(engine, '#22c55e' if record.get('engine') == 'longterm' else '#a855f7'),
             ('자동매매 ON' if record.get('auto') else '자동매매 OFF', '#22c55e' if record.get('auto') else '#64748b')]
    if live:
        chips.append((mode or '관찰(dry-run)', '#ef4444' if mode == '실주문' else '#f59e0b'))
    return card_html(record.get('label', key), account_rows(record, mode), '#f59e0b' if live else '#22c55e',
                     'account_balance' if live else 'science', '실계좌' if live else '모의', True, chips=chips,
                     note='저장된 설정이 잘못돼 기본값(자동 OFF)으로 보입니다.' if record.get('invalid') else None)


def strategy_card(settings):
    """The paper runner's buy/sell rules with the three quick choices as coloured chips."""
    from strategy_settings import IPO_PRESETS, STRENGTH_PRESETS, VOLUME_PRESETS
    presets = [('관심종목 강도', current_preset(settings, STRENGTH_PRESETS)), ('신규주', current_preset(settings, IPO_PRESETS)),
               ('거래량 확인', current_preset(settings, VOLUME_PRESETS))]
    chips = [(f'{label}: {name}', TONES.get(name, ('#a855f7',))[0]) for label, name in presets]
    return card_html('매매 기준 (모의 실행기 적용 중)', strategy_rows(settings), '#a855f7', 'tune', '전략', True, chips=chips,
                     note='실계좌는 엔진 원장을 따라가므로 이 매매 기준이 실계좌의 진입·청산에도 그대로 반영됩니다.')


def flow(record, settings, mode=None):
    """The buy-limit diagram of one account with its legend."""
    import streamlit as st
    st.markdown('**매수 한 건이 거치는 한도** · 지금 값')
    import theme as THEME
    THEME.graphviz(limits_dot(record, settings, mode), width='content')
    st.caption('보라 = 매수가 시작되는 곳, 빨강 = 매수 크기, 주황 = 한도(하나라도 넘으면 그 주문은 막힘), 초록 = 주문.')


def overview(records, settings, follow_modes):
    """한눈에 보기: every account's card in one row, then the strategy card."""
    import streamlit as st
    inject()
    cards = list(records.items())
    import dialogs as dlg
    for column, (key, record) in zip(st.columns(len(cards)), cards):
        column.markdown(account_card(key, record, follow_modes.get(key)), unsafe_allow_html=True)
        if column.button('자세히', key=f'sv_d_{key}', type='tertiary', icon=':material/open_in_full:'):
            dlg.account_dialog(key, record, follow_modes.get(key), settings)
    left, right = st.columns([3, 2])
    left.markdown(strategy_card(settings), unsafe_allow_html=True)
    right.info('값을 바꾸려면 위 탭에서 계좌를 고르세요. 모의계좌 탭에는 금액·주문 보호 한도와 매매 기준이, 실계좌 탭에는 '
               '그 계좌의 금액·한도·미러 규칙과 오늘 가상 실행이 있습니다.', icon=':material/touch_app:')
