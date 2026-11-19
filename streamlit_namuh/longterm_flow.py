"""The long-term engine as diagrams (user 2026-11-17, docs/longterm-dashboard-plan.md L1).

`state()` reads the engine's own records (settings, state, the live long-term accounts' day state) and never calls a broker.
`architecture()`, `algorithm()` (Archify workflow) and `regimes()` (Archify lifecycle) turn it into specs whose numbers
and names come from `vibe_longterm` constants and saved settings, so no figure here is written by hand.
`components()` is the table under the diagrams and `summary()` the one-paragraph text other guides reuse.
"""
from __future__ import annotations

from pathlib import Path

import vibe_longterm as L

WIDTH = 188  # Korean tags with live values need room (as in strategy_map)


def state(root=L.ROOT, live=None):
    """Everything the diagrams show. `live`: {account key: {'label', 'execute', 'orders'}} of long-term live accounts
    (None reads accounts.json and their day state)."""
    s = L.read_state(root)
    if live is None:
        live = live_accounts(Path(root).parent)
    return {'settings': L.load_settings(root), 'plan': s.get('plan') or {}, 'last_plan': s.get('last_plan') or {},
            'regime': s.get('regime') or {}, 'story': s.get('story') or {}, 'nav': s.get('nav_latest') or {}, 'live': live}


def live_accounts(nhplug):
    try:
        import accounts
        import longterm_live
        out = {}
        for key, r in accounts.longterm_accounts(accounts.load(Path(nhplug) / 'accounts.json')).items():
            day = longterm_live.load_state(nhplug, key)
            out[key] = {'label': r.get('label', key), 'execute': bool(r.get('live_orders')),
                        'orders': len(day.get('orders') or day.get('planned') or [])}
        return out
    except Exception:
        return {}


def confirm_need(regime, settings):
    """Days in a row a new regime needs to be confirmed: panic's fixed count, else the saved confirm days (the next
    decision counts with the saved settings)."""
    return L.CONFIRM_DAYS.get(regime, settings['tuning']['confirm_days'])


def _hm(stamp):
    return f'{stamp[5:10]} {stamp[11:16]}' if stamp else '기록 전'


def _sleeves(weights):
    total = lambda role: sum(weights.get(k, 0) for k, a in L.ASSETS.items() if a[2] == role)
    return f"코어 {total('core') * 100:.0f} · 위성 {total('sat') * 100:.0f} · 저수지 {total('reservoir') * 100:.0f}"


def _active(st):
    settings = st['settings']
    return st['plan'].get('active') or sorted(k for k, on in settings['modules'].items() if on)


def _masters(keys):
    return ' · '.join(dict.fromkeys(L.MODULES[k][0].split('·')[0].split(' ')[-1] for k in keys if k in L.MODULES))


def summary(st):
    """One paragraph for the engine guide: cadence, weights, rules (from the saved settings)."""
    s, t = st['settings'], st['settings']['tuning']
    return (f"하루 1회(평일 08:45 판단 · 09:40부터 주문) · 목표 비중 {_sleeves(s['weights'])} · "
            f"국면 확정 {t['confirm_days']}거래일(투매 {L.CONFIRM_DAYS['panic']}) · 밴드 ±{t['band'] * 100:.0f}%p · "
            f"하루 매수 순자산 {t['daily_buy_share'] * 100:.0f}% · 저수지 뺀 월 매도 {t['monthly_sell_share'] * 100:.0f}% · 손절 없음 · "
            f"모드 {L.MODES[s['mode']]}")


def architecture(st):
    """Workflow lanes as the system map: sources → engine steps → records → readers, tagged with freshness."""
    plan, last, s = st['plan'], st['last_plan'], st['settings']
    macro = plan.get('macro') or {}
    errors = macro.get('errors') or []
    live = st['live']
    live_tag = ' / '.join(f"{v['label']} · {'실주문' if v['execute'] else '계획만'} · 주문 {v['orders']}건" for v in live.values()) or '연결 계좌 없음'
    nodes = [
        {'id': 'yahoo', 'lane': 'src', 'col': 0, 'type': 'external', 'label': 'Yahoo 일봉', 'sublabel': '나스닥100·VIX·환율·테마 대용',
         'tag': f"미국 종가 {(plan.get('indicators') or {}).get('as_of', '기록 전')}"},
        {'id': 'fred', 'lane': 'src', 'col': 1, 'type': 'external', 'label': 'FRED · 연준 일정', 'sublabel': '금리·신용·발표일(참고)',
         'tag': ('오류 ' + ', '.join(errors))[:40] if errors else ('오류 0' if macro else '기록 전')},
        {'id': 'llm', 'lane': 'src', 'col': 2, 'type': 'cloud', 'label': 'LLM 스토리 점검', 'sublabel': '테마 유지·주의·훼손',
         'tag': f"출처 {st['story'].get('source', '기록 전')}"},
        {'id': 'nh', 'lane': 'src', 'col': 4, 'type': 'external', 'label': 'NH 현재가·잔고', 'sublabel': '주문 가격·실계좌 잔고', 'tag': '조회 전용'},
        {'id': 'decide', 'lane': 'engine', 'col': 1, 'type': 'backend', 'label': '아침 판단', 'sublabel': '국면·모듈·목표·타점',
         'tag': f"판단 {_hm(plan.get('time'))}"},
        {'id': 'trade', 'lane': 'engine', 'col': 3, 'type': 'backend', 'label': '주문 계획', 'sublabel': '밴드·상한 → 주문',
         'tag': f"{last.get('day') or '계획 전'} · {len(last.get('orders') or [])}건"},
        {'id': 'live', 'lane': 'engine', 'col': 4, 'type': 'security', 'label': '장기투자 실계좌', 'sublabel': '계좌 운용금 비례 · 보호 가드',
         'tag': live_tag[:48]},
        {'id': 'close', 'lane': 'engine', 'col': 5, 'type': 'backend', 'label': '마감', 'sublabel': '순자산·그림자 기록',
         'tag': f"순자산 {st['nav']['actual']:,.0f}" if st['nav'].get('actual') else '기록 전'},
        {'id': 'db', 'lane': 'store', 'col': 2, 'type': 'database', 'label': 'longterm.sqlite3', 'sublabel': '결정·순자산·상태', 'tag': '읽기 전용 표시'},
        {'id': 'book', 'lane': 'store', 'col': 3, 'type': 'database', 'label': '가상 계좌 1억', 'sublabel': '수수료 0.015%',
         'tag': '가상 체결' if s['execute'] else '계획만(dry-run)'},
        {'id': 'report', 'lane': 'view', 'col': 2, 'type': 'messagebus', 'label': '전략 리포트 08:50', 'sublabel': '리포트 스킬 · 매매일지', 'tag': '읽기 전용'},
        {'id': 'dash', 'lane': 'view', 'col': 3, 'type': 'messagebus', 'label': '대시보드', 'sublabel': '장기투자 탭·전략 구조', 'tag': '조회만'},
    ]
    for n in nodes:
        n['width'] = WIDTH
    # sides are left to readable-v2: pinned sides here made routes infeasible (archify validate)
    edges = [
        {'from': 'yahoo', 'to': 'decide', 'label': '일봉'}, {'from': 'fred', 'to': 'decide', 'label': '참고', 'role': 'async'},
        {'from': 'llm', 'to': 'decide', 'label': '스토리'}, {'from': 'decide', 'to': 'db', 'label': '계획'},
        {'from': 'db', 'to': 'trade', 'label': '오늘 계획'}, {'from': 'nh', 'to': 'trade', 'label': '가격'},
        {'from': 'trade', 'to': 'book', 'label': '체결'}, {'from': 'nh', 'to': 'live', 'label': '잔고'},
        {'from': 'trade', 'to': 'live', 'label': '같은 규칙', 'role': 'async'}, {'from': 'live', 'to': 'close', 'role': 'branch'},
        {'from': 'book', 'to': 'close', 'label': '평가'}, {'from': 'db', 'to': 'report', 'label': '자료'},
        {'from': 'book', 'to': 'dash', 'label': '보유'},
    ]
    route = ['yahoo', 'decide', 'db', 'trade', 'book', 'close']
    for e in edges:
        if e['from'] in route and e['to'] in route and route.index(e['to']) == route.index(e['from']) + 1:
            e['role'] = 'main'
    return {'schema_version': 2, 'diagram_type': 'workflow',
            'meta': {'title': '장기투자 아키텍처 · 자료 → 엔진 → 기록 → 화면', 'output': 'longterm-architecture.html',
                     'quality_profile': 'standard', 'animation': 'trace'},
            'lanes': [{'id': 'src', 'label': '자료 출처'}, {'id': 'engine', 'label': '장기 엔진'},
                      {'id': 'store', 'label': '기록'}, {'id': 'view', 'label': '리포트·화면'}],
            'phases': [{'id': 'morning', 'label': '08:40~08:50', 'fromCol': 0, 'toCol': 2, 'variant': 'emphasis'},
                       {'id': 'day', 'label': '09:40~마감', 'fromCol': 3, 'toCol': 5, 'variant': 'dashed'}],
            'mainPath': route, 'nodes': nodes, 'edges': edges}


def algorithm(st):
    """One morning decision, step by step, with the saved numbers; the main path is today's route."""
    s, t, plan, last = st['settings'], st['settings']['tuning'], st['plan'], st['last_plan']
    active = _active(st)
    regime_now = L.REGIMES.get(plan.get('regime'), ('판정 전',))[0]
    orders = len(last.get('orders') or []) if last.get('day') and last.get('day') == plan.get('day') else 0
    story_on = 'lynch_story' in active
    nodes = [
        {'id': 'market', 'lane': 'input', 'col': 0, 'type': 'external', 'label': '시황 수집', 'sublabel': '나스닥100 낙폭·200일선·VIX',
         'tag': f"VIX {(plan.get('indicators') or {}).get('vix', 0):.1f}" if plan.get('indicators') else '기록 전'},
        {'id': 'macro', 'lane': 'input', 'col': 3, 'type': 'external', 'label': '거시 배경', 'sublabel': 'FRED 금리·신용·일정', 'tag': '참고만(규칙 미사용)'},
        {'id': 'raw', 'lane': 'judge', 'col': 0, 'type': 'backend', 'label': '원시 국면', 'sublabel': '6개 국면 순서대로',
         'tag': L.REGIMES.get(plan.get('raw'), ('—',))[0]},
        {'id': 'confirm', 'lane': 'judge', 'col': 1, 'type': 'security', 'label': '국면 확정',
         'sublabel': f"{t['confirm_days']}거래일 연속 · 투매 {L.CONFIRM_DAYS['panic']}", 'tag': f'지금 {regime_now}'},
        {'id': 'modules', 'lane': 'judge', 'col': 2, 'type': 'backend', 'label': f"모듈 선택 · {L.MODES[s['mode']]}",
         'sublabel': _masters(active)[:30], 'tag': f"켜짐 {len(active)} · 추천 {len(plan.get('recommended') or [])}"},
        {'id': 'story', 'lane': 'judge', 'col': 3, 'type': 'cloud', 'label': '린치 스토리 점검', 'sublabel': '주의 절반 · 훼손 0',
         'tag': '켜짐' if story_on else '꺼짐'},
        {'id': 'targets', 'lane': 'weights', 'col': 3, 'type': 'backend', 'label': '목표 비중', 'sublabel': _sleeves(s['weights']),
         'tag': f'위성 합계 ≤ {L.SAT_MAX * 100:.0f}%'},
        {'id': 'limits', 'lane': 'weights', 'col': 4, 'type': 'security', 'label': '밴드·상한',
         'sublabel': f"±{t['band'] * 100:.0f}%p · 하루 매수 {t['daily_buy_share'] * 100:.0f}%",
         'tag': f"월 매도 {t['monthly_sell_share'] * 100:.0f}% · 손절 없음"},
        {'id': 'orders', 'lane': 'act', 'col': 4, 'type': 'messagebus', 'label': '주문 계획', 'sublabel': '매도 먼저 · 09:40부터',
         'tag': f'오늘 {orders}건'},
        {'id': 'record', 'lane': 'act', 'col': 5, 'type': 'database', 'label': '기록·그림자', 'sublabel': '기본(기준 비중+달리오)·자동(추천)',
         'tag': '가상 체결' if s['execute'] else '계획만(dry-run)'},
    ]
    for n in nodes:
        n['width'] = WIDTH
    down = {'fromSide': 'bottom', 'toSide': 'top'}
    edges = [
        {'from': 'market', 'to': 'raw', 'label': '지표', **down},
        {'from': 'raw', 'to': 'confirm', 'fromSide': 'right', 'toSide': 'left'},
        {'from': 'confirm', 'to': 'modules', 'label': '국면', 'fromSide': 'right', 'toSide': 'left'},
        {'from': 'modules', 'to': 'story', 'fromSide': 'right', 'toSide': 'left'},
        {'from': 'macro', 'to': 'story', 'label': '참고', 'role': 'async', **down},
        {'from': 'story', 'to': 'targets', **down},
        {'from': 'modules', 'to': 'targets', 'label': '스토리 꺼짐', 'role': 'branch', 'fromSide': 'bottom', 'toSide': 'left'},
        {'from': 'targets', 'to': 'limits', 'fromSide': 'right', 'toSide': 'left'},
        {'from': 'limits', 'to': 'orders', 'label': '밴드 밖', **down},
        {'from': 'limits', 'to': 'record', 'label': '유지', 'role': 'branch', 'fromSide': 'right', 'toSide': 'top'},
        {'from': 'orders', 'to': 'record', 'label': '체결·계획', 'fromSide': 'right', 'toSide': 'left'},
    ]
    route = ['market', 'raw', 'confirm', 'modules'] + (['story'] if story_on else []) + ['targets', 'limits'] + \
            (['orders'] if orders else []) + ['record']
    for e in edges:
        if e['from'] in route and e['to'] in route and route.index(e['to']) == route.index(e['from']) + 1:
            e['role'] = 'main'
    return {'schema_version': 2, 'diagram_type': 'workflow',
            'meta': {'title': f"장기투자 하루 알고리즘 · {plan.get('day') or '판단 전'} · {regime_now}", 'output': 'longterm-algorithm.html',
                     'quality_profile': 'standard', 'animation': 'trace'},
            'lanes': [{'id': 'input', 'label': '입력'}, {'id': 'judge', 'label': '판정'}, {'id': 'weights', 'label': '비중'},
                      {'id': 'act', 'label': '주문·기록'}],
            'mainPath': route, 'nodes': nodes, 'edges': edges}


# regime: (group, lifecycle type) — 'main' holds the calm states, 'stress' the falls, 'heal' the way back
REGIME_PLACE = {'bull': ('main', 'success'), 'overheat': ('main', 'decision'), 'correction': ('stress', 'waiting'),
                'bear': ('stress', 'failure'), 'panic': ('stress', 'failure'), 'recovery': ('heal', 'active')}
REGIME_GROUPS = [{'id': 'main', 'label': '평상'}, {'id': 'stress', 'label': '하락'}, {'id': 'heal', 'label': '회복'}]
# Archify lifecycle v3 lays states out itself (no lanes): the fall-and-recover cycle is the main path, panic and overheat
# branch below (five across is too wide at 1240px), and the groups are named through the type legend
REGIME_PATH = ['bull', 'correction', 'bear', 'recovery']
REGIME_LEGEND = {'success': '평상', 'decision': '평상 · 과열', 'waiting': '하락 · 조정', 'failure': '하락', 'active': '회복'}
# (from, to, label): labels only where the rule is not already the target state's subtitle (crowded gaps, archify validate)
REGIME_MOVES = [('bull', 'overheat', '과열'), ('overheat', 'bull', ''), ('bull', 'correction', ''), ('overheat', 'correction', ''),
                ('correction', 'bear', ''), ('bear', 'panic', ''), ('correction', 'panic', ''), ('panic', 'recovery', ''),
                ('bear', 'recovery', ''), ('correction', 'recovery', ''), ('recovery', 'bull', '다음 판정'), ('recovery', 'correction', '')]


def regimes(st):
    """The six regimes as a state machine; the official one and a pending one are tagged."""
    reg, t = st['regime'], st['settings']['tuning']
    now, pending = reg.get('regime'), reg.get('pending')
    states = []
    for k, (label, rule, _) in L.REGIMES.items():
        _, kind = REGIME_PLACE[k]
        need = confirm_need(k, st['settings'])
        tag = (f"● 지금 · {reg.get('since', '')}부터" if k == now else
               f"◌ 확정 대기 {reg.get('pending_days', 0)}/{need}" if k == pending else f'확정 {need}일')
        states.append({'id': k, 'type': kind, 'label': label, 'sublabel': rule, 'tag': tag})
    transitions = [{'from': a, 'to': b, **({'label': text} if text else {}), **({'variant': 'emphasis'} if a == now and b == pending else {})}
                   for a, b, text in REGIME_MOVES]
    cards = [{'dot': 'cyan', 'title': '확정 규칙', 'items': [f"같은 새 국면이 {t['confirm_days']}거래일 이어져야 바뀜",
                                                       f"투매는 {L.CONFIRM_DAYS['panic']}거래일", '하루에 한 번만 셈(미국 종가 기준)']},
             {'dot': 'amber', 'title': '국면별 추천 모듈', 'items': [f"{label}: {_masters(mods)}" for label, _, mods in list(L.REGIMES.values())[:3]]},
             {'dot': 'emerald', 'title': ' ', 'items': [f"{label}: {_masters(mods)}" for label, _, mods in list(L.REGIMES.values())[3:]]}]
    return {'schema_version': 3, 'diagram_type': 'lifecycle',
            'meta': {'title': '장기투자 국면 전이 · ' + (L.REGIMES[now][0] if now in L.REGIMES else '판정 전'),
                     'output': 'longterm-regimes.html', 'quality_profile': 'standard', 'animation': 'trace',
                     'legend': {'entries': {k: {'label': v} for k, v in REGIME_LEGEND.items()}}},
            'mainPath': REGIME_PATH, 'states': states, 'transitions': transitions, 'cards': cards}


def components(st):
    """The steps with their rule, setting key, file and record kind (the table under the diagrams)."""
    t = st['settings']['tuning']
    return [
        {'단계': '시황 수집', '규칙': '미국 종가 5일 넘게 오래되면 그날 판단 건너뜀', '설정': '—', '파일': 'vibe_longterm.market', '기록': 'skip'},
        {'단계': '거시 배경', '규칙': 'FRED 금리·신용·발표일, 연준 FOMC. 규칙 미사용(참고)', '설정': 'FRED_API_KEY', '파일': 'longterm_macro.collect', '기록': 'macro'},
        {'단계': '국면 판정', '규칙': ' → '.join(v[0] for v in L.REGIMES.values()) + ' 순서로 먼저 맞는 국면', '설정': '—', '파일': 'vibe_longterm.raw_regime', '기록': 'regime'},
        {'단계': '국면 확정', '규칙': f"{t['confirm_days']}거래일 연속(투매 {L.CONFIRM_DAYS['panic']})", '설정': 'tuning.confirm_days', '파일': 'vibe_longterm.confirm', '기록': 'regime'},
        {'단계': '모듈 선택', '규칙': '수동 = 켠 모듈 · 추천 = 그림자만 · 자동 실제 적용 = 국면 추천', '설정': 'mode · auto_live · modules', '파일': 'vibe_longterm.active_modules', '기록': 'regime'},
        {'단계': '스토리 점검', '규칙': '주의 = 위성 절반, 훼손 = 0, 기울기 ±25%. 실패하면 30일 안 이전 판단', '설정': 'modules.lynch_story', '파일': 'vibe_longterm.story_check', '기록': 'story'},
        {'단계': '목표 비중', '규칙': f'켜진 모듈 순서대로 조정, 위성 합계 ≤ {L.SAT_MAX * 100:.0f}%', '설정': 'weights', '파일': 'vibe_longterm.targets', '기록': 'plan'},
        {'단계': '밴드·상한', '규칙': f"±{t['band'] * 100:.0f}%p 밖만 · 하루 매수 {t['daily_buy_share'] * 100:.0f}% · 월 매도 {t['monthly_sell_share'] * 100:.0f}%",
         '설정': 'tuning.band · daily_buy_share · monthly_sell_share', '파일': 'vibe_longterm.trade_targets · orders', '기록': 'plan'},
        {'단계': '주문', '규칙': '09:40부터 매도 먼저. 가상 체결은 "가상 체결" 스위치가 켜졌을 때만', '설정': 'execute', '파일': 'vibe_longterm.trade', '기록': 'order'},
        {'단계': '실계좌', '규칙': '계좌 운용금 비례 · 실주문 스위치 + --execute가 모두 있어야 주문', '설정': 'accounts.json engine longterm', '파일': 'longterm_live.cycle', '기록': 'longterm-live.jsonl'},
        {'단계': '마감', '규칙': '순자산과 그림자(기본 기준 비중+달리오, 자동 추천 조합) 기록', '설정': '—', '파일': 'vibe_longterm.close', '기록': 'nav'},
    ]


def today(st):
    """Today's operating status rows."""
    plan, last, macro = st['plan'], st['last_plan'], st['plan'].get('macro') or {}
    reg = st['regime']
    return [
        {'항목': '판단', '값': _hm(plan.get('time'))},
        {'항목': '확정 국면', '값': L.REGIMES.get(reg.get('regime'), ('판정 전',))[0] + (f" · {reg.get('since')}부터" if reg.get('since') else '')},
        {'항목': '확정 대기', '값': f"{L.REGIMES[reg['pending']][0]} {reg.get('pending_days', 0)}일째" if reg.get('pending') in L.REGIMES else '없음'},
        {'항목': '모드 · 켜진 모듈', '값': f"{L.MODES[st['settings']['mode']]} · {_masters(_active(st))}"},
        {'항목': '스토리 출처', '값': st['story'].get('source', '기록 전')},
        {'항목': '거시 배경', '값': ('오류 ' + ', '.join(macro['errors'])) if macro.get('errors') else ('정상' if macro else '기록 전')},
        {'항목': '오늘 계획', '값': f"{L.why_label(last.get('why'))} · {len(last.get('orders') or [])}건 · "
                                f"{'가상 체결' if last.get('execute') else '계획만'}" if last.get('day') else '계획 전'},
        *[{'항목': v['label'], '값': f"{'실주문' if v['execute'] else '계획만'} · 주문 {v['orders']}건"} for v in st['live'].values()],
    ]


def dot(diagram):
    """Graphviz fallback for any of the three specs (lifecycle states/transitions read as workflow nodes/edges)."""
    import strategy_map
    if diagram['diagram_type'] == 'lifecycle':
        nodes = [{**n, 'lane': REGIME_PLACE[n['id']][0]} for n in diagram['states']]
        diagram = {'lanes': REGIME_GROUPS, 'mainPath': [], 'nodes': nodes, 'edges': diagram['transitions']}
    return strategy_map.dot(diagram)


def modules(st):
    """The eight master modules with their state today: on/off, recommended, and the replay evidence."""
    active, recommended = set(_active(st)), set(st['plan'].get('recommended') or [])
    return [{'운용': '켜짐' if k in active else '꺼짐', '국면 추천': '★ 추천' if k in recommended else '',
             '거장': m[0], '모듈': m[1], '규칙': m[2], '과거 재현': m[3], '고정': '고정' if m[5] else ''} for k, m in L.MODULES.items()]


def module_summary(st):
    """One line over the module table: how many run, how many today's regime recommends, and where they differ."""
    active, recommended = set(_active(st)), set(st['plan'].get('recommended') or [])
    names = lambda keys: ', '.join(L.MODULES[k][0] for k in L.MODULES if k in keys)
    parts = [f'켜짐 {len(active & set(L.MODULES))}/{len(L.MODULES)}', f'오늘 국면 추천 {len(recommended & set(L.MODULES))}']
    if recommended - active:
        parts.append(f'추천인데 꺼짐: {names(recommended - active)}')
    if active - recommended:
        parts.append(f'켜졌지만 추천 아님: {names(active - recommended)}')
    return ' · '.join(parts)


# module: (lane, col, what it moves, the sleeves it touches) — from the module rules in vibe_longterm.MODULES
# one edge per module to the sleeve it mainly moves (more edges ran through the legend; the sublabel names the move)
MODULE_PLACE = {'bogle_core': ('base', 0, '목표 비중', ('core',)), 'dalio_rebalance': ('base', 2, '밴드 복원', ('sat',)),
                'yeo_reservoir': ('defense', 0, '저수지 → 코어', ('core',)), 'marks_pendulum': ('defense', 2, '코어 ↔ 저수지', ('res',)),
                'buffett_lowturn': ('defense', 4, '매도 상한', ('limit',)), 'williams_trend': ('chance', 1, '위성 절반', ('sat',)),
                'williams_rebound': ('chance', 3, '저수지 → 나스닥100', ('res',)), 'lynch_story': ('judge', 3, '위성 조정', ('sat',))}


def module_map(st):
    """The eight master modules (on/off/recommended today) and the sleeves each one moves."""
    active, recommended = set(_active(st)), set(st['plan'].get('recommended') or [])
    weights = st['settings']['weights']
    share = lambda role: sum(weights.get(k, 0) for k, a in L.ASSETS.items() if a[2] == role)
    nodes = []
    for k, (lane, col, moves, _) in MODULE_PLACE.items():
        m = L.MODULES[k]
        tag = ('● 켜짐' if k in active else '○ 꺼짐') + (' · ★ 추천' if k in recommended else '')
        nodes.append({'id': k, 'lane': lane, 'col': col, 'type': 'backend' if k in active else 'external',
                      'label': m[0].split('·')[0].strip(), 'sublabel': f'{m[1]} · {moves}', 'tag': tag, 'width': WIDTH})
    t = st['settings']['tuning']
    nodes += [{'id': 'core', 'lane': 'assets', 'col': 0, 'type': 'database', 'label': '코어', 'sublabel': '나스닥100 · S&P500', 'tag': f"목표 {share('core') * 100:.0f}%"},
              {'id': 'sat', 'lane': 'assets', 'col': 2, 'type': 'database', 'label': '위성', 'sublabel': '반도체 · 양자 · 우주', 'tag': f"목표 {share('sat') * 100:.0f}% · 최대 {L.SAT_MAX * 100:.0f}%"},
              {'id': 'res', 'lane': 'assets', 'col': 3, 'type': 'database', 'label': '저수지', 'sublabel': 'KOFR 금리형', 'tag': f"목표 {share('reservoir') * 100:.0f}%"},
              {'id': 'limit', 'lane': 'assets', 'col': 5, 'type': 'security', 'label': '주문 상한', 'sublabel': '손절 없음',
               'tag': f"월 매도 {t['monthly_sell_share'] * 100:.0f}% · 하루 매수 {t['daily_buy_share'] * 100:.0f}%"}]
    for n in nodes:
        n['width'] = WIDTH
    edges = [{'from': k, 'to': target, **({} if k in active else {'role': 'async'})}
             for k, (_, _, _, targets) in MODULE_PLACE.items() for target in targets]
    return {'schema_version': 2, 'diagram_type': 'workflow',
            'meta': {'title': f"거장 모듈 지도 · {L.MODES[st['settings']['mode']]} · 켜짐 {len(active)}", 'output': 'longterm-modules.html',
                     'quality_profile': 'standard', 'animation': 'trace'},
            'lanes': [{'id': 'base', 'label': '기본'}, {'id': 'defense', 'label': '방어'}, {'id': 'chance', 'label': '추세·기회'},
                      {'id': 'judge', 'label': '판단(LLM)'}, {'id': 'assets', 'label': '자산 슬리브'}],
            'nodes': nodes, 'edges': edges}


def today_flow(st):
    """Today's run, step by step, each step tagged with what happened (✓ done · ✗ missing · … waiting)."""
    plan, last, macro, story = st['plan'], st['last_plan'], st['plan'].get('macro'), st['story']
    decided = bool(plan.get('time'))
    planned = bool(last.get('day')) and last.get('day') == plan.get('day')
    orders = len(last.get('orders') or []) if planned else 0
    errors = (macro or {}).get('errors') or []
    nav = st['nav']
    steps = [
        ('market', 'morning', 0, 'external', '시황 수집', 'Yahoo 미국 종가', f"✓ {(plan.get('indicators') or {}).get('as_of', '')}" if decided else '… 08:45 판단 전'),
        ('macro', 'morning', 1, 'external', '거시 배경', 'FRED · 연준 일정', '… 기록 전' if not macro else f"✗ 빠짐 {len(errors)}" if errors else '✓ 정상'),
        ('regime', 'morning', 2, 'backend', '국면 판정', '원시 → 확정', f"✓ {L.REGIMES.get(plan.get('regime'), ('—',))[0]}" if decided else '…'),
        ('story', 'morning', 3, 'cloud', '스토리 점검', '린치 모듈', f"✓ {story.get('source')}" if story.get('source') == 'llm' else
         f"△ {story.get('source')}" if story.get('source') else '…'),
        ('plan', 'day', 4, 'messagebus', '주문 계획', '09:40부터', f"✓ {L.why_label(last.get('why'))} · {orders}건" if planned else '… 09:40 전'),
        ('fill', 'day', 5, 'database', '가상 계좌', '가상 체결 스위치', ('✓ 가상 체결' if last.get('execute') else '— 계획만(dry-run)') if planned else '…'),
        ('close', 'close', 5, 'database', '마감 기록', '순자산 · 그림자', f"✓ {nav.get('day')}" if nav.get('day') == plan.get('day') and decided else
         f"… 마지막 {nav.get('day')}" if nav.get('day') else '…'),
    ]
    for key, v in st['live'].items():
        steps.append((f'live_{key}', 'day', 3, 'security', v['label'], '장기투자 실계좌', f"{'실주문' if v['execute'] else '— 계획만'} · {v['orders']}건"))
    nodes = [{'id': i, 'lane': lane, 'col': col, 'type': kind, 'label': label, 'sublabel': sub, 'tag': tag, 'width': WIDTH}
             for i, lane, col, kind, label, sub, tag in steps]
    edges = [{'from': 'market', 'to': 'regime'}, {'from': 'macro', 'to': 'story', 'role': 'async'}, {'from': 'regime', 'to': 'story'},
             {'from': 'story', 'to': 'plan'}, {'from': 'plan', 'to': 'fill'}, {'from': 'fill', 'to': 'close'},
             *({'from': 'plan', 'to': f'live_{k}', 'role': 'async'} for k in st['live'])]
    done = [n['id'] for n in nodes if n['tag'].startswith('✓')]
    route = [x for x in ('market', 'regime', 'story', 'plan', 'fill', 'close') if x in done]
    route = route[:next((i for i, (a, b) in enumerate(zip(route, route[1:])) if (a, b) not in {(e['from'], e['to']) for e in edges}), len(route) - 1) + 1]
    for e in edges:
        if e['from'] in route and e['to'] in route and route.index(e['to']) == route.index(e['from']) + 1:
            e['role'] = 'main'
    return {'schema_version': 2, 'diagram_type': 'workflow',
            'meta': {'title': f"오늘 운영 상태 · {plan.get('day') or '판단 전'}", 'output': 'longterm-today.html',
                     'quality_profile': 'standard', 'animation': 'trace'},
            'lanes': [{'id': 'morning', 'label': '아침 판단 08:40~'}, {'id': 'day', 'label': '장중 09:40~14:30'}, {'id': 'close', 'label': '마감'}],
            **({'mainPath': route} if len(route) > 1 else {}), 'nodes': nodes, 'edges': edges}  # archify needs 2+ when given


# ---------- Jev 화면과 같은 자리: 상태 카드 · 자세히 3종 · 국면별 대응 카드 · 매매 경로 카드 (user 2026-11-17) ----------

def rule_rows(st):
    """'자세히 · 운용 기준 전체': every number the engine uses now (saved settings, then fixed engine constants)."""
    s, t = st['settings'], st['settings']['tuning']
    rows = [{'구분': '목표 비중', '항목': f"{a[1]} ({a[0]})", '지금 값': f"{s['weights'].get(k, 0) * 100:.0f}%",
             '뜻': {'core': '코어', 'sat': '위성', 'reservoir': '저수지(대기 자금)'}[a[2]]} for k, a in L.ASSETS.items()]
    rows += [{'구분': '엔진 수치', '항목': v[3], '지금 값': f"{t[k] * 100:.0f}%" if isinstance(t[k], float) else f'{t[k]}일',
              '뜻': f"범위 {v[1]}~{v[2]}" if isinstance(v[1], int) else f"범위 {v[1] * 100:.0f}~{v[2] * 100:.0f}%"} for k, v in L.TUNING.items()]
    rows += [{'구분': '고정 규칙', '항목': name, '지금 값': value, '뜻': meaning} for name, value, meaning in [
        ('모드', L.MODES[s['mode']], '수동 = 켠 모듈 · 추천 = 그림자만 · 자동 실제 적용 = 국면 추천 조합'),
        ('실행 방식', '가상 체결' if s['execute'] else '계획만(dry-run)', '"가상 체결" 스위치(장기투자 → 계좌·실행)'),
        ('위성 합계 상한', f'{L.SAT_MAX * 100:.0f}%', '비중을 어떻게 정해도 위성 3종 합계는 이 값 이하'),
        ('투매 확정', f"{L.CONFIRM_DAYS['panic']}거래일", '다른 국면은 시장 탭의 확정 일수'),
        ('익절 제안', ' · '.join(f'+{x * 100:.0f}%' for x in L.TAKE_PROFIT), '린치 모듈: 평균가 대비 보유의 1/3씩(제안, 주문 안 함)'),
        ('과열 기준', f'200일선 × {L.OVERHEAT:g}', '위성이 이 위로 달아오르면 타점 지도에 덜어내기 제안'),
        ('스토리 유효 기간', f'{L.STORY_MAX_AGE.days}일', 'LLM 점검이 실패하면 이 기간 안의 마지막 판단을 씀'),
        ('작은 차이 무시', f'순자산 {L.MIN_TRADE_SHARE * 100:.1f}%', '목표와의 차이가 이보다 작으면 주문하지 않음'),
        ('수수료(가상)', f'{L.FEE * 100:.3f}%', '가상 체결과 그림자 순자산에 반영'),
        ('시작 자금', f'{L.CAPITAL / 1e8:g}억 원', '가상 계좌'), ('손절', '없음', '버핏·기준 비중: 매도는 리밸런싱·모듈 규칙으로만')]]
    return rows


def regime_rows(st):
    """'자세히 · 국면별 대응': each regime, its rule, confirmation days and recommended modules; ▶ marks the official one."""
    now = st['regime'].get('regime')
    return [{'': '▶ 지금' if k == now else '◌ 대기' if k == st['regime'].get('pending') else '', '국면': label, '판정 조건': rule,
             '확정': f"{confirm_need(k, st['settings'])}거래일", '추천 모듈': _masters(['bogle_core', *mods])}
            for k, (label, rule, mods) in L.REGIMES.items()]


def sources(st, root, now):
    """'자세히 · 참고 데이터': what the engine reads, when it was last refreshed and what happens when it is stale."""
    root = Path(root)
    plan, story, macro = st['plan'], st['story'], st['plan'].get('macro') or {}

    def mtime(path):
        try:
            from datetime import datetime
            return datetime.fromtimestamp(Path(path).stat().st_mtime, now.tzinfo).isoformat(timespec='minutes')
        except OSError:
            return None
    return [
        {'데이터': '미국 종가(나스닥100·VIX·환율·테마 대용)', '출처': 'Yahoo 일봉', '쓰임': '국면 판정·모듈 목표',
         '마지막 갱신': (plan.get('indicators') or {}).get('as_of') or '없음', '기준': '마지막 종가가 5일보다 오래되면 그날 판단 건너뜀'},
        {'데이터': '국내 상장 ETF 일봉', '출처': 'Yahoo(.KS)', '쓰임': '타점 지도·차트', '마지막 갱신': (plan.get('time') or '')[:16].replace('T', ' ') or '없음',
         '기준': '실패하면 타점만 빠지고 판단은 그대로'},
        {'데이터': '금리·신용·발표 일정', '출처': 'FRED · 연준 일정', '쓰임': '스토리 점검 입력·리포트(규칙 미사용)',
         '마지막 갱신': macro.get('as_of') or '없음', '기준': ('빠짐 ' + ', '.join(macro['errors'])) if macro.get('errors') else '실패하면 그 입력만 빠짐'},
        {'데이터': '테마 스토리', '출처': 'LLM(vibe-trading 에이전트)', '쓰임': '린치 모듈 위성 비중',
         '마지막 갱신': (story.get('time') or '')[:16].replace('T', ' ') or '없음', '기준': f'실패하면 {L.STORY_MAX_AGE.days}일 안의 이전 판단, 그 뒤 기본값(유지)'},
        {'데이터': '장전 브리프·매매일지 요약', '출처': '장전 수집 · 매매일지', '쓰임': '스토리 점검 배경(지시 아님)',
         '마지막 갱신': mtime(root.parent / 'shared-etf-paper' / 'premarket-brief.json') or '없음', '기준': '없으면 빼고 점검'},
        {'데이터': '주문 가격', '출처': 'NH 현재가(종가)', '쓰임': '주문 계획·가상 체결', '마지막 갱신': st['last_plan'].get('day') or '없음',
         '기준': '조회 실패 종목은 그날 주문하지 않음'},
        {'데이터': '과거 사례 DB', '출처': 'longterm_cases(주간 갱신)', '쓰임': '타점 근거·리포트', '마지막 갱신': mtime(root / 'cases.sqlite3') or '없음',
         '기준': '없으면 "과거 사례 없음"으로 표시'},
    ]


def route_cards(st):
    """'매매 경로' cards like Jev's entry routes: when, condition (the module's own rule text), size, exit; on = module active."""
    active, t = set(_active(st)), st['settings']['tuning']
    m = lambda k: L.MODULES[k][2]  # noqa: E731 - the module's rule sentence holds its numbers
    return [
        ('1 · 목표 비중 모아가기', [('언제', '첫 운용 · 현금이 목표보다 많을 때'), ('조건', m('bogle_core')),
                              ('크기', f"하루 순자산 {t['daily_buy_share'] * 100:.0f}%까지"), ('매도', '손절 없음')], '#a855f7', 'savings', 'bogle_core' in active),
        ('2 · 월 밴드 리밸런싱', [('언제', '매월 첫 거래일'), ('조건', m('dalio_rebalance')),
                              ('크기', f"목표까지 · 저수지 뺀 매도 월 {t['monthly_sell_share'] * 100:.0f}%까지"), ('매도', '밴드 위 초과분')], '#22c55e', 'balance', 'dalio_rebalance' in active),
        ('3 · 저수지 단계 투입', [('언제', '나스닥100 고점 대비 큰 하락'), ('조건', m('yeo_reservoir')),
                              ('크기', '단계마다 저수지 일부'), ('되돌림', '월 밴드로 천천히')], '#0ea5e9', 'water', 'yeo_reservoir' in active),
        ('4 · 시계추(과열·투매)', [('언제', '과열·투매 국면'), ('조건', m('marks_pendulum')), ('크기', '코어 일부 ↔ 저수지'),
                              ('되돌림', '국면이 바뀌면')], '#f59e0b', 'swap_vert', 'marks_pendulum' in active),
        ('5 · 급락 단기 반등', [('언제', '나스닥100 하루 급락 + VIX 상승'), ('조건', m('williams_rebound')), ('크기', '저수지 일부 → 나스닥100'),
                             ('되돌림', '정한 거래일 뒤')], '#ef4444', 'bolt', 'williams_rebound' in active),
        ('6 · 위성 추세·스토리', [('언제', '매일 아침'), ('조건', f"{m('williams_trend')} / {m('lynch_story')}"),
                             ('크기', f'위성 합계 ≤ {L.SAT_MAX * 100:.0f}%'), ('익절 제안', ' · '.join(f'+{x * 100:.0f}%' for x in L.TAKE_PROFIT) + ' 1/3씩')],
         '#06b6d4', 'rocket_launch', bool({'williams_trend', 'lynch_story'} & active)),
    ]


def status(st, now, enabled=None):
    """Top cards: operation switch, mode, execution, regime and the next step of the day."""
    s, plan = st['settings'], st['plan']
    clock, weekday, today = now.strftime('%H:%M'), now.weekday() < 5, now.strftime('%Y-%m-%d')
    on = s.get('enabled', True) if enabled is None else enabled
    nxt = ('주말 · 다음 평일 08:45 판단' if not weekday else '08:45 아침 판단' if clock < '08:45' and plan.get('day') != today
           else '09:40 주문 계획' if clock < '09:40' else '14:30까지 주문 창' if clock < '14:30' else '마감 기록 · 다음 평일 08:45')
    return [{'label': '장기투자 운영', 'value': '켜짐' if on else '꺼짐', 'tone': 'ok' if on else 'off', 'icon': 'power_settings_new',
             'help': '계좌·자동매매 → 운영 엔진에서 바꿉니다. 꺼지면 판단·주문·마감을 하지 않습니다.'},
            {'label': '모드', 'value': L.MODES[s['mode']], 'tone': 'ai', 'icon': 'tune',
             'sub': ('자동 실제 적용' if s['auto_live'] else '자동 조합은 그림자로만') if s['mode'] == 'auto' else None},
            {'label': '실행 방식', 'value': '가상 체결' if s['execute'] else '계획만', 'tone': 'ok' if s['execute'] else 'off', 'icon': 'play_circle',
             'sub': '가상 계좌 1억 원'},
            {'label': '다음 단계', 'value': nxt, 'tone': 'off', 'icon': 'schedule'}]
