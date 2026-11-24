"""운영 엔진 (계좌·자동매매 tab top): run Jev real-time trading, the vibe-trading long-term engine, both, or neither.

The switches are the existing settings (engine_ops): the shared paper account's automation switch and vibe_longterm's
'enabled'. Changing them asks for one confirmation click; turning Jev on while a live account sends real orders asks
for an explicit check, because the live mirror then follows the new paper buys with real money.
"""
from __future__ import annotations

import streamlit as st

import engine_ops
import status_cards as sc

EFFECTS = {
    'jev': {True: '장중 1분마다 Jev 판단으로 공유 모의계좌에 진입·추가 매수하고, 실계좌 미러가 그 체결을 따릅니다(실주문 스위치가 켜진 계좌만 실제 주문).',
            False: '새 진입·추가 매수를 멈춥니다. 보유 종목은 손절·익절·장 마감 정리로 계속 관리하고, 실계좌 미러도 새로 사지 않습니다. '
                   '바로 모두 팔려면 트레이딩 현황 → 즉시 매도를 쓰세요. 대시보드 수동 매수는 그대로 됩니다.'},
    'vibe': {True: '평일 08:40 장기 엔진이 국면·모듈로 판단하고(08:45~09:30), 설정에 따라 가상 계좌 주문 계획·체결과 15:40 마감 기록을 남깁니다.',
             False: '다음 회차부터 장기 엔진이 판단·주문·마감 기록을 하지 않습니다. 가상 계좌 보유는 그대로 두며, 장기투자 전략 리포트 예약 작업도 쓰지 않고 끝납니다.'},
}


def render(nhplug, records):
    status, current = engine_ops.status(nhplug), engine_ops.mode(nhplug)
    with st.container(border=True):
        st.markdown('**운영 엔진** · Jev 실시간 트레이딩과 장기투자를 따로 켜고 끕니다')
        sc.row([{'label': 'Jev 실시간 트레이딩', 'value': 'ON' if status['jev'] else 'OFF', 'icon': 'bolt' if status['jev'] else 'pause_circle',
                 'tone': 'ok' if status['jev'] else 'off', 'sub': '공유 모의계좌 · 실계좌 미러가 따름', 'help': EFFECTS['jev'][status['jev']]},
                {'label': '장기투자', 'value': 'ON' if status['vibe'] else 'OFF', 'icon': 'savings' if status['vibe'] else 'pause_circle',
                 'tone': 'ok' if status['vibe'] else 'off', 'sub': '미국 지수·테마 ETF 가상 계좌', 'help': EFFECTS['vibe'][status['vibe']]}])
        picked = st.segmented_control('운영 엔진 선택', list(engine_ops.MODES), format_func=engine_ops.LABELS.get, default=current,
                                      key=f'engine_mode_{current}', label_visibility='collapsed') or current  # a change elsewhere resets it
        if picked == current:
            st.caption(f'지금: {engine_ops.LABELS[current]}. 바꾸려면 위에서 고른 뒤 적용합니다. 실행기는 다음 회차(1분 안)부터, 장기 엔진은 다음 확인 때부터 따릅니다.')
            return
        jev, vibe = engine_ops.MODES[picked]
        changes = [(name, on) for name, on, was in (('jev', jev, status['jev']), ('vibe', vibe, status['vibe'])) if on != was]
        for name, on in changes:
            st.markdown(f"- **{'Jev 실시간 트레이딩' if name == 'jev' else '장기투자'} {'켜기' if on else '끄기'}**: {EFFECTS[name][on]}")
        armed = [r.get('label', k) for k, r in records.items() if r.get('kind') == 'live' and r.get('live_orders') is True and r.get('auto') is True]
        ok = True
        if jev and not status['jev'] and armed:
            st.warning(f"{', '.join(armed)}의 실주문 스위치가 켜져 있어 Jev가 새로 사면 실계좌도 실제로 따라 삽니다.", icon=':material/warning:')
            ok = st.checkbox('실계좌 실주문이 따라 나가는 것을 확인했습니다', key='engine_live_ack')
        if st.button(f'{engine_ops.LABELS[picked]}(으)로 적용', type='primary', key='engine_apply', disabled=not ok):
            try:
                engine_ops.set_mode(nhplug, picked)
            except (OSError, ValueError) as exc:
                st.error(f'저장하지 못했습니다: {exc}')
            else:
                st.toast(f'운영 엔진: {engine_ops.LABELS[picked]}', icon=':material/check_circle:')
                st.rerun()
