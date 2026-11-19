"""Ingest paper events and evaluate future bars separately from order execution."""
import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from src.config.paths import get_runtime_root
from trading_ledger import ingest
from paper_ai_modes import read_modes
from paper_learning import evaluate
from paper_online_model import train, train_jev
from sqlite_ro import ro_connect
import api_calls  # noqa: F401,E402 - counts this process's external API calls (dashboard 'API 연동')


def learning_inputs(ledger):
    """How many events evaluate() and train_jev() read: while it stays the same so do their results. The runner imports
    its own lines, so this watch's ingest count says nothing about what arrived."""
    with ro_connect(ledger) as db:
        return db.execute("SELECT COUNT(*) FROM events_v2 WHERE event IN ('decision', 'market_context', 'jev_shadow')").fetchone()[0]


def decision_mode(root):
    """The OpenAI decision mode (ai-modes.json): train() learns only its picks. On when the file is missing."""
    try:
        return read_modes(root)['decision']
    except (OSError, ValueError):
        return True


def review_time(root, day=None):
    """15:10, or 7 minutes after a later runner session close (session_times; the flatten time is a setting), on the
    day's exchange clock (16:10 on 수능 day)."""
    import session_times
    day = day or session_times.today()
    try:
        close = session_times.minutes(session_times.load(root, day)['close'])  # this watch's own account (isolated: defaults)
        return session_times.clock_text(max(session_times.minutes(session_times.shifted('15:10', day)), close + 7))
    except (OSError, ValueError, KeyError, TypeError):
        return session_times.shifted('15:10', day)


# 11-19 09:03~10:26 every round failed with sqlite3.DatabaseError in the long-running process (it kept handles of WAL
# files another process had deleted) while a fresh process read the same files fine: after this many in a row the
# watcher replaces itself with a fresh process (same pid for launchd, KeepAlive is off).
DB_ERROR_RESTART = 3


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shared-default', action='store_true', help='Use the shared ETF paper account research files')
    args = parser.parse_args(argv)
    root = get_runtime_root() / 'nhplug' / ('shared-etf-paper' if args.shared_default else 'isolated-paper')
    seen = result = model = jev_model = None
    db_errors = 0
    while True:
        now = datetime.now(ZoneInfo('Asia/Seoul'))
        end = review_time(root)
        if now.weekday() >= 5 or not '09:00' <= now.strftime('%H:%M') < end:
            if args.shared_default and now.weekday() < 5 and now.strftime('%H:%M') >= end:
                try:  # after-close review once the day's settlements are final; a failure must not hide the stop
                    from position_paths import update as update_paths
                    from daily_review import build, store
                    update_paths(root)
                    try:  # blocked strength/new-listing candidates' outcomes (research only, user 2026-11-17)
                        from blocked_outcomes import update as update_blocked
                        update_blocked(root)
                    except Exception as exc:
                        print(json.dumps({'blocked_outcomes_error': type(exc).__name__}), flush=True)
                    print(json.dumps({'daily_review': store(build(root), root)['lessons']}, ensure_ascii=False), flush=True)
                except Exception as exc:
                    print(json.dumps({'daily_review_error': type(exc).__name__}), flush=True)
                try:  # shadow (issue/theme/hot) candidates' outcomes (issue_candidates, research only, user 2026-11-18)
                    from issue_candidates import update_outcomes
                    print(json.dumps({'issue_candidates': update_outcomes(root=root)}), flush=True)
                except Exception as exc:
                    print(json.dumps({'issue_candidates_error': type(exc).__name__}), flush=True)
                try:  # 신규주 그림자 기록: today's listings under the current rule and two candidate changes (research only, user 2026-11-19)
                    import ipo_shadow
                    print(json.dumps({'ipo_shadow': ipo_shadow.update(root)}), flush=True)
                except Exception as exc:
                    print(json.dumps({'ipo_shadow_error': type(exc).__name__}), flush=True)
                try:  # 낙폭과대 반등 outcome log: today, and the previous session again so a held trade's result fills in
                    from oversold_outcomes import collect, market_data
                    import oversold
                    names = list(oversold.universe(root))
                    data = market_data(names, now.strftime('%Y-%m-%d'))
                    days = sorted({b[0].date().isoformat() for _, m5 in data[0].values() for b in m5})[-2:]
                    found = {day: len(collect(day, codes=names, data=data)['rows']) for day in days}
                    print(json.dumps({'oversold_outcomes': found}), flush=True)
                except Exception as exc:
                    print(json.dumps({'oversold_outcomes_error': type(exc).__name__}), flush=True)
                try:  # 낙폭과대 사례집: new falls of the last 40 days and their outcomes so far; the daily series for live lookups
                    import oversold_cases
                    done = oversold_cases.collect(years=2, recent_days=40, log=lambda *_: None)
                    print(json.dumps({'oversold_cases': done['cases']}), flush=True)
                except Exception as exc:
                    print(json.dumps({'oversold_cases_error': type(exc).__name__}), flush=True)
                try:  # 매매일지: the day's trades, market, issues and system (the 16:40 task adds the written perspective)
                    import trading_journal
                    print(json.dumps({'trading_journal': trading_journal.store(trading_journal.build(now.strftime('%Y-%m-%d')))}), flush=True)
                    print(json.dumps({'trading_journal_periods': trading_journal.update_periods(now.strftime('%Y-%m-%d'))}), flush=True)
                except Exception as exc:
                    print(json.dumps({'trading_journal_error': type(exc).__name__}), flush=True)
                try:  # 성과 비교 rows (Jev vs 장기투자, plan 2026-11-19): after the journal, whose KOSPI regime it reads
                    import strategy_compare
                    print(json.dumps({'strategy_compare': strategy_compare.build(root.parent)}), flush=True)
                except Exception as exc:
                    print(json.dumps({'strategy_compare_error': type(exc).__name__}), flush=True)
            break
        try:
            n = ingest(root / 'events.jsonl', root / 'trading-ledger.sqlite3')
            inputs = learning_inputs(root / 'trading-ledger.sqlite3')
            if inputs != seen:
                result = evaluate(root / 'trading-ledger.sqlite3', root / 'paper-observations.sqlite3')
                model = train(root / 'paper-observations.sqlite3', root / 'paper-model.sqlite3') if decision_mode(root) else None
                jev_model = train_jev(root / 'trading-ledger.sqlite3', root / 'paper-observations.sqlite3', root / 'paper-model.sqlite3')
                seen = inputs
            paths = None
            if args.shared_default:
                try:  # research records only: a failure here must not stop the ingest and evaluation above
                    from position_paths import update as update_paths
                    paths = update_paths(root)
                except Exception as exc:
                    paths = {'error_type': type(exc).__name__}
            print(json.dumps({'time': now.isoformat(), 'imported': n, 'observations': result, 'model': model, 'jev_research': jev_model,
                              'paths': paths}), flush=True)
            db_errors = 0
        except Exception as exc:
            import traceback
            where = [f'{Path(f.filename).name}:{f.lineno} {f.name}' for f in traceback.extract_tb(exc.__traceback__)[-3:]]
            # sqlite messages carry no secrets; they say which database failed and how (11-19 DatabaseError hunt)
            print(json.dumps({'time': now.isoformat(), 'error_type': type(exc).__name__, 'detail': str(exc)[:160],
                              'where': where}, ensure_ascii=False), flush=True)
            db_errors = db_errors + 1 if isinstance(exc, sqlite3.DatabaseError) else 0
            if db_errors >= DB_ERROR_RESTART:
                print(json.dumps({'time': now.isoformat(), 'self_restart': 'sqlite_errors', 'count': db_errors}), flush=True)
                os.execv(sys.executable, [sys.executable, '-u', os.path.abspath(__file__),
                                          *(sys.argv[1:] if argv is None else argv)])
        time.sleep(60)


if __name__ == '__main__':
    main()
