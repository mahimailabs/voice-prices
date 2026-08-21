"""The LiveKit plugin registry has to stay joinable to the catalog.

The coverage page is only worth reading if the join underneath it is sound, and every way it can
rot is silent: a provider gets renamed and a covered plugin quietly reads as a gap, a hand-edit
drops a `provides` list and the denominator shrinks, a skip reason gets a status nobody renders.
None of that fails a normal build, so it is pinned here instead.
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from prices.build_docs import CATEGORIES
from prices.livekit_coverage import (
    CAPABILITY_TABS,
    OUT_OF_SCOPE,
    REGISTRY,
    STATUS_LABEL,
    build_coverage,
    load_registry,
    render_coverage_page,
)
from prices.utils import package_dir

RAW, PLUGINS = load_registry()
DATA = cast('list[dict[str, Any]]', json.loads((package_dir / 'data.json').read_text()))
COVERAGE = build_coverage(PLUGINS, DATA)

VALID_KINDS = {'inference', 'unknown'} | set(OUT_OF_SCOPE)


def test_registry_is_sorted_and_unique():
    """Sorted by package, matching the house rule for the provider YAMLs, so diffs stay small."""
    packages = [plugin.package for plugin in PLUGINS]
    assert packages == sorted(packages), 'plugins in livekit_plugins.json must be sorted by package'
    assert len(packages) == len(set(packages)), 'duplicate package in livekit_plugins.json'


def test_every_provider_id_resolves():
    """A renamed provider must fail here rather than silently read as an uncovered plugin.

    `build_coverage` raises on an unresolved id, so constructing COVERAGE at import time is the
    assertion. This test names the failure mode so the traceback is legible.
    """
    known = {str(provider.get('id', '')) for provider in DATA}
    unresolved = [p.package for p in PLUGINS if p.provider_id is not None and p.provider_id not in known]
    assert not unresolved, f'registry names providers that are not in the catalog: {unresolved}'


@pytest.mark.parametrize('plugin', PLUGINS, ids=lambda p: p.package)
def test_plugin_entry_is_well_formed(plugin: Any):
    assert plugin.kind in VALID_KINDS, f'{plugin.package}: unknown kind {plugin.kind!r}'

    if plugin.kind in OUT_OF_SCOPE:
        # Out-of-scope plugins are not priced by definition, so a price-shaped field on one means
        # somebody classified it wrong.
        assert not plugin.provides, f'{plugin.package}: out-of-scope plugins must not declare capabilities'
        assert plugin.provider_id is None, f'{plugin.package}: out-of-scope plugins must not name a provider'
        return

    if plugin.kind == 'inference':
        assert plugin.provides, f'{plugin.package}: an inference plugin must declare what it provides'
    for capability in plugin.provides:
        assert capability in CAPABILITY_TABS, f'{plugin.package}: unknown capability {capability!r}'

    # `provider_id` and `status` answer the same question and must not both be set: one says the
    # vendor is in the catalog, the other says why it is not.
    if plugin.provider_id is not None:
        assert plugin.status is None, f'{plugin.package}: has a provider_id, so it needs no status'
    elif plugin.status is not None:
        assert plugin.status in STATUS_LABEL, f'{plugin.package}: unrenderable status {plugin.status!r}'


def test_capability_tabs_are_real_categories():
    """Every capability maps onto docs tabs that actually exist."""
    for capability, tabs in CAPABILITY_TABS.items():
        unknown = set(tabs) - set(CATEGORIES)
        assert not unknown, f'capability {capability!r} maps to non-existent tab(s) {unknown}'


def test_coverage_is_derived_not_recorded():
    """A plugin counts as priced only when the catalog actually prices that modality.

    This is the guard against the mistake the slot model exists to prevent: a vendor priced for
    the wrong thing reading as covered. `livekit-plugins-azure` is the live example, priced for
    LLM while the plugin calls Azure Speech.
    """
    for c in COVERAGE:
        for capability in c.priced:
            assert set(CAPABILITY_TABS[capability]) & set(c.modalities), (
                f'{c.plugin.package}: {capability} counted as priced but the catalog has no such tab for '
                f'{c.plugin.provider_id}'
            )
        for capability in c.unpriced:
            assert not (set(CAPABILITY_TABS[capability]) & set(c.modalities)), (
                f'{c.plugin.package}: {capability} counted as unpriced but the catalog prices it'
            )
        assert sorted((*c.priced, *c.unpriced)) == sorted(c.plugin.provides), (
            f'{c.plugin.package}: priced + unpriced must partition what the plugin provides'
        )


def test_page_is_up_to_date():
    """The committed page matches what the current registry and catalog produce."""
    from prices.livekit_coverage import COVERAGE_PAGE

    expected = render_coverage_page(COVERAGE, str(RAW.get('checked', '')))
    assert COVERAGE_PAGE.read_text() == expected, 'docs/livekit-coverage.mdx is stale; run `make livekit-coverage`'


def test_registry_records_when_it_was_checked():
    checked = RAW.get('checked')
    assert isinstance(checked, str) and checked, f'{REGISTRY.name} must record when the plugin list was read'
    assert RAW.get('source_url'), f'{REGISTRY.name} must record where the plugin list came from'
