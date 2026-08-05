"""Pull Telnyx Inference pricing from Telnyx's public pricing API.

Telnyx publishes rates at ``GET https://api.telnyx.com/v2/pricing/products/{slug}``, unauthenticated.
This reads the ``inference`` product and appends the models it can represent to
``prices/providers/telnyx.yml``.

Two things about that API drive the design of this module.

1. **Two products matter, and both need explicit paging.** ``inference`` carries the LLMs;
   ``voice-api`` carries text-to-speech and speech-to-text. Every endpoint here, including the
   product list itself, defaults to 20 items per page and silently ignores ``page_size``: the
   parameter is ``page[size]``. Reading the product list without it returns 20 of 33 products and
   hides ``voice-api`` entirely, which is exactly how this module first concluded, wrongly, that
   Telnyx published no voice pricing.

2. **The headline rate is not always the price.** Ten of the eighteen inference models report
   ``input_rate: "0.00"`` while their tier list prices the same dimension above 1,000,000 units.
   The API documents tiers as "volume-based" but does not say what the volume is measured over, and
   does not define how ``input_rate`` relates to ``input_tiers`` when they disagree. Publishing
   ``$0`` for those models would say a paid model is free, so they are refused rather than guessed
   at, and named in the report.

The two products are handled differently on purpose. Inference rows carry a machine ``model`` id, so
they can be imported automatically. Voice rows carry only a prose ``name`` ("Call Control Features
In-House HD Text-To-Speech - per character") and no id at all, so they cannot be mapped without a
curated table; those are drift-checked against the models this catalog already publishes, never
auto-added.

Only models whose every tier agrees with the headline rate are imported. Those land unverified
(``provenance.source = imported``, no ``prices_checked``), because a human sets the verified date.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Any, NamedTuple, cast

import httpx2
from pydantic import BaseModel, ConfigDict, Field

from .prices_types import ClauseEquals, ModelInfo, ModelPrice, Provenance
from .update import ProviderYaml
from .utils import package_dir

PRICING_API = 'https://api.telnyx.com/v2/pricing'
INFERENCE_SLUG = 'inference'
PRICING_SOURCE_URL = f'{PRICING_API}/products/{INFERENCE_SLUG}'

#: The only unit this importer knows how to convert. Anything else is refused rather than assumed.
SUPPORTED_UNIT = 'per_1k_tokens'
SUPPORTED_CURRENCY = 'USD'

#: Rates arrive per 1,000 tokens; the catalog stores dollars per 1,000,000.
PER_1K_TO_MTOK = 1000

IMPORT_ATTRIBUTION = (
    f'Imported from the Telnyx public pricing API ({PRICING_SOURCE_URL}), unit per_1k_tokens, '
    'converted x1000 to $/Mtok. Re-run `make telnyx-get` to refresh.'
)


class Tier(BaseModel):
    model_config = ConfigDict(extra='allow')

    min: int
    max: int | None = None
    rate: Decimal


class InferenceRow(BaseModel):
    """One row of the ``inference`` product.

    ``extra='allow'`` on purpose: Telnyx adding a field must not break the import, and this module
    refuses anything it does not positively understand rather than relying on strict validation.
    """

    model_config = ConfigDict(extra='allow', populate_by_name=True)

    model_id: str = Field(alias='model')
    input_rate: Decimal
    output_rate: Decimal
    cached_input_rate: Decimal | None = None
    input_tiers: list[Tier] = Field(default_factory=list)
    output_tiers: list[Tier] = Field(default_factory=list)
    cached_input_tiers: list[Tier] = Field(default_factory=list)
    unit: str
    currency: str


VOICE_SLUG = 'voice-api'
VOICE_SOURCE_URL = f'{PRICING_API}/products/{VOICE_SLUG}'

#: Voice rows are identified by prose, not by an id, so this map cannot be derived and is curated by
#: hand. Each key is the exact `name` Telnyx publishes; a rename shows up as a missing row, not as a
#: silently wrong price.
VOICE_ROWS: dict[str, str] = {
    'Call Control Features In-House Text-To-Speech - per character': 'natural',
    'Call Control Features In-House HD Text-To-Speech - per character': 'natural-hd',
    'Call Control Features Qwen3 Text-To-Speech - per character': 'qwen3tts',
    'Call Control Features Telnyx Ultra Text-To-Speech - per character': 'ultra',
    'Call Control Features Realtime Speech-To-Text In House (Telnyx) - per minute.': 'telnyx-stt',
}

#: First-party Telnyx voice models the API now prices that this catalog does not carry yet. Reported
#: so they are visible, never auto-added: each needs a `telnyx.<model>.<voice>` match clause
#: confirmed against the voice docs before it can route anything.
VOICE_UNLISTED: dict[str, str] = {
    'Bayan Text-To-Speech - charge is per character.': 'bayan',
    'Sukhan Text-To-Speech - charge is per character.': 'sukhan',
}


class VoiceRow(BaseModel):
    """One row of the ``voice-api`` product. Prose name, one rate, one unit."""

    model_config = ConfigDict(extra='allow')

    name: str
    rate: Decimal
    unit: str
    currency: str | None = None


def voice_rate_in_catalog_units(row: VoiceRow) -> tuple[str, Decimal] | None:
    """``(field, rate)`` in the unit the catalog stores, or None when the unit is not a voice one.

    Most of ``voice-api`` is call-control infrastructure (media forking, conferencing, per-number
    charges) rather than model pricing, so an unrecognised unit is skipped, not an error.
    """
    if row.unit == 'character':
        return 'input_kchars', row.rate * 1000  # $/char -> $/1k chars
    if row.unit == 'minute':
        return 'input_audio_kseconds', row.rate * 1000 / 60  # $/min -> $/1k seconds
    return None


class VoiceCheck(NamedTuple):
    unchanged: list[str]
    drifted: list[tuple[str, str]]
    unlisted: list[tuple[str, str]]
    missing_rows: list[str]


def check_voice(rows: list[VoiceRow], provider_yaml: ProviderYaml) -> VoiceCheck:
    """Compare the curated voice rows against what the catalog publishes.

    Never writes. These five rates were verified by hand against the pricing pages; this turns that
    one-off check into something a scheduled run can repeat.
    """
    by_name = {row.name: row for row in rows}
    by_id = {model.id: model for model in provider_yaml.provider.models}

    unchanged: list[str] = []
    drifted: list[tuple[str, str]] = []
    missing_rows: list[str] = []

    for name, model_id in VOICE_ROWS.items():
        row = by_name.get(name)
        if row is None:
            missing_rows.append(f'{name!r} (backs {model_id})')
            continue
        converted = voice_rate_in_catalog_units(row)
        model = by_id.get(model_id)
        if converted is None or model is None or not isinstance(model.prices, ModelPrice):
            missing_rows.append(f'{name!r} could not be compared against {model_id}')
            continue
        field, rate = converted
        current = getattr(model.prices, field)
        if current is None:
            drifted.append((model_id, f'{field}: catalog has no rate, API says {rate}'))
        elif abs(current - rate) / rate > Decimal('0.001'):
            drifted.append((model_id, f'{field}: catalog {current} vs API {rate}'))
        else:
            unchanged.append(model_id)

    unlisted = [
        (model_id, str(by_name[name].rate))
        for name, model_id in VOICE_UNLISTED.items()
        if name in by_name and model_id not in by_id
    ]
    return VoiceCheck(unchanged, drifted, unlisted, missing_rows)


def render_voice_report(check: VoiceCheck, total: int) -> str:
    lines = [
        f'Telnyx voice-api: {total} row(s) published, {len(check.unchanged)} unchanged, '
        f'{len(check.drifted)} drifted, {len(check.unlisted)} first-party model(s) not yet carried.',
        '',
    ]
    lines += [f'  DRIFT     {model_id}: {detail}' for model_id, detail in check.drifted]
    lines += [f'  UNCHANGED {model_id}' for model_id in check.unchanged]
    lines += [
        f'  AVAILABLE {model_id}: published at {rate} per unit, not in the catalog' for model_id, rate in check.unlisted
    ]
    lines += [f'  NO ROW    {detail}' for detail in check.missing_rows]
    if check.unlisted:
        lines += [
            '',
            '  AVAILABLE models are not added automatically: each needs a telnyx.<model>.<voice>',
            '  match clause confirmed against the voice docs before it can route.',
        ]
    if check.missing_rows:
        lines += ['', '  NO ROW means Telnyx renamed or dropped a row this map depends on. Fix VOICE_ROWS.']
    return '\n'.join(lines)


class Plan(NamedTuple):
    to_add: list[ModelInfo]
    unchanged: list[str]
    drifted: list[tuple[str, str]]
    refused: list[tuple[str, str]]


def flat_rate(headline: Decimal, tiers: list[Tier]) -> Decimal | None:
    """The rate when every tier agrees with the headline, else None.

    A disagreement is the ``input_rate: "0.00"`` case: the model reads free at the top level and
    priced in its highest tier. Since the API does not define what the tier bounds measure, there is
    no safe way to pick one number, so the caller refuses the model.
    """
    if not tiers:
        return headline
    return headline if {tier.rate for tier in tiers} == {headline} else None


def convert_row(row: InferenceRow) -> tuple[ModelInfo | None, str | None]:
    """Convert one API row into a catalog model, or explain why it cannot be converted."""
    if row.unit != SUPPORTED_UNIT:
        return None, f'unit is {row.unit!r}, expected {SUPPORTED_UNIT!r}'
    if row.currency != SUPPORTED_CURRENCY:
        return None, f'currency is {row.currency!r}, expected {SUPPORTED_CURRENCY!r}'

    input_rate = flat_rate(row.input_rate, row.input_tiers)
    output_rate = flat_rate(row.output_rate, row.output_tiers)
    if input_rate is None or output_rate is None:
        dimension = 'input' if input_rate is None else 'output'
        tiers = row.input_tiers if input_rate is None else row.output_tiers
        headline = row.input_rate if input_rate is None else row.output_rate
        rates = sorted({str(tier.rate) for tier in tiers})
        return None, (
            f'{dimension} headline rate {headline} disagrees with its tiers ({", ".join(rates)}); '
            'the API does not document what the tier bounds measure'
        )

    prices = ModelPrice(
        input_mtok=input_rate * PER_1K_TO_MTOK,
        output_mtok=output_rate * PER_1K_TO_MTOK,
    )
    # Validated whenever Telnyx publishes the field, including when the headline reads 0.00.
    # Decimal('0') is falsy, so gating this on truthiness would silently skip tier validation on the
    # cached dimension while input and output were being checked.
    if row.cached_input_rate is not None:
        cached = flat_rate(row.cached_input_rate, row.cached_input_tiers)
        if cached is None:
            rates = sorted({str(tier.rate) for tier in row.cached_input_tiers})
            return None, (
                f'cached input headline rate {row.cached_input_rate} disagrees with its tiers '
                f'({", ".join(rates)}); the API does not document what the tier bounds measure'
            )
        # A flat zero is ambiguous: it reads the same whether cache reads are free or the model has
        # no cache at all, and the API does not distinguish the two. Left unset rather than
        # published as free, which is the same call made everywhere else in this module.
        if cached > 0:
            prices.cache_read_mtok = cached * PER_1K_TO_MTOK

    return (
        ModelInfo(
            id=row.model_id,
            match=ClauseEquals(equals=row.model_id),
            prices=prices,
            pricing_source_url=cast('Any', PRICING_SOURCE_URL),
            price_comments=IMPORT_ATTRIBUTION,
            provenance=Provenance(source='imported', api_backed=True),
        ),
        None,
    )


PRICE_FIELDS = ('input_mtok', 'output_mtok', 'cache_read_mtok')


def price_diff(existing: ModelInfo, incoming: ModelInfo) -> str:
    """Describe how a published rate differs from what the catalog holds, or '' when it matches."""
    current, new = existing.prices, incoming.prices
    if not isinstance(current, ModelPrice):
        return 'catalog entry uses a tiered or conditional price that this importer cannot compare'
    assert isinstance(new, ModelPrice)
    changes = [
        f'{field} {getattr(current, field)} -> {getattr(new, field)}'
        for field in PRICE_FIELDS
        if getattr(current, field) != getattr(new, field)
    ]
    return ', '.join(changes)


def plan_import(rows: list[InferenceRow], provider_yaml: ProviderYaml) -> Plan:
    """Sort every published model into add, unchanged, drifted, or refused.

    Never clobbers. A model already in the catalog is compared and reported, never rewritten: a
    price change is for a human to confirm, the same rule every other source here follows.
    """
    to_add: list[ModelInfo] = []
    unchanged: list[str] = []
    drifted: list[tuple[str, str]] = []
    refused: list[tuple[str, str]] = []
    by_id = {model.id: model for model in provider_yaml.provider.models}

    for row in rows:
        model, reason = convert_row(row)
        if model is None:
            refused.append((row.model_id, reason or 'unknown'))
            continue
        # Two lookups, and neither implies the other. by_id catches a model whose id is already used
        # but whose clause does not match its own id (`telnyx-stt` is keyed by id and routed by
        # `equals: telnyx`), which find_model alone would miss, producing a duplicate id. find_model
        # catches the reverse: an id swallowed by another model's clause, which is how a collapsed
        # pair behaves (`gpt-5.1` resolves to the merged `gpt-5` entry).
        existing = by_id.get(model.id) or provider_yaml.provider.find_model(model.id)
        if existing is not None:
            diff = price_diff(existing, model)
            (drifted.append((model.id, diff)) if diff else unchanged.append(model.id))
            continue
        to_add.append(model)
    return Plan(to_add, unchanged, drifted, refused)


def render_report(plan: Plan, total: int) -> str:
    lines = [
        f'Telnyx inference: {total} model(s) published, {len(plan.to_add)} to add, '
        f'{len(plan.unchanged)} unchanged, {len(plan.drifted)} drifted, {len(plan.refused)} refused.',
        '',
    ]
    for model_id, diff in plan.drifted:
        lines.append(f'  DRIFT     {model_id}: {diff}')
    if plan.drifted:
        lines.append('')
    for model in plan.to_add:
        prices = cast('ModelPrice', model.prices)
        cached = f', cached {prices.cache_read_mtok}' if prices.cache_read_mtok is not None else ''
        lines.append(f'  ADD       {model.id}: in {prices.input_mtok}, out {prices.output_mtok}{cached} $/Mtok')
    if plan.to_add:
        lines.append('')
    for model_id in plan.unchanged:
        lines.append(f'  UNCHANGED {model_id}')
    if plan.unchanged:
        lines.append('')
    for model_id, reason in plan.refused:
        lines.append(f'  REFUSED   {model_id}: {reason}')
    if plan.refused:
        lines.append('')
        lines.append(
            '  Refused models are not free. Ask Telnyx what the tier bounds measure (monthly volume '
            'or request size) and whether input_rate or the top tier is the price above it.'
        )
    if plan.drifted:
        lines.append('  Drifted rates are NOT written. Confirm each against Telnyx, then update the')
        lines.append('  YAML and set prices_checked yourself.')
    return '\n'.join(lines)


def fetch_product(slug: str, row_type: type[Any]) -> list[Any]:  # pragma: no cover - network I/O
    """Read every page of one pricing product.

    `page[size]` is the bracket form the API actually honours: a plain `page_size` is accepted and
    silently ignored, which caps any request at the 20-item default. Paging is followed to the end
    rather than trusting one large page.
    """
    rows: list[Any] = []
    page = 1
    while True:
        response = httpx2.get(
            f'{PRICING_API}/products/{slug}',
            params={'page[number]': page, 'page[size]': 100},
            timeout=30,
        )
        response.raise_for_status()
        payload = cast('dict[str, Any]', response.json())
        rows += [row_type.model_validate(row) for row in cast('list[Any]', payload.get('data') or [])]
        meta = cast('dict[str, Any]', payload.get('meta') or {})
        total_pages = meta.get('total_pages')
        if not isinstance(total_pages, int) or page >= total_pages:
            return rows
        page += 1


def telnyx_get() -> int:  # pragma: no cover - CLI glue over network + fs
    """Import Telnyx LLM pricing and drift-check its voice rates (DRY_RUN=1 to only report)."""
    path = Path(package_dir) / 'providers' / 'telnyx.yml'
    provider_yaml = ProviderYaml(path)

    inference_rows = cast('list[InferenceRow]', fetch_product(INFERENCE_SLUG, InferenceRow))
    plan = plan_import(inference_rows, provider_yaml)
    print(render_report(plan, len(inference_rows)))

    # Voice is drift-check only: those rows carry no id, so the mapping is curated and adding a
    # model from it would mean inventing a match clause.
    voice_rows = cast('list[VoiceRow]', fetch_product(VOICE_SLUG, VoiceRow))
    check = check_voice(voice_rows, provider_yaml)
    print()
    print(render_voice_report(check, len(voice_rows)))

    blocking = bool(plan.drifted or check.drifted or check.missing_rows)

    if os.environ.get('DRY_RUN'):
        print('\nDRY_RUN set: nothing written.')
        return 1 if blocking else 0
    if not plan.to_add:
        print('\nNothing to add.')
        return 1 if blocking else 0

    for model in plan.to_add:
        provider_yaml.add_model(model)
    provider_yaml.save()
    print(f'\n{len(plan.to_add)} model(s) written to {path.name}. They land unverified: review the')
    print('rates, then add prices_checked yourself. Run `make build` to regenerate.')
    return 1 if blocking else 0
