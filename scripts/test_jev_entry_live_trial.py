from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


DAY = '2026-11-25'
CYCLE = 'a' * 32
DIGEST = 'b' * 64
SYMBOL = '005930'


def at(time):
    return f'{DAY}T{time}+09:00'


@pytest.fixture
def trial(tmp_path):
    reports = tmp_path / 'reports'
    reports.mkdir()
    (reports / f'{DAY}.json').write_text(json.dumps({
        'day': DAY,
        'cycles': [{
            'cycle_id': CYCLE, 'input_digest': DIGEST, 'input_linked': True,
            'model_second_symbol': SYMBOL, 'response_time': at('10:00:00'), 'execution_status': 'filled',
            'submissions': [{'symbol': '000660'}],
            'intents': [{'symbol': '000660', 'side': 'BUY', 'status': 'terminal'}],
            'fills': [{'symbol': '000660', 'side': 'BUY', 'time': at('10:00:20')}],
        }],
        'candidates': [{'cycle_id': CYCLE, 'input_digest': DIGEST, 'symbol': SYMBOL, 'model_rank': 2}],
    }), encoding='utf-8')
    frame = tmp_path / 'frame.json'
    frame.write_text(json.dumps({
        'scope': 'isolated_paper', 'day': DAY, 'cycle_id': CYCLE, 'symbol': SYMBOL,
        'input_digest': DIGEST, 'response_time': at('10:00:00'),
        'state': {'symbol': SYMBOL, 'completed_bars': [{'time': at('09:59:00'), 'close': 10000}]},
        'quote': {'at': at('10:00:24'), 'bid': 9990, 'ask': 10000},
        'check': {'at': at('10:00:25'), 'qty': 2, 'available_cash': 25000, 'held_limit': 30000,
                  'held_exposure': 10000, 'reserved_buy': 0, 'rules_permit': True, 'pending': False,
                  'first_reconciled_at': at('10:00:22')},
    }), encoding='utf-8')
    return frame, reports


@pytest.mark.parametrize(('key', 'production_key', 'quota'), [
    ('', 'production-test-key', True),
    ('production-test-key', 'production-test-key', True),
    ('research-test-key', 'production-test-key', False),
    ('research-test-key', '', True),
])
def test_unisolated_credentials_never_send_request(trial, key, production_key, quota):
    from jev_entry_live_trial import collect

    def post(*args, **kwargs):
        pytest.fail('unisolated request was sent')

    with pytest.raises(ValueError):
        collect(*trial, key, production_key, quota, post=post,
                clock=lambda: datetime.fromisoformat(at('10:00:25')))


def test_rechecks_quotes_and_capacity_after_one_request(trial):
    from jev_entry_live_trial import collect

    frame, _ = trial
    calls = []
    now = datetime.fromisoformat(at('10:00:25'))

    def post(payload, key, timeout):
        nonlocal now
        calls.append((payload, key, timeout))
        updated = json.loads(frame.read_text())
        updated['quote']['at'] = at('10:00:29')
        updated['check']['at'] = at('10:00:30')
        frame.write_text(json.dumps(updated), encoding='utf-8')
        now = datetime.fromisoformat(at('10:00:30'))
        return {'model': 'jev-1.14.0', 'answers': {'candidate': {
            'type': 'choice', 'choice': 'BUY', 'confidence': 0.9,
            'probabilities': {'BUY': 0.95, 'WAIT': 0.05},
        }}}

    result = collect(*trial, 'research-test-key', 'production-test-key', True, post=post, clock=lambda: now)
    assert len(calls) == 1
    assert calls[0][1] == 'research-test-key'
    [evidence] = result['evidence']
    assert evidence['quote_at'] == at('10:00:29')
    assert evidence['remaining_room'] == 20000
    assert result['comparison']['counts']['modeled'] == 1


def test_stale_initial_frame_never_sends_request(trial):
    from jev_entry_live_trial import collect

    def post(*args, **kwargs):
        pytest.fail('stale frame sent a request')

    with pytest.raises(ValueError):
        collect(*trial, 'research-test-key', 'production-test-key', True, post=post,
                clock=lambda: datetime.fromisoformat(at('10:01:01')))


def test_cli_explains_missing_isolated_credentials_before_reading_frame(tmp_path):
    env = os.environ.copy()
    env.pop('JEV_ISOLATED_API_KEY', None)
    env_file = tmp_path / '.env'
    env_file.write_text('JEV_API_KEY=production-test-key\n', encoding='utf-8')
    env['VIBE_TRADING_ENV_FILE'] = str(env_file)
    script = Path(__file__).with_name('jev_entry_live_trial.py')
    result = subprocess.run([sys.executable, str(script), '--frame', str(tmp_path / 'missing.json'),
                             '--reports', str(tmp_path), '--independent-quota'],
                            env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert not result.stdout
    assert 'JEV_ISOLATED_API_KEY' in result.stderr


@pytest.mark.parametrize('override', [None, 'process-isolated-key'])
def test_cli_loads_both_keys_from_dotenv_with_optional_process_override(tmp_path, monkeypatch, capsys, override):
    import jev_entry_live_trial as cli

    env_file = tmp_path / '.env'
    env_file.write_text('JEV_API_KEY=production-test-key\nJEV_ISOLATED_API_KEY=dotenv-isolated-key\n', encoding='utf-8')
    monkeypatch.setattr(cli, 'ENV', env_file)
    monkeypatch.delenv('JEV_ISOLATED_API_KEY', raising=False)
    if override:
        monkeypatch.setenv('JEV_ISOLATED_API_KEY', override)
    monkeypatch.setattr(sys, 'argv', ['trial', '--frame', 'frame.json', '--reports', 'reports', '--independent-quota'])

    def collect(frame, reports, isolated_key, production_key, independent_quota):
        assert isolated_key == (override or 'dotenv-isolated-key')
        assert production_key == 'production-test-key'
        assert independent_quota is True
        return {'evidence': []}

    monkeypatch.setattr(cli, 'collect', collect)
    cli.main()
    assert json.loads(capsys.readouterr().out) == {'evidence': []}


def test_waits_for_isolated_producer_without_resending_model_request(trial):
    from jev_entry_live_trial import collect

    frame, _ = trial
    now = datetime.fromisoformat(at('10:00:25'))
    calls = 0

    def post(payload, key, timeout):
        nonlocal now, calls
        calls += 1
        now = datetime.fromisoformat(at('10:00:30'))
        return {'model': 'jev-1.14.0', 'answers': {'candidate': {
            'type': 'choice', 'choice': 'BUY', 'confidence': 0.9,
            'probabilities': {'BUY': 0.95, 'WAIT': 0.05},
        }}}

    def pause(seconds):
        nonlocal now
        updated = json.loads(frame.read_text())
        updated['quote']['at'] = at('10:00:30')
        updated['check']['at'] = at('10:00:31')
        frame.write_text(json.dumps(updated), encoding='utf-8')
        now = datetime.fromisoformat(at('10:00:31'))

    result = collect(*trial, 'research-test-key', 'production-test-key', True,
                     post=post, clock=lambda: now, pause=pause)
    assert calls == 1
    assert result['evidence'][0]['checked_at'] == at('10:00:31')
    assert result['comparison']['counts']['modeled'] == 1
