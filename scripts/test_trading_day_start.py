import trading_day_start as d


def test_a_runner_that_dies_at_start_is_started_again():
    # 2026-11-18 08:12: the runner stopped at start on a transient NH account-list failure and nothing restarted it
    alive = iter([False, False, True])
    kicks = []
    started = d.start_services({'a': False, 'b': True}, kick=kicks.append, alive=lambda label: next(alive), wait=lambda s: None)
    assert kicks == ['a', 'a', 'a'] and started == {'a': 3}


def test_a_runner_that_stays_up_is_started_once_and_attempts_are_bounded():
    kicks = []
    assert d.start_services({'a': False}, kick=kicks.append, alive=lambda label: True, wait=lambda s: None) == {'a': 1}
    kicks.clear()
    assert d.start_services({'a': False}, kick=kicks.append, alive=lambda label: False, wait=lambda s: None) == {'a': d.START_ATTEMPTS}
    assert len(kicks) == d.START_ATTEMPTS


def test_unfinished_intents_reads_a_ledger_whose_folder_name_has_uri_characters(tmp_path):
    import sqlite3
    root = tmp_path / 'runtime #2'  # a hand-written 'file:{path}?mode=ro' misreads '#' and '?'
    root.mkdir()
    with sqlite3.connect(root / 'orders.sqlite3') as db:
        db.execute('CREATE TABLE intents (id TEXT, status TEXT)')
        db.executemany('INSERT INTO intents VALUES (?, ?)', [('a', 'submitted'), ('b', 'terminal')])
    assert d.unfinished_intents(root) == 1


def test_only_the_runner_s_own_state_decides_the_start(monkeypatch, tmp_path):
    """2026-11-24 review: a helper launchd could not answer for kept the paper runner from starting at 08:55."""
    import json
    import os_services as S
    kicked = []
    down = {'registered': True, 'running': False, 'pid': None, 'last_exit': 0, 'started': None}

    def state(name):
        if name == 'shared-etf-regime':
            raise S.Unknown('x')
        return {**down, 'running': name in kicked}
    monkeypatch.setattr(S, 'state', state)
    monkeypatch.setattr(S, 'start', lambda name: kicked.append(name) or True)
    monkeypatch.setattr(d, 'ROOT', tmp_path)
    monkeypatch.setattr(d, 'START_CHECK_SECONDS', 0)
    monkeypatch.setattr(d, 'ASK_WAIT', 0)
    monkeypatch.setattr(d, 'trading_day', lambda now: (True, 'trading_day'))
    d.main()
    record = json.loads((tmp_path / 'trading-day-start.jsonl').read_text())
    assert record['decision'] == 'start' and kicked == ['shared-etf-paper', 'shared-etf-learning'] and all(record['running_after'].values())


def test_one_slow_answer_about_the_runner_is_asked_again(monkeypatch):
    import os_services as S
    answers = iter([S.Unknown('x'), S.Unknown('x'), {'registered': True, 'running': False}])

    def state(name):
        got = next(answers)
        if isinstance(got, Exception):
            raise got
        return got
    monkeypatch.setattr(S, 'state', state)
    waits = []
    assert d.asked('shared-etf-paper', wait=waits.append) is False and waits == [d.ASK_WAIT] * 2
    monkeypatch.setattr(S, 'state', lambda name: (_ for _ in ()).throw(S.Unknown('x')))
    assert d.asked('shared-etf-paper', wait=waits.append) is d.UNKNOWN
