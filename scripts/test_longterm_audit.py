import json
import sqlite3
from datetime import date, datetime

import pytest

import longterm_audit as A
import vibe_longterm as L

KST = L.KST


@pytest.fixture(autouse=True)
def calendar(monkeypatch):
    monkeypatch.setattr(L, '_HOLIDAYS', {y: frozenset() for y in range(2024, 2031)}, raising=False)


def at(day, hh=8, mm=45):
    return datetime.combine(day, datetime.min.time(), KST).replace(hour=hh, minute=mm)


def seed(root):
    """Mon 11-16 full day, Tue 11-17 story fell back and the trade step failed, Wed 11-18 nothing."""
    db = L.connect(root)
    for d in (date(2026, 11, 16), date(2026, 11, 17)):
        day = d.isoformat()
        L.log(db, 'regime', day, regime='correction', raw='correction', mode='manual', modules=['bogle_core'], reason='조정', now=at(d))
        L.log(db, 'macro', day, mode='manual', indicators={'series': {}}, reason='거시', now=at(d))
    L.log(db, 'story', '2026-11-16', regime='correction', indicators={}, reason='[llm] 유지', now=at(date(2026, 11, 16)),
          targets={'source': 'llm', 'inputs': {'premarket': True}, 'sources': ['Reuters · 2026-11-16 · x']})
    L.log(db, 'story', '2026-11-17', regime='correction', indicators={}, reason='[default (TimeoutError)] ', now=at(date(2026, 11, 17)))
    L.log(db, 'plan', '2026-11-16', regime='correction', orders=[{'key': 'NDX', 'side': 'BUY', 'qty': 1, 'price': 1}], reason='초기 투입',
          targets={'why': 'initial_deployment', 'notes': [['lynch_story', '주의 → 절반']]}, now=at(date(2026, 11, 16), 9, 40))
    L.log(db, 'error', '2026-11-17', reason='trade: error:ValueError:quote unavailable', now=at(date(2026, 11, 17), 9, 40))
    with db:
        db.executemany('INSERT INTO nav VALUES (?,?,?)', [('2026-11-16', 'actual', 100_000_000), ('2026-11-17', 'actual', 99_000_000),
                                                           ('2026-11-16', 'base', 100_000_000), ('2026-11-17', 'base', 99_500_000)])
    (root / 'reports').mkdir()
    (root / 'reports' / 'strategy-2026-11-16.md').write_text('# x', encoding='utf-8')


def test_collect_finds_missing_inputs_fallbacks_errors_and_reports(tmp_path):
    seed(tmp_path)
    data = A.collect(tmp_path, date(2026, 11, 18), days=3, journal_dir=tmp_path / 'none')
    assert data['days'] == ['2026-11-16', '2026-11-17', '2026-11-18']
    by_day = {d['day']: d for d in data['coverage']}
    assert by_day['2026-11-16']['story_source'] == 'llm' and by_day['2026-11-16']['story_sources'] == 1
    assert by_day['2026-11-17']['story_source'].startswith('default') and by_day['2026-11-17']['errors'] == 1
    assert set(by_day['2026-11-18']['missing']) >= {'regime', 'macro', 'story', 'trade'}
    assert by_day['2026-11-16']['report'] and not by_day['2026-11-17']['report']
    found = ' '.join(data['findings'])
    assert '2026-11-18' in found and '기본값' in found and 'quote unavailable' in found and '리포트' in found
    assert data['nav']['actual']['change'] == -1_000_000 and data['nav']['base']['change'] == -500_000
    assert data['trades']['notes_by_module'] == {'lynch_story': 1}
    json.dumps(data, ensure_ascii=False)  # the scheduled task reads it as JSON


def test_blank_report_fails_the_check_until_written_and_keys_name_the_period(tmp_path):
    key = '2026-11-20'
    blank = A.blank(key)
    assert blank.splitlines()[0] == A.title(key) and A.check(blank, key)
    done = blank.replace(A.PLACEHOLDER, '내용')
    assert A.check(done, key) == []
    assert A.title('2026-11') == '# 장기투자 월간 감사 · 2026-11' and '일일 복기·감사' in A.title(key) and '주간 감사' in A.title('2026-W47')
    assert A.path(tmp_path, key) == tmp_path / 'reviews' / 'audit-2026-11-20.md'
    assert A.window('2026-11') == (date(2026, 11, 30), 21) and A.window('2026-11-20') == (date(2026, 11, 20), 1)
    assert A.window('2026-W47') == (date(2026, 11, 20), 5)
    assert A.sections(key) == A.DAY_SECTIONS and A.sections('2026-W47') == A.SECTIONS
    week = A.blank('2026-W47').replace(A.PLACEHOLDER, '내용')
    assert A.check(week, '2026-W47') == [] and A.check(week, key)  # a weekly body is not a daily report


def test_days_before_the_engine_started_are_not_findings_and_setting_changes_are_listed(tmp_path, monkeypatch):
    seed(tmp_path)
    history = tmp_path / 'h.sqlite3'
    con = sqlite3.connect(history)
    con.execute('CREATE TABLE changes (time TEXT, kind TEXT, key TEXT, old TEXT, new TEXT, who TEXT)')
    con.executemany('INSERT INTO changes VALUES (?,?,?,?,?,?)', [
        ('2026-11-17T12:02:24+09:00', 'vibe-longterm', 'execute', 'false', 'true', 'chat:user-approved'),
        ('2026-11-17T12:03:00+09:00', 'strategy', 'x', '1', '2', 'dashboard'),
        ('2026-11-01T12:00:00+09:00', 'vibe-longterm', 'mode', '"a"', '"b"', 'dashboard')])
    con.commit()
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(history))
    data = A.collect(tmp_path, date(2026, 11, 17), days=4, journal_dir=tmp_path / 'none')
    assert data['days'][0] == '2026-11-12' and data['started'] == '2026-11-16'
    assert not any(f.startswith(('2026-11-12', '2026-11-13')) for f in data['findings'])
    assert [c['key'] for c in data['settings_changes']] == ['execute']


def test_publish_writes_only_a_passing_draft_as_the_report(tmp_path, monkeypatch, capsys):
    """2026-11-18: the scheduled task publishes with one allowed command instead of mv + chmod (each waited for approval)."""
    monkeypatch.setattr(L, 'ROOT', tmp_path)
    key = '2026-11-20'
    draft = tmp_path / 'draft.md'
    draft.write_text(A.blank(key), encoding='utf-8')
    monkeypatch.setattr('sys.argv', ['x', '--check', key, '--file', str(draft), '--publish'])
    A.main()
    assert json.loads(capsys.readouterr().out)['published'] is None and not A.path(tmp_path, key).exists()
    draft.write_text(A.blank(key).replace(A.PLACEHOLDER, '내용'), encoding='utf-8')
    A.main()
    out = json.loads(capsys.readouterr().out)
    assert out['ok'] and out['published'] == str(A.path(tmp_path, key)) and oct(A.path(tmp_path, key).stat().st_mode)[-3:] == '600'


def test_the_daily_masters_section_reads_each_master_by_their_own_principle(tmp_path):
    """2026-11-18 (보완안 3): the review's 거장 관점 follows the strategy report's 거장별 관점 의견 — the seven masters by
    their own principle, against what the engine and the accounts did; the morning report's views come with the data."""
    import longterm_report as R
    blank = A.blank('2026-11-19')
    assert all(f"**{m['name']}**" in blank for m in R.MASTER_VIEWS.values())
    done = blank.replace(A.PLACEHOLDER, '내용')
    assert A.check(done, '2026-11-19') == []
    gone = done.replace('**여운봉**', '여운')
    assert any('여운봉' in p for p in A.check(gone, '2026-11-19'))
    assert A.check(gone.replace('2026-11-19', '2026-11-18'), '2026-11-18') == []  # published before the section existed
    (tmp_path / 'reports').mkdir()
    (tmp_path / 'reports' / 'strategy-2026-11-19.json').write_text(json.dumps({'master_views': {'yeo': {'action': '기다리기'}}}), encoding='utf-8')
    facts = A.collect(tmp_path, date(2026, 11, 19), 1)
    assert facts['masters']['morning']['2026-11-19']['yeo']['action'] == '기다리기'
    assert set(facts['masters']['principles']) == set(R.MASTER_VIEWS) and facts['masters']['yeo_accounts'] == {}


def test_the_check_reads_an_unnumbered_heading_and_rejects_empty_sections():
    # review 2026-11-18: the 거장 관점 check wanted '## 4. 거장 관점' only, and a review of bare '-' bodies passed
    done = A.blank('2026-11-19').replace(A.PLACEHOLDER, '내용')
    assert A.check(done.replace('## 4. 거장 관점', '## 거장 관점'), '2026-11-19') == []
    bare = A.title('2026-W47') + '\n\n' + '\n\n'.join(f'## {i}. {s}\n\n-' for i, s in enumerate(A.SECTIONS, 1)) + '\n'
    assert any('비었습니다' in p for p in A.check(bare, '2026-W47'))
    with pytest.raises(ValueError, match='감사 키'):
        A.window('2026-13')


def test_collect_checks_orders_against_fills_every_day(tmp_path):
    # user 2026-11-18: the fill checks (unfilled orders, a send recovered after a restart, a 여운봉 step leg owed) run in every
    # daily review and audit, not once
    import sqlite3
    from live_fills import NAME, SCHEMA
    root = tmp_path / 'vibe-longterm'
    root.mkdir()
    seed(root)
    db = L.connect(root)
    L.log(db, 'order', '2026-11-18', regime='correction', now=at(date(2026, 11, 18), 9, 40), reason='여운봉 리밸런싱',
          orders=[{'key': 'NDX', 'code': '379810', 'side': 'SELL', 'qty': 3, 'filled': 3, 'price': 23_000},
                  {'key': 'NDXMIX', 'code': '0261H0', 'side': 'BUY', 'qty': 6, 'filled': 2, 'price': 11_000, 'unfilled_why': '매도호가 11,050원 > 주문가 11,000원'}])
    L.put_state(db, **{'book:actual': {'yeo_owed': {'NDXMIX': 44_006.6}}})
    log = tmp_path / 'longterm-live.jsonl'
    log.write_text('\n'.join(json.dumps(e) for e in (
        {'time': '2026-11-18T09:42:00+09:00', 'account': 'live-2', 'action': 'BUY', 'symbol': '379810', 'qty': 5, 'price': 23_000,
         'execution': 'ACKNOWLEDGED_NOT_FILLED'},
        {'time': '2026-11-18T09:43:00+09:00', 'account': 'live-2', 'action': 'BUY', 'symbol': '0261H0', 'qty': 6, 'price': 11_000,
         'execution': 'SENT_BEFORE_RESTART'})))
    with sqlite3.connect(tmp_path / NAME) as live:
        live.execute(SCHEMA)
        live.execute('INSERT INTO fills VALUES (?,?,?,?,?,?,?,?,?)', ('live-2', '2026-11-18', 1, '379810', 'BUY', 5, 23_000, '2026-11-18T09:42:10+09:00', 'x'))
    data = A.collect(root, date(2026, 11, 18), days=3, journal_dir=tmp_path / 'none', live_log=log, now=at(date(2026, 11, 18), 16, 45))
    fills = data['fills']
    assert fills['virtual'] == [{'day': '2026-11-18', 'key': 'NDXMIX', 'qty': 6, 'filled': 2, 'why': '매도호가 11,050원 > 주문가 11,000원'}]
    assert any('(매도호가 11,050원 > 주문가 11,000원)' in f for f in data['findings'])
    assert fills['live'] == [{'day': '2026-11-18', 'account': 'live-2', 'symbol': '0261H0', 'side': 'BUY', 'qty': 6, 'filled': 0}]
    assert fills['recovered'] == [{'day': '2026-11-18', 'account': 'live-2', 'symbol': '0261H0'}]
    assert fills['owed'] == {'actual': {'NDXMIX': 44_007}}
    found = ' '.join(data['findings'])
    assert '가상 주문 미체결' in found and '실계좌 주문 미체결' in found and '재시작 뒤 전송 복구' in found and '여운봉 못 산 몫' in found
    json.dumps(data, ensure_ascii=False)
    early = A.collect(root, date(2026, 11, 18), days=3, journal_dir=tmp_path / 'none', live_log=log, now=at(date(2026, 11, 18), 10, 0))
    assert early['fills']['live'] == []  # today's live orders still have the session to fill
