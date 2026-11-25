import json
import sqlite3
from datetime import datetime

import schedules
from paper_ai_modes import DECISIONS_MODEL, JEV_MODEL
from schedule_models import STORY_PROMPT_PREFIX, last_model_use


def test_launchd_models_show_effective_mode_and_conditional_use(tmp_path):
    home = tmp_path / 'home'
    shared = home / '.vibe-trading/nhplug/shared-etf-paper'
    shared.mkdir(parents=True)
    (shared / 'ai-modes.json').write_text(json.dumps({'version': 1, 'jev': True, 'decision': False}))
    (home / '.vibe-trading/.env').write_text(
        'LANGCHAIN_PROVIDER=openai-codex\nLANGCHAIN_MODEL_NAME=openai-codex/gpt-test\nSECRET_VALUE=private\n')
    jobs = {j['key'].split('.')[-1]: j for j in schedules.launchd_jobs(home=home)}
    assert jobs['shared-etf-paper']['model'] == JEV_MODEL
    assert f'{DECISIONS_MODEL} 꺼짐' in jobs['shared-etf-paper']['model_detail']
    assert jobs['market-movers']['model'] == JEV_MODEL
    assert jobs['live-follow-1']['model'] == f'{JEV_MODEL} (조건부)'
    assert jobs['vibe-longterm']['provider'] == 'OpenAI Codex'
    assert jobs['vibe-longterm']['model'] == 'openai-codex/gpt-test'
    assert jobs['longterm-live-2']['provider'] == '판단 참조'
    assert jobs['state-sync']['model'] == '직접 호출 없음'
    assert jobs['ai-schedule-runner']['model'] == '작업별 설정'
    assert all('private' not in str(job) for job in jobs.values())


def test_model_usage_is_attributed_to_the_service_that_used_it(tmp_path, monkeypatch):
    home = tmp_path
    root = home / '.vibe-trading/nhplug'
    paper = root / 'shared-etf-paper'
    paper.mkdir(parents=True)
    paper_time = '2026-11-25T09:30:00+09:00'
    watch_time = '2026-11-25T09:50:00+09:00'
    with sqlite3.connect(paper / 'trading-ledger.sqlite3') as db:
        db.execute('CREATE TABLE events_v2 (time TEXT, event TEXT, data TEXT)')
        db.execute('INSERT INTO events_v2 VALUES (?,?,?)', (paper_time, 'jev_shadow', json.dumps({'jev': {'model': JEV_MODEL}})))
    with sqlite3.connect(paper / 'market-research.sqlite3') as db:
        db.execute('CREATE TABLE model_inputs (time TEXT, engine TEXT, question TEXT)')
        db.execute('INSERT INTO model_inputs VALUES (?,?,?)', (watch_time, 'jev', 'watchlist'))
        db.execute('INSERT INTO model_inputs VALUES (?,?,?)', ('2026-11-25T10:00:00+09:00', 'jev', 'holding'))

    advisor = home / '.vibe-trading-advisor/runs'
    for name, prompt in [('story', STORY_PROMPT_PREFIX + ' test'), ('other', 'Other AI run')]:
        run = advisor / name
        run.mkdir(parents=True)
        (run / 'req.json').write_text(json.dumps({'prompt': prompt}))
        (run / 'llm_usage.json').write_text(json.dumps(
            {'model': name, 'updated_at': '2026-11-25T00:01:00Z', 'totals': {'calls': 1}}))

    assert last_model_use('market-movers', root, home)[:2] == (None, watch_time)
    assert last_model_use('shared-etf-paper', root, home)[:2] == (JEV_MODEL, paper_time)
    assert last_model_use('live-follow-1', root, home) is None
    assert last_model_use('vibe-longterm', root, home)[0] == 'story'

    import schedule_dispatch
    import schedule_history

    observed = datetime.fromisoformat('2026-11-25T15:00:00+09:00')
    monkeypatch.setattr(schedules, 'claude_jobs', lambda *_: [])
    monkeypatch.setattr(schedules, 'codex_jobs', lambda *_: [])
    monkeypatch.setattr(schedule_dispatch, 'route_rows', lambda *a, **k: ({}, {}))
    monkeypatch.setattr(schedule_history, 'native_history', lambda *a, **k: {
        ('launchd 서비스', 'com.vibetrading.shared-etf-paper'): [{'observed': observed, 'status': '로그 관측'}]})
    jobs = {j['key']: j for j in schedules.all_jobs(home=home)}
    shared = jobs['com.vibetrading.shared-etf-paper']
    assert shared['last_observed'] == observed
    assert shared['last_model'] == JEV_MODEL
    assert shared['last_model_at'] == datetime.fromisoformat(paper_time)
