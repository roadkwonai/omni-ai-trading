from streamlit.testing.v1 import AppTest


def _app():
    import os
    from pathlib import Path
    import accounts
    import engine_view
    nhplug = Path(os.environ['ENGINE_VIEW_TEST'])
    armed = {'live-1': {'label': '실계좌 1', 'kind': 'live', 'auto': True, 'live_orders': True}} if os.environ.get('ENGINE_VIEW_ARMED') else {}
    engine_view.render(nhplug, {**accounts.load(nhplug / 'accounts.json'), **armed})


def test_switch_engines_and_confirm_live_orders_before_jev_restarts(tmp_path, monkeypatch):
    import accounts
    import engine_ops
    monkeypatch.setenv('ENGINE_VIEW_TEST', str(tmp_path))
    records = accounts.load(tmp_path / 'accounts.json')
    accounts.save(records, tmp_path / 'accounts.json')
    at = AppTest.from_function(_app, default_timeout=60).run()
    assert not at.exception and [m.value for m in at.markdown if '운영 엔진' in m.value]
    at.segmented_control(key='engine_mode_both').set_value('vibe').run()
    at.button(key='engine_apply').click().run()
    assert engine_ops.mode(tmp_path) == 'vibe'
    monkeypatch.setenv('ENGINE_VIEW_ARMED', '1')  # a live account sending real orders
    at = AppTest.from_function(_app, default_timeout=60).run()
    at.segmented_control(key='engine_mode_vibe').set_value('both').run()
    assert at.warning and at.button(key='engine_apply').disabled
    at.checkbox(key='engine_live_ack').check().run()
    at.button(key='engine_apply').click().run()
    assert engine_ops.mode(tmp_path) == 'both'
