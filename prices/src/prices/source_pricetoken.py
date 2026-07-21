"""Import voice (TTS/STT) prices from the PriceToken registry (affromero/pricetoken, MIT).

This is a bounded head start, not a source of truth. Imported rates land as
``provenance.source = 'imported'`` with NO ``prices_checked``: they are unverified until a
human confirms each against the vendor's own pricing page and sets ``prices_checked`` on merge
(the same human gate the freshness bot respects). PriceToken's registry carries no per-model
source URL, so ``pricing_source_url`` is authored per provider (see ``PROVIDER_MAP``), not imported.

This module contains the pure, tested conversion core. The orchestration that writes provider
YAMLs (new speech-specific providers plus new model IDs on existing voice providers) depends on
``PROVIDER_MAP`` being completed with real ``api_pattern`` / ``pricing_source_url`` values, and on
the maintainer's per-provider rate verification. See the module tests for the conversion contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from .prices_types import ClauseEquals, ModelInfo, ModelPrice, Provenance

Modality = Literal['tts', 'stt']

#: Attribution recorded on every imported model so the origin is auditable in the YAML.
IMPORT_ATTRIBUTION = (
    'Imported from the PriceToken registry (affromero/pricetoken, MIT). Unverified: '
    'confirm against the provider pricing page and set prices_checked before relying on it.'
)

#: The only source cost field accepted per modality. Anything else fails closed (Codex #4/#7):
#: PriceToken normalizes TTS to $/1M characters and STT to $/minute, so a model carrying any other
#: ``costPer*`` shape is an unexpected pricing structure we refuse to silently map.
_COST_FIELD: dict[Modality, str] = {'tts': 'costPerMChars', 'stt': 'costPerMinute'}


@dataclass(frozen=True)
class ProviderTarget:
    """How one PriceToken provider maps into voice-prices.

    ``target_id`` is the voice-prices provider id. ``is_new`` marks a new speech-specific provider
    file (needs ``api_pattern`` + ``pricing_source_url`` authored and human-confirmed) versus adding
    new model IDs to an existing voice provider.
    """

    target_id: str
    is_new: bool
    name: str | None = None
    api_pattern: str | None = None
    pricing_source_url: str | None = None


#: PriceToken provider id -> voice-prices target. api_pattern / pricing_source_url are left None where
#: they still need authoring + human confirmation; the orchestrator must refuse to write a new
#: provider whose api_pattern/url are unset rather than guess them.
PROVIDER_MAP: dict[str, ProviderTarget] = {
    # already voice providers in voice-prices: append new model IDs only
    'cartesia': ProviderTarget('cartesia', is_new=False),
    'deepgram': ProviderTarget('deepgram', is_new=False),
    'elevenlabs': ProviderTarget('elevenlabs', is_new=False),
    'assemblyai': ProviderTarget('assemblyai', is_new=False),
    'openai': ProviderTarget('openai', is_new=False),
    # LLM-collision cases -> new speech-specific providers (decided in eng review)
    'azure': ProviderTarget('azure_speech', is_new=True, name='Azure AI Speech'),
    'google-cloud': ProviderTarget('google_cloud', is_new=True, name='Google Cloud Speech'),
    'amazon': ProviderTarget('amazon_polly', is_new=True, name='Amazon Polly'),
    # zero-collision new providers
    'fal': ProviderTarget('fal', is_new=True, name='fal'),
    'playht': ProviderTarget('playht', is_new=True, name='PlayHT'),
    'replicate': ProviderTarget('replicate', is_new=True, name='Replicate'),
}


def convert_tts_rate(cost_per_mchars: Decimal | float | int | str) -> Decimal:
    """PriceToken TTS ``costPerMChars`` ($/1M characters) -> ``input_kchars`` ($/1,000 characters)."""
    return Decimal(str(cost_per_mchars)) / Decimal(1000)


def convert_stt_rate(cost_per_minute: Decimal | float | int | str) -> Decimal:
    """PriceToken STT ``costPerMinute`` ($/minute) -> ``input_audio_kseconds`` ($/1,000 seconds)."""
    return Decimal(str(cost_per_minute)) * Decimal(1000) / Decimal(60)


def convert_model(pt_model: dict[str, Any], modality: Modality) -> ModelInfo:
    """Convert one PriceToken registry entry into an unverified voice-prices ``ModelInfo``.

    Fails closed on any pricing structure other than the expected single field for the modality,
    rather than silently producing a wrong rate.
    """
    model_id = pt_model.get('modelId')
    if not isinstance(model_id, str) or not model_id:
        raise ValueError(f'PriceToken model is missing a string modelId: {pt_model!r}')

    expected_field = _COST_FIELD[modality]
    # Fail closed: refuse any unexpected costPer* structure (regional/tiered/per-token/etc.).
    unexpected = [k for k in pt_model if k.startswith('costPer') and k != expected_field]
    if unexpected:
        raise ValueError(f'{model_id}: unexpected cost field(s) {unexpected}; expected only {expected_field}')

    raw_rate = pt_model.get(expected_field)
    if raw_rate is None:
        raise ValueError(f'{model_id}: missing required {expected_field}')

    if modality == 'tts':
        prices = ModelPrice(input_kchars=convert_tts_rate(raw_rate))
    else:
        prices = ModelPrice(input_audio_kseconds=convert_stt_rate(raw_rate))

    status = pt_model.get('status')
    deprecated = True if status == 'deprecated' else None

    return ModelInfo(
        id=model_id,
        match=ClauseEquals(equals=model_id),
        name=pt_model.get('displayName'),
        deprecated=deprecated,
        price_comments=IMPORT_ATTRIBUTION,
        prices=prices,
        provenance=Provenance(source='imported'),
    )
