"""Operating tunables (not secrets, not trading rules) in ~/.vibe-trading/nhplug/tunables.json, edited on the dashboard
(운영 상태 → 운영 조정값). A missing or unreadable value means the default; a value outside its range is brought to the
edge, so an approved ceiling (`high`) can only be tightened. Runners read them at start unless noted otherwise.
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path

import config_store

# key: (default, low, high, group, label, help)
TUNABLES = {
    'session_open': ('09:00', '09:00', '10:00', '장 시간', '실행기 장 시작', '모든 실행기가 매매를 시작하는 시각입니다.'),
    'last_order': ('15:18', '15:00', '15:18', '장 시간', '마지막 주문 상한',
                   '정리 시각을 늦춰도 실행기는 이 시각까지만 돕니다. 15:20 종가 단일가 전이라 더 늦출 수 없습니다.'),
    'flatten_lead_min': (20, 10, 40, '장 시간', '정리 뒤 더 도는 분', '정리 주문은 건당 약 2분이 걸려, 정리 시각 뒤 이만큼 더 돕니다.'),
    'paper_max_order_qty': (100, 0, 1_000_000, '공유 모의 실행기', '1회 주문 최대 수량(주)', '0은 수량 제한 없음입니다. 금액 한도는 유지됩니다.'),
    'paper_max_total_qty': (1000, 0, 1_000_000, '공유 모의 실행기', '총 보유 최대 수량(주)', '0은 수량 제한 없음입니다. 금액 한도는 유지됩니다.'),
    'read_failure_limit': (5, 1, 20, '공유 모의 실행기', '조회 실패 정지 횟수', '시세·잔고 조회가 연속 이만큼 실패하면 실행기를 멈춥니다(vibe 실행기도 같음).'),
    'losing_streak': (3, 2, 10, '공유 모의 실행기', '연속 손실 쉬기: 횟수', '오늘 왕복이 이만큼 연속 손실이면 쉽니다.'),
    'losing_cooldown_min': (30, 5, 240, '공유 모의 실행기', '연속 손실 쉬기: 분', '쉬는 시간입니다.'),
    'ipo_start': ('09:20', '09:10', '14:00', '공유 모의 실행기', '신규주 보기 시작', '상장일 시초 흔들림을 피하려고 이 시각부터 봅니다.'),
    'ipo_max_scan': (3, 1, 10, '공유 모의 실행기', '신규주 시세 조회 수', '주기마다 시세를 볼 최신 신규주 수입니다.'),
    'vibe_interval_min': (15, 5, 60, 'vibe-trading', '판단 주기(분)', '기본 15(11/11 승인). 사용자가 2026-11-17 5로 바꿈. 판단기 시작 때 읽으므로 바꾸면 판단기를 다시 시작해야 합니다.'),
    'vibe_buy_score': (0.5, 0.1, 1.0, 'vibe-trading', '매수 점수', '두 회차 평균이 이 점수 이상이면 삽니다.'),
    'vibe_sell_score': (-0.3, -1.0, 0.0, 'vibe-trading', '매도 점수', '이 점수 이하이면 팝니다.'),
    'vibe_full_score_krw': (3_000_000, 100_000, 100_000_000, 'vibe-trading', '점수 1.0 매수 금액(원)', '점수에 비례해 삽니다.'),
    'vibe_strength_score': (0.2, 0.0, 1.0, 'vibe-trading', '강도 경로 최소 점수', '관심종목 강도 조건을 갖춘 종목의 최소 점수입니다.'),
    'vibe_strength_krw': (1_000_000, 100_000, 100_000_000, 'vibe-trading', '강도 경로 매수 금액(원)', '관심종목 강도 경로로 살 때의 금액입니다.'),
    'vibe_chase_vwap_pct': (2.0, 0.5, 10.0, 'vibe-trading', '추격 필터: VWAP 위 상한(%)',
                            '강도 경로는 현재가가 최근 80분 VWAP보다 이만큼 넘게 위면 사지 않습니다(11/16 손실 3건 +2.4~4%). 10이면 사실상 끔.'),
    'vibe_chase_range_pct': (85, 50, 100, 'vibe-trading', '추격 필터: 구간 위치 상한(%)',
                             '강도 경로는 현재가가 최근 80분 고저 범위의 이 위치 이상이면 사지 않습니다(0 저가, 100 고가). 100이면 사실상 끔.'),
    'live_session_end': ('15:20', '15:00', '15:20', '실계좌 미러', '미러 종료 시각', '더 이르게만 바꿀 수 있습니다.'),
    'quote_cache_s': (15, 0, 120, '대시보드', '시세 재사용(초)', '화면 표시용 시세를 이 시간 동안 다시 씁니다. 0이면 매번 조회합니다.'),
    'chart_cache_s': (30, 0, 300, '대시보드', '차트 재사용(초)', '차트·기간 시세를 이 시간 동안 다시 씁니다.'),
    'account_list_cache_s': (60, 0, 600, '대시보드', '계좌 목록 재사용(초)', '증권사 계좌 목록을 이 시간 동안 다시 씁니다.'),
    'process_cache_s': (5, 0, 60, '대시보드', '실행기 상태 재사용(초)', 'launchctl 조회 결과를 이 시간 동안 다시 씁니다.'),
}
GROUPS = ['장 시간', '공유 모의 실행기', 'vibe-trading', '실계좌 미러', '대시보드']
RESTART = {'장 시간': '공유 모의 실행기와 학습 관찰기를 다시 시작해야 반영됩니다',
           '공유 모의 실행기': '공유 모의 실행기를 STOP 절차로 다시 시작해야 반영됩니다',
           'vibe-trading': 'vibe 판단기·실행기를 다시 시작해야 반영됩니다',
           '실계좌 미러': '실계좌 미러 실행기를 다시 시작해야 반영됩니다', '대시보드': '바로 반영됩니다'}
_TIME = re.compile(r'([01]\d|2[0-3]):[0-5]\d')


def path():
    home = Path(os.environ.get('VIBE_TRADING_HOME') or Path.home() / '.vibe-trading').expanduser()
    return Path(os.environ.get('VIBE_TRADING_TUNABLES') or home / 'nhplug' / 'tunables.json')


def clean(key, raw):
    """`raw` as the tunable's type within its range, or None when it cannot be read."""
    default, low, high = TUNABLES[key][:3]
    try:
        if isinstance(default, str):
            value = str(raw).strip()
            if not _TIME.fullmatch(value):
                return None
        elif isinstance(default, int):
            if isinstance(raw, bool) or float(raw) != int(float(raw)):
                return None
            value = int(float(raw))
        else:
            if isinstance(raw, bool) or not math.isfinite(float(raw)):
                return None
            value = float(raw)
    except (TypeError, ValueError, OverflowError):  # int(inf); json reads Infinity and NaN
        return None
    return min(max(value, low), high)


_CACHE = {}


def stored():
    """{key: raw value} from the file, re-read only when it changed; {} when missing or unreadable."""
    file = path()
    try:
        stamp = (str(file), file.stat().st_mtime_ns)
    except OSError:
        return {}
    if _CACHE.get('stamp') != stamp:
        try:
            data = json.loads(file.read_text(encoding='utf-8'))
            values = data.get('values') if isinstance(data, dict) else None
        except (OSError, ValueError):
            values = None
        _CACHE.update(stamp=stamp, values=values if isinstance(values, dict) else {})
    return _CACHE['values']


def setting(key):
    """The tunable's value: the stored one (typed, in range) or its default."""
    raw = stored().get(key)
    value = clean(key, raw) if raw is not None else None
    return TUNABLES[key][0] if value is None else value


def values():
    return {key: setting(key) for key in TUNABLES}


def save(changes, who='dashboard'):
    """Store {key: value} after checking each one (ValueError in Korean otherwise); records the change history."""
    checked = {}
    for key, raw in changes.items():
        if key not in TUNABLES:
            raise ValueError(f'알 수 없는 설정입니다: {key}')
        value = clean(key, raw)
        if value is None:
            raise ValueError(f'{TUNABLES[key][4]} 값을 읽을 수 없습니다: {raw}')
        checked[key] = value
    before = values()
    after = {**{k: v for k, v in stored().items() if k in TUNABLES}, **checked}
    config_store.write_json(path(), {'version': 1, 'values': after})
    config_store.record('tunables', before, values(), who, saved=path())
    return checked
