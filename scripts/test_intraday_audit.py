import json
import sqlite3
from datetime import date, datetime
from types import SimpleNamespace

import intraday_audit as audit

KST = audit.KST
NOW = datetime(2026, 11, 17, 10, 20, tzinfo=KST)


def _launchctl(args, **_):
    if args[0] == 'ps':
        return SimpleNamespace(returncode=0, stdout='Tue Nov 17 09:00:01 2026\n')
    if args[-1].endswith('vibe-event-advisor'):
        return SimpleNamespace(returncode=113, stdout='')  # not registered
    pid = '' if args[-1].endswith('vibe-paper') else '"PID" = 42;\n'
    return SimpleNamespace(returncode=0, stdout=pid + '"LastExitStatus" = 0;\n')


def _ledger(root, beats, events=()):
    root.mkdir(parents=True)
    with sqlite3.connect(root / 'orders.sqlite3') as db:
        db.execute('CREATE TABLE fills (order_id TEXT PRIMARY KEY, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT)')
        db.execute('CREATE TABLE intents (id TEXT PRIMARY KEY, day TEXT, symbol TEXT, side TEXT, qty INTEGER, price INTEGER, '
                   'status TEXT, order_number INTEGER, created REAL)')
        db.execute("INSERT INTO fills VALUES ('a','005930','BUY',3,70000,'2026-11-17T09:12:00+09:00')")
        db.execute("INSERT INTO fills VALUES ('old','005930','SELL',9,70000,'2026-11-16T15:00:00+09:00')")
        db.execute("INSERT INTO intents VALUES ('i','2026-11-17','005930','BUY',1,70000,'submitted',1,0)")
    (root / 'events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
    (root / f'com.vibetrading.{root.name}.log').write_text(
        'not json\n' + ''.join(json.dumps({'status': 'wait', 'time': t}) + '\n' for t in beats))


def _nhplug(tmp_path):
    nhplug = tmp_path / 'nhplug'
    day = '2026-11-17T'
    _ledger(nhplug / 'shared-etf-paper',
            [day + '07:30:00+09:00', day + '09:10:00+09:00', day + '09:11:00+09:00', day + '09:20:00+09:00',
             day + '10:19:30+09:00'],
            [{'time': day + '09:15:00+09:00', 'event': 'v2_entry_gate', 'permitted': False, 'reason': 'trend_not_rising'},
             {'time': day + '10:15:00+09:00', 'event': 'v2_entry_gate', 'permitted': False, 'reason': 'volume_not_rising',
              'strength_reasons': {'090710': 'careful_reentry_chase', '005930': 'jev_below_min'}},
             {'time': day + '10:16:00+09:00', 'event': 'oversold_signal', 'premarket_audit': {'status': 'included_in_recorded_input'}},
             {'time': day + '10:17:00+09:00', 'event': 'v2_error', 'error': 'JevRequestBusy'}])
    _ledger(nhplug / 'vibe-paper', [day + '09:10:00+09:00'])  # the retired intraday vibe book, still on disk
    (nhplug / 'vibe-paper' / 'account-state.json').write_text(json.dumps({'time': day + '10:19:00+09:00',
                                                                          'holdings': {'005930': {}, '035420': {}}}))
    (nhplug / 'shared-etf-paper' / 'reviews').mkdir()
    return nhplug


def test_collect_reports_facts_and_keys(tmp_path):
    nhplug = _nhplug(tmp_path)
    facts = audit.collect(nhplug, NOW, since=datetime(2026, 11, 17, 10, 0, tzinfo=KST), run=_launchctl, holidays=frozenset())
    shared = facts['ledgers']['shared-etf-paper']
    assert shared['fills'] == {'BUY': {'count': 1, 'qty': 3}}  # yesterday's fill is not today's
    assert shared['unfinished_intents'] == 1
    assert shared['cycles'] == 4  # the 07:30 pre-session line is not a cycle
    assert shared['long_gaps'] == [{'after': '09:11:00', 'minutes': 9.0}, {'after': '09:20:00', 'minutes': 59.5}]
    assert shared['entry_blocks'] == {'volume_not_rising': 1}  # only the --since window
    assert shared['strength_blocks'] == {'090710': {'careful_reentry_chase': 1}, '005930': {'jev_below_min': 1}}
    assert shared['jev_busy'] == 1 and shared['errors'] == {'v2_error': 1}
    assert shared['oversold']['oversold_signal']['premarket_audit'] == {'included_in_recorded_input': 1}
    # vibe's intraday book, quote snapshots, event pipeline and replay are retired (user 2026-11-17): not read, no keys
    assert not {'quotes', 'events', 'event_replay'} & set(facts)
    keys = {f['key'] for f in facts['findings']}
    assert {'intents.unfinished:shared-etf-paper', 'cycle.gap:shared-etf-paper:09:20:00'} <= keys
    assert not any(k.startswith(('quote.', 'event.')) for k in keys)
    assert not keys & {'service.unregistered:vibe-event-advisor', 'service.down:vibe-paper', 'cycle.silent:vibe-paper'}
    assert 'cycle.silent:shared-etf-paper' not in keys
    assert not any(k.startswith('live.switch_on') for k in keys)


def test_findings_are_new_ongoing_or_resolved_against_todays_file(tmp_path):
    nhplug = _nhplug(tmp_path)
    (nhplug / 'shared-etf-paper' / 'reviews' / 'operations-audit-2026-11-17.md').write_text(
        '## 09:20\n- [intents.unfinished:shared-etf-paper] 미종결 [check-done:x] [1]\n- [cycle.silent:shared-etf-paper] 멈춤\n'
        '- [cycle.silent:old] 해소 [resolved:cycle.silent:old]\n')
    findings = {f['key']: f['status'] for f in audit.collect(nhplug, NOW, run=_launchctl, holidays=frozenset())['findings']}
    assert findings['intents.unfinished:shared-etf-paper'] == 'ongoing'
    assert findings['cycle.gap:shared-etf-paper:09:20:00'] == 'new'
    assert findings['cycle.silent:shared-etf-paper'] == 'resolved'
    assert 'cycle.silent:old' not in findings  # already marked resolved, not repeated
    assert not any(k.startswith('check-done') or k == '1' for k in findings)


def test_no_session_findings_on_a_holiday(tmp_path):
    nhplug = _nhplug(tmp_path)
    facts = audit.collect(nhplug, NOW, run=_launchctl, holidays=frozenset({date(2026, 11, 17)}))
    assert facts['trading_day'] is False
    keys = {f['key'] for f in facts['findings']}
    assert not any(k.startswith(('service.', 'cycle.silent')) for k in keys)
    assert 'day.closed' in keys


def test_dated_checks_drop_out_once_marked_done(tmp_path):
    nhplug = _nhplug(tmp_path)
    checks = tmp_path / 'checks.md'
    checks.write_text('# 날짜별 확인\n\n### event-first-sample\n내용\n\n### overnight-first\n내용\n')
    reviews = nhplug / 'shared-etf-paper' / 'reviews'
    (reviews / 'operations-audit-2026-11-16.md').write_text('- 확인 끝 [check-done:event-first-sample]\n')
    facts = audit.collect(nhplug, NOW, run=_launchctl, holidays=frozenset(), checks_file=checks)
    assert facts['checks'] == {'event-first-sample': '2026-11-16', 'overnight-first': None}


def test_the_retired_jev_virtual_runner_is_not_audited():
    # 2026-11-17: Jev is the only main engine, so its secondary virtual runner was retired
    assert 'jev-virtual' not in (*audit.SERVICES, *audit.SESSION_SERVICES, *audit.LEDGERS)


def test_the_session_summary_is_not_expected_at_the_open_or_from_the_watchlist(tmp_path):
    """11-19~11-24 each 09:20 round: the opening calls (too few bars of today) and the watchlist pick never carry it."""
    nhplug = _nhplug(tmp_path)
    with sqlite3.connect(nhplug / 'shared-etf-paper' / 'market-research.sqlite3') as db:
        db.execute('CREATE TABLE model_inputs (digest TEXT PRIMARY KEY, time TEXT, engine TEXT, question TEXT, inputs TEXT, response TEXT)')
        db.execute('CREATE TABLE model_evidence (digest TEXT PRIMARY KEY, time TEXT, engine TEXT, question TEXT, evidence TEXT)')
        def put(n, at, question, **state):
            db.execute('INSERT INTO model_inputs VALUES (?,?,?,?,?,?)', (str(n), f'2026-11-17T{at}:00+09:00', 'jev', question,
                                                                  json.dumps({'state': {'chart': 'c', **state}}), json.dumps({'choice': 'WAIT'})))
        for n in range(15):
            put(n, f'09:{n:02d}', 'candidate')
        put(50, '09:40', 'watchlist')
        rows = lambda: audit.jev_inputs(nhplug / 'shared-etf-paper', '2026-11-17')['presence_pct']  # noqa: E731
        db.commit()
        assert 'intraday_summary' not in rows() and rows()['chart'] == 100
        put(60, '09:30', 'candidate', intraday_summary={'K200': {'bars': 20}})
        put(61, '09:31', 'holding')
        db.commit()
        assert rows()['intraday_summary'] == 50


def test_jev_inputs_report_presence_freshness_and_evidence(tmp_path):
    nhplug = _nhplug(tmp_path)
    research = nhplug / 'shared-etf-paper' / 'market-research.sqlite3'
    with sqlite3.connect(research) as db:
        db.execute('CREATE TABLE model_inputs (digest TEXT PRIMARY KEY, time TEXT, engine TEXT, question TEXT, inputs TEXT, response TEXT)')
        db.execute('CREATE TABLE model_evidence (digest TEXT PRIMARY KEY, time TEXT, engine TEXT, question TEXT, evidence TEXT)')
        for n in range(12):
            at = f'2026-11-17T10:{n:02d}:00+09:00'
            state = {'chart': 'c', 'recent_news_headlines': [{'published': '2026-11-17T00:30:00+00:00'}],  # 09:30 KST
                     'market_regime_themes_global': {'as_of': f'2026-11-17T09:{50 + n % 5}'}, 'premarket_context': {'a': 1},
                     'own_track_record': {'a': 1}, 'trading_journal': {'a': 1},
                     **({'intraday_summary': {'K200': {'bars': 60}}} if n % 2 else {})}
            db.execute('INSERT INTO model_inputs VALUES (?,?,?,?,?,?)', (str(n), at, 'jev', 'candidate', json.dumps({'state': state}),
                                                                  json.dumps({'choice': 'INV' if n % 3 else 'WAIT'})))
            db.execute('INSERT INTO model_inputs VALUES (?,?,?,?,?,?)', (f'r{n}', at, 'jev', 'reentry', json.dumps({'state': state}),
                                                                  json.dumps({'choice': 'WAIT'})))
            db.execute('INSERT INTO model_evidence VALUES (?,?,?,?,?)', (str(n), at, 'jev', 'candidate', json.dumps(
                {'why_data': {'choice': 'CONFLICT' if n < 9 else 'ENOUGH', 'confidence': 0.4}, 'why_driver': {'choice': 'REGIME', 'confidence': 0.5}})))
    facts = audit.collect(nhplug, NOW, since=datetime(2026, 11, 17, 10, 0, tzinfo=KST), run=_launchctl, holidays=frozenset())
    jev = facts['jev_inputs']
    assert jev['calls'] == {'candidate': 12, 'reentry': 12} and jev['presence_pct']['chart'] == 100
    assert jev['answers'] == {'candidate': {'INV': 8, 'WAIT': 4}, 'reentry': {'WAIT': 12}}
    assert jev['presence_pct']['investor_daily_net_qty'] == 0 and jev['presence_pct']['intraday_summary'] == 50
    assert jev['news_newest_age_min']['median'] > 30 and jev['market_age_min_max'] >= 15
    assert jev['evidence'] == {'data': {'CONFLICT': 9, 'ENOUGH': 3}, 'driver': {'REGIME': 12}} and jev['evidence_calls'] == 12
    keys = {f['key'] for f in facts['findings']}
    assert {'jev.input_missing:investor_daily_net_qty', 'jev.input_missing:intraday_summary', 'jev.market_stale',
            'jev.data_insufficient', 'jev.reentry_never_buy'} <= keys and 'jev.input_missing:chart' not in keys


DAY = '2026-11-17T'


def _jev_calls(nhplug, calls, evidence=()):
    """calls: [(HH:MM, question, model inputs)]; `evidence` = indexes of the calls that got evidence answers."""
    with sqlite3.connect(nhplug / 'shared-etf-paper' / 'market-research.sqlite3') as db:
        db.execute('CREATE TABLE model_inputs (digest TEXT PRIMARY KEY, time TEXT, engine TEXT, question TEXT, inputs TEXT, response TEXT)')
        db.execute('CREATE TABLE model_evidence (digest TEXT PRIMARY KEY, time TEXT, engine TEXT, question TEXT, evidence TEXT)')
        for n, (hhmm, question, inputs) in enumerate(calls):
            db.execute('INSERT INTO model_inputs VALUES (?,?,?,?,?,?)', (str(n), f'{DAY}{hhmm}:00+09:00', 'jev', question,
                                                                         json.dumps(inputs), json.dumps({'choice': 'WAIT'})))
            if n in evidence:
                db.execute('INSERT INTO model_evidence VALUES (?,?,?,?,?)', (str(n), f'{DAY}{hhmm}:01+09:00', 'jev', question, json.dumps(
                    {'why_data': {'choice': 'ENOUGH', 'confidence': 0.6}})))


def _good_call(hhmm, **state):
    """A candidate call after the 11/18 fixes: recent news, timed stock news, intraday market sections, timed figures."""
    figures_at = f'{hhmm[:3]}{int(hhmm[3:]) - 2:02d}' if int(hhmm[3:]) >= 2 else hhmm
    base = {'chart': 'c', 'recent_news_headlines': [{'published': f'{DAY}{hhmm}:00+09:00', 'title': 'n'}],
            'market_regime_themes_global': {'as_of': f'{DAY}{hhmm}', 'index_intraday': {'코스피': {'as_of': hhmm}},
                                           'global_intraday': {'나스닥100 선물': {'as_of': hhmm}}},
            'watchlist_details': {'005930': {'headlines': [{'title': 't', 'age_min': 12}]}}}
    return {'state': {**base, **state}, 'criteria': {'005930': f'삼성전자 개별주식 · 등락 +1.00% ({figures_at} 기준)',
                                                    'K200': 'KODEX 200: KOSPI 200 추종', 'WAIT': 'wait'}}


def _keys(nhplug, now=NOW):
    return {f['key']: f.get('detail') for f in audit.collect(nhplug, now, run=_launchctl, holidays=frozenset())['findings']}


JEV_NEW = {'jev.news_old', 'jev.stock_news_old', 'jev.market_intraday_missing', 'jev.candidate_figures_stale',
           'jev.evidence_missing', 'jev.news_stale', 'jev.global_intraday_missing'}


def test_old_market_or_stock_headlines_fire_from_a_single_call(tmp_path):
    times = [f'10:{m:02d}' for m in range(10)]
    fresh = _nhplug(tmp_path / 'fresh')
    _jev_calls(fresh, [(t, 'candidate', _good_call(t)) for t in times], evidence=range(10))
    assert not set(_keys(fresh)) & JEV_NEW

    one = _nhplug(tmp_path / 'one')
    calls = [(t, 'candidate', _good_call(t)) for t in times]
    calls[3][2]['state']['recent_news_headlines'].append({'published': f'{DAY}07:00:00+09:00', 'title': 'old'})
    calls[5][2]['state']['watchlist_details'] = {'005930': {'headlines': ['시각 없는 기사']},
                                                 '000660': {'headlines': [{'title': 't', 'age_min': 120}]}}
    _jev_calls(one, calls, evidence=range(10))
    keys = _keys(one)
    assert {'jev.news_old', 'jev.stock_news_old'} <= set(keys) and 'jev.news_stale' not in keys
    assert '1건' in keys['jev.news_old'] and '2건' in keys['jev.stock_news_old']

    stale = _nhplug(tmp_path / 'stale')
    _jev_calls(stale, [(t, 'candidate', _good_call(t, recent_news_headlines=[{'published': f'{DAY}07:00:00+09:00'}]))
                       for t in times], evidence=range(10))
    assert {'jev.news_old', 'jev.news_stale'} <= set(_keys(stale))


def test_session_calls_without_intraday_market_sections_fire(tmp_path):
    times = [f'10:{m:02d}' for m in range(6)]
    missing = _nhplug(tmp_path / 'missing')
    _jev_calls(missing, [(t, 'candidate', _good_call(t, market_regime_themes_global={'as_of': f'{DAY}{t}'})) for t in times],
               evidence=range(6))
    assert 'jev.market_intraday_missing' in _keys(missing)
    present = _nhplug(tmp_path / 'present')
    _jev_calls(present, [(t, 'candidate', _good_call(t)) for t in times], evidence=range(6))
    assert 'jev.market_intraday_missing' not in _keys(present)


def test_market_news_age_uses_dispatch_time_instead_of_response_record_time(tmp_path):
    nhplug = _nhplug(tmp_path)
    _jev_calls(nhplug, [('10:00', 'candidate', _good_call('10:00',
                        news_checked_at=f'{DAY}09:59:59.500+09:00',
                        recent_news_headlines=[{'published': f'{DAY}08:29:59.900+09:00', 'title': '경계 기사'}]))])
    assert 'jev.news_old' not in _keys(nhplug)


def test_market_news_audit_uses_the_90_minute_policy_for_versioned_inputs(tmp_path):
    within, over = ([{'published': f'{DAY}{at}:00+09:00', 'title': '기사'}] for at in ('08:50', '08:25'))
    kept, old = _nhplug(tmp_path / 'kept'), _nhplug(tmp_path / 'old')
    for folder, articles in ((kept, within), (old, over)):
        _jev_calls(folder, [('10:00', 'candidate', _good_call('10:00', news_max_age_minutes=90,
                                                              recent_news_headlines=articles))])
    assert 'jev.news_old' not in _keys(kept)
    assert 'jev.news_old' in _keys(old)


def test_candidate_figures_older_than_the_ranking_window_fire(tmp_path):
    times = [f'10:{m:02d}' for m in range(6)]
    for name, suffix in (('old', ' (09:45 기준)'), ('untimed', '')):  # 15+ minutes old, or the watchlist's figure
        stale = _nhplug(tmp_path / name)
        calls = [(t, 'candidate', _good_call(t)) for t in times]
        for _, _, inputs in calls:
            inputs['criteria']['005930'] = '삼성전자 · 등락 +1.00%' + suffix
        _jev_calls(stale, calls, evidence=range(6))
        assert 'jev.candidate_figures_stale' in _keys(stale), name
    fresh = _nhplug(tmp_path / 'fresh')  # the watchlist selection's own criteria carry no ranking figures by design
    _jev_calls(fresh, [(t, 'candidate', _good_call(t)) for t in times]
               + [(t, 'watchlist', {'state': {}, 'criteria': {'005930': '삼성전자 (KOSPI200): 거래대금 상위'}}) for t in times],
               evidence=range(6))
    assert 'jev.candidate_figures_stale' not in _keys(fresh)


def test_missing_evidence_after_it_started_fires(tmp_path):
    calls = [(f'10:{m:02d}', 'candidate', _good_call(f'10:{m:02d}')) for m in range(8)]
    calls += [('10:09', 'watchlist', {'state': {}}), ('10:10', 'watchlist', {'state': {}})]  # never asked for evidence
    stopped = _nhplug(tmp_path / 'stopped')
    _jev_calls(stopped, calls, evidence=(0, 1))
    assert 'jev.evidence_missing' in _keys(stopped)
    started = _nhplug(tmp_path / 'started')  # the calls before the first evidence answer do not count
    _jev_calls(started, calls, evidence=range(3, 8))
    assert 'jev.evidence_missing' not in _keys(started)


def _market_context(nhplug, time, program_as_of, institution_at):
    (nhplug / 'shared-etf-paper' / 'market-context.json').write_text(json.dumps({
        'time': f'{DAY}{time}+09:00', 'index_intraday': {'markets': {'KOSPI': {'as_of': time[:5]}}},
        'program_intraday': {'markets': {'KOSPI': {'as_of': program_as_of, 'rows': []}}},
        'investor_rankings': {'investors': {'외국인': {'ranked_at': f'{DAY}{time}.000+09:00'},
                                            '기관': {'ranked_at': institution_at},
                                            '개인': {'ranked_at': '2026-11-16T20:20:00.000+09:00'}}}}, ensure_ascii=False))


def test_a_lagging_market_source_section_fires(tmp_path):
    nhplug = _nhplug(tmp_path / 'a')
    _market_context(nhplug, '10:18:30', '09:40', '2026-11-16T20:20:00.000+09:00')  # 기관 not out yet today: 78 min since 09:00
    keys = {k for k in _keys(nhplug) if k.startswith('source.lagging')}
    assert keys == {'source.lagging:program_intraday'}
    nhplug = _nhplug(tmp_path / 'b')
    _market_context(nhplug, '12:10:00', '12:09', '2026-11-17T09:30:00.000+09:00')  # Toss posts 기관 in batches
    keys = _keys(nhplug, NOW.replace(hour=12, minute=15))
    assert {k for k in keys if k.startswith('source.lagging')} == {'source.lagging:investor_rankings'}
    assert '기관' in keys['source.lagging:investor_rankings']


def test_ledger_ingest_error_marker_fires(tmp_path):
    nhplug = _nhplug(tmp_path)
    assert 'ledger.ingest_error:shared-etf-paper' not in _keys(nhplug)
    (nhplug / 'shared-etf-paper' / 'ledger-ingest-error.json').write_text(json.dumps(
        {'time': DAY + '10:10:00+09:00', 'event': 'new_event', 'error_type': 'ValueError', 'detail': 'unknown event'}))
    assert 'new_event' in _keys(nhplug)['ledger.ingest_error:shared-etf-paper']


def _append_events(nhplug, *events):
    with (nhplug / 'shared-etf-paper' / 'events.jsonl').open('a') as stream:
        stream.writelines(json.dumps({'time': f'{DAY}{t}+09:00', 'event': e}) + '\n' for t, e in events)


def test_a_restart_without_a_restart_record_fires(tmp_path):
    later = NOW.replace(minute=40)
    nhplug = _nhplug(tmp_path / 'a')
    _append_events(nhplug, ('09:30:00', 'stopped'), ('09:50:00', 'stopped'), ('10:37:00', 'stopped'), ('10:38:00', 'market_context'))
    (nhplug / 'shared-etf-paper' / 'restarts.jsonl').write_text(json.dumps(
        {'time': DAY + '09:49:00+09:00', 'result': 'restarted', 'stopped': {'time': DAY + '09:50:00+09:00'}}) + '\n')
    detail = _keys(nhplug, later)['runner.restart_unrecorded:shared-etf-paper']
    assert '09:30:00' in detail and '09:50:00' not in detail and '10:37:00' not in detail  # 10:37: a restart may still be writing
    nhplug = _nhplug(tmp_path / 'b')
    _append_events(nhplug, ('10:30:00', 'stopped'))  # the runner's own end, nothing after it
    assert 'runner.restart_unrecorded:shared-etf-paper' not in _keys(nhplug, later)


def _live_accounts(nhplug):
    import accounts
    live = {'kind': 'live', 'auto': True, 'budget': dict(accounts.DEFAULT_BUDGET['live'])}
    (nhplug / 'accounts.json').write_text(json.dumps({'accounts': {
        'live-1': {**live, 'label': '실계좌 1', 'engine': 'jev', 'hash': accounts.digest('00000000001')},
        'live-2': {**live, 'label': '실계좌 2', 'engine': 'longterm', 'hash': accounts.digest('00000000002')}}}))


def _live_fill(nhplug, key, time):
    import live_fills
    with sqlite3.connect(nhplug / live_fills.NAME) as db:
        db.execute(live_fills.SCHEMA)
        db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?,?,?,?)', (key, '20261117', 7, '114800', 'BUY', 1, 1000.0,
                                                                    f'{DAY}{time}+09:00', f'{DAY}10:00:00+09:00'))


def test_live_balance_gaps_and_unrecovered_fill_times_fire(tmp_path):
    nhplug = _nhplug(tmp_path / 'a')
    _live_accounts(nhplug)
    (nhplug / 'live-balance-live-1.jsonl').write_text(json.dumps({'time': DAY + '09:30:00+09:00', 'total_assets': 1}) + '\n')
    (nhplug / 'live-balance-live-2.jsonl').write_text(json.dumps({'time': DAY + '09:41:00+09:00', 'total_assets': 1}) + '\n')
    _live_fill(nhplug, 'live-1', '00:00:00')  # NH gave no clock and no executor record names the order
    keys = _keys(nhplug)
    assert {k for k in keys if k.startswith(('live.balance_missing', 'live.fill_time_unknown'))} == {
        'live.balance_missing:live-1', 'live.fill_time_unknown:live-1'}  # long-term executor idles after its plan
    nhplug = _nhplug(tmp_path / 'b')
    _live_accounts(nhplug)
    (nhplug / 'live-balance-live-1.jsonl').write_text(json.dumps({'time': DAY + '10:15:00+09:00', 'total_assets': 1}) + '\n')
    _live_fill(nhplug, 'live-1', '09:31:05')
    keys = _keys(nhplug)
    assert {k for k in keys if k.startswith(('live.balance_missing', 'live.fill_time_unknown'))} == {'live.balance_missing:live-2'}


def test_live_mirror_exit_lag_reports_pending_and_clears_after_reconciliation(tmp_path):
    nhplug = _nhplug(tmp_path)
    _live_accounts(nhplug)
    log = nhplug / 'live-follow.jsonl'
    lagging = {'account': 'live-1', 'time': DAY + '10:19:30+09:00', 'mode': 'execute', 'action': 'RECONCILE',
               'pending': 2, 'held': {'090710': 29}, 'target': {},
               'lag_alert': {'minutes': 5.2, 'excess': {'090710': 29}}}
    replacement = {**lagging, 'time': DAY + '10:18:00+09:00', 'action': 'SELL',
                   'replacement_recheck_seconds': 2.5, 'same_cycle_replacement': True}
    log.write_text(json.dumps(replacement) + '\n' + json.dumps(lagging) + '\n')
    facts = audit.collect(nhplug, NOW, run=_launchctl, holidays=frozenset())
    assert [row['replacement_recheck_seconds'] for row in facts['live']['replacement_checks']] == [2.5]
    assert audit.live(nhplug, NOW.date().isoformat(), NOW)['replacement_checks'] == []
    last = facts['live']['last']['live-1']
    assert last['pending'] == 2 and last['held'] == {'090710': 29} and last['target'] == {}
    finding = next(row for row in facts['findings'] if row['key'] == 'live.mirror_exit_lag:live-1')
    assert '090710 29주' in finding['detail']
    assert '미종결 2건' in finding['detail']

    with log.open('a') as stream:
        stream.write(json.dumps({**lagging, 'time': DAY + '10:19:59+09:00', 'action': 'RECONCILED',
                                 'pending': 0, 'held': {'090710': 0}}) + '\n')
    assert 'live.mirror_exit_lag:live-1' not in _keys(nhplug)


def test_every_audit_run_folds_its_findings_into_the_incident_history(tmp_path):
    import audit_incidents
    nhplug = _nhplug(tmp_path)
    first = audit.collect(nhplug, NOW, run=_launchctl, holidays=frozenset())['incidents']
    assert 'intents.unfinished:shared-etf-paper' in [i['key'] for i in first['new']]
    (nhplug / 'shared-etf-paper' / 'ledger-ingest-error.json').write_text(json.dumps({'event': 'x', 'error_type': 'ValueError'}))
    second = audit.collect(nhplug, NOW.replace(minute=40), run=_launchctl, holidays=frozenset())['incidents']
    assert 'ledger.ingest_error:shared-etf-paper' in [i['key'] for i in second['new']]
    assert 'intents.unfinished:shared-etf-paper' in [i['key'] for i in second['open']]
    counts = lambda db: {r['key']: r['count'] for r in audit_incidents.history(db)}  # noqa: E731
    assert counts(nhplug / 'audit-incidents.sqlite3')['intents.unfinished:shared-etf-paper'] == 2
    elsewhere = tmp_path / 'scratch.sqlite3'  # --incidents-db: a dry run must not touch the real history
    audit.collect(nhplug, NOW.replace(minute=50), run=_launchctl, holidays=frozenset(), incidents_db=elsewhere)
    key = 'intents.unfinished:shared-etf-paper'
    assert counts(elsewhere)[key] == 1 and counts(nhplug / 'audit-incidents.sqlite3')[key] == 2


def test_session_calls_without_intraday_overseas_futures_fire(tmp_path):
    """Phase 1 (user 2026-11-18): the market summary carries intraday US futures, gold, oil, dollar (global_intraday)."""
    times = [f'10:{m:02d}' for m in range(6)]
    missing = _nhplug(tmp_path / 'missing')
    view = {'as_of': f'{DAY}10:00', 'index_intraday': {'코스피': {'as_of': '10:00'}}}
    _jev_calls(missing, [(t, 'candidate', _good_call(t, market_regime_themes_global=view)) for t in times], evidence=range(6))
    assert 'jev.global_intraday_missing' in _keys(missing)
    present = _nhplug(tmp_path / 'present')
    _jev_calls(present, [(t, 'candidate', _good_call(t)) for t in times], evidence=range(6))
    assert 'jev.global_intraday_missing' not in _keys(present)


def test_lagging_overseas_futures_and_theme_leaders_fire(tmp_path):
    nhplug = _nhplug(tmp_path)
    path = nhplug / 'shared-etf-paper' / 'market-context.json'
    payload = {'time': f'{DAY}10:18:30+09:00',
               'global_intraday': {'markets': {'금 선물': {'as_of': f'{DAY}10:08+09:00'}, '나스닥100 선물': {'as_of': f'{DAY}09:30+09:00'}}},
               'theme_leaders': {'themes': [{'theme': '로봇', 'as_of': '10:16'}]}}
    path.write_text(json.dumps(payload))
    keys = _keys(nhplug)
    assert 'source.lagging:global_intraday' in keys and '나스닥100 선물' in keys['source.lagging:global_intraday']
    assert '금 선물' not in keys['source.lagging:global_intraday']  # 10 minutes: Yahoo's usual delay
    assert 'source.lagging:theme_leaders' not in keys
    payload['theme_leaders']['themes'][0]['as_of'] = '09:40'
    path.write_text(json.dumps(payload))
    assert 'source.lagging:theme_leaders' in _keys(nhplug)


def test_shadow_candidate_rounds_that_stop_or_fail_fire(tmp_path):
    """The shadow recorder (issue_candidates, market_movers.shadow_round) writes shadow-status.json every round."""
    nhplug = _nhplug(tmp_path)
    status = nhplug / 'shared-etf-paper' / 'shadow-status.json'
    assert not {k for k in _keys(nhplug) if k.startswith('shadow.')}  # never ran: nothing to judge yet
    status.write_text(json.dumps({'time': f'{DAY}10:16:00+09:00', 'recorded': 3, 'errors': {}}))
    assert not {k for k in _keys(nhplug) if k.startswith('shadow.')}
    status.write_text(json.dumps({'time': f'{DAY}09:50:00+09:00', 'recorded': 3, 'errors': {}}))
    assert 'shadow.silent' in _keys(nhplug)  # 30 minutes without a round
    status.write_text(json.dumps({'time': f'{DAY}10:16:00+09:00', 'recorded': 0, 'errors': {'rankings': 'KiwoomError'}}))
    assert 'shadow.source_error:rankings' in _keys(nhplug)


def test_a_service_that_ends_on_schedule_is_not_reported_down():
    """11/18 15:06: the regime observer stops itself at 15:00 (exit 0) and was reported service.down until 15:20."""
    facts = {'trading_day': True, 'services': {name: {'registered': True, 'pid': 1} for name in audit.SESSION_SERVICES},
             'ledgers': {},
             'live': {'live_orders': {}}}
    facts['services']['shared-etf-regime'] = {'registered': True, 'pid': None, 'last_exit': 0}
    late = audit.anomalies(facts, NOW.replace(hour=15, minute=6))
    assert 'service.down:shared-etf-regime' not in late
    early = audit.anomalies(facts, NOW.replace(hour=14, minute=30))
    assert 'service.down:shared-etf-regime' in early  # stopped mid-session is still a finding


def test_an_audit_run_after_the_session_leaves_session_findings_open(tmp_path):
    # the 15:20 run is out of session (checks skipped); it must not mark the session's findings resolved
    nhplug = _nhplug(tmp_path)
    _live_accounts(nhplug)
    keys = lambda result, status: [i['key'] for i in result['incidents'][status]]  # noqa: E731
    first = audit.collect(nhplug, NOW, run=_launchctl, holidays=frozenset())
    assert 'live.balance_missing:live-1' in keys(first, 'new')
    late = audit.collect(nhplug, NOW.replace(hour=15, minute=21), run=_launchctl, holidays=frozenset())
    assert 'live.balance_missing:live-1' not in keys(late, 'resolved')
    nextday = audit.collect(nhplug, NOW.replace(day=18), run=_launchctl, holidays=frozenset())
    assert 'live.balance_missing:live-1' in keys(nextday, 'open') and not keys(nextday, 'recurred')


def _bare_facts(**over):
    up = {'registered': True, 'pid': 1, 'last_exit': 0}
    return {'trading_day': True, 'services': {n: dict(up) for n in audit.SERVICES}, 'ledgers': {},
            'live': {'live_orders': {}, 'accounts': {}}, 'jev_inputs': None, 'shadow': None, 'sources': None, **over}


def test_status_files_left_from_an_earlier_day_fire():
    """market-movers never started today: shadow-status.json and market-context.json still hold yesterday 15:03."""
    found = audit.anomalies(_bare_facts(shadow={'time': '2026-11-16T15:03:59+09:00', 'errors': {'rankings': 'old'}},
                                        sources={'time': '2026-11-16T15:03:00+09:00', 'lag_min': {}}), NOW)
    assert 'shadow.silent' in found and 'source.lagging:market_context' in found
    assert 'shadow.source_error:rankings' not in found  # yesterday's error is not today's
    early = audit.anomalies(_bare_facts(shadow={'time': '2026-11-16T15:03:59+09:00', 'errors': {}},
                                        sources={'time': '2026-11-17T08:58:00+09:00', 'lag_min': {}}), NOW.replace(hour=9, minute=6))
    assert not {k for k in early if k.startswith(('shadow.', 'source.'))}  # the 09:00 loop's first round is not due yet


def test_session_loops_that_die_fire():
    """shared-etf-learning (all session) and market-movers (09:00~15:05) are session loops too."""
    facts = _bare_facts()
    for name in ('shared-etf-learning', 'market-movers'):
        facts['services'][name] = {'registered': True, 'pid': None, 'last_exit': 1}
    assert {'service.down:shared-etf-learning', 'service.down:market-movers'} <= set(audit.anomalies(facts, NOW))
    late = audit.anomalies(facts, NOW.replace(hour=15, minute=10))
    assert 'service.down:market-movers' not in late and 'service.down:shared-etf-learning' in late


def test_a_learning_watcher_failing_every_round_fires(tmp_path):
    """11-19 09:03~10:26: shared-etf-learning was running (no service.down) but every round printed DatabaseError."""
    nhplug = _nhplug(tmp_path)
    log = nhplug / 'shared-etf-paper' / 'com.vibetrading.shared-etf-learning.log'
    rows = [{'time': f'{DAY}10:1{m}:00+09:00', 'error_type': 'DatabaseError'} for m in range(5, 9)]
    log.write_text('WARN x\n' + '\n'.join(json.dumps(r) for r in rows) + '\n')
    assert 'learning.error' in _keys(nhplug)
    log.write_text('\n'.join(json.dumps(r) for r in rows) + '\n' + json.dumps({'time': f'{DAY}10:19:00+09:00', 'imported': 0}) + '\n')
    assert 'learning.error' not in _keys(nhplug)


def test_a_corrupted_database_fires_even_when_quick_check_would_pass(tmp_path):
    """11-19 10:36: the ledger's index was damaged (integrity_check 'Child page depth differs') while quick_check said
    ok; every audit run now runs integrity_check on the record databases, read-only."""
    nhplug = _nhplug(tmp_path)
    assert not {k for k in _keys(nhplug) if k.startswith('db.')}
    (nhplug / 'live-orders.sqlite3').write_bytes(b'SQLite format 3\x00' + b'\x07' * 4000)
    assert 'db.corrupt:live-orders.sqlite3' in _keys(nhplug)


def test_jev_entry_calls_failing_several_cycles_fires(tmp_path):
    """11-24 13:12~13:19: seven cycles in a row had no Jev answer (no market story within 60 minutes) and no audit key named it."""
    nhplug = _nhplug(tmp_path)
    gate = lambda hhmm: json.dumps({'time': f'{DAY}{hhmm}:12+09:00', 'event': 'v2_entry_gate', 'permitted': False,
                                    'choice': 'WAIT', 'reason': 'jev_error'})
    events = nhplug / 'shared-etf-paper' / 'events.jsonl'
    with events.open('a') as f:
        f.write('\n'.join([gate('10:05'), gate('10:06')]) + '\n')
    assert not {k for k in _keys(nhplug) if k.startswith('entry.jev_stopped')}
    with events.open('a') as f:
        f.write(gate('10:07') + '\n')
    assert '3회차' in next(v for k, v in _keys(nhplug).items() if k.startswith('entry.jev_stopped'))


def test_the_same_pick_refused_minutes_in_a_row_fires(tmp_path):
    """11-19 13:44~14:46: 009150 was picked and refused (single_order_limit) every minute; the user called a per-minute
    stall fatal. Three refusals of the same symbol and reason in consecutive minutes is a finding; a gap breaks the run."""
    nhplug = _nhplug(tmp_path)
    events = nhplug / 'shared-etf-paper' / 'events.jsonl'
    gate = lambda hhmm, symbol='009150': json.dumps({'time': f'{DAY}{hhmm}:12+09:00', 'event': 'v2_execution_gate',
                                                     'permitted': False, 'reason': 'single_order_limit', 'symbol': symbol})
    with events.open('a') as f:
        f.write('\n'.join([gate('10:05'), gate('10:06'), gate('10:10', '000002'), gate('10:14', '000002')]) + '\n')
    assert not {k for k in _keys(nhplug) if k.startswith('entry.repeat_block')}  # two in a row; a 4-minute gap
    with events.open('a') as f:
        f.write(gate('10:07') + '\n')
    found = _keys(nhplug)
    assert found['entry.repeat_block:009150:single_order_limit'].startswith('10:05~10:07 3분 연속')
    assert 'entry.repeat_block:000002:single_order_limit' not in found


def test_nh_same_query_limit_hits_in_the_window_fire(tmp_path):
    """11-19 11:00~15:10: NH refused the runner's second balance page 167 times (IGW42903, ~40 an hour) and no audit
    noticed; the retry hid it. Ten or more same-query refusals for one caller in the window are a finding."""
    nhplug = _nhplug(tmp_path)
    with sqlite3.connect(nhplug / 'api-calls.sqlite3') as db:
        db.execute('CREATE TABLE failures (time TEXT, provider TEXT, endpoint TEXT, caller TEXT, kind TEXT, status INTEGER, detail TEXT)')
        hit = lambda m, caller='nh_paper_isolated', code='IGW42903': (f'{DAY}10:{m:02d}:00+09:00', 'nh', 'mcp:krstockInquiryBalance',
                                                                       caller, 'response', None, f'tool error: rate_limit {code}')
        db.executemany('INSERT INTO failures VALUES (?,?,?,?,?,?,?)',
                       [hit(m) for m in range(9)] + [hit(m, 'nh_live_follow') for m in range(3)] + [hit(m, code='IGW42902') for m in range(20)])
    assert not {k for k in _keys(nhplug) if k.startswith('nh.same_query_limit')}  # 9 same-query hits; the per-app code is another
    with sqlite3.connect(nhplug / 'api-calls.sqlite3') as db:
        db.execute('INSERT INTO failures VALUES (?,?,?,?,?,?,?)', hit(9))
    found = _keys(nhplug)
    assert found['nh.same_query_limit:nh_paper_isolated'].startswith('IGW42903 10건')
    assert 'nh.same_query_limit:nh_live_follow' not in found


def test_a_rejected_live_balance_fires(tmp_path):
    nhplug = _nhplug(tmp_path)
    assert not {k for k in _keys(nhplug) if k.startswith('live.zero_total')}
    (nhplug / 'balance-rejected.jsonl').write_text(json.dumps({'time': f'{DAY}10:20:00+09:00', 'account': 'live-1', 'reason': '총자산 0원'}) + '\n')
    assert _keys(nhplug)['live.zero_total:live-1'].startswith('잘못된 잔고 1건')


def test_nh_app_rate_limit_hits_in_the_window_fire(tmp_path):
    """11-19: the movers scan's 60-stock burst every five minutes ran past NH's 5 calls a second per app key: 157 of its
    own quotes (~26 an hour) and 110 runner, mirror and dashboard calls within 3 s of it were refused (IGW42902)."""
    nhplug = _nhplug(tmp_path)
    with sqlite3.connect(nhplug / 'api-calls.sqlite3') as db:
        db.execute('CREATE TABLE failures (time TEXT, provider TEXT, endpoint TEXT, caller TEXT, kind TEXT, status INTEGER, detail TEXT)')
        hit = lambda m, caller='market_movers': (f'{DAY}10:{m:02d}:00+09:00', 'nh', 'mcp:get_stock_price', caller, 'response', None,
                                                 'tool error: rate_limit IGW42902')
        db.executemany('INSERT INTO failures VALUES (?,?,?,?,?,?,?)', [hit(m) for m in range(19)] + [hit(m, 'dashboard') for m in range(3)])
    assert not {k for k in _keys(nhplug) if k.startswith('nh.app_rate_limit')}  # 19 in the window: under the line
    with sqlite3.connect(nhplug / 'api-calls.sqlite3') as db:
        db.execute('INSERT INTO failures VALUES (?,?,?,?,?,?,?)', hit(19))
    found = _keys(nhplug)
    assert found['nh.app_rate_limit:market_movers'].startswith('IGW42902 20건')
    assert 'nh.app_rate_limit:dashboard' not in found and not {k for k in found if k.startswith('nh.same_query_limit')}


def test_api_load_findings_join_the_hourly_audit_without_repeating_the_nh_refusals(tmp_path):
    nhplug = _nhplug(tmp_path)
    with sqlite3.connect(nhplug / 'api-calls.sqlite3') as db:
        db.execute('CREATE TABLE failures (time TEXT, provider TEXT, endpoint TEXT, caller TEXT, kind TEXT, status INTEGER, detail TEXT)')
        db.executemany('INSERT INTO failures VALUES (?,?,?,?,?,?,?)', [
            *[(f'{DAY}10:15:0{s}+09:00', 'nh', 'mcp:x', 'dashboard', 'network', None, 'RuntimeError') for s in range(3)],
            *[(f'{DAY}10:15:0{s}+09:00', 'nh', 'mcp:x', 'dashboard', 'response', None, 'tool error: rate_limit IGW42903') for s in range(4)]])
    found = _keys(nhplug)
    assert '→' in found['api.local_start:nh'] and 'api.refused:nh' not in found


def _omni(answers):
    return lambda args: answers.get(' '.join(args), (0, {}))


def _cli_nhplug(tmp_path):
    nhplug = _nhplug(tmp_path)
    with sqlite3.connect(nhplug / 'live-orders.sqlite3') as db:
        db.execute('CREATE TABLE live_halts (scope TEXT, day TEXT)')
    return nhplug


TRADES = {'trades': [{'account': '모의계좌 (공유)', 'side': 'BUY', 'symbol': '005930', 'qty': 3}, {'account': '실계좌 1', 'side': 'BUY', 'symbol': '005930', 'qty': 1}]}


def test_a_cli_answer_that_disagrees_with_the_records_fires(tmp_path):
    nhplug = _cli_nhplug(tmp_path)
    status = {'live_accounts': {'live-1': {'loss_halt_today': False}}}
    good = {'status trades': (0, TRADES), 'live status': (0, status)}
    assert audit.cli_health(nhplug, NOW, _omni(good), reference=lambda: True) == {}
    wrong = {'trades': [{**TRADES['trades'][0], 'qty': 2}]}
    with sqlite3.connect(nhplug / 'live-orders.sqlite3') as db:
        db.execute("INSERT INTO live_halts VALUES ('s','20261117')")
    found = audit.cli_health(nhplug, NOW, _omni({**good, 'status trades': (0, wrong), 'jev blocks': (1, None), 'jev positions': (5, {})}), reference=lambda: False)
    assert set(found) == {'cli.mismatch:paper-fills', 'cli.mismatch:loss-halt', 'cli.failed:jev-blocks', 'cli.reference_stale'}  # 5 is stale data, said as such
    # the fill recorded while the CLI was read is not a mismatch
    def filling(args):
        if args == ['status', 'trades']:
            with sqlite3.connect(nhplug / 'shared-etf-paper' / 'orders.sqlite3') as db:
                db.execute("INSERT INTO fills VALUES ('b','005930','SELL',3,70000,'2026-11-17T10:20:00+09:00')")
            return 0, TRADES
        return _omni({**good, 'live status': (0, {'live_accounts': {'live-1': {'loss_halt_today': True}}})})(args)
    assert audit.cli_health(nhplug, NOW, filling, reference=lambda: True) == {}


def test_the_cli_check_reaches_the_findings_and_its_absence_resolves_nothing(tmp_path):
    nhplug = _cli_nhplug(tmp_path)
    facts = audit.collect(nhplug, NOW, run=_launchctl, holidays=frozenset(), cli=lambda *a: {'cli.failed:live-status': '종료 1'})
    assert 'cli.failed:live-status' in {f['key'] for f in facts['findings']}
    later = audit.collect(nhplug, NOW.replace(minute=40), run=_launchctl, holidays=frozenset())  # not asked
    assert 'cli.failed:live-status' not in {i['key'] for i in later['incidents'].get('resolved', [])}
