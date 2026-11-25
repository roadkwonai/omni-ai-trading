from datetime import date, datetime
from zoneinfo import ZoneInfo

import schedules as S

KST = ZoneInfo('Asia/Seoul')


def job(**kw):
    return {'kind': 'x', 'key': 'k', 'name': 'n', 'state': '등록됨', 'always': False, 'days': None, 'monthday': None,
            'date': None, 'times': [], **kw}


def test_cron_fields_expand_into_days_and_times():
    assert S.field('9-15', 0, 23) == list(range(9, 16)) and S.field('*/20', 0, 59) == [0, 20, 40] and S.field('8,13', 0, 23) == [8, 13]
    hourly = S.cron_job('20 9-15 * * 1-5')
    assert hourly['days'] == S.WEEKDAYS and hourly['times'][0] == (9, 20) and len(hourly['times']) == 7
    monthly = S.cron_job('10 17 1 * *')
    assert monthly['monthday'] == 1 and monthly['days'] is None
    once = S.cron_job('50 8,9 19 11 *')
    assert once['date'] == (11, 19) and once['times'] == [(8, 50), (9, 50)]
    assert S.cron_job('45 16 * * 5')['days'] == frozenset({4}) and S.cron_job('0 9 * * 0')['days'] == frozenset({6})


def test_a_day_plan_lists_each_start_in_time_order():
    weekday, friday, first = date(2026, 11, 19), date(2026, 11, 20), date(2026, 12, 1)
    jobs = [job(name='평일', days=S.WEEKDAYS, times=[(16, 40)]), job(name='금', days=frozenset({4}), times=[(8, 0)]),
            job(name='월초', monthday=1, times=[(17, 10)]), job(name='한 번', date=(2026, 11, 20), times=[(9, 55)]),
            job(name='상시', always=True)]
    assert [(h, m, j['name']) for h, m, j in S.day_plan(jobs, friday)] == [(8, 0, '금'), (9, 55, '한 번'), (16, 40, '평일')]
    assert [j['name'] for _, _, j in S.day_plan(jobs, weekday)] == ['평일']
    assert [j['name'] for _, _, j in S.day_plan(jobs, first)] == ['평일', '월초']
    now = datetime(2026, 11, 20, 10, 0, tzinfo=KST)
    at, nxt = S.next_start(jobs, now)
    assert nxt['name'] == '평일' and at.hour == 16


def test_codex_automations_of_this_repo_only(tmp_path):
    base = tmp_path / '.codex/automations'
    (base / 'mine').mkdir(parents=True)
    (base / 'mine' / 'automation.toml').write_text(f'name = "장중 감사"\nstatus = "PAUSED"\nprompt = "{S.REPO} 에서"\n'
                                                 'rrule = "FREQ=HOURLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=9,10;BYMINUTE=20"\n')
    (base / 'other').mkdir()
    (base / 'other' / 'automation.toml').write_text('name = "다른 일"\nstatus = "ACTIVE"\ncwds = ["/elsewhere"]\nrrule = "FREQ=WEEKLY;BYDAY=SA;BYHOUR=8"\n')
    found = S.codex_jobs(tmp_path)
    assert [(j['name'], j['state'], j['times']) for j in found] == [('장중 감사', '일시 정지', [(9, 20), (10, 20)])]
    assert not S.runs_on(found[0], date(2026, 11, 20))  # paused: not on the day's timeline


def test_utc_codex_rule_keeps_kst_weekdays_and_expiry(tmp_path):
    file = tmp_path / '.codex/automations/check/automation.toml'
    file.parent.mkdir(parents=True)
    file.write_text(f'prompt = "{S.REPO}에서 확인"\nstatus = "ACTIVE"\n'
                    'rrule = "FREQ=HOURLY;BYDAY=TU,WE,TH;BYHOUR=0,6;BYMINUTE=20;UNTIL=20261126T064000Z"\n')
    found, = S.codex_jobs(tmp_path)
    assert found['times'] == [(9, 20), (15, 20)]
    assert S.starts_on(found, date(2026, 11, 26)) == [(9, 20), (15, 20)]
    assert S.starts_on(found, date(2026, 12, 1)) == []


def test_launchd_services_carry_their_times_and_always_on_services_none():
    jobs = {j['key']: j for j in S.launchd_jobs({'장기투자 실행기': '종료'})}
    longterm = jobs['com.vibetrading.vibe-longterm']
    assert longterm['times'] == [(8, 40)] and longterm['days'] == S.WEEKDAYS and longterm['state'] == '종료'
    assert jobs['com.vibetrading.shared-etf-paper']['always'] and jobs['com.vibetrading.us-ideas']['days'] == frozenset({4})
