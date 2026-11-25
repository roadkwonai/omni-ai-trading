"""The read-only runner unattended Claude tasks use (user 2026-11-18: an 08:50 report task waited 30 minutes for a Bash
approval). Only what is listed below, never an order or live-order script and never --execute, so one narrow permission
rule (Bash(scripts/ro *) in .claude/settings.local.json) lets an unattended task run it.

  scripts/ro [--out FILE] <script.py> [args...]     a listed script (PYTHONPATH set)
  scripts/ro sql <db under ~/.vibe-trading> "<SELECT|WITH|PRAGMA ...>"   read-only SQLite query, rows as JSON lines
  scripts/ro json <file> [key ...]                     its parts and their sizes, or the one part those keys name
  scripts/ro services                                  the registered services (pid, last exit, label)
  scripts/ro pytest <scripts/test_*.py|streamlit_namuh/test_*.py>...
  scripts/ro vibe-status                               whether the long-term engine is switched on

--out writes stdout to FILE (no shell redirect needed). It and every path argument of a listed script stay under
.scratch/, ~/.vibe-trading/nhplug/vibe-longterm/reports/ or reviews/, or ~/.vibe-trading/nhplug/shared-etf-paper/reviews/.
`scripts/ro` starts this file with the checkout's Python; on Windows `.venv\\Scripts\\python.exe scripts\\ro.py ...` is the same.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WINDOWS = os.name == 'nt'
HOME = str(Path.home())
FORBIDDEN = ('--execute', '--live', '--watch')
SCRIPTS = set('''longterm_report.py longterm_audit.py longterm_cases.py us_ideas.py intraday_audit.py
 oversold_outcomes.py oversold_research.py oversold_cases.py daily_improvement.py trading_journal.py journal_template.py
 premarket.py premarket_template.py preflight.py launchd_services.py nh_live_shadow.py etf_regime_observer.py market_movers.py
 jev_explain.py audit_incidents.py strategy_compare.py ipo_shadow.py fade_shadow.py api_load.py
 overnight_catalysts.py mirror_gap.py'''.split())
NEEDS = {'market_movers.py': '--once', 'ipo_shadow.py': '--summary', 'fade_shadow.py': '--summary', 'overnight_catalysts.py': '--summary'}
PATHS = ['agent', 'scripts', 'streamlit_namuh']


class Refused(Exception):
    pass


def slashes(path):
    return path.replace('\\', '/') if WINDOWS else path


def is_path(arg):
    return '..' in arg or '/' in slashes(arg) or (WINDOWS and bool(re.match(r'(--[^=]*=)?[A-Za-z]:', arg)))


def task_path(path):
    """Whether a task may name this path: never the engine's databases or settings (2026-11-19 review)."""
    path, home = slashes(path), slashes(HOME)
    if '..' in path:
        return False
    if WINDOWS:
        path, home = path.lower(), home.lower()
    data = f'{home}/.vibe-trading/nhplug'
    return any(path.startswith(f'{base}/') for base in (
        '.scratch', f'{data}/vibe-longterm/reports', f'{data}/vibe-longterm/reviews', f'{data}/shared-etf-paper/reviews'))


def forbidden(arg):
    """An option that is, starts or abbreviates a forbidden one (argparse takes any unique prefix)."""
    name = arg.split('=', 1)[0]
    return any(arg.startswith(word) or (len(name) > 2 and name.startswith('--') and word.startswith(name)) for word in FORBIDDEN)


def sql(out, db, query):
    import sqlite3
    path, home = Path(db).expanduser().resolve(), (Path.home() / '.vibe-trading').resolve()
    if home not in path.parents or not path.is_file():
        raise Refused('sql: the database must be a file under ~/.vibe-trading')
    if (query.split(None, 1) or [''])[0].upper() not in ('SELECT', 'WITH', 'PRAGMA') or ';' in query.strip().rstrip(';'):
        raise Refused('sql: one SELECT, WITH or PRAGMA statement only')
    con = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    for row in con.execute(query):
        print(json.dumps(dict(row), ensure_ascii=False, default=str), file=out)
    return 0


def show_json(out, path, keys):
    """One part of a large JSON file: Read cannot pick one, and the `python3 -c` used instead waits for an approval."""
    if not task_path(path) or not Path(path).is_file():
        raise Refused(f'json: {path}: a file under .scratch/ or the report folders')
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except ValueError:
        raise Refused(f'json: {path} is not JSON') from None
    for key in keys:
        try:
            value = value[int(key)] if isinstance(value, list) else value[key]
        except (KeyError, IndexError, ValueError, TypeError):
            has = ', '.join(value) if isinstance(value, dict) else f'{len(value)} items' if isinstance(value, list) else 'a value'
            raise Refused(f'json: no {key}; there is: {has}') from None
    if not keys and isinstance(value, dict):
        for key, part in value.items():
            print(f'{key}\t{len(json.dumps(part, ensure_ascii=False))}', file=out)
        return 0
    print(json.dumps(value, ensure_ascii=False, indent=1), file=out)
    return 0


def services(out, states=None):
    import os_services
    try:
        found = (states or os_services.states)()
    except os_services.Unknown as exc:
        raise Refused(f'services: the service manager cannot be asked ({exc})') from None
    for name, row in sorted(found.items()):
        print(f"{row['pid'] or ('running' if row['running'] else '-')}\t{row['last_exit']}\t{os_services.LABEL}{name}", file=out)
    return 0


def pytest_args(args):
    """No other option: --basetemp deletes a folder, -p loads a module, -c/--rootdir move the config. At least one test file."""
    previous, files = '', 0
    for arg in args:
        if previous == '-k':
            previous = ''
            continue
        if previous == '-p':
            if arg != 'no:cacheprovider':
                raise Refused('pytest -p takes only no:cacheprovider')
            previous = ''
            continue
        name = slashes(arg)
        test = any(re.fullmatch(rf'{folder}/test_[^/]*\.py', name) for folder in ('scripts', 'streamlit_namuh'))
        if '..' in arg or not (test or arg in ('-q', '-x', '-v', '-k', '-p', '-pno:cacheprovider')):
            raise Refused('pytest runs only scripts/test_*.py or streamlit_namuh/test_*.py with -q -x -v -k -p no:cacheprovider')
        files += test
        previous = arg
    if previous in ('-k', '-p') or not files:
        raise Refused('pytest needs at least one test file')
    return args


def script_args(name, args):
    if name not in SCRIPTS:
        raise Refused(f'not allowed: {name}')
    if name in NEEDS and NEEDS[name] not in args:
        raise Refused(f'{name} needs {NEEDS[name]}')
    for arg in args:  # --reports, --incidents-db, --out, --root, --db ... (also --opt=PATH and abbreviations)
        if is_path(arg) and not task_path(re.sub(r'^--[^=]*=', '', arg)):
            raise Refused(f'{arg}: paths only under .scratch/ or the report folders')
    return [str(REPO / 'scripts' / name), *args]


def plan(argv):
    """(output file or None, function(stream) or a child command) of one call; Refused otherwise."""
    target = None
    if argv[:1] == ['--out']:
        if len(argv) < 3:
            raise Refused('--out FILE needs a command')
        target, argv = argv[1], argv[2:]
        if not task_path(target):
            raise Refused('--out must be under .scratch/, vibe-longterm/reports|reviews/ or shared-etf-paper/reviews/')
    for arg in argv:
        if forbidden(arg):
            raise Refused(f'{arg} is not allowed')
    name, args = (argv[0], argv[1:]) if argv else ('', [])
    if name == 'vibe-status':
        return target, ['-c', "import engine_ops, pathlib; print(engine_ops.status(pathlib.Path.home() / '.vibe-trading/nhplug')['vibe'])"]
    if name == 'json':
        if not args:
            raise Refused('json: a file')
        return target, lambda out: show_json(out, args[0], args[1:])
    if name == 'services':
        return target, services
    if name == 'sql':
        if len(args) < 2:
            raise Refused('sql: a database and a query')
        return target, lambda out: sql(out, args[0], args[1])
    if name == 'pytest':
        return target, ['-m', 'pytest', *pytest_args(args)]
    if fnmatch.fnmatchcase(name, '*.py'):
        return target, script_args(name, args)
    raise Refused(f"not allowed: {name or '<none>'} (scripts: {' '.join(sorted(SCRIPTS))}; or sql, json, services, pytest, vibe-status)")


def main(argv):
    os.chdir(REPO)
    sys.path[:0] = [str(REPO / d) for d in PATHS]
    try:
        target, work = plan(argv)
        if target:
            Path(target).parent.mkdir(parents=True, exist_ok=True)
        stream = open(target, 'w', encoding='utf-8') if target else sys.stdout
        try:
            if callable(work):
                return work(stream)
            stream.flush()
            env = {**os.environ, 'PYTHONPATH': os.pathsep.join(PATHS), **({'PYTHONUTF8': '1'} if WINDOWS else {})}
            if WINDOWS:
                return subprocess.run([sys.executable, *work], stdout=stream if target else None, cwd=REPO, env=env).returncode
            if target:
                os.dup2(stream.fileno(), 1)
            os.execve(sys.executable, [sys.executable, *work], env)  # become the script, so stopping ro stops it
        finally:
            if target:
                stream.close()
    except Refused as exc:
        print(f'ro: {exc}', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    if WINDOWS:
        sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main(sys.argv[1:]))
