"""Validate a provider-published pricing feed and diff it against the catalog.

The feed format is documented at ``docs/pricing-feed.mdx``. It exists so a provider can publish
their own rates once, in a machine-readable file, instead of every downstream tool scraping their
pricing page and getting it subtly wrong.

The design decision that makes it work: every rate is quoted **per one base unit** (one character,
one second, one token, one request) as a decimal string. There is no "per 1k" or "per 1M" anywhere,
so there is nothing to misread. This mirrors what OpenRouter and LiteLLM already publish for LLMs;
the only thing added here is the pair of meters voice needs (characters and seconds of audio).

This module deliberately never writes to ``prices/providers/``. It reports. A human decides what to
change and sets ``prices_checked``, exactly as with every other source in this package.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, NamedTuple, cast

import httpx2
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from .build_docs import base_prices, detect_modality
from .utils import package_dir

DATA_JSON = package_dir / 'data.json'

#: What is being metered. Mirrors the direction/modality split already used by ModelPrice.
Meter = Literal['text_input', 'text_output', 'cached_text_input', 'audio_input', 'audio_output', 'request']

#: The base unit a rate is quoted per. Always singular: one character, one second, one token.
Unit = Literal['character', 'token', 'second', 'minute', 'request']


class RateMap(NamedTuple):
    """Where a (meter, unit) pair lands in ModelPrice, and the exact factor to get there.

    The factor is kept as a numerator and denominator rather than a single Decimal so that
    per-minute rates convert exactly: 0.015/min becomes 0.015 * 1000 / 60 = 0.25 with no repeating
    decimal in the middle.
    """

    field: str
    numerator: int
    denominator: int = 1

    def convert(self, amount: Decimal) -> Decimal:
        return amount * self.numerator / self.denominator


#: The complete meter/unit grid. Anything not in here is refused rather than guessed at, because a
#: wrong unit is indistinguishable from a wrong price once it is in the catalog.
RATE_FIELDS: dict[tuple[str, str], RateMap] = {
    ('text_input', 'token'): RateMap('input_mtok', 1_000_000),
    ('text_output', 'token'): RateMap('output_mtok', 1_000_000),
    ('cached_text_input', 'token'): RateMap('cache_read_mtok', 1_000_000),
    ('text_input', 'character'): RateMap('input_kchars', 1_000),
    ('audio_input', 'second'): RateMap('input_audio_kseconds', 1_000),
    ('audio_input', 'minute'): RateMap('input_audio_kseconds', 1_000, 60),
    ('audio_output', 'second'): RateMap('output_audio_kseconds', 1_000),
    ('audio_output', 'minute'): RateMap('output_audio_kseconds', 1_000, 60),
    ('audio_input', 'token'): RateMap('input_audio_mtok', 1_000_000),
    ('audio_output', 'token'): RateMap('output_audio_mtok', 1_000_000),
    ('request', 'request'): RateMap('requests_kcount', 1_000),
}

#: Two rates that convert to within this relative distance are the same price. Per-minute and
#: per-second rates round differently on the way in, so exact equality would report false drift.
TOLERANCE = Decimal('0.001')


class FeedRate(BaseModel):
    """One priced meter. Extra keys are refused: this is the part that is money."""

    model_config = ConfigDict(extra='forbid')

    meter: Meter
    unit: Unit
    amount: Decimal
    plan: str | None = None
    voice_class: str | None = None
    from_quantity: int | None = None
    effective_from: str | None = None


class FeedModel(BaseModel):
    """One model. Extra keys are allowed so a provider can carry their own metadata."""

    model_config = ConfigDict(extra='allow')

    id: str
    name: str | None = None
    modality: Literal['stt', 'llm', 'tts', 's2s', 'vad'] | None = None
    status: Literal['ga', 'preview', 'deprecated'] | None = None
    sunset_on: str | None = None
    docs_url: str | None = None
    rates: list[FeedRate]


class Feed(BaseModel):
    """A provider's published rate card.

    ``currency`` is validated rather than merely defaulted. This catalog publishes USD only and
    does not convert, so a feed declaring anything else must be rejected outright: accepting it
    would ingest the amounts as dollars and silently publish a wrong price under the vendor's
    own name, which is the worst failure this project has. Rejecting is recoverable; a quietly
    mis-scaled rate is not.
    """

    model_config = ConfigDict(extra='allow')

    provider: str
    updated: str | None = None
    currency: str = 'USD'
    models: list[FeedModel]

    @field_validator('currency')
    @classmethod
    def _usd_only(cls, value: str) -> str:
        if value.upper() != 'USD':
            raise ValueError(
                f'currency is {value!r}, but this catalog publishes USD only and does not convert. '
                'Serve a USD rate card, or open an issue if you need another currency supported.'
            )
        return value.upper()


class Finding(NamedTuple):
    kind: Literal['DRIFT', 'NEW', 'MISSING', 'UNSUPPORTED', 'MATCH']
    model_id: str
    detail: str


def base_rates(model: FeedModel) -> tuple[FeedRate, ...]:
    """The rates that set the headline price: no plan, no volume tier, no voice class.

    A feed may legitimately carry several rates per meter (a cheaper enterprise plan, a volume
    break, a premium voice). The catalog stores one headline rate per field, so only the
    unqualified rates are comparable. The rest are reported as context, not silently averaged.
    """
    return tuple(
        rate for rate in model.rates if rate.plan is None and rate.voice_class is None and not rate.from_quantity
    )


def convert_rates(model: FeedModel) -> tuple[dict[str, Decimal], list[str]]:
    """Convert a model's base rates into ModelPrice fields. Returns ``(fields, problems)``.

    Fails closed: an unknown meter/unit pair, or two rates competing for the same field, is
    reported rather than resolved by guesswork.
    """
    fields: dict[str, Decimal] = {}
    problems: list[str] = []
    for rate in base_rates(model):
        mapping = RATE_FIELDS.get((rate.meter, rate.unit))
        if mapping is None:
            problems.append(f'no mapping for meter={rate.meter} unit={rate.unit}')
            continue
        if mapping.field in fields:
            problems.append(f'two base rates both map to {mapping.field}')
            continue
        if rate.amount < 0:
            problems.append(f'negative amount for {rate.meter}')
            continue
        fields[mapping.field] = mapping.convert(rate.amount)
    return fields, problems


def load_feed(source: str) -> tuple[Feed, list[str]]:
    """Read a feed from a URL or a local path. Returns ``(feed, warnings)``.

    Warnings cover things that are legal JSON but a bad idea, chiefly amounts published as JSON
    numbers. ``0.0000003`` as a float does not survive a round trip intact, which is why every
    published LLM feed (OpenRouter, LiteLLM) quotes amounts as strings.
    """
    if source.startswith(('http://', 'https://')):
        response = httpx2.get(source, timeout=30, follow_redirects=True)
        response.raise_for_status()
        raw_text = response.text
    else:
        raw_text = Path(source).read_text()

    raw = cast('dict[str, Any]', json.loads(raw_text))
    warnings: list[str] = []
    for model in cast('list[Any]', raw.get('models') or []):
        if not isinstance(model, dict):
            continue
        entry = cast('dict[str, Any]', model)
        for rate in cast('list[Any]', entry.get('rates') or []):
            if isinstance(rate, dict) and isinstance(cast('dict[str, Any]', rate).get('amount'), float):
                warnings.append(
                    f'{entry.get("id")}: amount for {cast("dict[str, Any]", rate).get("meter")} is a JSON '
                    'number. Publish amounts as strings so small rates survive the round trip.'
                )
    return Feed.model_validate(raw), warnings


def _fmt(value: Decimal) -> str:
    """Print a converted rate without trailing zeros, and never in scientific notation."""
    normalized = value.normalize()
    _, _, exponent = normalized.as_tuple()
    if isinstance(exponent, int) and exponent > 0:
        normalized = normalized.quantize(Decimal(1))
    return f'{normalized:f}'


def _close(a: Decimal, b: Decimal) -> bool:
    if a == b:
        return True
    if a == 0 or b == 0:
        return False
    return abs(a - b) / max(abs(a), abs(b)) <= TOLERANCE


def _catalog_provider(data: list[dict[str, Any]], provider_id: str) -> dict[str, Any] | None:
    return next((p for p in data if str(p.get('id')) == provider_id), None)


def compare(feed: Feed, data: list[dict[str, Any]]) -> list[Finding]:
    """Diff a feed against the committed catalog.

    Matching is by exact model id, which is the whole point of the format: the id a provider
    publishes is the string their API accepts. Anything that does not match is reported for a human
    to map rather than being fuzzily resolved.
    """
    provider = _catalog_provider(data, feed.provider)
    catalog_models: dict[str, dict[str, Any]] = {}
    if provider is not None:
        for raw in cast('list[Any]', provider.get('models') or []):
            if isinstance(raw, dict):
                catalog_models[str(cast('dict[str, Any]', raw).get('id'))] = cast('dict[str, Any]', raw)

    findings: list[Finding] = []
    seen: set[str] = set()
    feed_modalities: set[str] = set()

    for model in feed.models:
        fields, problems = convert_rates(model)
        for problem in problems:
            findings.append(Finding('UNSUPPORTED', model.id, problem))
        if not fields:
            continue

        modality = model.modality or detect_modality({k: float(v) for k, v in fields.items()})
        if modality:
            feed_modalities.add(modality)

        existing = catalog_models.get(model.id)
        if existing is None:
            rates = ', '.join(f'{field}={_fmt(value)}' for field, value in sorted(fields.items()))
            findings.append(Finding('NEW', model.id, f'not in the catalog ({rates})'))
            continue

        seen.add(model.id)
        current, _, _ = base_prices(existing.get('prices'))
        for field, value in sorted(fields.items()):
            if field not in current:
                findings.append(Finding('DRIFT', model.id, f'{field}: catalog has no rate, feed says {_fmt(value)}'))
                continue
            catalog_value = Decimal(str(current[field]))
            if _close(catalog_value, value):
                findings.append(Finding('MATCH', model.id, f'{field} = {_fmt(catalog_value)}'))
            else:
                delta = (value - catalog_value) / catalog_value * 100 if catalog_value else Decimal(0)
                findings.append(
                    Finding(
                        'DRIFT',
                        model.id,
                        f'{field}: catalog {_fmt(catalog_value)} vs feed {_fmt(value)} ({delta:+.1f}%)',
                    )
                )

    for model_id, raw in sorted(catalog_models.items()):
        if model_id in seen or raw.get('deprecated') is True:
            continue
        flat, _, _ = base_prices(raw.get('prices'))
        if not flat:
            continue
        modality = detect_modality(flat, (feed.provider, model_id))
        if modality in feed_modalities:
            findings.append(Finding('MISSING', model_id, f'in the catalog as {modality}, absent from the feed'))

    return findings


def render_report(feed: Feed, findings: list[Finding], warnings: list[str]) -> str:
    """A terminal-readable summary. Drift first, because that is the reason to run this."""
    order = ['DRIFT', 'UNSUPPORTED', 'NEW', 'MISSING', 'MATCH']
    counts = {kind: sum(1 for f in findings if f.kind == kind) for kind in order}
    lines = [
        f'feed: {feed.provider} ({len(feed.models)} models, currency {feed.currency}'
        + (f', updated {feed.updated}' if feed.updated else '')
        + ')',
        '  ' + '  '.join(f'{kind.lower()}={counts[kind]}' for kind in order),
        '',
    ]
    for warning in warnings:
        lines.append(f'  WARN  {warning}')
    if warnings:
        lines.append('')
    for kind in order:
        if kind == 'MATCH':
            continue
        rows = [f for f in findings if f.kind == kind]
        for finding in rows:
            lines.append(f'  {finding.kind:<12} {finding.model_id}: {finding.detail}')
        if rows:
            lines.append('')
    if not counts['DRIFT'] and not counts['UNSUPPORTED']:
        lines.append('  no drift against the committed catalog.')
    else:
        lines.append('  review each drift by hand, then update the YAML and set prices_checked yourself.')
    return '\n'.join(lines)


def render_rate_grid() -> str:
    """The supported meter/unit grid as a markdown table, generated for the docs page.

    Injected into `docs/pricing-feed.mdx` by `make build-docs`, so the published spec is the code
    rather than a description of it that slowly stops being true.
    """
    rows = ['| Meter | Unit | Catalog field | Conversion |', '| --- | --- | --- | --- |']
    for (meter, unit), mapping in sorted(RATE_FIELDS.items()):
        factor = f'amount x {mapping.numerator:,}'
        if mapping.denominator != 1:
            factor += f' / {mapping.denominator}'
        rows.append(f'| `{meter}` | `{unit}` | `{mapping.field}` | {factor} |')
    return '\n'.join(rows)


def registered_feeds() -> list[tuple[str, str]]:
    """Every provider that publishes a pricing endpoint, as ``(provider_id, url)``.

    The registry is the provider YAML itself (`pricing_feed_url`), so adding a provider to the poll
    is a one-line change reviewed like any other, and there is no second list to fall out of sync.
    """
    from .update import get_providers_yaml

    feeds: list[tuple[str, str]] = []
    for provider_yml in sorted(get_providers_yaml().values(), key=lambda x: x.provider.id):
        url = provider_yml.provider.pricing_feed_url
        if url is not None:
            feeds.append((provider_yml.provider.id, str(url)))
    return feeds


def feed_sync() -> int:
    """Poll every registered provider pricing endpoint and report rates that have moved."""
    feeds = registered_feeds()
    if not feeds:
        print('no provider publishes a pricing endpoint yet (set pricing_feed_url in a provider YAML)')
        return 0

    data = cast('list[dict[str, Any]]', json.loads(DATA_JSON.read_text()))
    blocking = 0
    unreachable = 0

    for provider_id, url in feeds:
        print(f'\n=== {provider_id}: {url} ===')
        try:
            feed, warnings = load_feed(url)
        except ValidationError as exc:
            print(f'  INVALID   the endpoint no longer matches the feed format:\n{exc}')
            blocking += 1
            continue
        except (OSError, ValueError, httpx2.HTTPError) as exc:
            # An endpoint being down is not a pricing problem, so it is reported but does not fail
            # the run. A permanently dead feed shows up as a repeated warning.
            print(f'  UNREACHABLE  {exc}')
            unreachable += 1
            continue

        if feed.provider != provider_id:
            print(f'  WARN  endpoint declares provider "{feed.provider}" but is registered under "{provider_id}"')

        findings = compare(feed, data)
        print(render_report(feed, findings, warnings))
        blocking += sum(1 for f in findings if f.kind in ('DRIFT', 'UNSUPPORTED'))

    print(f'\n{len(feeds)} endpoint(s) polled, {unreachable} unreachable, {blocking} rate(s) needing a human look.')
    return 1 if blocking else 0


def feed_check() -> int:
    """Validate a provider pricing feed and diff it against the catalog (set FEED=<url|path>)."""
    source = os.environ.get('FEED', '').strip()
    if not source:
        print(
            'set FEED to the feed URL or path, e.g. make feed-check FEED=https://api.acme.com/.well-known/pricing.json'
        )
        return 1

    try:
        feed, warnings = load_feed(source)
    except ValidationError as exc:
        print(f'{source} is not a valid pricing feed:\n{exc}')
        return 1
    except (OSError, ValueError, httpx2.HTTPError) as exc:
        print(f'could not read {source}: {exc}')
        return 1

    data = cast('list[dict[str, Any]]', json.loads(DATA_JSON.read_text()))
    findings = compare(feed, data)
    print(render_report(feed, findings, warnings))
    blocking = sum(1 for f in findings if f.kind in ('DRIFT', 'UNSUPPORTED'))
    return 1 if blocking else 0
