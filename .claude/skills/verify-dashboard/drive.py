#!/usr/bin/env python3
"""Drive the real dashboard (streamlit_namuh/app.py) headlessly with Streamlit AppTest and write the proof as JSON.

  drive.py --doctor
  drive.py --tab 장기투자 --state lt_tab=시황·국면 --expect "국면 기준" [--out DIR] [--id longterm-market]

One run renders one top tab (and any sub-tab chosen through --state widget keys) exactly as the app does: it reads the
live ~/.vibe-trading data and makes the same read-only quote calls a person opening that tab would. It never clicks a
button. The JSON records exceptions, the texts a user sees, whether each --expect text appeared, and every file under
~/.vibe-trading that changed while rendering (rendering must not write; a write there is a finding). Exit code 1 when
the tab raised, an --expect text is missing, or a file changed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
APP = REPO / 'streamlit_namuh' / 'app.py'
RUNTIME = Path(os.environ.get('VIBE_TRADING_HOME', Path.home() / '.vibe-trading'))
EVIDENCE = RUNTIME / 'verify'
NOISE = ('.log', '-wal', '-shm', '-journal', '.lock')  # logs and sqlite side files change on any read-only open


def changed_since(start):
    out = []
    for path in RUNTIME.rglob('*'):
        try:
            if path.is_file() and path.stat().st_mtime >= start and not path.name.endswith(NOISE) and EVIDENCE not in path.parents:
                out.append(str(path.relative_to(RUNTIME)))
        except OSError:
            continue
    return sorted(out)


def doctor():
    import streamlit
    env = REPO / '.env'
    report = {'app': str(APP), 'app_exists': APP.exists(), 'streamlit': streamlit.__version__, 'python': sys.version.split()[0],
              'env_file_present': env.exists(), 'runtime': str(RUNTIME), 'runtime_exists': RUNTIME.exists()}
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report['app_exists'] and report['runtime_exists'] else 1


def drive(tab, state, expect, out, ident):
    from streamlit.testing.v1 import AppTest
    start = time.time() - 1
    at = AppTest.from_file(str(APP), default_timeout=180)
    at.session_state['main_tab'] = tab
    for key, value in state.items():
        at.session_state[key] = value
    at.run()
    seen = [m.value for m in at.markdown] + [c.value for c in at.caption] + [i.value for i in at.info] + \
           [w.value for w in at.warning] + [e.value for e in at.error] + [s.value for s in at.subheader]
    text = '\n'.join(seen)
    report = {'id': ident, 'time': datetime.now().astimezone().isoformat(timespec='seconds'), 'tab': tab, 'state': state,
              'seconds': round(time.time() - start, 1), 'exceptions': [e.value for e in at.exception],
              'expect': {e: e in text for e in expect}, 'tabs': [t.label for t in at.tabs],
              'subheaders': [s.value for s in at.subheader], 'warnings': [w.value for w in at.warning],
              'errors': [e.value for e in at.error], 'infos': [i.value for i in at.info][:10],
              'captions': [c.value for c in at.caption][:40], 'files_changed': changed_since(start)}
    out.mkdir(parents=True, exist_ok=True)
    name = (ident or tab).replace('/', '_').replace(' ', '_')
    path = out / f'{name}.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    ok = not report['exceptions'] and all(report['expect'].values()) and not report['files_changed']
    print(json.dumps({'ok': ok, 'evidence': str(path), 'seconds': report['seconds'], 'exceptions': report['exceptions'],
                      'expect': report['expect'], 'files_changed': report['files_changed']}, ensure_ascii=False, indent=1))
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--doctor', action='store_true')
    parser.add_argument('--tab', help='top tab label, e.g. 장기투자')
    parser.add_argument('--state', action='append', default=[], help='widget key=value set before the run, e.g. lt_tab=개요')
    parser.add_argument('--expect', action='append', default=[], help='text the user must see (repeatable)')
    parser.add_argument('--id', default='', help='feature id for the evidence file name')
    parser.add_argument('--out', type=Path, default=EVIDENCE / datetime.now().strftime('%Y%m%d-%H%M%S'))
    args = parser.parse_args()
    if args.doctor:
        return doctor()
    if not args.tab:
        parser.error('--tab is required unless --doctor')
    state = dict(s.split('=', 1) for s in args.state)
    return drive(args.tab, state, args.expect, args.out, args.id)


if __name__ == '__main__':
    sys.exit(main())
