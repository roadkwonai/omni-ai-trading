"""Versioned releases of the public repository (roadkwonai/omni-ai-trading).

The local checkout is a shallow clone of the upstream Vibe-Trading and also holds operating files that must not be
published, so a release does not push the local branch. It builds a public snapshot of HEAD in a separate index
(the working tree and the local branch are not touched), commits it on top of the public ``main``, pushes it, tags it
``v<VERSION>`` through the GitHub API and creates (or updates) the GitHub Release from the release note.

Conventions (release/README.md):
  VERSION                                   the single version number (Semantic Versioning, e.g. 0.1.0)
  release/YYYY-MM-DD-v<VERSION>-<title>.md  the release note; its body becomes the GitHub Release text

Usage:
  scripts/release.py --check               verify VERSION, the note, the README badge and the landing version
  scripts/release.py --dry-run             build the public snapshot and list what it leaves out (no push)
  scripts/release.py --publish             snapshot → push main → tag → GitHub Release
  scripts/release.py --push-site           commit and push the Pages site folder (landing deploy without a report)

The token (GH_TOKEN) is read by report_pages.settings() and reaches git only through GIT_CONFIG_* environment
variables and the GitHub API only through a header; it is never printed or written.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASE_DIR = ROOT / 'release'
SEMVER = re.compile(r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$')
NOTE_NAME = re.compile(r'^(\d{4}-\d{2}-\d{2})-v(?P<ver>[0-9A-Za-z.-]+?)(?:-(?P<title>.+))?\.md$')
# Tracked locally but never published: operating settings, scheduled-task prompts and upstream-only CI.
# Internal docs are refused separately by tools/ci_private_files_gate.py, which runs on the snapshot index.
PRIVATE_PATHS = ('state', 'ops', '.github')
PUBLIC_BRANCH = 'main'
AUTHOR = {'GIT_AUTHOR_NAME': 'roadkwonai', 'GIT_AUTHOR_EMAIL': '337258440+roadkwonai@users.noreply.github.com'}
AUTHOR |= {'GIT_COMMITTER_NAME': AUTHOR['GIT_AUTHOR_NAME'], 'GIT_COMMITTER_EMAIL': AUTHOR['GIT_AUTHOR_EMAIL']}
TRAILER = 'Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>'


def version(root=ROOT):
    """The version in VERSION, validated as Semantic Versioning."""
    text = (Path(root) / 'VERSION').read_text().strip()
    if not SEMVER.match(text):
        raise ValueError(f'VERSION이 Semantic Versioning 형식이 아닙니다: {text!r}')
    return text


def note_path(ver, folder=RELEASE_DIR):
    """The one release note for ``ver`` (release/YYYY-MM-DD-v<ver>[-title].md)."""
    found = [p for p in sorted(Path(folder).glob('*.md')) if (m := NOTE_NAME.match(p.name)) and m['ver'] == ver]
    if len(found) != 1:
        raise ValueError(f'v{ver} 릴리스 노트가 {len(found)}개입니다(1개여야 함): release/YYYY-MM-DD-v{ver}-<제목>.md')
    return found[0]


def note_parts(path):
    """(title, body) of a release note: the first '# ' line is the release name, the rest is the Release text."""
    lines = Path(path).read_text().splitlines()
    if not lines or not lines[0].startswith('# '):
        raise ValueError(f'{Path(path).name}: 첫 줄이 "# 제목"이어야 합니다')
    return lines[0][2:].strip(), '\n'.join(lines[1:]).strip() + '\n'


def check(root=ROOT):
    """Problems that block a release (empty list = ready)."""
    root = Path(root)
    problems = []
    try:
        ver = version(root)
    except (OSError, ValueError) as exc:
        return [str(exc)]
    try:
        title, body = note_parts(note_path(ver, root / 'release'))
        if f'v{ver}' not in title:
            problems.append(f'릴리스 노트 제목에 v{ver}가 없습니다: {title}')
        if len(body) < 200:
            problems.append('릴리스 노트 본문이 너무 짧습니다')
    except (OSError, ValueError) as exc:
        problems.append(str(exc))
    readme = (root / 'README.md').read_text()
    if f'release-v{ver}-' not in readme or f'releases/tag/v{ver}' not in readme:
        problems.append(f'README 릴리스 배지가 v{ver}가 아닙니다')
    content = root / 'landing' / 'src' / 'content.ts'
    if content.is_file() and f"VERSION = '{ver}'" not in content.read_text():
        problems.append(f"landing/src/content.ts의 VERSION이 '{ver}'가 아닙니다")
    return problems


def _git(args, env=None, cfg=None, input_text=None):
    import report_pages as rp
    if cfg:
        return rp.git(args, ROOT, cfg)
    done = subprocess.run(['git', *args], cwd=ROOT, env=env, capture_output=True, text=True, input=input_text)
    if done.returncode:
        raise RuntimeError(rp.clean_error((done.stderr or done.stdout).strip() or f'git {args[0]} 실패'))
    return done.stdout


def snapshot_tree(ref='HEAD'):
    """(tree sha, left-out paths) of ``ref`` without PRIVATE_PATHS, checked by the private-files gate."""
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / 'index'))
        _git(['read-tree', ref], env)
        dropped = [p for p in _git(['ls-files', '--', *PRIVATE_PATHS], env).splitlines() if p]
        if dropped:
            _git(['rm', '-r', '-q', '--cached', '--ignore-unmatch', '--', *PRIVATE_PATHS], env)
        gate = subprocess.run([sys.executable, 'tools/ci_private_files_gate.py'], cwd=ROOT, env=env,
                              capture_output=True, text=True)
        if gate.returncode:
            raise RuntimeError('비공개 문서 검사 실패:\n' + (gate.stdout + gate.stderr).strip())
        return _git(['write-tree'], env).strip(), dropped


def public_head(cfg):
    """The public main commit (fetched into FETCH_HEAD), or None for an empty repository."""
    try:
        _git(['fetch', '-q', '--no-tags', f"https://github.com/{cfg['repo']}.git", PUBLIC_BRANCH], cfg=cfg)
    except RuntimeError as exc:
        if "couldn't find remote ref" in str(exc):
            return None
        raise
    return _git(['rev-parse', 'FETCH_HEAD']).strip()


def snapshot_commit(tree, parent, message):
    """A commit of ``tree`` on ``parent`` by the public author; the parent itself when nothing changed."""
    if parent and _git(['rev-parse', f'{parent}^{{tree}}']).strip() == tree:
        return parent
    args = ['commit-tree', tree, '-F', '-'] + (['-p', parent] if parent else [])
    return _git(args, env=dict(os.environ, **AUTHOR), input_text=message).strip()


def ensure_tag(cfg, tag, sha, message, api=None):
    """'created' or 'exists': an annotated tag ``tag`` on ``sha`` made through the GitHub API (no local tag)."""
    import report_pages as rp
    api = api or rp.github
    status, ref = api('GET', f"/repos/{cfg['repo']}/git/ref/tags/{tag}", cfg)
    if status == 200:
        obj = ref.get('object') or {}
        target = obj.get('sha')
        if obj.get('type') == 'tag':
            _, tag_obj = api('GET', f"/repos/{cfg['repo']}/git/tags/{target}", cfg)
            target = (tag_obj.get('object') or {}).get('sha')
        if target != sha:
            raise RuntimeError(f'{tag} 태그가 이미 다른 커밋({str(target)[:7]})에 있습니다. 버전을 올리세요.')
        return 'exists'
    tagger = {'name': AUTHOR['GIT_AUTHOR_NAME'], 'email': AUTHOR['GIT_AUTHOR_EMAIL']}
    status, made = api('POST', f"/repos/{cfg['repo']}/git/tags", cfg,
                       {'tag': tag, 'message': message, 'object': sha, 'type': 'commit', 'tagger': tagger})
    if status != 201:
        raise RuntimeError(f'태그 객체 만들기 실패({status}): {made.get("message")}')
    status, out = api('POST', f"/repos/{cfg['repo']}/git/refs", cfg, {'ref': f'refs/tags/{tag}', 'sha': made['sha']})
    if status != 201:
        raise RuntimeError(f'태그 참조 만들기 실패({status}): {out.get("message")}')
    return 'created'


def ensure_release(cfg, tag, name, body, api=None):
    """(action, html_url): the GitHub Release for ``tag``, created or its name/body updated."""
    import report_pages as rp
    api = api or rp.github
    status, rel = api('GET', f"/repos/{cfg['repo']}/releases/tags/{tag}", cfg)
    fields = {'name': name, 'body': body, 'draft': False, 'prerelease': False, 'make_latest': 'true'}
    if status == 200:
        status, rel = api('PATCH', f"/repos/{cfg['repo']}/releases/{rel['id']}", cfg, fields)
        action = 'updated'
    else:
        status, rel = api('POST', f"/repos/{cfg['repo']}/releases", cfg, {'tag_name': tag} | fields)
        action = 'created'
    if status not in (200, 201):
        raise RuntimeError(f'릴리스 {action} 실패({status}): {rel.get("message")}')
    return action, rel.get('html_url')


def push_site(cfg, site=None):
    """Commit the Pages site folder (landing build + reports) and push it ('pushed'; an unchanged site pushes nothing new)."""
    import report_pages as rp
    site = rp.prepare_repo(cfg, site)
    rp.git(['add', '-A'], site)
    if rp.git(['status', '--porcelain'], site).strip():
        rp.git(['commit', '-q', '-m', 'landing deploy'], site)
    rp.git(['push', '-q', 'origin', f"HEAD:{cfg['branch']}"], site, cfg)
    return 'pushed'


def publish(dry_run=False):
    import report_pages as rp
    problems = check()
    if problems:
        raise SystemExit('릴리스할 수 없습니다:\n- ' + '\n- '.join(problems))
    ver, cfg = version(), rp.settings()
    if cfg['missing']:
        raise SystemExit(f"설정이 없습니다: {', '.join(cfg['missing'])}")
    title, body = note_parts(note_path(ver))
    tree, dropped = snapshot_tree()
    top = sorted({p.split('/')[0] for p in dropped})
    print(f'공개 스냅숏 tree {tree[:12]} · 뺀 파일 {len(dropped)}개({", ".join(top) or "없음"})')
    if dry_run:
        return
    parent = public_head(cfg)
    local = _git(['rev-parse', '--short', 'HEAD']).strip()
    message = f'release: v{ver}\n\n{title}\n\nPublic snapshot of the private working branch ({local}).\n' \
              f'Left out: internal docs, {", ".join(PRIVATE_PATHS)}.\nBased on HKUDS/Vibe-Trading (MIT).\n\n{TRAILER}\n'
    sha = snapshot_commit(tree, parent, message)
    if sha != parent:
        _git(['push', '-q', f"https://github.com/{cfg['repo']}.git", f'{sha}:refs/heads/{PUBLIC_BRANCH}'], cfg=cfg)
        print(f'main 푸시: {sha[:7]}')
    else:
        print(f'main 변경 없음: {sha[:7]}')
    print('태그', f'v{ver}', ensure_tag(cfg, f'v{ver}', sha, f'{title}\n'))
    action, url = ensure_release(cfg, f'v{ver}', title, body)
    print('릴리스', action, url)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', action='store_true')
    group.add_argument('--dry-run', action='store_true')
    group.add_argument('--publish', action='store_true')
    group.add_argument('--push-site', action='store_true')
    args = parser.parse_args(argv)
    if args.check:
        problems = check()
        print('릴리스 준비됨' if not problems else '\n'.join(f'- {p}' for p in problems))
        return 1 if problems else 0
    if args.push_site:
        import report_pages as rp
        print(push_site(rp.settings()))
        return 0
    publish(dry_run=args.dry_run)
    return 0


if __name__ == '__main__':
    sys.exit(main())
