import json

import report_figures as F
import report_pages as rp


def test_callouts_turn_github_alerts_into_labelled_boxes():
    html = rp.markdown('> [!WARNING] 확인할 것\n> VIX가 **25**를 넘으면 미룬다.\n\n> [!참고]\n> 그냥 참고\n\n> 평범한 인용')
    out = F.callouts(html)
    assert '<aside class="callout warning" role="note" aria-label="주의">' in out and '확인할 것</p>' in out
    assert 'VIX가 <strong>25</strong>를' in out  # inline markup and its spacing survive
    assert 'class="callout note"' in out and out.count('<blockquote>') == 1  # a plain quote stays a quote


def test_marks_skip_code():
    assert F.marks('<p>==핵심== <code>==x==</code></p>') == '<p><mark>핵심</mark> <code>==x==</code></p>'


def test_table_with_signed_change_column_gets_bars_once():
    body, _ = rp.decorate(rp.markdown('| 지표 | 등락 |\n|---|---|\n| 나스닥 | -2.67% |\n| VIX | +18.70% |\n| 금 | −0.73% |\n'))
    out = F.table_charts(body)
    assert out.count('class="bars"') == 1 and 'bar up' in out and 'bar down' in out and '+18.7%' in out
    short, _ = rp.decorate(rp.markdown('| 지표 | 등락 |\n|---|---|\n| 나스닥 | -2.67% |\n| VIX | +18.70% |\n'))
    assert 'class="bars"' not in F.table_charts(short)  # fewer than three rows: no chart


def test_chart_blocks_and_bad_specs():
    line = F.chart_block(json.dumps({'type': 'line', 'title': '순자산', 'series': [{'name': 'a', 'points': [['1', 1], ['2', 2], ['3', 1.5]]}]}))
    assert '<polyline' in line and '순자산' in line
    bar = F.chart_block(json.dumps({'type': 'bar', 'data': [{'label': 'x', 'value': -1}, {'label': 'y', 'value': 2}]}))
    assert 'bar-row' in bar
    assert '차트를 그리지 못했습니다' in F.chart_block('{bad')


def test_page_has_theme_button_and_figures_and_passes_check(tmp_path, monkeypatch):
    md = tmp_path / 'premarket-2026-11-18.md'
    md.write_text('# 장전\n\n## 1. 시장\n\n> [!TIP]\n> ==핵심==\n\n```chart\n{"type":"bar","title":"t","data":[{"label":"a","value":1},{"label":"b","value":-2}]}\n```\n\n'
                  '```archify-workflow\n{"meta":{"title":"흐름"}}\n```\n')
    monkeypatch.setattr(rp, 'ARCHIFY', tmp_path / 'missing.mjs')  # no archify: a warning box, the page still builds
    out = rp.preview('premarket', md, tmp_path / 'site')
    page = out.read_text()
    assert rp.check(page) == [] and 'class="theme-toggle"' in page and 'assets/theme.js' in page
    assert 'callout tip' in page and '<mark>핵심</mark>' in page and 'bar-row' in page and '도식을 그리지 못했습니다' in page
    assert (tmp_path / 'site/assets/theme.js').exists()
