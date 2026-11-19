import json
import sqlite3
import tarfile

import pytest

import state_sync as ss


def make_runtime(tmp_path):
    root = tmp_path / 'rt'
    nh = root / 'nhplug'
    (nh / 'tokens').mkdir(parents=True)
    (nh / 'tokens' / 'abc.json').write_text('{"access_token": "x"}')
    (nh / 'runner.lock').write_text('1')
    (nh / 'broker_token.json').write_text('{}')
    (nh / 'accounts.json').write_text(json.dumps({'accounts': {'live-1': {
        'label': '실계좌 1', 'masked': '202-01-****46', 'hash': 'a' * 64, 'budget': {'capital_krw': 1000000}, 'live_orders': False}}}))
    (nh / 'events.jsonl').write_text('{"a": 1}\n')
    db = sqlite3.connect(nh / 'ledger.sqlite3')
    db.execute('create table t(x)')
    db.executemany('insert into t values (?)', [(i,) for i in range(50)])
    db.commit()
    db.close()
    return root


def test_sanitize_drops_account_identity():
    out = ss.sanitize({'accounts': {'live-1': {'label': 'L', 'masked': '1', 'hash': 'b' * 64, 'no': '12345678901', 'budget': {'x': 5}}}})
    assert out == {'accounts': {'live-1': {'label': 'L', 'no': None, 'budget': {'x': 5}}}}
    assert 'a' * 16 not in json.dumps(ss.sanitize({'a' * 16: 1, 'k': 'v'}))


def test_export_snapshot_manifest_and_exclusions(tmp_path):
    root = make_runtime(tmp_path)
    archive, manifest = ss.export_archive(root, tmp_path / 'out')
    paths = {f['path'] for f in manifest['files']}
    assert paths == {'nhplug/accounts.json', 'nhplug/events.jsonl', 'nhplug/ledger.sqlite3'}  # no tokens, locks, *token*
    assert ss.verify(archive) == []
    with tarfile.open(archive) as tar:
        names = tar.getnames()
        assert not any('token' in n or n.endswith('.lock') for n in names)
        tar.extract('runtime/nhplug/ledger.sqlite3', tmp_path / 'x', filter='data')
    assert sqlite3.connect(tmp_path / 'x' / 'runtime/nhplug/ledger.sqlite3').execute('select count(*) from t').fetchone() == (50,)
    written = ss.export_settings(root, tmp_path / 'settings')
    assert written == ['accounts.json']
    text = (tmp_path / 'settings' / 'accounts.json').read_text()
    assert 'hash' not in text and 'masked' not in text and '실계좌 1' in text


def test_retention(tmp_path):
    root = make_runtime(tmp_path)
    out = tmp_path / 'out'
    out.mkdir()
    for i in range(5):
        (out / f'{ss.PREFIX}2020010{i}-0000.tar.gz').write_bytes(b'x')
    ss.export_archive(root, out, keep=3)
    assert len(ss.archives(out)) == 3


def test_verify_catches_bad_checksum(tmp_path):
    root = make_runtime(tmp_path)
    archive, manifest = ss.export_archive(root, tmp_path / 'out')
    bad = tmp_path / 'bad.tar.gz'
    with tarfile.open(archive) as src, tarfile.open(bad, 'w:gz') as dst:
        for m in src:
            data = src.extractfile(m).read()
            if m.name.endswith('events.jsonl'):
                data = b'tampered\n'
                m.size = len(data)
            import io
            dst.addfile(m, io.BytesIO(data))
    assert any('checksum' in p for p in ss.verify(bad))


def test_import_refuses_while_services_run(tmp_path, monkeypatch):
    root = make_runtime(tmp_path)
    archive, _ = ss.export_archive(root, tmp_path / 'out')
    monkeypatch.setattr(ss, 'launchctl_list', lambda: '123\t0\tcom.vibetrading.shared-etf-paper\n456\t0\tcom.vibetrading.shared-etf-dashboard\n')
    with pytest.raises(SystemExit, match='shared-etf-paper'):
        ss.import_archive(archive, root)
    assert ss.running_services(launchctl_text()) == ['com.vibetrading.shared-etf-paper']


def launchctl_text():
    return '123\t0\tcom.vibetrading.shared-etf-paper\n456\t0\tcom.vibetrading.shared-etf-dashboard\n-\t0\tcom.vibetrading.market-movers\n'


def test_import_keeps_pre_restore_copy(tmp_path, monkeypatch):
    root = make_runtime(tmp_path)
    archive, _ = ss.export_archive(root, tmp_path / 'out')
    (root / 'nhplug' / 'events.jsonl').write_text('changed\n')
    (root / 'nhplug' / 'ledger.sqlite3-wal').write_text('stale')
    keep = root / 'nhplug' / 'untouched.txt'
    keep.write_text('keep')
    monkeypatch.setattr(ss, 'launchctl_list', lambda: '-\t0\tcom.vibetrading.market-movers\n')
    result = ss.import_archive(archive, root)
    assert (root / 'nhplug' / 'events.jsonl').read_text() == '{"a": 1}\n'
    aside = root / result['aside'].rsplit('/', 1)[-1]
    assert (aside / 'nhplug' / 'events.jsonl').read_text() == 'changed\n'
    assert (aside / 'nhplug' / 'ledger.sqlite3-wal').exists() and not (root / 'nhplug' / 'ledger.sqlite3-wal').exists()
    assert keep.read_text() == 'keep'


def test_tasks_export_keeps_schedule_and_flags_secrets(tmp_path):
    src, dst = tmp_path / 'src', tmp_path / 'dst'
    (src / 'a').mkdir(parents=True)
    (src / 'a' / 'SKILL.md').write_text('---\nname: a\ndescription: desc\n---\nkey ' + 'f' * 40)
    dst.mkdir()
    (dst / 'manifest.json').write_text(json.dumps([{'taskId': 'a', 'cron': '0 9 * * 1-5'}]))
    entries, warnings = ss.tasks_export(src, dst, stores=[])  # no app store readable: the manifest keeps its schedule
    assert entries[0]['cron'] == '0 9 * * 1-5' and entries[0]['description'] == 'desc' and warnings


def test_long_term_engine_state_is_backed_up(tmp_path):
    import vibe_longterm as L
    lt = tmp_path / 'nhplug' / 'vibe-longterm'
    L.save_settings(lt, L.load_settings(lt), who='test')
    L.put_state(L.connect(lt), plan={'day': '2026-11-18'})
    (lt / 'reports').mkdir()
    (lt / 'reports' / 'strategy-2026-11-18.md').write_text('# r')
    rel = {p.relative_to(tmp_path).as_posix() for p in ss.candidates(tmp_path)}
    assert {'nhplug/vibe-longterm/longterm.sqlite3', 'nhplug/vibe-longterm/settings.json',
            'nhplug/vibe-longterm/reports/strategy-2026-11-18.md'} <= rel
    assert 'nhplug/vibe-longterm/settings.json' in {p.relative_to(tmp_path).as_posix() for p in ss.settings_files(tmp_path)}


def test_state_archive_keeps_the_kill_switches(tmp_path):
    """The global live/HALT and live/<broker>/HALT (19 brokers stay halted, AGENTS.md) move with the state."""
    root = make_runtime(tmp_path)
    (root / 'live' / 'alpaca').mkdir(parents=True)
    (root / 'live' / 'alpaca' / 'HALT').write_text('{"by": "cli"}')
    (root / 'live' / 'HALT').write_text('{"by": "cli"}')
    (root / 'live' / 'nh-activation.json').write_text('{}')
    (root / 'live' / 'alpaca' / 'token.json').write_text('{}')
    taken = {p.relative_to(root).as_posix() for p in ss.candidates(root)}
    assert {'live/alpaca/HALT', 'live/HALT', 'live/nh-activation.json'} <= taken
    assert 'live/alpaca/token.json' not in taken


def test_task_report_names_tasks_that_lost_their_schedule(tmp_path):
    for task in ('daily', 'once', 'lost', 'stray'):
        (tmp_path / task).mkdir()
    (tmp_path / 'manifest.json').write_text(json.dumps([
        {'taskId': 'daily', 'cron': '0 9 * * 1-5', 'schedule': None}, {'taskId': 'once', 'cron': None, 'schedule': '2026-11-19T16:10:00+09:00'},
        {'taskId': 'lost', 'cron': None, 'schedule': None}]))
    assert ss.task_problems(tmp_path) == {'tasks_without_schedule': ['lost'], 'tasks_not_in_manifest': ['stray']}


def test_repo_task_manifest_keeps_every_schedule():
    """ops-bootstrap recreates the tasks from this manifest; a task with no cron and no time comes back as a question."""
    assert ss.task_problems(ss.TASKS_DST) == {'tasks_without_schedule': [], 'tasks_not_in_manifest': []}


def test_tasks_export_takes_schedules_from_the_app_and_drops_deleted_tasks(tmp_path):
    """user 2026-11-19: another Claude account must get every task back on the same schedule. The schedule lives in the
    Claude app's own store, so the backup reads it (cron, one-time fireAt, enabled, title, permission mode)."""
    src, dst, store = tmp_path / 'src', tmp_path / 'dst', tmp_path / 'app' / 'scheduled-tasks.json'
    for task in ('daily', 'once', 'deleted'):
        (src / task).mkdir(parents=True)
        (src / task / 'SKILL.md').write_text(f'---\nname: {task}\ndescription: {task} desc\n---\nbody')
    (dst / 'deleted').mkdir(parents=True)
    (dst / 'deleted' / 'SKILL.md').write_text('old')
    (dst / 'manifest.json').write_text(json.dumps([{'taskId': 'deleted', 'cron': '0 9 * * 1-5'}]))
    store.parent.mkdir(parents=True)
    store.write_text(json.dumps({'scheduledTasks': [
        {'id': 'daily', 'displayName': '매일', 'cronExpression': '20 9-15 * * 1-5', 'enabled': True, 'permissionMode': 'bypassPermissions'},
        {'id': 'once', 'displayName': '한 번', 'fireAt': 1796020800000, 'enabled': False}]}))
    entries, _ = ss.tasks_export(src, dst, stores=[store])
    got = {e['taskId']: e for e in entries}
    assert set(got) == {'daily', 'once'} and not (dst / 'deleted').exists()  # deleted in the app: gone from the backup
    assert (got['daily']['cron'], got['daily']['title'], got['daily']['enabled'], got['daily']['permission_mode']) == \
        ('20 9-15 * * 1-5', '매일', True, 'bypassPermissions')
    assert got['once']['cron'] is None and got['once']['schedule'] == '2026-11-30T15:40:00+09:00' and got['once']['enabled'] is False
    assert ss.task_problems(dst) == {'tasks_without_schedule': [], 'tasks_not_in_manifest': []}


def _task_dirs(root, names):
    for t in names:
        (root / t).mkdir(parents=True)
        (root / t / 'SKILL.md').write_text(f'---\nname: {t}\ndescription: {t}\n---\nprompt {t}\n')


def _backed_up(dst):
    _task_dirs(dst, ('a', 'b'))
    (dst / 'manifest.json').write_text(json.dumps([{'taskId': 'a', 'cron': '0 9 * * 1-5'}, {'taskId': 'b', 'cron': '0 10 * * 1-5'}]))


@pytest.mark.parametrize('store_body', [None, {'scheduledTasks': []}, {'tasks': [{'id': 'a'}]}])
def test_tasks_export_never_prunes_the_backup_without_a_store_listing_this_repos_tasks(tmp_path, store_body):
    """review5: ops/ is not in git, so a prune is permanent. A new machine (no store, nothing installed yet), a new account
    (store with no tasks) and an unknown store format must all leave every backed-up task and its schedule in place."""
    src, dst = tmp_path / 'src', tmp_path / 'dst'
    src.mkdir()
    _backed_up(dst)
    stores = []
    if store_body is not None:
        stores = [tmp_path / 'scheduled-tasks.json']
        stores[0].write_text(json.dumps(store_body))
    entries, _ = ss.tasks_export(src, dst, stores=stores)
    assert {e['taskId']: e['cron'] for e in entries} == {'a': '0 9 * * 1-5', 'b': '0 10 * * 1-5'}
    assert (dst / 'a' / 'SKILL.md').exists() and (dst / 'b' / 'SKILL.md').exists()


def test_tasks_export_skips_other_projects_and_reads_only_the_newest_store(tmp_path):
    """~/.claude/scheduled-tasks is shared by every project: another repo's task (its cwd in the store) is never copied
    in. An older account's store on this machine must not revive a task the current account deleted."""
    src, dst = tmp_path / 'src', tmp_path / 'dst'
    _task_dirs(src, ('a', 'b', 'other'))
    old, new = tmp_path / 'old.json', tmp_path / 'new.json'
    old.write_text(json.dumps({'scheduledTasks': [{'id': 'a', 'cronExpression': '0 9 * * *'}, {'id': 'b', 'cronExpression': '0 10 * * *'}]}))
    new.write_text(json.dumps({'scheduledTasks': [{'id': 'a', 'cronExpression': '0 9 * * *', 'cwd': str(ss.REPO)},
                                                  {'id': 'other', 'cronExpression': '0 9 * * *', 'cwd': '/Users/x/other-repo'}]}))
    entries, _ = ss.tasks_export(src, dst, stores=[old, new])  # app_stores() sorts oldest first
    assert [e['taskId'] for e in entries] == ['a'] and not (dst / 'other').exists() and not (dst / 'b').exists()


def test_export_still_writes_the_archive_when_the_task_manifest_is_unreadable(tmp_path, monkeypatch):
    runtime, out, dst = make_runtime(tmp_path), tmp_path / 'state', tmp_path / 'dst'
    dst.mkdir()
    (dst / 'manifest.json').write_text('{not json')
    real = ss.tasks_export
    monkeypatch.setattr(ss, 'tasks_export', lambda: real(tmp_path / 'src', dst, []))
    assert ss.main(['export', '--runtime', str(runtime), '--out', str(out)]) == 0
    assert list((out / 'backups').glob('*.tar.gz'))
