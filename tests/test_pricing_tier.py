"""Every provider with a priced voice model must name the vendor plan its rates came from.

A rate is not comparable with the rest of the catalog unless you know which plan produced it.
Most vendors publish several for the same model: Deepgram quotes Pay As You Go and Growth,
Cartesia sells four subscription tiers, Telnyx has pay-as-you-go and contracted volume pricing.
Mixing them silently makes the catalog incomparable with itself, and leaves a consumer unable to
tell whether a row matches the plan they are on.

The policy is one tier per provider: the cheapest a new account can reach with no spend
commitment. `check_contribution` enforces it on anything a PR adds or reprices; this pins the
catalog as a whole, so a provider that predates the rule cannot quietly stay unnamed.
"""

from __future__ import annotations

import pytest

from voice_prices.data import providers
from voice_prices.types import Provider

#: Rates billed per second of audio, per character, or per bundled minute. Token-priced LLM rows
#: are out of scope: they are cross-checked against aggregators rather than read off one plan.
VOICE_RATE_FIELDS = ('input_audio_kseconds', 'input_kchars', 'output_audio_kseconds', 'agent_kminutes')

#: The one value that means "there was no plan to choose between", as opposed to nobody looking.
SINGLE_RATE = 'Single published rate'


def voice_providers() -> list[Provider]:
    out: list[Provider] = []
    for provider in providers:
        for model in provider.models:
            prices = model.prices
            if any(getattr(prices, field, None) is not None for field in VOICE_RATE_FIELDS):
                out.append(provider)
                break
    return out


def test_there_are_voice_providers_to_check():
    """Guard the guard: if the field names drift, this must fail rather than pass on zero rows."""
    assert len(voice_providers()) >= 10


@pytest.mark.parametrize('provider', voice_providers(), ids=lambda p: p.id)
def test_voice_provider_names_its_pricing_tier(provider: Provider):
    tier = provider.pricing_tier
    assert tier, (
        f'{provider.id} prices voice models but does not say which vendor plan the rates came from. '
        'Set `pricing_tier` to the cheapest tier a new account can reach with no spend commitment, '
        f'worded as the vendor words it, or {SINGLE_RATE!r} if there is no plan to choose between.'
    )
    assert tier == tier.strip(), f'{provider.id} pricing_tier has surrounding whitespace: {tier!r}'


@pytest.mark.parametrize('provider', voice_providers(), ids=lambda p: p.id)
def test_pricing_tier_is_not_a_committed_plan(provider: Provider):
    """The policy names the entry tier. A committed plan understates every self-serve user.

    Deepgram's Growth is ~12.5% cheaper but costs $4,000/year to enter. `livekit-scale` is the
    deliberate exception: it exists precisely to publish the committed-tier rates *alongside*
    the standard ones, as a separate provider, so both are available rather than blended.
    """
    if provider.id == 'livekit-scale':
        return
    assert 'enterprise' not in (provider.pricing_tier or '').lower()
