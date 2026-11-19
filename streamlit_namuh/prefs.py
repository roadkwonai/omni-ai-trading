"""Dashboard display preferences (top-right "화면 설정"), kept in one small JSON file next to the runtime data.
Display only: nothing here changes trading, limits or orders."""
from __future__ import annotations

import json
from pathlib import Path

# 2026-11-19 (user-approved plan docs/dashboard-menu-plan-2026-11-19.md): three groups, daily → accounts → admin
GROUPS = (('매일', ('종합 현황', '시장 상황', '전략 구조', '트레이딩 현황', '매매일지', '성과 비교')),
          ('계좌', ('계좌·자동매매', '장기투자', '실계좌')),
          ('관리', ('운영',)))
TABS = [tab for _, tabs in GROUPS for tab in tabs]
OPS_VIEWS = ['운영 상태', '자동 작업', 'AI·실행 기록', 'API 연동', '스킬', '리포트 발행', '백업·이식']  # the sub-screens of 운영
LIVE_ORDERS = '주문·정리 종합'  # the 실계좌 view that took over the old 주문·정리 tab
# old tab name → (tab, session key of its sub-screen, sub-screen): saved start tabs, ?tab= links and code jumps
ALIASES = {'엔진 비교': ('성과 비교', None, None), '주문·정리': ('실계좌', 'live_view', LIVE_ORDERS),
           **{name: ('운영', 'ops_view', name) for name in OPS_VIEWS}}


VIEW_KEYS = {'운영': 'ops_view', '실계좌': 'live_view'}  # tabs whose sub-screen ?view= picks
VIEWS = {'운영': OPS_VIEWS, '실계좌': ['전체 합산', LIVE_ORDERS]}  # a link may name only these (a stray value would break the picker)


def place(name, view=None):
    """(tab, sub-screen key, sub-screen) for a tab name, old or new, and an optional ?view=; None when it is neither."""
    if name in TABS:
        return (name, VIEW_KEYS[name], view) if view in VIEWS.get(name, ()) else (name, None, None)
    return ALIASES.get(name)
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
    values = {**values, 'start_tab': (place(values.get('start_tab')) or (None,))[0] if isinstance(values.get('start_tab'), str) else None}
    return {k: values[k] if values.get(k) in CHOICES[k] and type(values.get(k)) is type(v) else v for k, v in DEFAULTS.items()}
