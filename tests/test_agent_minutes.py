"""Tests for `agent_kminutes`, the bundled voice-agent session rate.

The field exists because roughly 67 platforms (Vapi, Retell, Bland, ElevenLabs Agents,
Deepgram Voice Agent and similar) sell one blended per-minute price covering STT, LLM,
TTS and orchestration together. That price is real, public and comparable between those
platforms, and is not decomposable into the component fields the rest of the catalog uses.

The design decision worth protecting here: a bundled minute is charged to neither input
nor output. Attributing it to a direction would invent a split the vendor never published.
"""

from __future__ import annotations

import json
import pathlib
from decimal import Decimal
from typing import Any, cast

import pytest

from voice_prices import Usage, calc_price
from voice_prices.types import ModelPrice


def price(**kwargs: Decimal) -> ModelPrice:
    # kwargs are always priced Decimal fields here; voice_multipliers is never passed.
    return ModelPrice(**kwargs)  # pyright: ignore[reportArgumentType]


def test_agent_minutes_price_correctly():
    calc = price(agent_kminutes=Decimal(50)).calc_price(Usage(agent_minutes=Decimal(10)))
    assert calc['total_price'] == Decimal('0.5')  # 10 min at $0.05/min


def test_agent_charge_is_neither_input_nor_output():
    """A bundled minute prices orchestration plus undisclosed components. Splitting it
    across input and output would invent proportions the vendor never published."""
    calc = price(agent_kminutes=Decimal(50)).calc_price(Usage(agent_minutes=Decimal(10)))
    assert calc['input_price'] == 0
    assert calc['output_price'] == 0
    assert calc['total_price'] == Decimal('0.5')


def test_fractional_minutes():
    """Platforms bill per second and round at the end, so sub-minute precision must survive."""
    calc = price(agent_kminutes=Decimal(50)).calc_price(Usage(agent_minutes=Decimal('1.5')))
    assert calc['total_price'] == Decimal('0.075')


def test_zero_minutes_costs_nothing():
    calc = price(agent_kminutes=Decimal(50)).calc_price(Usage(agent_minutes=Decimal(0)))
    assert calc['total_price'] == 0


def test_unset_agent_minutes_costs_nothing():
    calc = price(agent_kminutes=Decimal(50)).calc_price(Usage(input_tokens=1000))
    assert calc['breakdown'].agent_kminutes == 0


def test_agent_minutes_ignored_when_model_has_no_agent_rate():
    """Passing agent_minutes to a model that does not sell them must not invent a charge."""
    calc = price(input_mtok=Decimal(1)).calc_price(Usage(agent_minutes=Decimal(10)))
    assert calc['total_price'] == 0


def test_breakdown_sum_invariant_holds():
    calc = price(agent_kminutes=Decimal(50), input_mtok=Decimal(2)).calc_price(
        Usage(agent_minutes=Decimal(4), input_tokens=1_000_000)
    )
    assert calc['breakdown'].sum() == calc['total_price']
    assert calc['total_price'] == Decimal('2.2')  # $2.00 tokens + $0.20 agent


def test_usage_addition_sums_agent_minutes():
    combined = Usage(agent_minutes=Decimal('1.5')) + Usage(agent_minutes=Decimal('2.5'))
    assert combined.agent_minutes == Decimal(4)


def test_usage_addition_preserves_none():
    assert (Usage(input_tokens=1) + Usage(input_tokens=1)).agent_minutes is None


def test_int_agent_minutes_coerces():
    """Mirrors the int-to-Decimal contract the other duration fields already honour."""
    calc = price(agent_kminutes=Decimal(50)).calc_price(Usage(agent_minutes=10))  # pyright: ignore[reportArgumentType]
    assert calc['total_price'] == Decimal('0.5')


def test_vapi_round_trip_through_the_catalog():
    """The one catalog entry, end to end, so the field is proven wired and not just defined."""
    calc = calc_price(Usage(agent_minutes=Decimal(60)), model_ref='vapi-platform')
    assert calc.total_price == Decimal(3)  # 60 min at $0.05/min
    assert calc.input_price == 0
    assert calc.output_price == 0


@pytest.mark.parametrize(
    ('minutes', 'expected'),
    [(Decimal(1), Decimal('0.05')), (Decimal(30), Decimal('1.5')), (Decimal(60), Decimal(3))],
)
def test_vapi_scales_linearly(minutes: Decimal, expected: Decimal):
    assert calc_price(Usage(agent_minutes=minutes), model_ref='vapi-platform').total_price == expected


# ---- docs rendering ----------------------------------------------------------
#
# An agent row carries no audio-second and no character rate, so before this was fixed it got
# `per_min = None`, which `_split_token_priced` reads as "billed per audio token". The row was
# pushed out of the main table and the Agents landing page rendered "No models priced yet" while
# simultaneously reporting one model. The docs build succeeded and every other test passed,
# because nothing asserted what the page actually said.


def test_agent_row_gets_a_per_minute_value():
    """The primary column for the Agents tab must be populated from `agent_kminutes`."""
    from prices.build_docs import build_catalog
    from prices.utils import package_dir

    data = cast('list[dict[str, Any]]', json.loads((package_dir / 'data.json').read_text()))
    rows = build_catalog(data)['agent'][0]['models']
    assert rows, 'no agent models in the catalog'
    assert rows[0]['values']['per_min'] == 0.05


def test_agent_landing_page_lists_the_model():
    """Guards the whole chain: value populated, row classified as main, table rendered."""
    page = (pathlib.Path(__file__).resolve().parents[1] / 'docs' / 'agent' / 'all-models.mdx').read_text()
    assert 'No models priced yet' not in page
    assert '`vapi-platform`' in page
    assert '$0.05' in page
    assert 'bill audio as tokens' not in page, 'agent rows must not be classified as token-priced'
