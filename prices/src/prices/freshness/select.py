"""Select the voice models due for a freshness check.

Reads the provider YAMLs (not data.json: prices_checked is excluded from the
built JSON), and returns the voice models that have a pricing_source_url and are
stale per their provider's staleness_threshold_days.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from prices.prices_types import ModelInfo, ModelPrice
from prices.update import get_providers_yaml

from .models import VOICE_FIELD_UNITS, WorkItem


def _model_price(model: ModelInfo) -> ModelPrice | None:
    """The flat ModelPrice for a model (the base block if conditional)."""
    prices = model.prices
    if isinstance(prices, ModelPrice):
        return prices
    # list[ConditionalPrice]: use the first (base) block.
    if prices:
        return prices[0].prices
    return None


def _voice_field(price: ModelPrice) -> tuple[str, Decimal] | None:
    """Return the (field, rate) for the voice priced field set on this price, if any."""
    for field in VOICE_FIELD_UNITS:
        value = getattr(price, field, None)
        if isinstance(value, Decimal):
            return field, value
    return None


def is_stale(model: ModelInfo, threshold_days: int, today: date) -> bool:
    if model.prices_checked is None:
        return True
    return model.prices_checked < today - timedelta(days=threshold_days)


def select_stale(today: date, *, all: bool = False) -> list[WorkItem]:
    """Voice models with a pricing_source_url that are stale (or all of them if `all`).

    `today` is passed in explicitly so the selection is deterministic and testable.
    """
    items: list[WorkItem] = []
    for provider_yml in get_providers_yaml().values():
        provider = provider_yml.provider
        for model in provider.models:
            if model.pricing_source_url is None:
                continue
            price = _model_price(model)
            if price is None:
                continue
            voice = _voice_field(price)
            if voice is None:
                continue
            if not all and not is_stale(model, provider.staleness_threshold_days, today):
                continue
            field, rate = voice
            items.append(
                WorkItem(
                    provider_id=provider.id,
                    model_id=model.id,
                    url=str(model.pricing_source_url),
                    field=field,
                    current_rate=rate,
                )
            )
    return items
