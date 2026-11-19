"""Which credential file the NH / Jev / OpenAI scripts read (copy `.env.example` to `.env`).

Order: `$VIBE_TRADING_ENV_FILE` → `<repo>/.env` → legacy `<repo>/../.envrc`. Callers parse it with
`dotenv_values(ENV)` and take only the keys they need; nothing is exported to `os.environ`, so broker keys never
reach the LLM agent process or child tools that did not ask for them. The file is never sourced as shell.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def env_file() -> Path:
    override = os.environ.get('VIBE_TRADING_ENV_FILE', '').strip()
    if override:
        return Path(override).expanduser()
    for path in (REPO / '.env', REPO.parent / '.envrc'):
        if path.is_file():
            return path
    return REPO / '.env'  # missing: callers report the absent key, and the message points at the right file


ENV = env_file()

