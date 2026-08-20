"""Generate the Mintlify documentation pages from the built price data.

Reads the committed ``prices/data.json`` (the canonical, post-processed catalog) and writes one
MDX page per (category, provider) under ``docs/``, a per-category index page, and the
``navigation`` block of ``docs/docs.json``.

Everything under ``docs/stt/``, ``docs/llm/``, ``docs/tts/``, ``docs/s2s/``, ``docs/vad/`` and ``docs/agent/`` is
generated: to change a price, edit the provider YAML and re-run ``make build``, never the MDX. The
hand-written pages (``docs/index.mdx``, ``docs/how-fresh.mdx``, ``docs/contribute.mdx``) carry
generated numbers between ``{/* generated:<key> start */}`` markers instead.

The pages never drift from the library because they are generated from the same data the package
ships, mirroring ``inject_providers.py`` for the README provider list.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal, NamedTuple, TypedDict, cast

from .utils import package_dir, root_dir

DATA_JSON = package_dir / 'data.json'
DOCS_DIR = root_dir / 'docs'

REPO_URL = 'https://github.com/mahimailabs/voice-prices'
ADD_PROVIDER_URL = f'{REPO_URL}/issues/new?template=add-provider.yml'
PROVIDER_YAML_URL = f'{REPO_URL}/blob/main/prices/providers'

Modality = Literal['stt', 'llm', 'tts', 's2s', 'vad', 'agent']

#: Tab order on the docs site. Voice first, deliberately: this is a voice pricing project.
CATEGORIES: tuple[Modality, ...] = ('stt', 'llm', 'tts', 's2s', 'vad', 'agent')

# Fields on a price block that are not per-unit rates and must not be treated as one.
_NON_RATE_FIELDS = {'voice_multipliers'}

# Models whose priced fields do not reveal their modality. Two kinds land here: models that bill
# audio per *token* rather than per audio-second (STT) or per character (TTS), and VAD models,
# which bill per audio-minute exactly like STT but detect speech rather than transcribe it.
# Reviewed by hand; `test_modality_overrides_are_exactly_these` pins the map so it cannot grow
# silently and quietly move a model to the wrong tab.
MODALITY_OVERRIDES: dict[tuple[str, str], Modality] = {
    ('openai', 'gpt-4o-transcribe'): 'stt',
    ('openai', 'gpt-4o-mini-transcribe'): 'stt',
    ('openai', 'gpt-4o-mini-tts'): 'tts',
    ('ai_coustics', 'quail-vad-2.0-xxs-16khz'): 'vad',
    ('ai_coustics', 'quail-vf-vad-2.0-s-16khz'): 'vad',
}

# Sidebar grouping. Gateways resell other vendors' models; HuggingFace Inference is one provider
# per partner and would otherwise bury the direct vendors under eleven near-identical entries.
GATEWAY_PROVIDERS = {'livekit', 'livekit-scale', 'openrouter'}
HUGGINGFACE_PREFIX = 'huggingface_'
GROUP_ORDER = ('Direct vendors', 'Gateways', 'HuggingFace Inference')

# Below this many providers a group heading is noise, so the sidebar stays flat.
MIN_PROVIDERS_FOR_GROUPS = 5


class Column(NamedTuple):
    """One table column: header text, the key it reads from a row's ``values``, and its format."""

    header: str
    key: str
    kind: Literal['usd', 'int']


# Per-category columns, in display order. A column whose value is None for every row on a page is
# dropped from that page, so no table ever carries a column of dashes.
CATEGORY_COLUMNS: dict[Modality, tuple[Column, ...]] = {
    'stt': (Column('$ / min', 'per_min', 'usd'),),
    'tts': (Column('$ / 1M chars', 'per_mchars', 'usd'),),
    'vad': (Column('$ / min', 'per_min', 'usd'),),
    'agent': (Column('$ / min', 'per_min', 'usd'),),
    'llm': (
        Column('Input $ / Mtok', 'input_mtok', 'usd'),
        Column('Output $ / Mtok', 'output_mtok', 'usd'),
        Column('Cached input $ / Mtok', 'cache_read_mtok', 'usd'),
        Column('Context', 'context_window', 'int'),
    ),
    's2s': (
        Column('Audio in $ / Mtok', 'input_audio_mtok', 'usd'),
        Column('Audio out $ / Mtok', 'output_audio_mtok', 'usd'),
        Column('Cached audio in $ / Mtok', 'cache_audio_read_mtok', 'usd'),
        Column('Text in $ / Mtok', 'input_mtok', 'usd'),
        Column('Text out $ / Mtok', 'output_mtok', 'usd'),
    ),
}

# Voice models billed per audio token render in a second table, because they share no unit with
# the main one. Keyed by the category whose page they appear on.
TOKEN_COLUMNS: dict[Modality, tuple[Column, ...]] = {
    'stt': (
        Column('Audio in $ / Mtok', 'input_audio_mtok', 'usd'),
        Column('Text in $ / Mtok', 'input_mtok', 'usd'),
        Column('Text out $ / Mtok', 'output_mtok', 'usd'),
    ),
    'tts': (
        Column('Audio out $ / Mtok', 'output_audio_mtok', 'usd'),
        Column('Text in $ / Mtok', 'input_mtok', 'usd'),
    ),
}


class CategoryMeta(NamedTuple):
    title: str
    tab: str
    icon: str
    unit: str
    note: str = ''


CATEGORY_META: dict[Modality, CategoryMeta] = {
    'stt': CategoryMeta(
        title='Speech-to-text pricing',
        tab='STT',
        icon='mic',
        unit='Priced from `input_audio_kseconds`, US dollars per 1,000 seconds of input audio, and '
        'shown converted to a per-minute rate for comparison. Vendors quote in whatever unit they '
        'prefer (per minute, per second, per hour), so the figure here is a conversion, not a quote.',
        note='Streaming and batch ship as separate rows, as do language tiers, because vendors price them separately.',
    ),
    'llm': CategoryMeta(
        title='LLM pricing',
        tab='LLM',
        icon='brain',
        unit='Priced from `input_mtok` and `output_mtok`, US dollars per 1,000,000 tokens.',
        note='LLM rows carry no per-model verification status. Unlike voice rates, they are '
        'cross-checked against external aggregators rather than against one vendor page each. '
        '[How Fresh?](/how-fresh) explains the difference.',
    ),
    'tts': CategoryMeta(
        title='Text-to-speech pricing',
        tab='TTS',
        icon='volume-2',
        unit='Priced from `input_kchars`, US dollars per 1,000 characters of input text, and shown '
        'converted to a per-1M-character rate for comparison. Vendors quote per character, per 1,000 '
        'or per 1M, so the figure here is a conversion, not a quote.',
        note='Vendors that sell credits rather than characters are converted at a named plan tier, '
        'recorded in `price_comments` on the model.',
    ),
    's2s': CategoryMeta(
        title='Speech-to-speech pricing',
        tab='S2S',
        icon='audio-waveform',
        unit='Realtime models that bill audio as tokens in both directions. All rates are US dollars '
        'per 1,000,000 tokens.',
        note='A model lands here only when it prices audio in **and** audio out. A chat model that '
        'merely accepts audio input stays under [LLM](/llm/all-models). Platforms that sell one bundled '
        'session minute instead of audio tokens are under [Agents](/agent/all-models).',
    ),
    'agent': CategoryMeta(
        title='Voice agent pricing',
        tab='Agents',
        icon='bot',
        unit='Priced from `agent_kminutes`, US dollars per 1,000 minutes of agent session time, and '
        'shown per minute. These platforms sell one blended rate covering speech-to-text, the model, '
        'text-to-speech and orchestration together.',
        note='**These rates are not comparable with the rest of the catalog.** A bundled minute prices '
        'whatever the platform chose to put in it, in proportions it usually does not disclose, and '
        'several platforms exclude the model or telephony and bill those at cost on top. Read '
        '`price_comments` on each row for what the number actually covers before comparing anything.',
    ),
    'vad': CategoryMeta(
        title='Voice activity detection pricing',
        tab='VAD',
        icon='activity',
        unit='Priced from `input_audio_kseconds`, US dollars per 1,000 seconds of processed audio. Shown per minute.',
        note='Voice activity detection is usually free. Silero VAD, WebRTC VAD, and the LiveKit turn '
        'detector all run locally and cost CPU rather than dollars per minute, so they carry no rows '
        'here. Only hosted or licensed VAD with a published rate appears below.',
    ),
}

GENERATED_NOTICE = '{/* Generated by `make build-docs` from prices/data.json. Do not edit by hand. */}'

# The per-category landing page. Deliberately not named `index`: whether a docs builder resolves
# `llm/index` to `/llm` or to `/llm/index` is builder-specific, and a guess there breaks every
# cross-page link. An explicit name is unambiguous under either behaviour.
INDEX_PAGE = 'all-models'


# --- LiveKit Inference vs direct-vendor comparison ---------------------------

# LiveKit slug prefixes whose direct-catalog provider id differs from the prefix.
_LIVEKIT_PREFIX_TO_PROVIDER = {'xai': 'x-ai', 'deepseek-ai': 'deepseek'}

# Curated overrides where a LiveKit model maps to a direct-catalog model whose id is not just the
# slug's suffix. Reviewed by hand; everything not covered here resolves by prefix + exact suffix,
# and anything that still does not resolve (Speechmatics, Inworld, Rime, xAI voice) renders as
# LiveKit-only.
LIVEKIT_DIRECT_ALIASES: dict[str, tuple[str, str]] = {
    # AssemblyAI: the single direct Universal model is the closest baseline for the streaming family.
    'assemblyai/u3-rt-pro': ('assemblyai', 'universal-2'),
    'assemblyai/universal-streaming': ('assemblyai', 'universal-2'),
    'assemblyai/universal-streaming-multilingual': ('assemblyai', 'universal-2'),
    # Cartesia: the direct catalog carries only sonic-3.
    'cartesia/sonic-2': ('cartesia', 'sonic-3'),
    'cartesia/sonic-3-2025-10-27': ('cartesia', 'sonic-3'),
    'cartesia/sonic-3-2026-01-12': ('cartesia', 'sonic-3'),
    'cartesia/sonic-3-latest': ('cartesia', 'sonic-3'),
    'cartesia/sonic-3.5': ('cartesia', 'sonic-3'),
    'cartesia/sonic-3.5-2026-05-04': ('cartesia', 'sonic-3'),
    'cartesia/sonic-latest': ('cartesia', 'sonic-3'),
    'cartesia/sonic-turbo': ('cartesia', 'sonic-3'),
    # Deepgram model variants share their base model's direct rate.
    'deepgram/nova-2-conversationalai': ('deepgram', 'nova-2'),
    'deepgram/nova-2-medical': ('deepgram', 'nova-2'),
    'deepgram/nova-2-phonecall': ('deepgram', 'nova-2'),
    'deepgram/nova-3-medical': ('deepgram', 'nova-3'),
    'deepgram/nova-3-multi': ('deepgram', 'nova-3-multilingual'),
    'deepgram/flux-general-en': ('deepgram', 'flux-general'),
    'deepgram/flux-general-multi': ('deepgram', 'flux-general'),
    # ElevenLabs Flash/Turbo v2 are NOT aliased to v2.5: they have their own direct entries at the
    # same 0.5 credits/char rate, so each version compares against itself (no cross-version proxy).
    # OpenAI chat-latest aliases map to their numbered direct model.
    'openai/gpt-5.1-chat-latest': ('openai', 'gpt-5.1'),
    'openai/gpt-5.2-chat-latest': ('openai', 'gpt-5.2'),
    'openai/gpt-5.3-chat-latest': ('openai', 'gpt-5.3'),
}


class ModelRow(TypedDict):
    """A single model's row. ``values`` holds every candidate column; the page picks which apply."""

    id: str
    name: str
    values: dict[str, float | int | None]
    status: str | None
    verified: str | None
    markers: list[str]


class ProviderEntry(TypedDict):
    id: str
    name: str
    models: list[ModelRow]
    pricing_tier: str | None


Catalog = dict[Modality, list[ProviderEntry]]


class ComparisonRow(TypedDict):
    id: str
    name: str
    direct: float | None
    livekit: float | None
    scale: float | None
    delta: float | None


Comparison = dict[Modality, list[ComparisonRow]]


def base_prices(prices: Any) -> tuple[dict[str, float], bool, bool]:
    """Normalize a model's ``prices`` to a flat ``{field: rate}`` dict plus flags.

    Handles the three shapes seen in ``data.json``:

    1. flat dict: ``{field: scalar}``
    2. tiered: ``{field: {base, tiers: [...]}}`` -> read ``.base``, set ``is_tiered``
    3. conditional list: ``[{prices, constraint?}, ...]`` -> use the first block with
       no constraint as the base, set ``is_conditional`` when a constrained block exists

    Returns ``(flat, is_tiered, is_conditional)``. The flags drive the row markers;
    the base rate alone cannot reveal a tier or a time window.
    """
    is_conditional = False
    block: Any = prices

    if isinstance(prices, list):
        blocks = cast('list[Any]', prices)
        is_conditional = any(isinstance(b, dict) and cast('dict[str, Any]', b).get('constraint') for b in blocks)
        base: Any = None
        for b in blocks:
            if isinstance(b, dict) and not cast('dict[str, Any]', b).get('constraint'):
                base = cast('dict[str, Any]', b).get('prices')
                break
        if base is None and blocks:
            first = blocks[0]
            base = cast('dict[str, Any]', first).get('prices') if isinstance(first, dict) else None
        block = base

    flat: dict[str, float] = {}
    is_tiered = False
    if isinstance(block, dict):
        for field, value in cast('dict[str, Any]', block).items():
            if field in _NON_RATE_FIELDS:
                continue
            if isinstance(value, dict):
                tiered_base = cast('dict[str, Any]', value).get('base')
                if isinstance(tiered_base, int | float):
                    flat[field] = float(tiered_base)
                    is_tiered = True
            elif isinstance(value, int | float) and not isinstance(value, bool):
                flat[field] = float(value)
    return flat, is_tiered, is_conditional


def detect_modality(flat: dict[str, float], ref: tuple[str, str] | None = None) -> Modality | None:
    """Classify a model by its priced fields. Returns None for unpriced models.

    ``ref`` is the ``(provider_id, model_id)`` pair, consulted only for the handful of models in
    ``MODALITY_OVERRIDES`` whose priced fields do not reveal what the model does.

    Speech-to-speech is checked first and requires audio pricing in *both* directions, so an
    audio-in chat model (``gpt-4o-audio-preview``) or an audio-in multimodal LLM
    (``gemini-2.5-flash``) stays an LLM rather than being mislabelled realtime.
    """
    if ref is not None and ref in MODALITY_OVERRIDES:
        return MODALITY_OVERRIDES[ref]
    # Checked before everything else: a bundled agent minute is the whole price of the call, so a
    # platform that also lists a component rate must still be filed as an agent, not as its parts.
    if 'agent_kminutes' in flat:
        return 'agent'
    if 'input_audio_mtok' in flat and 'output_audio_mtok' in flat:
        return 's2s'
    if 'input_kchars' in flat or 'output_audio_kseconds' in flat:
        return 'tts'
    if 'input_audio_kseconds' in flat:
        return 'stt'
    if any(field.endswith('_mtok') for field in flat):
        return 'llm'
    return None


def _model_row(model: dict[str, Any], flat: dict[str, float], tiered: bool, daily: bool) -> ModelRow:
    """Collect every candidate column value for one model. Column choice happens per page."""
    audio_kseconds = flat.get('input_audio_kseconds')
    agent_kminutes = flat.get('agent_kminutes')
    kchars = flat.get('input_kchars')
    context = model.get('context_window')
    prices = model.get('prices')
    has_voices = isinstance(prices, dict) and bool(cast('dict[str, Any]', prices).get('voice_multipliers'))

    provenance = model.get('provenance')
    api_backed = isinstance(provenance, dict) and bool(cast('dict[str, Any]', provenance).get('api_backed'))
    # A rate the vendor publishes as its own estimate rather than the meter it bills on. Without a
    # marker the docs would print it in the same column, same format, as a billed rate, which is
    # the misreading `provenance.estimated_fields` exists to prevent.
    raw_estimated = cast('dict[str, Any]', provenance).get('estimated_fields') if isinstance(provenance, dict) else None
    estimated_fields: set[str] = (
        {str(name) for name in cast('list[object]', raw_estimated)} if isinstance(raw_estimated, list) else set()
    )

    markers: list[str] = []
    if api_backed:
        markers.append('api')
    if tiered:
        markers.append('tiered')
    if daily:
        markers.append('daily')
    if has_voices:
        markers.append('voices')

    per_min: float | None = None
    per_min_estimated = False
    if audio_kseconds is not None:
        per_min = audio_kseconds * 60 / 1000
        per_min_estimated = 'input_audio_kseconds' in estimated_fields
    elif agent_kminutes is not None:
        per_min = agent_kminutes / 1000
        per_min_estimated = 'agent_kminutes' in estimated_fields

    # Only mark it when the estimate is a number this page actually displays. A model whose
    # estimated field is not rendered here would otherwise carry a footnote pointing at nothing.
    if per_min_estimated or (kchars is not None and 'input_kchars' in estimated_fields):
        markers.append('estimated')

    values: dict[str, float | int | None] = {
        'per_min': per_min,
        'per_mchars': kchars * 1000 if kchars is not None else None,
        'context_window': context if isinstance(context, int) else None,
    }
    for field in (
        'input_mtok',
        'output_mtok',
        'cache_read_mtok',
        'input_audio_mtok',
        'output_audio_mtok',
        'cache_audio_read_mtok',
    ):
        values[field] = flat.get(field)

    return {
        'id': str(model.get('id', '')),
        'name': str(model.get('name') or model.get('id') or ''),
        'values': values,
        'status': None,
        'verified': None,
        'markers': markers,
    }


def provenance_source(provenance: Any) -> tuple[str, str | None]:
    """The time-independent half of the freshness signal: ``(source, last_verified)``.

    Deliberately does NOT compute ``stale``. Staleness is a function of today's date, and these
    pages are committed to the repository: a wall-clock-dependent page would rewrite itself the day
    any model crossed its threshold, turning an unrelated pull request red. The live stale/fresh
    answer comes from ``voice_prices.model_freshness`` at read time, which is what /how-fresh
    documents. Here we publish what a human actually confirmed, and when.
    """
    if not isinstance(provenance, dict):
        return 'seed', None
    prov = cast('dict[str, Any]', provenance)
    last_verified = prov.get('last_verified')
    if isinstance(last_verified, str) and last_verified:
        return 'verified', last_verified
    return ('imported' if prov.get('source') == 'imported' else 'seed'), None


def build_catalog(data: list[dict[str, Any]]) -> Catalog:
    """Build the per-category catalog from the raw data.json providers list.

    Excludes unpriced (``prices == {}``) and deprecated models. A provider appears under each
    category it has at least one model in (e.g. OpenAI under stt + llm + tts + s2s). Providers are
    sorted by id; models are sorted by id within a provider, matching the YAML house rule.

    Voice rows also carry where their rate came from and when a human last confirmed it. LLM rows do
    not: most LLM entries are not individually source-tracked, so a per-model source there would
    read as low confidence when it only means the rate is cross-checked a different way.
    """
    catalog: Catalog = {category: [] for category in CATEGORIES}

    for provider in sorted(data, key=lambda p: str(p.get('id', ''))):
        provider_id = str(provider.get('id', ''))
        provider_name = str(provider.get('name') or provider.get('id') or '')
        models = provider.get('models')
        if not isinstance(models, list):
            continue

        by_category: dict[Modality, list[ModelRow]] = {category: [] for category in CATEGORIES}
        for raw_model in cast('list[Any]', models):
            if not isinstance(raw_model, dict):
                continue
            model = cast('dict[str, Any]', raw_model)
            if model.get('deprecated') is True:
                continue
            flat, tiered, daily = base_prices(model.get('prices'))
            if not flat:
                continue
            category = detect_modality(flat, (provider_id, str(model.get('id', ''))))
            if category is None:
                continue
            row = _model_row(model, flat, tiered, daily)
            if category != 'llm':
                row['status'], row['verified'] = provenance_source(model.get('provenance'))
            by_category[category].append(row)

        for category, rows in by_category.items():
            if rows:
                rows.sort(key=lambda r: r['id'])
                catalog[category].append(
                    {
                        'id': provider_id,
                        'name': provider_name,
                        'models': rows,
                        'pricing_tier': provider.get('pricing_tier'),
                    }
                )

    return catalog


def _resolve_direct(slug_: str) -> tuple[str, str] | None:
    """Map a LiveKit slug to its direct-catalog ``(provider_id, model_id)``, if any."""
    if slug_ in LIVEKIT_DIRECT_ALIASES:
        return LIVEKIT_DIRECT_ALIASES[slug_]
    prefix, sep, rest = slug_.partition('/')
    if not sep:
        return None
    return _LIVEKIT_PREFIX_TO_PROVIDER.get(prefix, prefix), rest


def _primary_rate(category: Modality, flat: dict[str, float] | None) -> float | None:
    """The single comparable rate per category: STT $/min, TTS $/1M chars, LLM input $/Mtok."""
    if not flat:
        return None
    if category in ('stt', 'vad'):
        rate = flat.get('input_audio_kseconds')
        return rate * 60 / 1000 if rate is not None else None
    if category == 'agent':
        rate = flat.get('agent_kminutes')
        return rate / 1000 if rate is not None else None
    if category == 'tts':
        rate = flat.get('input_kchars')
        return rate * 1000 if rate is not None else None
    if category == 's2s':
        return None
    return flat.get('input_mtok')


def _round(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def build_comparison(data: list[dict[str, Any]]) -> Comparison:
    """Pair every LiveKit Inference model with its direct-vendor price for the comparison tables.

    Returns per-category rows with the LiveKit Build/Ship rate, the Scale rate (None when the model
    is not separately discounted and falls back), the direct-vendor rate (None for LiveKit-only
    providers: Speechmatics, Inworld, Rime, xAI voice), and the Build/Ship-vs-direct delta percent.
    All rates are in the category's primary display unit.
    """
    index: dict[tuple[str, str], dict[str, float]] = {}
    for provider in data:
        provider_id = str(provider.get('id', ''))
        for raw_model in cast('list[Any]', provider.get('models') or []):
            if isinstance(raw_model, dict):
                model = cast('dict[str, Any]', raw_model)
                flat, _, _ = base_prices(model.get('prices'))
                index[(provider_id, str(model.get('id', '')))] = flat

    comparison: Comparison = {category: [] for category in CATEGORIES}
    livekit = next((p for p in data if p.get('id') == 'livekit'), None)
    if livekit is None:
        return comparison

    for raw_model in cast('list[Any]', livekit.get('models') or []):
        model = cast('dict[str, Any]', raw_model)
        slug_ = str(model.get('id', ''))
        flat = index.get(('livekit', slug_), {})
        category = detect_modality(flat, ('livekit', slug_))
        if category is None:
            continue
        livekit_rate = _primary_rate(category, flat)
        if livekit_rate is None:
            continue
        scale_rate = _primary_rate(category, index.get(('livekit-scale', slug_)))
        direct_ref = _resolve_direct(slug_)
        direct_rate = _primary_rate(category, index.get(direct_ref)) if direct_ref else None
        delta = round((livekit_rate - direct_rate) / direct_rate * 100, 1) if direct_rate else None
        comparison[category].append(
            {
                'id': slug_,
                'name': str(model.get('name') or slug_),
                'direct': _round(direct_rate),
                'livekit': _round(livekit_rate),
                'scale': _round(scale_rate),
                'delta': delta,
            }
        )
    return comparison


def missing_alias_targets(data: list[dict[str, Any]]) -> list[str]:
    """LiveKit alias slugs whose curated direct-catalog target model is absent from `data`.

    A non-empty result means a `LIVEKIT_DIRECT_ALIASES` entry points at a renamed or removed direct
    model, so the comparison would silently fall back to "LiveKit-only" and lose that baseline.
    `build_docs` surfaces this as a warning so it is caught rather than degrading quietly.
    """
    present: set[tuple[str, str]] = set()
    for provider in data:
        provider_id = str(provider.get('id', ''))
        for raw_model in cast('list[Any]', provider.get('models') or []):
            if isinstance(raw_model, dict):
                present.add((provider_id, str(cast('dict[str, Any]', raw_model).get('id', ''))))
    return sorted(slug_ for slug_, target in LIVEKIT_DIRECT_ALIASES.items() if target not in present)


def unknown_prefix_providers(data: list[dict[str, Any]]) -> list[str]:
    """Prefix-map entries whose target provider id is not in the catalog.

    A non-empty result means a LiveKit slug prefix is mapped to a provider that does not exist (a
    typo like ``x_ai`` for ``x-ai``), so every model with that prefix silently falls back to
    "LiveKit-only" even when a direct baseline exists. ``build_docs`` warns so it cannot recur quietly.
    """
    provider_ids = {str(provider.get('id', '')) for provider in data}
    return sorted(
        f'{prefix} -> {prov}' for prefix, prov in _LIVEKIT_PREFIX_TO_PROVIDER.items() if prov not in provider_ids
    )


# --- MDX rendering -----------------------------------------------------------


def slug(provider_id: str) -> str:
    """URL-safe page name for a provider id (``huggingface_novita`` -> ``huggingface-novita``)."""
    return provider_id.replace('_', '-')


def cell(text: str) -> str:
    """Escape a value for a markdown table cell inside MDX.

    A pipe would split the cell; ``<`` and ``{`` are JSX syntax and would fail the Mintlify build.
    """
    return text.replace('|', r'\|').replace('<', '&lt;').replace('{', '&#123;')


def fmt_usd(value: float | int | None) -> str:
    """Render a dollar rate without trailing zeros. Rates span $0.0000042 to $600 per unit."""
    if value is None:
        return '-'
    if value == 0:
        return '$0'
    text = f'{value:.8f}'.rstrip('0').rstrip('.')
    return f'${text}'


def fmt_int(value: float | int | None) -> str:
    return '-' if value is None else f'{int(value):,}'


def fmt_pct(value: float | None) -> str:
    """Signed percent. ``+0%`` would read as a markup, so an exact match says so instead."""
    if value is None:
        return 'LiveKit only'
    if round(value, 1) == 0:
        return 'same'
    return f'{value:+.1f}%'


def status_badge(status: str | None) -> str:
    if status is None:
        return '-'
    colors = {'verified': 'green', 'imported': 'gray', 'seed': 'gray'}
    return f'<Badge color="{colors.get(status, "gray")}" size="sm">{status}</Badge>'


def mdx_table(headers: list[str], rows: list[list[str]], text_cols: int = 2) -> str:
    """A GitHub-flavoured pipe table. The first ``text_cols`` columns are left-aligned, rest right."""
    if not rows:
        return '_No models priced yet._'
    aligns = ['---' if index < text_cols else '---:' for index in range(len(headers))]
    lines = ['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(aligns) + ' |']
    lines += ['| ' + ' | '.join(row) + ' |' for row in rows]
    return '\n'.join(lines)


def active_columns(rows: list[ModelRow], columns: tuple[Column, ...]) -> list[Column]:
    """Drop any column that is None for every row, so no table carries a column of dashes."""
    return [column for column in columns if any(row['values'].get(column.key) is not None for row in rows)]


def _marker_suffix(row: ModelRow) -> str:
    return f' <sup>{",".join(row["markers"])}</sup>' if row['markers'] else ''


def _markers_footnote(rows: list[ModelRow]) -> str:
    """Explain only the markers that actually appear on this page."""
    seen = {marker for row in rows for marker in row['markers']}
    notes = {
        'api': '`api` the vendor publishes this rate at a machine-readable endpoint, so it is '
        're-read and compared automatically rather than by a human reading a pricing page.',
        'tiered': '`tiered` the rate changes above a token threshold. The base rate is shown.',
        'daily': '`daily` the rate changes with the time of day. The standard rate is shown.',
        'voices': '`voices` some voice classes cost a multiple of the base rate.',
        'estimated': "`estimated` this rate is the vendor's own published estimate, not the meter it "
        'bills on. It will not reconcile against an invoice: the model is billed in another unit '
        '(usually tokens) and the figure here is the vendor restating that in this one. Useful for '
        'comparison and capacity planning, not for billing.',
    }
    lines = [notes[marker] for marker in ('api', 'tiered', 'daily', 'voices', 'estimated') if marker in seen]
    return '\n'.join(f'- {line}' for line in lines)


def render_rows(category: Modality, rows: list[ModelRow], columns: list[Column]) -> str:
    """Render model rows as a table, one row per model."""
    headers = ['Model', 'Name'] + [column.header for column in columns]
    if category != 'llm':
        headers += ['Source', 'Last verified']

    body: list[list[str]] = []
    for row in rows:
        cells = [f'`{cell(row["id"])}`{_marker_suffix(row)}', cell(row['name'])]
        for column in columns:
            value = row['values'].get(column.key)
            cells.append(fmt_usd(value) if column.kind == 'usd' else fmt_int(value))
        if category != 'llm':
            cells += [status_badge(row['status']), row['verified'] or '-']
        body.append(cells)
    return mdx_table(headers, body)


def _split_token_priced(category: Modality, rows: list[ModelRow]) -> tuple[list[ModelRow], list[ModelRow]]:
    """Separate rows carrying the category's primary unit from those billed per audio token."""
    if category not in TOKEN_COLUMNS:
        return rows, []
    primary = CATEGORY_COLUMNS[category][0].key
    main = [row for row in rows if row['values'].get(primary) is not None]
    token = [row for row in rows if row['values'].get(primary) is None]
    return main, token


def _frontmatter(fields: dict[str, str]) -> str:
    """YAML frontmatter.

    Values are contributor-supplied (a provider `name` comes straight from its YAML), so they are
    escaped: one unescaped quote would produce invalid YAML and fail the whole docs build.
    """

    def quoted(value: str) -> str:
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'

    body = '\n'.join(f'{key}: {quoted(value)}' for key, value in fields.items())
    return f'---\n{body}\n---'


def _page_summary(category: Modality) -> str:
    """What a provider page actually shows. LLM pages render no per-model source column, so
    claiming one in their description would be false."""
    if category == 'llm':
        return 'with input and output token rates'
    return 'with the rate and where it came from'


def _token_table_intro(category: Modality) -> str:
    per = 'second' if category in ('stt', 'vad') else 'character'
    return (
        f'## Billed per audio token\n\nThese models bill audio as tokens rather than by the {per}, '
        'so they are not comparable to the table above.'
    )


def render_provider_page(category: Modality, entry: ProviderEntry) -> str:
    """One page: every model a provider prices in one category."""
    meta = CATEGORY_META[category]
    rows = entry['models']
    main_rows, token_rows = _split_token_priced(category, rows)

    parts = [
        _frontmatter(
            {
                'title': f'{entry["name"]} {meta.tab} pricing',
                'sidebarTitle': entry['name'],
                'description': f'{len(rows)} {meta.tab} model(s) from {entry["name"]}, {_page_summary(category)}.',
                'mode': 'wide',
            }
        ),
        GENERATED_NOTICE,
        f'{meta.unit} Source of truth: [`prices/providers/{entry["id"]}.yml`]({PROVIDER_YAML_URL}/{entry["id"]}.yml).',
    ]

    if tier := entry.get('pricing_tier'):
        parts.append(
            f'**Plan:** {tier}. '
            + (
                'This vendor publishes one price with no plans to choose between.'
                if tier == 'Single published rate'
                else 'Rates below are that plan. A committed or negotiated plan will be cheaper, '
                'so check yours before budgeting from these.'
            )
        )

    if main_rows:
        parts.append(render_rows(category, main_rows, active_columns(main_rows, CATEGORY_COLUMNS[category])))
    if token_rows:
        parts.append(_token_table_intro(category))
        parts.append(render_rows(category, token_rows, active_columns(token_rows, TOKEN_COLUMNS[category])))

    footnote = _markers_footnote(rows)
    if footnote:
        parts.append(footnote)
    if category != 'llm':
        parts.append(
            'What `verified`, `imported`, and `seed` mean is on [How Fresh?](/how-fresh). '
            f'A rate that looks wrong is worth [reporting]({ADD_PROVIDER_URL}).'
        )
    return '\n\n'.join(parts) + '\n'


def render_comparison(category: Modality, rows: list[ComparisonRow]) -> str:
    """The LiveKit Inference vs direct-vendor table for one category."""
    priced = [row for row in rows if row['direct'] is not None]
    if not priced:
        return ''
    unit = {
        'stt': '$ / min',
        'tts': '$ / 1M chars',
        'llm': 'input $ / Mtok',
        'vad': '$ / min',
        's2s': '$ / Mtok',
        'agent': '$ / min',
    }[category]
    headers = ['Model', 'Name', f'Direct ({unit})', f'LiveKit ({unit})', f'Scale ({unit})', 'vs direct']
    body = [
        [
            f'`{cell(row["id"])}`',
            cell(row['name']),
            fmt_usd(row['direct']),
            fmt_usd(row['livekit']),
            fmt_usd(row['scale']),
            fmt_pct(row['delta']),
        ]
        for row in sorted(priced, key=lambda r: -(r['delta'] or 0))
    ]
    livekit_only = len(rows) - len(priced)
    tail = (
        f'\n\n{livekit_only} further LiveKit {CATEGORY_META[category].tab} model(s) have no direct-vendor '
        'entry to compare against.'
        if livekit_only
        else ''
    )
    return (
        '## Via LiveKit Inference\n\nWhat the same model costs going direct to the vendor versus through '
        f'the gateway. Scale is the discounted tier.\n\n{mdx_table(headers, body)}{tail}'
    )


def render_index_page(category: Modality, entries: list[ProviderEntry], comparison: list[ComparisonRow]) -> str:
    """The category landing page: every model across every vendor, cheapest first."""
    meta = CATEGORY_META[category]
    total = sum(len(entry['models']) for entry in entries)
    # The LLM index summarises by vendor rather than listing models, so "cheapest first" would be a
    # lie on that page.
    ordering = 'largest catalogs first' if category == 'llm' else 'cheapest first'
    parts = [
        _frontmatter(
            {
                'title': meta.title,
                'sidebarTitle': 'All models',
                'description': f'{total} {meta.tab} model(s) from {len(entries)} provider(s), {ordering}.',
                'mode': 'wide',
            }
        ),
        GENERATED_NOTICE,
        f'{total} {meta.tab} model(s) from {len(entries)} provider(s). {meta.unit} '
        'Pick a vendor in the sidebar to see that vendor alone.',
    ]
    if meta.note:
        parts.append(meta.note)

    if category == 'llm':
        # Over a thousand rows is not a readable page, so the LLM index summarises by vendor and
        # each vendor page carries every one of its models.
        headers = ['Provider', 'Models', 'Cheapest input $ / Mtok', 'Cheapest output $ / Mtok']
        body: list[list[str]] = []
        for entry in sorted(entries, key=lambda e: (-len(e['models']), e['id'])):
            inputs = [r['values']['input_mtok'] for r in entry['models'] if r['values'].get('input_mtok') is not None]
            outputs = [
                r['values']['output_mtok'] for r in entry['models'] if r['values'].get('output_mtok') is not None
            ]
            body.append(
                [
                    f'[{cell(entry["name"])}](/{category}/{slug(entry["id"])})',
                    str(len(entry['models'])),
                    fmt_usd(min(cast('list[float]', inputs)) if inputs else None),
                    fmt_usd(min(cast('list[float]', outputs)) if outputs else None),
                ]
            )
        parts.append(mdx_table(headers, body, text_cols=1))
    else:
        pairs = [(entry, row) for entry in entries for row in entry['models']]
        primary = CATEGORY_COLUMNS[category][0].key
        rated = [pair for pair in pairs if pair[1]['values'].get(primary) is not None]
        rated.sort(key=lambda pair: (cast('float', pair[1]['values'][primary]), pair[0]['id'], pair[1]['id']))
        unrated = [pair for pair in pairs if pair[1]['values'].get(primary) is None]

        columns = active_columns([row for _, row in rated], CATEGORY_COLUMNS[category])
        headers = ['Provider', 'Model', 'Name'] + [column.header for column in columns] + ['Source', 'Last verified']
        body = []
        for entry, row in rated:
            cells = [
                f'[{cell(entry["name"])}](/{category}/{slug(entry["id"])})',
                f'`{cell(row["id"])}`{_marker_suffix(row)}',
                cell(row['name']),
            ]
            for column in columns:
                value = row['values'].get(column.key)
                cells.append(fmt_usd(value) if column.kind == 'usd' else fmt_int(value))
            cells += [status_badge(row['status']), row['verified'] or '-']
            body.append(cells)
        parts.append(mdx_table(headers, body, text_cols=3))

        if unrated:
            names = ', '.join(f'`{cell(row["id"])}`' for _, row in unrated)
            per = 'second' if category in ('stt', 'vad') else 'character'
            parts.append(
                f'{len(unrated)} further {meta.tab} model(s) bill audio as tokens rather than by the {per} '
                f'and are listed on their vendor page: {names}.'
            )

        footnote = _markers_footnote([row for _, row in pairs])
        if footnote:
            parts.append(footnote)

    table = render_comparison(category, comparison)
    if table:
        parts.append(table)
    if category != 'llm':
        parts.append('What `verified`, `imported`, and `seed` mean is on [How Fresh?](/how-fresh).')
    return '\n\n'.join(parts) + '\n'


# --- navigation --------------------------------------------------------------


def _sidebar_group(provider_id: str) -> str:
    if provider_id in GATEWAY_PROVIDERS:
        return 'Gateways'
    if provider_id.startswith(HUGGINGFACE_PREFIX):
        return 'HuggingFace Inference'
    return 'Direct vendors'


def category_tab(category: Modality, entries: list[ProviderEntry]) -> dict[str, Any]:
    """One horizontal tab: the index page, then vendor pages grouped in the sidebar."""
    meta = CATEGORY_META[category]
    pages: list[Any] = [f'{category}/{INDEX_PAGE}']
    vendor_pages = [(entry['id'], f'{category}/{slug(entry["id"])}') for entry in entries]

    if len(entries) < MIN_PROVIDERS_FOR_GROUPS:
        pages += [page for _, page in vendor_pages]
    else:
        for group in GROUP_ORDER:
            members = [page for provider_id, page in vendor_pages if _sidebar_group(provider_id) == group]
            if members:
                pages.append({'group': group, 'pages': members})
    return {'tab': meta.tab, 'icon': meta.icon, 'pages': pages}


def build_navigation(catalog: Catalog) -> dict[str, Any]:
    """The full ``navigation`` block. Generated, so the nav can never list a page that does not exist."""
    tabs: list[dict[str, Any]] = [{'tab': 'Overview', 'icon': 'book-open', 'pages': ['index', 'how-fresh']}]
    tabs += [category_tab(category, catalog[category]) for category in CATEGORIES]
    tabs.append({'tab': 'Add Yours', 'icon': 'git-pull-request', 'pages': ['contribute', 'pricing-feed']})
    return {'tabs': tabs}


# --- writing -----------------------------------------------------------------


def write_if_changed(path: Path, content: str) -> bool:
    """Write only when the content differs, so an unchanged run leaves mtimes alone."""
    if path.exists() and path.read_text() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return True


def inject_blocks(path: Path, blocks: dict[str, str]) -> bool:
    """Replace the text between ``{/* generated:<key> start */}`` and its ``end`` marker.

    Mirrors ``inject_providers.py`` for the README. A key with no marker pair in the file raises:
    silently dropping a generated number is how a docs site starts lying.
    """
    text = path.read_text()
    for key, body in blocks.items():
        start = f'{{/* generated:{key} start */}}'
        end = f'{{/* generated:{key} end */}}'
        pattern = re.compile(re.escape(start) + r'.*?' + re.escape(end), re.DOTALL)
        if not pattern.search(text):
            raise ValueError(f'{path.name} has no `generated:{key}` marker pair')
        text = pattern.sub(f'{start}\n{body}\n{end}', text)
    return write_if_changed(path, text)


def overview_blocks(catalog: Catalog, comparison: Comparison) -> dict[str, str]:
    """The generated numbers on the hand-written overview page."""
    counts = {category: sum(len(entry['models']) for entry in catalog[category]) for category in CATEGORIES}
    providers = {entry['id'] for entries in catalog.values() for entry in entries}
    totals = (
        f'**{len(providers)} providers, {sum(counts.values()):,} priced models.** '
        f'{counts["stt"]} speech-to-text, {counts["tts"]} text-to-speech, '
        f'{counts["s2s"]} speech-to-speech, {counts["vad"]} voice activity detection, '
        f'{counts["agent"]} bundled voice agent, and {counts["llm"]:,} LLM.'
    )

    lines: list[str] = []
    for category in CATEGORIES:
        priced = [row for row in comparison[category] if row['delta'] is not None]
        if not priced:
            continue
        deltas = sorted(cast('float', row['delta']) for row in priced)
        median = deltas[len(deltas) // 2]
        label = CATEGORY_META[category].tab
        lines.append(
            f'| [{label}](/{category}/{INDEX_PAGE}) | {len(priced)} | {median:+.0f}% | '
            f'{deltas[0]:+.0f}% to {deltas[-1]:+.0f}% |'
        )
    table = '\n'.join(
        ['| Category | Models compared | Median vs direct | Range |', '| --- | ---: | ---: | ---: |', *lines]
    )
    backed = sum(
        1
        for category in CATEGORIES
        for entry in catalog[category]
        for row in entry['models']
        if 'api' in row['markers']
    )
    providers_backed = sorted(
        {
            entry['name']
            for category in CATEGORIES
            for entry in catalog[category]
            if any('api' in row['markers'] for row in entry['models'])
        }
    )
    if len(providers_backed) > 1:
        named = f'The providers are {", ".join(providers_backed[:-1])} and {providers_backed[-1]}. '
    elif providers_backed:
        named = f'The provider is {providers_backed[0]}. '
    else:
        named = ''
    coverage = (
        f'**{backed} of {sum(counts.values()):,} priced models** are published by their vendor at a '
        'machine-readable endpoint, so this catalog re-reads and compares them automatically. '
        + named
        + 'Every other rate is read off a pricing page by a person. '
        '[Serving an endpoint](/pricing-feed) is the single most useful thing a provider can do here.'
    )
    return {'catalog-counts': totals, 'livekit-summary': table, 'api-coverage': coverage}


def _prune(directory: Path, keep: set[Path]) -> list[Path]:
    """Delete generated pages for providers that no longer price anything in this category."""
    removed: list[Path] = []
    if not directory.is_dir():
        return removed
    for path in sorted(directory.glob('*.mdx')):
        if path not in keep:
            path.unlink()
            removed.append(path)
    return removed


def build_docs(docs_dir: Path | None = None) -> list[Path]:
    """Generate every provider page, every index page, and the ``docs.json`` navigation."""
    docs_dir = docs_dir or DOCS_DIR
    data = cast('list[dict[str, Any]]', json.loads(DATA_JSON.read_text()))

    catalog = build_catalog(data)
    comparison = build_comparison(data)

    missing = missing_alias_targets(data)
    if missing:
        print(f'WARNING: {len(missing)} LiveKit alias(es) point at a missing direct model: {missing}')
    bad_prefixes = unknown_prefix_providers(data)
    if bad_prefixes:
        print(f'WARNING: {len(bad_prefixes)} LiveKit prefix(es) map to a provider not in the catalog: {bad_prefixes}')

    written: list[Path] = []
    for category in CATEGORIES:
        entries = catalog[category]
        directory = docs_dir / category
        keep: set[Path] = set()

        index_path = directory / f'{INDEX_PAGE}.mdx'
        keep.add(index_path)
        if write_if_changed(index_path, render_index_page(category, entries, comparison[category])):
            written.append(index_path)

        for entry in entries:
            path = directory / f'{slug(entry["id"])}.mdx'
            keep.add(path)
            if write_if_changed(path, render_provider_page(category, entry)):
                written.append(path)

        for removed in _prune(directory, keep):
            print(f'removed stale page {removed.relative_to(docs_dir)}')

    overview = docs_dir / 'index.mdx'
    if overview.exists() and inject_blocks(overview, overview_blocks(catalog, comparison)):
        written.append(overview)

    # Imported here rather than at module scope: source_feed reads base_prices and detect_modality
    # from this module, so a top-level import would be circular.
    from .source_feed import render_rate_grid

    feed_page = docs_dir / 'pricing-feed.mdx'
    if feed_page.exists() and inject_blocks(feed_page, {'rate-grid': render_rate_grid()}):
        written.append(feed_page)

    docs_json = docs_dir / 'docs.json'
    if docs_json.exists():
        config = cast('dict[str, Any]', json.loads(docs_json.read_text()))
        config['navigation'] = build_navigation(catalog)
        if write_if_changed(docs_json, json.dumps(config, indent=2) + '\n'):
            written.append(docs_json)

    providers = {category: len(catalog[category]) for category in CATEGORIES}
    models = {category: sum(len(entry['models']) for entry in catalog[category]) for category in CATEGORIES}
    print(f'docs written to {docs_dir} ({len(written)} file(s) changed; providers {providers}; models {models})')
    return written


def main() -> None:
    build_docs()


if __name__ == '__main__':
    main()
