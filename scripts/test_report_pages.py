import json
import subprocess
from datetime import datetime, timedelta

import journal_template as JT
import premarket_template as PT
import report_pages as rp

JOURNAL = """# 매매일지 · 2026-11-17 (화)

> 코스피가 내린 날, 모의는 −54,328원이다.

## 1. 숫자로 보는 하루

| 계좌 | 실현손익 | 매수 대비 |
|---|---|---|
| 모의 | -54,328원 | -0.40% |
| 가상 | +10,920원 | +0.27% |
| 실계좌 | +0원 | −0.10% |

계좌 12345678901 은 가린다. [기사](https://example.com/a?id=12345678901)
"""


def runtime(tmp_path):
    reviews = tmp_path / 'nhplug/shared-etf-paper/reviews'
    reviews.mkdir(parents=True)
    (reviews / 'journal-2026-11-17.md').write_text(JOURNAL)
    (reviews / 'journal-2026-11-17.json').write_text(json.dumps({'summary': '요약 한 줄입니다.', 'top_issues': [{'title': '반도체 급락', 'stars': 5, 'why': '이유'}],
                                                                 'watch_next': ['엔비디아 실적']}, ensure_ascii=False))
    (reviews / 'premarket-2026-11-17.md').write_text('# 장전\n\n본문')
    (reviews / 'premarket-2026-11-17.prev-0745.md').write_text('# 이전 판')
    (reviews / 'journal-2026-11.md').write_text('# 11월 매매일지\n\n본문')
    (reviews / 'operations-audit-2026-11-17.md').write_text('# 내부')
    return tmp_path


CFG = {'repo': 'me/reports', 'url': 'https://me.github.io/reports/', 'branch': 'gh-pages', 'push': False, 'token': '', 'missing': []}


def finished_premarket(day):
    """A premarket analysis that passes premarket_template.check(written=True)."""
    return '\n'.join([PT.title(day), '', '> 요약 한 줄', '', '작성: 11/17 08:40 KST (개장 전)', '']
                     + [f'## {h}\n\n본문 [출처](https://example.com/{i})\n' for i, h in enumerate(PT.headings())])


def finished_period(kind, key):
    """A period journal that passes journal_template.check(written=True)."""
    return '\n'.join([JT.title(kind, key), '', '> 요약 한 줄', '']
                     + [f'## {h}\n\n본문 [출처](https://example.com/{i})\n' for i, h in enumerate(JT.headings('period'))])


def setup(tmp_path, monkeypatch, **cfg):
    monkeypatch.setattr(rp, 'ensure_assets', lambda site=None: None)
    monkeypatch.setattr(rp, 'LOCK', tmp_path / 'report-pages.lock')  # never the live watcher's lock
    rt = runtime(tmp_path)
    site, db = tmp_path / 'site', tmp_path / 'rp.sqlite3'
    return rt, site, db, {**CFG, **cfg}


def fake_github(calls, exists=False):
    def api(method, path, cfg, body=None):
        calls.append((method, path))
        if method == 'GET' and path == '/user':
            return 200, {'login': 'me'}
        if method == 'GET':
            return (200, {}) if exists else (404, {})
        return 201, {}
    return api


def test_ensure_repository_creates_public_repo_once():
    calls = []
    assert rp.ensure_repository({**CFG, 'token': 't'}, fake_github(calls)) == 'created'
    assert rp.ensure_repository({**CFG, 'token': 't'}, fake_github([], exists=True)) == 'exists'
    assert rp.ensure_pages({**CFG, 'token': 't'}, fake_github([], exists=True)) == 'on'
    other = rp.ensure_repository({**CFG, 'repo': 'team/reports', 'token': 't'}, fake_github(calls))
    assert other == 'created' and ('POST', '/orgs/team/repos') in calls


def test_sources_pick_report_files_only(tmp_path):
    found = {(k, key) for k, key, _ in rp.sources(runtime(tmp_path))}
    assert found == {('journal', '2026-11-17'), ('premarket', '2026-11-17'), ('journal-period', '2026-11')}  # no .prev, no audit


def test_render_masks_accounts_colours_signs_and_passes_check(tmp_path):
    rt = runtime(tmp_path)
    page, meta = rp.render('journal', '2026-11-17', rt / 'nhplug/shared-etf-paper/reviews/journal-2026-11-17.md')
    assert '12345678901 은' not in page and '•••• 은' in page and 'id=12345678901' in page  # text masked, link kept
    assert '<span class="down">-54,328원</span>' in page and '<span class="up">+10,920원</span>' in page
    assert '<span class="up">+0원' not in page and '<span class="down">−0.10%</span>' in page  # zero plain, −0.10% is not zero
    assert meta['summary'] == '요약 한 줄입니다.' and '반도체 급락' in page and '엔비디아 실적' in page
    assert '코스피가 내린 날' not in page.split('<article')[1]  # the opening quote moved to the hero, not repeated
    assert rp.check(page) == []
    assert 'target="_blank" rel="noopener noreferrer"' in page and 'class="table-wrap"' in page


def test_check_flags_secrets():
    assert any('계좌번호' in i for i in rp.check('<html lang="ko"><h1>x</h1><p>1234567890123</p>'))
    assert any('토큰' in i for i in rp.check('<p>ghp_abc</p>'))


def test_generate_once_then_only_on_change(tmp_path, monkeypatch):
    rt, site, db, cfg = setup(tmp_path, monkeypatch)
    with rp.connect(db) as con:
        first = rp.scan(con, cfg, site, rt)
        assert set(first) == {'journal:2026-11-17', 'premarket:2026-11-17', 'journal-period:2026-11'}
        assert rp.scan(con, cfg, site, rt) == []  # unchanged: nothing rewritten
        row = con.execute("SELECT * FROM reports WHERE id='journal:2026-11-17'").fetchone()
        assert row['send_status'] == 'pending' and row['url'] == 'https://me.github.io/reports/reports/journal/2026-11-17.html'
        (rt / 'nhplug/shared-etf-paper/reviews/premarket-2026-11-17.md').write_text('# 장전\n\n고친 본문')
        assert rp.scan(con, cfg, site, rt) == ['premarket:2026-11-17']
    data = json.loads((site / 'reports/reports.json').read_text())
    assert {r['kind'] for r in data} == {'journal', 'premarket', 'journal-period'} and data[0]['date'] >= data[-1]['date']
    assert (site / 'reports/journal/2026-11-17.html').exists() and (site / 'reports/index.html').exists()
    # the Pages root belongs to the landing page (user 2026-11-19): the archive lives under reports/ with links relative to it
    assert not (site / 'index.html').exists() and not (site / 'reports.json').exists()
    assert {r['path'] for r in data} >= {'journal/2026-11-17.html'}
    index = (site / 'reports/index.html').read_text()
    assert 'href="../assets/report.css' in index and 'src="../assets/index.js' in index and 'src="../assets/mascot.png' in index
    page = (site / 'reports/journal/2026-11-17.html').read_text()
    assert 'href="../../reports/index.html">전체 리포트' in page and 'class="brandmark" href="../../index.html"' in page


def test_generate_failure_is_recorded(tmp_path, monkeypatch):
    rt, site, db, cfg = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(rp, 'render', lambda *a, **k: (_ for _ in ()).throw(ValueError('틀 오류')))
    with rp.connect(db) as con:
        rp.scan(con, cfg, site, rt)
        row = con.execute("SELECT * FROM reports WHERE id='premarket:2026-11-17'").fetchone()
        assert row['gen_status'] == 'failed' and '틀 오류' in row['gen_error']
        assert con.execute("SELECT ok FROM events WHERE action='generate'").fetchone()[0] == 0


def test_publish_states(tmp_path, monkeypatch):
    rt, site, db, cfg = setup(tmp_path, monkeypatch)
    bare = tmp_path / 'remote.git'
    subprocess.run(['git', 'init', '-q', '--bare', str(bare)], check=True)
    with rp.connect(db) as con:
        rp.scan(con, cfg, site, rt)
        assert rp.publish(con, {**cfg, 'missing': ['REPORT_PAGES_REPO']}, site) == 'not_configured'
        assert rp.publish(con, cfg, site) == 'off'  # REPORT_PAGES_PUSH not on: local commit only
        assert con.execute("SELECT DISTINCT publish_status FROM reports").fetchall()[0][0] == 'off'
        calls = []
        assert rp.publish(con, {**cfg, 'push': True, 'remote': str(bare)}, site, fake_github(calls)) == 'ok'
        assert {r[0] for r in con.execute('SELECT publish_status FROM reports')} == {'ok'}
        assert ('POST', '/user/repos') in calls and ('POST', '/repos/me/reports/pages') in calls  # made on the first push
        assert rp.publish(con, {**cfg, 'push': True, 'remote': str(bare)}, site, fake_github(calls)) == 'nothing'
    log = subprocess.run(['git', '--git-dir', str(bare), 'log', '--oneline', 'gh-pages'], capture_output=True, text=True).stdout
    assert log.strip()


def test_send_waits_for_live_page_then_sends_link(tmp_path, monkeypatch):
    rt, site, db, cfg = setup(tmp_path, monkeypatch)
    log = []
    sender = lambda url, text: (log.append((url, text)), (True, 0, False))[1]  # noqa: E731
    urls = {'realtime': 'http://rt', 'longterm': 'http://lt'}
    with rp.connect(db) as con:
        rp.scan(con, cfg, site, rt)
        rid = 'journal:2026-11-17'
        assert rp.send(con, rid, urls, sender, live=lambda u: True) == 'failed'  # not published yet
        assert 'Pages 게시 전' in con.execute('SELECT send_error FROM reports WHERE id=?', (rid,)).fetchone()[0]
        rp.update(con, rid, publish_status='ok', published_at=rp.now_iso())
        assert rp.send(con, rid, urls, sender, live=lambda u: False) == 'waiting'
        assert rp.send(con, rid, urls, sender, live=lambda u: True) == 'sent'
        assert 'https://me.github.io/reports/reports/journal/2026-11-17.html' in log[-1][1] and '요약 한 줄입니다.' in log[-1][1]
        rp.update(con, rid, publish_status='pending')
        assert rp.send(con, rid, urls, sender, live=lambda u: True) == 'failed'  # a manual resend before the push
        assert con.execute('SELECT send_status FROM reports WHERE id=?', (rid,)).fetchone()[0] == 'sent'  # stays delivered
        other = 'premarket:2026-11-17'
        rp.update(con, other, publish_status='ok', published_at=rp.now_iso())
        late = datetime.now(rp.KST) + timedelta(minutes=rp.LIVE_WAIT_MIN + 1)
        n = len(log)
        assert rp.send(con, other, urls, sender, live=lambda u: False, now=late) == 'waiting'  # never opened: no text (user 2026-11-18)
        row = con.execute('SELECT send_status, send_error FROM reports WHERE id=?', (other,)).fetchone()
        assert row['send_status'] == 'waiting_live' and '15분' in row['send_error'] and len(log) == n


def test_cycle_sends_only_new_published_reports(tmp_path, monkeypatch):
    rt, site, db, cfg = setup(tmp_path, monkeypatch)
    bare = tmp_path / 'remote.git'
    subprocess.run(['git', 'init', '-q', '--bare', str(bare)], check=True)
    log = []
    sender = lambda url, text: (log.append(text), (True, 0, False))[1]  # noqa: E731
    reviews = rt / 'nhplug/shared-etf-paper/reviews'
    (reviews / 'premarket-2026-11-17.md').write_text(finished_premarket('2026-11-17'))
    (reviews / 'journal-2026-11.md').write_text(finished_period('month', '2026-11'))
    out = rp.cycle({'realtime': 'http://rt', 'longterm': 'http://lt'},
                   cfg={**cfg, 'push': True, 'remote': str(bare)}, sender=sender, live=lambda u: True, db=db, site=site, runtime=rt,
                   api=fake_github([], exists=True))
    assert out['publish'] == 'ok' and set(out['sent']) == {'premarket:2026-11-17', 'journal-period:2026-11'}
    assert len(log) == 2
    with rp.connect(db) as con:  # the day journal does not follow its template: not linked yet
        assert con.execute("SELECT send_status FROM reports WHERE id='journal:2026-11-17'").fetchone()[0] == 'draft'


def test_clean_error_hides_secrets():
    text = rp.clean_error('fatal: https://x-access-token:ghp_ABC123@github.com/me/r.git ghp_ZZZ github_pat_11AA https://discord.com/api/webhooks/1/abc 12345678901')
    assert 'ghp_' not in text and 'github_pat_' not in text and 'webhooks/1' not in text and '12345678901' not in text


def test_settings_reads_names_and_never_exposes_token():
    cfg = rp.settings({'REPORT_PAGES_REPO': 'me/reports', 'GH_TOKEN': 'ghp_secret', 'REPORT_PAGES_PUSH': 'on'})
    assert cfg['ready'] and cfg['url'] == 'https://me.github.io/reports/'
    assert 'token' not in rp.public(cfg) and rp.public(cfg)['token_set'] is True
    assert rp.settings({})['missing'] == ['REPORT_PAGES_REPO', 'GH_TOKEN']


def test_period_labels_and_dates():
    assert rp.period_label('journal-period', '2026-Q4') == '2026년 4분기' and rp.sort_date('2026-H2') == '2026-07-01'
    assert rp.sort_date('2026-11') == '2026-11-01' and rp.period_label('journal', '2026-11-17') == '2026년 11월 17일 (화)'


def test_cycle_sends_nothing_until_pages_is_ready(tmp_path, monkeypatch):
    """User 2026-11-18: no report as plain text on Discord; reports wait and go out as links once Pages is deployed."""
    rt, site, db, cfg = setup(tmp_path, monkeypatch)
    log = []
    sender = lambda url, text: (log.append(text), (True, 0, False))[1]  # noqa: E731
    out = rp.cycle({'realtime': 'http://rt'}, cfg=cfg, sender=sender, live=lambda u: True, db=db, site=site, runtime=rt)
    assert out['publish'] == 'off' and out['sent'] == {} and log == []
    with rp.connect(db) as con:
        assert {r[0] for r in con.execute('SELECT send_status FROM reports')} == {'pending'}
    assert rp.retry('send', 'premarket:2026-11-17', {'realtime': 'http://rt'}, cfg=cfg, db=db, site=site, sender=sender) and log == []


def test_failed_source_is_not_regenerated_every_pass(tmp_path, monkeypatch):
    rt, site, db, cfg = setup(tmp_path, monkeypatch)
    (rt / 'nhplug/shared-etf-paper/reviews/premarket-2026-11-17.md').write_text('# 하나\n\n# 둘\n')  # two h1 fail the check
    with rp.connect(db) as con:
        for _ in range(3):
            rp.scan(con, cfg, site, rt)
        fails = con.execute("SELECT count(*) FROM events WHERE report_id='premarket:2026-11-17' AND action='generate' AND ok=0").fetchone()[0]
        assert fails == 1
        rp.scan(con, {**cfg, 'url': 'https://other.example/'}, site, rt)  # a new link address rebuilds pages
        assert con.execute("SELECT url FROM reports WHERE id='journal:2026-11-17'").fetchone()[0].startswith('https://other.example/')


def test_malformed_fields_do_not_fail_the_page(tmp_path):
    rt = runtime(tmp_path)
    reviews = rt / 'nhplug/shared-etf-paper/reviews'
    (reviews / 'journal-2026-11-17.json').write_text(json.dumps({'top_issues': [{'title': 'a', 'stars': 'high'}, {'title': 'b', 'stars': '4.5'}],
                                                                 'watch_next': 'abc', 'scenarios': 'x'}))
    (reviews / 'journal-2026-11-17.md').write_text('# t\n\n| a |\n|---|\n| -1.2.3 x |\n| +3,000원 |\n')
    page, _ = rp.render('journal', '2026-11-17', reviews / 'journal-2026-11-17.md')
    assert rp.check(page) == [] and '<li>a</li>' not in page.split('class="watch"')[-1][:40]
    assert '★★★★☆' in page and '<span class="up">+3,000원</span>' in page


def test_masking_covers_hangul_neighbours_hyphens_titles_and_toc(tmp_path):
    assert rp.mask('계좌12345678901원') == '계좌••••원' and rp.mask('123-45-678901') == '••••'
    assert rp.mask('2026-11-17 10:00') == '2026-11-17 10:00' and rp.mask('해시 0123456789abcdef0') == '해시 ••••'
    rt = runtime(tmp_path)
    md = rt / 'nhplug/shared-etf-paper/reviews/premarket-2026-11-17.md'
    md.write_text('# 장전 12345678901\n\n## 계좌 98765432109 점검\n\n본문')
    page, meta = rp.render('premarket', '2026-11-17', md)
    assert '12345678901' not in page and '98765432109' not in page and '••••' in meta['title'] and rp.check(page) == []


def test_push_off_passes_do_not_touch_git_and_failures_back_off(tmp_path, monkeypatch):
    rt, site, db, cfg = setup(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(rp, 'git', lambda args, cwd, cfg=None, timeout=120: calls.append(args[0]) or '')
    with rp.connect(db) as con:
        rp.scan(con, cfg, site, rt)
        assert rp.publish(con, cfg, site) == 'off'
        n = len(calls)
        assert rp.publish(con, cfg, site) == 'nothing' and len(calls) == n  # 'off' pages wait until pushing is switched on
        rp.event(con, 'journal:2026-11-17', 'publish', False, 'push rejected')
        rp.update(con, 'journal:2026-11-17', publish_status='failed')
        assert rp.publish(con, {**cfg, 'push': True}, site, fake_github([])) == 'backoff'
        assert rp.publish(con, {**cfg, 'push': True}, site, fake_github([], exists=True), force=True) == 'ok'


def test_requeue_retries_transient_failures_and_returned_webhooks():
    import sqlite3
    con = sqlite3.connect(':memory:')
    con.row_factory = sqlite3.Row
    con.executescript(rp.SCHEMA)
    old = (datetime.now(rp.KST) - timedelta(minutes=30)).isoformat(timespec='seconds')
    for rid, err, tries in (('a', 'Discord 연결 실패 또는 429 계속', 1), ('b', 'Discord 거절(4xx: 웹훅 주소 확인)', 1),
                            ('c', 'DISCORD_WEBHOOK_LONGTERM 미설정', 1), ('d', 'Discord 연결 실패 또는 429 계속', 5)):
        con.execute("INSERT INTO reports (id, channel, gen_status, send_status, send_error, send_attempts) VALUES (?,?,?,?,?,?)",
                    (rid, 'longterm' if rid == 'c' else 'realtime', 'ok', 'failed', err, tries))
        con.execute('INSERT INTO events VALUES (?,?,?,?,?)', (old, rid, 'send', 0, err))
    rp.requeue(con, {'realtime': 'http://rt', 'longterm': 'http://lt'})
    states = dict(con.execute('SELECT id, send_status FROM reports').fetchall())
    assert states == {'a': 'pending', 'b': 'failed', 'c': 'pending', 'd': 'failed'}


def test_longterm_page_shows_watch_next(tmp_path):
    folder = tmp_path / 'nhplug/vibe-longterm/reports'
    folder.mkdir(parents=True)
    (folder / 'strategy-2026-11-17.md').write_text('# 장기 전략\n\n## 1. 결론\n\n유지')
    (folder / 'strategy-2026-11-17.json').write_text(json.dumps({'summary': '요약', 'scenarios': [{'name': '하락', 'trigger': '−10%', 'action': '분할 매수'}],
                                                                'watch_next': ['FOMC']}, ensure_ascii=False))
    page, _ = rp.render('longterm-strategy', '2026-11-17', folder / 'strategy-2026-11-17.md')
    assert '시나리오별 대응' in page and '<li>FOMC</li>' in page and rp.check(page) == []


def test_preview_renders_without_records(tmp_path, monkeypatch):
    monkeypatch.setattr(rp, 'DB', tmp_path / 'never.sqlite3')
    md = tmp_path / 'strategy-2026-11-20.md'
    md.write_text('# 샘플 장기 전략\n\n## 1. 결론\n\n유지')
    out = rp.preview('longterm-strategy', md, tmp_path / 'preview')
    assert out == tmp_path / 'preview/reports/longterm-strategy/2026-11-20.html' and rp.check(out.read_text()) == []
    assert (tmp_path / 'preview/assets/report.css').exists() and not (tmp_path / 'never.sqlite3').exists()


def test_opening_callout_becomes_summary_without_its_marker():
    title, body, lede = rp.split_title('# 제목\n\n> [!SUMMARY] 오늘의 결론\n> 코어는 ==유지==한다.\n\n## 1. 절\n\n본문')
    assert lede.startswith('오늘의 결론') and '[!' not in lede and '==' not in lede and body.startswith('## 1.')


def test_a_journal_skeleton_is_not_linked_until_the_writer_finishes_it(tmp_path, monkeypatch):
    """Review 4 (2026-11-19): trading-journal-writer fills journal-D.md in place; a pass between its edits linked the
    skeleton ('작성 전 — 자동 요약입니다…') to Discord."""
    rt, site, db, cfg = setup(tmp_path, monkeypatch)
    reviews = rt / 'nhplug/shared-etf-paper/reviews'
    for name in ('premarket-2026-11-17.md', 'journal-2026-11.md', 'journal-2026-11-17.json'):
        (reviews / name).unlink()
    skeleton = '\n'.join([JT.title('day', '2026-11-17'), '', f'> {JT.SUMMARY_PENDING}', '']
                          + [line for h in JT.headings('day') for line in (f'## {h}', '', JT.EMPTY_WRITTEN, '')])
    (reviews / 'journal-2026-11-17.md').write_text(skeleton)
    bare = tmp_path / 'remote.git'
    subprocess.run(['git', 'init', '-q', '--bare', str(bare)], check=True)
    log = []
    kw = dict(cfg={**cfg, 'push': True, 'remote': str(bare)}, sender=lambda u, t: (log.append(t), (True, 0, False))[1],
              live=lambda u: True, db=db, site=site, runtime=rt, api=fake_github([], exists=True))
    rp.cycle({'realtime': 'http://rt'}, **kw)
    assert log == []
    written = skeleton.replace(JT.SUMMARY_PENDING, '모의는 +1만 원').replace(JT.EMPTY_WRITTEN, '본문 [출처](https://example.com/a)')
    assert JT.check(written, 'day', written=True) == []
    (reviews / 'journal-2026-11-17.md').write_text(written)
    rp.cycle({'realtime': 'http://rt'}, **kw)
    assert len(log) == 1 and 'journal/2026-11-17.html' in log[0]


def test_assets_are_copied_only_when_they_differ(tmp_path):
    rp.ensure_assets(tmp_path)
    css = tmp_path / 'assets/report.css'
    before = css.stat().st_mtime_ns
    rp.ensure_assets(tmp_path)  # every 30 s pass: nothing rewritten
    assert css.stat().st_mtime_ns == before
    css.write_text('stale')
    rp.ensure_assets(tmp_path)
    assert css.read_bytes() == (rp.TEMPLATES / 'report.css').read_bytes()
