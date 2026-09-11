"""The correction / removal contact must be identical everywhere it appears.

It is one URL pasted into several hand-written files plus a constant the generated pages read.
If a vendor lands on any one of them, it has to work and it has to be the same place. A stale
copy on one surface is exactly the kind of thing nobody notices until the person it was meant
for is the one who finds it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prices.build_docs import DOCS_DIR, PRICE_DISCLAIMER, REMOVAL_FORM_URL
from prices.utils import root_dir

HAND_WRITTEN = (
    root_dir / 'README.md',
    root_dir / 'packages' / 'python' / 'README.md',
    DOCS_DIR / 'index.mdx',
    DOCS_DIR / 'how-fresh.mdx',
)


def test_constant_is_a_real_https_url():
    assert REMOVAL_FORM_URL.startswith('https://'), REMOVAL_FORM_URL


def test_generated_disclaimer_carries_the_contact():
    assert REMOVAL_FORM_URL in PRICE_DISCLAIMER
    assert '24 hours' in PRICE_DISCLAIMER


@pytest.mark.parametrize('path', HAND_WRITTEN, ids=lambda p: p.name)
def test_hand_written_surface_carries_the_same_contact(path: Path):
    text = path.read_text()
    assert REMOVAL_FORM_URL in text, f'{path.name} does not carry the removal contact, or carries a different URL'
    assert '24 hours' in text, f'{path.name} dropped the 24-hour commitment'


def test_readme_leads_with_it():
    """The README banner has to come before the first section heading, not buried under Warning."""
    text = (root_dir / 'README.md').read_text()
    assert text.index(REMOVAL_FORM_URL) < text.index('## Why voice-prices')
