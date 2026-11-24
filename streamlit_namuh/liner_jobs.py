"""Run AI search requests outside the Streamlit rendering thread."""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

import liner_history

_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix='liner')


def submit(kind: str, query: str, operation: Callable[[], Any],
           keep: Callable[[Any], int] | None = None) -> Future[tuple[Any, bool, int | None]]:
    """(result, kept?, talk id). Kept here, not on the screen that asked: an answer that arrives after it closed is not lost."""
    def request():
        result, talk = operation(), None
        try:
            if keep:
                talk = keep(result)
            saved = True
        except (OSError, sqlite3.Error, ValueError):  # ValueError: text SQLite cannot encode
            saved = False
        if kind != '내부 조회':
            try:
                liner_history.record(kind, query)
            except (OSError, sqlite3.Error, ValueError):
                pass  # only the list of requests to send again
        return result, saved, talk

    return _POOL.submit(request)
