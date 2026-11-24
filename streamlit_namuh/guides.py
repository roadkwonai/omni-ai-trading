"""Screen guides: one help popover in the same place on every tab, and a large guide dialog with diagrams.

Numbers in the text come from the runner's constants and the saved strategy settings, so the guide shows the rules in
force rather than a copy that drifts. Diagrams are Graphviz DOT drawn by Streamlit in the browser (no system Graphviz).
"""
from __future__ import annotations

import re

import streamlit as st

# Live account protections (AGENTS.md, user-approved): shown on the 실계좌 tab and in its guide.
from accounts import BUDGET_LIMITS as _B, LIVE_RULE_LIMITS as _R  # one source: the limits the live guard and mirror enforce
import dialogs
from settings_view import SIZING_GUIDE  # the same wording as the account form tooltips
LIVE_LIMITS = {'capital_krw': _B['live']['capital_krw'][1], 'daily_loss_krw': _B['live']['daily_loss_krw'][1],
               'order_qty': _R['max_order_qty'][1], 'order_price_max_krw': _B['live']['order_price_max_krw'][1],
               'stop_pct': _R['stop_loss_pct'][1]}  # ceilings; each account sets its own within them


def values():
    """Rules in force: saved settings (or defaults) plus the runner's fixed constants."""
    from strategy_settings import read_settings
    from src.config.paths import get_runtime_root
    import nh_paper_isolated as runner
    from accounts import load as load_accounts, paper_limits
    settings = read_settings(get_runtime_root() / 'nhplug' / 'shared-etf-paper')[0]  # falls back to defaults itself
    account = load_accounts(get_runtime_root() / 'nhplug' / 'accounts.json')['paper-shared']
    limits = paper_limits(account)
    import session_times
    clock = session_times.times(settings)
    return {**settings, 'cutoff': clock['cutoff'], 'flatten': clock['flatten'], 'session_close': clock['close'],
            'strength_min': settings['strength_min_prob'], 'strength_top': int(settings['strength_top_n']),
            'strength_lo': settings['strength_change_min'], 'strength_hi': settings['strength_change_max'],
            'ipo_days': int(settings['ipo_days']), 'ipo_size': settings['ipo_size_pct'], 'ipo_near': settings['ipo_near_high_pct'],
            'ipo_stop': settings['ipo_stop_pct'], 'ipo_half': settings['ipo_half_pct'], 'ipo_trail': settings['ipo_trail_pct'],
            'ipo_start': runner.IPO_START, 'weak_half': settings['weak_half_pct'], 'weak_all': settings['weak_all_pct'],
            'volume_ratio': settings['entry_volume_ratio'], 'tranche': account['budget']['first_buy_krw'], 'per_symbol': account['budget']['per_symbol_krw'],
            'single': limits['order_max_krw'], 'daily_buy': limits['daily_buy_krw'], 'held_max': limits['held_max_krw'],
            'daily_new_symbols': limits['daily_new_symbols'], 'target_lo': runner.TARGET_BOUNDS[0], 'target_hi': runner.TARGET_BOUNDS[1],
            'streak': runner.LOSING_STREAK, 'cooldown': int(runner.COOLDOWN.total_seconds() // 60),
            'overnight_rules': len(runner.OVERNIGHT_RULES)}


# ── diagrams ────────────────────────────────────────────────────────────────
STYLE = ('bgcolor="transparent"; pad=0.25; nodesep=0.3; ranksep=0.4; '
         'node [shape=box style="rounded,filled" fillcolor="#0E1223" color="#334155" fontcolor="#E2E8F0" '
         'fontname="sans-serif" fontsize=13 margin="0.14,0.07"]; '
         'edge [color="#64748B" fontcolor="#94A3B8" fontname="sans-serif" fontsize=11 arrowsize=0.7];')
BUY, SELL, GUARD, WAIT, INFO = '#ef4444', '#3b82f6', '#f59e0b', '#64748B', '#22C55E'  # Korean convention: buy red, sell blue


def _node(name, label, color=None, penwidth=None):
    extra = f' color="{color}" penwidth=2' if color else ''
    return f'{name} [label="{label}"{extra}];'


def _dot(body, rankdir='TB'):
    return f'digraph {{ rankdir={rankdir}; {STYLE} {body} }}'


def entry_dot(v):
    return _dot(' '.join([
        _node('cycle', '1분마다 실행기 주기'),
        _node('safe', '안전 확인\\n미확인 주문 · STOP · 증권사 잔고와 원장 대조', GUARD),
        _node('time', f"신규 매수 시간?\\n09:10 ~ {v['cutoff']}"),
        _node('bars', '분봉 읽기\\n7 ETF + 관심종목 (실패 시 1회 재시도)'),
        _node('jev', 'Jev 판단\\n차트 · 뉴스 · 투자자 · 시장 상황'),
        _node('conf', f"Jev 신뢰도 ≥ {v['entry_confidence']:.2f} ?"),
        _node('confirm', f"데이터 확인\\n지수 방향 일치 + 거래량 {v['volume_ratio']:g}배"),
        _node('strength', f"관심종목 강도 확인\\nJev 선정 상위 {v['strength_top']}위 (확률 ≥ {v['strength_min']:g}) · 전일 대비 +{v['strength_lo']:g}~{v['strength_hi']:g}%\\n"
                          f"분봉 상승 · 거래량 {v['volume_ratio']:g}배"),
        _node('rise', '최근 분봉 상승 등 진입 조건 확인\\n보유 중도 추가 매수 조건·한도 확인'),
        _node('size', f"수량 계산\\n약 {v['tranche'] / 1e4:,.0f}만 원 (회당 {v['single'] / 1e4:,.0f}만 원 이하)"),
        _node('guard', '주문 가드\\n계좌 · 시간 · 금액 한도 · 중복 차단', GUARD),
        _node('buy', '매수 주문 → 체결 대조', BUY),
        _node('wait', '대기 (사유를 기록)', WAIT),
        'cycle -> safe -> time; time -> bars [label="예"]; time -> wait [label="아니오"]; bars -> jev -> conf;',
        'conf -> confirm [label="예"]; conf -> strength [label="아니오·대기"];',
        'confirm -> rise [label="통과"]; confirm -> wait [label="불일치"];',
        _node('ipo', f"신규주 확인\\n상장 후 {v['ipo_days']}거래일 · 기준가·시가 위 · 고가 {v['ipo_near']:g}% 안\\n분봉 상승 · 거래량 (크기 {v['ipo_size']:g}%)"),
        'strength -> rise [label="충족"]; strength -> ipo [label="미충족"]; ipo -> rise [label="충족"]; ipo -> wait [label="미충족"];',
        'rise -> size [label="예"]; rise -> wait [label="아니오"]; size -> guard -> buy;',
    ]))


def exit_dot(v):
    return _dot(' '.join([
        _node('hold', '보유 종목 (1분마다 확인)'),
        _node('stop', f"일반 전략 손절: 평균가 −{v['stop_loss_pct']:g}%\\n시황이 불리하면 최대 −1%로 강화\n(롱: 횡보·하락장 · 인버스: 상승·횡보장)", SELL),
        _node('jev', f"Jev SELL 신뢰도 ≥ {v['sell_confidence']:.2f}\\n→ 전량 매도", SELL),
        _node('plan', '매수 때 고정한 청산 계획'),
        _node('defensive', f"방어형 (하락장 · 횡보장)\\n데이터 목표 {v['target_lo']:g}~{v['target_hi']:g}%에서 전량 익절", SELL),
        _node('trend', f"추세형 (상승장 · 지수 강세일)\\n+{v['trend_half_pct']:g}% 절반 → 고점 대비 −{v['trend_trail_pct']:g}% 추적 손절", SELL),
        _node('weak', f"약세장 (하락장 · 강세 아닌 날)\\n늦어도 +{v['weak_half']:g}% 절반 · +{v['weak_all']:g}% 전량\\n추가 매수 없음", SELL),
        _node('ipo', f"신규주형\\n−{v['ipo_stop']:g}% 손절 · +{v['ipo_half']:g}% 절반 · 고점 −{v['ipo_trail']:g}% 추적", SELL),
        _node('add', f"분할 추가 매수\\n실행기 가격·분봉·시황 조건 확인\\n"
                     f"(종목당 {v['per_symbol'] / 1e4:,.0f}만 원 · 금액 가드 적용)", BUY),
        _node('close', f"{v['flatten']} 장 마감 정리"),
        _node('overnight', f"종가매매 조건 {v['overnight_rules']}개 모두 충족\\n+ 한도 {v['overnight_max_manwon']:g}만 원 → 다음 날 보유"),
        _node('flat', '그 외 전량 매도', SELL),
        'hold -> stop; hold -> jev; hold -> plan; hold -> add; plan -> defensive; plan -> trend; plan -> ipo; defensive -> weak [label="약세장이면"]; hold -> close;',
        'close -> overnight [label="예외"]; close -> flat [label="기본"];',
    ]))


def regime_dot(v):
    return _dot(' '.join([
        _node('data', '시장 상황 수집 (5분)\\n코스피 · 코스닥 종가, 20 · 60일선, 20일 수익률\\n투자자 · 프로그램 · 시장 폭 · 해외 가격'),
        _node('up', '상승장', BUY), _node('side', '횡보장', INFO), _node('down', '하락장', SELL),
        _node('up_r', '레버리지 · 개별주 진입 쉬움\\n지수 강세일이면 추세형 청산(크게 노림)\\n종가매매 다음 날 보유 검토'),
        _node('side_r', '방어형 청산 · 목표 계수 0.5\\n지수가 오늘 0% 이상이면 롱, 이하면 인버스'),
        _node('down_r', '방어형 청산 · 목표 계수 0.4 (빨리 익절)\\n롱은 지수 강세일만 · 인버스는 약세일 허용\\n관심종목은 종목 강도 규칙으로 진입'),
        'data -> up -> up_r; data -> side -> side_r; data -> down -> down_r;',
    ]))


def safety_dot(v):
    return _dot(' '.join([
        _node('runner', '모의 실행기 (유일한 주문 경로)'),
        _node('reconcile', '매 주기 증권사 잔고 · 주문 대조\\n불일치면 스스로 멈춤', GUARD),
        _node('stop', 'STOP 파일\\n신규 매수 중단 · 보유 손절 감시는 계속', GUARD),
        _node('guard', f"주문 가드\\n회당 {v['single'] / 1e4:,.0f}만 · 하루 매수 {v['daily_buy'] / 1e4:,.0f}만\\n"
                       f"보유 {v['held_max'] / 1e4:,.0f}만 · 하루 {v['daily_new_symbols']}종목 · 미확인 주문 차단", GUARD),
        _node('streak', f"연속 손실 {v['streak']}회 → {v['cooldown']}분 신규 진입 쉼", GUARD),
        _node('broker', 'NH 모의계좌'),
        'runner -> reconcile -> guard -> broker; stop -> runner [style=dashed]; streak -> runner [style=dashed];',
    ]))


def live_dot(v=None):
    L = LIVE_LIMITS
    return _dot(' '.join([
        _node('engine', 'Jev 트레이딩 계좌\\n메인 엔진 판단 (Jev) → 모의 체결 미러'),
        _node('observe', '실주문 스위치 꺼짐\\n계좌 한도로 계획·가상 실행만 기록', INFO),
        _node('lt', '장기투자 계좌\\n장기 엔진 아침 판단(08:45) → 그날 계획'),
        _node('ltlive', '장기투자 실행기 (09:40~14:30)\\n계좌 운용금 비례 · 매도 먼저 · 미체결 60초 뒤 취소'),
        _node('ltstop', '장기투자 계좌: 종목 손절 없음\\n하루 손실은 실현 손실+수수료로만 셈', SELL),
        _node('path', '실계좌 보호 경로\\nnh_guarded_live (별도 승인 후에만)'),
        _node('guard', f"실계좌 가드\\n계좌별 운용금 · 하루 손실 {L['daily_loss_krw'] / 1e4:,.0f}만 원\\n"
                       f"매수 1회 최대 {L['order_qty']:,}주 · 1주 가격은 계좌 설정(최대 {L['order_price_max_krw'] // 10_000}만 원) · 미체결 · 미확인 주문 차단", GUARD),
        _node('broker', 'NH 실계좌', BUY),
        _node('stop', f"손절: 하루 손실 {L['daily_loss_krw']:,}원 또는 종목 −{L['stop_pct']:g}% 중 먼저", SELL),
        'engine -> observe; engine -> path [style=dashed label="실주문 스위치 켜짐"]; path -> guard -> broker; broker -> stop [style=dashed];',
        'lt -> ltlive; ltlive -> path [style=dashed label="실주문 스위치 + --execute"]; broker -> ltstop [style=dashed];',
    ]))


def data_dot(v=None):
    return _dot(' '.join([
        _node('nh', 'NH 나무 PLUG\\n시세 · 분봉 · 잔고 · 주문'),
        _node('web', '네이버 금융 · Yahoo\\n지수 · 테마 · 해외 · 투자자'),
        _node('news', '뉴스 헤드라인'),
        _node('premarket', '오늘 장전 수집·분석\\n해외 지표 · 뉴스 · 공시\\n시각·출처 확인 후 과거 맥락으로 제공', INFO),
        _node('movers', '순위 · 관심종목 선정\\n(거래대금 · 급등락 · 인기, Jev 선정)'),
        _node('jev', 'Jev (차트 이미지 모델)'), _node('vibe', '장기투자 엔진 (vibe · 일 1회)\\n미국 ETF · 국면 · 거장 모듈'),
        _node('ltbook', '장기투자 가상 계좌 · 그림자 2개'),
        _node('runner', '공유 모의 실행기 (Jev)'),
        _node('log', 'events.jsonl → SQLite 원장 · 연구 DB'),
        _node('ui', '이 대시보드'),
        'nh -> runner; web -> movers; nh -> movers; web -> jev; news -> jev; movers -> jev; nh -> jev; web -> vibe; nh -> ltbook;',
        'web -> premarket; news -> premarket; premarket -> jev [label="참고"]; premarket -> vibe [label="참고"];',
        'jev -> runner; vibe -> ltbook; runner -> log -> ui; ltbook -> ui; nh -> ui;',
    ], ), rankdir='LR')


def engine_rows(v):
    """Jev trading vs 장기투자 item by item, numbers from the saved settings (replaces the 2026-11-17 Graphviz picture,
    whose text stayed small at any size, 2026-11-19): [(항목, Jev, 장기투자)]."""
    import longterm_flow as LF
    import vibe_longterm as LT
    lt = LT.load_settings()
    t = lt['tuning']
    return [
        ('성격', '장중 단타 · 당일 정리 위주(조건을 채우면 이월)', '미국 지수·테마 ETF 장기·스윙 포트폴리오'),
        ('판단 주기', f"1분마다 · 09:00 개장부터 주문 · {v['flatten']} 정리", '하루 1회 · 08:45 판단 · 09:40 주문 · 15:40 순자산 기록'),
        ('보는 것', '1분봉 차트 이미지(7 ETF + 관심종목) · 뉴스 · 투자자 · 시장 국면 · 장전 자료 · 지난 성적',
         '나스닥100 낙폭 · 200일선 · VIX · 환율 · 테마 추세 · 뉴스(스토리 점검) · FRED(참고)'),
        ('판단', 'Jev 차트 이미지 모델 → 후보 하나와 신뢰도(0~1), 보유 종목엔 HOLD/SELL',
         f"국면 판정(확정 {t['confirm_days']}거래일 · 투매 {LT.CONFIRM_DAYS['panic']}) → 거장 모듈 조합 · 지금 {LT.MODES[lt['mode']]}"),
        ('매수', f"신뢰도 ≥ {v['entry_confidence']:.2f} + 지수 방향·거래량 확인(또는 관심종목 강도) · 분할 1회 약 {v['tranche'] / 1e4:,.0f}만 원",
         f"목표 비중({LF._sleeves(lt['weights'])}) · 밴드 ±{t['band'] * 100:.0f}%p · 모듈 변화일 때만"),
        ('매도', f"Jev SELL ≥ {v['sell_confidence']:.2f} · 국면별 청산 · 손절 · {v['flatten']} 정리",
         f"손절 없음 · 리밸런싱 · 스토리 훼손 · 저수지 뺀 월 매도 {t['monthly_sell_share'] * 100:.0f}%까지"),
        ('계좌', '공유 모의계좌(메인) · Jev 트레이딩 실계좌(미러)',
         f"가상 계좌 1억 원 · 운용 방식 {LT.book_strategy_label(lt, 'actual')} · 장기투자 실계좌(계좌별 운용 방식)"),
        ('평가 기준', '투입금 대비 수익률 · 왕복 승률 · 일 손익', '순자산 · 최대 낙폭 · 기본 그림자(기준 비중+달리오) 대비'),
    ]


def engines_view(v):
    """Jev와 장기투자 판단 방식: the two-column comparison cards (guide centre, 성과 비교, 계좌·자동매매)."""
    import guide_ui as GU
    GU.style()
    GU.section('Jev와 장기투자 판단 방식', '값은 지금 저장된 설정입니다. 두 엔진은 서로 주문에 관여하지 않습니다.')
    GU.compare(engine_rows(v), 'Jev 트레이딩', '장기투자')


def strength_dot(v):
    return _dot(' '.join([
        _node('pick', 'Jev 관심종목 선정 (30분마다)\n순위 상위 후보들을 차트·뉴스·수급으로 비교'),
        _node('prob', '후보마다 확률 · 모두 더하면 1\n예) 삼성전자 0.27 · JYP 0.24 · 이수페타시스 0.24 · …', '#a855f7'),
        _node('rank', f"① Jev 순위 상위 {v['strength_top']}위 안?"),
        _node('floor', f"② 확률 ≥ {v['strength_min']:g}?\n(후보가 많아도 근거 없는 꼴찌 배제)"),
        _node('held', '③ 보유 중이면 추가 매수 조건·한도 확인'),
        _node('move', f"④ 전일 대비 +{v['strength_lo']:g}~{v['strength_hi']:g}%?\n(약한 종목·상한가 근처 제외)"),
        _node('rise', '⑤ 최근 1분봉 상승?'),
        _node('vol', f"⑥ 최근 5분 거래량 ≥ 그 전 5분의 {v['volume_ratio']:g}배?"),
        _node('buy', f"매수 · 약 {v['tranche'] / 1e4:,.0f}만 원 → 국면별 청산 계획", BUY),
        _node('wait', '대기 · 다음 1분에 다시 확인', WAIT),
        'pick -> prob -> rank; rank -> floor [label="예"]; floor -> held [label="예"]; held -> move [label="예"];',
        'move -> rise [label="예"]; rise -> vol [label="예"]; vol -> buy [label="예"];',
        'rank -> wait [label="아니오"]; floor -> wait [label="아니오"]; move -> wait [label="아니오"]; rise -> wait [label="아니오"]; vol -> wait [label="아니오"];',
    ]))


def _ipo_hold(v):
    """'동시 N종목 · 하루 M종목' from the saved new-listing limits (ipo_max_positions, ipo_daily_max)."""
    return f"동시 {int(v['ipo_max_positions'])}종목 · 하루 {int(v['ipo_daily_max'])}종목"


def _ipo_time_stop(v):
    return f" · 산 뒤 {int(v['ipo_time_stop_min'])}분 지나면 정리" if v['ipo_time_stop_min'] else ''


def ipo_dot(v):
    return _dot(' '.join([
        _node('kind', 'KRX KIND 신규상장기업현황 (하루 한 번)\n최근 14일 신규상장 주권 · 스팩 제외'),
        _node('nh', 'NH 시세로 상장일 재확인\n(KIND 목록만으로는 사지 않음)', GUARD),
        _node('days', f"① 상장 후 {v['ipo_days']}거래일 안? · {v['ipo_start']} 이후?"),
        _node('base', '② 기준가 위? (상장일은 공모가, 이후는 전일 종가)'),
        _node('open', '③ 오늘 시가 위? (양봉)'),
        _node('high', f"④ 오늘 고가에서 {v['ipo_near']:g}% 안? (매수세 유지)"),
        _node('bar', f"⑤ 최근 1분봉 상승 · 거래량 {v['volume_ratio']:g}배?"),
        _node('buy', f"매수 · 분할 1회 매수 금액의 {v['ipo_size']:g}% ({_ipo_hold(v)})", BUY),
        _node('exit', f"신규주형 청산\n손절 −{v['ipo_stop']:g}% · +{v['ipo_half']:g}%에서 절반 익절\n나머지 고점 대비 −{v['ipo_trail']:g}% 또는 본전{_ipo_time_stop(v)} · {v['flatten']} 정리\n추가 매수 · 다음 날 보유 없음", SELL),
        _node('wait', '대기 · 다음 1분에 다시 확인', WAIT),
        'kind -> nh -> days; days -> base [label="예"]; base -> open [label="예"]; open -> high [label="예"]; high -> bar [label="예"]; bar -> buy [label="예"]; buy -> exit;',
        'days -> wait [label="아니오"]; base -> wait [label="아니오"]; open -> wait [label="아니오"]; high -> wait [label="아니오"]; bar -> wait [label="아니오"];',
    ]))

DIAGRAMS = {'entry': ('매수 판단 흐름', entry_dot), 'exit': ('보유 · 매도 흐름', exit_dot), 'regime': ('시장 국면별 대응', regime_dot),
            'safety': ('모의 주문 안전장치', safety_dot), 'live': ('실계좌 주문 경로', live_dot), 'data': ('데이터 흐름', data_dot), 'strength': ('관심종목 강도 진입 판단', strength_dot), 'ipo': ('신규주 진입 판단', ipo_dot),
            'oversold': ('낙폭과대 알고리즘 전체 FLOW (선별 v2 · 장중 반등 · 데이터)', lambda v: __import__('oversold_flow').flow_dot(v)),
            'longterm': ('장기투자 하루 알고리즘 (지금 설정)', lambda v: _longterm_dot('algorithm')),
            'longterm_regimes': ('장기투자 국면 전이', lambda v: _longterm_dot('regimes'))}


# Archify versions of the step-by-step flows (user rule 2026-11-18); the *_dot above stay as the fallback.
def _wf(*args, **kwargs):
    import guide_ui as GU
    return GU.workflow(*args, **kwargs)


def entry_flow(v):
    return _wf('매수 판단 흐름 · 1분마다', [('check', '안전·시간'), ('judge', 'Jev 판단'), ('route', '진입 경로'), ('order', '주문'),
                                     ('wait', '대기', 'exception')],
        [('cycle', 'check', 0, '1분 주기', '', 'messagebus'), ('safe', 'check', 1, '안전 확인', '미확인 주문·STOP·잔고 대조', 'security'),
         ('time', 'check', 2, '신규 매수 시간?', f"09:10 ~ {v['cutoff']}", 'security'),
         ('jev', 'judge', 2, 'Jev 판단', '차트·뉴스·투자자·시황', 'cloud'),
         ('conf', 'judge', 3, f"신뢰도 ≥ {v['entry_confidence']:.2f}?", '', 'security'),
         ('confirm', 'route', 3, '데이터 확인', f"지수 방향 + 거래량 {v['volume_ratio']:g}배"),
         ('strength', 'route', 4, '관심종목 강도', f"상위 {v['strength_top']}위 · +{v['strength_lo']:g}~{v['strength_hi']:g}%"),
         ('ipo', 'route', 5, '신규주 확인', f"상장 {v['ipo_days']}거래일 · 크기 {v['ipo_size']:g}%"),
         ('rise', 'order', 4, '분봉 상승·추가 매수 조건', '보유 중이면 한도 확인'),
         ('guard', 'order', 5, '주문 가드 → 매수', f"약 {v['tranche'] / 1e4:,.0f}만 원 · 회당 {v['single'] / 1e4:,.0f}만 이하", 'security'),
         ('wait', 'wait', 3, '대기', '사유를 기록', 'external')],
        [('cycle', 'safe', None, 'main'), ('safe', 'time', None, 'main'), ('time', 'jev', '예', 'main'), ('time', 'wait', '아니오', 'error'),
         ('jev', 'conf', None, 'main'), ('conf', 'confirm', '예', 'main'), ('conf', 'strength', '아니오', 'branch'),
         ('confirm', 'rise', '통과', 'main'), ('confirm', 'wait', '불일치', 'error'), ('strength', 'rise', '충족'), ('strength', 'ipo', '미충족', 'branch'),
         ('ipo', 'rise', '충족'), ('ipo', 'wait', '미충족', 'error'), ('rise', 'guard', '예', 'main'), ('rise', 'wait', '아니오', 'error')],
        main=['cycle', 'safe', 'time', 'jev', 'conf', 'confirm', 'rise', 'guard'])


def exit_flow(v):
    return _wf('보유 · 매도 흐름 · 1분마다', [('hold', '보유'), ('cut', '손절·판단 매도', 'exception'), ('plan', '청산 계획'), ('close', '장 마감')],
        [('hold', 'hold', 0, '보유 종목', '1분마다 확인', 'database'),
         ('add', 'hold', 2, '분할 추가 매수', f"조건 확인 · 종목당 {v['per_symbol'] / 1e4:,.0f}만"),
         ('stop', 'cut', 1, '손절', f"평균가 −{v['stop_loss_pct']:g}% · 불리한 국면 최대 −1%", 'security'),
         ('jev', 'cut', 2, 'Jev SELL', f"신뢰도 ≥ {v['sell_confidence']:.2f} → 전량", 'cloud'),
         ('plan', 'plan', 1, '매수 때 고정한 청산 계획'),
         ('defensive', 'plan', 2, '방어형', f"하락·횡보장 · 목표 {v['target_lo']:g}~{v['target_hi']:g}% 전량"),
         ('trend', 'plan', 3, '추세형', f"+{v['trend_half_pct']:g}% 절반 → 고점 −{v['trend_trail_pct']:g}% 추적"),
         ('weak', 'plan', 4, '약세장', f"늦어도 +{v['weak_half']:g}% 절반 · +{v['weak_all']:g}% 전량"),
         ('ipo', 'plan', 5, '신규주형', f"−{v['ipo_stop']:g}% · +{v['ipo_half']:g}% 절반 · 고점 −{v['ipo_trail']:g}%"),
         ('flatten', 'close', 1, f"{v['flatten']} 장 마감 정리", '', 'messagebus'),
         ('overnight', 'close', 2, '다음 날 보유', f"종가매매 조건 {v['overnight_rules']}개 + 한도 {v['overnight_max_manwon']:g}만"),
         ('flat', 'close', 3, '그 외 전량 매도', '', 'external')],
        [('hold', 'plan', None, 'main'), ('hold', 'stop'), ('hold', 'jev'), ('hold', 'add', None, 'async'), ('plan', 'defensive', None, 'main'),
         ('plan', 'trend'), ('plan', 'ipo'), ('defensive', 'weak', '약세장이면', 'branch'), ('hold', 'flatten'),
         ('flatten', 'overnight', '예외', 'branch'), ('flatten', 'flat', '기본')],
        main=['hold', 'plan', 'defensive'])


def _checklist(title, head, checks, buy, after=None):
    """A yes/no gate chain (Archify has six columns): the source on top, each check (예 → next, 아니오 → wait) left to right,
    then buy under the last check (and what follows it)."""
    last = len(checks) - 1
    nodes = [('src', 'in', 0, *head)] + [(f'c{i}', 'check', i, *c, 'security') for i, c in enumerate(checks)]
    nodes += [('buy', 'act', last, *buy), ('wait', 'wait', 2, '대기', '다음 1분에 다시 확인', 'external')]
    ids = ['src', *(f'c{i}' for i in range(len(checks))), 'buy']
    edges = [(a, b, '예' if a.startswith('c') else None, 'main') for a, b in zip(ids, ids[1:])]
    edges += [(f'c{i}', 'wait', '아니오', 'error') for i in range(len(checks))]
    if after:
        nodes.append(('after', 'act', last + 1, *after))
        edges.append(('buy', 'after', None, 'async'))
    return _wf(title, [('in', '자료'), ('check', '확인 · 모두 예일 때만'), ('act', '매수·청산'), ('wait', '대기', 'exception')],
               nodes, edges, main=ids)


def strength_flow(v):
    return _checklist('관심종목 강도 진입 판단',
        ('Jev 관심종목 선정', '30분마다 · 후보별 확률(합 1)', 'cloud'),
        [(f"순위 상위 {v['strength_top']}위?", ''), (f"확률 ≥ {v['strength_min']:g}?", '근거 없는 꼴찌 배제'), ('보유 중 추가 조건', '한도 확인'),
         (f"+{v['strength_lo']:g}~{v['strength_hi']:g}%?", '약한 종목·상한가 근처 제외'), ('1분봉 상승?', ''),
         (f"거래량 {v['volume_ratio']:g}배?", '최근 5분 vs 그 전 5분')],
        (f"매수 약 {v['tranche'] / 1e4:,.0f}만 원", '국면별 청산 계획'))


def ipo_flow(v):
    return _checklist('신규주 진입 판단',
        ('KIND 신규상장 목록', 'NH 상장일 재확인 · 최근 14일 · 스팩 제외', 'external'),
        [(f"상장 {v['ipo_days']}거래일 안?", f"{v['ipo_start']} 이후"), ('기준가 위?', '상장일 공모가 · 이후 전일 종가'), ('시가 위?', '양봉'),
         (f"고가에서 {v['ipo_near']:g}% 안?", '매수세 유지'), ('1분봉 상승 · 거래량?', f"{v['volume_ratio']:g}배")],
        (f"매수 · 분할 1회의 {v['ipo_size']:g}%", _ipo_hold(v)),
        ('신규주형 청산' + (f" · {int(v['ipo_time_stop_min'])}분 뒤 정리" if v['ipo_time_stop_min'] else ''),
         f"−{v['ipo_stop']:g}% · +{v['ipo_half']:g}% 절반 · 고점 −{v['ipo_trail']:g}% · {v['flatten']} 정리"))


def live_flow(v=None):
    L = LIVE_LIMITS
    return _wf('실계좌 주문 경로', [('jev', 'Jev 트레이딩 계좌'), ('lt', '장기투자 계좌'), ('guard', '보호 경로', 'exception'), ('broker', '증권사')],
        [('engine', 'jev', 0, 'Jev 판단', '모의 체결 미러', 'cloud'), ('observe', 'jev', 1, '실주문 스위치 꺼짐', '계획·가상 실행만 기록', 'database'),
         ('lt', 'lt', 0, '장기 엔진 08:45', '그날 계획', 'security'), ('ltlive', 'lt', 1, '장기투자 실행기', '09:40~14:30 · 매도 먼저'),
         ('ltstop', 'lt', 4, '종목 손절 없음', '하루 손실은 실현 손실+수수료', 'external'),
         ('path', 'guard', 2, '실계좌 보호 경로', 'nh_guarded_live · 별도 승인 후', 'security'),
         ('guard', 'guard', 3, '실계좌 가드', f"계좌별 운용금 · 하루 손실 {L['daily_loss_krw'] / 1e4:,.0f}만 · 미체결 차단", 'security'),
         ('broker', 'broker', 4, 'NH 실계좌', '', 'external'),
         ('stop', 'broker', 5, '손절', f"하루 손실 {L['daily_loss_krw']:,}원 또는 −{L['stop_pct']:g}% 먼저", 'external')],
        [('engine', 'observe', None, 'main'), ('engine', 'path', '실주문 스위치 켜짐', 'branch'), ('lt', 'ltlive', None, 'main'),
         ('ltlive', 'path', '실주문 스위치 + --execute', 'branch'), ('path', 'guard', None, 'main'), ('guard', 'broker', None, 'main'),
         ('broker', 'stop', None, 'async'), ('broker', 'ltstop', None, 'async')],
        main=['engine', 'observe'])


def data_flow(v=None):
    return _wf('데이터 흐름', [('src', '자료'), ('mid', '선정·분석'), ('engine', '판단 엔진'), ('out', '실행·기록')],
        [('nh', 'src', 0, 'NH 나무 PLUG', '시세·분봉·잔고·주문', 'external'), ('web', 'src', 1, '네이버 금융 · Yahoo', '지수·테마·해외·투자자', 'external'),
         ('news', 'src', 2, '뉴스 헤드라인', '', 'external'),
         ('movers', 'mid', 1, '순위·관심종목 선정', '거래대금·급등락·인기', 'messagebus'),
         ('premarket', 'mid', 2, '오늘 장전 수집·분석', '시각·출처 확인 후 맥락으로', 'cloud'),
         ('jev', 'engine', 2, 'Jev', '차트 이미지 모델 · 1분', 'cloud'), ('vibe', 'engine', 3, '장기투자 엔진', '미국 ETF · 하루 1회', 'cloud'),
         ('runner', 'out', 3, '공유 모의 실행기', 'Jev', 'backend'), ('ltbook', 'out', 4, '장기투자 가상 계좌', '그림자 2개', 'database'),
         ('log', 'out', 5, 'SQLite 원장 · 대시보드', 'events.jsonl → 연구 DB', 'database')],
        [('nh', 'movers'), ('web', 'movers'), ('movers', 'jev', None, 'main'), ('news', 'jev'), ('nh', 'jev', None, 'main'),
         ('web', 'premarket'), ('news', 'premarket'), ('premarket', 'jev', '참고', 'async'), ('premarket', 'vibe', '참고', 'async'),
         ('web', 'vibe'), ('jev', 'runner', None, 'main'), ('vibe', 'ltbook'), ('runner', 'log', None, 'main'), ('ltbook', 'log')],
        main=['nh', 'jev', 'runner', 'log'])


FLOWS = {'entry': entry_flow, 'exit': exit_flow, 'strength': strength_flow, 'ipo': ipo_flow, 'live': live_flow, 'data': data_flow,
         'oversold': lambda v: __import__('oversold_flow').flow_spec(v),
         'longterm': lambda v: _longterm_spec('algorithm'), 'longterm_regimes': lambda v: _longterm_spec('regimes')}


def _longterm_spec(name):
    import longterm_flow as LF
    return getattr(LF, name)(LF.state(live={}))


def _longterm_dot(name):
    import longterm_flow as LF
    return LF.dot(getattr(LF, name)(LF.state(live={})))


# ── screen guides ───────────────────────────────────────────────────────────
GUIDES = {
    'summary': {
        'title': '종합 현황', 'purpose': '모의계좌(Jev)·실계좌를 한 화면에서 봅니다. 오늘 손익, 거래, 보유, 판단·실행 활동과 주의 사항을 모았습니다.',
        'steps': ['맨 위 카드에서 오늘 실현손익(모의·가상 / 실계좌), 거래 수, 보유, 실행기 상태와 주의 건수를 봅니다.',
                  '계좌별 요약 표로 계좌마다 오늘·누적 결과, 보유, 마지막 활동을 비교합니다.',
                  '차트: 누적 실현손익(계좌별 선), 거래 타임라인(▲매수 ▼매도), 보유 매입금액, 10분 단위 판단·실행 활동.',
                  '아래 탭에서 오늘 거래 전체(판단 근거 포함), 최근 판단·계획, 주의 사항을 봅니다. 자세한 내용은 각 탭으로 갑니다.'],
        'terms': {'모의계좌': '공유 모의계좌에서 Jev가 매매합니다. 장기투자는 "장기투자" 탭의 별도 가상 계좌입니다.',
                  '실계좌': '엔진을 따라가는 실계좌 미러입니다. 실체결이 없는 날(관찰 모드)은 미러 실행기와 같은 계산의 가상 결과입니다.',
                  '증권사 조회 없음': '이 화면은 원장과 실행기 기록만 읽어 빠르게 열립니다. 증권사 잔고·미체결은 트레이딩 현황·주문·정리에서 조회합니다.'},
        'diagrams': ['data', 'live'],
    },
    'market': {
        'title': '시장 상황', 'purpose': '국내 지수와 투자자 수급, 테마와 해외 지표, 오늘의 장전 분석을 확인합니다.',
        'steps': ['상단 카드에서 코스피·코스닥 국면, 상승·하락 종목 수와 자료 수집 시각을 확인합니다.', '아래에서 두 지수의 차트와 수급, 테마 등락과 해외 지표를 확인합니다.',
                  '장전 분석에서 오늘의 대응 전략과 뉴스·공시·선물의 출처와 시각을 확인합니다.'],
        'terms': {'국면': '종가가 20일선 위/아래, 20일선과 60일선의 위치, 20일 수익률 ±3%로 정하는 규칙 기반 분류입니다. 예측이 아닙니다.',
                  '오늘 흐름': '오늘 지수 등락과 투자자 순매수로 정한 강세·약세·혼조입니다.',
                  '억 원': '투자자·프로그램 순매수 단위입니다. + 는 순매수, − 는 순매도.'},
        'diagrams': ['regime', 'data'],
    },
    'longterm': {
        'title': '장기투자', 'purpose': '미국 지수·테마 ETF 장기 엔진의 국면, 거장 모듈, 목표 비중, 가상 계좌와 리포트를 봅니다. 주문 화면이 아닙니다.',
        'steps': ['맨 위 상태 줄에서 오늘 국면·모드·실행 방식(가상 체결 또는 계획만)을 봅니다.',
                  '개요에서 순자산과 그림자(기본·자동) 차이, 오늘 계획을 봅니다.',
                  '시황·국면에서 확정 대기와 거시 배경(FRED·연준)을, 테마 스토리에서 위성 테마 판단을 봅니다.',
                  '차트·전략 리포트에서 타점 지도와 그날 리포트를 봅니다. 설정은 전략 탭(거장 카드)·계좌·실행에서 바꿉니다.'],
        'terms': {'확정 국면': '같은 새 국면이 정해진 거래일 수만큼 이어져야 바뀌는 국면입니다(투매는 2거래일).',
                  '그림자': '같은 자료로 다른 모듈 조합을 가상으로 굴린 순자산입니다. 기본 = 기준 비중+달리오, 자동 = 국면 추천 조합.',
                  '저수지': '금리형 ETF에 두는 대기 자금입니다. 큰 하락 때 코어로 옮깁니다.'},
        'diagrams': ['longterm', 'longterm_regimes'],
    },
    'strategy': {
        'title': '전략 구조', 'purpose': '설정·시간 기준 예상 경로와 국면별 규칙을 봅니다. 실제 실행과 판단 여부는 별도 확인이 필요합니다.',
        'steps': ['도식에서 강조된 경로가 지금 적용 중인 판단 단계입니다.', '국면별 규칙 표에서 방어형·추세형·인버스 처리가 어떻게 달라지는지 봅니다.',
                  '종가매매 조건 목록은 다음 날까지 들고 가는 예외 조건입니다.'],
        'terms': {'방어형': '데이터로 정한 목표(최근 20일 장중 상승폭 중앙값 × 국면 계수)에서 전량 익절합니다.',
                  '추세형': '상승장·강세일에 절반만 먼저 익절하고 나머지는 고점에서 일정 비율 내려올 때 팝니다.',
                  '종가매매': '장 마감 정리 때 조건을 모두 갖춘 종목만 다음 날까지 보유하는 예외입니다.'},
        'diagrams': ['entry', 'exit', 'regime'],
    },
    'etf': {
        'title': '트레이딩 현황 (모의)', 'purpose': '공유 모의계좌의 잔고·손익·보유 종목과 실행기의 매수·매도 판단을 실시간에 가깝게 봅니다.',
        'steps': ['상태 줄에서 실행기가 "실행 중"인지 먼저 확인합니다. 멈춰 있으면 손절 감시도 멈춥니다.',
                  '매매 기준 카드가 지금 적용 중인 매수·매도 규칙입니다.', '카드와 종목별 손익에서 평가손익과 손절~익절 사이 위치를 봅니다.',
                  '차트의 표식으로 Jev 판단과 실제 체결 시점을 확인하고, 아래 탭에서 관심종목·판단 근거·주문 원본을 봅니다.'],
        'terms': {'Jev 신뢰도': 'Jev가 고른 선택지에 붙이는 확신 정도(0~1)입니다. 수익 확률이 아닙니다.',
                  '체결 확인': '실행기가 증권사 조회로 확인한 체결입니다. 시각은 증권사 체결 시각이 아닌 확인 시각입니다.',
                  '관심종목 강도 진입': 'Jev가 대기할 때 관심종목의 순위·상승률·분봉·거래량을 확인합니다. 보유 종목도 추가 매수 조건과 한도를 충족하면 후보가 됩니다.'},
        'diagrams': ['ipo', 'strength', 'entry', 'exit', 'safety'],
    },
    'live_desk': {
        'title': '트레이딩 현황 (실계좌)', 'purpose': '선택한 실계좌의 실제 잔고·주문과, 메인 엔진이 이 계좌 한도로 했을 가상 매매를 함께 봅니다.',
        'steps': ['상태 줄에서 실주문 스위치(켜짐 = 보호 경로로 실제 주문, 꺼짐 = 계획만 기록)를 먼저 봅니다.', '매매 기준 카드는 그 계좌의 한도(매수 크기·1주 가격 상한·하루 손실 2만 원)와 매도 규칙입니다.',
                  '차트의 보라 원은 가상 매매, 화살표는 증권사에 실제로 체결된 주문입니다.', '아래 탭에서 가상 실행 결과·당일 주문·실행기 기록·계좌 설정을 봅니다.'],
        'terms': {'가상 실행': '엔진 판단을 분봉 종가로 체결됐다고 가정한 기록입니다. 호가·수수료·세금은 반영하지 않습니다.',
                  '국면별 청산': '켜져 있으면 모의 실행기가 그 매수에 정한 청산 계획(방어형 데이터 목표 · 추세형 절반+추적 손절)을 그대로 따르고, 하락장 약세일에는 떨어지는 종목 추가 매수를 막습니다. vibe 매수는 그 시각 시장 상황으로 방어형·추세형을 고릅니다.',
                  '관찰 기록': '켜 두면 가상 실행을 매일 쌓습니다. 실계좌 전환 판단의 근거 자료입니다.'},
        'diagrams': ['live', 'entry'],
    },
    'accounts': {
        'title': '계좌·자동매매', 'purpose': '계좌마다 자동매매 여부와 금액 한도를 정합니다. 공유 모의계좌는 Jev 트레이딩, 실계좌는 계좌마다 Jev 트레이딩(미러) 또는 장기투자를 고르고, 장기투자 가상 계좌는 장기투자 탭에서 운용합니다.',
        'steps': ['계좌 카드에서 메인 엔진과 자동매매(관찰 기록) 상태를 봅니다.', '금액 칸의 (?)에 허용 범위가 있습니다. 실계좌 한도는 보호 경로 한도를 넘을 수 없습니다.',
                  '계좌 탭마다 "제한 규칙"에서 그 계좌에 걸린 제한을 모두 봅니다. 모의계좌는 끌 수 있는 규칙을 스위치로 끄고 켭니다.',
                  'Jev 트레이딩 실계좌는 "매수 크기 기준"(1주 기준 · 금액 기준)을 고르고, 바로 아래 "이 계좌 예시" 표로 실제로 몇 주를 따라 사는지 확인합니다.'],
        'sections': [('실계좌 매수 크기 기준 · 1주 기준과 금액 기준', SIZING_GUIDE)],
        'terms': {'메인 엔진': '그 계좌의 매수·매도 판단을 맡는 엔진입니다. 모든 계좌가 Jev입니다(2026-11-17 고정).',
                  '관찰 모드': '실주문 스위치가 꺼진 실계좌는 실제 주문 없이 같은 계산으로 계획·가상 실행만 기록합니다. 켜진 계좌는 보호 경로로 실제 주문합니다.',
                  '제한 규칙': '실행기가 매수를 막거나 줄이는 규칙입니다(재진입 제한·연속 손실 쉬기·물타기 금지 등). 끄기 가능 / 다른 설정으로 조절 / 항상 적용으로 나뉩니다.'},
        'diagrams': ['engines', 'limits', 'live', 'data'],
    },
    'compare': {
        'title': '성과 비교', 'purpose': 'Jev 트레이딩(국내 장중)과 장기투자(미국 지수·테마 ETF)의 전략과 성과를 같은 날짜 위에 놓고 비교합니다. 날짜·장부별 기록은 compare_daily 표에 쌓여 다른 분석에도 씁니다.',
        'steps': ['위에서 기간(이번 주 · 이번 달 · 전체 · 직접)을 고르고, 필요하면 "그림자 장부"를 켜 장기투자의 비교 기준을 겹쳐 봅니다.',
                  '카드에서 두 방식의 손익·수익률·최대 낙폭·이긴 날과 두 방식의 상관을 봅니다. 표본이 20일보다 적으면 "판단하기 이름"이 붙습니다.',
                  '"성과 곡선"은 누적 수익률과 KODEX 200 · 나스닥100, 아래 띠는 그날의 국내·미국 국면입니다.',
                  '"국면별 성적"에서 어떤 장에서 어느 방식이 나았는지, "두 방식 관계"에서 함께 들었을 때의 흔들림을 봅니다.',
                  '"방식 한눈에"는 판단 주기·대상·매수·매도·계좌를 줄마다 마주 놓은 비교 카드입니다. "일별 기록"은 CSV로 내려받습니다.'],
        'terms': {'Jev 수익률': '(실현 + 평가손익 변화 − 수수료·세금 추정) ÷ 그날 최대 투입금입니다. 모의계좌 예수금은 실제로 쓰는 돈보다 훨씬 커서 기준으로 쓰지 않습니다.',
                  '장기투자 수익률': '(순자산 변화 − 그날 넣은 적립금) ÷ (전날 순자산 + 적립금)입니다. 수수료는 순자산에 이미 들어 있습니다.',
                  '기본 그림자': '기준 비중+달리오만 쓰는 장기투자 비교 기준입니다. 자동 그림자는 국면 추천 조합입니다.',
                  '상관': '두 방식 일별 수익률이 같이 움직이는 정도(−1~+1)입니다. 낮을수록 함께 들 때 흔들림이 줄어듭니다.'},
        'diagrams': ['engines'],
    },
    'ai': {
        'title': 'AI·실행 기록', 'purpose': '날짜별로 Jev 엔진의 판단·실행기 활동·체결과 장기투자 결정을 되짚고, 실계좌는 미러 실행기가 그 판단을 어떻게 실행했는지 봅니다.',
        'steps': ['운영 탭에서 "AI·실행 기록"을 고른 뒤, 위쪽 "기록"에서 종합 · Jev 엔진 · 장기투자 결정 · 실계좌 실행을 고릅니다. 종합은 모든 엔진·실계좌의 판단·주문·체결·계획을 한 날짜로 모읍니다. 실계좌는 자체 판단이 없어 따라가는 엔진의 기록이 판단 근거입니다.',
                  '기록 날짜와 차트 종목을 고릅니다.', '차트의 색 표식(파랑 Decision · 보라 Jev · 초록 매수 · 빨강 매도)으로 판단과 체결 시점을 봅니다.',
                  'Jev 신뢰도 추이와 실행기 활동(10분 단위)에서 왜 대기했는지 확인합니다.'],
        'terms': {'진입 조건 허용/불허': '그 주기에 매수 조건을 통과했는지입니다. 불허 사유가 함께 표시됩니다.',
                  '국면 관찰': '연구용 기록입니다. 매매 신호가 아닙니다.'},
        'diagrams': ['entry', 'exit'],
    },
    'live': {
        'title': '실계좌', 'purpose': '실계좌 자동매매가 지금 어떤 상태인지, 어떤 보호 한도로 돌아가는지, 실제 잔고·주문이 어떤지를 한 곳에서 봅니다.',
        'steps': ['맨 위 상태 카드 4개로 실행기 · 신규 매수 · 주문 보호 · 마지막 기록을 확인합니다.',
                  '보호 한도 카드는 실주문에 항상 적용되는 한도입니다(운용금 · 하루 손실 · 1회 매수 수량 · 가격 · 손절).',
                  '"주문·정리 종합" 보기는 모든 계좌(모의 포함)의 보유·오늘 체결·정리 요청·장기투자 주문 계획을 한 번에 보여 줍니다(예전 주문·정리 탭).',
                  '계좌 이름을 고르면 아래 "주문·정리" 탭에서 그 계좌의 증권사 잔고·주문, 미러 주문·실체결 기록을 보고 전량 정리를 요청합니다. 정리 요청은 미러 실행기가 산 수량만 팝니다.',
                  '"잔고·주문" 탭에서 증권사 조회 결과를 봅니다. 조회하기 전 값은 과거 상태입니다.',
                  '"자동매매 제어" 탭의 중단은 신규 매수만 멈춥니다. 보유분 손절 감시는 계속합니다.'],
        'terms': {'관찰 모드': '실주문 스위치가 꺼진 실계좌는 실제 주문 없이 같은 계산으로 계획·가상 실행만 기록합니다. 켜진 계좌는 보호 경로로 실제 주문합니다.',
                  '주문 보호(HALT)': '전역 중단 표시입니다. 켜져 있으면 실주문 경로가 막힙니다.',
                  '하루 손실 한도': '실주문 직전에 손익을 다시 조회해 2만 원 손실에 닿으면 그날 신규 매수를 막고 정리합니다. 급변 시 초과 손실이 날 수 있습니다.'},
        'diagrams': ['live'],
    },
    'ops': {
        'title': '운영 → 운영 상태', 'purpose': '운영 탭은 운영 상태 · 자동 작업 · AI·실행 기록 · API 연동 · 스킬 · 리포트 발행 · 백업·이식을 위쪽 버튼으로 고릅니다. 운영 상태는 자동 작업(실행기·학습기·시장 수집 등)이 돌고 있는지, 오늘 실행기가 무엇을 했고 왜 기다렸는지 봅니다.',
        'steps': ['서비스 상태 배지에서 "실행 중"이 아닌 항목을 먼저 봅니다.', '오늘 활동 차트에서 판단 횟수와 대기 사유를 봅니다.',
                  '학습 데이터 누적량은 학습 필터를 쓸 수 있는지(표본 20건 이상) 보여줍니다.'],
        'terms': {'미등록': '자동 작업이 시스템에 등록되지 않은 상태입니다. 재부팅 뒤에도 자동으로 올라오는지 확인하세요.',
                  '대기 사유': 'Jev 신뢰도 부족, 지수 방향 불일치, 분봉 조회 실패 등 그 주기에 사지 않은 이유입니다.'},
        'diagrams': ['safety', 'data'],
    },
    'schedule': {
        'title': '운영 → 자동 작업', 'purpose': '정해진 시각에 도는 모든 작업(launchd 서비스 · Claude 예약 작업 · Codex 자동화)을 한곳에서 시각 순으로 봅니다.',
        'steps': ['위 카드에서 상시 서비스가 모두 실행 중인지, Claude 예약 작업이 몇 개 켜져 있는지, 다음에 시작할 작업이 무엇인지 봅니다.',
                  '오른쪽 위 날짜를 바꾸면 그날 시작하는 작업이 시각 막대와 표로 나옵니다. 5분·1시간 간격으로 여러 번 도는 작업은 한 줄로 묶습니다.',
                  '"전체 목록"에서 구분(launchd · Claude · Codex)을 골라 언제·상태·하는 일·권한 모드를 봅니다.'],
        'terms': {'상시 서비스': '로그인하면 시작해 계속 도는 launchd 서비스입니다(실행기·관찰기·대시보드·알림).',
                  '시간 서비스': '정한 시각에 한 번 돌고 끝나는 launchd 서비스입니다. 끝나면 "종료"가 정상입니다.',
                  'Claude 예약 작업': 'Claude 앱이 정한 시각에 여는 작업입니다. 앱이 닫혀 있으면 다음에 열 때 실행됩니다. 끝난 한 번 작업은 "꺼짐"입니다.',
                  'Codex 자동화': '같은 일을 Claude 예약 작업으로 옮겨 일시 정지해 둔 Codex 작업입니다. 둘 다 켜면 두 번 돌아갑니다.'},
        'diagrams': [],
    },
    'api': {
        'title': 'API 연동', 'purpose': '외부 API(NH·키움·토스·네이버·KRX·OpenDART·뉴스·Yahoo·AI)의 연동 상태, 언제 얼마나 호출하는지, 실패 이력과 데이터가 쓰이는 화면을 봅니다.',
        'steps': ['위 카드와 "API별 연동 상태" 표에서 실패·주의·키 없음을 먼저 봅니다.', '"API 상세"에서 API를 골라 엔드포인트별 호출·실패 이력·산출물 갱신 시각을 봅니다.',
                  '"화면별 데이터 출처"에서 화면을 고르면 그 화면의 숫자가 어느 API에서 오는지 나옵니다.'],
        'terms': {'응답 내용 오류': 'HTTP는 성공했지만 응답 안의 결과 코드가 오류인 경우입니다(예: 키움 return_code).',
                  '호출 없음': '오늘 그 API를 부른 기록이 없습니다. 장 시간 밖이거나 서비스가 아직 새 코드로 재시작하지 않았을 수 있습니다.',
                  '산출물': 'API 데이터를 받아 쓴 파일입니다. 갱신 시각으로 마지막 성공을 짐작할 수 있습니다.'},
        'diagrams': ['data'],
    },
}

TOPICS = {'engines': 'Jev · 장기투자', 'longterm': '장기투자', 'entry': '매수 전략', 'strength': '관심종목 진입', 'oversold': '낙폭과대 반등', 'ipo': '신규주 진입', 'exit': '매도 전략', 'regime': '시장 국면', 'limits': '제한 규칙', 'safety': '안전장치', 'live': '실계좌', 'data': '데이터 흐름'}


def limits_text(v):
    from restrictions import GROUPS, KIND_LABELS, RULES
    lines = ['실행기가 매수를 **막거나 줄이는 규칙** 전체입니다. **끄기 가능** 규칙은 계좌·자동매매 → 모의계좌 → **제한 규칙** 탭의 스위치로 끄고 켜며, '
             '실행기가 다음 주기(약 1분)부터 따릅니다. **다른 설정으로 조절** 규칙은 매매 기준이나 금액·주문 보호 한도의 숫자로 넓힙니다. '
             '**항상 적용** 규칙은 안전·증권사 규칙이거나 별도 승인이 필요한 값입니다.']
    for group in GROUPS:
        lines.append(f'\n**{group}**')
        for _, g, name, kind, key, what, why in RULES:
            if g == group:
                state = ('켜짐' if v.get(key, 1) else '**꺼짐**') if kind == 'switch' else KIND_LABELS[kind]
                lines.append(f'- **{name}** ({state}) — {what} {why}')
    lines.append('\n실계좌는 엔진 체결 원장을 따라가므로 위 규칙이 미러에도 반영되고, 그 위에 계좌별 보호 한도(운용금·하루 손실·1주 가격 상한·회당 한도·새 종목·손절)가 더 붙습니다. '
                 '실계좌 탭마다 "제한 규칙"에 정리돼 있습니다.')
    return '\n'.join(lines)
def live_text():
    """The live accounts as they are now (2026-11-19: the old text still said 관찰 모드 · 인버스 전용 · 운용금 10억)."""
    from accounts import ENGINE_LABELS, load
    lives = [r for r in load().values() if r.get('kind') == 'live']
    now = ' · '.join(f"{r.get('label', '실계좌')} {ENGINE_LABELS.get(r.get('engine'), r.get('engine'))}"
                     f"({'실주문 켜짐' if r.get('live_orders') else '계획만'})" for r in lives) or '등록된 실계좌 없음'
    return (f'실계좌는 계좌마다 운용 방식(**Jev 트레이딩** = 모의 체결 미러, **장기투자** = 장기 엔진 계획)을 고르고, **실주문 스위치**가 켜진 계좌만 '
            f'보호 경로(nh_guarded_live)로 실제 주문을 냅니다. 꺼진 계좌는 같은 계산으로 계획만 기록합니다.\n\n'
            f'**지금**: {now}.\n\n'
            f"모든 계좌에 하루 손실 최대 {LIVE_LIMITS['daily_loss_krw']:,}원, 한 번 주문 {LIVE_LIMITS['order_qty']:,}주 이하, "
            f"1주 가격 상한(계좌별, 최대 {LIVE_LIMITS['order_price_max_krw']:,}원), 하루 새 종목 5개, 미체결·미확인 주문 차단이 걸립니다. "
            '운용금은 계좌 잔고 안에서 계좌마다 정합니다.\n\n'
            '**바꾸는 곳**: 계좌·자동매매 → 실계좌 탭(운용 방식·금액 한도·매수 크기·실주문 스위치).')


TOPIC_TEXT = {
    'longterm': lambda v: (__import__('longterm_flow').summary(__import__('longterm_flow').state(live={})) + '\n\n'
                           '매일 아침 미국 종가로 **원시 국면**을 정하고, 같은 새 국면이 확정 일수만큼 이어질 때만 **확정 국면**을 바꿉니다. '
                           '켜진 **거장 모듈**이 차례로 목표 비중을 조정하고(린치 스토리는 LLM 판단), 목표가 밴드 밖일 때만 하루·월 상한 안에서 주문을 냅니다. '
                           '손절은 없고, 기본 그림자(기준 비중+달리오)와 자동 그림자(국면 추천 조합)를 같이 기록해 비교합니다. FRED 금리·신용과 발표 일정은 참고 자료이며 규칙에 쓰지 않습니다.\n\n'
                           '**도식 전체**: 전략 구조 → 맨 위 "장기투자" 전환 (아키텍처 · 하루 알고리즘 · 국면 전이). **바꾸는 곳**: 장기투자 탭 → 전략(거장 카드·집행 속도) · 시장(국면 확정 일수) · 계좌·실행(가상 체결).'),
    'ipo': lambda v: ("**신규주**(새로 상장한 종목)는 상장 직후 며칠 동안 거래가 몰려 짧은 시간에 크게 움직입니다. 지수와 상관없이 움직이는 경우가 많아 "
                      "**하락장에서도 수익을 낼 수 있는 수단**입니다. 대신 하루 −40%~+300%까지 움직일 수 있어 위험도 큽니다.\n\n"
                      f"실행기는 KRX KIND의 신규상장 목록을 하루 한 번 받아 NH 시세의 상장일로 다시 확인한 뒤, 상장 후 **{v['ipo_days']}거래일** 안의 종목을 "
                      f"{v['ipo_start']}부터 봅니다(장 시작 직후의 큰 흔들림은 피함). **기준가 위**(상장일은 공모가) · **시가 위** · **고가 {v['ipo_near']:g}% 안** · "
                      f"**1분봉 상승과 거래량 {v['volume_ratio']:g}배**를 모두 만족할 때만 분할 1회 매수 금액의 **{v['ipo_size']:g}%** 크기로 삽니다. 신규주는 {_ipo_hold(v)}까지 삽니다.\n\n"
                      f"청산은 **신규주형**: −{v['ipo_stop']:g}% 손절, +{v['ipo_half']:g}%에서 절반 익절, 나머지는 고점 대비 −{v['ipo_trail']:g}% 또는 본전 아래에서 매도{_ipo_time_stop(v).replace(' · ', ', ')}, "
                      f"{v['flatten']} 당일 정리입니다. 떨어질 때 더 사지 않고(물타기 없음), 다음 날로 넘기지 않습니다.\n\n"
                      "**바꾸는 곳**: 전략·AI 설정의 \"신규주 진입 · 빠른 선택\" — 보수적(당일만·30%·손절 2%), 기본(2거래일·50%·손절 3%), "
                      "적극적(3거래일·70%·손절 4%·절반 익절 5%), 끄기. 여섯 값을 하나씩 직접 바꿀 수도 있습니다."),
    'strength': lambda v: (f"Jev가 7개 ETF 중 어느 것도 확신하지 못해 대기할 때 쓰는 두 번째 진입 길입니다. 30분마다 Jev가 관심종목 후보를 비교해 **확률**을 매기는데, "
                           "이 확률은 후보끼리 나눠 갖는 **상대 점수**라서 모두 더하면 1입니다. 후보가 10개면 평균이 0.1이고, 0.6 같은 높은 기준은 한 종목만 넘을 수 있습니다(두 종목이 0.6이면 합이 1을 넘음). "
                           f"그래서 **순위 상위 {v['strength_top']}위 안**이면서 **확률 {v['strength_min']:g} 이상**(근거 없는 꼴찌는 빼는 바닥값)인 종목을 후보로 보고, "
                           f"보유 종목도 추가 매수 조건과 한도를 확인합니다: 전일 대비 +{v['strength_lo']:g}~{v['strength_hi']:g}%, 최근 1분봉 상승, 거래량 {v['volume_ratio']:g}배. "
                           "모두 통과한 종목 하나를 그 분에 사고, 다음 분에는 다른 종목을 다시 봅니다. 확률은 수익 확률이 아닙니다.\n\n"
                           + (f"**전략 슬롯**이 켜져 있어 Jev가 ETF를 고른 회차에도 Jev 판단보다 먼저 확인합니다(동시 {int(v['strength_slot_max'])}종목 · "
                              f"하루 {int(v['strength_slot_daily'])}종목, 인버스 보유 중에는 대기).\n\n" if v.get('strength_slot_on') else '')
                           + "**바꾸는 곳**: 계좌·자동매매 → 모의계좌 → 매매 기준 전체(또는 트레이딩 현황 → 전략·AI 설정)의 \"관심종목 강도 진입 · 빠른 선택\". "
                           "**보수적**(상위 1위 · 0.30 · +3~20%: 가장 유력한 한 종목만), **기본**(상위 3위 · 0.15 · +2~25%), "
                           "**적극적**(상위 5위 · 0.10 · +1~29%: 기회는 넓지만 근거 약한 종목도 섞임), **끄기**(Jev가 직접 고를 때만 개별주) 중에서 고르거나 네 값을 직접 바꿉니다. "
                           "하락장처럼 Jev가 자주 대기하는 날일수록 이 규칙이 거래 대부분을 결정합니다."),
    'engines': lambda v: ('**Jev**는 1분 주기로 차트 이미지를 보고 후보 하나를 신뢰도와 함께 고르는 장중 이미지 모델이며, 공유 모의계좌와 실계좌 미러의 메인 엔진입니다. '
                          '장전에 준비를 마치고 09:00 개장부터 주문 시간 조건을 확인합니다. **장기투자(vibe)**는 하루 한 번 나스닥100 낙폭·200일선·VIX로 시황 국면을 정하고, '
                          '거장 모듈(기준 비중·달리오·여운봉·막스·윌리엄스·린치·버핏) 조합으로 미국 지수·테마 ETF의 목표 비중을 정해 별도 가상 계좌에서 운용합니다. '
                          '두 엔진은 서로 주문에 관여하지 않으며, 장기투자는 Jev 자료를 하루 요약으로만 참고합니다. 장전 자료는 과거 참고 정보이며 주문 신호가 아닙니다.'),
    'entry': lambda v: (f"실행기는 1분마다 7개 ETF와 오늘 관심종목의 분봉을 읽고 Jev에게 묻습니다. Jev가 한 종목을 신뢰도 **{v['entry_confidence']:.2f} 이상**으로 고르면 "
                        f"지수 방향과 거래량(최근 5분이 그 전 5분의 **{v['volume_ratio']:g}배** 이상)으로 한 번 더 확인합니다. Jev가 대기하면 관심종목 중 "
                        f"Jev 관심종목 선정 **상위 {v['strength_top']}위 안(확률 {v['strength_min']:g} 이상)**, 전일 대비 **+{v['strength_lo']:g}~{v['strength_hi']:g}%**, 분봉 상승, 거래량 조건을 모두 갖춘 종목을 삽니다. "
                        f"한 번에 약 **{v['tranche'] / 1e4:,.0f}만 원**이며 **{v['cutoff']}** 이후에는 새로 사지 않습니다."),
    'exit': lambda v: (f"일반 전략의 기본 손절은 평균 매입가 **−{v['stop_loss_pct']:g}%**이며 시황이 포지션에 불리하면 −1%로 강화합니다(롱은 횡보장·하락장, 인버스는 상승장·횡보장). 신규주와 낙폭과대 전용 손절은 별도입니다. 매수할 때 시장 상황으로 청산 방식을 정합니다. "
                       f"방어형은 데이터 목표(**{v['target_lo']:g}~{v['target_hi']:g}%**)에서 전량, 추세형은 **+{v['trend_half_pct']:g}%**에서 절반을 판 뒤 고점 대비 **−{v['trend_trail_pct']:g}%**에서 나머지를 팝니다. "
                       f"**{v['flatten']}**에 당일 정리하며, 종가매매 조건 {v['overnight_rules']}개를 모두 갖춘 종목만 다음 날까지 보유합니다."),
    'limits': limits_text,
    'oversold': lambda v: ("**선별 v2 낙주매매 (먼저 확인)**: 시장 전체가 폭락한 날(지수 저가 "
                           f"−{v['oversold_v2_index_drop_pct']:g}% 이상, 또는 직전 5일 −{v['oversold_v2_prior5_pct']:g}% 뒤 −{v['oversold_v2_capit_drop_pct']:g}% 이상, "
                           "전날 나스닥 하락) 감시 종목 중 시장과 같이 빠진 종목을 Jev가 모든 데이터로 검토해 분명한 악재가 없으면 "
                           f"장 막판에 절반 사고, 다음 날 더 싸면 나머지 절반을 삽니다. **+{v['oversold_v2_half_tp_pct']:g}%** 절반, "
                           f"**+{v['oversold_v2_tp_pct']:g}%** 나머지, **{int(v['oversold_v2_hold_days'])}거래일**째 정리, 평균가 **−{v['oversold_v2_stop_pct']:g}%** 손절 뒤 저점이 {int(v['oversold_v2_reentry_hold_min'])}분 버티고 +{v['oversold_v2_reentry_bounce_pct']:g}% 반등하면 다시 판단해 재매수(최대 {int(v['oversold_v2_reentry_max'])}번). "
                           "근거는 과거 폭락일 재현(docs/oversold-case-study.md 2-1·2-2절)이며 아래 도식에 지금 설정값으로 전체 흐름이 있습니다.\n\n"
                           "**낙폭과대 반등**은 실적과 애널리스트 평가가 탄탄한 종목·지수 ETF가 미국 증시 급락 같은 시장 충격으로 평소보다 훨씬 크게 빠졌을 때, "
                           "하락이 멈춘 것을 확인하고 사서 낙폭 일부가 회복되면 바로 파는 매매입니다(사용자 요청 11/13).\n\n"
                           f"실행기는 감시 종목을 매분 확인해 오늘 저가가 전일 종가보다 개별주 **−{v['oversold_stock_drop_pct']:g}%**, ETF **−{v['oversold_etf_drop_pct']:g}%** 이상이고 "
                           f"평소 하루 변동폭(최근 20일 ATR)의 **{v['oversold_atr_mult']:g}배** 이상 빠졌는지 봅니다. 그다음 **{int(v['oversold_calm_min'])}분** 동안 저가를 새로 쓰지 않고 "
                           f"저가보다 **{v['oversold_rebound_pct']:g}%** 올라오면 하락이 멈춘 것으로 봅니다. 마지막으로 Jev가 차트·시황·뉴스·투자자 수급·애널리스트 컨센서스와 최근 리포트를 보고 "
                           f"BUY를 **{v['oversold_jev_min']:.2f}** 이상으로 골라야 삽니다. 회사 고유의 악재나 목표가 하향이 원인이면 사지 않습니다.\n\n"
                           f"크기는 분할 1회의 **{v['oversold_size_pct']:g}%**이고, **+{v['oversold_tp_pct']:g}%** 또는 낙폭의 **{v['oversold_recover_pct']:g}%** 회복 중 먼저 닿는 값에서 전량 팝니다. "
                           f"당일 저가보다 **{v['oversold_stop_pct']:g}%** 더 빠지면 손절하고, 목표에 못 닿으면 **{int(v['oversold_hold_days'])}거래일**까지 들고 갑니다. 추가 매수는 하지 않습니다.\n\n"
                           "**근거**: scripts/oversold_research.py로 2년 일봉과 60일 5분봉(삼성전자·SK하이닉스·알테오젠·KODEX 200·레버리지·반도체레버리지)을 본 결과, "
                           "깊게 빠진 개별주와 KODEX 200에서만 약한 반등 우위가 있었고 2배 ETF는 없어 기본으로 뺐습니다. 반등은 대부분 1~3일에 걸쳐 나왔습니다. "
                           "11/6 삼성전자(나스닥 −5.5% 다음 날 −11.1%)는 이 규칙이면 목표가에 닿았습니다. 표본이 작아 수익을 보장하지 않습니다.\n\n"
                           "**바꾸는 곳**: 계좌·자동매매 → 모의계좌 → 매매 기준의 \"낙폭과대 반등 · 빠른 선택\"과 감시 종목. 오늘 발동 기록은 전략 구조 탭의 \"낙폭과대 감시\"."),
    'regime': lambda v: '국면은 지수 종가와 20·60일 이동평균, 20일 수익률로 정한 규칙 기반 분류입니다. 하락장일수록 목표를 낮춰 빨리 익절하고, 롱 진입에는 지수 확인을 더 엄격히 요구합니다.',
    'safety': lambda v: '모의계좌 주문은 자동 실행기 한 곳에서만 실행됩니다. 매 주기 증권사와 원장을 대조하고, 주문 가드가 금액·종목 수·미확인 주문을 막습니다. 대시보드는 주문을 내지 않습니다.',
    'live': lambda v: live_text(),
    'data': lambda v: '시세·잔고는 NH 나무 PLUG, 시장 상황은 네이버 금융·Yahoo에서 받습니다. 오늘의 장전 자료는 시각·출처를 확인해 Jev 판단과 장기투자 스토리 점검에 과거 맥락으로 제공하며, 현재 호가나 주문 신호를 대신하지 않습니다. 판단과 주문은 기록·원장으로 모입니다. Jev 신뢰도는 수익 확률이 아닙니다.',
}


def md(text):
    """Markdown-safe guide text: two "~" in one paragraph (ranges like +2~25%) would render as strikethrough."""
    return text.replace('~', '\\~')


def topic(key, v):
    return md(TOPIC_TEXT[key](v))


CALLOUTS = {'근거': ('info', '근거'), '바꾸는 곳': ('ok', '바꾸는 곳'), '도식 전체': ('info', '도식 전체'), '지금': ('warn', '지금')}


def topic_view(key, v):
    """A topic in the guide template (2026-11-19): the first paragraph as the summary card, 근거 · 바꾸는 곳 · 도식 전체 as
    labelled callouts, the rest as paragraphs, instead of one wall of markdown."""
    import guide_ui as GU
    GU.style()
    if key == 'limits':
        return limits_view(v)
    for i, text in enumerate(p for p in TOPIC_TEXT[key](v).split('\n\n') if p.strip()):  # GU escapes; no md()
        found = re.match(r'\*\*([^*]+)\*\*\s*[:：]\s*', text)
        if found and found.group(1) in CALLOUTS:
            tone, label = CALLOUTS[found.group(1)]
            GU.note(text[found.end():], tone, label)
        elif i == 0:
            GU.lead(text)
        else:
            GU.para(text)


def limits_view(v):
    """제한 규칙 as status cards by group (2026-11-19: the bullet list was hard to read)."""
    import guide_ui as GU
    from restrictions import GROUPS, KIND_LABELS, RULES

    def state(kind, key):
        if kind == 'switch':
            return ('on', '켜짐') if v.get(key, 1) else ('off', '꺼짐')
        return ('fix', KIND_LABELS[kind]) if kind == 'fixed' else ('set', KIND_LABELS[kind])
    cards = [(g, name, *state(kind, key), what, why) for _, g, name, kind, key, what, why in RULES]
    counts = {k: sum(1 for c in cards if c[2] == k) for k in GU.BADGES}
    GU.lead(f'실행기가 매수를 **막거나 줄이는 규칙 {len(cards)}개**입니다. **켜짐·꺼짐**은 계좌·자동매매 → 모의계좌 → 제한 규칙 탭의 스위치로 '
            '바꾸고 실행기가 다음 주기(약 1분)부터 따릅니다. **설정값**은 매매 기준·금액 한도의 숫자로 넓히고, **항상 적용**은 안전·증권사 규칙이거나 '
            '별도 승인이 필요한 값입니다.', '제한 규칙 한눈에')
    GU.legend(counts)
    for group in GROUPS:
        items = [c[1:] for c in cards if c[0] == group]
        if items:
            GU.section(group, f'{len(items)}개')
            GU.rules(items)
    GU.note('실계좌는 엔진 체결 원장을 따라가므로 위 규칙이 미러에도 반영되고, 그 위에 계좌별 보호 한도(운용금·하루 손실·1주 가격 상한·회당 한도·'
            '새 종목·손절)가 더 붙습니다. 실계좌 탭마다 "제한 규칙"에 정리돼 있습니다.', 'info', '실계좌')


def diagram(key, v):
    """A guide diagram: Archify for the step-by-step flows (FLOWS), Graphviz for the rest (comparisons, rule grids)."""
    import guide_ui as GU
    if key == 'limits':  # 27 boxes shrank to unreadable in one picture (2026-11-19): the rule cards are the overview
        return limits_view(v)
    if key == 'engines':  # a comparison reads best as aligned rows, not a picture (2026-11-19)
        return engines_view(v)
    title, build = DIAGRAMS[key]
    GU.section(title)
    if key in FLOWS:
        GU.flow(f'jev-{key}', FLOWS[key](v), build(v))
    else:
        GU.diagram(build(v))


@dialogs.dialog('가이드 센터')
def guide_dialog(key=None):
    """All guides in one place; opens on the given screen."""
    import guide_ui as GU
    dialogs.wide()
    GU.style()
    v = values()
    keys = list(GUIDES)
    screen = st.selectbox('화면', keys, index=keys.index(key) if key in GUIDES else 0, format_func=lambda k: GUIDES[k]['title'],
                          key='guide_screen')
    tabs = st.tabs(['이 화면', *TOPICS.values()])
    with tabs[0]:
        guide = GUIDES[screen]
        GU.lead(guide['purpose'], '이 화면은')
        GU.section('읽는 순서')
        GU.steps(guide['steps'])
        if guide.get('terms'):
            GU.section('용어')
            GU.table(list(guide['terms'].items()), head=('용어', '뜻'))
        for title, rows in guide.get('sections', ()):
            GU.section(title)
            GU.table(rows, head=('항목', '설명'))
        for name in guide['diagrams']:
            diagram(name, v)
    for tab, name in zip(tabs[1:], TOPICS):
        with tab:
            topic_view(name, v)
            if name != 'limits':  # topic_view already drew the rule cards
                diagram(name, v)
    GU.note('숫자는 지금 저장된 설정과 실행기 값입니다. 가격은 1분마다 확인하므로 체결가가 정확히 기준가라는 보장은 없고, 수수료·세금은 반영하지 않습니다.')


def render_text(guide):
    st.markdown(md(f"**이 화면은** — {guide['purpose']}"))
    st.markdown(md('**읽는 순서**\n' + '\n'.join(f'{i}. {step}' for i, step in enumerate(guide['steps'], 1))))
    if guide.get('terms'):
        st.markdown(md('**용어**\n' + '\n'.join(f'- **{term}**: {text}' for term, text in guide['terms'].items())))
    for title, rows in guide.get('sections', ()):
        st.markdown(md(f'**{title}**\n' + '\n'.join(f'- **{name}**: {text}' for name, text in rows)))


def help_row(key):
    """First row of a tab: the screen's purpose on the left, its guide on the right (same place on every tab)."""
    import ask_ai
    guide = GUIDES[key]
    left, middle, right = st.columns([6, 1.2, 1.2], vertical_alignment='center')
    left.caption(guide['purpose'])
    with middle.popover('화면 안내', icon=':material/help:', width='stretch', help='이 화면이 보여주는 것과 읽는 법'):
        render_text(guide)
        ask_ai.guide_questions(key)
    # outside the popover: a dialog opened from inside it would sit under the still-open popover
    if right.button('가이드 센터', key=f'guide_open_{key}', icon=':material/menu_book:', width='stretch',
                    help='이 화면의 읽는 순서·용어·설명과 매수·매도 전략, 제한 규칙, 안전장치 도식을 한 팝업에서 봅니다.'):
        guide_dialog(key)
