import json

import pytest

import accounts
import engine_ops
import vibe_longterm as L


@pytest.fixture
def nhplug(tmp_path):
    accounts.save(accounts.load(tmp_path / 'accounts.json'), tmp_path / 'accounts.json')  # the defaults: Jev on
    return tmp_path


def test_each_mode_sets_both_engines_and_reads_back(nhplug):
    assert engine_ops.status(nhplug) == {'jev': True, 'vibe': True} and engine_ops.mode(nhplug) == 'both'
    for mode, expected in (('jev', {'jev': True, 'vibe': False}), ('vibe', {'jev': False, 'vibe': True}),
                           ('off', {'jev': False, 'vibe': False}), ('both', {'jev': True, 'vibe': True})):
        engine_ops.set_mode(nhplug, mode)
        assert engine_ops.status(nhplug) == expected and engine_ops.mode(nhplug) == mode
    with pytest.raises(ValueError):
        engine_ops.set_mode(nhplug, 'all')


def test_jev_switch_is_the_shared_paper_auto_switch_and_keeps_other_settings(nhplug):
    before = accounts.load(nhplug / 'accounts.json')['paper-shared']
    engine_ops.set_mode(nhplug, 'vibe')
    after = accounts.load(nhplug / 'accounts.json')['paper-shared']
    assert after['auto'] is False and {k: v for k, v in after.items() if k != 'auto'} == {k: v for k, v in before.items() if k != 'auto'}
    assert L.load_settings(nhplug / 'vibe-longterm')['enabled'] is True


def test_vibe_off_ends_the_daily_run_before_any_step(tmp_path, monkeypatch, capsys):
    L.save_settings(tmp_path, {'enabled': False})
    monkeypatch.setattr(L, 'trading_day', lambda day: True)
    monkeypatch.setattr(L, 'decide', lambda *a, **k: pytest.fail('decided while off'))
    L.run(tmp_path, None, False)
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])['status'] == 'disabled'


def test_mode_switch_leaves_other_accounts_as_stored(tmp_path):
    """An invalid live record (price cap above the 2,000,000 approval) stays as stored, so runners keep refusing it."""
    good = accounts.load(tmp_path / 'none.json')['paper-shared']
    live = {'label': '실계좌 2', 'kind': 'live', 'engine': 'longterm', 'auto': True, 'live_orders': False, 'hash': 'a' * 64,
            'budget': {'capital_krw': 500_000, 'order_price_max_krw': 2_500_000, 'daily_loss_krw': 20_000}}
    (tmp_path / 'accounts.json').write_text(json.dumps({'version': 1, 'accounts': {'paper-shared': good, 'live-2': live}}))
    assert accounts.load(tmp_path / 'accounts.json')['live-2'].get('invalid') is True
    engine_ops.set_mode(tmp_path, 'vibe')
    assert accounts.stored(tmp_path / 'accounts.json')['live-2'] == live
    assert accounts.load(tmp_path / 'accounts.json')['paper-shared']['auto'] is False
