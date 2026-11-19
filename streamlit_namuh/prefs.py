"""Dashboard display preferences (top-right "화면 설정"), kept in one small JSON file next to the runtime data.
Display only: nothing here changes trading, limits or orders."""
from __future__ import annotations

import json
from pathlib import Path

TABS = ['종합 현황', '시장 상황', '전략 구조', '트레이딩 현황', '매매일지', '계좌·자동매매', '엔진 비교', '장기투자', '주문·정리', 'AI·실행 기록', '실계좌', '운영 상태', 'API 연동', '백업·이식']
UNITS = ['원', '만원', '10만원', '100만원', '1000만원', '억원']
REFRESH = [15, 30, 60, 120]
FONT_SCALES = [70, 75, 80, 85, 90, 100, 110, 120]
DEFAULTS = {'status_blink': True, 'start_tab': TABS[0], 'refresh_s': 30, 'quotes_auto': False, 'money_unit': '만원', 'font_scale': 100, 'theme': 'system'}
CHOICES = {'status_blink': (True, False), 'start_tab': TABS, 'refresh_s': REFRESH, 'quotes_auto': (True, False), 'money_unit': UNITS, 'font_scale': FONT_SCALES,
           'theme': ('system', 'dark', 'light')}


def load(path):
    """Saved preferences merged over the defaults; unknown keys and values outside the choices fall back to the default."""
    try:
        saved = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        saved = {}
    return load_from(saved if isinstance(saved, dict) else {})


def save(path, values):
    """Write only known keys with allowed values; returns what was written."""
    import config_store
    clean = load_from(values)
    before = load(path)
    config_store.write_json(path, clean)
    config_store.record('prefs', before, clean, saved=path)
    return clean


def load_from(values):
    # type check too: True == 1 would otherwise let a bool pass for a number and back
    return {k: values[k] if values.get(k) in CHOICES[k] and type(values.get(k)) is type(v) else v for k, v in DEFAULTS.items()}
