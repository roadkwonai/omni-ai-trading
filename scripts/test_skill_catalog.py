import os

import skill_catalog as sc


def test_every_skill_file_exists_and_every_job_names_a_known_skill():
    # archify and diagram are installed per machine under ~/.claude/skills; agent/tests sandboxes HOME for the whole run
    user_level = {'archify', 'diagram'}
    assert all(sc.skill_file(s['key']) for s in sc.SKILLS if s['key'] not in user_level)
    assert {j['skill'] for j in sc.JOBS if j['skill']} <= set(sc.BY_KEY)


def test_outputs_pick_the_newest_file_of_each_kind(tmp_path):
    reviews = tmp_path / sc.REVIEWS
    reviews.mkdir(parents=True)
    for name, mtime in (('journal-2026-11-16.md', 1), ('journal-2026-11-17.md', 2), ('journal-2026-11.md', 3), ('journal-2026-Q4.md', 4), ('journal-2026.md', 5)):
        (reviews / name).write_text('x')
        os.utime(reviews / name, (mtime, mtime))
    outputs = dict(sc.BY_KEY['trading-journal-writer']['outputs'])
    picked = {label: sc.latest(pattern, tmp_path)[0].name for label, pattern in outputs.items() if sc.latest(pattern, tmp_path)[0]}
    assert picked == {'하루 매매관점 리포트': 'journal-2026-11-17.md', '월간 매매일지': 'journal-2026-11.md',
                      '분기·반기 매매일지': 'journal-2026-Q4.md', '연간 매매일지': 'journal-2026.md'}


def test_job_state_reads_codex_status_and_claude_registration(tmp_path):
    codex = tmp_path / '.codex/automations/automation'
    codex.mkdir(parents=True)
    (codex / 'automation.toml').write_text('name = "장중"\nstatus = "PAUSED"\n')
    claude = tmp_path / '.claude/scheduled-tasks/trading-journal'
    claude.mkdir(parents=True)
    (claude / 'SKILL.md').write_text('---\n')
    jobs = {j['key']: j for j in sc.JOBS}
    assert sc.job_state({'key': 'automation', 'runner': 'Codex 자동화'}, tmp_path) == '일시 정지'
    assert sc.job_state({'key': 'automation-2', 'runner': 'Codex 자동화'}, tmp_path) == '없음'
    assert sc.job_state(jobs['trading-journal'], tmp_path) == '등록됨'


def test_jobs_are_the_tasks_that_run():
    """2026-11-18: the premarket analysis and the hourly audit moved from the paused Codex automations to Claude tasks,
    and the 08:25 preflight task is new."""
    jobs = {j['key']: j for j in sc.JOBS}
    for key in ('trading-preflight', 'premarket-analysis-daily', 'intraday-audit-hourly'):
        assert jobs[key]['runner'] == 'Claude 예약 작업'
    assert not [j for j in sc.JOBS if j['runner'] == 'Codex 자동화']
    assert all('Codex' not in w for s in sc.SKILLS for w in s['when'])


def test_diagram_skills_are_listed_with_where_their_files_are():
    from pathlib import Path
    assert sc.BY_KEY['archify']['kind'] == '도식·차트 스킬' and '전략 구조' in sc.BY_KEY['archify']['screen']
    assert sc.shown_path(None) == '파일 없음'
    assert sc.shown_path(sc.REPO / '.claude/skills/x/SKILL.md') == '.claude/skills/x/SKILL.md'
    assert sc.shown_path(Path.home() / '.claude/skills/archify/SKILL.md') == '~/.claude/skills/archify/SKILL.md'



def test_jobs_list_every_backed_up_task_with_its_schedule(tmp_path):
    """user 2026-11-19: the screen lists every Claude scheduled task from the backup manifest (ops/scheduled-tasks), so a task
    added later shows up and another account restores the same list."""
    import json
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps([
        {'taskId': 'trading-journal', 'title': '매매일지 작성', 'description': 'd', 'cron': '40 16 * * 1-5', 'schedule': None, 'enabled': True},
        {'taskId': 'new-task', 'title': '새 작업', 'description': '새 설명', 'cron': '0 7 * * 5', 'schedule': None, 'enabled': False},
        {'taskId': 'once', 'title': '한 번', 'description': 'x', 'cron': None, 'schedule': '2026-11-30T15:40:00+09:00', 'enabled': True}]))
    jobs = {j['key']: j for j in sc.jobs(manifest)}
    assert set(jobs) == {'trading-journal', 'new-task', 'once'}
    assert jobs['trading-journal']['skill'] == 'trading-journal-writer' and jobs['trading-journal']['when'] == '평일 16:40'
    assert (jobs['new-task']['name'], jobs['new-task']['when'], jobs['new-task']['result']) == ('새 작업', '금 07:00', '새 설명')
    assert jobs['once']['when'] == '11/30 15:40 한 번'
    assert sc.job_state(jobs['new-task'], tmp_path) == '꺼짐'
    assert sc.cron_text('20 9-15 * * 1-5') == '평일 9~15시 20분' and sc.cron_text('10 17 1 * *') == '매월 1일 17:10'


def test_jobs_run_in_clock_order():
    order = [j['key'] for j in sc.jobs()]
    assert order.index('trading-preflight') < order.index('premarket-analysis-daily') < order.index('intraday-audit-hourly') < order.index('trading-journal')
