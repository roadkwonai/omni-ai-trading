"""11-19 10:36 the shared paper ledger corrupted ('database disk image is malformed'): code opened and closed existing
SQLite files outside SQLite on every call. Closing any descriptor of a file drops the whole process's POSIX locks on it
(sqlite.org/howtocorrupt.html 2.2), so another process took itself for the last connection and deleted a WAL still in use."""
import os
import re
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
PATTERN = re.compile(r'os\.open\([^)]*O_CREAT[^)]*\)[\s;]*\n?\s*os\.close\(fd\)')


def test_private_file_creates_0600_and_never_reopens_an_existing_file(tmp_path):
    from src.tools.private_db import private_file
    path = tmp_path / 'x.sqlite3'
    private_file(path)
    assert path.exists() and path.stat().st_mode & 0o777 == 0o600
    os.chmod(path, 0o644)
    with patch('src.tools.private_db.os.open', side_effect=AssertionError('opened an existing file')):
        private_file(path)
    assert path.stat().st_mode & 0o777 == 0o600


def test_no_code_opens_and_closes_a_database_file_outside_sqlite():
    found = [str(p.relative_to(ROOT)) for base in ('agent/src', 'scripts') for p in (ROOT / base).rglob('*.py')
             if 'test' not in p.name and p.name != 'private_db.py' and PATTERN.search(p.read_text(encoding='utf-8'))]
    assert found == []
