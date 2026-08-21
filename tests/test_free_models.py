"""`free` separates "the provider charges nothing" from "nobody has entered a rate".

Both were a ModelPrice with every field None, so they were the same bytes. The only hint was a
`:free` suffix in the model id, which is a naming convention rather than data. A consumer
holding a zero could not tell whether to report it as priced or to refuse it, and those want
opposite handling. Reported from a consuming metering system, which had a real Deepgram call
recorded as costing nothing.

The flag drives two things, and both are pinned here:

  1. `calc_price` reports an empty `unpriced_usage` for a free model, so `unpriced_usage` stays
     the single thing a caller reads to decide whether a zero is trustworthy.
  2. `exclude_free` keys on the flag rather than on "every rate is None", so the slim dataset
     drops genuinely free models and KEEPS unpriced ones. It previously dropped both, which
     made data.json and data_slim.json answer the same lookup differently.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from prices.prices_types import ClauseEquals, ModelInfo, ModelPrice as BuildModelPrice
from voice_prices import Usage, calc_price

DATA = Path(__file__).parent.parent / 'prices'


def test_a_free_model_reports_nothing_unpriced():
    """The zero is the answer, so there is no gap to report."""
    calc = calc_price(Usage(input_tokens=1000), model_ref='moderation', provider_id='openai')
    assert calc.total_price == 0
    assert calc.model.free is True
    assert calc.unpriced_usage == ()


def test_an_unpriced_model_still_reports_the_gap():
    """Same zero, opposite meaning, and the caller can tell."""
    calc = calc_price(Usage(audio_input_seconds=Decimal(60)), model_ref='nova-general', provider_id='deepgram')
    assert calc.total_price == 0
    assert calc.model.free is False
    assert calc.unpriced_usage == ('audio_input_seconds',)


@pytest.mark.parametrize(
    ('provider_id', 'model_ref', 'usage', 'free'),
    [
        ('openai', 'moderation', Usage(input_tokens=1000), True),
        ('telnyx', 'us-tollfree-outbound', Usage(telephony_minutes=Decimal(5)), True),
        ('deepgram', 'nova-general', Usage(audio_input_seconds=Decimal(60)), False),
        ('deepgram', 'whisper-tiny', Usage(audio_input_seconds=Decimal(60)), False),
        ('elevenlabs', 'scribe_v1', Usage(audio_input_seconds=Decimal(60)), False),
        # Deprecated with "No rate was ever recorded here" in its own price_comments: the
        # clearest case of a zero that must not be billed on.
        ('cerebras', 'qwen-3-coder-480b', Usage(input_tokens=1000), False),
    ],
)
def test_every_zero_declares_which_kind_it_is(provider_id: str, model_ref: str, usage: Usage, free: bool):
    calc = calc_price(usage, model_ref=model_ref, provider_id=provider_id)
    assert calc.total_price == 0
    assert calc.model.free is free
    # The invariant a consumer can rely on: exactly one of the two signals fires.
    assert bool(calc.unpriced_usage) is (not free)


def test_free_and_a_rate_is_rejected():
    """Contradictory claims must not be storable."""
    with pytest.raises(ValidationError, match='free'):
        ModelInfo(
            id='x',
            match=ClauseEquals(equals='x'),
            free=True,
            prices=BuildModelPrice(input_mtok=Decimal(1)),
        )


def test_free_with_an_empty_price_block_is_fine():
    model = ModelInfo(id='x', match=ClauseEquals(equals='x'), free=True, prices=BuildModelPrice())
    assert model.free is True


def _ids(path: Path, provider_id: str) -> set[str]:
    for provider in json.loads(path.read_text()):
        if provider['id'] == provider_id:
            return {m['id'] for m in provider['models']}
    return set()


def test_slim_drops_free_models_and_keeps_unpriced_ones():
    """The bug this caused: the two datasets answered the same lookup differently.

    `nova` returned a zero from data.json and raised LookupError from data_slim.json, because
    exclude_free could not tell an unpriced row from a free one.
    """
    full, slim = DATA / 'data.json', DATA / 'data_slim.json'

    # Free: dropped from slim, as intended. Slim is the priced subset.
    assert 'moderation' in _ids(full, 'openai')
    assert 'moderation' not in _ids(slim, 'openai')

    # Unpriced: kept, so a lookup resolves the same way against either dataset.
    for provider_id, model_id in [('deepgram', 'nova'), ('deepgram', 'whisper'), ('elevenlabs', 'scribe_v1')]:
        assert model_id in _ids(full, provider_id)
        assert model_id in _ids(slim, provider_id), f'{provider_id}/{model_id} vanished from the slim dataset'


def test_the_free_suffix_convention_is_now_backed_by_the_field():
    """`:free` in an id was the only signal. Anything carrying it must now say so in data."""
    unmarked: list[str] = []
    for provider in json.loads((DATA / 'data.json').read_text()):
        for model in provider['models']:
            if ':free' in model['id'] and not model.get('free'):
                unmarked.append(f'{provider["id"]}/{model["id"]}')
    assert not unmarked, f'models named `:free` but not flagged free: {unmarked[:5]}'
