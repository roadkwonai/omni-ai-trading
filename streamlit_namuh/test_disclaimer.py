"""The investment disclaimer reads the same everywhere it is shown (user 2026-11-19)."""
import json
import re
from pathlib import Path

import disclaimer as D

ROOT = Path(__file__).resolve().parent.parent


def _flat(text):
    return re.sub(r'\s+', ' ', text)


def test_readme_carries_the_full_notice_and_paid_ai_note():
    readme = _flat((ROOT / 'README.md').read_text())
    assert '## 투자 유의사항 및 면책 고지' in readme
    for item in D.FULL:
        assert item in readme
    assert D.AI_PAID in readme and D.SHORT in readme


def test_landing_uses_the_same_wording():
    content = (ROOT / 'landing/src/content.ts').read_text()
    assert f'export const DISCLAIMER_SHORT = {json.dumps(D.SHORT, ensure_ascii=False)}' in content
    assert f'export const AI_PAID = {json.dumps(D.AI_PAID, ensure_ascii=False)}' in content
    for item in D.FULL:
        assert json.dumps(item, ensure_ascii=False) in content
    app = (ROOT / 'landing/src/App.tsx').read_text()
    assert 'id="disclaimer"' in app and 'href="#disclaimer"' in app


def test_every_report_page_shows_the_short_notice():
    base = (ROOT / 'scripts/report_templates/_base.html').read_text()
    assert D.SHORT in base and 'disclaimer-bar' in base


def test_full_markdown_numbers_every_item():
    text = D.full_markdown()
    assert all(f'{i}. {item}' in text for i, item in enumerate(D.FULL, 1))
    assert D.AI_PAID in text
