from datetime import date, timedelta

import pytest

import longterm_cases as C


def path():
    """Up 300 days, a 25% fall over 50 days, then a full recovery: one dd10 and one dd20 case, no dd30."""
    px = [100 + i * 0.2 for i in range(300)]
    top = px[-1]
    px += [top * (1 - 0.25 * (i + 1) / 50) for i in range(50)]
    low = px[-1]
    px += [low + (top * 1.2 - low) * (i + 1) / 300 for i in range(300)]
    return px


def test_first_touch_cases_and_their_forward_returns():
    px = path()
    days = [f'd{i}' for i in range(len(px))]
    out = C.study(px, days)
    assert len(out['dd10']['events']) == 1 and len(out['dd20']['events']) == 1 and out['dd30']['events'] == []
    i = days.index(out['dd20']['events'][0])
    assert px[i] <= max(px[i - 252:i + 1]) * 0.80 < px[i - 1]
    f = out['dd20']['h'][120]
    assert f['n'] == 1 and f['median'] == pytest.approx(px[i + 120] / px[i] - 1) and f['worst'] <= 0
    assert out['baseline']['h'][20]['n'] > 10


def test_build_evidence_and_description(tmp_path):
    start = date(2016, 1, 1)
    series = {start + timedelta(days=i): v for i, v in enumerate(path())}
    flat = {d: 1.0 for d in series}
    vix = {d: 20.0 for d in series}
    fetch = lambda s: flat if s == 'KRW=X' else vix if s == '^VIX' else series
    assert C.build(tmp_path, fetch) > 0
    ev = C.evidence(tmp_path)
    assert ev[('NDX', 'dd20')][120]['n'] == 1 and ('RES', 'dd20') not in ev
    line = C.describe(ev[('NDX', 'dd20')], ev[('NDX', 'baseline')])
    assert line.startswith('과거 1건 · 120일 뒤 중앙') and '평소' in line
    assert C.events('NDX', 'dd20', tmp_path) and C.describe(None) == '과거 사례 없음'


def test_every_drawdown_level_the_map_draws_has_past_cases():
    from test_vibe_longterm import level_map
    active = ['bogle_core', 'yeo_reservoir', 'marks_pendulum']  # 투매 with 막스 moves the tiers to −15%·−25%
    drawn = {x['case'] for regime in ('bull', 'panic') for x in level_map(regime=regime, active=active) if x['master'] == 'yeo_reservoir'}
    px = path()
    assert drawn == {'dd15', 'dd20', 'dd25', 'dd30'} and drawn <= set(C.study(px, [f'd{i}' for i in range(len(px))]))
