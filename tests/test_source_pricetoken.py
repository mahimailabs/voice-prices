from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from prices.prices_types import ClauseEquals, ModelPrice
from prices.source_pricetoken import (
    IMPORT_ATTRIBUTION,
    PROVIDER_MAP,
    ProviderTarget,
    apply_import,
    convert_model,
    convert_stt_rate,
    convert_tts_rate,
    plan_import,
    render_report,
)
from prices.update import ProviderYaml


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


def test_default_provider_map_refuses_everything_until_urls_authored():
    # As shipped, no target has a pricing_source_url, so a live run imports nothing (safe by default).
    tts = [{'modelId': 'a', 'provider': 'deepgram', 'costPerMChars': 30}]
    plans, _ = plan_import({'tts': tts, 'stt': []}, {})
    assert plans['deepgram'].refused_reason is not None
    assert plans['deepgram'].to_add == []


_MAP = {
    'deepgram': ProviderTarget('deepgram', is_new=False, pricing_source_url='https://deepgram.com/pricing'),
    'amazon': ProviderTarget(
        'amazon_polly',
        is_new=True,
        name='Amazon Polly',
        api_pattern=r'.*polly.*',
        pricing_source_url='https://aws.amazon.com/polly/pricing/',
    ),
    'azure': ProviderTarget('azure_speech', is_new=True, name='Azure Speech'),  # unconfigured -> refused
}


def test_plan_import_routes_dedups_and_refuses():
    tts = [
        {'modelId': 'nova-voice', 'provider': 'deepgram', 'costPerMChars': 30, 'status': 'active'},
        {'modelId': 'aura-2', 'provider': 'deepgram', 'costPerMChars': 30},  # already present -> skipped
        {'modelId': 'amazon-polly-generative', 'provider': 'amazon', 'costPerMChars': 30},
        {'modelId': 'azure-neural', 'provider': 'azure', 'costPerMChars': 16},  # refused (no url)
        {'modelId': 'x', 'provider': 'unknownprov', 'costPerMChars': 5},  # unmapped
    ]
    plans, unmapped = plan_import({'tts': tts, 'stt': []}, {'deepgram': {'aura-2'}}, provider_map=_MAP)

    assert [m.id for m in plans['deepgram'].to_add] == ['nova-voice']
    assert plans['deepgram'].skipped_existing == ['aura-2']
    assert str(plans['deepgram'].to_add[0].pricing_source_url) == 'https://deepgram.com/pricing'

    assert [m.id for m in plans['amazon_polly'].to_add] == ['amazon-polly-generative']
    assert plans['azure_speech'].refused_reason is not None
    assert plans['azure_speech'].to_add == []
    assert unmapped == ['unknownprov']


def test_render_report_mentions_refusal_and_unmapped():
    tts = [
        {'modelId': 'nova-voice', 'provider': 'deepgram', 'costPerMChars': 30},
        {'modelId': 'azure-neural', 'provider': 'azure', 'costPerMChars': 16},
        {'modelId': 'x', 'provider': 'unknownprov', 'costPerMChars': 5},
    ]
    plans, unmapped = plan_import({'tts': tts, 'stt': []}, {}, provider_map=_MAP)
    report = render_report(plans, unmapped)
    assert 'REFUSED' in report
    assert 'unknownprov' in report
    assert '+1 to add' in report


def test_apply_writes_new_provider_file(tmp_path: Path):
    tts = [{'modelId': 'amazon-polly-generative', 'provider': 'amazon', 'costPerMChars': 30, 'status': 'active'}]
    plans, _ = plan_import({'tts': tts, 'stt': []}, {}, provider_map={'amazon': _MAP['amazon']})
    apply_import(plans, providers_dir=tmp_path)

    written = tmp_path / 'amazon_polly.yml'
    assert written.exists()
    provider = ProviderYaml(written).provider
    assert provider.id == 'amazon_polly'
    [model] = provider.models
    assert model.id == 'amazon-polly-generative'
    assert model.provenance is not None and model.provenance.source == 'imported'
    assert isinstance(model.prices, ModelPrice) and model.prices.input_kchars == Decimal('0.03')
    assert model.prices_checked is None  # imported, unverified


def test_apply_appends_to_existing_without_clobbering(tmp_path: Path):
    existing = tmp_path / 'deepgram.yml'
    existing.write_text(
        'id: deepgram\n'
        'name: Deepgram\n'
        'api_pattern: .*deepgram.*\n'
        'models:\n'
        '  - id: aura-existing\n'
        '    match:\n'
        '      equals: aura-existing\n'
        '    prices:\n'
        '      input_kchars: 0.015\n'
    )
    tts = [
        {'modelId': 'nova-new', 'provider': 'deepgram', 'costPerMChars': 30},
        {'modelId': 'aura-existing', 'provider': 'deepgram', 'costPerMChars': 99},  # collides -> never-clobber
    ]
    plans, _ = plan_import(
        {'tts': tts, 'stt': []}, {'deepgram': {'aura-existing'}}, provider_map={'deepgram': _MAP['deepgram']}
    )
    apply_import(plans, providers_dir=tmp_path)

    models = {m.id: m for m in ProviderYaml(existing).provider.models}
    assert set(models) == {'aura-existing', 'nova-new'}
    # the existing verified rate is untouched, not overwritten by the colliding import
    assert isinstance(models['aura-existing'].prices, ModelPrice)
    assert models['aura-existing'].prices.input_kchars == Decimal('0.015')
