"""장기투자 관심목록 (user approval 2026-11-18, docs/longterm-account-settings-plan.md 2-A): KRX codes outside the engine's
six ETFs that the 적립식 may buy as its own sleeve. The engine never rebalances or sells them; the virtual NAV, the shadows,
the live account's capital room and the report count them. vibe-longterm/longterm-watchlist.json is {code: name}, saved through
config_store (history kind vibe-longterm). Taking a code off the list stops new 적립식 buys; what is held stays held.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

FILE = 'longterm-watchlist.json'  # not watchlist.json: that name is Jev's daily snapshot in shared-etf-paper
MAX = 20
CODE = re.compile(r'[0-9][0-9A-Z]{5}')


def clean(data):
    """{code: name} keeping KRX codes outside the engine's ETFs, in order, at most MAX."""
    from vibe_longterm import CODE_KEY
    items = data.items() if isinstance(data, dict) else ()
    out = {c: n if isinstance(n, str) and n else c for c, n in items if isinstance(c, str) and CODE.fullmatch(c) and c not in CODE_KEY}
    return dict(list(out.items())[:MAX])


def load(root):
    try:
        return clean(json.loads((Path(root) / FILE).read_text(encoding='utf-8')))
    except (OSError, ValueError):
        return {}


def problem(code, listed):
    """Korean reason `code` cannot join the list, or None."""
    from vibe_longterm import CODE_KEY
    if not CODE.fullmatch(code or ''):
        return '국내 상장 종목코드 6자리(예: 005930)를 넣으세요.'
    if code in CODE_KEY:
        return '엔진이 운용하는 ETF입니다. 적립 종목 선택에서 바로 고르세요.'
    if code in listed:
        return '이미 관심목록에 있습니다.'
    return f'관심목록은 {MAX}종목까지입니다.' if len(listed) >= MAX else None


def save(root, listed, who='dashboard'):
    import config_store
    before, after = load(root), clean(listed)
    config_store.write_json(Path(root) / FILE, after)
    config_store.record('vibe-longterm', {'watchlist': before}, {'watchlist': after}, who=who, saved=Path(root) / FILE)
    return after


def for_dca(cfg, root):
    """The 적립식 setting with its picks limited to the engine's ETFs, the 여운봉 매매법 ETFs (0261H0 …, offered as picks without
    being listed) and the codes still on the list."""
    from vibe_longterm import ASSETS, key_of
    from yeo_method import EXTRA
    listed = load(root)
    return {**cfg, 'picks': [k for k in cfg['picks'] if k in ASSETS or key_of(k) in EXTRA or k in listed]}
