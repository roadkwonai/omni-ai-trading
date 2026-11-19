"""장기투자 → 전략 → 적립식: '적용 설정 요약', '이번 회차 예시' and '거장 값 미세 조정' (user request 2026-11-18, v3). Everything
shown comes from longterm_dca.effective(), i.e. the merged rule (rule()) and plan() itself, recomputed from the card's unsaved
values; the tune editor prefills the master's numbers and returns only what differs from them."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import longterm_dca as D
import longterm_dca_guide as G
import vibe_longterm as L

TUNED, DEFAULT = ':orange-badge[내 조정값]', ':blue-badge[거장 기본값]'


def example_inputs(root, key=None):
    """(regime, indicators, today's target weights, ({key: price}, source)) for the example: the engine's plan of the day (a
    live account: its own strategy's target) and the newest recorded order prices (this account's day state first). No broker call."""
    import longterm_account_view as A
    import vibe_longterm_view as VV
    st_ = L.read_state(root, ('plan', 'last_plan'))
    plan, last = st_.get('plan') or {}, st_.get('last_plan') or {}
    want = L.today_want(plan, last.get('want') if last.get('day') == plan.get('day') else None)
    today = {}
    if key and key != 'actual':
        try:
            import longterm_live
            from src.config.paths import get_runtime_root
            today = longterm_live.load_state(get_runtime_root() / 'nhplug', key)
        except Exception:  # noqa: BLE001 - the example falls back to the engine's prices
            today = {}
    if key and key != 'actual' and plan.get('indicators'):  # a live account's 적립식 splits by ITS strategy's target (books.<key>)
        info = today.get('info') or {}
        if today.get('day') == plan.get('day') and info.get('want'):
            want = info['want']
        else:
            stories = (L.read_state(root, ('story',)).get('story') or {}).get('stories') or {}
            mods = L.book_modules(L.load_settings(root), key, plan)
            want = L.targets(plan['indicators'], mods, plan['regime'], today.get('book') or {}, stories, plan.get('day') or '', plan.get('weights'), plan.get('params'))[0]
    want = L.dca_targets(want, L.yeo_settings(L.load_settings(root), key or 'actual'))  # what the book's strategy buys (v2)
    found = A.latest_prices(today, VV.load(root)[1])
    sources = sorted({src for _, src in found.values()})
    return plan.get('regime'), plan.get('indicators') or {}, want, ({k: p for k, (p, _) in found.items()}, ', '.join(sources) or '가격 기록 없음')


def summary(cfg, regime, ind, want, prices, state, book_modules, live=None, yeo=False, value=None):
    """The applied setting (each part: who decides it, its value, a badge when 내 조정 changed it) and this round's example,
    from effective(). value: the book's value (a 여운봉 book's master size reads it)."""
    prices, source = prices
    eff = D.effective(cfg, regime, ind, want, prices, cash=value if yeo else None, state=state, yeo=yeo)
    label = L.REGIMES[regime][0] if regime in L.REGIMES else '국면 미정'
    st.markdown(f'**적용 설정 요약** :gray[· 저장 전 화면 값으로 계산 · 지금 {label} 국면]')
    lines = []
    for f in eff['fields']:
        if f['key'] == 'modules' and eff['rule']['modules'] and cfg['style'] == 'master':
            same = set(eff['rule']['modules']) == set(book_modules)
            f = {**f, 'value': f['value'] + ' · 거장 묶음: ' + ', '.join(f"{L.MODULES[m][0]} {L.MODULES[m][1]}" for m in eff['rule']['modules'])
                 + (' :green[반영됨]' if same else ' :gray[반영 안 됨(아래 "엔진 모듈 묶음 반영")]')}
        badge = TUNED if f['tuned'] else ''
        extra = f" :gray[(거장 값 {f['default']})]" if f['tuned'] and f['default'] else ''
        lines.append(f"| {f['label']} | {f['value']} {badge}{extra} |")
    st.markdown('| 항목 | 지금 적용 |\n|---|---|\n' + '\n'.join(lines))
    with st.popover('항목 설명', icon=':material/help:'):
        for key, (name, *_rest) in G.FIELDS.items():
            st.markdown(f'**{name}** · {G.field_tip(key)}')
    st.markdown(f"**이번 회차 예시** :gray[· 오늘이 사는 날이라면 · 가격: {source}]")
    if not eff['amount']:
        st.caption(' · '.join(eff['notes']) or '예시를 계산하지 못했습니다.')
        return eff
    rows = [{'종목': r['name'], '배분 비중': f"{r['weight']:.1%}" if r['weight'] else '—',
             '계좌 전략 목표': f"{r['target']:.1%}" if r['target'] else '—', '1주 가격': f"{r['price']:,.0f}원" if r['price'] else '없음',
             '살 금액': f"{r['krw']:,.0f}원" if r['krw'] else '—', '주 수': f"{r['qty']:,}주" if r['qty'] else '—',
             '비고': '제한으로 제외' if r['excluded'] else f"1주 미만 이월 {r['carry']:,.0f}원" if r['carry'] >= 1 else ''} for r in eff['rows']]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
    spent = sum(eff['orders'].values())
    st.caption(f"이번 회차 매수 {spent:,.0f}원 · 남는 적립식 몫 {eff['waiting']:,.0f}원" + (f" · {' / '.join(eff['notes'])}" if eff['notes'] else ''))
    if live:
        st.caption(f"실계좌: 운용금 {live.get('capital', 0):,}원 · 1주 가격 상한 {live.get('price_cap', 0):,}원 · 회당 매수 한도 {live.get('order_max', 0):,}원 · "
                   '여유 현금은 주문 시점에 증권사 잔고로 확인합니다(모자라면 다음 거래일).')
    return eff


def build_tune(base, numbers, mult):
    """The tune to save from the editor: only what differs from the master's rule `base` (percent inputs compared as shown)."""
    out = {}
    for field, scale in (('rate', 10_000), ('pace', 100), ('floor', 100)):
        if field in numbers and round(numbers[field]) != round(base[field] * scale):
            out[field] = numbers[field] / scale
    if 'wait' in numbers and int(numbers['wait']) != base['wait']:
        out['wait'] = int(numbers['wait'])
    changed = {r: round(v / 100, 2) for r, v in mult.items() if v is not None and v == v and round(v) != round(base['mult'][r] * 100)}
    return D.clean_tune(out | ({'mult': changed} if changed else {}))


def tune_editor(prefix, cfg, regime, yeo=False):
    """'거장 값 미세 조정': inputs start from the saved tune over the master's numbers; returns the tune to save ({} = the master's).
    A reset puts the master's values back in the inputs."""
    base, mine = D.rule({**cfg, 'tune': {}}, regime), D.rule(cfg, regime)

    def reset():
        for k in ('rate', 'pace', 'floor', 'wait', 'mult'):
            st.session_state.pop(f'{prefix}_{k}', None)
        st.session_state[f'{prefix}_base'] = True
    start = base if st.session_state.pop(f'{prefix}_base', False) else mine
    on = st.toggle('거장 값 미세 조정', value=bool(cfg['tune']), key=f'{prefix}_on',
                   help='끄면 고른 거장의 값을 그대로 씁니다. 켜면 그 값으로 채운 칸에서 바꾸고, 바꾼 값만 "내 조정값"으로 저장됩니다.')
    if not on:
        return {}
    st.button('전체 거장 값으로', key=f'{prefix}_reset_all', on_click=reset, icon=':material/restart_alt:')
    # (label, low, high, shown value, step) per field this book uses: a 여운봉 book's size reads its value (rate), the 적립금
    # pace (the larger of the two) and its cash floor; a dip master waits
    inputs = {'rate': ('매주 사는 비율(운용자금의 0.01%)', 10, 100, int(round(start['rate'] * 10_000)), 5),
              'pace': ('적립금 속도(%)', 30, 100, int(round(start['pace'] * 100)), 5),
              'floor': ('현금 하한(%)', 5, 50, int(round(start['floor'] * 100)), 5),
              'wait': ('하락일 최대 대기(일)', 1, 10, int(start['wait']), 1)}
    used = [f for f in inputs if (f in ('rate', 'floor') and yeo) or f == 'pace' or (f == 'wait' and base['when'] == 'dip')]
    numbers = {f: col.number_input(*inputs[f][:4], step=inputs[f][4], key=f'{prefix}_{f}', help=G.field_tip(f))
               for f, col in zip(used, st.columns(len(used)))}
    frame = pd.DataFrame([{'국면': L.REGIMES[r][0], '거장 값(배×100)': round(base['mult'][r] * 100), '내 값(배×100)': round(start['mult'][r] * 100)}
                          for r in D.REGIMES])
    st.markdown('**국면별 배수** :gray[· 50~150 = 0.5~1.5배]', help=G.field_tip('mult'))
    edited = st.data_editor(frame, key=f'{prefix}_mult', hide_index=True, width='stretch', disabled=['국면', '거장 값(배×100)'],
                            column_config={'내 값(배×100)': st.column_config.NumberColumn(min_value=50, max_value=150, step=5)})
    tune = build_tune(base, numbers, dict(zip(D.REGIMES, edited['내 값(배×100)'].tolist())))
    if start is mine:  # a value this book does not show (a strategy switch) stays saved, not silently dropped
        tune = D.clean_tune({**{f: v for f, v in cfg['tune'].items() if f in inputs and f not in numbers}, **tune})
    st.caption('내 조정: ' + (D.tune_text(tune) or '없음(거장 값과 같음)'))
    return tune
