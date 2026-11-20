"""Run AI search requests outside the Streamlit rendering thread."""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

import liner_history

_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix='liner')


def submit(kind: str, query: str, operation: Callable[[], Any]) -> Future[tuple[Any, bool]]:
    def request():
        result = operation()
        if kind == '내부 조회':
            return result, True
        try:
            liner_history.record(kind, query)
        except (OSError, sqlite3.Error):
            return result, False
        return result, True

    return _POOL.submit(request)
