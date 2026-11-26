"""docs/omni-cli-reference.md from the code: every command, its arguments, an example, what it answers, and every setting.

    .venv/bin/python agent-harness/build_reference.py            # write it
    .venv/bin/python agent-harness/build_reference.py --check    # exit 1 when it is not what the code says
    .venv/bin/python agent-harness/build_reference.py --probe    # read the answers' field names again (local reads on this machine's records)

The settings come from a temporary home with made-up accounts, so no value of this machine is in the document. The field
names (never values) are kept in answer_fields.json.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
OUT = REPO / 'docs' / 'omni-cli-reference.md'
FIELDS = HERE / 'answer_fields.json'
sys.path.insert(0, str(HERE))

NH, KRX, PRICES = 'NH', 'KRX 휴장일', '시세 서버'
EXTERNAL = {'market quote': NH, 'market bars': NH, 'market brief': NH + '·뉴스', 'market investors': NH, 'market refresh': '여러 곳', 'status refresh': NH, 'live balance': NH,
            'premarket preflight': NH, 'premarket status': KRX, 'lt chart': PRICES, 'lt report-data': PRICES + '·KRX', 'lt report-blank': PRICES + '·KRX', 'lt report-check': KRX,
            'lt report-status': KRX, 'lt audit-data': KRX, 'lt cases-refresh': PRICES, 'jev chart': '기록이 없으면 키움·Yahoo, 전일 종가는 Yahoo', 'journal rebuild': '여러 곳',
            'journal merge': '저장된 날이 아니면 여러 곳', 'journal status': KRX, 'ops audit': KRX, 'ops day-start': KRX, 'ops start': KRX, 'ai local': '실계좌 보유를 물으면 NH',
            'research replay': PRICES, 'trade live-sell': NH, 'trade live-buy': NH, 'plan apply': '계획에 따라', 'settings apply': '계획에 따라'}
PAID = {'ai search', 'ai ask', 'schedule probe', 'schedule validate-model'}
WRITES = {'status refresh': '실계좌 잔고 기록 한 줄', 'market refresh': '시장 자료 파일', 'market brief': '공유 뉴스 캐시', 'journal rebuild': '일지 DB', 'journal merge': '일지 DB',
          'backup create': '백업 파일', 'backup tasks': 'ops/scheduled-tasks', 'compare export': '지정한 새 CSV', 'report preview': '지정한 새 폴더', 'premarket preflight': '점검 기록 한 줄',
          'lt cases-refresh': '장기 사례 DB', 'ops audit': '문제 이력 DB', 'ops service-files': '지정한 새 폴더', 'plan approve': '승인 코드 발송', 'settings approve': '승인 코드 발송',
          'plan apply': '계획한 변경·요청·발송', 'settings apply': '계획한 변경·요청·발송', 'plan drop': '계획 상태', 'live pause': '모든 실계좌 신규 매수 중단(바로 적용)·알림 발송',
          'ai ask': '대화 이력', 'ai search': '대화 이력',
          'schedule export': '비공개 예약 번들', 'schedule profile-add': 'AI 실행 프로필',
          'schedule plan': '예약 이전 계획', 'schedule probe': '모델 호출 확인',
          'schedule validate-model': '작업별 검증 결과', 'schedule rollback': '예약 실행 경로 복원',
          'schedule device': '이전용 기기 신원', 'schedule handoff': '원본 실행권 해제 및 인수 영수증',
          'schedule receipt-export': '기발급 인수 영수증 파일'}
PHRASE = {'live on': '<계좌 이름> 실주문', 'live unhalt': 'HALT 해제', 'backup import': '백업 가져오기'}
SETTING_PLANS = {'settings plan', 'settings preset', 'settings revert', 'settings list-edit', 'engine', 'account match'}
ARGS = {'code': '6자리 종목코드', 'key': '계좌 키나 기간 키', 'account': '실계좌 키(`live-1` 등)', 'live': '실계좌 키', 'plan': '계획 번호', 'plan_id': '계획 번호',
        'kind': '리포트 종류(`report list`)', 'name': '이름', 'pairs': '`주소=값` 여러 개', 'book': '장부(`actual`, `live-2`)', 'where': '`paper` 또는 실계좌 키',
        'database': 'nhplug 아래 DB 이름', 'sql': 'SELECT 한 문장', 'service': '서비스 이름(`ops services`)', 'words': '검색어', 'word': '검색어', 'question': '질문',
        'query': '검색어', 'number': '대화 번호', 'address': '설정 주소', 'addresses': '설정 주소 여러 개', 'change': '이력 번호', 'choice': '선택 이름', 'which': '목록',
        'op': '추가·삭제', 'price': '1주 가격(원)', 'qty': '주식 수', 'drop': '하락률 %(−6처럼 써도 됨)', 'need': '실측 이름', 'page': 'HTML 파일', 'markdown': '.md 파일', 'mode': '엔진 모드', 'more': '낱말'}
EXAMPLES = {
    'account checklist': 'live-1', 'account match': 'live-1 --positions 2 --reason "모의를 실계좌에 맞춤"', 'account quote': 'live-1 50000 20', 'account show': 'live-1',
    'ai ask': '"금리가 오르면 성장주는?" --yes', 'ai history': '--find 금리', 'ai local': '"오늘 모의 손익"', 'ai search': '"FOMC 일정" --yes', 'ai talk': '1', 'ai topics': '적립식',
    'backup import': 'vibe-trading-state-20261124-1655.tar.gz --reason "새 맥으로 옮김"', 'backup verify': 'vibe-trading-state-20261124-1655.tar.gz',
    'compare export': '--out ./compare.csv', 'engine': 'jev --reason "장기투자 중지"', 'jev chart': '069500', 'jev events': '--symbol 069500 --limit 20', 'jev explain': '--at 10:30',
    'journal check': '2026-11-24', 'journal merge': '--day 2026-11-24', 'journal period': '2026-11', 'journal rebuild': '--day 2026-11-24', 'live balance': 'live-1', 'live fills': 'live-1',
    'live log': 'live-1', 'live longterm': 'live-2', 'lt chart': 'NDX --days 60', 'lt dca-preview': 'actual amount_krw=300000', 'lt log': '--kind story --detail',
    'lt orders': '--why --day 2026-11-23', 'lt preview': 'lt.weights.NDX=0.25 lt.weights.SPX=0.35', 'market bars': '005930', 'market context': '--keys indices,themes', 'market investors': '005930',
    'market quote': '005930', 'ops logs': 'preflight --lines 40', 'ops service-files': '--system linux --out ./services', 'ops start': 'market-movers --reason "멈춰 있어서"',
    'plan apply': '1a2b3c4d --code ABCD2345', 'plan approve': '1a2b3c4d', 'plan drop': '1a2b3c4d', 'plan show': '1a2b3c4d',
    'schedule export': '--out schedules.json', 'schedule profile-add': 'codex work --model <모델-ID>',
    'schedule probe': 'codex:work', 'schedule plan': '--to codex:work --all',
    'schedule handoff': '--to-device device-1234567890abcdef1234567890abcdef --job "Claude 예약 작업:trading-intraday-report" --out receipt.json',
    'schedule receipt-export': 'transfer-1234567890abcdef1234567890abcdef --out copy.json',
    'schedule validate-model': 'schedule-123456abcdef "Claude 예약 작업:intraday-audit-hourly" --fixture fixture.json',
    'schedule verify': 'schedule-123456abcdef', 'schedule rollback': 'schedule-123456abcdef',
    'query': 'shared-etf-paper/orders.sqlite3 "SELECT count(*) FROM fills"', 'report check-page': './page.html', 'report preview': 'journal ./journal.md --out ./preview',
    'report publish': 'journal 2026-11-24 --reason "오타 고침"', 'report read': 'journal 2026-11-24 --lines 20', 'report send': 'journal 2026-11-24 --reason "다시 보냄"',
    'research cases': '005930 -6', 'research replay': 'yeo', 'settings apply': '1a2b3c4d --code ABCD2345', 'settings approve': '1a2b3c4d',
    'settings explain': 'strategy.entry_confidence', 'settings get': 'strategy.entry_confidence strategy.scale_max_adds', 'settings history': '--group strategy --limit 10',
    'settings list': '--find "분할 금액"', 'settings list-edit': 'watch add 005930 --reason "관심종목에 더함"', 'settings plan': 'strategy.scale_max_adds=5 --reason "추가 매수 줄임"',
    'settings preset': 'volume 엄격 --reason "거래량 확인 강화"', 'settings revert': '203 --reason "되돌림"', 'settings show-plan': '1a2b3c4d', 'template startup': '--blank',
    'trade cancel': 'paper 069500 --reason "취소"', 'trade live-buy': 'live-1 069500 --qty 2 --reason "추가 매수"', 'trade live-sell': 'live-1 069500 --all --reason "정리"',
    'trade paper-buy': '069500 --qty 2 --reason "추가 매수"', 'trade paper-sell': '069500 --qty 2 --reason "비중 축소"',
    **{c: '--reason "사용자가 말한 이유"' for c in ('jev restart', 'jev stop', 'jev unstop', 'live halt', 'live pause', 'live resume', 'live unhalt', 'lt stop', 'lt unstop', 'ops day-start', 'ops notify-test')},
    'live off': 'live-1 --reason "잠시 중단"', 'live on': 'live-1 --reason "다시 시작"'}


def commands():
    """[(name, click command, its group's help)] in the order of the tree."""
    import click
    from cli_anything.omni_trading import omni_trading_cli as C
    import cli_anything.omni_trading.more_commands  # noqa: F401
    out = []

    def walk(group, path, text):
        for name, found in sorted(group.commands.items()):
            if isinstance(found, click.Group):
                walk(found, (*path, name), found.help or '')
            else:
                out.append((' '.join((*path, name)), found, text if path else '묶음 없는 명령'))
    walk(C.cli, (), '')
    return out


def nature(name, found):
    tags = []
    if name in PAID:
        tags.append('**유료 · 외부 전송**')
    elif name in PHRASE:
        tags.append(f'**계획 · 승인 코드 + 확인 문구 `{PHRASE[name]}`**')
    elif name in SETTING_PLANS:
        tags.append('계획(실계좌에 영향을 주면 승인 코드)')
    elif name != 'live pause' and any('--reason' in p.opts for p in found.params):
        tags.append('**계획 · 승인 코드**')
    elif name not in WRITES:
        tags.append('조회')
    if name in EXTERNAL:
        tags.append('외부 조회: ' + EXTERNAL[name])
    if name in WRITES:
        tags.append('씀: ' + WRITES[name])
    return ' · '.join(tags)


def parameter(p):
    import click
    if isinstance(p, click.Argument):
        shown = f'<{p.name}>' + ('…' if p.nargs == -1 else '')
        text = ARGS.get(p.name, '')
    else:
        shown = '/'.join(p.opts) + ('' if p.is_flag else f' <{p.name}>')
        text = p.help or ''
    kind = p.type
    if isinstance(kind, click.Choice):
        text = f"{text} " if text else ''
        text += ' \\| '.join(f'`{c}`' for c in kind.choices)
    elif isinstance(kind, click.IntRange):
        low, high = kind.min, kind.max
        text += f" ({'' if low is None else low}~{'' if high is None else f'{high:,}'})"
    default = getattr(p, 'default', None)
    if isinstance(default, (int, str)) and not isinstance(default, bool) and default != '' and not isinstance(p, click.Argument):
        text += f' · 기본 {default}'
    return f"`{shown}`{' (필수)' if p.required else ''}{': ' + text.strip() if text.strip() else ''}"


def settings_catalog():
    """Every setting on a temporary home: (address with the account replaced, label, what it takes, default, when it takes effect)."""
    from cli_anything.omni_trading.core import settings as S
    from cli_anything.omni_trading.utils.omni_backend import nhplug
    nh, seen = nhplug(), {}
    for group in S.GROUPS.values():
        for key, spec in group.specs(nh).items():
            address = f'{group.name}.{key}'
            if group.name in ('accounts', 'book', 'dca'):
                first, _, rest = key.partition('.')
                address = f"{group.name}.{'paper-shared' if first == 'paper-shared' and group.name == 'accounts' else '<실계좌>' if group.name == 'accounts' else '<장부>'}.{rest}"
            takes = ' \\| '.join(f'`{c}`' for c in spec.choices) if spec.choices else f'{spec.low}~{spec.high}' if spec.low is not None else {
                'bool': '`true` \\| `false`', 'json': 'JSON', 'text': '텍스트'}.get(spec.kind, '')
            default = '—' if spec.default is None else f'`{json.dumps(spec.default, ensure_ascii=False)}`'.replace('|', '\\|')
            seen.setdefault((group.name, address), [address, spec.label, takes, default if len(default) < 60 else 'JSON', spec.effect])
    out = {}
    for (group, _), row in seen.items():
        out.setdefault(group, []).append(row)
    return out


GROUP_TEXT = {'strategy': '매매 기준(Jev 공유 모의 실행기). 실주문이 켜진 Jev 실계좌가 있으면 승인 코드가 필요합니다.',
              'accounts': '계좌별 자동매매·금액·한도. 모의는 `paper-shared`, 실계좌는 `live-1` 같은 키입니다.',
              'lt': '장기 엔진(가상 계좌)의 운용 방식·거장 모듈·비중·수치.', 'book': '장부별 전략(가상 계좌 `actual`, 장기투자 실계좌 키).',
              'dca': '장부별 적립식.', 'tunables': '운영 조정값.', 'ai': 'Jev·OpenAI 판단 사용.', 'prefs': '대시보드 화면 설정(장중에도 바로 적용, 승인 없음).'}


def render():
    import textwrap
    found, fields = commands(), json.loads(FIELDS.read_text(encoding='utf-8')) if FIELDS.exists() else {}
    out = ['# omni 명령·설정 참조', '',
           '`agent-harness/build_reference.py`가 코드에서 만든 문서입니다. 직접 고치지 않습니다. 사용법은 [omni CLI 가이드](omni-cli.md)에 있습니다.', '',
           f'명령 {len(found)}개. 모든 명령에 `--json`을 붙일 수 있습니다. `--json`은 명령 앞뒤 어디에 써도 됩니다.', '',
           '**성격**: 조회는 기록만 읽습니다. 외부 조회는 증권사·시세 서버를 부릅니다. 계획은 `~/.vibe-trading/cli`에 저장만 하고 설정·주문을 바꾸지 않습니다. 승인 코드는 사용자가 알림에서 받아 알려 주는 값입니다.', '',
           '**답**은 `data`의 첫 단계 항목과 목록 한 줄의 항목 이름입니다. 기록에 따라 빠지거나 더 있을 수 있고, 계좌 키(`live-1` 등)는 적지 않았습니다.', '', '## 차례', '']
    names = []
    for name, _, _ in found:
        group = name.split()[0] if ' ' in name else '기타'
        if group not in names:
            names.append(group)
    out += [f"- [{g}](#{g})" for g in sorted(names, key=lambda g: g == '기타')] + ['- [설정](#설정)', '']
    for group in sorted(names, key=lambda g: g == '기타'):
        rows = [(n, c, t) for n, c, t in found if (n.split()[0] if ' ' in n else '기타') == group]
        out += [f'## {group}', '', rows[0][2], '']
        for name, command, _ in rows:
            out += [f'### omni {name}', '', f"{(command.help or '').strip().splitlines()[0]}", '', f'- 성격: {nature(name, command)}']
            params = [parameter(p) for p in command.params]
            out += ['- 인자: ' + (' · '.join(params) if params else '없음')]
            out += [f"- 예: `omni {name}{' ' + EXAMPLES[name] if name in EXAMPLES else ''}`"]
            if fields.get(name):
                out += ['- 답: ' + textwrap.shorten(fields[name], 700, placeholder=' …').replace('[](', '[] (')]  # [](x) would be an empty link
            out += ['']
    out += ['## 설정', '', '주소는 `묶음.키`입니다. 지금 값은 `omni settings get <주소>`, 실계좌 영향은 `omni settings explain <주소>`로 봅니다. '
            '`<실계좌>`·`<장부>` 자리에는 실제 키를 씁니다. 범위는 저장할 때 다시 검사하며, 다른 값에 따라 더 좁을 수 있습니다(예: 운용금은 계좌 잔고까지).', '']
    for group, rows in settings_catalog().items():
        out += [f'### 설정 {group}', '', GROUP_TEXT[group], '', '| 주소 | 이름 | 값 | 기본 | 반영 |', '| --- | --- | --- | --- | --- |']
        out += [f"| `{a}` | {label} | {takes} | {default} | {effect or '—'} |" for a, label, takes, default, effect in rows] + ['']
    return '\n'.join(out).rstrip() + '\n'


SAFE = re.compile(r'(?!live-\d|paper-shared)[a-z][a-z0-9_-]*')


def shape(data):
    """The field names of an answer, never a value (a code or an account may be a key: only plain names are kept)."""
    if not isinstance(data, dict):
        return ''
    def names(d):
        return [k for k in d if SAFE.fullmatch(k)]
    parts = []
    for key in names(data):
        value = data[key]
        row = value[0] if isinstance(value, list) and value and isinstance(value[0], dict) else None
        inner = names(row)[:14] if row else names(value)[:14] if isinstance(value, dict) else []
        parts.append(f"`{key}`" + (f"{'[]' if row else ''}({', '.join(inner)})" if inner else '[]' if isinstance(value, list) else ''))
    return ' · '.join(parts)


def probe():
    """Run every local read without required arguments (and those with an example) on this machine; keep the field names."""
    out = {}
    skip = {n for n, where in EXTERNAL.items() if KRX not in where and 'KRX' not in where} | PAID | set(WRITES) | {'repl', 'template startup', 'report check-page'}
    for name, command, _ in commands():
        if name in skip or any('--reason' in p.opts for p in command.params):
            continue
        import shlex
        args = shlex.split(EXAMPLES.get(name, ''))
        done = subprocess.run([sys.executable, '-m', 'cli_anything.omni_trading', '--json', *name.split(), *args], cwd=REPO, capture_output=True, text=True, encoding='utf-8',
                              env={**os.environ, 'PYTHONPATH': str(HERE)}, timeout=180, stdin=subprocess.DEVNULL)
        try:
            answer = json.loads(done.stdout)
        except ValueError:
            continue
        if answer.get('ok') and (text := shape(answer['data'])):
            out[name] = text
    FIELDS.write_text(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True) + '\n', encoding='utf-8')
    return len(out)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--inside', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.probe:
        print(f'{probe()} commands probed')
        return 0
    if not args.inside:
        with tempfile.TemporaryDirectory() as home:
            seed(Path(home))
            env = {k: v for k, v in os.environ.items() if not k.startswith('VIBE_TRADING_')} | {'HOME': home, 'USERPROFILE': home, 'PYTHONUTF8': '1',
                                                                                                'VIBE_TRADING_ENV_FILE': str(Path(home) / 'empty.env')}
            return subprocess.run([sys.executable, __file__, '--inside', *(['--check'] if args.check else [])], env=env).returncode
    text = render()
    if args.check:
        if not OUT.exists() or OUT.read_text(encoding='utf-8') != text:
            print('참조 문서가 코드와 다릅니다. agent-harness/build_reference.py를 다시 실행하세요.')
            return 1
        print('참조 문서가 코드와 같습니다.')
        return 0
    OUT.write_text(text, encoding='utf-8')
    print(OUT.relative_to(REPO), len(text.splitlines()), '줄')
    return 0


def seed(home):
    """Made-up accounts and default settings, as the tests use."""
    from cli_anything.omni_trading.utils.omni_backend import load
    load()
    import accounts as A
    import strategy_settings as ST
    nh = home / '.vibe-trading' / 'nhplug'
    (nh / 'shared-etf-paper').mkdir(parents=True)
    (nh / 'vibe-longterm').mkdir()
    (home / 'empty.env').write_text('', encoding='utf-8')
    record = lambda kind, engine, label: {'label': label, 'kind': kind, 'engine': engine, 'auto': False, 'budget': dict(A.DEFAULT_BUDGET[kind]),  # noqa: E731
                                          **({'hash': 'h' * 64, 'masked': '***', 'live_orders': False} if kind == 'live' else {})}
    records = {'paper-shared': record('paper', 'jev', '모의계좌'), 'live-1': record('live', 'jev', '실계좌 1'), 'live-2': record('live', 'longterm', '실계좌 2')}
    (nh / 'accounts.json').write_text(json.dumps({'version': 1, 'accounts': records}, ensure_ascii=False), encoding='utf-8')
    (nh / 'shared-etf-paper' / 'strategy-settings.json').write_text(json.dumps(ST.DEFAULTS), encoding='utf-8')
    (nh / 'shared-etf-paper' / 'ai-modes.json').write_text(json.dumps({'version': 1, 'jev': True, 'decision': True}), encoding='utf-8')


if __name__ == '__main__':
    sys.exit(main())
