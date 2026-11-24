"""Run one service script with its output in a log file: the Windows Task Scheduler gives a task neither a log nor an environment.

    pythonw -X utf8 -u scripts/run_service.py --log LOG -- scripts/x.py ARGS...
    pythonw -X utf8 -u scripts/run_service.py --log LOG --module streamlit -- run app.py ...

The script runs in this process, so stopping the task stops the script. pythonw has no console and no standard streams.
"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main(argv):
    at = argv.index('--')
    options, rest = argv[:at], argv[at + 1:]
    log = Path(options[options.index('--log') + 1])
    module = options[options.index('--module') + 1] if '--module' in options else None
    log.parent.mkdir(parents=True, exist_ok=True)
    out = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    nothing = os.open(os.devnull, os.O_RDONLY)
    for source, target in ((nothing, 0), (out, 1), (out, 2)):  # child processes inherit them
        os.dup2(source, target)
    sys.stdout = open(1, 'w', encoding='utf-8', errors='replace', buffering=1, closefd=False)
    sys.stderr = open(2, 'w', encoding='utf-8', errors='replace', buffering=1, closefd=False)
    paths = [str(REPO / 'agent'), str(REPO / 'scripts')]
    os.environ.update(PYTHONUTF8='1', PYTHONPATH=os.pathsep.join(paths))
    sys.path[:0] = paths
    if module:
        sys.argv = [module, *rest]
        runpy.run_module(module, run_name='__main__', alter_sys=True)
    else:
        script = str(REPO / rest[0])
        sys.argv = [script, *rest[1:]]
        sys.path.insert(0, str(Path(script).parent))
        runpy.run_path(script, run_name='__main__')


if __name__ == '__main__':
    main(sys.argv[1:])
