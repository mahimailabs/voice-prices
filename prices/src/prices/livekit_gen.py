"""Generate the `livekit` and `livekit-scale` provider YAMLs from LiveKit's pricing JSON.

LiveKit Inference is a gateway (like OpenRouter): it resells STT, TTS, and LLM models
under a single API key at its own per-model rates, distinct from buying direct. LiveKit
publishes Build and Ship tiers identically for every model, and only Scale ever differs
(and only for STT/TTS, never LLM). So we emit two providers:

- ``livekit``: every active model at the Build/Ship price.
- ``livekit-scale``: only the models whose Scale price differs, with
  ``fallback_model_providers: [livekit]`` so LLM and flat-priced voice models reuse the
  ``livekit`` price instead of being duplicated.

Source: LiveKit Docs ``get_pricing_info`` JSON (``.inference.{stt,tts,llm}``). Numbers are
exact decimal strings; conversions to our fields are deterministic:

- STT ``$/min`` -> ``input_audio_kseconds`` (``* 1000 / 60``)
- TTS ``$/1,000,000 chars`` -> ``input_kchars`` (``/ 1000``)
- LLM ``$/1,000,000 tokens`` -> ``input_mtok`` / ``output_mtok`` / ``cache_read_mtok`` (1:1)

This module is build-side only (not shipped in the ``voice_prices`` wheel). Regenerate
with ``make livekit-get`` after refreshing the source JSON.
"""

from __future__ import annotations

import json
import os
import textwrap
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, cast

from .utils import package_dir, root_dir

PRICING_URL = 'https://livekit.io/pricing'
API_PATTERN = r'https://[^/]*\.livekit\.cloud'
_DEFAULT_JSON = package_dir / 'sources' / 'livekit_pricing.json'
_SIX_DP = Decimal('0.000001')

LLM_METRIC_FIELD = {
    'input_tokens': 'input_mtok',
    'output_tokens': 'output_mtok',
    'cached_input_tokens': 'cache_read_mtok',
}

# Render order for the priced fields; only the ones a model actually sets are emitted.
_PRICE_FIELD_ORDER = ('input_mtok', 'output_mtok', 'cache_read_mtok', 'input_kchars', 'input_audio_kseconds')

_BASE_COMMENT = (
    'LiveKit Inference is a gateway that resells STT, TTS, and LLM models under a single API key, '
    'at its own per-model rates (distinct from buying direct from each vendor). These are the '
    'Build/Ship tier prices, which LiveKit publishes identically for both plans; Scale-tier '
    'discounts live in the livekit-scale provider. Resolution is by explicit provider_id '
    '(VoiceGateway passes provider_id=livekit); model_match is intentionally omitted so a bare '
    'model ref still resolves to the direct vendor. Conversions: STT $/min to input_audio_kseconds '
    '(x1000/60); TTS $/1M chars to input_kchars (/1000); LLM $/1M tokens map 1:1. Realtime bundled '
    'models and LiveKit Cloud platform per-minute rates are out of scope. Source: LiveKit '
    'get_pricing_info; regenerate with make livekit-get.'
)
_SCALE_COMMENT = (
    'Scale-tier prices for LiveKit Inference. Only models whose Scale rate differs from Build/Ship '
    'are listed here; LLM models (identical across all tiers) and flat-priced voice models fall '
    'back to the livekit provider via fallback_model_providers. Same conversions and source as the '
    'livekit provider.'
)


def stt_rate(per_min: str | Decimal) -> Decimal:
    """Convert a LiveKit STT rate in ``$/min`` to our ``input_audio_kseconds`` ($ per 1000 sec)."""
    return (Decimal(per_min) * 1000 / 60).quantize(_SIX_DP, rounding=ROUND_HALF_UP)


def tts_rate(per_mchar: str | Decimal) -> Decimal:
    """Convert a LiveKit TTS rate in ``$/1,000,000 chars`` to our ``input_kchars`` ($ per 1000 chars)."""
    return Decimal(per_mchar) / 1000


def scale_differs(entry: dict[str, Any]) -> bool:
    """Whether any of a model's rates is cheaper on Scale than on Build/Ship."""
    rates = cast('list[dict[str, Any]]', entry['rates'])
    return any(Decimal(str(r['build'])) != Decimal(str(r['scale'])) for r in rates)


def _model_prices(entry: dict[str, Any], modality: str, tier: str) -> dict[str, Decimal]:
    rates = cast('list[dict[str, Any]]', entry['rates'])
    if modality == 'stt':
        return {'input_audio_kseconds': stt_rate(str(rates[0][tier]))}
    if modality == 'tts':
        return {'input_kchars': tts_rate(str(rates[0][tier]))}
    prices: dict[str, Decimal] = {}
    for rate in rates:
        prices[LLM_METRIC_FIELD[str(rate['metric'])]] = Decimal(str(rate[tier]))
    return prices


def _build_models(
    inference: dict[str, Any], tier: str, *, only_scale_differs: bool, checked_date: date
) -> list[dict[str, Any]]:
    models: list[dict[str, Any]] = []
    for modality in ('stt', 'tts', 'llm'):
        for entry in cast('list[dict[str, Any]]', inference.get(modality, [])):
            if entry.get('is_deprecated'):
                continue
            if only_scale_differs and not scale_differs(entry):
                continue
            models.append(
                {
                    'id': entry['model_id'],
                    'name': entry['model_label'],
                    'match': {'equals': entry['model_id']},
                    'prices_checked': checked_date,
                    'pricing_source_url': PRICING_URL,
                    'prices': _model_prices(entry, modality, tier),
                }
            )
    models.sort(key=lambda m: cast('str', m['id']))
    return models


def build_provider(inference: dict[str, Any], *, scale: bool, checked_date: date) -> dict[str, Any]:
    """Build a provider dict (validatable by ``Provider``) for one tier of LiveKit Inference."""
    tier = 'scale' if scale else 'build'
    provider: dict[str, Any] = {
        'name': 'LiveKit Inference (Scale)' if scale else 'LiveKit Inference',
        'id': 'livekit-scale' if scale else 'livekit',
        'pricing_urls': [PRICING_URL],
        'api_pattern': API_PATTERN,
    }
    if scale:
        provider['fallback_model_providers'] = ['livekit']
        provider['price_comments'] = _SCALE_COMMENT
    else:
        provider['provider_match'] = {'contains': 'livekit'}
        provider['price_comments'] = _BASE_COMMENT
    provider['models'] = _build_models(inference, tier, only_scale_differs=scale, checked_date=checked_date)
    return provider


def _fmt(value: Decimal) -> str:
    """Format a Decimal as a plain (non-exponent) number with trailing zeros stripped."""
    text = format(value, 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text


def render_yaml(provider: dict[str, Any]) -> str:
    """Render a provider dict to a provider YAML string matching the catalog's house style."""
    lines: list[str] = ['# yaml-language-server: $schema=.schema.json']
    lines.append(f'name: {provider["name"]}')
    lines.append(f'id: {provider["id"]}')
    lines.append('pricing_urls:')
    for url in cast('list[str]', provider['pricing_urls']):
        lines.append(f'  - {url}')
    lines.append(f"api_pattern: '{provider['api_pattern']}'")
    if 'provider_match' in provider:
        match = cast('dict[str, str]', provider['provider_match'])
        lines.append('provider_match:')
        lines.append(f'  contains: {match["contains"]}')
    if 'fallback_model_providers' in provider:
        lines.append('fallback_model_providers:')
        for fallback in cast('list[str]', provider['fallback_model_providers']):
            lines.append(f'  - {fallback}')
    lines.append('price_comments: >-')
    lines.extend(f'  {line}' for line in textwrap.wrap(cast('str', provider['price_comments']), width=95))
    lines.append('models:')
    for model in cast('list[dict[str, Any]]', provider['models']):
        model_id = cast('str', model['id'])
        lines.append(f'  - id: {model_id}')
        lines.append(f'    name: {model["name"]}')
        lines.append('    match:')
        lines.append(f'      equals: {model_id}')
        lines.append(f'    prices_checked: {cast("date", model["prices_checked"]).isoformat()}')
        lines.append(f'    pricing_source_url: {model["pricing_source_url"]}')
        lines.append('    prices:')
        prices = cast('dict[str, Decimal]', model['prices'])
        for field in _PRICE_FIELD_ORDER:
            if field in prices:
                lines.append(f'      {field}: {_fmt(prices[field])}')
    return '\n'.join(lines) + '\n'


def generate(json_path: Path, out_dir: Path, *, checked_date: date) -> tuple[Path, Path]:
    """Read LiveKit's pricing JSON and write ``livekit.yml`` and ``livekit_scale.yml`` into ``out_dir``."""
    payload = cast('dict[str, Any]', json.loads(Path(json_path).read_text()))
    inference = cast('dict[str, Any]', payload['inference'])
    base_path = out_dir / 'livekit.yml'
    scale_path = out_dir / 'livekit_scale.yml'
    base_path.write_text(render_yaml(build_provider(inference, scale=False, checked_date=checked_date)))
    scale_path.write_text(render_yaml(build_provider(inference, scale=True, checked_date=checked_date)))
    return base_path, scale_path


def livekit_gen() -> None:
    """Generate the livekit and livekit-scale provider YAMLs from LiveKit's pricing JSON."""
    json_path = Path(os.environ.get('LIVEKIT_PRICING_JSON', str(_DEFAULT_JSON)))
    base_path, scale_path = generate(json_path, package_dir / 'providers', checked_date=date.today())
    print(f'wrote {base_path.relative_to(root_dir)} and {scale_path.relative_to(root_dir)}')
