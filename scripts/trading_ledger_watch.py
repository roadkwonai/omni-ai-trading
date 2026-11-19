"""Synchronize paper events independently of the trading process until 15:05 KST."""
from datetime import datetime
from zoneinfo import ZoneInfo
import time
from trading_ledger import DATABASE, EVENTS, ingest

if __name__ == '__main__':
    while True:
        try:
            print('imported', ingest(EVENTS, DATABASE), flush=True)
        except Exception as exc:
            print('sync_error', type(exc).__name__, flush=True)
        if datetime.now(ZoneInfo('Asia/Seoul')).strftime('%H:%M') >= '15:05':
            break
        time.sleep(60)
