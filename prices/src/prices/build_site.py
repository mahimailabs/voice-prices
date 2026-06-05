"""Generate the static documentation site from the built price data.

Reads the committed ``prices/data.json`` (the canonical, post-processed catalog)
and renders a single self-contained ``site/index.html``: one page with LLM / TTS /
STT tabs, read-only price tables collapsed by provider, search, and an
"Add a provider" button that opens a prefilled GitHub issue.

The page never drifts from the library because it is generated from the same data
the package ships, mirroring ``inject_providers.py`` for the README list.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

from .utils import package_dir, root_dir

DATA_JSON = package_dir / 'data.json'
TEMPLATE_PATH = Path(__file__).parent / 'templates' / 'site.html'
SITE_DIR = root_dir / 'site'

ADD_PROVIDER_URL = 'https://github.com/mahimailabs/voice-prices/issues/new?template=add-provider.yml'

Modality = Literal['llm', 'tts', 'stt']

# Fields on a price block that are not per-unit rates and must not be treated as one.
_NON_RATE_FIELDS = {'voice_multipliers'}


class _ModelRowBase(TypedDict):
    id: str
    name: str


class ModelRow(_ModelRowBase, total=False):
    """A single model's row in the slim catalog. Modality decides which keys are set."""

    # LLM
    input_mtok: float | None
    output_mtok: float | None
    cache_read_mtok: float | None
    context_window: int | None
    # TTS
    input_kchars: float | None
    # STT
    input_audio_kseconds: float | None
    per_min: float | None
    # markers
    tiered: bool
    daily: bool


class ProviderEntry(TypedDict):
    id: str
    name: str
    models: list[ModelRow]


Catalog = dict[Modality, list[ProviderEntry]]


# --- LiveKit Inference vs direct-vendor comparison ---------------------------

# LiveKit slug prefixes whose direct-catalog provider id differs from the prefix.
_LIVEKIT_PREFIX_TO_PROVIDER = {'xai': 'x_ai', 'deepseek-ai': 'deepseek'}

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
    # ElevenLabs: older versions map to the nearest direct version.
    'elevenlabs/eleven_flash_v2': ('elevenlabs', 'eleven_flash_v2_5'),
    'elevenlabs/eleven_turbo_v2': ('elevenlabs', 'eleven_turbo_v2_5'),
    # OpenAI chat-latest aliases map to their numbered direct model.
    'openai/gpt-5.1-chat-latest': ('openai', 'gpt-5.1'),
    'openai/gpt-5.2-chat-latest': ('openai', 'gpt-5.2'),
}


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


def detect_modality(flat: dict[str, float]) -> Modality | None:
    """Classify a model by its priced fields. Returns None for unpriced models."""
    if 'input_kchars' in flat or 'output_audio_kseconds' in flat:
        return 'tts'
    if 'input_audio_kseconds' in flat:
        return 'stt'
    if any(field.endswith('_mtok') for field in flat):
        return 'llm'
    return None


def _esc(value: str) -> str:
    return html.escape(value, quote=True)


def _model_row(
    model: dict[str, Any], modality: Modality, flat: dict[str, float], tiered: bool, daily: bool
) -> ModelRow:
    model_id = _esc(str(model.get('id', '')))
    name = _esc(str(model.get('name') or model.get('id') or ''))
    row: ModelRow = {'id': model_id, 'name': name}
    if modality == 'llm':
        ctx = model.get('context_window')
        row['input_mtok'] = flat.get('input_mtok')
        row['output_mtok'] = flat.get('output_mtok')
        row['cache_read_mtok'] = flat.get('cache_read_mtok')
        row['context_window'] = ctx if isinstance(ctx, int) else None
        row['tiered'] = tiered
        row['daily'] = daily
    elif modality == 'tts':
        row['input_kchars'] = flat.get('input_kchars')
    else:  # stt
        rate = flat.get('input_audio_kseconds')
        row['input_audio_kseconds'] = rate
        row['per_min'] = (rate * 60 / 1000) if rate is not None else None
    return row


def build_catalog(data: list[dict[str, Any]]) -> Catalog:
    """Build the slim per-modality catalog from the raw data.json providers list.

    Excludes unpriced (``prices == {}``) and deprecated models. A provider appears
    under each modality it has at least one model in (e.g. OpenAI under llm + tts).
    Providers are sorted by id; models keep their catalog order.
    """
    catalog: Catalog = {'llm': [], 'tts': [], 'stt': []}

    for provider in sorted(data, key=lambda p: str(p.get('id', ''))):
        provider_id = _esc(str(provider.get('id', '')))
        provider_name = _esc(str(provider.get('name') or provider.get('id') or ''))
        models = provider.get('models')
        if not isinstance(models, list):
            continue

        by_modality: dict[Modality, list[ModelRow]] = {'llm': [], 'tts': [], 'stt': []}
        for raw_model in cast('list[Any]', models):
            if not isinstance(raw_model, dict):
                continue
            model = cast('dict[str, Any]', raw_model)
            if model.get('deprecated') is True:
                continue
            flat, tiered, daily = base_prices(model.get('prices'))
            if not flat:
                continue
            modality = detect_modality(flat)
            if modality is None:
                continue
            by_modality[modality].append(_model_row(model, modality, flat, tiered, daily))

        for modality, rows in by_modality.items():
            if rows:
                catalog[modality].append({'id': provider_id, 'name': provider_name, 'models': rows})

    return catalog


def _resolve_direct(slug: str) -> tuple[str, str] | None:
    """Map a LiveKit slug to its direct-catalog ``(provider_id, model_id)``, if any."""
    if slug in LIVEKIT_DIRECT_ALIASES:
        return LIVEKIT_DIRECT_ALIASES[slug]
    prefix, sep, rest = slug.partition('/')
    if not sep:
        return None
    return _LIVEKIT_PREFIX_TO_PROVIDER.get(prefix, prefix), rest


def _primary_rate(modality: Modality, flat: dict[str, float] | None) -> float | None:
    """The single comparable rate per modality: STT $/min, TTS $/1M chars, LLM input $/Mtok."""
    if not flat:
        return None
    if modality == 'stt':
        rate = flat.get('input_audio_kseconds')
        return rate * 60 / 1000 if rate is not None else None
    if modality == 'tts':
        rate = flat.get('input_kchars')
        return rate * 1000 if rate is not None else None
    return flat.get('input_mtok')


def _round(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def build_comparison(data: list[dict[str, Any]]) -> Comparison:
    """Pair every LiveKit Inference model with its direct-vendor price for the comparison view.

    Returns per-modality rows with the LiveKit Build/Ship rate, the Scale rate (None when the model
    is not separately discounted and falls back), the direct-vendor rate (None for LiveKit-only
    providers: Speechmatics, Inworld, Rime, xAI voice), and the Build/Ship-vs-direct delta percent.
    All rates are in the modality's primary display unit.
    """
    index: dict[tuple[str, str], dict[str, float]] = {}
    for provider in data:
        provider_id = str(provider.get('id', ''))
        for raw_model in cast('list[Any]', provider.get('models') or []):
            if isinstance(raw_model, dict):
                model = cast('dict[str, Any]', raw_model)
                flat, _, _ = base_prices(model.get('prices'))
                index[(provider_id, str(model.get('id', '')))] = flat

    comparison: Comparison = {'llm': [], 'tts': [], 'stt': []}
    livekit = next((p for p in data if p.get('id') == 'livekit'), None)
    if livekit is None:
        return comparison

    for raw_model in cast('list[Any]', livekit.get('models') or []):
        model = cast('dict[str, Any]', raw_model)
        slug = str(model.get('id', ''))
        flat = index.get(('livekit', slug), {})
        modality = detect_modality(flat)
        if modality is None:
            continue
        livekit_rate = _primary_rate(modality, flat)
        scale_rate = _primary_rate(modality, index.get(('livekit-scale', slug)))
        direct_ref = _resolve_direct(slug)
        direct_rate = _primary_rate(modality, index.get(direct_ref)) if direct_ref else None
        delta = (
            round((livekit_rate - direct_rate) / direct_rate * 100, 1)
            if direct_rate and livekit_rate is not None
            else None
        )
        comparison[modality].append(
            {
                'id': _esc(slug),
                'name': _esc(str(model.get('name') or slug)),
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
    `build_site` surfaces this as a warning so it is caught rather than degrading quietly.
    """
    present: set[tuple[str, str]] = set()
    for provider in data:
        provider_id = str(provider.get('id', ''))
        for raw_model in cast('list[Any]', provider.get('models') or []):
            if isinstance(raw_model, dict):
                present.add((provider_id, str(cast('dict[str, Any]', raw_model).get('id', ''))))
    return sorted(slug for slug, target in LIVEKIT_DIRECT_ALIASES.items() if target not in present)


def render_html(catalog: Catalog, comparison: Comparison) -> str:
    """Inject the catalog + comparison JSON and add-provider URL into the template."""
    template = TEMPLATE_PATH.read_text()
    # Guard against an accidental </script> inside the embedded JSON.
    catalog_json = json.dumps(catalog, separators=(',', ':')).replace('</', '<\\/')
    comparison_json = json.dumps(comparison, separators=(',', ':')).replace('</', '<\\/')
    return (
        template.replace('__CATALOG_JSON__', catalog_json)
        .replace('__COMPARISON_JSON__', comparison_json)
        .replace('__ADD_PROVIDER_URL__', ADD_PROVIDER_URL)
    )


def build_site(out_dir: Path | None = None) -> Path:
    """Generate ``index.html`` into ``out_dir`` (default ``site/``). Returns the path."""
    out_dir = out_dir or SITE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    data = cast('list[dict[str, Any]]', json.loads(DATA_JSON.read_text()))
    catalog = build_catalog(data)
    comparison = build_comparison(data)
    missing = missing_alias_targets(data)
    if missing:
        print(
            f'WARNING: {len(missing)} LiveKit alias(es) point at a missing direct model (comparison baseline lost): {missing}'
        )
    out_path = out_dir / 'index.html'
    out_path.write_text(render_html(catalog, comparison))
    counts = {modality: len(entries) for modality, entries in catalog.items()}
    compared = sum(len(rows) for rows in comparison.values())
    print(f'site written to {out_path} (providers per modality: {counts}; livekit models compared: {compared})')
    return out_path


def main() -> None:
    build_site()


if __name__ == '__main__':
    main()
