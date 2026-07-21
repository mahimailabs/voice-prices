from __future__ import annotations

from decimal import Decimal

import pytest

from prices.prices_types import ClauseEquals, ModelPrice
from prices.source_pricetoken import (
    IMPORT_ATTRIBUTION,
    PROVIDER_MAP,
    convert_model,
    convert_stt_rate,
    convert_tts_rate,
)


def test_tts_rate_conversion():
    # $/1M chars -> $/1k chars
    assert convert_tts_rate(30) == Decimal('0.03')
    assert convert_tts_rate(1000) == Decimal('1')
    assert convert_tts_rate('16') == Decimal('0.016')


def test_stt_rate_conversion():
    # $/minute -> $/1000 seconds
    assert convert_stt_rate(6) == Decimal('100')
    assert convert_stt_rate(60) == Decimal('1000')
    assert convert_stt_rate('0.0035') == Decimal('0.0035') * Decimal(1000) / Decimal(60)


def test_convert_tts_model_happy_path():
    model = convert_model(
        {
            'modelId': 'amazon-polly-generative',
            'provider': 'amazon',
            'displayName': 'Amazon Polly Generative',
            'costPerMChars': 30,
            'status': 'active',
        },
        'tts',
    )
    assert model.id == 'amazon-polly-generative'
    assert model.match == ClauseEquals(equals='amazon-polly-generative')
    assert model.name == 'Amazon Polly Generative'
    assert isinstance(model.prices, ModelPrice)
    assert model.prices.input_kchars == Decimal('0.03')
    assert model.prices.input_audio_kseconds is None
    # imported + unverified: never carries prices_checked, always marked imported
    assert model.prices_checked is None
    assert model.provenance is not None
    assert model.provenance.source == 'imported'
    assert model.price_comments == IMPORT_ATTRIBUTION
    assert model.deprecated is None


def test_convert_stt_model_happy_path():
    model = convert_model(
        {'modelId': 'assemblyai-best', 'costPerMinute': 6, 'status': 'active'},
        'stt',
    )
    assert isinstance(model.prices, ModelPrice)
    assert model.prices.input_audio_kseconds == Decimal('100')
    assert model.prices.input_kchars is None
    assert model.provenance is not None and model.provenance.source == 'imported'


def test_deprecated_status_maps_to_deprecated_flag():
    model = convert_model({'modelId': 'old-voice', 'costPerMChars': 10, 'status': 'deprecated'}, 'tts')
    assert model.deprecated is True


def test_fail_closed_missing_cost_field():
    with pytest.raises(ValueError, match='missing required costPerMChars'):
        convert_model({'modelId': 'x', 'status': 'active'}, 'tts')
    with pytest.raises(ValueError, match='missing required costPerMinute'):
        convert_model({'modelId': 'y', 'status': 'active'}, 'stt')


def test_fail_closed_unexpected_cost_field():
    # a per-token or per-second field is an unexpected structure we refuse to map
    with pytest.raises(ValueError, match='unexpected cost field'):
        convert_model({'modelId': 'x', 'costPerMChars': 30, 'costPerToken': 0.1}, 'tts')
    with pytest.raises(ValueError, match='unexpected cost field'):
        convert_model({'modelId': 'y', 'costPerMinute': 6, 'costPerMChars': 30}, 'stt')


def test_fail_closed_missing_model_id():
    with pytest.raises(ValueError, match='missing a string modelId'):
        convert_model({'costPerMChars': 30}, 'tts')


def test_provider_map_targets():
    # existing voice providers append models; collision cases become speech-specific providers
    assert PROVIDER_MAP['deepgram'].is_new is False
    assert PROVIDER_MAP['deepgram'].target_id == 'deepgram'
    assert PROVIDER_MAP['azure'].is_new is True
    assert PROVIDER_MAP['azure'].target_id == 'azure_speech'
    assert PROVIDER_MAP['amazon'].target_id == 'amazon_polly'
    # every new provider carries a display name (api_pattern / url are authored + confirmed later)
    for pt_id, target in PROVIDER_MAP.items():
        if target.is_new:
            assert target.name, f'{pt_id} new provider needs a name'
