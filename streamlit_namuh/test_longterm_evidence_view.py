import json

import longterm_evidence as E
import longterm_evidence_view as V
import vibe_longterm as L
from datetime import datetime

from test_vibe_longterm import DAY, KST, Read, fake_fetch, krx_calendar  # noqa: F401

WATCH = json.dumps({'market_view': '', 'stories': {k: {'status': 'watch' if k == 'QUANT' else 'intact', 'tilt': 0, 'reason': 'ok'}
                                                   for k in L.SATS}})


def seeded(tmp_path, monkeypatch):
    """A dry plan with 피터 린치 halving the quantum ETF (its story on watch)."""
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    now = datetime.combine(DAY, datetime.min.time(), KST).replace(hour=8, minute=45)
    L.decide(tmp_path, now, fetch=fake_fetch, ask=lambda text: WATCH)
    L.trade(tmp_path, Read(), now.replace(hour=9, minute=40))
    return E.rows(tmp_path)


def test_rows_filter_by_symbol_reason_and_master(tmp_path, monkeypatch):
    rows = seeded(tmp_path, monkeypatch)
    table = V.table_rows(rows)
    assert len(table) == len(rows) and {'날짜', '종목', '구분', '사유', '거장', '요약'} <= set(table[0])
    quant = [r for r in rows if r['key'] == 'QUANT']
    assert V.filtered(rows, keys=['QUANT']) == quant
    assert V.filtered(rows, reasons=['초기 투입']) == rows
    moved = [r for r in rows if 'lynch_story' in json.loads(r['masters'])]
    assert moved and V.filtered(rows, masters=['lynch_story']) == moved


def test_the_why_card_puts_the_summary_first_and_marks_used_and_reference_parts(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    row = next(r for r in seeded(tmp_path, monkeypatch) if r['key'] == 'QUANT')

    def view(row):
        import longterm_evidence_view
        longterm_evidence_view.why(row)
    at = AppTest.from_function(view, args=(row,)).run()
    assert not at.exception
    assert row['summary'] in at.info[0].value
    text = ' '.join(m.value for m in at.markdown)
    assert '주문 계산에 쓰임' in text and '참고만 함' in text
    assert all(t in text for t in E.PART_TITLES.values())


def test_the_list_renders_with_its_filters_and_a_picker(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    seeded(tmp_path, monkeypatch)

    def view(root):
        import longterm_evidence_view
        longterm_evidence_view.render(root, key='t')
    at = AppTest.from_function(view, args=(tmp_path,)).run()
    assert not at.exception
    assert at.multiselect(key='t_keys') and at.selectbox(key='t_pick').options
    at.button(key='t_open').click().run()
    assert not at.exception
