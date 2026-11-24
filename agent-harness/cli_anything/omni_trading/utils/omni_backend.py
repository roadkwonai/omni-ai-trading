"""Where the real software is: this checkout's scripts and the runtime folder. The CLI calls them, it never re-implements them."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ..core.output import Refused


def repo() -> Path:
    given = os.environ.get('OMNI_REPO', '').strip()
    for base in ([Path(given).expanduser()] if given else Path(__file__).resolve().parents):
        if (base / 'scripts' / 'nh_paper_isolated.py').is_file() and (base / 'streamlit_namuh').is_dir():
            return base
    raise Refused('Omni 저장소를 찾지 못했습니다. 저장소에서 `.venv/bin/pip install -e agent-harness`로 설치하거나 OMNI_REPO를 지정하세요.')


def load() -> Path:
    """Put the checkout on the import path (idempotent) and return it."""
    base = repo()
    for name in ('streamlit_namuh', 'scripts', 'agent'):
        path = str(base / name)
        if path not in sys.path:
            sys.path.insert(0, path)
    return base


def nhplug() -> Path:
    """The runtime folder, only when every settings module points at the same one: VIBE_TRADING_HOME alone moves
    tunables and the history but not accounts.json or the long-term settings (measured 2026-11-24)."""
    load()
    import accounts
    import config_store
    import tunables
    import vibe_longterm
    root = Path.home() / '.vibe-trading' / 'nhplug'
    seen = {'accounts.json': accounts.FILE.parent, 'tunables.json': tunables.path().parent,
            'settings-history': config_store.history_path().parent, 'vibe-longterm': vibe_longterm.ROOT.parent}
    other = [name for name, path in seen.items() if path.resolve() != root.resolve()]
    if other:
        raise Refused(f'런타임 폴더가 서로 다릅니다({", ".join(other)}). VIBE_TRADING_* 환경변수를 지우거나 HOME으로 바꾸세요.')
    return root


def readonly():
    """The NH connection that can only read; one whose endpoint could order is refused."""
    from src.config.loader import load_agent_config
    from src.tools.mcp import MCPServerAdapter
    from nh_live_broker import require_readonly
    adapter = MCPServerAdapter('nhplug_readonly', load_agent_config().mcp_servers['nhplug_readonly'])
    require_readonly(adapter)
    return adapter


def script(name, *args, timeout=180):
    """Run one read-only script of the checkout; (exit code, stdout, stderr)."""
    base = load()
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join(str(base / d) for d in ('agent', 'scripts', 'streamlit_namuh')), 'PYTHONUTF8': '1'}
    try:
        done = subprocess.run([sys.executable, str(base / 'scripts' / name), *args], cwd=base, env=env, capture_output=True,
                              text=True, encoding='utf-8', errors='replace', timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise Refused(f'{name}이 {timeout}초 안에 끝나지 않았습니다.', code=6) from None
    return done.returncode, done.stdout, done.stderr
