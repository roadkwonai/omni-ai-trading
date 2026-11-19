"""Korean wording for the live mirror's machine values (nh_live_follow plans, LiveOrderGuard states) on the dashboard."""
from __future__ import annotations

import re

ACTIONS = {'BUY': '매수', 'SELL': '매도', 'HOLD': '유지', 'WAIT': '대기', 'BLOCK': '차단', 'RECONCILE': '주문 대조 중',
           'RECONCILED': '대조 완료', 'CANCEL_PENDING': '미체결 취소', 'STOP': '장 종료'}
EXECUTIONS = {'DRY_RUN': '계획만 기록(관찰)', 'NO_ORDER': '주문 없음', 'ACKNOWLEDGED_NOT_FILLED': '주문 접수',
              'BLOCKED': '차단', 'CANCEL_ACKNOWLEDGED_NOT_RECONCILED': '취소 접수'}
STATES = {'terminal': '완료', 'acknowledged': '접수·체결 확인 중', 'uncertain': '결과 불명(대조 필요)', 'reserved': '전송 준비'}
OPERATIONS = {'krstockOrderCashBuy': '매수', 'krstockOrderCashSell': '매도', 'krstockOrderCancel': '취소'}
REASONS = {
    'matches the paper runner': '엔진 원장과 같음',
    'paper runner bought': '엔진이 매수함',
    'paper runner holds less': '엔진이 줄여서 매도',
    'before the session': '장 시작 전',
    'outside session': '장 종료',
    'outside the entry window': '매수 시간이 아님',
    'new buys paused by operator': '신규 매수 일시정지',
    'daily loss latch': '하루 손실 한도 도달',
    'not bought by the paper runner today or price moved past its fill': '오늘의 새 매수 또는 기존 보유 조정 조건·가격 확인 대기',
    'own order outstanding or uncertain': '내 주문이 아직 확인 중',
    'broker has outstanding orders this runner does not own': '다른 미체결 주문이 있음',
    'ledger holds more than the broker balance': '원장 수량이 잔고보다 많음(대조 필요)',
    'exit requested from the dashboard': '대시보드 정리 요청',
    'quote unavailable': '시세 조회 실패',
    'broker request failed': '증권사 조회 실패 · 주문 없음, 다음 주기 재시도',
    'broker rate limit': '증권사 호출 한도 초과 · 주문 없음, 다음 주기 재시도',
    'above the account price cap': '1주 가격 상한 초과',
    'above the account order limit': '회당 매수 한도보다 1주 가격이 큼',
    'daily new symbol limit': '하루 새 종목 수 한도 도달',
    'automation off': '자동매매 꺼짐(미러 쉼)',
    'capital limit reached': '운용금(또는 주문가능금액) 남은 금액이 1주 가격보다 작음',
    'global or NH live halt': '주문 보호(HALT) 켜짐',
    'engine still picks it, below the paper first target': '엔진이 다시 고름 · 1차 익절가 아래',
}


def reason(text):
    """A plan reason in Korean; unknown text is returned as is."""
    text = str(text or '')
    if text in REASONS:
        return REASONS[text]
    stop = re.fullmatch(r'([\d.]+)% stop from average price', text)
    if stop:
        return f'평균가 대비 {stop.group(1)}% 손절'
    chase = re.fullmatch(r'within ([\d.]+)% of the paper fill', text)
    if chase:
        return f'엔진 체결가 +{chase.group(1)}% 이내'
    if text.startswith('no paper ledger for engine'):
        return '따라갈 엔진 원장 없음'
    return text


def clock(stamp, today):
    """'HH:MM' of an ISO record time on `today` (YYYY-MM-DD), 'MM-DD HH:MM' on an earlier day: an old record must not
    read as today's."""
    text = str(stamp or '')
    return text[11:16] if text.startswith(today) else text[5:16].replace('T', ' ')


def day(value):
    """20261106 -> 2026-11-06."""
    text = str(value or '')
    return f'{text[:4]}-{text[4:6]}-{text[6:8]}' if len(text) == 8 and text.isdigit() else text
