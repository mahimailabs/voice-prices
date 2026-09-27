"""The prices.mahimai.ca data file and its warnings.

`site/src/data/catalog.json` is committed so the site builds with Node alone. Like the docs pages it
must be a pure function of data.json, or the site would show a number the library no longer agrees
with. And like the docs pages, every page on the site that shows a number must say it may be wrong.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from prices.build_docs import CATEGORIES, DATA_JSON, REMOVAL_FORM_URL, build_catalog
from prices.build_site import SITE_DATA, build_site, render_site_data
from prices.utils import root_dir

SITE = root_dir / 'site' / 'src'


def _data() -> list[dict[str, Any]]:
    return cast('list[dict[str, Any]]', json.loads(DATA_JSON.read_text()))


def test_committed_site_data_is_in_sync():
    assert SITE_DATA.read_text() == render_site_data(_data()), 'site/src/data/catalog.json is stale: run `make build`'


def test_build_site_writes_and_is_idempotent(tmp_path: Path):
    path = tmp_path / 'catalog.json'
    build_site(path)
    first = path.read_text()
    build_site(path)
    assert path.read_text() == first


def test_site_data_matches_the_docs_catalog():
    """Same tabs, same providers, same models, same display rates as the Mintlify pages."""
    data = _data()
    site = json.loads(render_site_data(data))
    catalog = build_catalog(data)
    for category in CATEGORIES:
        site_providers = site['categories'][category]['providers']
        assert [p['id'] for p in site_providers] == [e['id'] for e in catalog[category]]
        for site_provider, entry in zip(site_providers, catalog[category]):
            assert [m['id'] for m in site_provider['models']] == [m['id'] for m in entry['models']]
            for site_model, row in zip(site_provider['models'], entry['models']):
                assert site_model['values'] == {k: v for k, v in row['values'].items() if v is not None}


def test_site_data_has_no_clock():
    """No build date: a file that changed with the clock would turn unrelated PRs red."""
    text = SITE_DATA.read_text()
    assert 'built' not in json.loads(text)
    assert json.loads(text)['removalForm'] == REMOVAL_FORM_URL


def test_every_provider_links_to_its_yaml():
    site = json.loads(SITE_DATA.read_text())
    for category in site['categories'].values():
        for provider in category['providers']:
            assert provider['yaml'], provider['id']
            assert (root_dir / 'prices' / 'providers' / provider['yaml']).exists()


def test_site_warning_makes_every_claim():
    caveat = (SITE / 'components' / 'Caveat.astro').read_text()
    for claim in (
        'may be inaccurate',
        'not published by, affiliated with, or endorsed by',
        'out of date',
        "vendor's own pricing page",
        'LICENSE',
        'within 24 hours',
        'REMOVAL_FORM',
    ):
        assert claim in caveat, claim


def test_every_page_that_shows_a_rate_carries_the_warning():
    """The layout's footer carries the full warning on every page; the pages that show rates add
    their own next to the numbers, and the estimate says it is one."""
    assert '<Caveat variant="full" />' in (SITE / 'components' / 'Layout.astro').read_text()
    for page in ('pages/index.astro', 'components/CategoryPage.astro', 'components/ProviderPage.astro'):
        assert '<Caveat' in (SITE / page).read_text(), page
    estimator = (SITE / 'components' / 'Estimator.tsx').read_text()
    assert 'Estimate' in estimator and 'not a quote' in estimator
