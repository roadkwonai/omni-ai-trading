import json
import pytest

import schedule_antigravity as agy


def test_credential_permissions_and_identity(tmp_path):
    key = tmp_path / 'credential'
    key.write_text('example-test-key', encoding='utf-8')
    key.chmod(0o600)
    profile = {'credential_file': str(key), 'model': 'gemini-test'}
    assert len(agy.account(profile)) == 64
    assert agy.env(profile)['GEMINI_API_KEY'] == 'example-test-key'
    key.chmod(0o644)
    with pytest.raises(ValueError):
        agy.account(profile)
    key.chmod(0o600)
    link = tmp_path / 'link'
    link.symlink_to(key)
    with pytest.raises(ValueError):
        agy.account({'credential_file': str(link)})


def test_fixture_transport_denies_tool_usage_and_reports_model():
    args = agy.command('/bin/agy', 'gemini-test', '/tmp')
    settings = json.loads(args[args.index('--settings') + 1])
    assert settings['modelProvider'] == 'gemini'
    assert {'command(*)', 'write_file(*)', 'read_file(*)', 'mcp(*)'} <= set(settings['permissions']['deny'])
    assert json.loads(agy.input_text('제목'))['message']['content'] == '제목'
    data = '\n'.join(map(json.dumps, [
        {'event': 'init', 'init': {'model': 'gemini-test'}},
        {'event': 'result', 'result': {'status': 'SUCCESS', 'response': '확인'}},
    ]))
    assert agy.response(data) == ('확인', 'gemini-test')
    assert agy.response(data, 'permission denied') == (None, None)
    assert agy.response(data + '\n' + json.dumps({'event': 'step_update', 'step_update': {'step_type': 'tool'}})) == (None, None)


def test_cli_receives_secret_only_via_environment_and_prompt_via_stdin(tmp_path):
    key = tmp_path / 'key'
    key.write_text('example-test-key')
    key.chmod(0o600)
    cli = tmp_path / 'agy'
    cli.write_text('''#!/usr/bin/env python3
import json, os, sys
assert os.environ['GEMINI_API_KEY'] == 'example-test-key'
assert 'secret-task-prompt' not in repr(sys.argv)
request = json.loads(sys.stdin.readline())
assert request['message']['content'] == 'secret-task-prompt'
print(json.dumps({'event':'init','init':{'model':'gemini-test'}}))
print(json.dumps({'event':'result','result':{'status':'SUCCESS','response':'ok'}}))
''')
    cli.chmod(0o700)
    assert agy.test_model({'credential_file': str(key), 'model': 'gemini-test'}, cli,
                          'secret-task-prompt', tmp_path) == ('ok', 'gemini-test')
