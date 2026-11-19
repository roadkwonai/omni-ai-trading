"""Behavior pins for the 2026-11-17 runner consolidation (stage 1: atomic state files and the live open-order check;
stage 2: the session clock constants). Each test drives a public entry point and asserts what a reader or the
broker would observe."""
import json
import stat
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest

KST = ZoneInfo('Asia/Seoul')


def private(path):
    return stat.S_IMODE(Path(path).stat().st_mode) == 0o600


# ---- stage 1: state files are private, readable back by their own readers, and failures stay non-fatal ----

def test_paper_exit_plan_and_requests_round_trip_privately(tmp_path):
    import nh_paper_isolated as p
    p.save_exit_plan(tmp_path, '114800')
    assert p.exit_plans(tmp_path)['114800']['profile'] == 'defensive' and private(tmp_path / 'exit-plans.json')
    p.request_exits(tmp_path, ['114800'])
    assert json.loads((tmp_path / 'exit-requests.json').read_text()) == ['114800']  # sell-alls stay a plain list
    assert p.exit_requests(tmp_path, {'114800': 10}) == {'114800': None} and private(tmp_path / 'exit-requests.json')
    p.write_requests(tmp_path / 'exit-requests.json', {'114800': 3})
    assert p.exit_requests(tmp_path, {'114800': 10}) == {'114800': 3}
    assert p.exit_requests(tmp_path, {}) == {} and not (tmp_path / 'exit-requests.json').exists()  # lapses when flat


def test_live_request_file_round_trips_privately(tmp_path):
    import nh_live_follow as f
    f.write_request_file(f.exit_request_path(tmp_path, 'live-1'), {'114800': 5, '005930': None})
    assert f.exit_requests(tmp_path, 'live-1') == {'114800': 5, '005930': None}
    assert private(f.exit_request_path(tmp_path, 'live-1'))
    f.write_request_file(f.exit_request_path(tmp_path, 'live-1'), {})
    assert not f.exit_request_path(tmp_path, 'live-1').exists()


def test_reentry_watch_and_regime_trims_round_trip_privately(tmp_path):
    import position_sizing as ps
    import v2_reentry as r
    r.stopped(tmp_path, '005930', opened='2026-11-17', hold_days=3, qty=10, reentries=0, limit=2)
    assert r.watch(tmp_path)['005930']['qty'] == 10 and private(tmp_path / r.FILE)
    ps._save(tmp_path, {'114800': {'position': 1, 'entry_regime': '하락장', 'trimmed': False}})
    ps.mark_trimmed(tmp_path, '114800')
    assert ps._load(tmp_path)['114800']['trimmed'] is True and private(tmp_path / ps.TRIMS)


def test_vibe_account_state_is_private_json_the_advisor_can_read(tmp_path):
    import vibe_runner as v
    balance = {'Output_0': {'orr_pbl_amt1': 1_000_000},
               'Output_1': [{'iem_cd': '114800', 'iem_nm': 'KODEX 인버스', 'rsdl_qty': 5, 'phs_pr': 1340, 'now_pr': 1350}]}
    v.write_account_state(tmp_path, balance, {'114800': 5})
    state = json.loads((tmp_path / 'account-state.json').read_text(encoding='utf-8'))
    assert state['holdings']['114800']['iem_nm'] == 'KODEX 인버스' and state['cash'] == 1_000_000
    assert private(tmp_path / 'account-state.json')


def test_unwritable_state_folder_never_raises_where_it_did_not_before(tmp_path):
    import nh_paper_isolated as p
    import v2_reentry as r
    tmp_path.chmod(0o500)
    try:
        p.save_exit_plan(tmp_path, '114800')  # the position then follows the current settings
        r.stopped(tmp_path, '005930', opened='2026-11-17', hold_days=3, qty=10, reentries=0, limit=2)
    finally:
        tmp_path.chmod(0o700)
    assert not any(tmp_path.iterdir())  # and no temporary file is left behind


# ---- stage 2: the session clock ----

def at(clock):
    return datetime.fromisoformat(f'2026-11-18T{clock}:00+09:00')  # a Wednesday


@pytest.mark.parametrize('opening, before, first', [('09:00', '08:59', '09:00'), ('09:30', '09:29', '09:30')])
def test_paper_orders_start_at_the_later_of_the_open_and_the_session_open(opening, before, first):
    import nh_paper_isolated as p
    with patch('session_times.SESSION_OPEN', opening), patch.object(p, 'SESSION_CLOSE', '15:00'):
        assert p.in_observation_session(at('09:00'))
        assert not p.order_session(at(before)) and p.order_session(at(first))


@pytest.mark.parametrize('opening, before, first', [('09:00', '08:59', '09:00'), ('09:30', '09:29', '09:30')])
def test_live_mirror_buys_start_with_the_paper_orders(opening, before, first):
    import nh_live_follow as f
    plan = dict(target={'005930': 1}, buyable={'005930'}, owned={}, held={}, own_pending=False, broker_open=False,
                halted=False, exits={}, buy_paused=False)
    with patch('session_times.SESSION_OPEN', opening), patch.object(f, 'buy_window_end', return_value='14:30'):
        assert f.decide(**plan, clock=before)['action'] == 'WAIT'
        assert f.decide(**plan, clock=first)['action'] == 'BUY'


def test_vibe_runner_observes_until_a_later_session_open_instead_of_ending_the_day(tmp_path):
    # 11/17 review: with 장 시작 at 09:30 the vibe runner used to settle and exit at 09:10 for the rest of the day
    import vibe_runner as v

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at('09:20')

    observed = Mock(side_effect=RuntimeError('observed'))
    with patch('session_times.SESSION_OPEN', '09:30'), patch.object(v, 'datetime', Clock), \
         patch.object(v, 'refresh_clock'), patch.object(v, 'observe_state', observed), \
         patch.object(v, 'settle_before_stop') as settle:
        with pytest.raises(RuntimeError, match='observed'):
            v.loop(Mock(), None, None, 'acct', tmp_path, False)
    settle.assert_not_called()


# ---- exits follow the runner that keeps the ledger, not the engine's name (11/17 review finding 1) ----
# shared-etf-paper and jev-virtual are kept by nh_paper_isolated (exit-plans.json) whichever engine is main;
# vibe-paper is kept by vibe_runner (current-settings defensive plan, 낙폭과대 prices in oversold-plans.json).

SETTINGS = {'stop_loss_pct': 2.0, 'take_profit_half_pct': 1.0, 'take_profit_all_pct': 2.0}
OVERSOLD_PLAN = {'profile': 'oversold', 'stop_pct': 0.5, 'half_pct': 1.5, 'all_pct': 1.5}


def ledger(folder, fills, sources):
    """A ledger folder with buy fills [(id, symbol, qty, price)] and their submitted sources {id: source}."""
    import sqlite3
    folder.mkdir(parents=True)
    with sqlite3.connect(folder / 'orders.sqlite3') as db:
        db.execute('create table fills (order_id text, symbol text, side text, quantity integer, price real, time text)')
        db.execute('create table decision_order_links (order_id text, cycle_id text)')
        db.executemany('insert into fills values (?,?,?,?,?,?)', [(i, s, 'BUY', q, p, 't') for i, s, q, p in fills])
        db.executemany('insert into decision_order_links values (?,?)', [(i, 'c' + i) for i, *_ in fills])
    (folder / 'events.jsonl').write_text('\n'.join(json.dumps({'event': 'submitted', 'cycle_id': 'c' + i,
                                                               'symbol': s, 'source': sources[i]}) for i, s, *_ in fills))
    return folder / 'orders.sqlite3'


def test_plan_for_reads_the_plan_store_of_the_folder_runner(tmp_path):
    import engine_exits as e
    shared, vibe = tmp_path / 'shared-etf-paper', tmp_path / 'vibe-paper'
    shared.mkdir(); vibe.mkdir()
    (shared / 'exit-plans.json').write_text(json.dumps({'005930': OVERSOLD_PLAN}))
    (vibe / 'oversold-plans.json').write_text(json.dumps({'005930': {'stop_price': 99500, 'target_price': 101500}}))
    assert e.plan_for(shared, '005930', SETTINGS) == (OVERSOLD_PLAN, None)  # also when vibe is the main engine
    assert e.plan_for(shared, '069500', SETTINGS) == (e.defensive_plan(SETTINGS), None)
    assert e.plan_for(vibe, '005930', SETTINGS)[1]['stop_price'] == 99500
    assert e.plan_for(vibe, '069500', SETTINGS) == (e.defensive_plan(SETTINGS), None)


def test_live_mirror_of_the_shared_ledger_keeps_its_oversold_stop_with_vibe_main(tmp_path):
    import nh_live_follow as f
    import nh_paper_isolated as paper
    source = ledger(tmp_path / 'shared-etf-paper', [('a', '005930', 10, 100000)], {'a': 'oversold'})
    (source.parent / 'exit-plans.json').write_text(json.dumps({'005930': OVERSOLD_PLAN}))
    with patch.object(paper, 'market_view', return_value={'코스피': {'regime': '하락장'}}), \
         patch.object(paper, 'stop_symbol_index', return_value='코스피'):
        basis = f.stop_percent_for_positions(source, tmp_path, {'005930': 1}, 2.0)
    assert (basis['005930']['pct'], basis['005930']['profile']) == (0.5, 'oversold')


def test_shared_runner_positions_in_vibe_mode_or_hand_added_are_ordinary(tmp_path):
    # the paper runner tightens these in an adverse regime, so the mirror must too (finding 2)
    import nh_live_follow as f
    source = ledger(tmp_path / 'shared-etf-paper', [('a', '069500', 10, 10000), ('b', '069500', 5, 10000),
                                                    ('c', '122630', 10, 20000), ('d', '005930', 1, 100000)],
                    {'a': 'jev', 'b': 'manual', 'c': 'vibe', 'd': 'oversold'})
    assert f.ordinary_positions(source) == {'069500': True, '122630': True, '005930': False}
    vibe = ledger(tmp_path / 'vibe-paper', [('a', '114800', 10, 1000), ('b', '069500', 1, 10000)], {'a': 'jev_fast', 'b': 'oversold'})
    assert f.ordinary_positions(vibe) == {'114800': True, '069500': False}


# ---- request files: anything malformed is ignored, never raised (finding 3) ----

@pytest.mark.parametrize('content', ['[{"x": 1}]', '[["114800"]]', '[114800]', '{"114800": "3"}', '"114800"'])
def test_malformed_sell_requests_are_ignored_not_raised(tmp_path, content):
    import nh_live_follow as f
    import nh_paper_isolated as p
    (tmp_path / 'exit-requests.json').write_text(content)
    assert p.exit_requests(tmp_path, {'114800': 10}) == {}
    f.exit_request_path(tmp_path, 'live-1').write_text(content)
    assert f.exit_requests(tmp_path, 'live-1') == {}


def test_fast_exit_observer_reads_partial_sell_requests(tmp_path):
    # the paper runner rewrites exit-requests.json as {code: shares}; the observer must still see the request (finding 5)
    import vibe_fast_exit_watch as watch
    from test_vibe_fast_exit_watch import NOW, setup_position
    root = setup_position(tmp_path)
    (root / 'exit-requests.json').write_text(json.dumps({'005930': 3}))
    assert [p['symbol'] for p in watch.positions(root, NOW)] == ['100790']
    (root / 'exit-requests.json').write_text(json.dumps({'100790': 3}))
    assert watch.positions(root, NOW) == []  # a requested sale is the runner's, not the observer's
