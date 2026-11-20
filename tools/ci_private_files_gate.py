#!/usr/bin/env python3
"""Reject internal documents in the Git index, including force-added files.

Run before committing with ``python tools/ci_private_files_gate.py``.
Only indexed paths are inspected; ignored local files and their contents are
never read. The same index check runs on CI's checked-out commit.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path, PurePosixPath


PRIVATE_DOCUMENT_NAMES = frozenset({
    "agents.md",
    "agents.override.md",
    "claude.md",
    "claude.local.md",
    "memory.md",
    "codex_memory.md",
    "notes.md",
})

# The published user guides under the root docs/ (README.md links them). Every other docs/ path stays internal,
# so a plan or session note dropped next to them still fails; add a guide here deliberately when publishing it.
PUBLIC_DOCS = frozenset({
    "README.md", "getting-started.md", "architecture.md", "strategy.md", "dashboard.md",
    "configuration.md", "operations.md", "safety.md", "faq.md", "nxt.md",
    # guides added with the README rewrite (2026-11-19)
    "oversold.md", "live-accounts.md", "longterm.md", "longterm-dca.md", "yeo-method.md", "journal.md",
    "premarket.md", "report-pages.md", "automation.md", "notifications.md", "portability.md",
    # AI agent guide (2026-11-19)
    "ai-agents.md", "ai-search.md",
    # external services and webhooks (2026-11-19)
    "integrations.md",
})
PUBLIC_DOC_IMAGES = frozenset({".jpg", ".png", ".svg"})


def _public_doc(path: PurePosixPath) -> bool:
    """A published guide (docs/<name>.md in PUBLIC_DOCS) or an image under docs/images/."""
    if path.parts[0] != "docs" or len(path.parts) < 2:
        return False
    if len(path.parts) == 2:
        return path.name in PUBLIC_DOCS
    if path.parts[1] == "vibe-trading" and len(path.parts) == 3:
        return path.name in {"README_upstream.md", "README_zh.md", "README_ja.md", "README_ko.md",
                             "README_ar.md", "README_es.md", "README_id.md"}
    return path.parts[1] == "images" and path.suffix.lower() in PUBLIC_DOC_IMAGES


def main(repo_root: Path | None = None) -> int:
    """Check the Git index for internal document paths.

    Args:
        repo_root: Repository to check; defaults to this script's repository.

    Returns:
        Zero for a clean index, one for forbidden paths, or two if Git fails.
    """
    root = repo_root if repo_root is not None else Path(__file__).resolve().parent.parent
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--full-name", "-z"],
            cwd=root,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        print("FAIL: cannot read the Git index; internal-document check did not run.")
        return 2

    forbidden = []
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        name = os.fsdecode(raw_path)
        path = PurePosixPath(name)
        if (path.parts[0].casefold() == "docs" and not _public_doc(path)) or path.name.casefold() in PRIVATE_DOCUMENT_NAMES:
            forbidden.append(name)

    if forbidden:
        print("FAIL: internal documents are tracked in Git:")
        for name in sorted(set(forbidden)):
            # Escape control characters so a filename cannot inject CI output.
            print(f"  {ascii(name)}")
        print("Use git rm --cached -- <path> to retain the local file without tracking it.")
        print("Publish project documentation under wiki/docs/ or the relevant source directory.")
        return 1

    print("OK: no internal documents are tracked in Git.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
