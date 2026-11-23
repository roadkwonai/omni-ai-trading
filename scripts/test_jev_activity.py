import fcntl
import io
import json
import multiprocessing
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from jev_activity import JevRequestBusy, regular_request, regular_request_busy


def _hold_regular(path, ready, stop):
    with regular_request(path):
        ready.set()
        stop.wait(60)


def _race_once(path, start, entered, release, results):
    start.wait(5)
    try:
        with regular_request(path):
            results.put('sent')
            entered.set()
            release.wait(5)
    except JevRequestBusy:
        results.put('busy')


def test_regular_request_is_visible_and_released(tmp_path):
    path = tmp_path / 'activity.lock'
    assert not regular_request_busy(path)
    with regular_request(path):
        assert regular_request_busy(path)
        with pytest.raises(JevRequestBusy):
            with regular_request(path):
                pytest.fail('a second HTTP request must not start')
        assert regular_request_busy(path)
    assert not regular_request_busy(path)


def test_regular_does_not_wait_or_send_when_event_has_lock(tmp_path):
    path = tmp_path / 'activity.lock'
    with path.open('w') as event:
        fcntl.flock(event, fcntl.LOCK_EX | fcntl.LOCK_NB)
        start = time.monotonic()
        with pytest.raises(JevRequestBusy):
            with regular_request(path):
                pytest.fail('regular request was sent while event lock was held')
        assert time.monotonic() - start < 0.5
        fcntl.flock(event, fcntl.LOCK_UN)
    assert not regular_request_busy(path)


def test_unavailable_lock_skips_request_without_waiting(tmp_path):
    unavailable = tmp_path / 'directory'
    unavailable.mkdir()
    with pytest.raises(JevRequestBusy):
        with regular_request(unavailable):
            pytest.fail('no request is allowed without the shared lock')
    assert regular_request_busy(unavailable)


def test_event_is_rejected_when_regular_process_is_in_flight(tmp_path):
    path = tmp_path / 'activity.lock'
    ready, stop = multiprocessing.Event(), multiprocessing.Event()
    process = multiprocessing.Process(target=_hold_regular, args=(path, ready, stop))
    process.start()
    try:
        assert ready.wait(5)
        start = time.monotonic()
        with pytest.raises(JevRequestBusy):
            with regular_request(path):
                pytest.fail('event sent while regular request was in flight')
        assert time.monotonic() - start < 0.5
    finally:
        stop.set()
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join(5)
    with regular_request(path):
        assert regular_request_busy(path)


def test_processes_starting_simultaneously_never_send_together(tmp_path):
    path = tmp_path / 'activity.lock'
    start, entered, release = multiprocessing.Event(), multiprocessing.Event(), multiprocessing.Event()
    results = multiprocessing.Queue()
    workers = [multiprocessing.Process(target=_race_once, args=(path, start, entered, release, results)) for _ in range(2)]
    for worker in workers:
        worker.start()
    try:
        start.set()
        assert entered.wait(5)
        assert sorted([results.get(timeout=5), results.get(timeout=5)]) == ['busy', 'sent']
    finally:
        release.set()
        for worker in workers:
            worker.join(5)
            if worker.is_alive():
                worker.terminate()
                worker.join(5)


def test_lock_is_released_after_http_error(tmp_path):
    path = tmp_path / 'activity.lock'
    with pytest.raises(TimeoutError):
        with regular_request(path):
            raise TimeoutError('simulated transport timeout')
    with regular_request(path):
        assert regular_request_busy(path)


def test_stop_does_not_wait_for_a_busy_jev_lock(tmp_path):
    import nh_paper_isolated as paper

    path = tmp_path / 'activity.lock'
    with regular_request(path), patch.object(paper, 'jev_holding7') as jev:
        started = time.monotonic()
        assert paper.holding_action(None, tmp_path, '114800', 100, 98,
                                    etf7=True, stop_hit=True)
        assert time.monotonic() - started < 0.5
        jev.assert_not_called()


def test_flatten_does_not_call_busy_jev_and_still_submits_protective_sale(tmp_path):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import nh_paper_isolated as paper

    now = datetime(2026, 11, 16, 14, 50, tzinfo=ZoneInfo('Asia/Seoul'))

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    class Reader:
        def call_tool(self, tool, args):
            return {'status': 'ok', 'data': {}}

    class Broker:
        def call_tool(self, tool, args):
            return {'status': 'ok', 'data': {'rsp_cd': 'XA102', 'Output_0': {'iem_cd': '114800', 'sll_pbl_qty': 1}}}

    def pages(broker, operation, fields, block):
        if operation == 'krstockInquiryDailyOrderExecution':
            return {'Output_0': []}
        return {'Output_0': {}, 'Output_1': [{'iem_cd': '114800', 'rsdl_qty': 1, 'phs_pr': 100}]}

    db = paper.connect_journal(tmp_path / 'orders.sqlite3', paper.account_hash('50001000002'))
    db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('a', '114800', 'BUY', 1, 100, now.isoformat()))
    db.commit()
    try:
        with regular_request(tmp_path / 'activity.lock'), patch.object(paper, 'datetime', Clock), \
             patch.object(paper, 'paper_pages', side_effect=pages), \
             patch.object(paper, 'fresh_quote', return_value={'bidp': 100, 'iem_cd': '114800',
                                                               'market_status': '1', 'hoga_bsop_hour': '14:50:00'}), \
             patch.object(paper, 'etf7_protective_deadline', return_value=2**52), \
             patch.object(paper, 'overnight_reason', return_value='not_allowed'), \
             patch.object(paper, 'holding_action') as holding, \
             patch.object(paper, 'journal_event'), \
             patch.object(paper, 'submit', return_value=('b'*32, 1)) as submit:
            assert paper.cycle(db, Reader(), Broker(), '50001000002', tmp_path, True, now, etf7=True) == 'sell'
            holding.assert_not_called()
            assert submit.call_args.args[4] == '114800'
            assert submit.call_args.args[5] == 'SELL'
    finally:
        db.close()


def test_busy_model_records_unavailable_opinion_without_sending(tmp_path):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import jev_activity
    import nh_etf_auto_v2 as model
    import nh_jev_compare
    import nh_paper_isolated as paper

    now = datetime(2026, 11, 16, 12, 0, tzinfo=ZoneInfo('Asia/Seoul'))

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    rows = [{'bsop_time': '115800', 'stck_prpr': '100'} for _ in range(6)]
    reader = MagicMock()
    reader.call_tool.return_value = {'status': 'ok', 'data': {}}
    figure = MagicMock()
    axes = SimpleNamespace(flat=[MagicMock() for _ in range(8)])
    path = tmp_path / 'activity.lock'
    with patch.object(jev_activity, 'LOCK', path), regular_request(path), \
         patch.object(paper, 'datetime', Clock), \
         patch.object(paper, 'minute_bars', return_value={'status': 'ok', 'data': {}}), \
         patch.object(paper, 'valid_bars', return_value=rows), \
         patch.object(paper, 'fresh_etf7_bars', return_value=True), \
         patch.object(paper, 'investor_flows', return_value=None), \
         patch.object(paper, 'track_record', return_value={}), \
         patch.object(paper, 'market_view', return_value=None), patch.object(paper, 'trading_journal_brief', return_value={}), \
         patch.object(paper, 'premarket_view', return_value=None), \
         patch.object(paper.oversold_cases, 'bars_context', return_value={}), \
         patch('etf_news.headlines7', return_value=[{'title': 'market', 'published': now.isoformat()}]), \
         patch.object(model, '_etf7_inputs'), patch.object(model.old, 'dotenv_values', return_value={'JEV_API_KEY': 'test'}), \
         patch.object(nh_jev_compare.plt, 'subplots', return_value=(figure, axes)), \
         patch.object(nh_jev_compare.plt, 'close'), patch.object(model.urllib.request, 'urlopen') as request, \
         patch.object(paper, 'journal_event') as events:
        assert not paper.holding_action(reader, tmp_path, '114800', 100, 100, etf7=True,
                                        modes={'decision': False, 'jev': True}, stop_hit=False)
        request.assert_not_called()
        error = next(call for call in events.call_args_list if call.args[1] == 'v2_holding_decision_error')
        assert error.kwargs['error_type'] == 'JevRequestBusy'


def test_process_exit_does_not_leave_stale_busy_state(tmp_path):
    path = tmp_path / 'activity.lock'
    ready = multiprocessing.Event()
    stop = multiprocessing.Event()
    process = multiprocessing.Process(target=_hold_regular, args=(path, ready, stop))
    process.start()
    try:
        assert ready.wait(5)
        assert regular_request_busy(path)
    finally:
        process.terminate()
        process.join(5)
    assert not regular_request_busy(path)


def test_regular_model_marks_busy_during_network_call(tmp_path):
    import jev_activity
    import nh_etf_auto_v2 as runner
    import nh_jev_compare
    from etf_universe import SYMBOLS

    path = tmp_path / 'activity.lock'
    bars = {name: [{'stck_prpr': 100, 'bsop_time': '101000'}] for name in SYMBOLS}
    figure = MagicMock()
    axes = SimpleNamespace(flat=[MagicMock() for _ in range(8)])

    def fake_urlopen(_request, *, timeout):
        assert timeout == 25
        assert regular_request_busy(path)
        return io.BytesIO(json.dumps({'model': 'jev-1.14.0', 'answers': {
            'holding': {'type': 'choice', 'choice': 'HOLD', 'confidence': 0.8}}}).encode())

    with patch.object(jev_activity, 'LOCK', path), patch.object(runner, '_etf7_inputs'), \
         patch.object(runner.old, 'dotenv_values', return_value={'JEV_API_KEY': 'test'}), \
         patch.object(nh_jev_compare.plt, 'subplots', return_value=(figure, axes)), \
         patch.object(nh_jev_compare.plt, 'close'), patch.object(runner.urllib.request, 'urlopen', side_effect=fake_urlopen), \
         patch('research_store.record_input', return_value='a' * 64):
        news = [{'title': '시장', 'published': runner.datetime.now(runner.old.KST).isoformat()}]
        result = runner.jev_holding7(bars, news, SYMBOLS['INV'], 100, 101)
    assert result['holding']['choice'] == 'HOLD'
    assert not regular_request_busy(path)


def test_legacy_jev_http_is_not_sent_while_lock_is_held(tmp_path):
    import jev_activity
    import nh_jev_compare as legacy

    path = tmp_path / 'activity.lock'
    with patch.object(jev_activity, 'LOCK', path), regular_request(path), \
         patch.object(legacy, 'dotenv_values', return_value={'JEV_API_KEY': 'test'}), \
         patch.object(legacy, 'chart_png', return_value=b'chart'), \
         patch.object(legacy.urllib.request, 'urlopen') as request:
        with pytest.raises(JevRequestBusy):
            legacy.jev({}, [])
        request.assert_not_called()


def test_live_observer_jev_http_is_not_sent_while_lock_is_held(tmp_path):
    import jev_activity
    import nh_live_ai_observer as observer
    import nh_jev_compare

    path = tmp_path / 'activity.lock'
    bars = {'K200': [{'stck_prpr': '100'}], 'INV': [{'stck_prpr': '100'}]}
    decision = {'answers': [{'name': 'candidate', 'type': 'choice', 'choice': 'WAIT', 'confidence': 0.5},
                            {'name': 'exit', 'type': 'choice', 'choice': 'HOLD', 'confidence': 0.5}]}
    figure, axes = MagicMock(), [MagicMock(), MagicMock()]
    with patch.object(jev_activity, 'LOCK', path), regular_request(path), \
         patch.object(observer, '_post', return_value=decision) as post, \
         patch.object(nh_jev_compare.plt, 'subplots', return_value=(figure, axes)), \
         patch.object(nh_jev_compare.plt, 'close'):
        with pytest.raises(JevRequestBusy):
            observer.judgments(bars, [], {'OPENAI_API_KEY': 'test', 'JEV_API_KEY': 'test'})
        assert post.call_count == 1
        assert post.call_args.args[0].startswith('https://api.openai.com/')
