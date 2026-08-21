"""Tests for `telephony_kminutes`, the carrier leg of a phone call.

A voice agent reachable on a phone number pays two unrelated bills for the same minute. One is
for understanding and speaking: speech-to-text, the model, text-to-speech, or a bundled agent
minute containing all three. The other is for carrying the audio over the phone network, which
a carrier charges whether or not the agent says a word.

The design decision worth protecting: those are separate fields. Reusing `agent_kminutes` for
both would double-count the same minute on any bundled platform, since a bundled minute already
contains the speech and a carrier minute never does.

The second decision: direction and number type live in the model id, because both change the
rate and not in the same direction. Twilio charges more to receive on toll-free than to dial
out on it, and the reverse on local.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from voice_prices import Usage, calc_price
from voice_prices.types import ModelPrice


def price(**kwargs: Decimal) -> ModelPrice:
    return ModelPrice(**kwargs)  # pyright: ignore[reportArgumentType]


def test_telephony_minutes_price_correctly():
    calc = price(telephony_kminutes=Decimal('8.5')).calc_price(Usage(telephony_minutes=Decimal(10)))
    assert calc['total_price'] == Decimal('0.085')  # 10 min at $0.0085/min


def test_carrier_minutes_are_neither_input_nor_output():
    """A carrier charges for the call leg. Nothing is transcribed and nothing is generated."""
    calc = price(telephony_kminutes=Decimal(14)).calc_price(Usage(telephony_minutes=Decimal(10)))
    assert calc['input_price'] == 0
    assert calc['output_price'] == 0
    assert calc['total_price'] == Decimal('0.14')


def test_a_bundled_agent_minute_and_a_carrier_minute_both_bill():
    """The double-count this field exists to prevent, in the one case it could happen.

    A model that sold both would charge for the platform AND the carrier, because they are
    genuinely two bills. If they shared a field, one of the two would silently vanish.
    """
    calc = price(agent_kminutes=Decimal(50), telephony_kminutes=Decimal('8.5')).calc_price(
        Usage(agent_minutes=Decimal(10), telephony_minutes=Decimal(10))
    )
    assert calc['breakdown'].agent_kminutes == Decimal('0.5')
    assert calc['breakdown'].telephony_kminutes == Decimal('0.085')
    assert calc['total_price'] == Decimal('0.585')


def test_fractional_minutes():
    """Carriers bill per second and round at the end, so sub-minute precision must survive."""
    calc = price(telephony_kminutes=Decimal('8.5')).calc_price(Usage(telephony_minutes=Decimal('2.5')))
    assert calc['total_price'] == Decimal('0.02125')


@pytest.mark.parametrize(
    ('model_id', 'per_minute'),
    [
        ('us-local-inbound', '0.0085'),
        ('us-local-outbound', '0.0140'),
        ('us-tollfree-inbound', '0.0220'),
        ('us-tollfree-outbound', '0.0140'),
        ('us-sip-inbound', '0.0040'),
        ('us-sip-outbound', '0.0040'),
        ('us-client-inbound', '0.0040'),
        ('us-client-outbound', '0.0040'),
    ],
)
def test_twilio_us_rates_round_trip_to_the_published_figure(model_id: str, per_minute: str):
    calc = calc_price(Usage(telephony_minutes=Decimal(1000)), model_ref=model_id, provider_id='twilio')
    assert calc.total_price == Decimal(per_minute) * 1000


def test_toll_free_inverts_the_direction_premium():
    """The asymmetry that makes direction a price rather than a detail.

    Receiving on toll-free costs more than dialling out on it; local is the other way round.
    Assuming inbound is always cheaper gets toll-free wrong by 2.6x.
    """
    minute = Usage(telephony_minutes=Decimal(1))
    local_in = calc_price(minute, model_ref='us-local-inbound', provider_id='twilio').total_price
    local_out = calc_price(minute, model_ref='us-local-outbound', provider_id='twilio').total_price
    free_in = calc_price(minute, model_ref='us-tollfree-inbound', provider_id='twilio').total_price
    free_out = calc_price(minute, model_ref='us-tollfree-outbound', provider_id='twilio').total_price

    assert local_in < local_out, 'local: receiving is the cheaper direction'
    assert free_in > free_out, 'toll-free: receiving is the DEARER direction'
    assert free_in / local_in > 2.5


def test_a_carrier_row_refuses_agent_minutes():
    """Twilio does not sell agent minutes, so it must say so rather than bill zero for them."""
    calc = calc_price(
        Usage(telephony_minutes=Decimal(5), agent_minutes=Decimal(5)),
        model_ref='us-local-inbound',
        provider_id='twilio',
    )
    assert calc.total_price == Decimal('0.0425')  # the carrier leg only
    assert calc.unpriced_usage == ('agent_minutes',)


def test_an_agent_row_refuses_carrier_minutes():
    """And the reverse: a bundled platform's rate does not include the carrier.

    Together with the test above, a caller pricing a real phone call is told they need both
    rows rather than being handed a silent undercount.
    """
    calc = calc_price(
        Usage(agent_minutes=Decimal(5), telephony_minutes=Decimal(5)),
        model_ref='vapi-platform',
        provider_id='vapi',
    )
    assert calc.unpriced_usage == ('telephony_minutes',)
