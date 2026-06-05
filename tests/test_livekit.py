"""Tests for the LiveKit Inference price generator (`prices.livekit_gen`).

The generator turns LiveKit's structured pricing JSON into two provider YAMLs:
`livekit` (every active model at the Build/Ship price) and `livekit-scale` (only the
models whose Scale price differs, with `fallback_model_providers: [livekit]`).
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pydantic_core
import pytest
from ruamel.yaml import YAML

from prices.livekit_gen import (
    LLM_METRIC_FIELD,
    build_provider,
    generate,
    scale_differs,
    stt_rate,
    tts_rate,
)
from prices.prices_types import ModelInfo, ModelPrice, Provider
from voice_prices import Usage, calc_price

CHECKED = date(2026, 6, 4)


def _prices(model: ModelInfo) -> ModelPrice:
    """Narrow a generated model's prices (always a flat ModelPrice, never conditional)."""
    assert isinstance(model.prices, ModelPrice)
    return model.prices


# A small but representative slice of LiveKit's `.inference` payload:
# - one STT model that drops on Scale, one that is flat
# - one deprecated TTS model (must be skipped) and one active TTS model that drops on Scale
# - one LLM model (identical across all tiers, so never in the Scale file)
INFERENCE = {
    'stt': [
        {
            'model_id': 'deepgram/nova-2',
            'model_label': 'Nova-2',
            'is_deprecated': False,
            'rates': [{'metric': 'minute_usage', 'build': '0.0058', 'ship': '0.0058', 'scale': '0.0047'}],
        },
        {
            'model_id': 'assemblyai/universal-streaming',
            'model_label': 'Universal-Streaming',
            'is_deprecated': False,
            'rates': [{'metric': 'minute_usage', 'build': '0.0025', 'ship': '0.0025', 'scale': '0.0025'}],
        },
    ],
    'tts': [
        {
            'model_id': 'cartesia/sonic',
            'model_label': 'Sonic',
            'is_deprecated': True,
            'rates': [{'metric': 'character_usage', 'build': '50', 'ship': '50', 'scale': '37.50'}],
        },
        {
            'model_id': 'cartesia/sonic-2',
            'model_label': 'Sonic 2',
            'is_deprecated': False,
            'rates': [{'metric': 'character_usage', 'build': '50', 'ship': '50', 'scale': '37.50'}],
        },
    ],
    'llm': [
        {
            'model_id': 'openai/gpt-4o',
            'model_label': 'GPT-4o',
            'is_deprecated': False,
            'rates': [
                {'metric': 'cached_input_tokens', 'build': '1.25', 'ship': '1.25', 'scale': '1.25'},
                {'metric': 'input_tokens', 'build': '2.50', 'ship': '2.50', 'scale': '2.50'},
                {'metric': 'output_tokens', 'build': '10.00', 'ship': '10.00', 'scale': '10.00'},
            ],
        },
    ],
}


# ---- conversions ------------------------------------------------------------


def test_stt_rate_converts_per_minute_to_per_kseconds():
    # $/min -> $ per 1000 audio seconds: value * 1000 / 60
    assert stt_rate('0.0058') == Decimal('0.096667')
    assert stt_rate('0.0075') == Decimal('0.125')


def test_tts_rate_converts_per_million_chars_to_per_kchars():
    # $/1,000,000 chars -> $ per 1000 chars: value / 1000
    assert tts_rate('50') == Decimal('0.05')
    assert tts_rate('37.50') == Decimal('0.0375')
    assert tts_rate('300') == Decimal('0.3')


def test_llm_metric_field_mapping():
    assert LLM_METRIC_FIELD['input_tokens'] == 'input_mtok'
    assert LLM_METRIC_FIELD['output_tokens'] == 'output_mtok'
    assert LLM_METRIC_FIELD['cached_input_tokens'] == 'cache_read_mtok'


# ---- selection --------------------------------------------------------------


def test_scale_differs_true_only_when_a_rate_drops():
    stt_discounted = INFERENCE['stt'][0]
    stt_flat = INFERENCE['stt'][1]
    llm_flat = INFERENCE['llm'][0]
    assert scale_differs(stt_discounted) is True
    assert scale_differs(stt_flat) is False
    assert scale_differs(llm_flat) is False


# ---- provider assembly ------------------------------------------------------


def test_base_provider_has_all_active_models_at_build_price():
    provider = Provider.model_validate(build_provider(INFERENCE, scale=False, checked_date=CHECKED))
    assert provider.id == 'livekit'
    ids = {m.id for m in provider.models}
    # deprecated cartesia/sonic excluded; everything else present
    assert ids == {'deepgram/nova-2', 'assemblyai/universal-streaming', 'cartesia/sonic-2', 'openai/gpt-4o'}

    nova = next(m for m in provider.models if m.id == 'deepgram/nova-2')
    assert _prices(nova).input_audio_kseconds == Decimal('0.096667')

    gpt = next(m for m in provider.models if m.id == 'openai/gpt-4o')
    assert _prices(gpt).input_mtok == Decimal('2.5')
    assert _prices(gpt).output_mtok == Decimal('10')
    assert _prices(gpt).cache_read_mtok == Decimal('1.25')


def test_generated_models_opt_out_of_collapse():
    # collapse: false keeps every LiveKit model a distinct row (the collapse-models pipeline step
    # would otherwise merge same-price id-prefixed variants), and keeps generation idempotent.
    provider = Provider.model_validate(build_provider(INFERENCE, scale=False, checked_date=CHECKED))
    assert all(m.collapse is False for m in provider.models)


def test_scale_provider_only_discounted_models_with_fallback():
    provider = Provider.model_validate(build_provider(INFERENCE, scale=True, checked_date=CHECKED))
    assert provider.id == 'livekit-scale'
    assert provider.fallback_model_providers == ['livekit']
    ids = {m.id for m in provider.models}
    # only the scale-discounted, non-deprecated models: the flat STT, the LLM, and the
    # deprecated TTS are all absent (LLM + flat fall back to `livekit`).
    assert ids == {'deepgram/nova-2', 'cartesia/sonic-2'}

    nova = next(m for m in provider.models if m.id == 'deepgram/nova-2')
    assert _prices(nova).input_audio_kseconds == Decimal('0.078333')
    sonic2 = next(m for m in provider.models if m.id == 'cartesia/sonic-2')
    assert _prices(sonic2).input_kchars == Decimal('0.0375')


# ---- input validation (loud failure instead of silent drift) ----------------


def test_generator_rejects_ship_diverging_from_build():
    # The generator only emits the Build/Ship tier and assumes they are equal; if LiveKit ever
    # prices Ship differently it must fail loudly, not silently use Build.
    bad = {
        'stt': [
            {
                'model_id': 'vendor/model',
                'model_label': 'Model',
                'is_deprecated': False,
                'rates': [{'metric': 'minute_usage', 'build': '0.005', 'ship': '0.006', 'scale': '0.005'}],
            }
        ],
        'tts': [],
        'llm': [],
    }
    with pytest.raises(ValueError, match='Ship'):
        build_provider(bad, scale=False, checked_date=CHECKED)


def test_generator_rejects_unknown_llm_metric():
    # A new LLM rate metric must raise a clear error naming it, not a bare KeyError.
    bad = {
        'stt': [],
        'tts': [],
        'llm': [
            {
                'model_id': 'openai/x',
                'model_label': 'X',
                'is_deprecated': False,
                'rates': [{'metric': 'mystery_tokens', 'build': '1', 'ship': '1', 'scale': '1'}],
            }
        ],
    }
    with pytest.raises(ValueError, match='mystery_tokens'):
        build_provider(bad, scale=False, checked_date=CHECKED)


# ---- end-to-end generation --------------------------------------------------


def test_generate_writes_strict_valid_yaml(tmp_path: Path):
    src = tmp_path / 'livekit_pricing.json'
    src.write_text(json.dumps({'inference': INFERENCE}))
    out_dir = tmp_path / 'providers'
    out_dir.mkdir()

    base_path, scale_path = generate(src, out_dir, checked_date=CHECKED)
    assert base_path.name == 'livekit.yml'
    assert scale_path.name == 'livekit_scale.yml'

    yaml = YAML()
    for path, expected_id in ((base_path, 'livekit'), (scale_path, 'livekit-scale')):
        data = cast(Any, yaml.load(path.read_text()))  # pyright: ignore[reportUnknownMemberType]
        # validate exactly the way build.py does (strict, via JSON round-trip)
        provider = Provider.model_validate_json(pydantic_core.to_json(data), strict=True)
        assert provider.id == expected_id
        assert provider.models, 'each generated provider must list at least one model'


# ---- calc_price against the generated catalog (data.py) ---------------------


def test_calc_price_livekit_stt():
    # input_audio_kseconds is $ per 1000 audio seconds, so 1000 seconds == the raw rate.
    price = calc_price(Usage(audio_input_seconds=Decimal(1000)), model_ref='deepgram/nova-2', provider_id='livekit')
    assert price.total_price == Decimal('0.096667')
    assert price.provider.id == 'livekit'


def test_calc_price_livekit_tts():
    price = calc_price(Usage(characters=1000), model_ref='cartesia/sonic-2', provider_id='livekit')
    assert price.total_price == Decimal('0.05')


def test_calc_price_livekit_llm():
    price = calc_price(
        Usage(input_tokens=1_000_000, output_tokens=1_000_000), model_ref='openai/gpt-4o', provider_id='livekit'
    )
    assert price.input_price == Decimal('2.5')
    assert price.output_price == Decimal('10')


def test_calc_price_scale_applies_voice_discount():
    price = calc_price(
        Usage(audio_input_seconds=Decimal(1000)), model_ref='deepgram/nova-2', provider_id='livekit-scale'
    )
    assert price.total_price == Decimal('0.078333')


def test_calc_price_scale_falls_back_to_livekit_for_llm():
    # LLM is identical across tiers, so gpt-4o is absent from livekit-scale and falls back.
    price = calc_price(Usage(input_tokens=1_000_000), model_ref='openai/gpt-4o', provider_id='livekit-scale')
    assert price.input_price == Decimal('2.5')


def test_calc_price_scale_falls_back_for_flat_voice():
    # xai/tts-1 has no Scale discount, so it is absent from livekit-scale and falls back to livekit.
    price = calc_price(Usage(characters=1000), model_ref='xai/tts-1', provider_id='livekit-scale')
    assert price.total_price == Decimal('0.015')


def test_bare_model_ref_does_not_resolve_to_livekit():
    # livekit omits model_match, so a bare ref must still resolve to the direct vendor.
    price = calc_price(Usage(input_tokens=1000, output_tokens=100), model_ref='gpt-4o')
    assert price.provider.id == 'openai'


def test_livekit_excluded_from_freshness_scrape():
    # LiveKit is refreshed via the structured JSON (make livekit-get), not the browser-scrape
    # freshness check, so its voice models must never be selected even though they have a URL.
    from prices.freshness.select import select_stale

    items = select_stale(date(2020, 1, 1), all=True)
    assert not any(it.provider_id in {'livekit', 'livekit-scale'} for it in items)
