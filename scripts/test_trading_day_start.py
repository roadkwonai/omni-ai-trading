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
