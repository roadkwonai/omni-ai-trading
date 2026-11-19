from datetime import date

from streamlit.testing.v1 import AppTest

import report_pages as rp


def _app():
    from pathlib import Path
    import report_pages_view
    report_pages_view.render(Path('/nonexistent-runtime'))


def seed(tmp_path, monkeypatch):
    import report_pages as rp
    monkeypatch.setattr(rp, 'DB', tmp_path / 'rp.sqlite3')
    monkeypatch.setattr(rp, 'SITE', tmp_path / 'site')
    monkeypatch.setattr(rp, 'settings', lambda env=None: {'repo': '', 'url': '', 'branch': 'gh-pages', 'push': False, 'token': 'x',
                                                          'missing': ['REPORT_PAGES_REPO'], 'ready': False})
    with rp.connect() as con:
        for rid, kind, key, gen, pub, send, err in (
                ('journal:2026-11-17', 'journal', '2026-11-17', 'ok', 'not_configured', 'pending', None),
                ('premarket:2026-11-16', 'premarket', '2026-11-16', 'ok', 'ok', 'failed', 'Discord 거절(4xx: 웹훅 주소 확인)'),
                ('journal-period:2026-10', 'journal-period', '2026-10', 'failed', None, 'pending', None)):
            con.execute('INSERT INTO reports (id, kind, key, title, summary, channel, gen_status, publish_status, send_status, send_error, text, html_path) '
                        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', (rid, kind, key, f'{kind} {key}', '요약', 'realtime', gen, pub, send, err, '인버스 본문', ''))
            rp.event(con, rid, 'send', send != 'failed', err or '')


def test_report_tab_renders_list_history_and_plan(tmp_path, monkeypatch):
    seed(tmp_path, monkeypatch)
    at = AppTest.from_function(_app, default_timeout=60).run()
    assert not at.exception
    assert [t.label for t in at.tabs] == ['리포트 목록', '발송·작업 이력', '발행 대상·일정']
    listing = at.dataframe[0].value
    assert len(listing) == 3 and set(listing['Discord 발송']) == {'대기', '실패'}
    assert 'Discord 거절' in ' '.join(listing['사유'])
    assert any('REPORT_PAGES_REPO' in i.value for i in at.info)
    plan = at.dataframe[2].value
    assert len(plan) == len(rp.REPORTS) and {'매매일지', '장기투자 복기·감사'} <= set(plan['리포트'])


def test_filters_by_text_kind_and_date():
    import report_pages_view as v
    rows = [{'id': 'journal:2026-11-17', 'kind': 'journal', 'key': '2026-11-17', 'title': '매매일지', 'summary': '인버스 손절', 'text': '',
             'gen_status': 'ok', 'publish_status': 'ok', 'send_status': 'sent', 'channel': 'realtime', 'url': 'https://x', 'gen_error': None,
             'publish_error': None, 'send_error': None},
            {'id': 'journal-period:2026-10', 'kind': 'journal-period', 'key': '2026-10', 'title': '10월', 'summary': '', 'text': '반도체',
             'gen_status': 'ok', 'publish_status': 'ok', 'send_status': 'sent', 'channel': 'realtime', 'url': None, 'gen_error': None,
             'publish_error': None, 'send_error': None}]
    frame = v.table(rows)
    assert list(v.filtered(frame, '인버스', [], '전체', '전체', None)['id']) == ['journal:2026-11-17']
    assert list(v.filtered(frame, '', ['기간 매매일지'], '전체', '전체', None)['id']) == ['journal-period:2026-10']
    assert list(v.filtered(frame, '', [], '2026', '10', None)['id']) == ['journal-period:2026-10']
    assert list(v.filtered(frame, '', [], '전체', '전체', date(2026, 11, 17))['id']) == ['journal:2026-11-17']
