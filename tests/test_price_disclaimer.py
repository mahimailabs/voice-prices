"""Every page that shows a rate has to carry the reference-only disclaimer.

The README, the PyPI page and /how-fresh have said this for a long time, but none of them is
where a reader lands: a search for "deepgram tts pricing" opens /tts/deepgram, which showed a
number and no caveat at all. The disclaimer now renders from one constant in `build_docs`, and
this pins it, because the failure mode is silent. A page that quietly loses it still builds,
still parses, and still looks right.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prices.build_docs import CATEGORIES, DOCS_DIR, PRICE_DISCLAIMER

#: Hand-written pages that show or discuss real rates and so need the caveat too. The generated
#: category pages are covered by walking the directories instead.
HAND_WRITTEN = ('index.mdx', 'how-fresh.mdx', 'livekit-coverage.mdx')

#: Phrases the disclaimer must actually make, spelled out so a reword that drops one fails here
#: rather than passing because the `<Warning>` tags survived.
REQUIRED_CLAIMS = (
    'not published by, affiliated with, or endorsed by',
    'out of date',
    "vendor's own pricing page",
    'LICENSE',
)

GENERATED_PAGES = sorted(path for category in CATEGORIES for path in (DOCS_DIR / category).glob('*.mdx'))


def test_generated_pages_exist():
    """Guards the walk above: an empty glob would make every parametrised test vacuous."""
    assert len(GENERATED_PAGES) > 50, f'expected the full set of generated pages, found {len(GENERATED_PAGES)}'


def _page_id(page: Path) -> str:
    return f'{page.parent.name}/{page.name}'


@pytest.mark.parametrize('page', GENERATED_PAGES, ids=_page_id)
def test_generated_page_carries_disclaimer(page: Path):
    assert PRICE_DISCLAIMER in page.read_text(), (
        f'{_page_id(page)} shows rates without the reference-only disclaimer; re-run `make build-docs`'
    )


@pytest.mark.parametrize('name', HAND_WRITTEN)
def test_hand_written_page_carries_disclaimer(name: str):
    text = (DOCS_DIR / name).read_text()
    assert '<Warning>' in text, f'{name} has no <Warning> block'
    missing = [claim for claim in REQUIRED_CLAIMS if claim not in text]
    assert not missing, f'{name} disclaimer is missing: {missing}'


def test_disclaimer_makes_every_required_claim():
    """The rendered constant itself, not just the pages built from it."""
    missing = [claim for claim in REQUIRED_CLAIMS if claim not in PRICE_DISCLAIMER]
    assert not missing, f'PRICE_DISCLAIMER no longer says: {missing}'
    assert PRICE_DISCLAIMER.startswith('<Warning>') and PRICE_DISCLAIMER.endswith('</Warning>')
