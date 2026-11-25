"""NH shared call budget (docs/nh-call-reduction-plan.md 3-1): one app key, eight processes, NH refuses past 5 a second."""
from src.tools import nh_budget as B


class Clock:
    def __init__(self):
        self.now, self.slept = 1000.0, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def take(path, clock, caller, tool='get_stock_price', arguments=None):
    return B.take(tool, arguments or {}, caller=caller, clock=clock, sleep=clock.sleep, path=path)


def test_a_background_caller_leaves_room_for_the_trading_processes(tmp_path):
    path, clock = tmp_path / 'budget.json', Clock()
    for _ in range(2):
        assert take(path, clock, 'market_movers') == 0
    waited = take(path, clock, 'market_movers')  # a third in the same second waits for the oldest to age out
    assert 0 < waited <= 1.0 and clock.slept
    clock.now += 2
    for _ in range(4):
        assert take(path, clock, 'nh_paper_isolated') == 0  # the runner gets the full four a second
    assert take(path, clock, 'nh_paper_isolated') > 0


def test_orders_never_wait_and_a_broken_budget_lets_the_call_through(tmp_path):
    path, clock = tmp_path / 'budget.json', Clock()
    for _ in range(4):
        take(path, clock, 'nh_paper_isolated')
    assert take(path, clock, 'nh_paper_isolated', 'call_api_guarded', {'operationId': 'krstockOrderCashSell'}) == 0
    assert take(path, clock, 'nh_live_follow', 'call_api', {'operationId': 'krstockOrderCashBuy'}) == 0
    assert not clock.slept
    path.write_text('{not json')
    assert take(path, clock, 'market_movers') == 0  # unreadable: no wait, no exception
    assert take(tmp_path / 'missing-dir' / 'x' / 'budget.json', clock, 'market_movers') == 0


def test_a_background_wait_is_capped_and_then_the_call_goes(tmp_path):
    path, clock = tmp_path / 'budget.json', Clock()
    for _ in range(2):
        take(path, clock, 'dashboard')
    frozen = Clock()  # its sleep is a no-op: the slots never free up and the clock never reaches the cap
    frozen.sleep = lambda seconds: None
    assert take(path, frozen, 'dashboard') == 0  # bounded loop: it gives up waiting and lets the call go
    assert len(__import__('json').loads(path.read_text())) == 3  # and it is counted


def test_the_tier_is_by_program_and_unknown_programs_are_background():
    assert B.high_priority('nh_paper_isolated') and B.high_priority('nh_live_follow') and B.high_priority('longterm_live')
    assert not B.high_priority('dashboard') and not B.high_priority('market_movers') and not B.high_priority('something_new')


def test_pytest_runs_never_touch_the_live_budget(monkeypatch):
    touched = []
    monkeypatch.setattr(B, '_locked', lambda *a, **k: touched.append(a))
    assert B.take('get_stock_price', {}) == 0 and not touched  # no explicit path under pytest: off


def inquiry(path, clock, caller='nh_paper_isolated', account='11111111', operation='krstockInquiryBalance'):
    return take(path, clock, caller, 'call_api', {'operationId': operation, 'input': {'act_no': account}})


def test_the_same_account_query_starts_a_second_apart_across_programs(tmp_path):
    """NH answers one account query (operation and account) about once a second and refuses the rest with IGW42903:
    11-23 the dashboard and the runner collided 27 times; each cost a 1 s retry."""
    path, clock = tmp_path / 'budget.json', Clock()
    assert inquiry(path, clock) == 0
    assert inquiry(path, clock, 'dashboard') == B.SAME_GAP       # another program, the same query
    start = clock.now
    assert inquiry(path, clock, account='22222222') == 0          # another account
    assert inquiry(path, clock, operation='krstockInquiryDailyOrderExecution') == 0  # another query
    clock.now = start + 5
    assert inquiry(path, clock) == 0
    assert take(path, clock, 'nh_paper_isolated', 'get_stock_price', {'stock_code': '005930'}) == 0  # quotes are not spaced
    assert take(path, clock, 'nh_paper_isolated', 'call_api', {'operationId': 'krstockQuotePeriod', 'input': {'act_no': 'x'}}) == 0
    text = (tmp_path / B.SAME_FILE).read_text()
    assert '11111111' not in text and '22222222' not in text      # account numbers are never written


def test_a_queue_for_one_query_is_capped_and_an_order_never_joins_it(tmp_path):
    path, clock = tmp_path / 'budget.json', Clock()
    clock.sleep = lambda seconds: clock.slept.append(seconds)     # everyone asks at the same moment
    waits = [inquiry(path, clock, 'dashboard') for _ in range(4)]
    assert waits[:3] == [0, B.SAME_GAP, round(2 * B.SAME_GAP, 3)] and max(clock.slept) <= B._same_deadline['low']
    assert inquiry(path, clock, 'nh_paper_isolated') <= B._same_deadline['high'] + 1.0  # the runner does not queue behind the dashboard
    order = {'operationId': 'krstockOrderCashSell', 'input': {'act_no': '11111111'}}
    before = len(clock.slept)
    assert take(path, clock, 'nh_paper_isolated', 'call_api', order) == 0 and len(clock.slept) == before


def test_a_broken_same_query_file_leaves_the_app_budget_working(tmp_path):
    path, clock = tmp_path / 'budget.json', Clock()
    (tmp_path / B.SAME_FILE).write_text('[broken')
    assert inquiry(path, clock) == 0
    assert len(__import__('json').loads(path.read_text())) == 1   # still counted against the app key


def test_old_queries_leave_the_same_query_file(tmp_path):
    import json
    path, clock = tmp_path / 'budget.json', Clock()
    for n in range(5):
        inquiry(path, clock, account=str(n))
        clock.now += 20
    assert len(json.loads((tmp_path / B.SAME_FILE).read_text())) == 1


def test_the_same_query_gap_is_kept_after_a_wait_for_a_slot(tmp_path):
    # 2026-11-25: the dashboard reserved its second, then waited for a slot and called late, so the next program's same
    # query came less than a second behind it (5 of the day's 9 refusals)
    path, clock, started = tmp_path / 'budget.json', Clock(), []
    for _ in range(B.RATE - B.RESERVE):
        take(path, clock, 'nh_paper_isolated')
    for caller in ('dashboard', 'nh_paper_isolated'):
        inquiry(path, clock, caller)
        started.append(clock.now)
        if caller == 'dashboard':
            assert clock.slept  # it waited for a slot first
    assert started[1] - started[0] >= B.SAME_GAP
