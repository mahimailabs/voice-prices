"""Maintenance-contract test for PriceBreakdown.

Section 2 of the TTS pricing design doc pins this rule:

  Every Decimal-typed priced field on ModelPrice MUST have a matching Decimal
  field on PriceBreakdown. When ModelPrice gains a new priced field, add the
  corresponding field here in the same PR.

This test walks ModelPrice's dataclass fields at runtime and asserts each
priced field has its counterpart on PriceBreakdown. Drift across an upstream
merge will fail CI rather than silently shipping an underspecified breakdown.
"""

from __future__ import annotations

import dataclasses

from genai_prices.types import ModelPrice, PriceBreakdown

# Mapping from a priced ModelPrice field name to:
#   (main_breakdown_field, optional_multiplier_eligible_adjustment_field_or_None)
#
# Multiplier-eligible fields (input_kchars, output_audio_kseconds) additionally
# require their paired voice_class_*_adjustment slot to exist on PriceBreakdown.
_PRICED_FIELD_MAP: dict[str, tuple[str, str | None]] = {
    # token-based fields
    'input_mtok': ('input_tokens', None),
    'output_mtok': ('output_tokens', None),
    'cache_read_mtok': ('cache_read_tokens', None),
    'cache_write_mtok': ('cache_write_tokens', None),
    'input_audio_mtok': ('input_audio_tokens', None),
    'output_audio_mtok': ('output_audio_tokens', None),
    'cache_audio_read_mtok': ('cache_audio_read_tokens', None),
    # request-count field
    'requests_kcount': ('requests', None),
    # TTS character/audio-second fields with multiplier pairs
    'input_kchars': ('input_kchars', 'voice_class_input_adjustment'),
    'output_audio_kseconds': ('output_audio_kseconds', 'voice_class_output_adjustment'),
}

# Fields on ModelPrice that are NOT priced (and thus should not appear in the map).
# This guards against accidentally classifying a configuration field as priced.
_NON_PRICED_MODEL_PRICE_FIELDS: set[str] = {'voice_multipliers'}


def _model_price_field_names() -> set[str]:
    return {f.name for f in dataclasses.fields(ModelPrice)}


def _breakdown_field_names() -> set[str]:
    return {f.name for f in dataclasses.fields(PriceBreakdown)}


def test_priced_field_map_covers_every_modelprice_field():
    """Every field on ModelPrice is either priced (in the map) or explicitly
    excluded as non-priced configuration. Drift here means a new field landed
    without a deliberate decision about its breakdown counterpart.
    """
    model_price_fields = _model_price_field_names()
    accounted_for = set(_PRICED_FIELD_MAP) | _NON_PRICED_MODEL_PRICE_FIELDS
    unaccounted = model_price_fields - accounted_for

    assert not unaccounted, (
        f'ModelPrice fields not classified as priced or non-priced: {unaccounted!r}. '
        f'Add to _PRICED_FIELD_MAP or _NON_PRICED_MODEL_PRICE_FIELDS in '
        f'test_price_breakdown_contract.py.'
    )


def test_breakdown_covers_priced_fields():
    """Every priced ModelPrice field has a matching PriceBreakdown field."""
    breakdown_fields = _breakdown_field_names()

    missing: list[str] = []
    for model_price_field, (breakdown_field, _) in _PRICED_FIELD_MAP.items():
        if breakdown_field not in breakdown_fields:
            missing.append(
                f'ModelPrice.{model_price_field} requires PriceBreakdown.{breakdown_field} (maintenance contract).'
            )

    assert not missing, '\n'.join(missing)


def test_breakdown_covers_multiplier_eligible_adjustments():
    """Every multiplier-eligible priced field also has its paired adjustment slot."""
    breakdown_fields = _breakdown_field_names()

    missing: list[str] = []
    for model_price_field, (_, adjustment_field) in _PRICED_FIELD_MAP.items():
        if adjustment_field is None:
            continue
        if adjustment_field not in breakdown_fields:
            missing.append(
                f'ModelPrice.{model_price_field} is multiplier-eligible and requires PriceBreakdown.{adjustment_field}.'
            )

    assert not missing, '\n'.join(missing)


def test_no_orphan_breakdown_fields():
    """Every PriceBreakdown field maps back to either a main priced field or an
    adjustment counterpart. Catches drift in the other direction (breakdown gains
    a field with no ModelPrice source).
    """
    breakdown_fields = _breakdown_field_names()
    expected_breakdown_fields: set[str] = set()
    for main, adjustment in _PRICED_FIELD_MAP.values():
        expected_breakdown_fields.add(main)
        if adjustment is not None:
            expected_breakdown_fields.add(adjustment)

    orphans = breakdown_fields - expected_breakdown_fields
    assert not orphans, (
        f'PriceBreakdown fields with no source in ModelPrice: {orphans!r}. '
        f'Either add a priced field on ModelPrice or remove the orphan from PriceBreakdown.'
    )
