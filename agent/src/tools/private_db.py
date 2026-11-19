"""Owner-only (0600) database files without touching an existing one outside SQLite.

Closing any descriptor of a file drops the whole process's POSIX advisory locks on it, including those SQLite holds
for other open connections (sqlite.org/howtocorrupt.html 2.2). A database that was os.open'ed and closed on every
call let another process take itself for the last connection and delete a WAL still in use: the shared paper ledger
corrupted on 2026-11-19 10:36. So an existing file only gets its mode fixed; only a missing one is created here.
"""
from __future__ import annotations

import os
from pathlib import Path


def private_file(path):
    path = Path(path)
    if not path.exists():
        os.close(os.open(path, os.O_CREAT | os.O_RDWR, 0o600))
    if path.stat().st_mode & 0o777 != 0o600:
        os.chmod(path, 0o600)
    return path
