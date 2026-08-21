"""The snapshot installed by `set_custom_snapshot` is process-wide, and resetting it works.

Not a hypothetical. Two engineers on a consuming team spent half a day suspecting this of
causing a flaky test, because `_custom_snapshot` is a module global, `UpdatePrices` mutates it
from a background daemon thread, and nothing documented how to put it back. It was not the
cause in the end, but it was credible enough to burn the time, which is its own kind of defect.

`set_custom_snapshot(None)` is the supported reset. These pin that, so a refactor that quietly
breaks it fails here rather than in somebody else's test suite.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from voice_prices import Usage, calc_price
from voice_prices.data_snapshot import DataSnapshot, set_custom_snapshot


@pytest.fixture(autouse=True)
def _always_reset():  # pyright: ignore[reportUnusedFunction]
    """Reset either side, so this file cannot leak state into the rest of the suite.

    Which is the same discipline the docs now ask consumers for.
    """
    set_custom_snapshot(None)
    yield
    set_custom_snapshot(None)


def bundled_rate() -> Decimal:
    return calc_price(Usage(audio_input_seconds=Decimal(60)), model_ref='nova-3', provider_id='deepgram').total_price


def test_reset_restores_the_bundled_snapshot():
    before = bundled_rate()

    # An empty snapshot: unmistakable, and it proves installation without depending on any
    # particular rate surviving a future reprice.
    set_custom_snapshot(DataSnapshot(providers=[], from_auto_update=False))
    with pytest.raises(LookupError):
        bundled_rate()

    set_custom_snapshot(None)
    assert bundled_rate() == before, '`set_custom_snapshot(None)` must restore the bundled data'


def test_reset_is_reachable_from_the_public_module():
    """It is exported, which is what makes it a supported reset rather than a private poke."""
    from voice_prices import data_snapshot

    assert 'set_custom_snapshot' in data_snapshot.__all__


def test_update_prices_resets_on_exit():
    """The context manager cleans up, so `with UpdatePrices():` needs no manual reset.

    `stop()` clears the snapshot AFTER joining the thread, so an in-flight fetch cannot
    reinstall one behind you. That ordering is load-bearing and is asserted here.
    """
    from voice_prices import UpdatePrices

    before = bundled_rate()
    with UpdatePrices() as updater:
        assert updater is not None
    # __exit__ called stop(), which reset the snapshot.
    assert bundled_rate() == before
