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


LIVE = """# 매매일지 · 2026-11-17 (화)

> 요약 문단.

## 1. 숫자로 보는 하루

| 계좌 | 실현손익 | 매수 대비 |
|---|---|---|
| 모의 (Jev) | -54,328원 | -0.40% |
| 장기투자 (vibe 가상) | +17,860원 | +0.80% |
| 실계좌 1 | -1,413원 | -0.10% |
| 실계좌 2 (장기투자) | +0원 | 0% |

모의는 6,702주를 손절해 −90,802원을 잃었다.

실계좌 1 — 미러, −1,413원. 197주 매도(1,339원), 496주를 팔고 17회 매수(137.9만 원), 운용금 100만 원.

- 035760 · 실계좌 1 · 매수 1 · 매도 1 · 실현 -100원

모의는 −12,000원, 실계좌 1은 −1,413원을 따라 잃었다. 모의 재진입은 −36,474원이었다.

사실: 실계좌 1은 −1,413원이다. 496주 매도(1,329원)는 모의 손절을 따른 것이다.
"""


def test_public_pages_hide_live_account_money_and_quantities(tmp_path):
    rt = runtime(tmp_path)
    md = rt / 'nhplug/shared-etf-paper/reviews/journal-2026-11-17.md'
    md.write_text(LIVE)
    (md.with_suffix('.json')).write_text(json.dumps({'summary': 'Jev −54,328원, 실계좌 1은 −1,413원이다.',
                                                     'top_issues': [{'title': '실계좌 1 −3,623원', 'stars': 3, 'why': '실계좌 2주 매수'}]}, ensure_ascii=False))
    page, meta = rp.render('journal', '2026-11-17', md)
    body = page.split('<article')[1]
    for leaked in ('1,413원', '197주', '1,339원', '137.9만 원', '496주', '100만 원', '-100원', '3,623원', '실계좌 2주', '1,413원을', '1,329원'):
        assert leaked not in page and leaked not in meta['text'] and leaked not in meta['summary'], leaked
    assert '-54,328원' in body and '6,702주' in body and '−90,802원' in body  # paper numbers stay
    assert '-0.10%' in body and '운용금 •••원' in body and '•••주 매도' in body  # units stay for the particles
    assert '−54,328원' in meta['summary'] and '실계좌 1은 •••원이다' in meta['summary']  # only what follows 실계좌
    assert '−36,474원' in body and '•••원을 따라' in body  # percentages stay, amounts say 비공개
    assert rp.check(page) == []
    assert any('실계좌' in i for i in rp.check('<html lang="ko"><h1>x</h1><p>실계좌 1은 −1,413원이다.</p>'))


def test_live_account_columns_are_hidden_too():
    html_in = ('<table><thead><tr><th>기간</th><th>모의 (Jev)</th><th>실계좌 1</th></tr></thead>'
               '<tbody><tr><td>11-02 ~ 11-10</td><td>+9,996원</td><td>-1,413원</td></tr></tbody></table>')
    out = rp.hide_live(html_in)
    assert '+9,996원' in out and '-1,413원' not in out and '•••원' in out and '11-02 ~ 11-10' in out
    assert rp.hide_live(out) == out


SOURCED = """# 2026-11-20 매매일지

> 외국인 순매도의 절반이 삼성전자(−6,318억, 토스증권 잠정)였다. 코스피는 −4.4% 내렸다.

## 8. 시장·수급

### 투자자·프로그램 수급 (순매수, 억 원)

| 시장 | 외국인 | 기관 |
|---|---|---|
| 코스피 | -12,682 | -8,382 |

### 기관 세부·기타법인 (토스증권, 순매수 억 원)

| 시장 | 금융투자 | 연기금 |
|---|---|---|
| 코스피 | +9,061 | -1,234 |
| 코스닥 | -12,682 | A -2,222, B +3,333 |

_토스증권 외국인은 미등록 외국인을 포함해 위 네이버 합계와 다를 수 있습니다._

### 매매 종목 수급 (그날 순매수)

| 종목 | 외국인 |
|---|---|
| 하림 | +1,111주 |

### 매매 종목 공매도·신용·대차 (토스증권)

| 종목 | 공매도 |
|---|---|
| 삼성전자 | 77.7억 (12.34%) |

_신용은 다음 영업일 값입니다._

코스피 하락 739 종목이 손익을 갈랐다(−4.4%). 기관의 −2,222억 매도와 외국인 −12,682억 매도가 겹쳤다. [기사 2,222억](https://n.example/2,222)

## 9. 뉴스

외국인이 **−14,696억 원**을 팔았고(삼성전자 **−6,000억**, 토스증권 잠정) 08:30에 시작했다. 상승 161·하락 739였다.

매수 쪽은 기타법인 +4,274억·금융투자 +1,255억 원이었다. 개인은 +3,921억 원이다.

- 스튜디오드래곤 +13.48%, 클리오 +4.2%(KRX 대비 괴리율, 키움)입니다. CJ ENM은 −3.33%입니다.
- 야간선물 +3.12%, 08:30 프리마켓에서 SK하이닉스 +12.35%, 첫 체결 188,000 → 195,400였습니다. 반대 시나리오는 187,500원 아래입니다. 11/23 기관은 1,134억 원을 샀다.
- 미국 프리마켓은 +0.7%였다.
- 20261109 키움증권: 3Q 광고 반등 +20% 성공
- [연기금 1조 원 매수](https://news.example.com/a-12억)
- 키움 1분봉으로 진입가 대비 +1% 익절을 적용했다.
- 포토스튜디오 매출 +5% 늘었다.

```chart
{"type": "bar", "title": "토스증권 순매수", "unit": "억", "data": [{"label": "외국인", "value": 4321}]}
```
"""


def test_public_pages_leave_out_kiwoom_and_toss_figures(tmp_path):
    """Kiwoom's and Toss's terms allow the operator's own use only (docs/report-pages-data-policy-plan.md, user 2026-11-24)."""
    rt = runtime(tmp_path)
    md = rt / 'nhplug/shared-etf-paper/reviews/journal-2026-11-20.md'
    md.write_text(SOURCED)
    md.with_suffix('.json').write_text(json.dumps({
        'summary': '기관은 SK하이닉스(−2,086억, 토스증권)에 몰렸다. 손익은 −54,328원이다.',
        'top_issues': [{'title': '외국인 매도', 'stars': 3, 'why': '삼성전자(−6,318억, 토스증권 잠정)', 'url': 'https://x.example/a?토스=−5억'}]}, ensure_ascii=False))
    page, meta = rp.render('journal', '2026-11-20', md)
    for leaked in ('6,318', '9,061', '1,234', '77.7', '12.34', '14,696', '6,000억', '4,274', '1,255', '13.48', '4.2%', '12.35', '2,086', '188,000', '195,400', '187,500', '−2,222억', '4321', '4,321'):
        assert leaked not in page and leaked not in meta['text'] and leaked not in meta['summary'], leaked
    for kept in ('-12,682', '+1,111주', '상승 161·하락 739', '3,921억', '−3.33%', '+0.7%', '08:30', '야간선물 +3.12%', '1,134억 원', '갈랐다(−4.4%)', '외국인 −12,682억 매도', '기사 2,222억', '키움증권: 3Q 광고 반등 +20%',
                 '연기금 1조 원 매수', '+1% 익절', '+5% 늘었다', '하림', '매매 종목 공매도·신용·대차', rp.SOURCE_NOTE):
        assert kept in page, kept
    assert '−54,328원' in meta['summary'] and rp.public_text('https://x.example/a?토스=−5억') == 'https://x.example/a?토스=−5억'
    assert rp.check(page) == []
    assert md.read_text() == SOURCED  # the local report keeps everything


def test_a_page_with_a_kiwoom_or_toss_figure_is_not_published():
    ok = '<p>외국인 순매수 상위는 대시보드에서 봅니다.</p>'
    assert not any('키움' in i for i in rp.check(ok))
    for bad in ('<p>삼성전자 <b>−6,318억</b>(<i>토스증권</i> 잠정)</p>', '<h3>매매 종목 공매도 (토스증권)</h3><p>표</p>',
                '<li>하나투어 +4.18%(괴리율, 키움)</li>', '<table><tr><td>금융투자</td><td>+9,061</td></tr></table>'):
        assert any('키움·토스' in i for i in rp.check(bad)), bad
        assert rp.hide_sources(rp.hide_sources(bad)) == rp.hide_sources(bad)


def test_diagram_texts_lose_the_figures_too(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(rp, 'archify', lambda kind, source, out: seen.update(source=source))
    md = '```archify-workflow\n{"meta": {"title": "흐름"}, "nodes": [{"label": "금융투자 +9,061억 순매수", "x": -12}]}\n```\n'
    rp.blocks(md, '2026-11-20', tmp_path)
    assert '9,061' not in seen['source'] and json.loads(seen['source'])['nodes'][0]['x'] == -12
