"""Requests that need the person's approval code (core/actions.py), paid web questions, and the last local batches."""
from __future__ import annotations

from datetime import datetime

import click

from .core import approval, reads
from .core.output import KST, OmniError, Refused, clean, command
from .more_commands import ai, backup
from .omni_trading_cli import (cli, code_of, day_of, group, jev, journal, live, lt, nh, ops, report, research, run_script, settings_apply, settings_approve, settings_plans,
                               settings_show_plan)

trade = group('trade', '매매 요청 계획(승인 코드 필요, 실행기·미러가 가드를 거쳐 처리)')
plan = group('plan', '계획 보기·승인 코드·적용(설정과 요청 공통)')
for _name, _command in (('list', settings_plans), ('show', settings_show_plan), ('approve', settings_approve), ('apply', settings_apply)):
    plan.add_command(_command, _name)

@plan.command('drop')
@click.argument('plan_id')
@command
def plan_drop(plan_id):
    """아직 적용하지 않은 계획 버리기(승인 필요 없음)"""
    return approval.drop(nh(), plan_id)


reason = click.option('--reason', required=True, help='요청 이유(기록에 남음)')


def make(action, reason, **args):
    return approval.plan_action(nh(), action, reason, **args)


def sized(qty, everything):
    if (qty is None) is not everything:
        raise Refused('--qty와 --all 중 하나를 쓰세요.', code=2)
    return qty


@trade.command('paper-sell')
@click.argument('code')
@click.option('--qty', type=click.IntRange(1, 1_000_000))
@click.option('--all', 'everything', is_flag=True, help='전량')
@reason
@command
def trade_paper_sell(code, qty, everything, reason):
    """모의 보유 종목 매도 요청 계획(실계좌 미러도 따라 매도)"""
    return make('paper_sell', reason, code=code_of(code), qty=sized(qty, everything))


@trade.command('paper-buy')
@click.argument('code')
@click.option('--qty', type=click.IntRange(1, 1_000_000), required=True)
@reason
@command
def trade_paper_buy(code, qty, reason):
    """모의 보유 종목 추가 매수 요청 계획(실계좌 미러도 따라 매수)"""
    return make('paper_buy', reason, code=code_of(code), qty=qty)


@trade.command('live-sell')
@click.argument('account')
@click.argument('code')
@click.option('--qty', type=click.IntRange(1, 1_000_000))
@click.option('--all', 'everything', is_flag=True, help='전량(오늘 다시 사지 않음)')
@reason
@command
def trade_live_sell(account, code, qty, everything, reason):
    """실계좌 매도 요청 계획(미러가 산 수량만, 외부 조회 1회)"""
    return make('live_sell', reason, account=account, code=code_of(code), qty=sized(qty, everything))


@trade.command('live-buy')
@click.argument('account')
@click.argument('code')
@click.option('--qty', type=click.IntRange(1, 1_000_000), required=True)
@reason
@command
def trade_live_buy(account, code, qty, reason):
    """실계좌 추가 매수 요청 계획(미러 보유 종목만, 외부 조회 1회)"""
    return make('live_buy', reason, account=account, code=code_of(code), qty=qty)


@live.command('on')
@click.argument('account')
@reason
@command
def live_on(account, reason):
    """실주문 켜기 계획(승인 코드와 확인 문구)"""
    return make('live_on', reason, account=account)


@live.command('resume')
@reason
@command
def live_resume(reason):
    """실계좌 신규 매수 재개 계획(미확인 주문·오늘 손실 정지가 있으면 거절)"""
    return make('live_resume', reason)


@live.command('unhalt')
@reason
@command
def live_unhalt(reason):
    """NH 주문 HALT 해제 계획(승인 코드와 확인 문구, 다른 증권사 정지는 유지)"""
    return make('live_unhalt', reason)


@jev.command('unstop')
@reason
@command
def jev_unstop(reason):
    """모의 실행기 중단 해제 계획(미확인 주문 0건일 때, 시작은 하지 않음)"""
    return make('paper_unstop', reason)


@backup.command('import')
@click.argument('name')
@reason
@command
def backup_import(name, reason):
    """백업 가져오기 계획(서비스가 모두 꺼져 있을 때, 승인 코드와 확인 문구)"""
    return make('backup_import', reason, name=name)


@trade.command('cancel')
@click.argument('where')
@click.argument('code')
@reason
@command
def trade_cancel(where, code, reason):
    """대기 중인 매매 요청 취소 계획(where: paper 또는 실계좌 키)"""
    return make('request_cancel', reason, where=where, code=code_of(code))


@jev.command('stop')
@reason
@command
def jev_stop(reason):
    """모의 실행기 중단 계획(멈춘 동안 손절·익절 감시도 없음)"""
    return make('paper_stop', reason)


@jev.command('restart')
@reason
@command
def jev_restart(reason):
    """모의 실행기 재시작 계획(미확인 주문·손절 근접이면 거절)"""
    return make('paper_restart', reason)


@lt.command('stop')
@reason
@command
def lt_stop(reason):
    """장기 엔진 중지 요청 계획"""
    return make('lt_stop', reason)


@lt.command('unstop')
@reason
@command
def lt_unstop(reason):
    """장기 엔진 중지 요청 해제 계획"""
    return make('lt_unstop', reason)


@ops.command('day-start')
@reason
@command
def ops_day_start(reason):
    """거래일 시작 계획(모의 실행기·학습·국면 관찰기)"""
    return make('day_start', reason)


@ops.command('start')
@click.argument('service')
@reason
@command
def ops_start(service, reason):
    """등록된 서비스 시작 계획(주문 서비스는 거래일 08:00~15:00, 미확인 주문 0건일 때)"""
    return make('service_start', reason, service=service)


@ops.command('notify-test')
@reason
@command
def ops_notify_test(reason):
    """알림 채널 시험 발송 계획"""
    return make('notify_test', reason)


@report.command('publish')
@click.argument('kind')
@click.argument('key')
@reason
@command
def report_publish(kind, key, reason):
    """리포트 다시 게시 계획(공개 페이지)"""
    return make('report_publish', reason, report=f'{kind}:{key}')


@report.command('send')
@click.argument('kind')
@click.argument('key')
@reason
@command
def report_send(kind, key, reason):
    """리포트 링크 다시 발송 계획"""
    return make('report_send', reason, report=f'{kind}:{key}')


# --- paid web questions (Liner) -----------------------------------------------------------------------------------------------

DAILY = 20  # calls a day by this CLI; the dashboard has no such cap


def paid(kind, text, yes):
    """Checks before a paid, outgoing request, and the day's count (kept even when the request then fails)."""
    if clean(text) != text:
        raise Refused('계좌번호나 키로 보이는 값이 들어 있어 보내지 않습니다.')
    if not yes:
        raise Refused(f'외부 서비스(Liner)로 보내는 유료 요청입니다. 사용자가 요금과 내용을 확인했으면 --yes를 붙이세요(하루 {DAILY}회까지).', code=3)
    root = nh()
    with approval.store(root) as db:
        today = reads.today()
        used = db.execute('SELECT count(*) FROM paid WHERE time>=?', (today,)).fetchone()[0]
        if used >= DAILY:
            raise Refused(f'오늘 유료 요청 {DAILY}회를 다 썼습니다.')
        db.execute('INSERT INTO paid VALUES (?,?)', (datetime.now(KST).isoformat(timespec='seconds'), kind))
    return used + 1


def asked(call):
    try:
        return call()
    except ValueError as exc:  # liner_search's own words (no key, no body)
        raise OmniError(str(exc), 6) from None


@ai.command('issues')
@command
def ai_issues():
    """최근 매매일지의 주요 이슈 5개(요금 없음)"""
    import api_status_view as V
    day, found = V.liner_issues()
    return {'day': day, 'issues': [{'number': i, 'stars': r.get('stars'), 'title': r.get('title'), 'link': r.get('link')} for i, r in enumerate(found, 1)]}


@ai.command('search')
@click.argument('query')
@click.option('--yes', is_flag=True, help='요금과 외부 전송을 확인함')
@command
def ai_search(query, yes):
    """웹 검색(유료, Liner로 전송)"""
    import liner_history
    import liner_search
    used = paid('검색', query, yes)
    found = asked(lambda: liner_search.search(query))
    liner_history.record('검색', query)
    number = liner_history.save('검색', query, {'question': query, 'answer': '', 'sources': found}, subject={'주제': query})
    return {'talk': number, 'used_today': used, 'results': found}


@ai.command('ask')
@click.argument('question')
@click.option('--talk', type=int, help='이어서 물을 대화 번호')
@click.option('--issue', type=click.IntRange(1, 5), help='`ai issues`의 번호(시장 영향 분석)')
@click.option('--yes', is_flag=True, help='요금과 외부 전송을 확인함')
@command
def ai_ask(question, talk, issue, yes):
    """투자·서비스 질문(유료, Liner로 전송; 내부 데이터 질문은 `ai local`)"""
    import api_status_view as V
    import liner_history
    import liner_local_data
    import liner_search
    import liner_topics
    if liner_local_data.resolve(question) is not None:
        raise Refused('이 컴퓨터의 기록으로 답할 수 있는 질문입니다. 보내지 않았습니다.', next=[f'omni ai local "{question}"'])
    thread = liner_history.load(talk) if talk else {'subject': None, 'turns': []}
    if not thread:
        raise Refused('해당 대화가 없습니다.', code=5)
    topic = thread.get('subject')
    if issue:
        day, found = V.liner_issues()
        if issue > len(found):
            raise Refused('해당 이슈가 없습니다.', code=5)
        topic = {'주제': found[issue - 1]['title'].strip(), '매매일지 기준일': day, '기사 링크': found[issue - 1].get('link') or ''}
    used = paid('질문', question, yes)
    guides = liner_topics.find_guides(question)
    answer, sources = asked(lambda: liner_search.analyze(V.question_messages(topic, thread.get('turns') or [], question, guides, datetime.now(KST))))
    liner_history.record('질문', question)
    number = liner_history.save('대화', question, {'question': question, 'answer': answer, 'sources': sources, 'guides': guides, 'local': False, 'local_query': None},
                                thread_id=talk, subject=topic)
    return {'talk': number, 'used_today': used, 'answer': answer, 'sources': sources}


# --- local batches ----------------------------------------------------------------------------------------------------------------

REPLAYS = {'dca': ('longterm_dca_replay.py', ()), 'dca-masters': ('longterm_dca_replay.py', ('--profiles',)), 'dca-presets': ('longterm_dca_replay.py', ('--presets',)),
           'dca-pace': ('longterm_dca_replay.py', ('--pace',)), 'dca-v3': ('longterm_dca_v3_replay.py', ()), 'yeo': ('yeo_method_replay.py', ()),
           'yeo-dca': ('yeo_method_replay.py', ('--dca',)), 'engine': ('vibe_longterm_replay.py', ())}


@research.command('replay')
@click.argument('name', type=click.Choice(list(REPLAYS)))
@command
def research_replay(name):
    """장기 전략 과거 재현(외부 시세 조회, 몇 분 걸림, 기록하지 않음)"""
    nh()
    script, args = REPLAYS[name]
    return run_script(script, *args, as_text=True, timeout=900)


@lt.command('cases-refresh')
@command
def lt_cases_refresh():
    """장기 과거 사례 다시 만들기(외부 시세 조회, 사례 DB에 씀)"""
    nh()
    return run_script('longterm_cases.py', '--refresh', timeout=600)


@journal.command('merge')
@click.option('--day', required=True)
@command
def journal_merge(day):
    """작성된 분석을 저장된 일지에 붙임(일지 DB에 씀)"""
    nh()
    return run_script('trading_journal.py', '--merge', '--day', day_of(day))


@ops.command('audit')
@click.option('--since', help='HH:MM')
@command
def ops_audit(since):
    """장중 감사 자료(문제 이력 DB에 기록)"""
    import re
    nh()
    if since and not re.fullmatch(r'\d{2}:\d{2}', since):
        raise Refused('시각은 HH:MM입니다.', code=2)
    return run_script('intraday_audit.py', *(['--since', since] if since else []))


@backup.command('tasks')
@command
def backup_tasks():
    """예약 작업 정의 백업(저장소 ops/scheduled-tasks)"""
    nh()
    return run_script('state_sync.py', 'tasks-export', as_text=True)


# --- the words an option shows when its command gives none (one place, so --help and the reference say the same) -----------------

HELP = {'day': 'YYYY-MM-DD(기본 오늘)', 'since': '이 날짜부터(YYYY-MM-DD)', 'start': '시작 날짜(YYYY-MM-DD)', 'end': '끝 날짜(YYYY-MM-DD)', 'limit': '최대 개수',
        'reason': '사용자가 말한 이유(기록에 남음)', 'keys': '쉼표로 고른 항목만', 'book': '장부 이름', 'key': '날짜 또는 기간 키(2026-11-24 · 2026-W48 · 2026-11)',
        'days': '최근 일수', 'find': '검색어', 'archived': '보관한 대화도 포함', 'event': '이벤트 이름', 'symbol': '6자리 종목코드', 'kind': '기록 종류', 'lines': '줄 수',
        'qty': '주식 수', 'positions': '동시 보유 종목 수', 'name': '표시 이름', 'group': '설정 묶음(strategy · accounts · lt · book · dca · tunables · ai · prefs)', 'account': '계좌 키', 'during_market': '장중(08:55~15:20)에도 적용',
        'system': '운영체제', 'out': '새로 만들 경로'}


def describe(where):
    for found in where.commands.values():
        if isinstance(found, click.Group):
            describe(found)
        for p in found.params:
            if isinstance(p, click.Option) and not p.help:
                p.help = HELP.get(p.opts[0].lstrip('-').replace('-', '_')) or HELP.get(p.name)


describe(cli)
