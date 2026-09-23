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
    render_yaml,
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


@pytest.mark.parametrize('provider_id', ['livekit', 'livekit-scale'])
def test_kimi_k26_livekit_price(provider_id: str):
    result = calc_price(
        Usage(input_tokens=1_000_000, output_tokens=1_000_000),
        model_ref='moonshotai/kimi-k2.6',
        provider_id=provider_id,
    )
    assert result.input_price == Decimal('0.95')
    assert result.output_price == Decimal('4')
    assert result.total_price == Decimal('4.95')
    assert result.model_price.cache_read_mtok is None
    assert result.model.deprecated is True


@pytest.mark.parametrize('provider_id', ['livekit', 'livekit-scale'])
@pytest.mark.parametrize('qualified', [False, True])
@pytest.mark.parametrize(
    ('prefix', 'model', 'usage', 'base_cost', 'scale_cost'),
    [
        ('moonshotai', 'kimi-k2.6', Usage(input_tokens=1_000_000), '0.95', '0.95'),
        ('google', 'gemini-3-flash', Usage(input_tokens=1_000_000, output_tokens=1_000_000), '3.5', '3.5'),
        ('google', 'gemini-3.1-pro', Usage(input_tokens=1_000_000, output_tokens=1_000_000), '22', '22'),
        ('assemblyai', 'universal-3-5-pro', Usage(audio_input_seconds=Decimal(60)), '0.0075', '0.0075'),
        ('deepgram', 'flux-general', Usage(audio_input_seconds=Decimal(1000)), '0.108333', '0.095'),
        ('deepgram', 'flux-general:multi', Usage(audio_input_seconds=Decimal(1000)), '0.13', '0.113333'),
        ('fishaudio', 's2-pro', Usage(characters=1_000_000), '15', '15'),
        ('fishaudio', 's2.1-pro', Usage(characters=1_000_000), '15', '15'),
        ('fishaudio', 's2.1-pro-free', Usage(characters=1_000_000), '0', '0'),
    ],
)
def test_requested_livekit_names_and_rates(
    provider_id: str, qualified: bool, prefix: str, model: str, usage: Usage, base_cost: str, scale_cost: str
):
    ref = f'{prefix}/{model}' if qualified else model
    result = calc_price(usage, model_ref=ref, provider_id=provider_id)
    assert result.total_price == Decimal(scale_cost if provider_id == 'livekit-scale' else base_cost)
    assert result.unpriced_usage == ()
    assert result.model.free is (model == 's2.1-pro-free')


@pytest.mark.parametrize('model, cache_rate', [('gemini-3-flash', '0.05'), ('gemini-3.1-pro', '0.4')])
def test_gemini_alias_preserves_cached_input_rate(model: str, cache_rate: str):
    result = calc_price(
        Usage(input_tokens=1_000_000, cache_read_tokens=1_000_000), model_ref=model, provider_id='livekit'
    )
    assert result.total_price == Decimal(cache_rate)


@pytest.mark.parametrize('provider_id', ['livekit', 'livekit-scale'])
@pytest.mark.parametrize(
    'model',
    [
        'deepseek-ai/deepseek-v3',
        'deepseek-ai/deepseek-v3.2',
        'zai/glm-5.1',
        'deepgram/aura',
        'cartesia/sonic',
        'inworld/inworld-stt-1',
        'inworld/inworld-tts-1',
        'inworld/inworld-tts-1-max',
        'inworld/inworld-tts-1.5',
        'fishaudio/s2.1-pro-unknown',
        'google/gemini-3.1-pro-unknown',
    ],
)
def test_no_guessed_rate_for_retired_unknown_or_unpublished_models(provider_id: str, model: str):
    with pytest.raises(LookupError):
        calc_price(Usage(characters=1000), model_ref=model, provider_id=provider_id)


def test_reviewed_source_rows_regenerate_aliases_and_free_status():
    from voice_prices.data_snapshot import get_snapshot

    source = Path(__file__).resolve().parents[1] / 'prices/sources/livekit_pricing.json'
    inference = json.loads(source.read_text())['inference']
    # A later regeneration must preserve reviewed aliases and lifecycle flags.
    selected = {kind: [row for row in rows if row.get('aliases')] for kind, rows in inference.items()}
    snapshot = get_snapshot()
    for scale, provider_id in [(False, 'livekit'), (True, 'livekit-scale')]:
        generated = build_provider(selected, scale=scale, checked_date=date(2026, 9, 23))
        yaml = YAML(typ='safe')
        loaded = cast(Any, yaml.load(render_yaml(generated)))  # pyright: ignore[reportUnknownMemberType]
        provider = Provider.model_validate(loaded)
        for model in provider.models:
            _, bundled = snapshot.find_provider_model(model.id, None, provider_id, None)
            assert model.free == bundled.free
            assert model.deprecated == bundled.deprecated
            for entry in selected['stt'] + selected['tts'] + selected['llm']:
                if entry['model_id'] == model.id:
                    for alias in entry['aliases']:
                        assert model.is_match(alias)
                        assert bundled.is_match(alias)


def test_fish_free_is_explicit_and_excluded_only_from_slim():
    root = Path(__file__).resolve().parents[1] / 'prices'
    for filename, present in [('data.json', True), ('data_slim.json', False)]:
        provider = next(p for p in json.loads((root / filename).read_text()) if p['id'] == 'livekit')
        models = {m['id']: m for m in provider['models']}
        assert ('fishaudio/s2.1-pro-free' in models) is present
        assert 'fishaudio/s2.1-pro' in models
        if present:
            assert models['fishaudio/s2.1-pro-free']['free'] is True


@pytest.mark.parametrize('checked_day, included', [(25, True), (26, False), (27, False)])
def test_deprecated_model_remains_until_verified_retirement(checked_day: int, included: bool):
    entry = {
        **INFERENCE['llm'][0],
        'is_deprecated': True,
        'retirement_date': '2026-09-26',
    }
    provider = build_provider({'llm': [entry]}, scale=False, checked_date=date(2026, 9, checked_day))
    assert bool(provider['models']) is included
    if included:
        yaml = YAML(typ='safe')
        loaded = cast(Any, yaml.load(render_yaml(provider)))  # pyright: ignore[reportUnknownMemberType]
        model = Provider.model_validate(loaded).models[0]
        assert model.deprecated is True
        assert model.price_comments is not None and '2026-09-26' in model.price_comments


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


def test_full_page_coverage_and_each_published_rate():
    """Every visible row and billing metric must resolve under both plan selectors."""
    from datetime import datetime, timezone

    source = Path(__file__).resolve().parents[1] / 'prices/sources/livekit_pricing.json'
    inference = json.loads(source.read_text())['inference']
    assert {kind: len(rows) for kind, rows in inference.items()} == {'llm': 58, 'stt': 22, 'tts': 27}
    assert len({row['source_model_id'] for rows in inference.values() for row in rows}) == 90
    for kind, rows in inference.items():
        for row in rows:
            for tier, provider_id in [('build', 'livekit'), ('scale', 'livekit-scale')]:
                ref = row['model_id']
                if kind == 'llm':
                    ref = row['source_model_id'] + '@' + row['serving_provider']
                result = calc_price(
                    Usage(),
                    model_ref=ref,
                    provider_id=provider_id,
                    genai_request_timestamp=datetime(2026, 9, 23, tzinfo=timezone.utc),
                )
                assert result.model.id == row['model_id']
                for rate in row['rates']:
                    expected = Decimal(rate[tier])
                    if kind == 'llm':
                        field = LLM_METRIC_FIELD[rate['metric']]
                    elif kind == 'stt':
                        field, expected = 'input_audio_kseconds', stt_rate(expected)
                    else:
                        field, expected = 'input_kchars', tts_rate(expected)
                    assert getattr(result.model_price, field) == expected, (ref, tier, field)


@pytest.mark.parametrize('provider_id,regular', [('livekit', '48'), ('livekit-scale', '36')])
@pytest.mark.parametrize(
    'moment,free',
    [
        ('2026-09-09T06:59:59+00:00', False),
        ('2026-09-09T07:00:00+00:00', True),
        ('2026-10-08T06:59:59+00:00', True),
        ('2026-10-08T07:00:00+00:00', False),
        ('2026-10-08T03:00:00-04:00', False),
        ('2026-09-09T06:59:59', False),
        ('2026-09-09T07:00:00', True),
        ('2026-10-08T06:59:59', True),
        ('2026-10-08T07:00:00', False),
    ],
)
def test_promotion_exact_boundaries(provider_id: str, regular: str, moment: str, free: bool):
    from datetime import datetime

    result = calc_price(
        Usage(characters=1_000_000),
        model_ref='gradium/default',
        provider_id=provider_id,
        genai_request_timestamp=datetime.fromisoformat(moment),
    )
    assert result.total_price == (Decimal(0) if free else Decimal(regular))
    assert result.unpriced_usage == ()
    assert result.model.free is False  # A temporary promotion must remain in the slim catalog.


def test_source_parser_and_normalizer_keep_routes_and_filter_retired():
    from prices.livekit_source import normalize, page_data

    entry = {
        'model_id': 'openai/example',
        'model_label': 'Example',
        'model_creator_label': 'OpenAI',
        'provider_id': 'azure',
        'provider_label': 'Azure',
        'pricing_current': {
            'rates': [
                {
                    'metric': 'input_tokens',
                    'unit': {'currency': 'usd', 'measure': 'tokens', 'per': 1_000_000},
                    'unit_price': {tier: {'amount': '1.25'} for tier in ('build', 'ship', 'scale')},
                }
            ]
        },
    }
    data = {
        'llmModels': [
            entry,
            {**entry, 'provider_id': 'openai'},
            {**entry, 'is_sunset': True},
            {**entry, 'is_unlisted': True},
        ],
        'sttModels': [entry],
        'ttsModels': [entry],
    }
    html = '<script>self.__next_f.push(' + json.dumps([1, json.dumps(data)]) + ')</script>'
    rows = normalize(page_data(html), {}, CHECKED)['inference']['llm']
    assert [r['model_id'] for r in rows] == ['openai/example', 'openai/example@openai']
    assert rows[0]['aliases'] == ['openai/example@azure']
    assert rows[1]['rates'][0]['build'] == '1.25'
    with pytest.raises(ValueError):
        page_data('<html>Pricing unavailable</html>')


def test_generator_preserves_manual_telephony_and_is_idempotent(tmp_path: Path):
    root = Path(__file__).resolve().parents[1] / 'prices'
    source = root / 'sources/livekit_pricing.json'
    for name in ('livekit.yml', 'livekit_scale.yml'):
        (tmp_path / name).write_text((root / 'providers' / name).read_text())
    before = {p.name: p.read_text() for p in tmp_path.iterdir()}
    generate(source, tmp_path, checked_date=date(2026, 9, 23))
    assert {p.name: p.read_text() for p in tmp_path.iterdir()} == before


@pytest.mark.parametrize('route,expected', [('azure', '0.13'), ('openai', '0.125')])
def test_gpt5_route_specific_cached_cost(route: str, expected: str):
    result = calc_price(
        Usage(input_tokens=1_000_000, cache_read_tokens=1_000_000),
        model_ref=f'openai/gpt-5@{route}',
        provider_id='livekit-scale',
    )
    assert result.total_price == Decimal(expected)
