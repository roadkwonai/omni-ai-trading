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
