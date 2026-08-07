"""Read Cerebras pricing from their keyless public models endpoint.

``GET https://api.cerebras.ai/public/v1/models`` needs no API key and returns per-token rates as
decimal strings (``{"prompt": "0.00000035", "completion": "0.00000075"}``), the same shape
OpenRouter publishes. It also carries ``limits.max_context_length``, ``deprecated`` and ``preview``.

That makes Cerebras the second provider in this catalog whose rates a machine can re-read, so its
models carry ``provenance.api_backed`` and this module is what makes that claim true.

The retirement check is the reason this exists. Cerebras removed three of the four models this
catalog listed (llama-3.3-70b, llama3.1-8b, qwen-3-32b) over nine months, and nothing noticed
because LLM rates have no automated freshness job. A model that is marked ``api_backed`` and then
vanishes from the endpoint is now reported on every run.

Like every other source here, this reports rather than rewrites prices: a rate that moved is for a
human to confirm, and only a human sets ``prices_checked``.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Any, NamedTuple, cast

import httpx2
from pydantic import BaseModel, ConfigDict, Field

from .prices_types import ClauseEquals, ClauseOr, ClauseStartsWith, ModelInfo, ModelPrice, Provenance
from .update import ProviderYaml
from .utils import package_dir

MODELS_API = 'https://api.cerebras.ai/public/v1/models'

#: Rates arrive per single token; the catalog stores dollars per 1,000,000.
PER_TOKEN_TO_MTOK = 1_000_000

#: Two rates within this relative distance are the same price, absorbing decimal-string rounding.
TOLERANCE = Decimal('0.001')


class Limits(BaseModel):
    model_config = ConfigDict(extra='allow')

    max_context_length: int | None = None


class Pricing(BaseModel):
    model_config = ConfigDict(extra='allow')

    prompt: Decimal | None = None
    completion: Decimal | None = None


class CerebrasModel(BaseModel):
    """One row of the models endpoint. Extra keys allowed so a new field cannot break the read."""

    model_config = ConfigDict(extra='allow', populate_by_name=True)

    model_id: str = Field(alias='id')
    name: str | None = None
    description: str | None = None
    pricing: Pricing | None = None
    limits: Limits | None = None
    deprecated: bool = False
    preview: bool = False


class Plan(NamedTuple):
    to_add: list[ModelInfo]
    unchanged: list[str]
    drifted: list[tuple[str, str]]
    retired: list[str]
    refused: list[tuple[str, str]]


def convert_model(row: CerebrasModel) -> tuple[ModelInfo | None, str | None]:
    """Build a catalog model from an API row, or explain why it cannot be built.

    Fails closed on a missing or non-positive rate rather than publishing a zero, which would read
    as "free" for a model that is merely undocumented.
    """
    if row.pricing is None or row.pricing.prompt is None or row.pricing.completion is None:
        return None, 'no prompt/completion pricing published'
    if row.pricing.prompt <= 0 or row.pricing.completion <= 0:
        return None, f'non-positive rate (prompt={row.pricing.prompt}, completion={row.pricing.completion})'

    prices = ModelPrice(
        input_mtok=row.pricing.prompt * PER_TOKEN_TO_MTOK,
        output_mtok=row.pricing.completion * PER_TOKEN_TO_MTOK,
    )
    # Mirrors the clause shape the hand-authored entries already use, so a generated model routes
    # the same way as one written by hand.
    match = ClauseOr.model_validate(
        {
            'or': [
                {'equals': row.model_id},
                {'starts_with': f'cerebras/{row.model_id}'},
                {'starts_with': f'cerebras:{row.model_id}'},
            ]
        }
    )
    return (
        ModelInfo(
            id=row.model_id,
            name=row.name,
            match=match,
            context_window=row.limits.max_context_length if row.limits else None,
            prices=prices,
            pricing_source_url=cast('Any', MODELS_API),
            price_comments=f'Imported from {MODELS_API}, per-token rates converted x1,000,000 to $/Mtok.',
            provenance=Provenance(source='imported', api_backed=True),
        ),
        None,
    )


def price_diff(existing: ModelInfo, incoming: ModelInfo) -> str:
    """How a published rate differs from the catalog's, or '' when they agree."""
    current, new = existing.prices, incoming.prices
    if not isinstance(current, ModelPrice):
        return 'catalog entry uses a tiered or conditional price that this importer cannot compare'
    assert isinstance(new, ModelPrice)
    changes: list[str] = []
    for field in ('input_mtok', 'output_mtok'):
        old_value = cast('Decimal | None', getattr(current, field))
        new_value = cast('Decimal | None', getattr(new, field))
        if old_value is None or new_value is None:
            if old_value != new_value:
                changes.append(f'{field} {old_value} -> {new_value}')
            continue
        if new_value == 0 or abs(old_value - new_value) / new_value > TOLERANCE:
            changes.append(f'{field} {old_value} -> {new_value}')
    return ', '.join(changes)


def plan_import(rows: list[CerebrasModel], provider_yaml: ProviderYaml) -> Plan:
    """Sort the published models into add / unchanged / drifted / refused, and find retirements."""
    to_add: list[ModelInfo] = []
    unchanged: list[str] = []
    drifted: list[tuple[str, str]] = []
    refused: list[tuple[str, str]] = []

    by_id = {model.id: model for model in provider_yaml.provider.models}
    published: set[str] = set()

    for row in rows:
        published.add(row.model_id)
        model, reason = convert_model(row)
        if model is None:
            refused.append((row.model_id, reason or 'unknown'))
            continue
        existing = by_id.get(model.id) or provider_yaml.provider.find_model(model.id)
        if existing is None:
            to_add.append(model)
            continue
        diff = price_diff(existing, model)
        (drifted.append((model.id, diff)) if diff else unchanged.append(model.id))

    # A model marked api_backed is one this catalog claims to re-read automatically. If it stops
    # being published, that claim is false and the entry needs retiring by hand.
    retired = sorted(
        model.id
        for model in provider_yaml.provider.models
        if model.provenance is not None
        and model.provenance.api_backed
        and model.id not in published
        and not model.removed
    )
    return Plan(to_add, unchanged, drifted, retired, refused)


def render_report(plan: Plan, total: int) -> str:
    lines = [
        f'Cerebras: {total} model(s) published, {len(plan.to_add)} to add, {len(plan.unchanged)} unchanged, '
        f'{len(plan.drifted)} drifted, {len(plan.retired)} retired, {len(plan.refused)} refused.',
        '',
    ]
    lines += [f'  RETIRED   {model_id}: marked api_backed but no longer published' for model_id in plan.retired]
    lines += [f'  DRIFT     {model_id}: {detail}' for model_id, detail in plan.drifted]
    for model in plan.to_add:
        prices = cast('ModelPrice', model.prices)
        lines.append(f'  ADD       {model.id}: in {prices.input_mtok}, out {prices.output_mtok} $/Mtok')
    lines += [f'  UNCHANGED {model_id}' for model_id in plan.unchanged]
    lines += [f'  REFUSED   {model_id}: {reason}' for model_id, reason in plan.refused]

    if plan.retired:
        lines += [
            '',
            '  A retired model is still being published by this catalog but no longer exists upstream.',
            '  Mark it `removed: true`, or keep it resolvable and drop its api_backed flag.',
        ]
    if plan.drifted:
        lines += ['', '  Drifted rates are NOT written. Confirm each, then update the YAML and set prices_checked.']
    return '\n'.join(lines)


def fetch_models() -> list[CerebrasModel]:  # pragma: no cover - network I/O
    """Read the keyless public models endpoint. No API key, no pagination."""
    response = httpx2.get(MODELS_API, timeout=30)
    response.raise_for_status()
    payload = cast('dict[str, Any]', response.json())
    return [CerebrasModel.model_validate(row) for row in cast('list[Any]', payload.get('data') or [])]


def cerebras_get() -> int:  # pragma: no cover - CLI glue over network + fs
    """Read Cerebras pricing from their public models API (DRY_RUN=1 to only report)."""
    path = Path(package_dir) / 'providers' / 'cerebras.yml'
    provider_yaml = ProviderYaml(path)
    rows = fetch_models()
    plan = plan_import(rows, provider_yaml)
    print(render_report(plan, len(rows)))

    blocking = bool(plan.drifted or plan.retired or plan.refused)
    if os.environ.get('DRY_RUN'):
        # A dry run writes nothing, so a model waiting to be added is still outstanding work and
        # has to be reported. A live run adds it, which is why this only applies here.
        print('\nDRY_RUN set: nothing written.')
        return 1 if blocking or plan.to_add else 0
    if not plan.to_add:
        print('\nNothing to add.')
        return 1 if blocking else 0

    for model in plan.to_add:
        provider_yaml.add_model(model)
    provider_yaml.save()
    print(f'\n{len(plan.to_add)} model(s) written to {path.name}. They land unverified: review the')
    print('rates, then add prices_checked yourself. Run `make build` to regenerate.')
    return 1 if blocking else 0


__all__ = [
    'MODELS_API',
    'CerebrasModel',
    'ClauseEquals',
    'ClauseStartsWith',
    'Plan',
    'cerebras_get',
    'convert_model',
    'fetch_models',
    'plan_import',
    'price_diff',
    'render_report',
]
