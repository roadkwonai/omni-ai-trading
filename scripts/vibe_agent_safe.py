"""Run the vibe-trading CLI for unattended advice with no shell, code, file, order or broker tools.

The stock CLI always enables shell tools (agent/cli/_legacy.py builds the registry with include_shell_tools=True).
An unattended advisor reading news must not be able to run commands, read credential files or reach a broker,
so this launcher wraps build_registry before the CLI imports it. Usage: same arguments as `vibe-trading`.
"""
from __future__ import annotations

import re
import sys

import src.tools as tools

# Allowlist: read-only tools the Korean intraday scoring task uses. Anything that runs code, reads local files, spawns
# agents, schedules work, edits skills/memory or touches a broker is dropped. Every allowed tool's schema is sent on
# every model call, so tools this task never uses (US/China filings, options, fund flows, ...) are left out too: 38
# tools (~50,000 schema characters) cost most of a run's tokens (review 2026-11-12). The vibe-trading skills the task
# relies on (technical-basic) are computed by vibe_advisor and passed in, so load_skill is not needed here.
ALLOWED = re.compile(r'get_market_data|get_stock_news|technical_indicators|krx_daily_snapshot|opendart|sentiment|'
                     r'search_symbol|web_search|compact')
_build = tools.build_registry


def safe_registry(*args, **kwargs):
    kwargs['include_shell_tools'] = False
    registry = _build(*args, **kwargs)
    for name in [n for n in registry.tool_names if not ALLOWED.fullmatch(n)]:
        registry._tools.pop(name, None)  # ToolRegistry keeps tools in a plain dict; no public removal API
    return registry


tools.build_registry = safe_registry

if __name__ == '__main__':
    from cli import main
    sys.exit(main(sys.argv[1:]))
