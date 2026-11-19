import re
import sys
from pathlib import Path

import pytest

import release

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import ci_private_files_gate as gate  # noqa: E402


def tree(tmp_path, ver='0.1.0', note=True, badge=True, landing=True):
    (tmp_path / 'VERSION').write_text(ver + '\n')
    (tmp_path / 'release').mkdir()
    if note:
        (tmp_path / 'release' / f'2026-11-19-v{ver}-first.md').write_text(f'# Omni AI Trading v{ver}\n\n' + '본문 ' * 80)
    (tmp_path / 'README.md').write_text(f'release-v{ver}-ff6600 releases/tag/v{ver}' if badge else 'none')
    if landing:
        (tmp_path / 'landing' / 'src').mkdir(parents=True)
        (tmp_path / 'landing' / 'src' / 'content.ts').write_text(f"export const VERSION = '{ver}'\n")
    return tmp_path


def test_repository_is_ready_for_its_version():
    assert release.check() == []


def test_check_passes_and_reports_each_problem(tmp_path):
    assert release.check(tree(tmp_path)) == []
    (tmp_path / 'b').mkdir()
    problems = release.check(tree(tmp_path / 'b', note=False, badge=False, landing=False))
    assert any('릴리스 노트' in p for p in problems) and any('README' in p for p in problems)


def test_landing_version_must_match(tmp_path):
    root = tree(tmp_path)
    (root / 'landing' / 'src' / 'content.ts').write_text("export const VERSION = '0.0.9'\n")
    assert any('content.ts' in p for p in release.check(root))


@pytest.mark.parametrize('text', ['0.1', 'v0.1.0', '01.0.0', ''])
def test_version_must_be_semver(tmp_path, text):
    (tmp_path / 'VERSION').write_text(text)
    with pytest.raises(ValueError):
        release.version(tmp_path)


def test_note_lookup_needs_exactly_one_note(tmp_path):
    folder = tmp_path / 'release'
    folder.mkdir()
    (folder / '2026-11-19-v0.1.0-first.md').write_text('# x\n')
    (folder / '2026-11-19-v0.1.1-next.md').write_text('# y\n')
    (folder / 'README.md').write_text('# guide\n')
    assert release.note_path('0.1.0', folder).name == '2026-11-19-v0.1.0-first.md'
    (folder / '2026-11-20-v0.1.0-again.md').write_text('# z\n')
    with pytest.raises(ValueError):
        release.note_path('0.1.0', folder)


def test_note_parts_split_title_and_body(tmp_path):
    path = tmp_path / 'n.md'
    path.write_text('# Omni v0.1.0\n\n## 요약\n내용\n')
    assert release.note_parts(path) == ('Omni v0.1.0', '## 요약\n내용\n')
    path.write_text('제목 없음\n')
    with pytest.raises(ValueError):
        release.note_parts(path)


def test_snapshot_leaves_out_private_paths_and_passes_the_gate():
    tree_sha, dropped = release.snapshot_tree()
    assert re.fullmatch(r'[0-9a-f]{40}', tree_sha)
    assert all(p.split('/')[0] in release.PRIVATE_PATHS for p in dropped)


def test_gitignore_whitelists_exactly_the_public_guides():
    lines = (ROOT / '.gitignore').read_text().splitlines()
    listed = {line[len('!/docs/'):] for line in lines if re.fullmatch(r'!/docs/[^/]+\.md', line)}
    assert listed == set(gate.PUBLIC_DOCS)


def test_release_api_creates_then_updates():
    calls = []

    def api(method, path, cfg, body=None):
        calls.append((method, path))
        if method == 'GET':
            return (200, {'id': 7}) if len(calls) > 2 else (404, {})
        return 201 if method == 'POST' else 200, {'html_url': 'u'}

    cfg = {'repo': 'o/r', 'token': 't'}
    assert release.ensure_release(cfg, 'v0.1.0', 'n', 'b', api) == ('created', 'u')
    assert release.ensure_release(cfg, 'v0.1.0', 'n', 'b', api) == ('updated', 'u')
    assert ('PATCH', '/repos/o/r/releases/7') in calls


def test_existing_tag_on_another_commit_is_refused():
    def api(method, path, cfg, body=None):
        if path.endswith('/git/ref/tags/v0.1.0'):
            return 200, {'object': {'type': 'tag', 'sha': 'tagobj'}}
        if path.endswith('/git/tags/tagobj'):
            return 200, {'object': {'sha': 'other'}}
        raise AssertionError(path)

    with pytest.raises(RuntimeError):
        release.ensure_tag({'repo': 'o/r'}, 'v0.1.0', 'mine', 'm', api)
    assert release.ensure_tag({'repo': 'o/r'}, 'v0.1.0', 'other', 'm', api) == 'exists'
