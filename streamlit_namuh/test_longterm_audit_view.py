
import longterm_audit as A


def view(root):
    import longterm_audit_view
    longterm_audit_view.render(root)


def test_without_reports_it_explains_the_schedule_and_still_shows_todays_check(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    at = AppTest.from_function(view, args=(tmp_path,)).run()
    assert not at.exception
    text = ' '.join(i.value for i in at.info)
    assert '평일 16:45' in text and '금 17:40' in text and '매월 1일 17:45' in text
    assert any('자동 점검' in m.value for m in at.markdown)


def test_a_written_audit_is_shown_with_its_check_and_download(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    key = '2026-11-20'
    A.path(tmp_path, key).parent.mkdir(parents=True)
    A.path(tmp_path, key).write_text(A.blank(key).replace(A.PLACEHOLDER, '기록 보완 제안'), encoding='utf-8')
    A.path(tmp_path, '2026-11').write_text('# 틀린 제목\n', encoding='utf-8')
    A.path(tmp_path, '2026-11-21.draft').write_text('초안', encoding='utf-8')  # a draft is not listed
    at = AppTest.from_function(view, args=(tmp_path,)).run()
    assert not at.exception
    assert at.selectbox(key='lt_audit_key').options[0].startswith('2026-11-20')  # newest first
    assert any('기록 보완 제안' in m.value for m in at.markdown) and not at.warning
    at.selectbox(key='lt_audit_key').set_value('2026-11').run()
    assert at.warning and '템플릿 검사' in at.warning[0].value
