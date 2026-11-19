import restrictions


def test_add_and_new_listing_limits_are_counted_under_their_rules():
    day = '2026-11-17'
    events = [{'time': f'{day}T10:00:00', 'event': 'v2_entry_gate', 'permitted': False, 'reason': 'add_count_limit'},
              {'time': f'{day}T10:01:00', 'event': 'v2_execution_gate', 'permitted': False, 'reason': 'add_not_better'},
              {'time': f'{day}T10:02:00', 'event': 'v2_entry_gate', 'permitted': False, 'reason': 'jev_wait', 'ipo_reason': 'ipo_daily_limit'}]
    found = restrictions.counts(events, day)
    rule_of = {reason: rule for rule, reasons in restrictions.REASONS.items() for reason in reasons}
    assert {found[rule_of[r]] for r in ('add_count_limit', 'add_not_better', 'ipo_daily_limit')} == {1}
    assert {rule_of[r] for r in ('add_count_limit', 'add_not_better', 'ipo_daily_limit')} <= {r[0] for r in restrictions.RULES}


def test_inverse_gate_refusals_count_under_the_inverse_rule():
    # r3 E10: the 제한 규칙 tab showed 0 for the inverse rule on the days it refused most picks
    day = '2026-11-18'
    reasons = ('inverse_regime_not_falling', 'inverse_index_not_falling', 'inverse_index_rebounding',
               'inverse_flows_not_selling', 'inverse_us_not_falling', 'inverse_market_unknown')
    events = [{'time': f'{day}T13:40:00', 'event': 'v2_entry_gate', 'permitted': False, 'reason': r} for r in reasons]
    events.append({'time': f'{day}T13:41:00', 'event': 'v2_execution_gate', 'permitted': False, 'reason': 'inverse_us_not_falling'})
    assert restrictions.counts(events, day)['inverse_guard'] == 7
    assert 'inverse_guard' in {r[0] for r in restrictions.RULES}


def test_fixed_safety_rows_show_the_stored_tunables(tmp_path, monkeypatch):
    """11/17 the runner held 6,702 inverse shares with paper_max_total_qty 0 while the 'broker' row said 1,000주 상한."""
    import importlib
    import json
    import tunables
    file = tmp_path / 'tunables.json'
    file.write_text(json.dumps({'version': 1, 'values': {'paper_max_total_qty': 0, 'read_failure_limit': 3}}))
    monkeypatch.setenv('VIBE_TRADING_TUNABLES', str(file))
    try:
        rows = {r[0]: r for r in importlib.reload(restrictions).RULES}
        assert '1,000주' not in rows['broker'][2] + rows['broker'][5] and '제한 없음' in rows['broker'][2]
        assert '3번' in rows['read_fail'][2] and '5번' not in rows['read_fail'][2] + rows['read_fail'][5]
        file.write_text(json.dumps({'version': 1, 'values': {'paper_max_total_qty': 2500}}))
        assert '2,500주' in {r[0]: r for r in importlib.reload(restrictions).RULES}['broker'][2]
    finally:
        monkeypatch.undo()
        importlib.reload(restrictions)
