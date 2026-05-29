"""Tests for the documentation-site generator (prices.build_site).

Covers the price-shape normalizer, modality detection, exclusion rules, and a
full build against the real prices/data.json so the page can never silently break
when the catalog changes shape.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from prices.build_site import (
    DATA_JSON,
    base_prices,
    build_catalog,
    build_site,
    detect_modality,
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
