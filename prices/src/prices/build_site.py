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


def render_html(catalog: Catalog) -> str:
    """Inject the catalog JSON and add-provider URL into the template."""
    template = TEMPLATE_PATH.read_text()
    # Guard against an accidental </script> inside the embedded JSON.
    catalog_json = json.dumps(catalog, separators=(',', ':')).replace('</', '<\\/')
    return template.replace('__CATALOG_JSON__', catalog_json).replace('__ADD_PROVIDER_URL__', ADD_PROVIDER_URL)


def build_site(out_dir: Path | None = None) -> Path:
    """Generate ``index.html`` into ``out_dir`` (default ``site/``). Returns the path."""
    out_dir = out_dir or SITE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    data = cast('list[dict[str, Any]]', json.loads(DATA_JSON.read_text()))
    catalog = build_catalog(data)
    out_path = out_dir / 'index.html'
    out_path.write_text(render_html(catalog))
    counts = {modality: len(entries) for modality, entries in catalog.items()}
    print(f'site written to {out_path} (providers per modality: {counts})')
    return out_path


def main() -> None:
    build_site()


if __name__ == '__main__':
    main()
