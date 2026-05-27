"""Validators on the Pydantic schema in prices.prices_types.

Each test exercises one rule pinned in the TTS pricing design doc Section 2.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

import pytest
from pydantic import HttpUrl, ValidationError

from prices.prices_types import (
    ClauseEquals,
    ModelInfo,
    ModelPrice,
    Provider,
    Tier,
    TieredPrices,
)


def _provider(**kwargs: Any) -> Provider:
    defaults: dict[str, Any] = {'id': 'p', 'name': 'P', 'api_pattern': r'https://p\.example', 'models': []}
    defaults.update(kwargs)
    return Provider(**defaults)


def test_voice_multipliers_require_default():
    """A voice_multipliers block must include a `default` key.

    Section 2: `_require_default_key` validator on VoiceMultipliers.
    """
    with pytest.raises(ValidationError) as exc:
        ModelPrice(
            input_kchars=Decimal('0.18'),
            voice_multipliers={'premium': Decimal('1.5')},
        )
    assert 'default' in str(exc.value)


def test_voice_multiplier_values_positive():
    """Each multiplier value must be > 0.

    Section 2: Gt(0) constraint on VoiceMultiplier.
    """
    for bad_value in (Decimal('0'), Decimal('-0.5')):
        with pytest.raises(ValidationError):
            ModelPrice(
                input_kchars=Decimal('0.18'),
                voice_multipliers={'default': Decimal('1.0'), 'broken': bad_value},
            )


def test_no_tiers_on_char_or_sec_fields():
    """`TieredPrices` is rejected on `input_kchars` and `output_audio_kseconds`.

    Section 2: v0.1 scope. Character and audio-second tiers wait for a real provider need.
    Enforced by the type annotation (`DollarPrice | None`, no TieredPrices union member).
    """
    tier = TieredPrices(base=Decimal('0.18'), tiers=[Tier(start=1000, price=Decimal('0.15'))])
    with pytest.raises(ValidationError):
        ModelPrice(input_kchars=cast(Any, tier))
    with pytest.raises(ValidationError):
        ModelPrice(output_audio_kseconds=cast(Any, tier))


def test_voice_multipliers_require_scalable_field():
    """`voice_multipliers` is rejected if the model has no field for it to scale.

    Section 2: validator at the model level. Multipliers with nothing to scale are a bug.
    """
    # No input_kchars / output_audio_kseconds / input_audio_mtok / output_audio_mtok.
    with pytest.raises(ValidationError) as exc:
        ModelPrice(
            input_mtok=Decimal('1.0'),  # token field; not a scalable field for multipliers
            voice_multipliers={'default': Decimal('1.0')},
        )
    assert 'voice_multipliers requires at least one scalable priced field' in str(exc.value)


def test_voice_multipliers_accept_audio_token_scalable_field():
    """Sanity-check: input_audio_mtok / output_audio_mtok count as scalable fields."""
    # Should validate cleanly with just audio-token field set
    ModelPrice(input_audio_mtok=Decimal('40'), voice_multipliers={'default': Decimal('1.0')})
    ModelPrice(output_audio_mtok=Decimal('80'), voice_multipliers={'default': Decimal('1.0')})


def test_staleness_threshold_defaults():
    """`Provider.staleness_threshold_days` defaults to 60 when unset.

    Section 2: int = 60.
    """
    p = _provider()
    assert p.staleness_threshold_days == 60

    # Explicit override is respected.
    p2 = _provider(staleness_threshold_days=30)
    assert p2.staleness_threshold_days == 30


def test_pricing_source_url_validates_as_url():
    """`ModelInfo.pricing_source_url` is HttpUrl; bare strings that are not URLs are rejected.

    Pydantic accepts str at runtime and coerces to HttpUrl; we use `cast(HttpUrl, ...)`
    on the literal strings to satisfy strict type checking while exercising the runtime
    behavior the test cares about.
    """
    # Valid URL accepted.
    m = ModelInfo(
        id='t',
        match=ClauseEquals(equals='t'),
        prices=ModelPrice(input_kchars=Decimal('0.18')),
        pricing_source_url=cast(HttpUrl, 'https://example.com/pricing#model'),
    )
    assert str(m.pricing_source_url).startswith('https://example.com/pricing')

    # Garbage rejected.
    with pytest.raises(ValidationError):
        ModelInfo(
            id='t',
            match=ClauseEquals(equals='t'),
            prices=ModelPrice(input_kchars=Decimal('0.18')),
            pricing_source_url=cast(HttpUrl, 'not-a-url'),
        )
