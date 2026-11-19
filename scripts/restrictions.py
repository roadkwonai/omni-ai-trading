"""Every restriction the shared paper runner applies, in one list for the dashboard's 제한 규칙 view and the guide.

kind: 'switch' = a strategy setting (1 on / 0 off) the user can turn off on the dashboard;
      'value'  = a time or count set on the same tab;
      'setting' = a number elsewhere on the dashboard (0 or a wider value relaxes it);
      'fixed'   = always on here (a safety rule, a broker rule, or a value that needs its own approval).
The runner reads the switches through strategy_settings; this file only describes them.
"""
from __future__ import annotations

from tunables import setting

# shortcut: read once at import, so a 운영 상태 change shows after the dashboard restarts; move to a function if that matters.
_READ_FAILS, _HELD_MAX = setting('read_failure_limit'), setting('paper_max_total_qty')

SWITCHES = ('reentry_allow', 'careful_reentry_on', 'reentry_guard', 'losing_streak_guard', 'averaging_down_guard', 'opposite_guard', 'direction_confirm',
            'rising_check', 'ipo_add_guard')
VALUES = ('entry_cutoff_hhmm', 'flatten_hhmm', 'ipo_max_positions', 'careful_reentry_vwap_pct', 'careful_reentry_range_pct',
          'careful_reentry_conf', 'careful_reentry_size_pct')  # kind 'value': set on the 제한 규칙 tab itself
EDITABLE = SWITCHES + VALUES

RULES = [
    # (id, group, name, kind, setting key or None, what it does, why it exists / what turning it off means)
    ('reentry_allow', '진입', '같은 날 재진입 허용', 'switch', 'reentry_allow',
     '켜면 그날 사고판 종목도 진입 조건을 다시 모두 통과하면 다시 삽니다. 관심종목 강도 경로의 재진입은 종목 강도에 더해 지수 방향(시황)도 확인합니다.',
     '끄면 그날 거래를 마친 종목은 그날 어떤 일반 진입 경로로도 다시 사지 않습니다(낙폭과대 선별 v2는 자기 규칙). 사용자 요청 2026-11-17.'),
    ('careful_reentry', '진입', '신중 재진입 (불리한 국면)', 'switch', 'careful_reentry_on',
     '그날 사고판 종목을 국면이 그 포지션에 불리할 때(롱은 횡보장·하락장, 인버스는 상승장·횡보장) 다시 사면 네 가지를 모두 봅니다: '
     '추격 금지(VWAP보다 설정 % 넘게 높거나 오늘 범위 상단이면 안 삼), 약세 받기 금지(VWAP 아래면 안 삼), Jev 재진입 재검토'
     '(그날 이 종목의 매매 이력·당일 흐름·뉴스·시황을 보고 BUY 신뢰도 기준 이상), 크기 축소. 국면이 유리하면 평소처럼 삽니다.',
     '11/13 036540 재진입 2건(VWAP 아래)과 11/18 043260 재진입(VWAP +2.4%)이 2~6분 만에 손절됐습니다(−37,346원). 사용자 2026-11-18: '
     '재진입은 허용하되 횡보·하락장에서는 신중하게, 추격하지 않게. 끄면 재진입은 처음 진입과 같은 조건만 봅니다.'),
    ('careful_values', '진입', '신중 재진입 기준값', 'value', 'careful_reentry_vwap_pct',
     'VWAP 대비 최대 %, 오늘 범위 위치 상한 %, Jev 재검토 매수 신뢰도, 매수 크기(평소 대비 %)입니다.',
     '기본 2% · 85% · 0.6 · 50%. 표본이 5건이라 잠정 값이며 결과를 보며 조정합니다.'),
    ('reentry', '진입', '같은 날 재진입 제한', 'switch', 'reentry_guard',
     '그날 판 종목(익절·손절·추적 손절 모두)은 관심종목 강도 경로로 다시 살 때 직전 매도가보다 비쌀 때만 삽니다.',
     '11/13 SFA반도체를 매도가 아래에서 두 번 다시 사서 −31,100원이 났고, 익절한 원익QnC도 매도가 아래에서 5번 다시 사려 했습니다'
     '(사용자 승인 11/13, 16:15 개선안 2번으로 익절까지 넓힘). 끄면 식어 가는 종목도 다시 삽니다.'),
    ('streak', '진입', '연속 손실 쉬기', 'switch', 'losing_streak_guard',
     '오늘 왕복 거래가 3번 연속 손실이면 마지막 청산 뒤 30분 동안 새로 사지 않습니다.',
     '나쁜 장에서 연달아 들어가는 것을 막습니다. 끄면 손실 직후에도 바로 다시 삽니다.'),
    ('direction', '진입', '지수 방향 확인 (Jev 경로)', 'switch', 'direction_confirm',
     'Jev가 고른 종목은 그 지수가 같은 방향(롱은 강세·상승, 인버스는 약세·하락)일 때만 삽니다. 시장 요약이 없으면 사지 않습니다.',
     'Jev 신뢰도 0.5를 데이터로 한 번 더 확인하는 규칙입니다(11/11 승인). 끄면 거래량·분봉 확인만 남습니다.'),
    ('opposite', '진입', '반대 방향 동시 보유 차단', 'switch', 'opposite_guard',
     '롱 종목을 보유 중이면 인버스를, 인버스를 보유 중이면 롱을 새로 사지 않습니다.',
     '롱과 인버스는 서로 상쇄돼(1분 상관 −0.4~−0.85) 비용만 냅니다. 끄면 동시 보유가 가능하고, 방향 전환(손실 보유 정리)도 일어나지 않습니다.'),
    ('volume', '진입', '거래량 증가 확인', 'setting', 'entry_volume_ratio',
     '최근 5분 거래량이 그 전 5분의 설정 배수 이상일 때만 삽니다(모든 진입 경로).',
     '매수 기준의 "진입 거래량 배수"를 0으로 하면 끕니다.'),
    ('rising', '진입', '최근 분봉 상승 확인', 'switch', 'rising_check',
     '후보의 현재가가 5분 전보다 높을 때만 삽니다(Jev·관심종목 강도·신규주 모든 진입 경로).',
     '떨어지는 중에 사지 않게 하는 기본 조건입니다(사용자 2026-11-13: 끌 수 있게). 끄면 거래량·지수 조건만으로 삽니다.'),
    ('one_position', '진입', '같은 종목 중복 진입 차단', 'fixed', None,
     '이미 보유한 종목은 새 진입으로 다시 사지 않고, 분할 계획(2·3차)으로만 더 삽니다.', '분할 계획과 손절 기준이 섞이지 않게 합니다.'),
    ('cutoff', '시간', '신규 매수 마감 · 장 마감 정리 시각', 'value', 'entry_cutoff_hhmm',
     '마감 시각부터 새로 사지 않고(분할 추가 매수 포함), 정리 시각부터 보유를 팝니다. 종가매매 조건을 모두 충족한 종목만 보유 한도 안에서 넘깁니다.',
     '마감 10:00~15:00, 정리 14:00~15:05에서 정합니다(기본 14:30·14:40). 정리는 주문당 약 2분이 걸려 실행기가 정리 뒤 20분(최대 15:18, 15:20 종가 단일가 전)까지 돌아갑니다. '
     '정리를 늦출수록 막판 움직임을 더 타지만 팔 시간이 줄어 미체결로 다음 날까지 남을 위험이 커집니다. 실계좌 미러·vibe도 같은 시각을 씁니다.'),
    ('averaging', '추가 매수', '약세장 물타기 금지', 'switch', 'averaging_down_guard',
     '하락장의 약세·혼조일(인버스는 상승장의 강세일)에는 2·3차 추가 매수를 하지 않습니다.',
     '떨어지는 장에서 평단을 낮추다 손실이 커지는 것을 막습니다(11/12 승인). 끄면 국면과 상관없이 추가 매수합니다.'),
    ('add_count', '추가 매수', '종목당 추가 매수 횟수', 'setting', 'scale_max_adds',
     '첫 매수를 빼고 한 종목에 이 횟수까지만 더 삽니다(새 판단 추가·가격 단계 추가·익절 후 재매수 합산).',
     '11/17 인버스 한 종목에 9번 추가 매수한 뒤 정했습니다(사용자 2026-11-17). 매매 기준 → 분할 추가 매수에서 0~100회로 정합니다.'),
    ('add_better', '추가 매수', '직전보다 나은 조건일 때만 추가 매수', 'setting', 'add_conf_sizing_on',
     'Jev 새 판단 추가 매수는 직전 매수보다 신뢰도가 0.02 이상 높거나, 평균가보다 한 단계 싸거나, 한 단계 수익 중일 때만 삽니다.',
     '같은 신뢰도로 비슷한 값에 거듭 사 한쪽 베팅만 커지던 11/17 사례 뒤 정했습니다. 매매 기준 → 분할 추가 매수의 스위치로 끕니다.'),
    ('inverse_guard', '진입', '인버스는 모든 관점이 폭락을 가리킬 때만 · 비폭락일 +0.3% 전량 익절', 'fixed', None,
     '인버스(114800·251340)는 일봉 국면 하락장, 그 지수 오늘 −0.5% 이하, 최근 30분 반등 아님, 프로그램 순매도, 전날 나스닥100 하락이 모두 맞을 때만 새로 사거나 더 삽니다. '
     '산 날 지수 하락이 −1.5%보다 작으면 +0.3%에서 전량 익절하고, −1.5% 이상 폭락일만 평소 청산을 씁니다.',
     '11/11~11/18 인버스 8왕복 중 폭락일(코스피200 −3.35%) 한 번이 +154,553원, 나머지는 약 −6만 원이었습니다(재현: 실제 +93,833원 → 두 규칙 +161,642원, 2026-11-18 사용자 결정).'),
    ('add_cutoff', '추가 매수', '정리 30분 전 추가 매수 마감', 'fixed', None,
     '장 마감 정리 시각 30분 전부터는 보유 종목에 더 사지 않습니다(새 판단 추가·가격 단계 추가·익절 후 재매수). 정리 15:00이면 14:30.',
     '정리 직전 추가 매수는 평균가만 올리고 팔 시간이 짧습니다(11/16 14:30~14:47 추가 3회, 2026-11-17 승인). 신규 진입 마감과 낙폭과대 선별 v2 창은 따로입니다.'),
    ('spread', '주문', '호가 차이보다 좁은 손절 거부', 'fixed', None,
     '손절 폭이 매수·매도 호가 차이의 2배보다 좁으면 사지 않습니다.', '평범한 호가 움직임만으로 손절되는 매수를 막는 안전 규칙입니다.'),
    ('qty', '주문', '1회 주문·전체 보유 수량 제한', 'setting', None,
     'Jev 공유 모의의 일반 매수·일반 매도 1회 수량과 전체 보유 수량 제한을 각각 선택할 수 있습니다.',
     '제한을 끄면 매도 가능 수량 이내에서 주수 제한이 없습니다. 손절·수동·마감 청산은 신속한 처리를 위해 1회 수량 제한과 별개이며, 금액 한도는 유지됩니다. 실계좌는 별도 수량 한도를 따릅니다.'),
    ('order_guard', '주문', '주문 보호 한도 (회당·하루·보유·새 종목·하루 손실)', 'setting', None,
     '회당 매수, 하루 매수, 보유 금액, 하루 새 종목 수, 하루 손실이 한도를 넘으면 주문 가드가 거부합니다.',
     '끌 수는 없고 "금액·주문 보호 한도"에서 범위 안으로 넓힐 수 있습니다.'),
    ('budget', '주문', '분할 1회 금액 · 종목당 최대 금액', 'setting', None,
     '한 번에 사는 금액과 한 종목에 넣는 최대 금액입니다.', '"금액·주문 보호 한도"에서 정합니다.'),
    ('overnight', '보유', '장 마감 보유 한도', 'setting', 'overnight_max_manwon',
     '종가매매 조건을 모두 충족해도 이 금액까지만 다음 날로 넘깁니다(0이면 항상 정리).', '매매 기준의 "장 마감 보유 한도"에서 정합니다.'),
    ('ipo_count', '보유', '신규주 동시 보유 종목 수', 'value', 'ipo_max_positions',
     '신규주를 한 번에 이 수까지만 보유합니다(1~3, 기본 1).', '변동이 큰 신규주의 손실 폭을 묶어 둡니다.'),
    ('ipo_daily', '진입', '신규주 하루 매수 횟수', 'setting', 'ipo_daily_max',
     '신규주는 하루 이 횟수까지만 삽니다(기본 2회).', '변동이 큰 신규주 매수를 하루 단위로 묶습니다(사용자 2026-11-17). 매매 기준에서 1~10회로 정합니다.'),
    ('ipo_add', '추가 매수', '신규주 추가 매수 금지', 'switch', 'ipo_add_guard',
     '신규주는 떨어져도 2·3차 추가 매수를 하지 않습니다.', '하루 −40%까지 움직이는 신규주에 물타기를 막습니다. 끄면 일반 종목처럼 분할 추가 매수합니다.'),
    ('reconcile', '안전', '미확인 주문 · 잔고 불일치 시 정지', 'fixed', None,
     '결과가 확인되지 않은 주문이 있거나 증권사 잔고와 원장이 다르면 새 주문을 내지 않고 멈춥니다.', '중복·유령 주문을 막는 핵심 안전장치라 끌 수 없습니다.'),
    ('read_fail', '안전', f'조회 실패 {_READ_FAILS}번 연속이면 정지', 'fixed', None,
     f'시세·분봉·잔고 조회가 한 번 실패하면 다음 주기에 다시 하고, {_READ_FAILS}번 연속이면 실행기를 멈춥니다(횟수는 운영 → 운영 상태).',
     '잘못된 데이터로 주문하지 않기 위한 규칙입니다.'),
    ('broker', '안전', f"하루 주문 998건 · {f'보유 {_HELD_MAX:,}주 상한' if _HELD_MAX else '보유 수량 제한 없음'}", 'fixed', None,
     '하루 주문이 998건(증권사 모의계좌 제한)에 닿거나 전체 보유가 운영 → 운영 상태의 총 보유 최대 수량(0이면 제한 없음)에 닿으면 새로 사지 않습니다.',
     '증권사 규칙과 운영 설정입니다.'),
]

# Which journal reasons each rule writes when it holds a buy back (v2_entry_gate reason / strength_reason, v2_execution_gate).
REASONS = {'reentry_allow': ('reentry_disabled', 'reentry_market_unconfirmed'),
           'careful_reentry': ('careful_reentry_chase', 'careful_reentry_weak', 'careful_reentry_no_session', 'careful_reentry_jev_wait',
                               'careful_reentry_review_error'), 'reentry': ('reentry_below_last_exit',), 'streak': ('losing_streak_cooldown',),
           'direction': ('direction_not_confirmed', 'market_unconfirmed'), 'opposite': ('opposite_direction_holding',),
           'volume': ('volume_not_rising',), 'rising': ('trend_not_rising',), 'one_position': ('split_plan_holding',),
           'add_cutoff': ('add_cutoff',), 'add_count': ('add_count_limit',), 'add_better': ('add_not_better',), 'ipo_daily': ('ipo_daily_limit',),
           'cutoff': ('entry_window_closed',), 'ipo_count': ('ipo_already_held',), 'averaging': ('downtrend_no_averaging_down',), 'spread': ('stop_inside_spread',),
           'order_guard': ('order_guard_refused', 'single_order_limit', 'split_limit', 'one_share_over_buy'), 'broker': ('order_limit', 'position_limit'),
           'inverse_guard': ('inverse_regime_not_falling', 'inverse_index_not_falling', 'inverse_index_rebounding',
                             'inverse_flows_not_selling', 'inverse_us_not_falling', 'inverse_market_unknown')}


def counts(events, day):
    """{rule id: times it held a buy back on `day` (YYYY-MM-DD)} from the runner journal."""
    by_reason = {}
    for e in events:
        if not isinstance(e, dict) or not str(e.get('time', '')).startswith(day):
            continue
        if e.get('event') in ('v2_entry_gate', 'v2_execution_gate') and e.get('permitted') is False:
            for reason in {e.get('reason'), e.get('strength_reason'), e.get('ipo_reason')} - {None}:
                if not isinstance(reason, str):
                    continue
                by_reason[reason] = by_reason.get(reason, 0) + 1
    return {rule: sum(by_reason.get(r, 0) for r in reasons) for rule, reasons in REASONS.items()}


GROUPS = ['진입', '시간', '추가 매수', '주문', '보유', '안전']
KIND_LABELS = {'switch': '끄기 가능', 'value': '여기서 설정', 'setting': '다른 설정으로 조절', 'fixed': '항상 적용'}


def live_rules(record):
    """A live account's protections as rows (항목, 지금 값, 조절, 내용); none can be turned off from the dashboard."""
    from accounts import live_limits, live_rules as rules_of
    budget, rules, limits = record.get('budget') or {}, rules_of(record), live_limits(record=record)
    won = lambda v: f'{v:,}원'
    size = '1주 기준' if rules['sizing'] == 'share' else f"금액 기준(운용금 비율, 체결 1건당 최대 {won(rules['tranche_krw'])})"
    rows = [
        ('실주문 스위치', '켜짐' if record.get('live_orders') is True else '꺼짐(계획만 기록)', '켜기는 확인 문구 필요',
         '자동매매 ON · HALT 꺼짐 · 이 스위치가 모두 켜져야 실제 주문이 실행됩니다.'),
        ('운용금', won(budget.get('capital_krw', 0)), '실계좌 잔고 이내', '실계좌 매입 금액의 합이 이 금액을 넘지 않습니다(매수 직전 현금·총자산도 확인).'),
        ('하루 손실 한도', won(budget.get('daily_loss_krw', 0)), '최대 2만 원(계좌별)', '닿으면 그날 매수를 막고 정리합니다. 시세 급변·미체결로 초과할 수 있습니다.'),
        ('1주 가격 상한', won(budget.get('order_price_max_krw', 0)), '최대 200만 원', '이보다 비싼 종목은 사지 않습니다(매수에만 적용).'),
        ('회당 매수 한도', won(limits['order_max_krw']), '1만 원~운용금', '한 번의 주문 금액이 이 값을 넘지 않습니다.'),
        ('하루 새 종목', f"{limits['daily_new_symbols']}개", '1~5개', '그날 처음 사는 종목 수 상한(이미 산 종목 재매수는 세지 않음).'),
        ('매수 크기 · 1회 최대 수량', f"{size} · " + (f"{rules['max_order_qty']:,}주" if rules['sizing'] == 'share' else '주수 제한 없음'), '1~1,000주(1주 기준만)',
         '1주 기준은 모의 분할 1회마다 1주, 금액 기준은 모의 체결 금액 × 운용금/(모의 분할 1회 × 3분할 × 하루 새 종목)(체결 1건당 최대 금액 이하)로 따라 삽니다.'),
        ('종목 손절', f"−{rules['stop_loss_pct']:g}%", '최대 2%', '평균가 대비 이만큼 내려가면 모의보다 먼저 전량 매도합니다(모의 손절과 둘 중 좁은 값).'),
        ('추가 매수 최대 횟수', f"{rules.get('max_adds', 100)}회" + (' (엔진 그대로)' if rules.get('max_adds', 100) >= 100 else ''), '0~100회',
         '엔진이 한 종목을 나눠 살 때 이 실계좌가 따라 사는 추가 매수 횟수(첫 매수 제외). 엔진 횟수를 넘지 않고, 바꾸면 새 모의 체결부터 적용됩니다.'),
        ('가격 추격 한도', f"{rules['chase_pct']:g}%" + (' · 근거 추격 켬' if rules['evidence_chase'] else ''), '0.1~5%',
         '엔진 체결가보다 이만큼 넘게 오르면 따라 사지 않습니다(근거 추격: 엔진이 최근 다시 고르고 1차 익절가 아래면 삼).'),
        ('매도 하한', '같은 종목 매수호가 −3%', '고정', '매도 지정가가 현재 매수호가보다 3% 넘게 낮지 않게 합니다.'),
        ('미체결·미확인 주문 차단', '항상 적용', '고정', '결과가 확인되지 않은 주문이 있으면 새 주문을 내지 않습니다.'),
        ('주문 보호(HALT)', '전역 표시', '실계좌 탭에서', '켜져 있으면 실계좌 주문 경로 전체가 막힙니다.'),
    ]
    return [{'항목': a, '지금 값': b, '조절': c, '내용': d} for a, b, c, d in rows]
