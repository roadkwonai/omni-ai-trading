"""What has to hold for the native Windows port (docs/cli/windows-support.md W1), checked on any OS.

Windows itself is not run here: fcntl is made unimportable, msvcrt is a stand-in built on flock, and a refused folder
open / a file another process holds are imitated.
"""
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import config_store
from src import file_lock

REPO = Path(__file__).resolve().parents[1]
OURS = [p for folder in ('scripts', 'streamlit_namuh', 'agent-harness') for p in sorted((REPO / folder).rglob('*.py'))
        if not p.name.startswith(('test_', 'conftest')) and 'report_templates' not in p.parts]
OURS += [REPO / 'agent/src/file_lock.py', *sorted((REPO / 'agent/src/tools').glob('nh*.py'))]


def parsed():
    return [(p.relative_to(REPO), ast.parse(p.read_text(encoding='utf-8'))) for p in OURS]


# --- text encoding: a Korean Windows reads and writes cp949 unless a call says otherwise -----------------------------------

def _mode(call, position):
    node = next((k.value for k in call.keywords if k.arg == 'mode'), None) or (call.args[position] if len(call.args) > position else None)
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else ('r' if node is None else 'b')


def _text_call(call):
    """Whether this call reads or writes text in the platform's encoding unless `encoding` is given."""
    f = call.func
    if isinstance(f, ast.Name):
        return f.id == 'open' and 'b' not in _mode(call, 1)
    if not isinstance(f, ast.Attribute):
        return False
    owner = f.value.id if isinstance(f.value, ast.Name) else ''
    if f.attr == 'read_text':
        return not call.args
    if f.attr == 'write_text':
        return len(call.args) < 2
    if f.attr == 'fdopen':
        return 'b' not in _mode(call, 1)
    if f.attr == 'open' and owner not in ('os', 'webbrowser', 'gzip', 'tarfile', 'zipfile', 'Image', 'opener'):
        return set(_mode(call, 0)) <= set('rwaxt+')
    if f.attr in ('run', 'Popen', 'check_output') and owner == 'subprocess':
        return any(k.arg in ('text', 'universal_newlines') for k in call.keywords)
    return False


def test_every_text_file_and_child_output_names_its_encoding():
    missing = [f'{path}:{n.lineno}' for path, tree in parsed() for n in ast.walk(tree)
               if isinstance(n, ast.Call) and _text_call(n) and not any(k.arg == 'encoding' for k in n.keywords)]
    assert not missing, missing


def test_no_date_format_windows_does_not_have():
    assert not [str(p) for p in OURS if re.search(r'%-[a-zA-Z]', p.read_text(encoding='utf-8'))]


# --- locks ------------------------------------------------------------------------------------------------------------------

def test_only_the_shared_module_locks_files():
    assert not [str(path) for path, tree in parsed() if path.name != 'file_lock.py' for n in ast.walk(tree)
                if isinstance(n, (ast.Import, ast.ImportFrom)) and 'fcntl' in [getattr(n, 'module', None), *(a.name for a in n.names)]]


HOLD = 'import sys, time; from src import file_lock; f = open(sys.argv[1], "a"); file_lock.lock(f); print("held", flush=True); time.sleep(60)'


def holder(path):
    child = subprocess.Popen([sys.executable, '-c', HOLD, str(path)], stdout=subprocess.PIPE, text=True, encoding='utf-8',
                             env={**os.environ, 'PYTHONPATH': str(REPO / 'agent')})
    assert child.stdout.readline().strip() == 'held'
    return child


class Msvcrt:
    """msvcrt.locking as Windows documents it, on flock: the lock belongs to the open file, LK_NBLCK fails at once with
    EACCES, and it covers bytes from the file position."""
    LK_NBLCK, LK_UNLCK = 2, 0
    refusal = 13  # EACCES; EDEADLK (36) is the other answer Windows documents

    def __init__(self):
        self.offsets = []

    def locking(self, fd, mode, nbytes):
        import fcntl
        self.offsets.append(os.lseek(fd, 0, os.SEEK_CUR))
        try:
            fcntl.flock(fd, fcntl.LOCK_UN if mode == self.LK_UNLCK else fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise OSError(self.refusal, 'locked') from None


@pytest.fixture(params=['posix', 'windows', 'windows-deadlock'])
def backend(request, monkeypatch):
    if request.param == 'posix':
        return None
    import errno
    fake = Msvcrt()
    fake.refusal = errno.EDEADLK if request.param == 'windows-deadlock' else errno.EACCES
    monkeypatch.setattr(file_lock, 'fcntl', None)
    monkeypatch.setattr(file_lock, 'msvcrt', fake)
    return fake


def test_a_second_process_is_refused_at_once_and_let_in_when_the_first_dies(tmp_path, backend):
    path = tmp_path / 'runner.lock'
    child = holder(path)
    try:
        with path.open('a', encoding='utf-8') as mine, pytest.raises(BlockingIOError):
            file_lock.lock(mine, blocking=False)
    finally:
        child.kill()
        child.wait()
    with path.open('a', encoding='utf-8') as mine:
        file_lock.lock(mine, blocking=False)


def test_a_waiting_lock_gets_in_when_the_other_lets_go(tmp_path, backend):
    path = tmp_path / 'x.lock'
    child = holder(path)
    waits = []

    def nap(seconds):
        waits.append(seconds)
        child.kill()
        child.wait()
    if backend is None:
        child.kill()
        child.wait()
    with path.open('a', encoding='utf-8') as mine:
        file_lock.lock(mine, sleep=nap)
    assert bool(waits) == (backend is not None)


def test_unlock_and_close_both_release(tmp_path, backend):
    path = tmp_path / 'x.lock'
    with path.open('a', encoding='utf-8') as one, path.open('a', encoding='utf-8') as two:
        file_lock.lock(one, blocking=False)
        with pytest.raises(BlockingIOError):
            file_lock.lock(two, blocking=False)
        file_lock.unlock(one)
        file_lock.lock(two, blocking=False)
    with path.open('a', encoding='utf-8') as three:
        file_lock.lock(three, blocking=False)


def test_the_windows_lock_leaves_the_contents_and_the_position_alone(tmp_path, backend):
    path = tmp_path / 'budget.json'
    path.write_text('[1, 2]', encoding='utf-8')
    with path.open('r+', encoding='utf-8') as stream:
        file_lock.lock(stream)
        assert json.load(stream) == [1, 2]  # read from the start, as nh_budget does
        stream.seek(0)
        stream.truncate()
        stream.write('[3]')
        file_lock.unlock(stream)
    assert path.read_text(encoding='utf-8') == '[3]'
    if backend:
        assert set(backend.offsets) == {file_lock.FAR}  # not where other programs read


def test_another_error_is_not_taken_for_a_busy_lock(tmp_path, monkeypatch):
    class Broken(Msvcrt):
        def locking(self, fd, mode, nbytes):
            raise OSError(9, 'Bad file descriptor')
    monkeypatch.setattr(file_lock, 'fcntl', None)
    monkeypatch.setattr(file_lock, 'msvcrt', Broken())
    with (tmp_path / 'x').open('a', encoding='utf-8') as stream, pytest.raises(OSError) as caught:
        file_lock.lock(stream)
    assert not isinstance(caught.value, BlockingIOError)


def test_no_lock_backend_is_an_error_not_an_unlocked_run(tmp_path, monkeypatch):
    monkeypatch.setattr(file_lock, 'fcntl', None)
    monkeypatch.setattr(file_lock, 'msvcrt', None)
    with (tmp_path / 'x').open('a', encoding='utf-8') as stream, pytest.raises(OSError):
        file_lock.lock(stream, blocking=False)


def test_every_module_imports_without_the_posix_only_modules(tmp_path):
    """In a child: fcntl, pwd, grp, resource and termios cannot be imported; a module that needs an uninstalled package is not this test's."""
    names = sorted(p.stem for folder in ('scripts', 'streamlit_namuh') for p in (REPO / folder).glob('*.py')
                   if not p.stem.startswith(('test_', 'conftest')) and p.stem != 'app')
    code = '''
import importlib, json, logging, sys, types
logging.disable(logging.CRITICAL)
BLOCKED = {'fcntl', 'pwd', 'grp', 'resource', 'termios'}
class Block:
    def find_spec(self, name, path=None, target=None):
        if name in BLOCKED:
            raise ModuleNotFoundError(name, name=name)
for name in BLOCKED:
    sys.modules.pop(name, None)
sys.meta_path.insert(0, Block())
bad = []
for name in json.loads(sys.argv[1]):
    try:
        importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name in BLOCKED:
            bad.append(f'{name}: {exc.name}')
    except BaseException:
        pass
print('RESULT' + json.dumps(bad))
'''
    done = subprocess.run([sys.executable, '-c', code, json.dumps(names)], capture_output=True, text=True, encoding='utf-8', cwd=tmp_path,
                          env={**os.environ, 'HOME': str(tmp_path), 'PYTHONPATH': os.pathsep.join(str(REPO / d) for d in ('agent', 'scripts', 'streamlit_namuh'))})
    out = [line for line in done.stdout.splitlines() if line.startswith('RESULT')]
    assert out, done.stderr[-2000:]
    assert json.loads(out[0][6:]) == []


# --- settings files ---------------------------------------------------------------------------------------------------------

def test_a_settings_save_does_not_open_the_folder_on_windows(tmp_path, monkeypatch):
    real = os.open

    def refuse_folders(path, *args, **kw):
        if os.path.isdir(path):
            raise PermissionError(13, 'Permission denied')
        return real(path, *args, **kw)
    monkeypatch.setattr(config_store, 'WINDOWS', True)
    monkeypatch.setattr(os, 'open', refuse_folders)
    config_store.write_json(tmp_path / 'a.json', {'가': 1})
    import paper_ai_modes
    paper_ai_modes._write(tmp_path / 'b.json', {'x': 1})
    assert json.loads((tmp_path / 'a.json').read_text(encoding='utf-8')) == {'가': 1} and (tmp_path / 'b.json').exists()


def test_a_replace_waits_for_a_reader_and_gives_up_in_the_end(tmp_path, monkeypatch):
    real, tries = os.replace, []

    def busy(times):
        def replace(a, b):
            tries.append(1)
            if len(tries) <= times:
                raise PermissionError(13, 'used by another process')
            return real(a, b)
        return replace
    monkeypatch.setattr(config_store, 'WINDOWS', True)
    monkeypatch.setattr(os, 'replace', busy(3))
    config_store.write_json(tmp_path / 'a.json', {'x': 1}, sleep=lambda s: None)
    assert json.loads((tmp_path / 'a.json').read_text(encoding='utf-8')) == {'x': 1} and len(tries) == 4
    tries.clear()
    monkeypatch.setattr(os, 'replace', busy(1000))
    with pytest.raises(PermissionError):
        config_store.write_json(tmp_path / 'a.json', {'x': 2}, sleep=lambda s: None)
    assert json.loads((tmp_path / 'a.json').read_text(encoding='utf-8')) == {'x': 1} and [p.name for p in tmp_path.iterdir()] == ['a.json']
    tries.clear()
    monkeypatch.setattr(config_store, 'WINDOWS', False)
    with pytest.raises(PermissionError):
        config_store.write_json(tmp_path / 'a.json', {'x': 3})
    assert len(tries) == 1  # elsewhere a refusal is a refusal


# --- account settings that cannot be read: off, and never written over ------------------------------------------------------

@pytest.mark.parametrize('content', [b'{"accounts": {', '{"accounts": {"paper-shared": {"label": "모의"}}}'.encode('cp949'), b'[]', b'{"accounts": []}', b''])
def test_an_unreadable_account_file_means_off_and_keeps_the_file(tmp_path, content):
    import accounts
    path = tmp_path / 'accounts.json'
    path.write_bytes(content)
    found = accounts.load(path)
    assert found['paper-shared']['auto'] is False and found['paper-shared']['invalid'] is True
    with pytest.raises(ValueError):
        accounts.save_one('paper-shared', {'auto': True}, path)
    assert path.read_bytes() == content


def test_a_missing_account_file_is_a_new_install(tmp_path):
    import accounts
    path = tmp_path / 'accounts.json'
    assert accounts.load(path)['paper-shared']['auto'] is True and 'invalid' not in accounts.load(path)['paper-shared']
    accounts.save_one('paper-shared', {'auto': False}, path)
    assert accounts.load(path)['paper-shared']['auto'] is False


def test_the_engine_switch_refuses_an_unreadable_account_file(tmp_path):
    """Choosing "Jev off" read the unreadable file as already off and said it was applied (2026-11-24 review)."""
    import engine_ops
    (tmp_path / 'accounts.json').write_bytes(b'{')
    for mode in ('off', 'vibe', 'jev', 'both'):
        with pytest.raises(ValueError):
            engine_ops.set_mode(tmp_path, mode)
    assert not (tmp_path / 'vibe-longterm').exists()
