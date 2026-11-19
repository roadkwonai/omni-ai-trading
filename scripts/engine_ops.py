"""Which engine runs (dashboard 계좌·자동매매 → 운영 엔진, user request 2026-11-17).

- Jev real-time trading = the shared paper account's automation switch (accounts.json paper-shared.auto). Off: the
  runner takes no new entries or adds ('auto_off') but keeps managing what it holds (stops, take-profits, the close
  flatten), and the live mirrors, which follow its fills, buy nothing new either.
- vibe-trading long-term = vibe_longterm settings 'enabled'. Off: the daily run ends before deciding, trading or
  closing; the virtual book stays as it is.
Both values live in their existing settings files, written through config_store with history.
"""
from __future__ import annotations

from pathlib import Path

import accounts
import vibe_longterm

MODES = {'both': (True, True), 'jev': (True, False), 'vibe': (False, True), 'off': (False, False)}
LABELS = {'both': '둘 다 운영', 'jev': 'Jev만', 'vibe': '장기투자만', 'off': '모두 끔'}


def status(nhplug):
    """{'jev': bool, 'vibe': bool} from the two settings files under the nhplug runtime folder."""
    nhplug = Path(nhplug)
    return {'jev': accounts.load(nhplug / 'accounts.json')['paper-shared']['auto'] is True,
            'vibe': vibe_longterm.load_settings(nhplug / 'vibe-longterm')['enabled']}


def mode(nhplug):
    now = status(nhplug)
    return next(key for key, (jev, vibe) in MODES.items() if (jev, vibe) == (now['jev'], now['vibe']))


def set_mode(nhplug, key, who='dashboard'):
    """Apply one of MODES; only a switch that changes is written (each file keeps its own history row)."""
    if key not in MODES:
        raise ValueError(f'unknown mode: {key}')
    nhplug, (jev, vibe) = Path(nhplug), MODES[key]
    if accounts.load(nhplug / 'accounts.json')['paper-shared']['auto'] is not jev:
        accounts.save_one('paper-shared', {'auto': jev}, nhplug / 'accounts.json')
    if vibe_longterm.load_settings(nhplug / 'vibe-longterm')['enabled'] is not vibe:
        vibe_longterm.save_settings(nhplug / 'vibe-longterm', {'enabled': vibe}, who=who)
    return status(nhplug)
