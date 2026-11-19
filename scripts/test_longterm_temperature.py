"""Marks' fear·greed temperature (user request 2026-11-18): a reference reading from the engine's own facts, not a rule."""
import vibe_longterm as L

CALM = {'vix': 12.0, 'gap': 0.15, 'dd': 0.0}
PANIC = {'vix': 45.0, 'gap': -0.25, 'dd': -0.40}


def macro(value, stale=False):
    return {'series': {'BAMLH0A0HYM2': {'value': value, 'stale': stale}}}


def test_the_ends_and_the_bands():
    assert L.temperature(CALM, macro(2.5)) == {'score': 100.0, 'label': '극단적 탐욕', 'parts': L.temperature(CALM, macro(2.5))['parts']}
    assert L.temperature(PANIC, macro(7.0))['score'] == 0.0 and L.temperature(PANIC, macro(7.0))['label'] == '극단적 공포'  # clipped
    # 2026-11-17 facts: VIX 25.5, −3.8% under the 200-day line, −13.5% off the high, HY 3.34%p → neutral
    got = L.temperature({'vix': 25.46, 'gap': -0.0375, 'dd': -0.135}, macro(3.34))
    assert got['label'] == '중립' and 50 < got['score'] < 60 and [p['key'] for p in got['parts']] == ['vix', 'gap', 'dd', 'hy']


def test_missing_or_stale_parts_are_left_out():
    got = L.temperature({'vix': 26.0, 'gap': None, 'dd': float('nan')}, macro(3.0, stale=True))
    assert [p['key'] for p in got['parts']] == ['vix'] and got['score'] == 50.0
    assert L.temperature({}, None) is None and L.temperature(None) is None
