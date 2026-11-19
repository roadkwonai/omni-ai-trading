"""계좌·자동매매 → 모의계좌 → 제한 규칙: every restriction the runner applies (scripts/restrictions.py), whether it is on,
how often it held a buy back today, and switches for the ones that may be turned off (saved to strategy-settings.json)."""
from __future__ import annotations

from restrictions import GROUPS, KIND_LABELS, REASONS, RULES, SWITCHES, counts  # noqa: F401 (counts used by tests)

GROUP_LOOK = {'진입': ('#a855f7', 'login'), '시간': ('#f59e0b', 'schedule'), '추가 매수': ('#f97316', 'stacked_line_chart'),
              '주문': ('#06b6d4', 'receipt_long'), '보유': ('#64748b', 'nightlight'), '안전': ('#22c55e', 'verified_user')}


def value_text(rule_id, settings, limits=None, budget=None):
    """The current value of a 'setting' rule in words."""
    if rule_id == 'volume':
        ratio = settings.get('entry_volume_ratio', 0)
        return f'{ratio:g}배' if ratio else '꺼짐'
    if rule_id == 'qty':
        from tunables import setting
        order, total = setting('paper_max_order_qty'), setting('paper_max_total_qty')
        return f"1회 {f'{order:,}주' if order else '제한 없음'} · 전체 {f'{total:,}주' if total else '제한 없음'}"
    if rule_id == 'overnight':
        amount = settings.get('overnight_max_manwon', 0)
        return f'{amount:g}만 원' if amount else '항상 정리'
    if rule_id == 'order_guard' and limits:
        return (f"회당 {limits['order_max_krw'] // 10_000:,}만 · 하루 {limits['daily_buy_krw'] // 10_000:,}만 · "
                f"보유 {limits['held_max_krw'] // 10_000:,}만 · 새 종목 {limits['daily_new_symbols']}개 · 손실 {limits['daily_loss_krw'] // 10_000:,}만")
    if rule_id == 'budget' and budget:
        return f"분할 1회 {budget['first_buy_krw'] // 10_000:,}만 · 종목당 {budget['per_symbol_krw'] // 10_000:,}만"
    return '—'


def time_of(hhmm):
    from datetime import time
    value = int(hhmm)
    return time(value // 100, value % 100)


def change_text(key, value):
    """'같은 날 재진입 제한 끔' / '장 마감 정리 시각 15:00' for the saved-message."""
    from strategy_settings import LABELS
    if key in SWITCHES:
        return f"{LABELS[key]} {'켬' if value else '끔'}"
    if key.endswith('_hhmm'):
        return f'{LABELS[key]} {value // 100:02d}:{value % 100:02d}'
    return f'{LABELS[key]} {value}'


def render(settings, events, day, save, limits=None, budget=None, prefix=''):
    """The 제한 규칙 view; `save({key: value})` writes the switches and values (raises ValueError on a bad value)."""
    import streamlit as st
    import status_cards as sc
    import settings_view as sv
    sv.inject()
    held = counts(events, day)
    off = [r for r in RULES if r[3] == 'switch' and not settings.get(r[4], 1)]
    sc.row([{'label': '끌 수 있는 규칙', 'value': f'{len(SWITCHES) - len(off)}/{len(SWITCHES)} 켜짐', 'icon': 'toggle_on',
             'tone': 'ok' if not off else 'warn', 'sub': ('꺼짐: ' + ', '.join(r[2] for r in off)) if off else '모두 켜짐',
             'help': '아래에서 스위치로 끄거나 켭니다. 실행기가 다음 주기(약 1분)부터 따릅니다.'},
            {'label': '여기서 설정', 'value': f"{sum(r[3] == 'value' for r in RULES)}개", 'icon': 'schedule', 'tone': 'ai',
             'help': '신규 매수 마감·장 마감 정리 시각, 신규주 동시 보유 수를 아래에서 정합니다.'},
            {'label': '항상 적용', 'value': f"{sum(r[3] == 'fixed' for r in RULES)}개", 'icon': 'lock', 'tone': 'off',
             'help': '안전 규칙·증권사 규칙·별도 승인이 필요한 값입니다. 여기서 끌 수 없습니다.'},
            {'label': '다른 설정으로 조절', 'value': f"{sum(r[3] == 'setting' for r in RULES)}개", 'icon': 'tune', 'tone': 'ai',
             'help': '매매 기준이나 금액·주문 보호 한도의 숫자로 넓히거나 끕니다.'},
            {'label': f'{day[5:]} 막은 매수', 'value': f'{sum(held.values()):,}회', 'icon': 'block',
             'tone': 'warn' if sum(held.values()) else 'off', 'sub': '대기 회차 기준(같은 신호가 매분 반복되면 매분 셉니다)',
             'help': '각 규칙이 오늘 매수를 막은 횟수의 합입니다. 실행기 기록(events.jsonl)에서 셉니다.'}])
    if off:
        st.warning('꺼진 제한: ' + ', '.join(r[2] for r in off) + '. 꺼진 규칙이 막던 매수가 그대로 나갑니다.', icon=':material/warning:')
    flash = st.session_state.pop(f'{prefix}restrict_flash', None)
    if flash:
        st.success(flash, icon=':material/check_circle:')
    with st.form(f'{prefix}restriction_switches', border=False):
        chosen = {}
        for group in GROUPS:
            color, icon = GROUP_LOOK[group]
            with st.container(border=True):
                st.markdown(sv.card_html(group, [], color, icon), unsafe_allow_html=True)
                for rule_id, _, name, kind, key, what, why in (r for r in RULES if r[1] == group):
                    text, control, today = st.columns([6, 2, 1], vertical_alignment='center')
                    text.markdown(f'**{name}** · :gray[{KIND_LABELS[kind]}]  \n{what}  \n:gray[{why}]')
                    if kind == 'switch':
                        chosen[key] = int(control.toggle('켜짐', value=bool(settings.get(key, 1)), key=f'{prefix}restrict_{key}',
                                                         help=f'{what} {why}'))
                    elif kind == 'value' and rule_id == 'cutoff':
                        for field, label in (('entry_cutoff_hhmm', '신규 매수 마감'), ('flatten_hhmm', '장 마감 정리')):
                            picked = control.time_input(label, value=time_of(settings[field]), step=300, key=f'{prefix}restrict_{field}',
                                                        help=f'{what} {why}')
                            chosen[field] = picked.hour * 100 + picked.minute
                    elif kind == 'value' and rule_id == 'careful_values':
                        for field, label, step, cast in (('careful_reentry_vwap_pct', 'VWAP 대비 최대 %', 0.5, float),
                                                         ('careful_reentry_range_pct', '범위 위치 상한 %', 5, int),
                                                         ('careful_reentry_conf', 'Jev 매수 신뢰도', 0.05, float),
                                                         ('careful_reentry_size_pct', '매수 크기 %', 10, int)):
                            chosen[field] = cast(control.number_input(label, step=step, value=cast(settings.get(field)),
                                                                      key=f'{prefix}restrict_{field}', help=f'{what} {why}'))
                    elif kind == 'value':
                        chosen[key] = int(control.number_input('종목 수', step=1, value=int(settings[key]),  # no min/max: a Korean message on save
                                                               key=f'{prefix}restrict_{key}', help=f'{what} {why}'))
                    elif kind == 'setting':
                        control.markdown(f':blue[**{value_text(rule_id, settings, limits, budget)}**]',
                                         help='매매 기준 또는 금액·주문 보호 한도 탭에서 바꿉니다.')
                    else:
                        control.markdown(':gray[**항상 적용**]', help=why)
                    count = held.get(rule_id)
                    today.markdown('' if count is None else f'{count:,}회', help='오늘 이 규칙이 매수를 막은 횟수')
        submitted = st.form_submit_button('제한 규칙 저장', type='primary', icon=':material/save:')
    if submitted:
        changed = {k: v for k, v in chosen.items() if float(settings.get(k, 1)) != float(v)}  # float values too (careful re-entry)
        if not changed:
            st.info('바뀐 값이 없습니다.')
        else:
            try:
                save(chosen)
                st.session_state[f'{prefix}restrict_flash'] = ('저장했습니다: ' + ', '.join(change_text(k, v) for k, v in changed.items())
                                                              + '. 실행기의 다음 주기(약 1분 안)부터 적용됩니다.')
                st.rerun()
            except (OSError, ValueError) as exc:
                st.error(f'저장하지 못했습니다: {exc}')
    st.caption('모의계좌 실행기의 규칙입니다. 실계좌 미러는 엔진 체결 원장을 따라가며 자기 한도(운용금·하루 손실·회당 한도·새 종목·손절)를 따로 지킵니다.')
