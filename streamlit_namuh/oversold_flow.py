"""낙폭과대 알고리즘 한눈에 (user 2026-11-13): every component of the oversold dip-buy algorithm defined in one place,
drawn as a flow with the live settings, listed as a component table, and today's operating state, for the 전략 구조 tab.

Two buy routes share the data layer:
- 선별 v2 낙주매매 (main): market crash day -> candidates -> research veto -> late half buy -> next-day second half ->
  half / final take-profit, session limit, disaster stop.
- 낙폭과대 반등 (intraday): a single code far below its usual range -> fall stopped -> engine BUY -> quick rebound exit.
The data layer (case library, replay record, samples, outcome log, audits) feeds the research and the reviews.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from guides import BUY, GUARD, INFO, SELL, WAIT, _dot, _node, _wf

KST = ZoneInfo('Asia/Seoul')
DATA = '#a855f7'  # the data / learning layer


# the scheduled read-only reports (ops/scheduled-tasks: trading-daily-improvement, oversold-weekly-audit; a test checks the crons)
REPORTS = '평일 16:15 장 마감 개선안 · 금 16:45 주간 감사 (제안만, 적용은 승인 뒤)'


def _hhmm(value):
    from session_times import hhmm
    return hhmm(value)


def stages(v):
    """[{'phase', 'name', 'what', 'data', 'keys', 'code'}] — the algorithm's components, in the order they run."""
    tp, half = v['oversold_v2_tp_pct'], v['oversold_v2_half_tp_pct']
    window = f"{_hhmm(v['oversold_v2_from_hhmm'])}~{_hhmm(v['oversold_v2_until_hhmm'])}"
    return [
        {'phase': '① 감지', 'name': '시장 폭락 판정',
         'what': f"KODEX 200·코스닥150 저가 −{v['oversold_v2_index_drop_pct']:g}% 이상, 또는 직전 5일 −{v['oversold_v2_prior5_pct']:g}% 뒤 "
                 f"−{v['oversold_v2_capit_drop_pct']:g}% 이상 · 전날 밤 나스닥 하락(모르면 매수 안 함) · 매분",
         'data': 'NH 현재가(지수 ETF) · 사례집 지수 일봉(직전 5일) · Yahoo 나스닥',
         'keys': 'oversold_v2_index_drop_pct · capit_drop_pct · prior5_pct', 'code': 'oversold.v2_gate'},
        {'phase': '① 감지', 'name': '후보 고르기',
         'what': f"감시 종목 중 자기 시장과 같이 빠진 종목(개별주 −5%·ETF −3% 이상, 시장보다 {v['oversold_v2_rel_max_pct']:g}%p 넘게 더 빠지지 않음) · 깊은 순",
         'data': 'NH 현재가 · 감시 종목(oversold-universe.json)',
         'keys': 'oversold_v2_rel_max_pct · 감시 종목', 'code': 'oversold.v2_candidates'},
        {'phase': '② 판단', 'name': '리서치 (거부권·크기)',
         'what': f"Jev가 차트·뉴스·수급·시황/해외 테마·애널리스트·과거 사례를 보고 WAIT {v['oversold_v2_veto_conf']:g}+ 면 거부, BUY 0.8+ 면 크기 "
                 f"{v['oversold_v2_boost_pct']:g}% · 후보마다 하루 한 번",
         'data': '분봉 차트 · 뉴스 · 투자자 수급 · market-context · 네이버 컨센서스·리포트 · 사례집 비슷한 사례',
         'keys': 'oversold_v2_veto_conf · boost_pct', 'code': 'nh_paper_isolated.v2_research · nh_etf_auto_v2.jev_oversold7'},
        {'phase': '③ 진입', 'name': '1차 매수',
         'what': f"{window} (신규 매수 마감 뒤에도 이 경로만) · 분할 1회의 {v['oversold_v2_size_pct']:g}%"
                 + (' 중 절반' if v['oversold_v2_add_on'] else '') + f" · 하루 {int(v['oversold_v2_max_new'])}종목",
         'data': '매도 호가 · 주문 가드 한도', 'keys': 'oversold_v2_from/until_hhmm · size_pct · max_new',
         'code': 'nh_paper_isolated.oversold_v2_entry'},
        {'phase': '③ 진입', 'name': '2차 매수 (다음 거래일)',
         'what': ('같은 시간 창에 1차 매수가보다 싸고 손절가보다 위면 나머지 절반 · 손절가는 그대로, 목표는 새 평균 기준'
                  if v['oversold_v2_add_on'] else '꺼짐 (한 번에 전량)'),
         'data': '현재가 · 청산 계획(exit-plans.json)', 'keys': 'oversold_v2_add_on', 'code': 'oversold_v2_entry(add) · update_v2_plan'},
        {'phase': '④ 관리', 'name': '분할 익절',
         'what': f"평균가 +{half:g}% 절반 → +{tp:g}% 나머지 · 매분 매수호가로 확인해 닿는 즉시 매도",
         'data': '매수호가', 'keys': 'oversold_v2_half_tp_pct · tp_pct', 'code': 'plan_levels · split_step · exit_reason'},
        {'phase': '④ 관리', 'name': '보유 유지',
         'what': f"{int(v['oversold_v2_hold_days'])}거래일까지 장 마감 정리에서 빠짐(주말·휴장일은 세지 않음) · "
                 + ('수익 중이면 Jev 보유 SELL(매도 신뢰도 이상)로 매도' if v.get('oversold_v2_jev_sell_on') else 'Jev 보유 SELL로는 팔지 않고 기록')
                 + (f" · 평균가 +{v['oversold_v2_trail_at_pct']:g}% 이상 오른 뒤 고점 −{v['oversold_v2_trail_pct']:g}%면 전량 매도" if v.get('oversold_v2_trail_at_pct') else ''),
         'data': 'KRX 휴장일', 'keys': 'oversold_v2_hold_days', 'code': 'oversold_may_carry · is_v2'},
        {'phase': '⑤ 청산', 'name': '시간 청산 · 재난 손절',
         'what': f"{int(v['oversold_v2_hold_days'])}거래일째 장 마감 정리에서 남은 물량 매도 · 평균가 −{v['oversold_v2_stop_pct']:g}%면 전량 손절 → 저점 {int(v['oversold_v2_reentry_hold_min'])}분 유지·+{v['oversold_v2_reentry_bounce_pct']:g}% 반등 시 재판단 후 재매수(최대 {int(v['oversold_v2_reentry_max'])}번)",
         'data': '매수호가 · 장 마감 정리 시각', 'keys': 'oversold_v2_hold_days · stop_pct', 'code': 'exit_now · flatten'},
        {'phase': '⑥ 기록·학습', 'name': '표본·재현·감사',
         'what': f'장 마감 뒤 사례집 최근 40일 갱신 → 모든 사례에 지금 규칙·v2 전략 재현 · 선별 v2 표본 · {REPORTS}',
         'data': 'Yahoo 일봉·분봉 · 네이버 수급·리포트 · 실행기 이벤트', 'keys': '—',
         'code': 'oversold_cases.collect · oversold_replay · isolated_paper_learning_watch'},
    ]


def flow_dot(v):
    """선별 v2 낙주매매, step by step with the live settings (short lines so the chart stays readable)."""
    on = bool(v.get('oversold_v2_on'))
    window = f"{_hhmm(v['oversold_v2_from_hhmm'])}~{_hhmm(v['oversold_v2_until_hhmm'])}"
    add = bool(v['oversold_v2_add_on'])
    nodes = ' '.join([
        _node('minute', '매분 실행기 주기\\n안전 확인 · 잔고 대조 뒤'),
        _node('gate', f"① 시장 폭락 판정\\n지수 저가 −{v['oversold_v2_index_drop_pct']:g}% 이상\\n"
                      f"또는 5일 −{v['oversold_v2_prior5_pct']:g}% 뒤 −{v['oversold_v2_capit_drop_pct']:g}%\\n전날 나스닥 하락", GUARD),
        _node('cands', f"① 후보\\n감시 종목 중 시장과 같이 빠짐\\n시장 대비 −{v['oversold_v2_rel_max_pct']:g}%p 이내", GUARD),
        _node('research', "② 리서치 · 하루 한 번\\n차트·뉴스·수급·시황\\n애널리스트·과거 사례", INFO),
        _node('buy1', f"③ 1차 매수 {window}\\n분할 1회의 {v['oversold_v2_size_pct']:g}%" + (' 중 절반' if add else '')
                      + f"\\n하루 {int(v['oversold_v2_max_new'])}종목", BUY),
        _node('buy2', '③ 2차 매수 · 다음 거래일\\n1차가보다 싸고 손절가 위\\n나머지 절반' if add else '③ 2차 매수 꺼짐', BUY if add else WAIT),
        _node('half', f"④ 분할 익절\\n평균 +{v['oversold_v2_half_tp_pct']:g}% 절반\\n+{v['oversold_v2_tp_pct']:g}% 나머지", SELL),
        _node('hold', f"④ 보유 유지\\n{int(v['oversold_v2_hold_days'])}거래일까지 정리 제외\\n엔진 매도 의견은 기록만", WAIT),
        _node('exit', f"⑤ 청산\\n{int(v['oversold_v2_hold_days'])}거래일째 정리\\n평균가 −{v['oversold_v2_stop_pct']:g}% 손절 → 저점 재매수", SELL),
        _node('skip', '대기 · 기록만', WAIT),
        _node('veto', f"거부\\nJev WAIT {v['oversold_v2_veto_conf']:g}+\\n그날 다시 안 봄", WAIT),
    ])
    edges = ('minute -> gate; gate -> cands [label=" 폭락"]; gate -> skip [label=" 아님"]; cands -> research [label=" 후보"]; '
             'cands -> skip [label=" 없음"]; research -> veto [label=" WAIT"]; '
             f'research -> buy1 [label=" 통과\\n 강한 동의 {v["oversold_v2_boost_pct"]:g}%"]; '
             'buy1 -> buy2 [label=" 다음 날"]; buy1 -> hold; buy2 -> hold; hold -> half [label=" 목표"]; '
             'hold -> exit [label=" 기간 끝 · 손절"]; half -> exit [label=" 나머지"];')
    body = nodes + ' ' + edges
    if not on:
        body += ' gate [label="선별 v2 꺼짐" color="#64748B" style="rounded,filled,dashed"];'
    return _dot(body)


def rebound_dot(v):
    """낙폭과대 반등 (intraday), the second route, checked when 선별 v2 has nothing."""
    on = bool(v.get('oversold_on'))
    body = ' '.join([
        _node('fall', f"종목 저가\\n개별주 −{v['oversold_stock_drop_pct']:g}% · ETF −{v['oversold_etf_drop_pct']:g}%\\n"
                      f"변동폭 {v['oversold_atr_mult']:g}배 이상", GUARD),
        _node('calm', f"하락 멈춤\\n{int(v['oversold_calm_min'])}분 저가 유지\\n저가 +{v['oversold_rebound_pct']:g}%"),
        _node('judge', f"엔진 확인\\nJev BUY ≥ {v['oversold_jev_min']:.2f}", INFO),
        _node('trade', f"매수 → 매도\\n+{v['oversold_tp_pct']:g}% 또는 낙폭 {v['oversold_recover_pct']:g}% 회복\\n"
                       f"저가 −{v['oversold_stop_pct']:g}% · {int(v['oversold_hold_days'])}거래일", BUY),
        'fall -> calm; calm -> judge; judge -> trade;',
        '' if on else 'fall [label="낙폭과대 반등 꺼짐" color="#64748B" style="rounded,filled,dashed"];'])
    return _dot(body)


def loop_dot(v):
    """The data and learning loop around both routes (daily)."""
    return _dot(' '.join([
        _node('trades', '모의·가상 체결\\n판정·후보·리서치 기록'),
        _node('lib', '사례집 · 재현 기록\\n장 마감 뒤 최근 40일 갱신\\n모든 사례에 v2 전략 재현', DATA),
        _node('samples', '선별 v2 표본\\n매매와 별도로 매일', DATA),
        _node('audit', '개선안·감사 보고서\\n' + REPORTS.replace(' · ', '\\n'), DATA),
        _node('settings', '설정 변경\\n사용자 승인 뒤', GUARD),
        _node('research', '다음 리서치\\n비슷한 과거 사례', INFO),
        'trades -> audit; lib -> samples; samples -> audit; lib -> audit; audit -> settings; lib -> research;']))


def flow_spec(v):
    """flow_dot as an Archify workflow (user rule 2026-11-18: step-by-step flows are drawn with Archify)."""
    on, add = bool(v.get('oversold_v2_on')), bool(v['oversold_v2_add_on'])
    window = f"{_hhmm(v['oversold_v2_from_hhmm'])}~{_hhmm(v['oversold_v2_until_hhmm'])}"
    days = int(v['oversold_v2_hold_days'])
    return _wf('선별 v2 낙주매매' + ('' if on else ' · 꺼짐'), [('detect', '① 감지'), ('judge', '② 판단'), ('entry', '③ 진입'), ('manage', '④ 관리·⑤ 청산'),
                                                        ('skip', '대기·거부', 'exception')],
        [('minute', 'detect', 0, '매분 실행기 주기', '안전 확인 · 잔고 대조 뒤', 'messagebus'),
         ('gate', 'detect', 1, '시장 폭락 판정' if on else '선별 v2 꺼짐',
          f"지수 저가 −{v['oversold_v2_index_drop_pct']:g}% · 또는 5일 −{v['oversold_v2_prior5_pct']:g}% 뒤 −{v['oversold_v2_capit_drop_pct']:g}%",
          'security'),
         ('cands', 'detect', 2, '후보', f"시장과 같이 빠짐 · 시장 대비 −{v['oversold_v2_rel_max_pct']:g}%p 이내", 'security'),
         ('research', 'judge', 2, '리서치 · 하루 한 번', '차트·뉴스·수급·시황·애널리스트·과거 사례', 'cloud'),
         ('buy1', 'entry', 3, f'1차 매수 {window}', f"분할 1회의 {v['oversold_v2_size_pct']:g}%" + (' 중 절반' if add else '')
          + f" · 하루 {int(v['oversold_v2_max_new'])}종목"),
         ('buy2', 'entry', 4, '2차 매수 · 다음 거래일' if add else '2차 매수 꺼짐', '1차가보다 싸고 손절가 위' if add else ''),
         ('hold', 'manage', 4, '보유 유지', f'{days}거래일까지 정리 제외 · 엔진 매도는 기록만', 'database'),
         ('half', 'manage', 5, '분할 익절', f"+{v['oversold_v2_half_tp_pct']:g}% 절반 · +{v['oversold_v2_tp_pct']:g}% 나머지"),
         ('exit', 'skip', 5, '청산', f"{days}거래일째 정리 · 평균가 −{v['oversold_v2_stop_pct']:g}% 손절 → 저점 재매수", 'external'),
         ('skip', 'skip', 1, '대기 · 기록만', '', 'external'),
         ('veto', 'skip', 3, '거부', f"Jev WAIT {v['oversold_v2_veto_conf']:g}+ · 그날 다시 안 봄", 'external')],
        [('minute', 'gate', None, 'main'), ('gate', 'cands', '폭락', 'main'), ('gate', 'skip', '아님', 'error'), ('cands', 'research', '후보', 'main'),
         ('cands', 'skip', '없음', 'error'), ('research', 'veto', 'WAIT', 'error'),
         ('research', 'buy1', f"통과 · 강한 동의 {v['oversold_v2_boost_pct']:g}%", 'main'), ('buy1', 'buy2', '다음 날', 'branch'),
         ('buy1', 'hold', None, 'main'), ('buy2', 'hold'), ('hold', 'half', '목표', 'main'), ('hold', 'exit', '기간 끝 · 손절', 'error'),
         ('half', 'exit', '나머지')],
        main=['minute', 'gate', 'cands', 'research', 'buy1', 'hold', 'half'])


def rebound_spec(v):
    on = bool(v.get('oversold_on'))
    return _wf('낙폭과대 반등 (장중)' + ('' if on else ' · 꺼짐'), [('flow', '선별 v2 후보가 없을 때')],
        [('fall', 'flow', 0, '종목 저가', f"개별주 −{v['oversold_stock_drop_pct']:g}% · ETF −{v['oversold_etf_drop_pct']:g}% · 변동폭 {v['oversold_atr_mult']:g}배",
          'security'),
         ('calm', 'flow', 1, '하락 멈춤', f"{int(v['oversold_calm_min'])}분 저가 유지 · 저가 +{v['oversold_rebound_pct']:g}%"),
         ('judge', 'flow', 2, '엔진 확인', f"Jev BUY ≥ {v['oversold_jev_min']:.2f}", 'cloud'),
         ('trade', 'flow', 3, '매수 → 매도', f"+{v['oversold_tp_pct']:g}% 또는 낙폭 {v['oversold_recover_pct']:g}% 회복 · 저가 −{v['oversold_stop_pct']:g}% · "
          f"{int(v['oversold_hold_days'])}거래일")],
        [('fall', 'calm', None, 'main'), ('calm', 'judge', None, 'main'), ('judge', 'trade', None, 'main')],
        main=['fall', 'calm', 'judge', 'trade'])


def loop_spec(v):
    return _wf('데이터 · 학습 순환 (하루 단위)', [('data', '기록'), ('learn', '학습·감사'), ('apply', '반영')],
        [('trades', 'data', 0, '모의·가상 체결', '판정·후보·리서치 기록', 'database'),
         ('lib', 'data', 1, '사례집 · 재현 기록', '장 마감 뒤 최근 40일 · 모든 사례 v2 재현', 'database'),
         ('samples', 'learn', 2, '선별 v2 표본', '매매와 별도로 매일', 'database'),
         ('audit', 'learn', 3, '개선안·감사 보고서', '평일 16:15 · 금 16:45 (제안만)'),  # REPORTS, short for the box
         ('settings', 'apply', 4, '설정 변경', '사용자 승인 뒤', 'security'), ('research', 'apply', 2, '다음 리서치', '비슷한 과거 사례', 'cloud')],
        [('trades', 'audit', None, 'main'), ('lib', 'samples'), ('samples', 'audit'), ('lib', 'audit'), ('audit', 'settings', None, 'main'),
         ('lib', 'research', None, 'async')],
        main=['trades', 'audit', 'settings'])


def positions(root, now=None):
    """Open 선별 v2 positions with their stage: [{'code', 'opened', 'sessions', 'stage', 'stop_pct', 'half_pct', 'all_pct'}]."""
    import nh_paper_isolated as runner
    today = (now or datetime.now(KST)).date()
    out = []
    for code, plan in runner.exit_plans(root).items():
        if not runner.is_v2(plan):
            continue
        basis = plan.get('target_basis') or {}
        try:
            opened = datetime.fromisoformat(plan['opened']).astimezone(KST).date()
        except (KeyError, TypeError, ValueError):
            continue
        held = runner.sessions_held(opened, today)
        stage = ('2차 매수 완료' if basis.get('added') else '1차만 보유') + (' · 리서치 강한 동의' if basis.get('research') == 'boost' else '')
        out.append({'code': code, 'opened': opened.isoformat(), 'sessions': held, 'left': max(0, int(plan.get('hold_days', 0)) - held),
                    'stage': stage, 'stop_pct': plan.get('stop_pct'), 'half_pct': plan.get('half_pct'), 'all_pct': plan.get('all_pct')})
    return out


def today_events(root, day=None, read=None):
    """Today's 선별 v2 gate states and candidates (with research verdicts) from the runner journal."""
    day = day or datetime.now(KST).strftime('%Y-%m-%d')
    rows = (read or _tail)(Path(root) / 'events.jsonl')
    return ([e for e in rows if str(e.get('time', '')).startswith(day) and e.get('event') == 'oversold_v2_gate'],
            [e for e in rows if str(e.get('time', '')).startswith(day) and e.get('event') == 'oversold_v2_signal'])


def _tail(path, limit=20000):
    try:
        lines = Path(path).read_text(encoding='utf-8').splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in lines:
        if '"oversold_v2' in line:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out
