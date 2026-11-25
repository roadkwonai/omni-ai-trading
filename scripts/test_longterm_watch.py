"""장기투자 장중 상시 감시 (user request 2026-11-24): each check fires on the record that proves it, and stays quiet otherwise."""
import json
from datetime import datetime

import longterm_watch as W

KST = W.KST
NOW = datetime(2026, 11, 24, 10, 0, tzinfo=KST)
ALIVE = {'vibe-longterm': True, 'longterm-live-2': True, 'discord-notify': True}


def row(kind, hhmm='08:45', rowid=1, **kw):
    return {'rowid': rowid, 'time': f'2026-11-24T{hhmm}:00+09:00', 'kind': kind, 'reason': kw.get('reason', ''),
            'indicators': kw.get('indicators', {}), 'targets': kw.get('targets', {})}


def facts(**kw):
    news = {'rows': [{'topic': t} for t in W.TOPICS]}
    macro = {'series': {k: {'stale': False} for k in W.MACRO}}
    base = {'decisions': [row('regime', indicators={'as_of': '2026-11-23'}), row('macro', indicators=macro), row('news', indicators=news),
                          row('story', targets={'source': 'llm', 'sources': ['한국경제 · 2026-11-23 · x']}), row('map'), row('plan', '09:40')],
            'live': [{'time': '2026-11-24T09:40:07+09:00', 'mode': 'execute', 'armed': True, 'action': 'PLAN', 'orders': []}],
            'live_on': True, 'failures': [], 'services': dict(ALIVE), 'stop': [], 'cursor': 1, 'rejected': 0, 'report': True, 'nav': False}
    return {**base, **kw}


def found(now=NOW, **kw):
    return W.findings(facts(**kw), now)


def test_a_healthy_morning_has_no_findings():
    assert found() == {}


def test_a_missing_step_fires_only_after_its_deadline():
    only = [row('regime', indicators={'as_of': '2026-11-23'})]
    assert 'step.plan' in found(decisions=only) and 'step.news' in found(decisions=only)
    assert found(now=NOW.replace(hour=8, minute=50), decisions=[], live=[], report=False) == {}
    assert 'step.close' in found(now=NOW.replace(hour=15, minute=55)) and 'step.close' not in found(now=NOW.replace(hour=15, minute=55), nav=True)


def test_engine_errors_and_a_dead_service_are_named():
    got = found(decisions=facts()['decisions'] + [row('error', '15:40', 9, reason='close: price unavailable NDXMIX')], cursor=9)
    assert 'NDXMIX' in got['engine.error:9']
    assert 'service.vibe-longterm' in found(services={**ALIVE, 'vibe-longterm': False})
    assert 'service.longterm-live-2' not in found(now=NOW.replace(hour=15, minute=0), services={**ALIVE, 'longterm-live-2': False})
    # stopped by the dashboard switch is not a fault
    assert 'stop' in found(stop=['vibe-longterm/STOP'])


def test_news_topics_stale_macro_and_old_story_sources():
    news = {'rows': [{'topic': t} for t in W.TOPICS if t != 'fed']}
    macro = {'series': {k: {'stale': k == 'DGS10'} for k in W.MACRO}}
    story = {'source': 'llm', 'sources': ['McKinsey · 2026-04-28 · Quantum', 'SpaceNexus · 2026-02-14 · Space']}
    d = [row('regime', indicators={'as_of': '2026-11-18'}), row('macro', indicators=macro), row('news', indicators=news),
         row('story', targets=story), row('map'), row('plan', '09:40')]
    got = found(decisions=d)
    assert '연준' in got['news.topic'] and 'DGS10' in got['macro.stale'] and 'story.old_sources' in got and 'regime.stale' in got
    none = [*d[:3], row('story', targets={'source': 'llm', 'sources': ['새 근거 없음']}), *d[4:]]
    assert 'story.no_evidence' in found(decisions=none) and 'story.old_sources' not in found(decisions=none)
    assert 'story.fallback' in found(decisions=[*d[:3], row('story', targets={'source': 'default'}), *d[4:]])


def test_a_monday_regime_dated_friday_is_fresh():
    monday = datetime(2026, 11, 23, 10, 0, tzinfo=KST)
    d = [row('regime', indicators={'as_of': '2026-11-20'}), *facts()['decisions'][1:]]
    assert 'regime.stale' not in W.findings(facts(decisions=d), monday)


def live(action, hhmm, **kw):
    return {'time': f'2026-11-24T{hhmm}:00+09:00', 'mode': 'execute', 'armed': True, 'action': action, **kw}


def test_live_orders_blocked_repeated_or_sent_twice():
    plan = live('PLAN', '09:40', orders=[])
    retry = [live('WAIT', f'09:4{i}', symbol='379810', execution='RETRY', reason='quote') for i in (3, 4, 5)]
    assert '379810' in found(live=[plan, *retry])['live.blocked:379810']
    assert 'live.blocked:379810' not in found(live=[plan, *retry, live('BUY', '09:46', symbol='379810', execution='ACKNOWLEDGED_NOT_FILLED')])
    twice = [live('BUY', f'09:4{i}', symbol='379810', execution='ACKNOWLEDGED_NOT_FILLED', qty=2) for i in (1, 5)]
    assert 'live.twice:379810:BUY' in found(live=[plan, *twice])
    assert 'live.twice:379810:BUY' not in found(live=[plan, twice[0], live('RECONCILED', '09:43', pending=0, reconciled={'x': {'filled': 0, 'cancelled': 2}}), twice[1]])
    assert 'live.unarmed' in found(live=[{**plan, 'armed': False}]) and 'live.unarmed' not in found(live=[{**plan, 'armed': False}], live_on=False)
    assert 'live.refused' in found(live=[plan, live('BLOCK', '09:50', reason='daily loss')])


def test_api_failures_discord_lag_rejected_balance_and_missing_report():
    fails = [('2026-11-24T09:50:00+09:00', 'nh', 'mcp:get_stock_price', 'longterm_live', 'network')]
    got = found(failures=fails * 3, cursor=0, rejected=2, report=False)
    assert 'longterm_live' in got['api.nh:longterm_live'] and 'discord.behind' in got and 'balance.rejected' in got and 'report.missing' in got
    assert 'api.nh:longterm_live' not in found(failures=fails)  # one failed call clears on the retry


def test_changes_report_new_and_resolved_once_and_start_over_each_day(tmp_path):
    state = tmp_path / 's.json'
    assert W.changes({'a': 'x'}, state, NOW) == [('new', 'a', 'x')]
    assert W.changes({'a': 'x again'}, state, NOW) == []
    assert W.changes({}, state, NOW) == [('resolved', 'a', 'x again')]
    W.changes({'a': 'x'}, state, NOW)
    assert W.changes({'a': 'x'}, state, NOW.replace(day=25)) == [('new', 'a', 'x')]
    assert json.loads(state.read_text())['day'] == '2026-11-25'


def test_collect_reads_the_records_without_writing(tmp_path):
    (tmp_path / 'vibe-longterm').mkdir()
    import sqlite3
    with sqlite3.connect(tmp_path / 'vibe-longterm' / 'longterm.sqlite3') as db:
        db.execute('CREATE TABLE decisions (day, time, kind, reason, indicators, targets)')
        db.execute('CREATE TABLE nav (day, book, value)')
        db.execute("INSERT INTO decisions VALUES ('2026-11-24','2026-11-24T08:45:00+09:00','map','r','{}',NULL)")
    (tmp_path / 'longterm-live.jsonl').write_text(json.dumps(live('HOLD', '09:41', account='live-2')) + '\n')
    before = sorted(p.name for p in tmp_path.rglob('*'))
    got = W.collect(tmp_path, NOW, run=lambda *a, **k: type('R', (), {'returncode': 1, 'stdout': ''})(), halted=lambda: False)
    assert got['decisions'][0]['kind'] == 'map' and got['live'][0]['action'] == 'HOLD' and got['services']['vibe-longterm'] is False
    assert sorted(p.name for p in tmp_path.rglob('*')) == before


def test_watch_sends_changes_once_and_retries_a_failed_send(tmp_path):
    state, sent, ok = tmp_path / 's.json', [], [False]

    def send(text):
        sent.append(text)
        return ok[0]
    W.step(send, state, NOW, collected=facts(report=False))  # the send fails: nothing is marked as reported
    ok[0] = True
    W.step(send, state, NOW, collected=facts(report=False))
    W.step(send, state, NOW, collected=facts(report=False))
    W.step(send, state, NOW, collected=facts())
    assert len(sent) == 3 and sent[0] == sent[1] and '아침 전략 리포트' in sent[1] and '해소' in sent[2]


def test_a_message_never_carries_a_long_number(tmp_path):
    sent = []
    d = facts()['decisions'] + [row('error', '09:50', 9, reason='boom 12345678901 at 379810')]
    W.step(lambda t: sent.append(t) or True, tmp_path / 's.json', NOW, collected=facts(decisions=d, cursor=9))
    assert '12345678901' not in sent[0] and '379810' in sent[0]


def test_the_discord_sender_uses_only_the_longterm_discord_channel(monkeypatch):
    import discord_notify as D
    seen = []
    monkeypatch.setattr(D, 'urls_from_env', lambda: {'longterm': [('discord', 'L'), ('telegram', ('t', 'c'))], 'realtime': [('discord', 'R')]})
    monkeypatch.setattr(D, 'send_everywhere', lambda targets, text: seen.append(targets) or {'discord': True})
    assert W.discord('x') is True and seen == [[('discord', 'L')]]
    monkeypatch.setattr(D, 'urls_from_env', lambda: {'longterm': []})
    assert W.discord('x') is False


def test_a_refusal_that_stands_is_blocked_though_the_executor_writes_it_once():
    plan, retry = live('PLAN', '09:40', orders=[]), live('WAIT', '09:50', symbol='379810', execution='RETRY', reason='quote')
    assert 'live.blocked:379810' in found(live=[plan, retry])  # 10:00, ten minutes on and nothing after it
    assert 'live.blocked:379810' not in W.findings(facts(live=[plan, retry]), NOW.replace(hour=9, minute=51))
    assert 'live.blocked:379810' not in found(live=[plan, retry, live('HOLD', '09:52', reason='today done')])


def test_an_engine_error_clears_once_its_step_works_again():
    d = facts()['decisions'] + [row('error', '09:50', 9, reason='dca: error:ValueError:quote time unavailable or stale')]
    assert 'engine.error:9' in found(decisions=d, cursor=9)
    assert 'engine.error:9' in found(decisions=d, cursor=9, recovered={'dca': '2026-11-24T09:40:07+09:00', 'trade': '2026-11-24T09:55:00+09:00'})
    assert 'engine.error:9' not in found(decisions=d, cursor=9, recovered={'dca': '2026-11-24T09:55:09+09:00'})


def test_a_wait_that_read_no_quote_is_not_a_recovery(tmp_path):
    # 2026-11-24 12:06: after a restart the 적립식 said 'waiting' between its five-minute looks and the error cleared
    (tmp_path / 'vibe-longterm').mkdir()
    log = tmp_path / 'vibe-longterm' / 'com.vibetrading.vibe-longterm.log'
    line = lambda hhmm, step, status: json.dumps({'time': f'2026-11-24T{hhmm}:44+09:00', 'step': step, 'status': status})
    run = lambda *a, **k: type('R', (), {'returncode': 1, 'stdout': ''})()
    log.write_text('\n'.join([line('12:05', 'dca', 'error:ValueError:x'), line('12:06', 'dca', 'waiting'), line('12:06', 'trade', 'already_traded')]))
    look = lambda hhmm: W.collect(tmp_path, NOW.replace(hour=12, minute=hhmm), run=run, halted=lambda: False)['recovered']
    assert 'dca' not in look(8) and 'trade' in look(8)
    assert look(13)['dca'] > '2026-11-24T12:10'  # the 12:10 look passed and wrote no error: the engine writes only changes
    log.write_text('\n'.join([line('12:05', 'dca', 'error:ValueError:x'), line('12:10', 'dca', 'waiting')]))
    assert 'dca' in look(10)


def test_a_later_error_does_not_undo_an_earlier_recovery(tmp_path):
    (tmp_path / 'vibe-longterm').mkdir()
    line = lambda t, status: json.dumps({'time': f'2026-11-24T{t}+09:00', 'step': 'dca', 'status': status})
    (tmp_path / 'vibe-longterm' / 'com.vibetrading.vibe-longterm.log').write_text('\n'.join(
        [line('10:30:03', 'error:x'), line('10:35:09', 'waiting'), line('12:05:05', 'error:x'), line('12:06:44', 'waiting')]))
    run = lambda *a, **k: type('R', (), {'returncode': 1, 'stdout': ''})()
    rec = W.collect(tmp_path, NOW.replace(hour=12, minute=8), run=run, halted=lambda: False)['recovered']
    d = facts()['decisions'] + [row('error', '10:30', 9, reason='dca: error'), row('error', '12:05', 10, reason='dca: error')]
    got = found(now=NOW.replace(hour=12, minute=8), decisions=d, cursor=10, recovered=rec)
    assert 'engine.error:9' not in got and 'engine.error:10' in got


def test_a_halt_of_nh_alone_is_found_by_its_real_name(tmp_path, monkeypatch):
    # until 2026-11-25 the watch asked for a broker named 'nh', which no one halts; every test passed its own `halted`
    from src.live import halt
    monkeypatch.setattr('src.live.paths.get_runtime_root', lambda: tmp_path)
    (tmp_path / 'nhplug').mkdir()
    run = lambda *a, **k: type('R', (), {'returncode': 1, 'stdout': ''})()  # noqa: E731
    look = lambda: W.collect(tmp_path / 'nhplug', NOW, run=run)['stop']  # noqa: E731
    assert look() == []
    halt.trip_halt('cli', 'test', broker='nhplug')
    assert look() == ['NH HALT']
