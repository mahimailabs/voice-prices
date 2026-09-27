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
    """The committed prices/data.json, which every generated file is built from."""
    return cast('list[dict[str, Any]]', json.loads(DATA_JSON.read_text()))


def test_committed_site_data_is_in_sync():
    """The drift gate for the site: its data file must match what `make build` would write."""
    assert SITE_DATA.read_text() == render_site_data(_data()), 'site/src/data/catalog.json is stale: run `make build`'


def test_build_site_writes_and_is_idempotent(tmp_path: Path):
    """A second build over an unchanged catalog writes the same bytes."""
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
    """Each provider page's YAML link points at a file that exists."""
    site = json.loads(SITE_DATA.read_text())
    for category in site['categories'].values():
        for provider in category['providers']:
            assert provider['yaml'], provider['id']
            assert (root_dir / 'prices' / 'providers' / provider['yaml']).exists()


def test_site_warning_makes_every_claim():
    """The site's warning keeps every claim the README and docs disclaimer make."""
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


def test_readme_gateway_numbers_are_generated_and_in_sync():
    """The README's direct-vs-LiveKit paragraph comes from the data, not from a hand edit."""
    import re

    from prices.inject_providers import gateway_block

    readme = (root_dir / 'README.md').read_text()
    m = re.search(r'(\[comment\]: +<> +\(gateway-start\)).+(\[comment\]: +<> +\(gateway-end\))', readme, re.DOTALL)
    assert m, 'README.md lost its gateway-start/gateway-end markers'
    assert m.group(0) == gateway_block(m), 'the README gateway numbers are stale: run `make build`'


def test_gateway_summary_counts_each_model_once():
    """Pinned variants count once, and the site reads the same numbers the README prints."""
    from prices.build_docs import build_comparison, distinct_rows, gateway_summary

    comparison = build_comparison(_data())
    summary = gateway_summary(comparison)
    rows = [row for category in CATEGORIES for row in distinct_rows(comparison[category])]
    assert not any('@' in row['id'] and row['id'].split('@')[0] in {r['id'] for r in rows} for row in rows)
    assert summary['compared'] == sum(1 for row in rows if row['delta'] is not None)
    assert 0 < summary['at_or_below'] <= summary['compared']
    assert len(summary['examples']) >= 3, 'most README example models no longer resolve'
    site = json.loads(SITE_DATA.read_text())
    assert site['gatewaySummary']['compared'] == summary['compared']
    assert site['gatewaySummary']['atOrBelow'] == summary['at_or_below']


def test_streaming_livekit_models_compare_against_streaming_rates():
    """LiveKit's Universal-3.5 Pro is the realtime tier; comparing it to the async rate invented a +114% markup."""
    from prices.build_docs import build_comparison

    rows = {row['id']: row for row in build_comparison(_data())['stt']}
    assert rows['assemblyai/universal-3-5-pro']['delta'] == 0
    # Multilingual Flux has no direct rate yet; English Flux is not a stand-in for it.
    assert rows['deepgram/flux-general-multi']['direct'] is None
