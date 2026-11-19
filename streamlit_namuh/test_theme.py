import json
import tempfile
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import prefs
import settings_view
import status_cards
import theme


def test_button_cycles_system_dark_light():
    assert [theme.next_mode(m) for m in theme.MODES] == ['dark', 'light', 'system']
    assert theme.next_mode('없음') == 'system'
    for mode in theme.MODES:
        label, icon = theme.LABELS[mode]
        assert label and icon
    assert [theme.LABELS[m][0] for m in theme.MODES] == ['시스템 테마', '다크 테마', '라이트 테마']


def test_theme_preference_is_validated_and_persisted():
    with tempfile.TemporaryDirectory() as tmp, patch('config_store.record'):
        path = Path(tmp) / 'p.json'
        assert prefs.load(path)['theme'] == 'system'
        assert prefs.save(path, {**prefs.load(path), 'theme': 'light'})['theme'] == 'light'
        assert prefs.load(path)['theme'] == 'light'
        for invalid in ('Dark', '', None, 1, True):
            assert prefs.load_from({'theme': invalid})['theme'] == 'system'


def test_css_switches_light_tokens_by_mode():
    assert theme.css('dark') == ''
    assert theme.css('light').startswith(':root{') and '--om-ink:' in theme.css('light')
    assert theme.css('system').startswith('@media (prefers-color-scheme: light){')
    assert theme.css('light') in theme.css('system')


def test_script_writes_streamlit_and_archify_choice():
    dark, system = theme.script('dark'), theme.script('system')
    assert json.dumps({'st': 'Dark', 'archify': 'dark'}) in dark
    assert json.dumps({'st': 'System', 'archify': None}) in system
    assert 'stActiveTheme-' in dark and 'archify-theme' in dark
    assert 'removeItem' in system  # system: Archify follows prefers-color-scheme again


def test_ink_follows_the_active_theme():
    active = lambda kind: patch.object(theme, 'st', SimpleNamespace(context=SimpleNamespace(theme={'type': kind})))
    with active('light'):
        assert theme.ink() == '#334155'
    with active('dark'):
        assert theme.ink() == '#e2e8f0'
        assert theme.ink('#cbd5e1') == '#cbd5e1'


def test_config_defines_both_themes_without_forcing_one():
    config = tomllib.loads((Path(__file__).parents[1] / '.streamlit' / 'config.toml').read_text())
    assert 'base' not in config['theme']  # a fixed base would hide the light/dark/system choice
    assert config['theme']['light']['backgroundColor'] and config['theme']['dark']['backgroundColor'] == '#020617'


def test_cards_use_theme_tokens():
    for css in (status_cards.CSS, settings_view.CSS):
        assert 'var(--om-ink' in css and 'var(--om-card-end' in css
        assert 'rgba(14,18,35' not in css.replace('var(--om-card-end,rgba(14,18,35', '')


def _theme_page():
    import app
    app.prefs_view()
    app.apply_theme()
    app.prefs_form()


def test_theme_button_saves_next_mode_and_display_save_keeps_it():
    import app
    with tempfile.TemporaryDirectory() as d, patch.object(app, 'prefs_path', return_value=Path(d) / 'prefs.json'), \
         patch('config_store.record'):
        ui = AppTest.from_function(_theme_page, default_timeout=30).run()
        assert not ui.exception
        button = ui.button[0]  # the first control of the 화면 설정 pop-over, not a header button
        assert (button.key, button.label) == ('theme_toggle', '시스템 테마')
        button.click().run()
        assert app.PREFS.load(Path(d) / 'prefs.json')['theme'] == 'dark'
        assert next(b for b in ui.button if b.key == 'theme_toggle').label == '다크 테마'
        assert any('"st": "Dark"' in h.value for h in ui.html)
        next(b for b in ui.button if b.key == 'theme_toggle').click().run()
        assert app.PREFS.load(Path(d) / 'prefs.json')['theme'] == 'light'
        ui.button(key='FormSubmitter:prefs_form-저장').click().run()  # 화면 설정 저장 must not reset the theme
        assert app.PREFS.load(Path(d) / 'prefs.json')['theme'] == 'light'


def test_tradingview_chart_follows_the_active_theme():
    import app
    active = lambda kind: patch.object(theme, 'st', SimpleNamespace(context=SimpleNamespace(theme={'type': kind})))
    bars = [{'time': 1, 'open': 1, 'high': 1, 'low': 1, 'close': 1, 'volume': 1}]
    for kind, background, gone in (('light', '#ffffff', '#101827'), ('dark', '#101827', '#ffffff')):
        with active(kind), patch.object(app.st, 'iframe') as frame:
            app.tradingview_chart(bars, [])
        html = frame.call_args.args[0]
        assert f"background:{{color:'{background}'}}" in html and gone not in html, kind


def test_graphviz_diagrams_get_light_nodes_in_the_light_theme():
    import guides
    active = lambda kind: patch.object(theme, 'st', SimpleNamespace(context=SimpleNamespace(theme={'type': kind})))
    src = 'digraph{' + guides.STYLE + ' a [label="x"]; subgraph cluster_1 { fontcolor="#E2E8F0"; } }'
    with active('light'):
        light = theme.dot(src)
    assert '#0E1223' not in light and 'fillcolor="#FFFFFF"' in light and '#E2E8F0' not in light.upper()
    with active('dark'):
        assert theme.dot(src) == src


def test_every_graphviz_chart_goes_through_the_theme():
    import pathlib, re
    folder = pathlib.Path(theme.__file__).parent
    direct = [f.name for f in folder.glob('*.py') if not f.name.startswith('test_') and f.name != 'theme.py'
              and re.search(r'st\.graphviz_chart\(', f.read_text())]
    assert direct == []
