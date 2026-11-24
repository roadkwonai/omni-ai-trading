import json
import sqlite3
import threading
from datetime import datetime, timedelta

import pytest

from cli_anything.omni_trading.core import approval as AP
from cli_anything.omni_trading.core import output as O
from cli_anything.omni_trading.core import reads as R
from cli_anything.omni_trading.core import settings as S
from cli_anything.omni_trading.core.output import OmniError, Refused
from cli_anything.omni_trading.utils import omni_backend

from .conftest import account, write

STRATEGY = S.GROUPS['strategy']


@pytest.fixture(autouse=True)
def evening(monkeypatch):
    monkeypatch.setattr(AP, 'now', lambda: datetime(2026, 11, 24, 18, 0, tzinfo=O.KST))


class Box(list):
    def __call__(self, text):
        self.append(text)
        return 1

    @property
    def code(self):
        asked = [text for text in self if '이 코드를 알려 주세요: ' in text]  # a result notice may come after it
        return asked[-1].split('이 코드를 알려 주세요: ')[1].split('\n')[0]


def history(nh):
    with sqlite3.connect(nh / 'settings-history.sqlite3') as db:
        return db.execute('SELECT kind, key, new, who FROM changes ORDER BY rowid').fetchall()


def plan(nh, group='strategy', reason='시험', **changes):
    return AP.make(nh, S.ALL[group], {k.replace('__', '.'): v for k, v in changes.items()}, reason)


def test_the_four_settings_folders_must_be_one(home, monkeypatch, tmp_path):
    assert omni_backend.nhplug() == home
    monkeypatch.setenv('VIBE_TRADING_HOME', str(tmp_path / 'other'))
    with pytest.raises(Refused, match='런타임 폴더가 서로 다릅니다'):
        omni_backend.nhplug()


@pytest.mark.parametrize('group', list(S.GROUPS))
def test_every_setting_has_a_value_that_parses_back_to_itself(home, group):
    g = S.GROUPS[group]
    values = g.read(home)
    for key, spec in g.specs(home).items():
        assert key in values, key
        if values[key] is not None:
            raw = json.dumps(values[key]) if spec.kind == 'json' else str(values[key])
            assert S.parse(spec, raw, key) == values[key], key


def test_all_strategy_and_tunable_keys_are_reachable(home):
    import strategy_settings as ST
    import tunables as T
    assert set(STRATEGY.specs(home)) == set(ST.DEFAULTS)
    assert set(S.GROUPS['tunables'].specs(home)) == set(T.TUNABLES)


@pytest.mark.parametrize('group,key,value', [
    ('strategy', 'add_step_pct', 0.9), ('tunables', 'losing_streak', 4), ('prefs', 'refresh_s', 60), ('ai', 'decision', False),
    ('accounts', 'paper-shared.budget.first_buy_krw', 500_000), ('accounts', 'paper-shared.limits.daily_new_symbols', 3),
    ('lt', 'tuning.band', 0.06), ('lt', 'modules.marks_pendulum', True), ('lt', 'params.marks_pendulum.trim', 0.1), ('lt', 'enabled', False),
    ('book', 'actual.reservoir', '357870'), ('book', 'actual.yeo.park', True), ('book', 'actual.yeo.risk', 0.8),
    ('dca', 'actual.amount_krw', 200_000), ('dca', 'actual.tune.rate', 0.005), ('dca', 'actual.tune.mult.bear', 1.2)])
def test_a_planned_change_is_written_with_its_name_and_can_be_read_back(quiet, group, key, value):
    made = AP.make(quiet, S.ALL[group], {key: value}, '이유')
    assert made['level'] == 'W2' and S.ALL[group].read(quiet)[key] != value  # planning writes nothing
    done = AP.apply(quiet, made['plan'], agent='codex')
    assert done['status'] == 'applied' and S.ALL[group].read(quiet)[key] == value
    assert {row[3] for row in history(quiet)} == {'cli:codex 이유'}
    with pytest.raises(Refused, match='이미 처리'):
        AP.apply(quiet, made['plan'])


def test_one_module_of_a_book_without_its_own_set_keeps_the_engines_others(quiet):
    import vibe_longterm as L
    made = AP.make(quiet, S.ALL['book'], {'actual.modules.marks_pendulum': True}, '이유')
    AP.apply(quiet, made['plan'])
    saved = L.load_settings(quiet / 'vibe-longterm')
    assert saved['books']['actual']['modules'] == {**saved['modules'], 'marks_pendulum': True}


@pytest.mark.parametrize('address,raw', [('strategy.add_step_pct', '99'), ('strategy.reentry_guard', '0.5'), ('tunables.session_open', '23:00'),
                                         ('lt.tuning.band', '9'), ('dca.actual.amount_krw', '1'), ('accounts.live-1.budget.daily_loss_krw', '50000'),
                                         ('dca.actual.day', '2.5'), ('prefs.refresh_s', '7'), ('lt.enabled', 'maybe'), ('strategy.add_step_pct', 'nan')])
def test_a_value_out_of_range_is_refused_not_moved(home, address, raw):
    g, key = S.split(address)
    with pytest.raises(Refused):
        S.parse(S.spec_of(g, home, key), raw, address)


def test_cross_rules_and_unreal_saves_are_refused(quiet):
    with pytest.raises(Refused, match='익절'):
        plan(quiet, take_profit_half_pct=3.0, take_profit_all_pct=2.0)
    with pytest.raises(Refused, match='합계'):
        AP.make(quiet, S.ALL['lt'], {'weights.NDX': 0.5}, '이유')
    with pytest.raises(Refused, match='그대로 저장되지 않는'):
        AP.make(quiet, S.ALL['lt'], {'auto_live': True}, '이유')  # only with mode auto
    with pytest.raises(Refused, match='바뀌는 것이 없습니다'):
        plan(quiet, add_step_pct=0.7)
    with pytest.raises(Refused, match='계좌 하나씩'):
        AP.make(quiet, S.ALL['accounts'], {'live-1.auto': False, 'live-2.auto': False}, '이유')
    with pytest.raises(Refused, match='총자산'):
        AP.make(quiet, S.ALL['accounts'], {'live-1.budget.capital_krw': 2_000_000}, '이유')
    with pytest.raises(Refused, match='이유'):
        plan(quiet, reason=' ', add_step_pct=0.9)
    assert not (quiet / 'settings-history.sqlite3').exists()


@pytest.mark.parametrize('address', ['accounts.live-1.live_orders', 'accounts.live-1.hash', 'accounts.live-1.kind', 'accounts.paper-shared.match_live.account'])
def test_the_live_switch_and_identity_are_not_settings(home, address):
    with pytest.raises(Refused):
        g, key = S.split(address)
        S.spec_of(g, home, key)


@pytest.mark.parametrize('file,group', [('accounts.json', 'accounts'), ('shared-etf-paper/strategy-settings.json', 'strategy'),
                                        ('vibe-longterm/settings.json', 'lt'), ('tunables.json', 'tunables'), ('shared-etf-paper/ai-modes.json', 'ai'),
                                        ('dashboard-prefs.json', 'prefs')])
def test_an_unreadable_file_is_never_written_over(quiet, file, group):
    (quiet / file).write_text('{broken')
    with pytest.raises(Refused):
        S.ALL[group].read(quiet)
    assert (quiet / file).read_text() == '{broken'


def test_a_strategy_file_with_a_bad_value_is_refused(quiet):
    path = quiet / 'shared-etf-paper/strategy-settings.json'
    path.write_text(json.dumps({**json.loads(path.read_text()), 'add_step_pct': 99}))
    with pytest.raises(Refused):
        plan(quiet, stop_loss_pct=1.5)


def test_levels(home):
    level = lambda *rows: S.level(home, [list(r) for r in rows])  # noqa: E731
    assert level(('strategy.add_step_pct', 1, 2)) == ('H', ['live-1'])
    assert level(('accounts.paper-shared.auto', True, False)) == ('H', ['live-1'])
    assert level(('lt.tuning.band', 1, 2)) == ('H', ['live-2'])
    assert level(('prefs.theme', 1, 2)) == ('W2', [])
    assert level(('accounts.live-1.principal_krw', 1, 2)) == ('W2', [])
    write(home, {'paper-shared': account(False), 'live-1': account(live_orders=False), 'live-2': account(engine='longterm', live_orders=False)})
    assert level(('strategy.add_step_pct', 1, 2)) == ('W2', [])
    assert level(('accounts.live-1.rules.max_adds', 1, 2)) == ('H', ['live-1'])  # a live account's own rules always
    assert level(('lt.execute', False, True))[0] == 'H'
    write(home, {'paper-shared': account(False), 'live-1': account(live_orders=True, auto=False)})
    assert level(('strategy.add_step_pct', 1, 2)) == ('H', ['live-1'])  # automation may come back on
    (home / 'accounts.json').write_text('{')
    assert level(('strategy.add_step_pct', 1, 2))[0] == 'H'  # unknown counts as live


def test_dashboard_tunables_are_not_live(home):
    import tunables as T
    screen = next(k for k, v in T.TUNABLES.items() if v[3] == '대시보드')
    mirror = next(k for k, v in T.TUNABLES.items() if v[3] == '실계좌 미러')
    assert S.level(home, [[f'tunables.{screen}', 1, 2]])[0] == 'W2'
    assert S.level(home, [[f'tunables.{mirror}', 1, 2]])[0] == 'H'


def test_a_linked_change_is_part_of_the_plan(quiet):
    made = AP.make(quiet, S.ALL['match'], {'account': 'live-1', 'positions': 2, 'on': True}, '이유')
    AP.apply(quiet, made['plan'])
    made = AP.make(quiet, S.ALL['accounts'], {'live-1.budget.capital_krw': 600_000}, '이유')
    linked = {c['setting'] for c in made['changes'] if c.get('linked')}
    assert 'accounts.paper-shared.budget.first_buy_krw' in linked and made['level'] == 'H'


def test_a_live_change_needs_the_code_of_this_plan(home):
    made = plan(home, add_step_pct=0.9)
    assert made['level'] == 'H'
    for code in (None, '', 'AAAAAAAA'):
        with pytest.raises(OmniError) as caught:
            AP.apply(home, made['plan'], code)
        assert caught.value.code == 3
    box = Box()
    AP.approve(home, made['plan'], box)
    assert made['plan'] in box[0] and 'strategy.add_step_pct: 0.7 → 0.9' in box[0] and 'live-1' in box[0]
    other = plan(home, add_step_pct=1.1)
    AP.approve(home, other['plan'], Box())
    with pytest.raises(OmniError):
        AP.apply(home, other['plan'], box.code)
    done = AP.apply(home, made['plan'], box.code.lower())
    assert done['who'].startswith(f"cli:approved {made['plan']}") and STRATEGY.read(home)['add_step_pct'] == 0.9
    with pytest.raises(Refused):
        AP.apply(home, made['plan'], box.code)


def test_wrong_tries_expiry_and_a_newer_code(home, monkeypatch):
    made, box = plan(home, add_step_pct=0.9), Box()
    AP.approve(home, made['plan'], box)
    first = box.code
    AP.approve(home, made['plan'], box)
    with pytest.raises(OmniError):
        AP.apply(home, made['plan'], first)  # only the newest code
    for _ in range(2):
        with pytest.raises(OmniError):
            AP.apply(home, made['plan'], 'ZZZZZZZZ')
    with pytest.raises(OmniError):
        AP.apply(home, made['plan'], box.code)  # three tries are used
    AP.approve(home, made['plan'], box)
    monkeypatch.setattr(AP, 'now', lambda: datetime(2026, 11, 24, 18, 11, tzinfo=O.KST))
    with pytest.raises(OmniError):
        AP.apply(home, made['plan'], box.code)
    with pytest.raises(Refused, match='번까지'):
        AP.approve(home, made['plan'], box)
    assert STRATEGY.read(home)['add_step_pct'] == 0.7


def test_an_unsent_code_does_not_work_and_no_channel_refuses(home):
    made = plan(home, add_step_pct=0.9)
    with pytest.raises(Refused, match='대시보드'):
        AP.approve(home, made['plan'])  # no destination in this .env
    box = Box()
    def failing(text):
        box.append(text)
        return 0
    with pytest.raises(Refused):
        AP.approve(home, made['plan'], failing)
    with pytest.raises(OmniError):
        AP.apply(home, made['plan'], box.code)


def test_the_code_is_not_kept(home):
    made, box = plan(home, add_step_pct=0.9), Box()
    AP.approve(home, made['plan'], box)
    assert box.code.encode() not in (home.parent / 'cli/cli.sqlite3').read_bytes()
    assert len(box.code) == 8


def test_a_plan_on_other_contents_is_refused(quiet, monkeypatch):
    made = plan(quiet, add_step_pct=0.9)
    path = quiet / 'shared-etf-paper/strategy-settings.json'
    path.write_text(json.dumps({**json.loads(path.read_text()), 'stop_loss_pct': 1.9}))
    with pytest.raises(Refused, match='바뀌었습니다'):
        AP.apply(quiet, made['plan'])
    assert STRATEGY.read(quiet)['stop_loss_pct'] == 1.9


def test_a_plan_that_became_live_needs_a_new_plan(quiet):
    made = plan(quiet, add_step_pct=0.9)
    assert made['level'] == 'W2'
    write(quiet, {'paper-shared': account(False), 'live-1': account()})
    with pytest.raises(Refused, match='다시 계획'):
        AP.apply(quiet, made['plan'])


def test_a_plan_expires_and_the_market_needs_a_flag(quiet, monkeypatch):
    made = plan(quiet, add_step_pct=0.9)
    monkeypatch.setattr(AP, 'now', lambda: datetime(2026, 11, 24, 18, 31, tzinfo=O.KST))
    with pytest.raises(Refused, match='30분'):
        AP.apply(quiet, made['plan'])
    monkeypatch.setattr(AP, 'now', lambda: datetime(2026, 11, 25, 10, 0, tzinfo=O.KST))
    made = plan(quiet, add_step_pct=0.9)
    with pytest.raises(Refused, match='장중'):
        AP.apply(quiet, made['plan'])
    screen = AP.make(quiet, S.ALL['prefs'], {'refresh_s': 60}, '이유')
    assert AP.apply(quiet, screen['plan'])['status'] == 'applied'
    assert AP.apply(quiet, made['plan'], during_market=True)['status'] == 'applied'
    assert AP.in_market(datetime(2026, 11, 19, 8, 56, tzinfo=O.KST)) is False  # 수능일: an hour later
    assert AP.in_market(datetime(2026, 11, 28, 10, 0, tzinfo=O.KST)) is False


def test_two_applies_at_once_write_once(quiet):
    made, results = plan(quiet, add_step_pct=0.9), []
    def go():
        try:
            results.append(AP.apply(quiet, made['plan'])['status'])
        except OmniError as exc:
            results.append(type(exc).__name__)
    threads = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in threads], [t.join() for t in threads]
    assert results.count('applied') == 1
    assert len([r for r in history(quiet) if r[1] == 'add_step_pct']) == 1


def test_a_save_that_fails_midway_is_reported(quiet, monkeypatch):
    made = plan(quiet, add_step_pct=0.9)
    real = S.Strategy.write
    def broken(self, nh, changes, who):
        if who == 'plan':
            return real(self, nh, changes, who)
        raise OSError('disk')
    monkeypatch.setattr(S.Strategy, 'write', broken)
    with pytest.raises(OmniError, match='계획과 다릅니다'):
        AP.apply(quiet, made['plan'])
    assert AP.show(quiet, made['plan'])['status'] == 'failed'


def test_turning_live_orders_off_and_halt_need_the_code(home):
    import accounts as A
    with pytest.raises(Refused):
        AP.plan_action(home, 'live_off', '이유', account='paper-shared')
    made, box = AP.plan_action(home, 'live_off', '이유', account='live-1'), Box()
    with pytest.raises(OmniError) as caught:
        AP.apply(home, made['plan'])
    assert caught.value.code == 3 and A.load(home / 'accounts.json')['live-1']['live_orders'] is True
    AP.approve(home, made['plan'], box)
    AP.apply(home, made['plan'], box.code)
    assert A.load(home / 'accounts.json')['live-1']['live_orders'] is False
    from src.live.halt import halt_flag_set
    made = AP.plan_action(home, 'halt', '이유')
    AP.approve(home, made['plan'], box)
    assert not halt_flag_set('nhplug')
    AP.apply(home, made['plan'], box.code)
    assert halt_flag_set('nhplug')


def test_lists(quiet):
    import oversold
    import watchlist
    made = AP.make(quiet, S.ALL['watch'], {'paper-shared.add.005930': '삼성전자'}, '이유')
    AP.apply(quiet, made['plan'])
    assert watchlist.manual(quiet / 'shared-etf-paper/watchlist-manual.json')['add'] == {'005930': '삼성전자'}
    first = next(iter(oversold.universe(quiet / 'shared-etf-paper')))
    made = AP.make(quiet, S.ALL['universe'], {first: None}, '이유')
    AP.apply(quiet, made['plan'])
    assert first not in oversold.universe(quiet / 'shared-etf-paper')
    made = AP.make(quiet, S.ALL['dcalist'], {'069500': 'KODEX 200'}, '이유')
    AP.apply(quiet, made['plan'])
    assert S.ALL['dcalist'].read(quiet) == {'069500': 'KODEX 200'}
    assert {r[3] for r in history(quiet)} == {'cli:cli 이유'}


def test_output_masks_what_must_stay_inside(home, monkeypatch):
    number = '123-45-678901'
    (home.parent.parent / 'empty.env').write_text(f'ACCOUNT_NUMBER={number}\nJEV_API_KEY=abcdef123456\nREPORT_PAGES_REPO=owner/public\n')
    O.secrets.cache_clear()
    import hashlib
    digest = hashlib.sha256(b'12345678901').hexdigest()
    shown = json.dumps(O.clean({'hash': 'x', 'a': f'{number} 12345678901 abcdef123456 {digest} https://discord.com/api/webhooks/1/abc owner/public 999-88-777777'}))
    for word in (number, '12345678901', 'abcdef123456', digest, 'webhooks/1', '999-88-777777', '"hash"'):
        assert word not in shown
    assert 'owner/public' in shown
    O.secrets.cache_clear()


def test_tail_reads_the_newest_matching_rows_first(tmp_path):
    path = tmp_path / 'e.jsonl'
    lines = [json.dumps({'time': f'2026-11-{23 + i // 3000}T10:00:00', 'n': i, 'event': 'a' if i % 2 else 'b'}) for i in range(6000)]
    path.write_text('\n'.join(lines) + '\n{"broken\n', encoding='utf-8')
    assert [r['n'] for r in R.tail(path, 3)] == [5999, 5998, 5997]
    assert [r['n'] for r in R.tail(path, 2, '2026-11-24', lambda r: r['event'] == 'b')] == [5998, 5996]
    assert len(R.tail(path, 99999, '2026-11-24')) == 3000
    assert [r['n'] for r in R.tail(path, 1, '2026-11-23')] == [2999]


def test_approval_requests_are_limited_by_the_hour(home):
    for i in range(AP.HOURLY):
        AP.approve(home, plan(home, add_step_pct=1.0 + i / 10)['plan'], Box())
    with pytest.raises(Refused, match='한 시간'):
        AP.approve(home, plan(home, add_step_pct=3.0)['plan'], Box())


def test_an_account_named_otherwise_is_still_live(home):
    write(home, {'paper-shared': account(False), 'my-money': account(live_orders=False)})
    assert S.level(home, [['accounts.my-money.auto', True, False]]) == ('H', ['my-money'])
