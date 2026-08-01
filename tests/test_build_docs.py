"""Tests for the docs generator (prices.build_docs).

Covers the price-shape normalizer, modality detection (including the curated overrides), MDX
rendering, the generated navigation, and a full build against the real prices/data.json so the
committed pages can never silently drift from the catalog they claim to describe.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, cast

import pytest

from prices.build_docs import (
    CATEGORIES,
    DATA_JSON,
    DOCS_DIR,
    INDEX_PAGE,
    MODALITY_OVERRIDES,
    Column,
    Comparison,
    ModelRow,
    _resolve_direct,
    active_columns,
    base_prices,
    build_catalog,
    build_comparison,
    build_docs,
    build_navigation,
    category_tab,
    cell,
    detect_modality,
    fmt_pct,
    fmt_usd,
    inject_blocks,
    mdx_table,
    missing_alias_targets,
    provenance_source,
    render_index_page,
    render_provider_page,
    slug,
    unknown_prefix_providers,
)


def _real_data() -> list[dict[str, Any]]:
    return json.loads(DATA_JSON.read_text())


# ---- price shape normalizer --------------------------------------------------


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


# ---- modality detection ------------------------------------------------------


def test_detect_modality_by_priced_field():
    assert detect_modality({'input_kchars': 0.04}) == 'tts'
    assert detect_modality({'input_audio_kseconds': 0.08}) == 'stt'
    assert detect_modality({'input_mtok': 2.0, 'output_mtok': 8.0}) == 'llm'
    assert detect_modality({}) is None


def test_detect_modality_s2s_needs_both_audio_directions():
    # Bidirectional audio pricing is what makes a model speech-to-speech.
    assert detect_modality({'input_audio_mtok': 32.0, 'output_audio_mtok': 64.0}) == 's2s'
    # Audio in only is a multimodal chat model, not a realtime voice model.
    assert detect_modality({'input_mtok': 5.0, 'input_audio_mtok': 40.0}) == 'llm'
    # Audio out only, with no override, is likewise not s2s.
    assert detect_modality({'input_mtok': 0.6, 'output_audio_mtok': 12.0}) == 'llm'


def test_detect_modality_overrides_win_over_the_field_rule():
    # gpt-4o-transcribe bills audio per token, so the field rule alone would file it under llm and
    # it would disappear from the STT tab.
    audio_tokens = {'input_mtok': 2.5, 'output_mtok': 10.0, 'input_audio_mtok': 6.0}
    assert detect_modality(audio_tokens) == 'llm'
    assert detect_modality(audio_tokens, ('openai', 'gpt-4o-transcribe')) == 'stt'
    assert detect_modality({'output_audio_mtok': 12.0}, ('openai', 'gpt-4o-mini-tts')) == 'tts'
    # A VAD model is priced exactly like STT and is only distinguishable by the override.
    assert detect_modality({'input_audio_kseconds': 0.025}) == 'stt'
    assert detect_modality({'input_audio_kseconds': 0.025}, ('ai_coustics', 'quail-vad-2.0-xxs-16khz')) == 'vad'


def test_modality_overrides_are_exactly_these():
    # The override map is the one place a model's category is decided by hand rather than by its
    # priced fields. Pinning it means a new entry has to be a deliberate, reviewed change.
    assert MODALITY_OVERRIDES == {
        ('openai', 'gpt-4o-transcribe'): 'stt',
        ('openai', 'gpt-4o-mini-transcribe'): 'stt',
        ('openai', 'gpt-4o-mini-tts'): 'tts',
        ('ai_coustics', 'quail-vad-2.0-xxs-16khz'): 'vad',
        ('ai_coustics', 'quail-vf-vad-2.0-s-16khz'): 'vad',
    }


def test_every_override_target_exists_in_the_real_catalog():
    # An override naming a renamed or removed model would silently stop applying, dropping the model
    # back onto the wrong tab with no error anywhere.
    present: set[tuple[str, str]] = set()
    for provider in _real_data():
        models: list[dict[str, Any]] = provider.get('models') or []
        for model in models:
            present.add((str(provider.get('id')), str(model.get('id'))))
    assert set(MODALITY_OVERRIDES) <= present


# ---- catalog -----------------------------------------------------------------


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
    assert [m['id'] for m in catalog['llm'][0]['models']] == ['acme-1']


def test_build_catalog_sorts_models_by_id():
    data: list[dict[str, Any]] = [
        {
            'id': 'acme',
            'name': 'Acme',
            'models': [
                {'id': 'z-model', 'prices': {'input_mtok': 1.0}},
                {'id': 'a-model', 'prices': {'input_mtok': 2.0}},
            ],
        }
    ]
    assert [m['id'] for m in build_catalog(data)['llm'][0]['models']] == ['a-model', 'z-model']


def test_build_catalog_provider_can_span_categories():
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
    row = build_catalog(data)['stt'][0]['models'][0]
    assert row['values']['per_min'] == 0.08 * 60 / 1000  # 0.0048


def test_tts_derives_per_million_chars():
    data: list[dict[str, Any]] = [
        {'id': 'e', 'name': 'E', 'models': [{'id': 'flash', 'prices': {'input_kchars': 0.05}}]}
    ]
    row = build_catalog(data)['tts'][0]['models'][0]
    assert row['values']['per_mchars'] == 50.0


def test_voice_rows_carry_source_and_verified_date():
    data: list[dict[str, Any]] = [
        {
            'id': 'd',
            'name': 'D',
            'models': [
                {
                    'id': 'a-verified',
                    'prices': {'input_audio_kseconds': 0.08},
                    'provenance': {'last_verified': '2026-07-01'},
                },
                {'id': 'b-imported', 'prices': {'input_audio_kseconds': 0.09}, 'provenance': {'source': 'imported'}},
                {'id': 'c-seed', 'prices': {'input_audio_kseconds': 0.10}},
            ],
        }
    ]
    rows = {r['id']: r for r in build_catalog(data)['stt'][0]['models']}
    assert (rows['a-verified']['status'], rows['a-verified']['verified']) == ('verified', '2026-07-01')
    assert (rows['b-imported']['status'], rows['b-imported']['verified']) == ('imported', None)
    assert (rows['c-seed']['status'], rows['c-seed']['verified']) == ('seed', None)


def test_llm_rows_carry_no_source():
    data: list[dict[str, Any]] = [
        {'id': 'o', 'name': 'O', 'models': [{'id': 'gpt', 'prices': {'input_mtok': 2.5, 'output_mtok': 10.0}}]}
    ]
    row = build_catalog(data)['llm'][0]['models'][0]
    assert row['status'] is None
    assert row['verified'] is None


def test_provenance_source_never_computes_staleness():
    # The committed pages must be a pure function of data.json. If this ever returned a
    # wall-clock-dependent value, every page would rewrite itself the day a model went stale and
    # turn an unrelated pull request red.
    assert provenance_source({'last_verified': '2020-01-01'}) == ('verified', '2020-01-01')
    assert provenance_source(None) == ('seed', None)
    assert provenance_source({'source': 'imported'}) == ('imported', None)


def test_markers_flag_tiered_daily_and_voices():
    data: list[dict[str, Any]] = [
        {
            'id': 'm',
            'name': 'M',
            'models': [
                {'id': 'tiered-one', 'prices': {'input_mtok': {'base': 1.0, 'tiers': [{'start': 1, 'price': 2}]}}},
                {'id': 'voiced', 'prices': {'input_kchars': 0.04, 'voice_multipliers': {'default': 1.0, 'p': 1.5}}},
            ],
        }
    ]
    catalog = build_catalog(data)
    assert catalog['llm'][0]['models'][0]['markers'] == ['tiered']
    assert catalog['tts'][0]['models'][0]['markers'] == ['voices']


# ---- real catalog ------------------------------------------------------------


def test_real_catalog_classifies_every_priced_model():
    # Nothing priced may fall through the classifier: a model with no category is a model that
    # silently vanishes from the docs site.
    data = _real_data()
    unclassified: list[str] = []
    for provider in data:
        models: list[dict[str, Any]] = provider.get('models') or []
        for model in models:
            if model.get('deprecated') is True:
                continue
            flat, _, _ = base_prices(model.get('prices'))
            if flat and detect_modality(flat, (str(provider['id']), str(model['id']))) is None:
                unclassified.append(f'{provider["id"]}:{model["id"]}')
    assert unclassified == []


def test_real_catalog_has_every_category_populated():
    catalog = build_catalog(_real_data())
    for category in CATEGORIES:
        assert catalog[category], f'{category} has no providers'
    assert 'anthropic' in {p['id'] for p in catalog['llm']}
    assert 'deepgram' in {p['id'] for p in catalog['stt']}
    assert 'cartesia' in {p['id'] for p in catalog['tts']}


def test_s2s_is_exactly_the_bidirectional_realtime_models():
    # Pinned on purpose: a new realtime model appearing here should be a visible, reviewed change,
    # and a model dropping out means the S2S tab quietly lost a row.
    catalog = build_catalog(_real_data())
    found = {(entry['id'], model['id']) for entry in catalog['s2s'] for model in entry['models']}
    assert found == {
        ('aws', 'amazon.nova-sonic-v1:0'),
        ('google', 'gemini-live-2.5-flash-preview'),
        ('openai', 'gpt-4o-mini-realtime-preview'),
        ('openai', 'gpt-4o-realtime-preview'),
        ('openai', 'gpt-realtime'),
        ('openai', 'gpt-realtime-mini'),
    }


def test_vad_is_the_ai_coustics_models():
    catalog = build_catalog(_real_data())
    assert [entry['id'] for entry in catalog['vad']] == ['ai_coustics']
    assert [m['id'] for m in catalog['vad'][0]['models']] == [
        'quail-vad-2.0-xxs-16khz',
        'quail-vf-vad-2.0-s-16khz',
    ]


def test_ai_coustics_vad_rate_matches_the_published_per_minute_rate():
    # $0.0015/minute at the Startup tier, stored as $ per 1,000 seconds.
    catalog = build_catalog(_real_data())
    for model in catalog['vad'][0]['models']:
        per_min = model['values']['per_min']
        assert per_min is not None
        assert round(per_min, 8) == 0.0015


def test_no_deprecated_model_leaks_into_the_catalog():
    # Model ids are not globally unique (OpenRouter mirrors other providers' ids), so this is
    # checked per (provider id, model id), not globally.
    data = _real_data()
    deprecated: set[tuple[str, str]] = set()
    for provider in data:
        models: list[dict[str, Any]] = provider.get('models') or []
        for model_data in models:
            if model_data.get('deprecated') is True:
                deprecated.add((str(provider['id']), str(model_data['id'])))
    assert deprecated  # data.json has at least one deprecated model today
    catalog = build_catalog(data)
    for entries in catalog.values():
        for entry in entries:
            for model in entry['models']:
                assert (entry['id'], model['id']) not in deprecated


# ---- MDX rendering -----------------------------------------------------------


def test_cell_escapes_mdx_and_table_syntax():
    assert cell('a|b') == r'a\|b'
    assert cell('<script>') == '&lt;script>'
    assert cell('{value}') == '&#123;value}'


def test_fmt_usd_strips_trailing_zeros_and_keeps_small_rates():
    assert fmt_usd(50.0) == '$50'
    assert fmt_usd(1.25) == '$1.25'
    assert fmt_usd(0.0015) == '$0.0015'
    assert fmt_usd(0) == '$0'
    assert fmt_usd(None) == '-'


def test_fmt_pct_never_shows_a_misleading_plus_zero():
    assert fmt_pct(200.0) == '+200.0%'
    assert fmt_pct(-2.0) == '-2.0%'
    assert fmt_pct(0.0) == 'same'
    assert fmt_pct(None) == 'LiveKit only'


def test_mdx_table_aligns_text_then_numbers():
    out = mdx_table(['Model', 'Name', 'Rate'], [['`a`', 'A', '$1']])
    assert out.splitlines()[1] == '| --- | --- | ---: |'
    wide = mdx_table(['Provider', 'Model', 'Name', 'Rate'], [['P', '`a`', 'A', '$1']], text_cols=3)
    assert wide.splitlines()[1] == '| --- | --- | --- | ---: |'


def test_mdx_table_empty_says_so_rather_than_rendering_a_headless_table():
    assert mdx_table(['Model'], []) == '_No models priced yet._'


def _row(**values: float | int | None) -> ModelRow:
    return {'id': 'm', 'name': 'M', 'values': dict(values), 'status': None, 'verified': None, 'markers': []}


def test_active_columns_drops_columns_that_are_none_for_every_row():
    columns = (Column('A', 'a', 'usd'), Column('B', 'b', 'usd'))
    rows = [_row(a=1.0, b=None), _row(a=2.0, b=None)]
    assert [c.header for c in active_columns(rows, columns)] == ['A']
    rows = [_row(a=1.0, b=None), _row(a=2.0, b=3.0)]
    assert [c.header for c in active_columns(rows, columns)] == ['A', 'B']


def test_slug_makes_provider_ids_url_safe():
    assert slug('huggingface_novita') == 'huggingface-novita'
    assert slug('x-ai') == 'x-ai'


def test_render_provider_page_has_frontmatter_and_the_do_not_edit_notice():
    catalog = build_catalog(_real_data())
    entry = next(e for e in catalog['stt'] if e['id'] == 'deepgram')
    page = render_provider_page('stt', entry)
    assert page.startswith('---\n')
    assert 'sidebarTitle: "Deepgram"' in page
    assert 'Do not edit by hand' in page
    assert 'prices/providers/deepgram.yml' in page
    assert '$ / min' in page


def test_llm_pages_carry_no_source_column():
    # LLM rates are cross-checked against aggregators rather than per-model vendor pages, so a
    # per-model source column there would read as low confidence when it means nothing of the sort.
    catalog = build_catalog(_real_data())
    entry = next(e for e in catalog['llm'] if e['id'] == 'anthropic')
    page = render_provider_page('llm', entry)
    assert 'Last verified' not in page
    assert '<Badge' not in page


def test_voice_pages_carry_a_source_badge():
    catalog = build_catalog(_real_data())
    entry = next(e for e in catalog['tts'] if e['id'] == 'elevenlabs')
    page = render_provider_page('tts', entry)
    assert 'Last verified' in page
    assert '<Badge color="green" size="sm">verified</Badge>' in page


def test_token_priced_voice_models_render_in_their_own_table():
    catalog = build_catalog(_real_data())
    entry = next(e for e in catalog['stt'] if e['id'] == 'openai')
    page = render_provider_page('stt', entry)
    assert '## Billed per audio token' in page
    assert 'gpt-4o-transcribe' in page


def test_llm_index_summarises_by_vendor_rather_than_listing_every_model():
    # Over a thousand rows on one page is not readable; the vendor pages carry the detail.
    data = _real_data()
    catalog = build_catalog(data)
    page = render_index_page('llm', catalog['llm'], build_comparison(data)['llm'])
    assert 'Cheapest input $ / Mtok' in page
    assert page.count('\n|') < 100  # one row per provider, not per model
    assert '/llm/openrouter' in page


def test_index_page_lists_every_model_for_small_categories():
    data = _real_data()
    catalog = build_catalog(data)
    page = render_index_page('stt', catalog['stt'], build_comparison(data)['stt'])
    total = sum(len(entry['models']) for entry in catalog['stt'])
    # every model gets a row, minus the token-priced ones which are called out separately
    token_priced = sum(1 for e in catalog['stt'] for m in e['models'] if m['values']['per_min'] is None)
    assert page.count('\n| [') == total - token_priced
    assert 'bill audio as tokens' in page


def test_comparison_section_renders_on_voice_index_pages():
    data = _real_data()
    catalog = build_catalog(data)
    page = render_index_page('tts', catalog['tts'], build_comparison(data)['tts'])
    assert '## Via LiveKit Inference' in page
    assert 'vs direct' in page


# ---- navigation --------------------------------------------------------------


def test_category_tab_groups_a_large_sidebar_and_leaves_a_small_one_flat():
    catalog = build_catalog(_real_data())
    llm = category_tab('llm', catalog['llm'])
    assert llm['tab'] == 'LLM'
    pages = cast('list[Any]', llm['pages'])
    assert pages[0] == f'llm/{INDEX_PAGE}'
    groups = [str(cast('dict[str, Any]', page)['group']) for page in pages if isinstance(page, dict)]
    assert groups == ['Direct vendors', 'Gateways', 'HuggingFace Inference']

    s2s = category_tab('s2s', catalog['s2s'])
    assert all(isinstance(page, str) for page in cast('list[Any]', s2s['pages']))  # only 3 vendors: no group headings


def test_build_navigation_has_the_seven_tabs_in_order():
    nav = build_navigation(build_catalog(_real_data()))
    assert [tab['tab'] for tab in nav['tabs']] == ['Overview', 'STT', 'LLM', 'TTS', 'S2S', 'VAD', 'Add Yours']


def _nav_pages(pages: list[Any]) -> list[str]:
    flat: list[str] = []
    for page in pages:
        if isinstance(page, str):
            flat.append(page)
        else:
            flat += _nav_pages(page['pages'])
    return flat


def test_every_navigation_entry_resolves_to_a_committed_page():
    # Mintlify fails the build on a nav entry with no file, so this catches it before a deploy does.
    config = json.loads((DOCS_DIR / 'docs.json').read_text())
    for tab in config['navigation']['tabs']:
        for page in _nav_pages(tab['pages']):
            assert (DOCS_DIR / f'{page}.mdx').is_file(), f'{page}.mdx missing'


def test_no_internal_link_points_at_a_missing_page():
    # A broken internal link fails the Mintlify build. This also covers the hand-written pages,
    # which the generator never touches and so nothing else would catch.
    pages = {str(path.relative_to(DOCS_DIR).with_suffix('')) for path in DOCS_DIR.rglob('*.mdx')}
    broken: list[str] = []
    for page in sorted(DOCS_DIR.rglob('*.mdx')):
        for target in re.findall(r'\]\((/[^)#\s]*)', page.read_text()):
            if target.lstrip('/') not in pages:
                broken.append(f'{page.relative_to(DOCS_DIR)} -> {target}')
    assert broken == []


def test_every_committed_page_is_reachable_from_the_navigation():
    # The other direction: an orphaned page is dead weight Mintlify warns about.
    config = json.loads((DOCS_DIR / 'docs.json').read_text())
    listed = {page for tab in config['navigation']['tabs'] for page in _nav_pages(tab['pages'])}
    on_disk = {str(path.relative_to(DOCS_DIR).with_suffix('')) for path in DOCS_DIR.rglob('*.mdx')}
    assert on_disk == listed


# ---- generated-block injection ----------------------------------------------


def test_inject_blocks_replaces_between_markers(tmp_path: Path):
    page = tmp_path / 'page.mdx'
    page.write_text('before\n{/* generated:counts start */}\nold\n{/* generated:counts end */}\nafter\n')
    assert inject_blocks(page, {'counts': 'new'}) is True
    assert 'new' in page.read_text()
    assert 'old' not in page.read_text()
    assert page.read_text().startswith('before')
    assert page.read_text().endswith('after\n')


def test_inject_blocks_is_idempotent(tmp_path: Path):
    page = tmp_path / 'page.mdx'
    page.write_text('{/* generated:counts start */}\nold\n{/* generated:counts end */}\n')
    inject_blocks(page, {'counts': 'new'})
    assert inject_blocks(page, {'counts': 'new'}) is False  # second run changes nothing


def test_inject_blocks_raises_when_the_marker_is_missing(tmp_path: Path):
    # Silently dropping a generated number is how a docs site starts lying.
    page = tmp_path / 'page.mdx'
    page.write_text('no markers here\n')
    with pytest.raises(ValueError, match='no `generated:counts` marker pair'):
        inject_blocks(page, {'counts': 'new'})


# ---- full build --------------------------------------------------------------


def test_build_docs_is_deterministic_and_committed_output_is_in_sync():
    """The drift gate.

    Regenerate the whole site into a scratch copy and byte-compare against what is committed. A
    failure here means someone edited a price without running `make build`, so the published docs
    would show a number the library no longer agrees with.
    """
    scratch = Path(__import__('tempfile').mkdtemp()) / 'docs'
    shutil.copytree(DOCS_DIR, scratch)
    build_docs(scratch)

    committed = sorted(path.relative_to(DOCS_DIR) for path in DOCS_DIR.rglob('*.mdx'))
    regenerated = sorted(path.relative_to(scratch) for path in scratch.rglob('*.mdx'))
    assert committed == regenerated, 'a page was added or removed by regeneration'
    for relative in committed:
        assert (DOCS_DIR / relative).read_text() == (scratch / relative).read_text(), (
            f'{relative} is stale: run `make build`'
        )
    assert json.loads((DOCS_DIR / 'docs.json').read_text()) == json.loads((scratch / 'docs.json').read_text())


def test_build_docs_prunes_a_page_whose_provider_no_longer_prices_anything(tmp_path: Path):
    scratch = tmp_path / 'docs'
    shutil.copytree(DOCS_DIR, scratch)
    orphan = scratch / 'stt' / 'gone-vendor.mdx'
    orphan.write_text('stale page\n')
    build_docs(scratch)
    assert not orphan.exists()


def test_build_docs_leaves_hand_written_pages_alone(tmp_path: Path):
    scratch = tmp_path / 'docs'
    shutil.copytree(DOCS_DIR, scratch)
    before = (scratch / 'how-fresh.mdx').read_text()
    build_docs(scratch)
    assert (scratch / 'how-fresh.mdx').read_text() == before


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
    assert missing_alias_targets(_real_data()) == []


def test_missing_alias_targets_detects_renamed_target():
    broken = [p for p in _real_data() if p.get('id') != 'cartesia']  # drop the provider holding sonic-3
    assert 'cartesia/sonic-2' in missing_alias_targets(broken)


def test_livekit_prefix_providers_all_exist_in_catalog():
    # Guards the x_ai/x-ai class of typo: a prefix mapped to a missing provider would silently make
    # every model with that prefix show as LiveKit-only even when a direct baseline exists.
    assert unknown_prefix_providers(_real_data()) == []


def test_elevenlabs_flash_and_turbo_v2_resolve_to_their_own_direct_entry():
    # Flash/Turbo v2 must compare against their OWN direct price, not the v2.5 entry. A cross-version
    # alias made the site report a "+233% Flash v2" markup computed against Flash v2.5's price, which
    # is indefensible (two different models). They now have explicit direct entries.
    assert _resolve_direct('elevenlabs/eleven_flash_v2') == ('elevenlabs', 'eleven_flash_v2')
    assert _resolve_direct('elevenlabs/eleven_turbo_v2') == ('elevenlabs', 'eleven_turbo_v2')


def test_elevenlabs_flash_and_turbo_v2_are_distinct_priced_direct_models():
    # The v2 and v2.5 entries share a price ($0.045/kchar) and an id prefix, so the collapse hook
    # would merge v2.5 into v2 and drop it. Both must survive in the catalog so each LiveKit model
    # compares against its matching version.
    elevenlabs = next(p for p in _real_data() if p['id'] == 'elevenlabs')
    priced = {m['id']: m for m in elevenlabs['models'] if m.get('prices')}
    for model_id in ('eleven_flash_v2', 'eleven_flash_v2_5', 'eleven_turbo_v2', 'eleven_turbo_v2_5'):
        assert model_id in priced, f'{model_id} missing from the direct ElevenLabs catalog'
        assert priced[model_id]['prices'].get('input_kchars') is not None


def test_elevenlabs_flash_v2_markup_is_a_true_same_model_comparison():
    # Flash v2's baseline must come from Flash v2's OWN direct entry (guaranteed by the resolve test
    # above), not a cross-version proxy. Flash v2 and v2.5 bill the same direct rate, so both rows show
    # the same real markup. Value-independent on purpose: the exact percentage is not pinned, because it
    # tracks whatever direct-rate basis the catalog uses and would churn on any legitimate reprice.
    comp = build_comparison(_real_data())
    v2 = next(r for r in comp['tts'] if r['id'] == 'elevenlabs/eleven_flash_v2')
    v2_5 = next(r for r in comp['tts'] if r['id'] == 'elevenlabs/eleven_flash_v2_5')
    assert v2['direct'] is not None and v2_5['direct'] is not None
    assert v2['direct'] == v2_5['direct']  # both Flash models bill the same direct rate
    assert v2['delta'] == v2_5['delta']
    assert v2['delta'] is not None and v2['delta'] > 0  # a real markup against its own baseline


def test_livekit_xai_grok_resolves_to_a_direct_baseline():
    # Regression: xai/grok-4-1-fast was wrongly LiveKit-only because the prefix mapped to 'x_ai'
    # rather than the real provider id 'x-ai'. It has an exact direct match (pass-through).
    comp = build_comparison(_real_data())
    grok = next(r for r in comp['llm'] if r['id'] == 'xai/grok-4-1-fast-non-reasoning')
    assert grok['direct'] is not None  # the regression: was wrongly None
    assert grok['direct'] == grok['livekit']  # resolved to the same-priced direct model (pass-through)
    latest = next(r for r in comp['llm'] if r['id'] == 'openai/gpt-5.3-chat-latest')
    assert latest['direct'] is not None


def test_comparison_has_no_s2s_or_vad_rows():
    # Neither has a single comparable unit, and LiveKit prices neither today. A row appearing here
    # would render with an empty rate column.
    comp: Comparison = build_comparison(_real_data())
    assert comp['s2s'] == []
    assert comp['vad'] == []
