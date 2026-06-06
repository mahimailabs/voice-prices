"""Tests for the documentation-site generator (prices.build_site).

Covers the price-shape normalizer, modality detection, exclusion rules, and a
full build against the real prices/data.json so the page can never silently break
when the catalog changes shape.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from prices.build_site import (
    DATA_JSON,
    Comparison,
    _resolve_direct,
    base_prices,
    build_catalog,
    build_comparison,
    build_site,
    detect_modality,
    hero_stats,
    missing_alias_targets,
    render_html,
    unknown_prefix_providers,
)


def test_base_prices_flat():
    flat, tiered, daily = base_prices({'input_mtok': 2.0, 'output_mtok': 8.0})
    assert flat == {'input_mtok': 2.0, 'output_mtok': 8.0}
    assert tiered is False
    assert daily is False


def test_base_prices_tiered_reads_base():
    flat, tiered, daily = base_prices({'input_mtok': {'base': 3, 'tiers': [{'start': 200000, 'price': 6}]}})
    assert flat == {'input_mtok': 3.0}
    assert tiered is True
    assert daily is False


def test_base_prices_conditional_uses_base_block_and_flags_daily():
    prices = [
        {'prices': {'input_mtok': 0.135, 'output_mtok': 0.55}},
        {
            'constraint': {'start_time': '00:30:00Z', 'end_time': '16:30:00Z'},
            'prices': {'input_mtok': 0.27, 'output_mtok': 1.1},
        },
    ]
    flat, tiered, daily = base_prices(prices)
    assert flat == {'input_mtok': 0.135, 'output_mtok': 0.55}
    assert tiered is False
    assert daily is True


def test_base_prices_skips_voice_multipliers():
    flat, _, _ = base_prices({'input_kchars': 0.04, 'voice_multipliers': {'default': 1.0, 'premium': 1.5}})
    assert flat == {'input_kchars': 0.04}


def test_base_prices_empty():
    flat, tiered, daily = base_prices({})
    assert flat == {}
    assert tiered is False
    assert daily is False


def test_detect_modality():
    assert detect_modality({'input_kchars': 0.04}) == 'tts'
    assert detect_modality({'input_audio_kseconds': 0.08}) == 'stt'
    assert detect_modality({'input_mtok': 2.0, 'output_mtok': 8.0}) == 'llm'
    # audio-token models classify as LLM (token-priced), not STT/TTS
    assert detect_modality({'input_mtok': 5.0, 'input_audio_mtok': 40.0}) == 'llm'
    assert detect_modality({}) is None


def test_build_catalog_excludes_unpriced_and_deprecated():
    data: list[dict[str, Any]] = [
        {
            'id': 'acme',
            'name': 'Acme',
            'models': [
                {'id': 'acme-1', 'prices': {'input_mtok': 2.0, 'output_mtok': 8.0}, 'context_window': 1000},
                {'id': 'acme-free', 'prices': {}},  # unpriced: excluded
                {'id': 'acme-old', 'prices': {'input_mtok': 1.0}, 'deprecated': True},  # deprecated: excluded
            ],
        }
    ]
    catalog = build_catalog(data)
    assert [p['id'] for p in catalog['llm']] == ['acme']
    model_ids = [m['id'] for m in catalog['llm'][0]['models']]
    assert model_ids == ['acme-1']


def test_build_catalog_provider_can_span_modalities():
    data: list[dict[str, Any]] = [
        {
            'id': 'multi',
            'name': 'Multi',
            'models': [
                {'id': 'multi-llm', 'prices': {'input_mtok': 1.0, 'output_mtok': 2.0}},
                {'id': 'multi-tts', 'prices': {'input_kchars': 0.02}},
            ],
        }
    ]
    catalog = build_catalog(data)
    assert [p['id'] for p in catalog['llm']] == ['multi']
    assert [p['id'] for p in catalog['tts']] == ['multi']
    assert catalog['stt'] == []


def test_stt_derives_per_minute():
    data: list[dict[str, Any]] = [
        {'id': 'd', 'name': 'D', 'models': [{'id': 'nova', 'prices': {'input_audio_kseconds': 0.08}}]}
    ]
    catalog = build_catalog(data)
    row = catalog['stt'][0]['models'][0]
    assert row.get('input_audio_kseconds') == 0.08
    assert row.get('per_min') == 0.08 * 60 / 1000  # 0.0048


def test_build_catalog_from_real_data_json():
    data: list[dict[str, Any]] = json.loads(DATA_JSON.read_text())
    catalog = build_catalog(data)
    # Each modality has at least one provider (counts not pinned: they drift with data.json).
    assert len(catalog['llm']) > 0
    assert len(catalog['tts']) > 0
    assert len(catalog['stt']) > 0
    llm_ids = {p['id'] for p in catalog['llm']}
    tts_ids = {p['id'] for p in catalog['tts']}
    stt_ids = {p['id'] for p in catalog['stt']}
    assert 'anthropic' in llm_ids
    assert 'cartesia' in tts_ids
    assert 'deepgram' in stt_ids
    # No deprecated model leaks into its own provider's rows. Model ids are not
    # globally unique (OpenRouter mirrors other providers' ids), so this is checked
    # per (provider id, model id), not globally.
    deprecated_pairs: set[tuple[str, str]] = set()
    for p in data:
        models: list[dict[str, Any]] = p.get('models') or []
        for m in models:
            if m.get('deprecated') is True:
                deprecated_pairs.add((p['id'], m['id']))
    assert deprecated_pairs  # data.json has at least one deprecated model today
    for entries in catalog.values():
        for entry in entries:
            for model in entry['models']:
                assert (entry['id'], model['id']) not in deprecated_pairs


# ---- LiveKit vs direct comparison -------------------------------------------

COMP_DATA: list[dict[str, Any]] = [
    {'id': 'deepgram', 'name': 'Deepgram', 'models': [{'id': 'nova-2', 'prices': {'input_audio_kseconds': 0.098333}}]},
    {'id': 'cartesia', 'name': 'Cartesia', 'models': [{'id': 'sonic-3', 'prices': {'input_kchars': 0.04}}]},
    {
        'id': 'openai',
        'name': 'OpenAI',
        'models': [{'id': 'gpt-4o', 'prices': {'input_mtok': 2.5, 'output_mtok': 10.0}}],
    },
    {
        'id': 'livekit',
        'name': 'LiveKit Inference',
        'models': [
            {'id': 'deepgram/nova-2', 'name': 'Nova-2', 'prices': {'input_audio_kseconds': 0.096667}},
            {'id': 'cartesia/sonic-2', 'name': 'Sonic 2', 'prices': {'input_kchars': 0.05}},
            {'id': 'openai/gpt-4o', 'name': 'GPT-4o', 'prices': {'input_mtok': 2.5, 'output_mtok': 10.0}},
            {
                'id': 'speechmatics/standard',
                'name': 'Speechmatics Standard',
                'prices': {'input_audio_kseconds': 0.0833},
            },
        ],
    },
    {
        'id': 'livekit-scale',
        'name': 'LiveKit Inference (Scale)',
        'models': [
            {'id': 'deepgram/nova-2', 'name': 'Nova-2', 'prices': {'input_audio_kseconds': 0.078333}},
            {'id': 'cartesia/sonic-2', 'name': 'Sonic 2', 'prices': {'input_kchars': 0.0375}},
        ],
    },
]


def test_build_comparison_maps_direct_and_computes_delta():
    comp = build_comparison(COMP_DATA)
    nova = next(r for r in comp['stt'] if r['id'] == 'deepgram/nova-2')
    assert nova['direct'] == round(0.098333 * 60 / 1000, 6)  # $/min, auto-matched to deepgram:nova-2
    assert nova['livekit'] == round(0.096667 * 60 / 1000, 6)
    assert nova['scale'] == round(0.078333 * 60 / 1000, 6)
    assert nova['delta'] == round((0.096667 - 0.098333) / 0.098333 * 100, 1)


def test_build_comparison_alias_resolves_cartesia_to_sonic3():
    comp = build_comparison(COMP_DATA)
    sonic = next(r for r in comp['tts'] if r['id'] == 'cartesia/sonic-2')
    assert sonic['direct'] == round(0.04 * 1000, 6)  # $/1M chars from the single direct cartesia model
    assert sonic['livekit'] == round(0.05 * 1000, 6)
    assert sonic['scale'] == round(0.0375 * 1000, 6)


def test_build_comparison_livekit_only_has_no_direct_baseline():
    comp = build_comparison(COMP_DATA)
    spx = next(r for r in comp['stt'] if r['id'] == 'speechmatics/standard')
    assert spx['direct'] is None  # speechmatics is not a direct provider
    assert spx['delta'] is None
    assert spx['scale'] is None  # absent from livekit-scale -> falls back to livekit


def test_build_comparison_llm_is_pass_through():
    comp = build_comparison(COMP_DATA)
    gpt = next(r for r in comp['llm'] if r['id'] == 'openai/gpt-4o')
    assert gpt['direct'] == 2.5  # input $/Mtok
    assert gpt['livekit'] == 2.5
    assert gpt['delta'] == 0.0
    assert gpt['scale'] is None  # LLM never in livekit-scale


def test_alias_targets_all_present_in_real_catalog():
    # Every curated LiveKit->direct alias must point at a model that exists in the catalog, so a
    # future direct-model rename surfaces here instead of silently dropping a comparison baseline.
    data: list[dict[str, Any]] = json.loads(DATA_JSON.read_text())
    assert missing_alias_targets(data) == []


def test_missing_alias_targets_detects_renamed_target():
    data: list[dict[str, Any]] = json.loads(DATA_JSON.read_text())
    broken = [p for p in data if p.get('id') != 'cartesia']  # drop the provider holding sonic-3
    missing = missing_alias_targets(broken)
    assert 'cartesia/sonic-2' in missing  # its alias target cartesia:sonic-3 is now gone


def test_livekit_prefix_providers_all_exist_in_catalog():
    # Guards the x_ai/x-ai class of typo: a prefix mapped to a missing provider would silently make
    # every model with that prefix show as LiveKit-only even when a direct baseline exists.
    data: list[dict[str, Any]] = json.loads(DATA_JSON.read_text())
    assert unknown_prefix_providers(data) == []


def test_elevenlabs_flash_and_turbo_v2_resolve_to_their_own_direct_entry():
    # Flash/Turbo v2 must compare against their OWN direct price, not the v2.5 entry. A cross-version
    # alias made the hero report a "+233% Flash v2" markup computed against Flash v2.5's price, which
    # is indefensible (two different models). They now have explicit direct entries.
    assert _resolve_direct('elevenlabs/eleven_flash_v2') == ('elevenlabs', 'eleven_flash_v2')
    assert _resolve_direct('elevenlabs/eleven_turbo_v2') == ('elevenlabs', 'eleven_turbo_v2')


def test_elevenlabs_flash_and_turbo_v2_are_distinct_priced_direct_models():
    # The v2 and v2.5 entries share a price ($0.045/kchar) and an id prefix, so the collapse hook
    # would merge v2.5 into v2 and drop it. Both must survive in the catalog so each LiveKit model
    # compares against its matching version.
    data: list[dict[str, Any]] = json.loads(DATA_JSON.read_text())
    elevenlabs = next(p for p in data if p['id'] == 'elevenlabs')
    priced = {m['id']: m for m in elevenlabs['models'] if m.get('prices')}
    for model_id in ('eleven_flash_v2', 'eleven_flash_v2_5', 'eleven_turbo_v2', 'eleven_turbo_v2_5'):
        assert model_id in priced, f'{model_id} missing from the direct ElevenLabs catalog'
        assert priced[model_id]['prices'].get('input_kchars') is not None


def test_elevenlabs_flash_v2_markup_is_a_true_same_model_comparison():
    # Flash v2's baseline must come from Flash v2's OWN direct entry (guaranteed by the resolve test
    # above), not a cross-version proxy. Flash v2 and v2.5 bill the same direct rate, so both rows show
    # the same real markup. Value-independent on purpose: the exact percentage is not pinned, because it
    # tracks whatever direct-rate basis the catalog uses and would churn on any legitimate reprice.
    data: list[dict[str, Any]] = json.loads(DATA_JSON.read_text())
    comp = build_comparison(data)
    v2 = next(r for r in comp['tts'] if r['id'] == 'elevenlabs/eleven_flash_v2')
    v2_5 = next(r for r in comp['tts'] if r['id'] == 'elevenlabs/eleven_flash_v2_5')
    assert v2['direct'] is not None and v2_5['direct'] is not None
    assert v2['direct'] == v2_5['direct']  # both Flash models bill the same direct rate
    assert v2['delta'] == v2_5['delta']
    assert v2['delta'] is not None and v2['delta'] > 0  # a real markup against its own baseline


def test_livekit_xai_grok_resolves_to_a_direct_baseline():
    # Regression: xai/grok-4-1-fast was wrongly LiveKit-only because the prefix mapped to 'x_ai'
    # rather than the real provider id 'x-ai'. It has an exact direct match (pass-through).
    data: list[dict[str, Any]] = json.loads(DATA_JSON.read_text())
    comp = build_comparison(data)
    grok = next(r for r in comp['llm'] if r['id'] == 'xai/grok-4-1-fast-non-reasoning')
    assert grok['direct'] is not None  # the regression: was wrongly None
    assert grok['direct'] == grok['livekit']  # resolved to the same-priced direct model (pass-through)
    latest = next(r for r in comp['llm'] if r['id'] == 'openai/gpt-5.3-chat-latest')
    assert latest['direct'] is not None


def test_hero_stats_picks_a_balanced_set():
    comp: Comparison = {
        'llm': [
            {'id': 'openai/gpt-4o', 'name': 'GPT-4o', 'direct': 2.5, 'livekit': 2.5, 'scale': None, 'delta': 0.0},
        ],
        'tts': [
            {
                'id': 'elevenlabs/eleven_multilingual_v2',
                'name': 'Eleven Multilingual v2',
                'direct': 91.0,
                'livekit': 300.0,
                'scale': 120.0,
                'delta': 229.7,
            },
            {
                'id': 'cartesia/sonic-2',
                'name': 'Sonic 2',
                'direct': 40.0,
                'livekit': 50.0,
                'scale': 37.5,
                'delta': 25.0,
            },
        ],
        'stt': [
            {
                'id': 'deepgram/nova-2',
                'name': 'Nova-2',
                'direct': 0.0059,
                'livekit': 0.0058,
                'scale': 0.0047,
                'delta': -1.7,
            },
        ],
    }
    stats = hero_stats(comp)
    labels = [s['label'] for s in stats]
    assert len(labels) == len(set(labels))  # distinct models
    assert any(s['sign'] != 'up' for s in stats)  # balance: never all markups

    by_label = {s['label']: s for s in stats}
    # biggest markup
    assert by_label['Eleven Multilingual v2'] == {
        'label': 'Eleven Multilingual v2',
        'detail': '+230% via LiveKit',
        'sign': 'up',
    }
    # cheapest / at-cost (min delta)
    assert by_label['Nova-2']['detail'] == '-2% via LiveKit'
    assert by_label['Nova-2']['sign'] == 'down'
    # pass-through LLM
    assert 'pass-through' in by_label['GPT-4o']['detail']
    assert by_label['GPT-4o']['sign'] == 'flat'
    # Scale win, distinct from the markup model
    assert by_label['Sonic 2']['detail'] == '25% cheaper on Scale'


def test_hero_stats_empty_when_no_priced_rows():
    empty: Comparison = {'llm': [], 'tts': [], 'stt': []}
    assert hero_stats(empty) == []


def test_hero_stats_marks_a_positive_min_delta_as_up():
    # When every priced row is a markup, the at-cost slot must still color it 'up', not 'flat'.
    comp: Comparison = {
        'llm': [],
        'tts': [
            {'id': 'v/a', 'name': 'A', 'direct': 10.0, 'livekit': 30.0, 'scale': None, 'delta': 200.0},
            {'id': 'v/b', 'name': 'B', 'direct': 10.0, 'livekit': 11.0, 'scale': None, 'delta': 10.0},
        ],
        'stt': [],
    }
    b = next(s for s in hero_stats(comp) if s['label'] == 'B')
    assert b['detail'] == '+10% via LiveKit'
    assert b['sign'] == 'up'


def test_hero_stats_near_zero_delta_reads_about_the_same():
    comp: Comparison = {
        'llm': [],
        'tts': [{'id': 'v/m', 'name': 'M', 'direct': 100.0, 'livekit': 130.0, 'scale': None, 'delta': 30.0}],
        'stt': [{'id': 's/n', 'name': 'N', 'direct': 0.005, 'livekit': 0.00498, 'scale': None, 'delta': -0.4}],
    }
    n = next(s for s in hero_stats(comp) if s['label'] == 'N')
    assert n['detail'] == 'about the same via LiveKit'
    assert n['sign'] == 'flat'


def test_hero_stats_dedupes_by_id_not_display_name():
    # Two different models that share a display name (different modalities) must both be eligible.
    comp: Comparison = {
        'llm': [{'id': 'x/nova', 'name': 'Nova', 'direct': 2.0, 'livekit': 2.0, 'scale': None, 'delta': 0.0}],
        'tts': [{'id': 'y/nova', 'name': 'Nova', 'direct': 10.0, 'livekit': 18.0, 'scale': None, 'delta': 80.0}],
        'stt': [],
    }
    nova_stats = [s for s in hero_stats(comp) if s['label'] == 'Nova']
    assert len(nova_stats) == 2  # deduped by id; name-based dedup would drop one


def test_render_html_stamps_last_updated_date():
    out = render_html(build_catalog([]), build_comparison([]), [], today=date(2026, 6, 5))
    assert 'last updated' in out
    assert 'June 5, 2026' in out


def test_build_site_writes_valid_html(tmp_path: Path):
    out = build_site(tmp_path)
    assert out.exists()
    html = out.read_text()
    assert len(html) > 5000
    # Add-a-provider funnel present.
    assert 'issues/new?template=add-provider.yml' in html
    # Known provider rendered.
    assert 'Anthropic' in html
    # Embedded catalog is valid JSON and has the three modality keys.
    match = re.search(r'<script id="catalog" type="application/json">(.*?)</script>', html, re.DOTALL)
    assert match is not None
    catalog = json.loads(match.group(1))
    assert set(catalog) == {'llm', 'tts', 'stt'}
    assert len(catalog['llm']) > 0
    # LiveKit vs direct comparison: tab present and data embedded for every modality.
    assert 'data-modality="compare"' in html
    cmp_match = re.search(r'<script id="comparison" type="application/json">(.*?)</script>', html, re.DOTALL)
    assert cmp_match is not None
    comparison = json.loads(cmp_match.group(1))
    assert set(comparison) == {'llm', 'tts', 'stt'}
    assert sum(len(rows) for rows in comparison.values()) >= 70  # ~78 LiveKit models
    # Launch hero: headline, quick-start dialog, and embedded balanced stats.
    assert 'Know the real cost' in html
    assert 'id="quickstart"' in html
    assert 'Use in code' in html
    hero_match = re.search(r'<script id="hero-stats" type="application/json">(.*?)</script>', html, re.DOTALL)
    assert hero_match is not None
    stats = json.loads(hero_match.group(1))
    assert stats and any(s['sign'] != 'up' for s in stats)  # non-empty and never all-markups
