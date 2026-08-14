"""Guard the low-trust path: validate what a pull request adds to ``prices/providers/*.yml``.

This runs on every PR, including PRs from forks, so it must be fully deterministic and
need no secrets. That constraint decides what it can be. It cannot tell whether a rate is
*true*: only the freshness verifier can, and that needs an API key and a browser, which a
fork PR does not get. What it can do is refuse the shapes a wrong rate takes.

Every check here exists because of a specific way a plausible-looking number gets in wrong:

- no ``pricing_source_url``          nothing to verify against, ever
- no ``price_comments``             a converted rate with the arithmetic hidden
- no or future ``prices_checked``   a date nobody actually stood behind
- an aggregator URL                 a rate copied from OpenRouter rather than the vendor
- a rate outside its band           the 1000x unit error, the single most common one

The bands below are deliberately loose: roughly an order of magnitude outside the widest
rate the catalog holds today. They are a unit-error trap, not a plausibility opinion. A
genuinely new price point should pass; ``$30`` in a field that means dollars per thousand
characters should not.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import yaml

PROVIDERS_DIR = Path('prices/providers')

# A screenshot is the one piece of evidence a generated rate cannot fabricate cheaply: it
# means someone loaded the vendor's page. Markdown image, HTML tag, or a GitHub upload URL.
_IMAGE_RE = re.compile(
    r'!\[[^\]]*\]\([^)]+\)|<img\s|https://github\.com/user-attachments/|https://user-images\.githubusercontent\.com/',
    re.IGNORECASE,
)

# (low, high) for each priced field, about 10x outside the observed catalog range.
PRICE_BANDS: dict[str, tuple[Decimal, Decimal]] = {
    'input_audio_kseconds': (Decimal('0.0005'), Decimal('2')),
    'output_audio_kseconds': (Decimal('0.0005'), Decimal('2')),
    'input_kchars': (Decimal('0.0001'), Decimal('1')),
    'output_kchars': (Decimal('0.0001'), Decimal('1')),
    'input_mtok': (Decimal('0.0005'), Decimal('2000')),
    'output_mtok': (Decimal('0.001'), Decimal('5000')),
    'input_audio_mtok': (Decimal('0.01'), Decimal('500')),
    'output_audio_mtok': (Decimal('0.1'), Decimal('1000')),
    'cache_read_mtok': (Decimal('0.0001'), Decimal('500')),
    'cache_write_mtok': (Decimal('0.001'), Decimal('1000')),
    'cache_audio_read_mtok': (Decimal('0.001'), Decimal('500')),
    'requests_kcount': (Decimal('0.001'), Decimal('10000')),
}

# Fields whose value is a unit conversion away from what the vendor publishes, so a
# contributor must show their working. Token fields are excluded: vendors quote those
# per million already, which is the field's own unit.
CONVERTED_FIELDS = frozenset(
    {'input_audio_kseconds', 'output_audio_kseconds', 'input_kchars', 'output_kchars', 'requests_kcount'}
)

# A rate copied from one of these is not evidence of what the vendor charges. The
# contribute page says so; this enforces it.
AGGREGATOR_HOSTS = frozenset(
    {
        'openrouter.ai',
        'litellm.ai',
        'helicone.ai',
        'llm-prices.com',
        'artificialanalysis.ai',
        'pricepertoken.com',
        'docsbot.ai',
        'huggingface.co',
    }
)

# How old a `prices_checked` date may be on a *newly contributed* model. A contributor is
# reading the page now, so anything older means the date was copied rather than set.
MAX_CHECKED_AGE_DAYS = 30


@dataclass(frozen=True)
class Finding:
    provider_id: str
    model_id: str
    message: str

    def __str__(self) -> str:
        return f'{self.provider_id}/{self.model_id}: {self.message}'


def is_api_backed(model: dict[str, Any]) -> bool:
    """True when an importer wrote this row, so a human never read a page for it."""
    provenance: object = model.get('provenance')
    if not isinstance(provenance, dict):
        return False
    return cast('dict[object, object]', provenance).get('api_backed') is True


def has_screenshot(pr_body: str) -> bool:
    return bool(_IMAGE_RE.search(pr_body))


def _git(*args: str) -> str | None:
    """Run a git command, returning None when it fails (file absent in the base ref)."""
    try:
        result = subprocess.run(['git', *args], capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return result.stdout


def base_is_resolvable(base_ref: str) -> bool:
    return _git('rev-parse', '--verify', '--quiet', f'{base_ref}^{{commit}}') is not None


def changed_provider_files(base_ref: str) -> list[Path]:
    """Provider YAMLs this branch touches relative to `base_ref`.

    Callers must check `base_is_resolvable` first. Falling back to "check every file" when
    the base ref is missing would be exactly backwards: a shallow clone would then report
    the whole catalog as newly added, turning an infrastructure gap into 1,300 false
    failures. No base means no claim to check.
    """
    out = _git('diff', '--name-only', f'{base_ref}...HEAD', '--', str(PROVIDERS_DIR))
    if out is None:
        return []
    return [Path(line) for line in out.split('\n') if line.endswith('.yml')]


def _load_yaml(text: str) -> dict[str, Any] | None:
    """Parse a provider YAML, returning None for anything that is not a mapping.

    Deliberately plain YAML rather than the pydantic types: the base-ref side of a diff may
    predate a schema change, and this check must never fail because history did not validate.
    """
    try:
        doc: object = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return cast('dict[str, Any]', doc) if isinstance(doc, dict) else None


def _models(doc: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not doc:
        return {}
    raw: object = doc.get('models') or []
    if not isinstance(raw, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for item in cast('list[object]', raw):
        if isinstance(item, dict) and 'id' in item:
            model = cast('dict[str, Any]', item)
            out[str(model['id'])] = model
    return out


def base_version(path: Path, base_ref: str) -> dict[str, Any] | None:
    """The provider YAML as it exists in the base ref, or None if the file is new."""
    out = _git('show', f'{base_ref}:{path}')
    return None if out is None else _load_yaml(out)


def _priced_fields(model: dict[str, Any]) -> dict[str, Decimal]:
    """Flat priced fields on a model, ignoring tiered and conditional shapes.

    Tiered and conditional prices are an advanced path a first-time contributor is not on,
    and reviewing them needs a human anyway. Skipping them here means this check never
    blocks on a shape it cannot reason about.
    """
    prices: object = model.get('prices')
    if not isinstance(prices, dict):
        return {}
    out: dict[str, Decimal] = {}
    for field, value in cast('dict[object, object]', prices).items():
        if not isinstance(field, str) or isinstance(value, dict | list):
            continue
        try:
            out[field] = Decimal(str(value))
        except (InvalidOperation, ValueError):
            continue
    return out


def check_model(
    provider_id: str,
    model: dict[str, Any],
    *,
    pricing_hosts: set[str],
    today: date,
) -> list[Finding]:
    model_id = str(model.get('id', '<unknown>'))
    found: list[Finding] = []

    def fail(message: str) -> None:
        found.append(Finding(provider_id, model_id, message))

    fields = _priced_fields(model)
    if not fields:
        return found  # nothing priced (or a tiered shape): nothing this check can say

    url = model.get('pricing_source_url')
    if not url:
        fail('no `pricing_source_url`. Every priced model needs the exact page the rate came from.')
    else:
        parsed = urlparse(str(url))
        if parsed.scheme != 'https':
            fail(f'`pricing_source_url` is not https: {url}')
        host = parsed.netloc.lower().removeprefix('www.')
        if host in AGGREGATOR_HOSTS:
            fail(
                f'`pricing_source_url` points at the aggregator {host}. '
                'An aggregator is a cross-check, not a source. Cite the vendor.'
            )
        elif pricing_hosts and host not in pricing_hosts:
            fail(
                f'`pricing_source_url` host {host} is not one of the provider `pricing_urls` hosts '
                f'({", ".join(sorted(pricing_hosts))}). Cite the vendor, or add the host to `pricing_urls`.'
            )

    checked = model.get('prices_checked')
    if checked is None:
        fail('no `prices_checked`. Set it to the date you read the pricing page.')
    elif isinstance(checked, date):
        if checked > today:
            fail(f'`prices_checked` is in the future: {checked}.')
        elif checked < today - timedelta(days=MAX_CHECKED_AGE_DAYS):
            fail(
                f'`prices_checked` is {(today - checked).days} days old ({checked}). '
                f'A newly added rate should have been read within {MAX_CHECKED_AGE_DAYS} days.'
            )

    if CONVERTED_FIELDS & fields.keys() and not model.get('price_comments'):
        converted = ', '.join(sorted(CONVERTED_FIELDS & fields.keys()))
        fail(
            f'`{converted}` is a converted unit but there is no `price_comments`. '
            'Record the rate as published and the arithmetic, so a reviewer can check it.'
        )

    for field, value in sorted(fields.items()):
        band = PRICE_BANDS.get(field)
        if band is None:
            continue
        low, high = band
        if value <= 0:
            continue  # a genuine zero (free tier, bundled) is a human call, not a unit error
        if not (low <= value <= high):
            fail(
                f'`{field}: {value}` is outside the plausible band {low} to {high}. '
                'This is almost always a unit error (per 1M entered into a per-1k field, or the reverse).'
            )

    return found


def check_contribution() -> int:
    """CLI action: validate every model this branch adds or reprices."""
    base_ref = os.environ.get('BASE_REF', 'origin/main')
    today = date.today()

    if not base_is_resolvable(base_ref):
        message = f'base ref {base_ref!r} is not available in this checkout, so there is nothing to diff against'
        # The CI job that enforces this sets REQUIRE_BASE=1 and checks out with fetch-depth 0.
        # There, an unresolvable base is a broken guard rather than a shallow clone, and must
        # fail loudly instead of quietly passing every PR.
        if os.environ.get('REQUIRE_BASE') == '1':
            print(f'{message} (REQUIRE_BASE=1): fetch the base ref, or unset REQUIRE_BASE')
            return 1
        print(f'{message}; skipping')
        return 0

    findings: list[Finding] = []
    checked_models = 0
    hand_read: list[str] = []

    for path in changed_provider_files(base_ref):
        if not path.exists():
            continue  # deleted in this branch
        head = _load_yaml(path.read_text())
        if not head:
            continue
        provider_id = str(head.get('id', path.stem))
        raw_urls: object = head.get('pricing_urls') or []
        urls = cast('list[object]', raw_urls) if isinstance(raw_urls, list) else []
        pricing_hosts = {urlparse(str(u)).netloc.lower().removeprefix('www.') for u in urls} - {''}

        before = _models(base_version(path, base_ref))
        for model_id, model in _models(head).items():
            was = before.get(model_id)
            if was is not None and was.get('prices') == model.get('prices'):
                continue  # unchanged price: not this PR's claim to defend
            checked_models += 1
            findings.extend(check_model(provider_id, model, pricing_hosts=pricing_hosts, today=today))
            if _priced_fields(model) and not is_api_backed(model):
                hand_read.append(f'{provider_id}/{model_id}')

    if not checked_models:
        print('no added or repriced models in this branch')
        return 0

    print(f'checked {checked_models} added or repriced model(s) against {base_ref}')

    # PR_BODY is only set by the workflow, so a local run never fails on a missing screenshot.
    pr_body = os.environ.get('PR_BODY')
    if pr_body is not None and hand_read and not has_screenshot(pr_body):
        print(
            f'\nNo screenshot in the pull request body, but {len(hand_read)} rate(s) were read from a page by hand:\n'
        )
        for name in hand_read:
            print(f'  {name}')
        print(
            '\nAttach a screenshot of the vendor pricing page showing these rates. It is the one\n'
            'piece of evidence that someone actually loaded the page. Rates written by an importer\n'
            '(`provenance.api_backed: true`) are exempt, because no human read a page for those.'
        )
        return 1

    if not findings:
        print('all good')
        return 0

    print(f'\n{len(findings)} problem(s) found:\n')
    for finding in findings:
        print(f'  {finding}')
    print(
        '\nThese are structural checks, not a claim that the rates are wrong. '
        'See https://prices.voicegateway.dev/contribute for what each field is for.'
    )
    return 1
