"""Read-only access for dashboards and reports: `with ro_connect(path) as db:` opens a SQLite file without write access
and closes it afterwards (a plain `with sqlite3.connect(...)` only ends the transaction and leaves the file open), and
`day_rows` reads one day's lines of a JSON-lines log."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path


def ro_connect(path, timeout=5.0):
    # as_uri() escapes '?' and '#' in the path, which a hand-written 'file:{path}?mode=ro' would misread
    return closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=timeout))


def day_rows(path, day):
    """(rows whose own "time" falls on `day` (YYYY-MM-DD), lines of that day that did not parse); (None, 0) when the file
    cannot be read. A line that only mentions the day (a daily bar, a news date) is not that day's row."""
    rows, bad = [], 0
    try:
        with Path(path).open(encoding='utf-8') as stream:
            for line in stream:
                if f'"{day}T' not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    bad += 1
                    continue
                if isinstance(row, dict) and str(row.get('time', '')).startswith(day):
                    rows.append(row)
    except OSError:
        return None, 0
    return rows, bad
