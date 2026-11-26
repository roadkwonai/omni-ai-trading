from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from jev_buy_books import apply_quote, export_samples, report

KST = ZoneInfo('Asia/Seoul')
AT = datetime(2026, 11, 27, 9, 30, tzinfo=KST)


def quote(path, event_id, at, bid, ask, decisions=None, final=False):
    return apply_quote(path, event_id=event_id, symbol='005930', source_at=at,
                       received_at=at + timedelta(seconds=1), bid=bid, ask=ask, decisions=decisions, final=final)


def buy(at=AT):
    decided = (at - timedelta(seconds=1)).isoformat()
    return {'baseline': {'action': 'BUY', 'decided_at': decided, 'signal_id': 'first'},
            'experience': {'action': 'WAIT', 'decided_at': decided, 'signal_id': 'first'}}


def test_two_books_survive_reopen_and_costed_exit(tmp_path):
    path = tmp_path / 'experiment.sqlite3'
    quote(path, 'q1', AT, 99, 100, buy())
    assert path.stat().st_mode & 0o777 == 0o600
    first = report(path)
    assert first['baseline']['holdings']['005930']['qty'] > 0
    assert first['baseline']['realized_krw'] == 0
    assert first['baseline']['latest_equity_krw'] < 1_000_000
    assert first['cost_bps_per_side'] == 15
    assert first['experience']['holdings'] == {}
    quote(path, 'q2', AT + timedelta(minutes=1), 102, 103)
    result = report(path)
    assert result['baseline']['holdings'] == {}
    assert result['baseline']['fills'] == 2
    assert result['baseline']['realized_krw'] > 0
    assert result['baseline']['sampled_max_drawdown_krw'] > 0
    assert result['baseline']['exit_reasons']['take_profit'] == 1
    assert result['experience']['fills'] == 0
    sample = export_samples(path)
    assert sample[0]['arm'] == 'baseline'
    assert sample[0]['signal_id'] == 'first'
    assert sample[0]['closed_at'] == (AT + timedelta(minutes=1, seconds=1)).isoformat()
    assert sample[0]['net_return_pct'] == pytest.approx(1.697)
    assert sample[0]['cost_bps_per_side'] == 15


def test_missing_mark_is_counted_without_treating_it_as_zero(tmp_path):
    path = tmp_path / 'experiment.sqlite3'
    quote(path, 'q1', AT, 99, 100, buy())
    later = AT + timedelta(minutes=1)
    apply_quote(path, event_id='other', symbol='000660', source_at=later,
                received_at=later + timedelta(seconds=1), bid=120, ask=121, decisions=None)
    result = report(path)['baseline']
    assert result['missing_equity_marks'] == 1
    assert result['latest_equity_krw'] is None
    assert not result['drawdown_complete']


def test_idempotent_concurrent_and_conflicting_event(tmp_path):
    path = tmp_path / 'experiment.sqlite3'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: quote(path, 'q1', AT, 99, 100, buy()), range(2)))
    assert sorted(results) == ['applied', 'duplicate']
    assert report(path)['baseline']['fills'] == 1
    with pytest.raises(ValueError, match='conflicting'):
        quote(path, 'q1', AT, 98, 100, buy())
    quote(path, 'q2', AT + timedelta(seconds=30), 100, 100, buy())
    assert report(path)['baseline']['fills'] == 1


def test_failed_second_arm_rolls_back_first_arm(tmp_path):
    path = tmp_path / 'experiment.sqlite3'
    quote(path, 'q1', AT, 99, 100, buy())
    at = AT + timedelta(seconds=30)
    changed = buy()
    changed['baseline']['signal_id'] = 'second'
    changed['experience']['action'] = 'BUY'
    with pytest.raises(ValueError, match='conflicting'):
        quote(path, 'q2', at, 99, 100, changed)
    assert report(path)['baseline']['fills'] == 1
    assert report(path)['experience']['fills'] == 0
    changed['experience']['signal_id'] = 'second'
    assert quote(path, 'q2', at, 99, 100, changed) == 'applied'
    assert report(path)['experience']['fills'] == 1


def test_future_or_stale_quotes_are_rejected(tmp_path):
    path = tmp_path / 'experiment.sqlite3'
    with pytest.raises(ValueError):
        quote(path, 'q1', AT, 99, 100, buy(AT + timedelta(seconds=1)))
    with pytest.raises(ValueError):
        apply_quote(path, event_id='q2', symbol='005930', source_at=AT,
                    received_at=AT + timedelta(seconds=6), bid=99, ask=100, decisions=None)
    assert not path.exists()


def test_stop_final_close_and_missing_marks(tmp_path):
    path = tmp_path / 'experiment.sqlite3'
    quote(path, 'q1', AT, 99, 100, buy())
    quote(path, 'q2', AT + timedelta(minutes=1), 99, 100)
    assert report(path)['baseline']['exit_reasons']['stop_loss'] == 1
    other = tmp_path / 'other.sqlite3'
    quote(other, 'q1', AT, 99, 100, buy())
    quote(other, 'q2', AT + timedelta(minutes=1), 100, 101, final=True)
    assert report(other)['baseline']['exit_reasons']['session_close'] == 1


def test_protect_operational_paths():
    with pytest.raises(ValueError):
        quote(Path.home() / '.vibe-trading/nhplug/shared-etf-paper/orders.sqlite3', 'q1', AT, 99, 100, buy())


def test_bootstrap_baseline_only_is_persistently_noncomparable(tmp_path):
    from jev_buy_books import apply_bootstrap_quote

    path = tmp_path / 'experiment.sqlite3'
    kwargs = {'event_id': 'boot1', 'symbol': '005930', 'source_at': AT,
              'received_at': AT + timedelta(seconds=1), 'bid': 99, 'ask': 100,
              'decision': buy()['baseline']}
    assert apply_bootstrap_quote(path, **kwargs) == 'applied'
    assert apply_bootstrap_quote(path, **kwargs) == 'duplicate'
    summary = report(path)
    assert summary['baseline']['holdings']['005930']['qty'] > 0
    assert summary['experience']['holdings'] == {}
    assert summary['comparison_cases'] == {'unavailable': 1}
    assert summary['baseline']['signal_outcomes'] == {'filled': 1}
    assert summary['experience']['signal_outcomes'] == {}
    quote(path, 'exit', AT + timedelta(minutes=1), 102, 103)
    assert len(export_samples(path)) == 1
    assert export_samples(path)[0]['comparison'] == 'unavailable'


def test_signal_outcomes_record_blocked_buy_reasons(tmp_path):
    path = tmp_path / 'experiment.sqlite3'
    quote(path, 'first', AT, 100, 100, buy())
    second = buy(AT + timedelta(seconds=10))
    second['baseline']['signal_id'] = second['experience']['signal_id'] = 'second'
    second['experience']['action'] = 'BUY'
    quote(path, 'second-quote', AT + timedelta(seconds=10), 100, 100, second)
    third = buy(AT + timedelta(seconds=20))
    third['baseline']['signal_id'] = third['experience']['signal_id'] = 'third'
    third['experience']['action'] = 'BUY'
    quote(path, 'third-quote', AT + timedelta(seconds=20), 100, 100, third)
    summary = report(path)
    assert summary['baseline']['signal_outcomes'] == {'filled': 1, 'already_held': 2}
    assert summary['experience']['signal_outcomes'] == {'wait': 1, 'filled': 1, 'already_held': 1}


def test_buy_too_expensive_records_amount_limit(tmp_path):
    path = tmp_path / 'experiment.sqlite3'
    quote(path, 'q1', AT, 1_000_000, 1_000_001, buy())
    assert report(path)['baseline']['signal_outcomes'] == {'amount_limit': 1}
    assert report(path)['baseline']['fills'] == 0


def test_bootstrap_cannot_be_reclassified_as_paired(tmp_path):
    from jev_buy_books import apply_bootstrap_quote

    path = tmp_path / 'experiment.sqlite3'
    apply_bootstrap_quote(path, event_id='boot', symbol='005930', source_at=AT,
                          received_at=AT + timedelta(seconds=1), bid=100, ask=100, decision=buy()['baseline'])
    with pytest.raises(ValueError, match='conflicting comparison identity'):
        quote(path, 'paired', AT + timedelta(seconds=10), 100, 100, buy())
    assert report(path)['comparison_cases'] == {'unavailable': 1}
    assert report(path)['experience']['fills'] == 0
