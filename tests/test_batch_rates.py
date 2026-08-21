"""Guards for `<model>-batch` rows, which is where a specific misreading keeps landing.

Deepgram's pricing page shows a Streaming / Pre-Recorded toggle, and inside the streaming
table each cell reads "Current price $X/min  Regular price $Y/min" because a promotion is
running. Three catalog rows were built by taking that second number, the undiscounted
streaming price, and recording it as the pre-recorded rate:

    flux-general-batch          $0.0077   = Flux English *regular streaming*
    nova-3-batch                $0.0077   = Nova-3 Monolingual *regular streaming*
    nova-3-multilingual-batch   $0.0092   = Nova-3 Multilingual *regular streaming*

The real Pre-Recorded table was on the same page, and the web.archive.org snapshot from
2026-05-31 (the day the rows were written) shows $0.0043 and $0.0052 already there. The
numbers were wrong on the day they were recorded, not stale.

`flux-general-batch` was worse than wrong: Flux is a WebSocket-only conversational model
(`wss://api.deepgram.com/v2/listen`) with exactly two ids, `flux-general-en` and
`flux-general-multi`. There is no batch endpoint, so the row described a product that does
not exist. It was deleted rather than repriced.

The invariant below is what would have caught all three at review time.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from voice_prices import Usage, calc_price
from voice_prices.data import providers


def rate(provider_id: str, model_id: str) -> Decimal:
    return calc_price(Usage(audio_input_seconds=Decimal(3600)), model_ref=model_id, provider_id=provider_id).total_price


def batch_pairs() -> list[tuple[str, str, str]]:
    """Every `<x>-batch` model in the catalog, paired with the `<x>` it discounts."""
    found: list[tuple[str, str, str]] = []
    for provider in providers:
        ids = {model.id for model in provider.models}
        for model_id in sorted(ids):
            if model_id.endswith('-batch') and model_id.removesuffix('-batch') in ids:
                found.append((provider.id, model_id.removesuffix('-batch'), model_id))
    return found


def test_batch_pairs_exist_to_be_checked():
    """If the naming convention changes, the invariant below must not silently pass on zero rows."""
    assert batch_pairs(), 'no `<model>-batch` pairs found; the guard below is checking nothing'


@pytest.mark.parametrize(('provider_id', 'streaming_id', 'batch_id'), batch_pairs())
def test_batch_is_cheaper_than_streaming(provider_id: str, streaming_id: str, batch_id: str):
    """Prerecorded transcription is cheaper than realtime, because it is easier to serve.

    A `-batch` row priced *above* its streaming sibling is the signature of reading an
    undiscounted "Regular price" out of the streaming table. That is exactly how
    nova-3-batch came to hold $0.0077 against a streaming rate of $0.0048.

    Equal is allowed, and does occur: Speechmatics publishes $0.24/hour for both Batch
    Standard and Real-time Standard. Strictly-cheaper would reject that correct pair while
    catching nothing extra, since the defect this guards against makes batch *dearer*.

    If a vendor ever genuinely charges more for batch, update this with the evidence rather
    than deleting it.
    """
    streaming = rate(provider_id, streaming_id)
    batch = rate(provider_id, batch_id)
    assert batch <= streaming, (
        f'{provider_id}/{batch_id} costs ${batch}/hour, more than streaming '
        f'{streaming_id} at ${streaming}/hour. Check it is not the undiscounted streaming rate.'
    )


def test_deepgram_prerecorded_rates_match_the_prerecorded_table():
    """Pinned to Deepgram's Pre-Recorded table, not the Regular column of the streaming one."""
    # $0.0043/min and $0.0052/min, Pay As You Go, verified 2026-08-20.
    assert rate('deepgram', 'nova-3-batch') == Decimal('0.2580012')  # $0.0043/min * 60
    assert rate('deepgram', 'nova-3-multilingual-batch') == Decimal('0.3120012')  # $0.0052/min * 60


def test_flux_has_no_batch_tier():
    """Flux is WebSocket only. A `flux-general-batch` row would be inventing a product.

    Deepgram's Pre-Recorded table lists Nova-3 Monolingual, Nova-3 Multilingual, Whisper Large
    and Custom. No Flux row, because Flux does not run on the prerecorded endpoint at all.
    """
    with pytest.raises(LookupError):
        calc_price(Usage(audio_input_seconds=Decimal(60)), model_ref='flux-general-batch', provider_id='deepgram')

    # The two real Flux ids still resolve, at the streaming rate.
    for model_id in ('flux-general', 'flux-general-en'):
        assert rate('deepgram', model_id) == Decimal('0.3899988')  # $0.0065/min * 60
