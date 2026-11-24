"""The agent documents: the reference is what the code says, and every example in them is a command that exists."""
import re
import shlex
import subprocess
import sys
from pathlib import Path

import click
import pytest

from cli_anything.omni_trading.omni_trading_cli import cli

REPO = Path(__file__).resolve().parents[4]
DOCS = [REPO / 'README.md', REPO / 'docs/omni-cli.md', REPO / 'docs/omni-cli-reference.md', REPO / 'docs/ai-agents.md', REPO / 'agent-harness/cli_anything/omni_trading/skills/SKILL.md']


def parse(line):
    """The command a line names, with its arguments accepted (nothing runs)."""
    words = shlex.split(line, comments=True)
    where, ctx = cli, click.Context(cli, info_name='omni')
    while words and words[0] in ('--json', '--agent'):
        words = words[2 if words[0] == '--agent' else 1:]
    while isinstance(where, click.Group) and words and words[0] in where.commands:
        where = where.commands[words.pop(0)]
    words = [w for w in words if w != '--json']
    if '--help' in words or (isinstance(where, click.Group) and where.invoke_without_command and not words):
        return where
    assert not isinstance(where, click.Group), line
    where.make_context(where.name, words, parent=ctx)
    return where


def examples():
    for path in DOCS:
        if not path.exists():
            continue
        text = path.read_text(encoding='utf-8')
        for block in re.findall(r'```bash\n(.*?)```', text, re.S):
            for line in block.splitlines():
                if line.startswith(('omni ', '.venv/bin/omni ')):
                    yield path.name, line.split('omni ', 1)[1]
        for line in re.findall(r'^- 예: `omni (.+)`$', text, re.M):
            yield path.name, line


@pytest.mark.parametrize('where, line', list(examples()))
def test_every_example_is_a_command_that_exists(where, line):
    try:
        parse(line)
    except click.UsageError as exc:
        pytest.fail(f'{where}: omni {line}: {exc.format_message()}')


def test_every_command_the_guide_names_exists():
    names = {' '.join(p) for p in walk(cli, ())}
    groups = {n.split()[0] for n in names}
    text = (REPO / 'docs/omni-cli.md').read_text(encoding='utf-8')
    for line in re.findall(r'`(?:omni )?((?:%s) [a-z][a-z0-9-]*)' % '|'.join(sorted(groups)), text):
        assert line in names or line.split()[0] in ('query', 'engine', 'guide', 'repl'), line


def walk(where, path):
    for name, found in where.commands.items():
        yield from walk(found, (*path, name)) if isinstance(found, click.Group) else [(*path, name)]


def test_every_command_and_option_explains_itself():
    for path in walk(cli, ()):
        where = cli
        for name in path:
            where = where.commands[name]
        assert (where.help or '').strip(), path
        assert all(p.help for p in where.params if isinstance(p, click.Option)), path


def test_the_reference_is_what_the_code_says():
    done = subprocess.run([sys.executable, str(REPO / 'agent-harness/build_reference.py'), '--check'], capture_output=True, text=True, encoding='utf-8')
    assert done.returncode == 0, done.stdout + done.stderr[-500:]


def test_the_guide_lists_every_command_that_goes_outside_or_writes():
    sys.path.insert(0, str(REPO / 'agent-harness'))
    import build_reference as B
    text = (REPO / 'docs/omni-cli.md').read_text(encoding='utf-8')
    table = text[text.index('### 기록을 남기거나 외부로 요청하는 명령'):text.index('### DB 직접 조회')]
    skip = {'plan apply', 'settings apply', 'plan approve', 'settings approve', 'plan drop', 'live pause'}  # section 7 and 8 tell these
    for name in (set(B.EXTERNAL) | B.PAID | set(B.WRITES)) - skip:
        assert name in table or f"`{name.split()[1]}`" in table, name


def test_the_guide_s_contents_lead_to_its_sections():
    text = (REPO / 'docs/omni-cli.md').read_text(encoding='utf-8')
    anchor = lambda title: re.sub(r'[^\w\- ]', '', title.lower()).replace(' ', '-')  # noqa: E731 - as GitHub makes them
    have = {anchor(h) for h in re.findall(r'^#+ (.+)$', text, re.M)}
    links = re.findall(r'\]\(#([^)]+)\)', text)
    assert links and not [x for x in links if x not in have]


def test_the_readme_and_the_landing_page_give_the_same_install_prompt_and_real_commands():
    landing = REPO / 'landing/src/content.ts'
    if not landing.exists():
        pytest.skip('no landing page here')
    page, readme = landing.read_text(encoding='utf-8'), (REPO / 'README.md').read_text(encoding='utf-8')
    prompt = re.search(r'export const CLI_PROMPT = `(.*?)`\n', page, re.S).group(1)
    assert prompt in readme
    scenes = page[page.index('export const CLI_SCENES'):page.index('export const STEPS')]
    lines = re.findall(r"'\$ omni (.+?)'(?=[,\] ])", scenes)
    assert len(lines) >= 8
    for line in lines:
        parse(line)
    names = {' '.join(p) for p in walk(cli, ())} | set(cli.commands)
    for line in re.findall(r'`omni ([a-z][^`]*)`', readme):
        assert ' '.join(line.split()[:2]) in names or line.split()[0] in names, line
