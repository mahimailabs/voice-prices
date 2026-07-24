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

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast

from .prices_types import ClauseEquals, ModelInfo, ModelPrice, Provenance, Provider
from .update import ProviderYaml, ProviderYamlDict, get_provider_yaml_string, get_providers_yaml
from .utils import package_dir

#: Raw GitHub location of PriceToken's MIT-licensed registry.
PRICETOKEN_RAW = 'https://raw.githubusercontent.com/affromero/pricetoken/main/registry'

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
    'mistral': ProviderTarget('mistral_speech', is_new=True, name='Mistral (Voxtral)'),
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


def convert_model(pt_model: dict[str, Any], modality: Modality, *, pricing_source_url: str | None = None) -> ModelInfo:
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
        pricing_source_url=cast(Any, pricing_source_url),
        deprecated=deprecated,
        price_comments=IMPORT_ATTRIBUTION,
        prices=prices,
        provenance=Provenance(source='imported'),
    )


@dataclass
class ImportPlan:
    """What the import would do for one target provider (never mutates anything itself)."""

    target: ProviderTarget
    to_add: list[ModelInfo]
    skipped_existing: list[str]
    refused_reason: str | None = None
    errors: list[str] = field(default_factory=list)  # models that failed conversion (skipped, run continues)


def plan_import(
    models_by_modality: dict[Modality, list[dict[str, Any]]],
    existing_model_ids: dict[str, set[str]],
    *,
    provider_map: dict[str, ProviderTarget] = PROVIDER_MAP,
) -> tuple[dict[str, ImportPlan], list[str]]:
    """Compute the import plan without writing anything.

    A target is REFUSED (nothing added) until its ``pricing_source_url`` (and, for a new provider,
    its ``api_pattern``) is authored, so unverified rates cannot land before the config + human
    verification are done. Models already present in the target provider are skipped (never-clobber).
    Returns (plans keyed by target id, sorted list of unmapped PriceToken provider ids).
    """
    plans: dict[str, ImportPlan] = {}
    unmapped: set[str] = set()
    for modality, models in models_by_modality.items():
        for pt in models:
            pt_provider = pt.get('provider')
            target = provider_map.get(pt_provider) if isinstance(pt_provider, str) else None
            if target is None:
                if isinstance(pt_provider, str):
                    unmapped.add(pt_provider)
                continue

            plan = plans.setdefault(target.target_id, ImportPlan(target=target, to_add=[], skipped_existing=[]))

            if target.pricing_source_url is None or (target.is_new and target.api_pattern is None):
                plan.refused_reason = 'pricing_source_url / api_pattern not authored yet'
                continue

            try:
                model = convert_model(pt, modality, pricing_source_url=target.pricing_source_url)
            except ValueError as exc:
                # one malformed upstream entry must not abort the whole import
                plan.errors.append(f'{pt.get("modelId", "?")}: {exc}')
                continue
            if model.id in existing_model_ids.get(target.target_id, set()):
                plan.skipped_existing.append(model.id)
                continue
            if any(m.id == model.id for m in plan.to_add):
                continue  # de-duplicate within this import
            plan.to_add.append(model)

    return plans, sorted(unmapped)


def render_report(plans: dict[str, ImportPlan], unmapped: list[str]) -> str:
    """Human-readable summary of a plan (for the dry run and the grouped PR body, Codex #13)."""
    lines: list[str] = ['PriceToken import plan:']
    for target_id in sorted(plans):
        plan = plans[target_id]
        kind = 'new provider' if plan.target.is_new else 'existing provider'
        if plan.refused_reason:
            lines.append(f'  {target_id} ({kind}): REFUSED, {plan.refused_reason}')
        else:
            url = plan.target.pricing_source_url
            lines.append(
                f'  {target_id} ({kind}): +{len(plan.to_add)} to add, '
                f'{len(plan.skipped_existing)} already present -> verify against {url}'
            )
        if plan.errors:
            lines.append(f'    {len(plan.errors)} model(s) failed conversion: {"; ".join(plan.errors)}')
    if unmapped:
        lines.append(f'  unmapped PriceToken providers (skipped): {", ".join(unmapped)}')
    return '\n'.join(lines)


def _write_new_provider(plan: ImportPlan, providers_dir: Path) -> None:
    target = plan.target
    # Provider.validate requires models sorted by id, so sort before validating (get_provider_yaml_string
    # sorts again on write, but validation runs first and would otherwise reject an unsorted import).
    models = sorted(
        (m.model_dump(by_alias=True, mode='json', exclude_none=True) for m in plan.to_add),
        key=lambda m: cast('str', m['id']),
    )
    data: dict[str, Any] = {
        'id': target.target_id,
        'name': target.name,
        'api_pattern': target.api_pattern,
        'models': models,
    }
    Provider.model_validate(data)  # fail fast if the generated provider is not schema-valid
    path = providers_dir / f'{target.target_id}.yml'
    if path.exists():
        raise FileExistsError(f'{path} already exists; refusing to overwrite an existing provider')
    path.write_text(get_provider_yaml_string(cast(ProviderYamlDict, data)))


def _append_to_existing(plan: ImportPlan, providers_dir: Path) -> None:
    path = providers_dir / f'{plan.target.target_id}.yml'
    provider_yaml = ProviderYaml(path)
    for model in plan.to_add:
        # match-aware never-clobber: skip if the id matches ANY existing model's match clause, not just
        # an exact id, so an import cannot introduce a model that collides with an existing starts_with/
        # regex clause (which would produce a schema-invalid YAML on the next build).
        if provider_yaml.provider.find_model(model.id) is not None:
            continue
        provider_yaml.add_model(model)
    provider_yaml.save()


def apply_import(plans: dict[str, ImportPlan], *, providers_dir: Path | None = None) -> None:
    """Write the (non-refused) plans to disk. Imports land unverified; a human confirms on merge."""
    providers_dir = providers_dir or (package_dir / 'providers')
    for plan in plans.values():
        if plan.refused_reason or not plan.to_add:
            continue
        if plan.target.is_new:
            _write_new_provider(plan, providers_dir)
        else:
            _append_to_existing(plan, providers_dir)


def fetch_pricetoken_models(modality: Modality) -> list[dict[str, Any]]:  # pragma: no cover - network I/O
    import httpx
    from ruamel.yaml import YAML

    resp = httpx.get(f'{PRICETOKEN_RAW}/{modality}.yaml', timeout=30)
    resp.raise_for_status()
    data = cast('dict[str, Any]', YAML(typ='safe').load(resp.text))  # pyright: ignore[reportUnknownMemberType]
    return list(data['models'])


def import_pricetoken(*, write: bool = False) -> None:  # pragma: no cover - CLI glue over network + fs
    """CLI action: dry-run by default; pass write=True only after authoring URLs and verifying rates."""
    models_by_modality: dict[Modality, list[dict[str, Any]]] = {m: fetch_pricetoken_models(m) for m in ('tts', 'stt')}
    existing = {pid: {model.id for model in py.provider.models} for pid, py in get_providers_yaml().items()}
    plans, unmapped = plan_import(models_by_modality, existing)
    print(render_report(plans, unmapped))
    if not write:
        print('\nDry run: no files written. Complete PROVIDER_MAP urls and verify rates, then write=True.')
        return
    refused = sorted(p.target.target_id for p in plans.values() if p.refused_reason)
    if refused:
        raise SystemExit(f'Refusing to write: author pricing_source_url / api_pattern for {refused} first.')
    apply_import(plans)
    print('Wrote imports (source=imported, unverified). Run `make build`, verify each rate, set prices_checked.')
