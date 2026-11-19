import json
from datetime import datetime, timedelta

import broker_token as bt


def later(minutes):
    return datetime.now(bt.KST) + timedelta(minutes=minutes)


def test_valid_shared_token_is_reused_without_issuing(tmp_path):
    path = tmp_path / 'token.json'
    path.write_text(json.dumps({'token': 'shared', 'expires_at': later(60).isoformat()}))
    assert bt.token(path, issue=lambda: (_ for _ in ()).throw(AssertionError('issued'))) == 'shared'


def test_expiring_token_is_reissued_and_stored_private(tmp_path):
    path = tmp_path / 'token.json'
    path.write_text(json.dumps({'token': 'old', 'expires_at': later(5).isoformat()}))  # inside the refresh margin
    assert bt.token(path, issue=lambda: ('new', later(600))) == 'new'
    assert json.loads(path.read_text())['token'] == 'new' and path.stat().st_mode & 0o777 == 0o600


def test_rejected_token_uses_another_process_replacement_or_issues_once(tmp_path):
    path = tmp_path / 'token.json'
    path.write_text(json.dumps({'token': 'replaced-by-other', 'expires_at': later(600).isoformat()}))
    assert bt.token(path, issue=lambda: ('mine', later(600)), stale='rejected') == 'replaced-by-other'
    issued = []
    assert bt.token(path, issue=lambda: issued.append(1) or ('mine', later(600)), stale='replaced-by-other') == 'mine'
    assert issued == [1]
