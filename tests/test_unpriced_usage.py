"""Tests for `PriceCalculation.unpriced_usage`, the signal that a zero is not a real zero.

A model can resolve, carry prices, and still return `Decimal('0')` when the caller measures
in a unit the vendor does not bill in. OpenAI prices its audio models per token; a voice
runtime measures seconds of audio and characters of speech. Nothing bridges the two, so the
calculation completes with every breakdown field at zero and reports success. That is worse
than a `LookupError`: it is a wrong number that looks like a right one, and downstream it is
indistinguishable from a genuinely free call.

`unpriced_usage` names the `Usage` fields that found no meter, which is the only way a
caller can tell the two apart.

The design decision worth protecting here is the one in `_UNPRICED_RATE_CHAINS`: a field is
unpriced only when every rate that could pay for it is absent, NOT when its own matching
rate is absent. `cache_read_tokens` on a model with no `cache_read_mtok` is priced at
`input_mtok` by the bucket logic, and must not be reported. 250 catalog models have no cache
rate, so getting this wrong turns the field into noise.
"""

from __future__ import annotations

from decimal import Decimal

from voice_prices import Usage, calc_price
from voice_prices.types import ModelPrice


def price(**kwargs: Decimal) -> ModelPrice:
    # kwargs are always priced Decimal fields here; voice_multipliers is never passed.
    return ModelPrice(**kwargs)  # pyright: ignore[reportArgumentType]


def test_quiet_when_everything_supplied_was_priced():
    calc = price(input_mtok=Decimal(1), output_mtok=Decimal(2)).calc_price(Usage(input_tokens=1000, output_tokens=1000))
    assert calc['unpriced_usage'] == ()


def test_seconds_against_a_token_only_model():
    """The case from the coverage report: a token-priced STT model billed at zero."""
    calc = price(input_mtok=Decimal('2.5'), input_audio_mtok=Decimal(6), output_mtok=Decimal(10)).calc_price(
        Usage(audio_input_seconds=Decimal(60))
    )
    assert calc['total_price'] == 0
    assert calc['unpriced_usage'] == ('audio_input_seconds',)


def test_characters_against_a_token_only_model():
    calc = price(input_mtok=Decimal('0.6'), output_audio_mtok=Decimal(12)).calc_price(Usage(characters=1000))
    assert calc['total_price'] == 0
    assert calc['unpriced_usage'] == ('characters',)


def test_cache_tokens_priced_by_the_parent_rate_are_not_reported():
    """The false positive this field has to avoid.

    With no `cache_read_mtok`, the bucket logic leaves cached tokens inside the text-input
    remainder and prices them at `input_mtok`. They are paid for, so reporting them would be
    wrong, and would fire on the 250 catalog models that carry no cache rate.
    """
    calc = price(input_mtok=Decimal(1)).calc_price(
        Usage(input_tokens=1000, cache_read_tokens=400, cache_write_tokens=100)
    )
    assert calc['total_price'] == Decimal('0.001')
    assert calc['unpriced_usage'] == ()


def test_audio_tokens_priced_by_the_parent_rate_are_not_reported():
    """Same fallback, one field over: no `input_audio_mtok` means audio tokens bill at `input_mtok`."""
    calc = price(input_mtok=Decimal(1)).calc_price(Usage(input_tokens=1000, input_audio_tokens=900))
    assert calc['unpriced_usage'] == ()


def test_cache_audio_read_falls_back_two_levels():
    calc = price(input_mtok=Decimal(1)).calc_price(
        Usage(input_tokens=1000, input_audio_tokens=900, cache_audio_read_tokens=500)
    )
    assert calc['unpriced_usage'] == ()


def test_reported_when_the_whole_chain_is_absent():
    """A characters-only TTS model asked to price tokens has no rate anywhere in the chain."""
    calc = price(input_kchars=Decimal('0.015')).calc_price(Usage(input_tokens=1000, cache_read_tokens=400))
    assert calc['total_price'] == 0
    assert calc['unpriced_usage'] == ('input_tokens', 'cache_read_tokens')


def test_zero_usage_is_never_reported():
    """Absent and zero-valued fields are not a gap: nothing was measured, so nothing is owed."""
    calc = price(input_mtok=Decimal(1)).calc_price(
        Usage(input_tokens=1000, characters=0, audio_input_seconds=Decimal(0))
    )
    assert calc['unpriced_usage'] == ()


def test_partial_pricing_flags_only_the_missing_meter():
    """`total_price` is a real number here, just an undercount. The flag is the only clue."""
    calc = price(input_kchars=Decimal('0.015')).calc_price(Usage(characters=1000, audio_output_seconds=Decimal(30)))
    assert calc['total_price'] == Decimal('0.015')
    assert calc['unpriced_usage'] == ('audio_output_seconds',)


def test_agent_minutes_against_a_component_priced_model():
    calc = price(input_audio_kseconds=Decimal('0.1')).calc_price(Usage(agent_minutes=Decimal(10)))
    assert calc['unpriced_usage'] == ('agent_minutes',)


def test_order_is_stable_and_follows_the_chain_table():
    calc = price().calc_price(
        Usage(
            output_tokens=10,
            characters=100,
            input_tokens=10,
            agent_minutes=Decimal(1),
            audio_input_seconds=Decimal(1),
        )
    )
    assert calc['unpriced_usage'] == (
        'input_tokens',
        'output_tokens',
        'characters',
        'audio_input_seconds',
        'agent_minutes',
    )


def test_surfaces_on_the_public_calc_price():
    """The field has to reach the caller, not just the internal dict.

    gpt-4o-mini-tts is the last catalog model priced only in audio tokens: OpenAI publishes no
    per-character figure for it, not even an estimate, so a TTS runtime measuring characters
    still gets a silent zero and this is the only thing that says so.
    """
    calculation = calc_price(Usage(characters=1000), model_ref='gpt-4o-mini-tts', provider_id='openai')
    assert calculation.total_price == 0
    assert calculation.unpriced_usage == ('characters',)
    assert 'unpriced_usage' in repr(calculation)


def test_quiet_once_a_seconds_rate_exists():
    """gpt-4o-transcribe used to bill at zero for seconds. It now carries an estimated rate."""
    calculation = calc_price(
        Usage(audio_input_seconds=Decimal(60)), model_ref='gpt-4o-transcribe', provider_id='openai'
    )
    assert calculation.total_price == Decimal('0.006')
    assert calculation.unpriced_usage == ()
    # ...but the rate is the vendor's estimate, not the meter, and provenance says so.
    assert calculation.model.provenance is not None
    assert calculation.model.provenance.estimated_fields == ['input_audio_kseconds']


def test_real_catalog_model_priced_in_its_own_unit_is_quiet():
    calculation = calc_price(Usage(audio_input_seconds=Decimal(60)), model_ref='whisper-1', provider_id='openai')
    assert calculation.total_price == Decimal('0.006')
    assert calculation.unpriced_usage == ()


def test_default_is_empty_for_hand_built_calculations():
    """The field is additive: existing constructors that predate it still work."""
    calculation = calc_price(Usage(input_tokens=1000), model_ref='gpt-4o', provider_id='openai')
    assert calculation.unpriced_usage == ()
