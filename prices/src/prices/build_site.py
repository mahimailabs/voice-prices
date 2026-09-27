"""Write the catalog the prices.mahimai.ca site renders, from the built price data.

Reads the committed ``prices/data.json`` and writes ``site/src/data/catalog.json``: the same
per-category catalog and LiveKit comparison the Mintlify pages are generated from
(``build_docs.build_catalog`` and ``build_docs.build_comparison``), plus the per-model source
fields the site shows next to a rate. Reusing those two functions is the point: the site and the
docs pages can never disagree about which tab a model is on or what its display rate is.

The file is committed so the site builds with Node alone, and like the docs pages it is a pure
function of ``data.json``: no build date, nothing that changes with the clock. Staleness is worked
out in the browser against the viewer's own date, using the provider threshold written here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

from .build_docs import (
    ADD_PROVIDER_URL,
    CATEGORIES,
    CATEGORY_COLUMNS,
    DATA_JSON,
    GATEWAY_PROVIDERS,
    HUGGINGFACE_PREFIX,
    REMOVAL_FORM_URL,
    REPO_URL,
    TOKEN_COLUMNS,
    _resolve_direct,  # pyright: ignore[reportPrivateUsage]
    build_catalog,
    build_comparison,
    distinct_rows,
    gateway_summary,
    slug,
)
from .utils import package_dir, root_dir

SITE_DATA = root_dir / 'site' / 'src' / 'data' / 'catalog.json'

# The staleness threshold a provider gets when its YAML does not set one; matches the package.
DEFAULT_STALENESS_DAYS = 60


def _group(provider_id: str) -> str:
    if provider_id in GATEWAY_PROVIDERS:
        return 'gateway'
    if provider_id.startswith(HUGGINGFACE_PREFIX):
        return 'huggingface'
    return 'direct'


def _provider_files() -> dict[str, str]:
    """Provider id -> its YAML file name. Ids and file names differ in places (hyphen vs underscore)."""
    files: dict[str, str] = {}
    for path in sorted((package_dir / 'providers').glob('*.yml')):
        match = re.search(r'^id: *(\S+)', path.read_text(), re.MULTILINE)
        if match:
            files[match.group(1).strip('\'"')] = path.name
    return files


def _direct_ref(livekit_id: str, direct_rate: float | None) -> str | None:
    if direct_rate is None:
        return None
    ref = _resolve_direct(livekit_id)
    return f'{ref[0]}/{ref[1]}' if ref else None


def _model_details(data: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Per-model fields the catalog rows leave out: where the rate was read, and the caveats."""
    details: dict[tuple[str, str], dict[str, Any]] = {}
    for provider in data:
        provider_id = str(provider.get('id', ''))
        for raw_model in cast('list[Any]', provider.get('models') or []):
            if not isinstance(raw_model, dict):
                continue
            model = cast('dict[str, Any]', raw_model)
            source = model.get('pricing_source_url')
            comments = model.get('price_comments')
            details[(provider_id, str(model.get('id', '')))] = {
                'source': source if isinstance(source, str) else None,
                'comments': comments if isinstance(comments, str) else None,
            }
    return details


def build_site_data(data: list[dict[str, Any]]) -> dict[str, Any]:
    catalog = build_catalog(data)
    comparison = build_comparison(data)
    details = _model_details(data)
    providers = {str(p.get('id', '')): p for p in data}
    files = _provider_files()

    categories: dict[str, Any] = {}
    for category in CATEGORIES:
        entries: list[dict[str, Any]] = []
        for entry in catalog[category]:
            provider = providers[entry['id']]
            threshold = provider.get('staleness_threshold_days')
            urls = provider.get('pricing_urls')
            models: list[dict[str, Any]] = []
            for row in entry['models']:
                extra = details.get((entry['id'], row['id']), {})
                model: dict[str, Any] = {
                    'id': row['id'],
                    'name': row['name'],
                    # Only the values that exist, so the file stays small; the site treats a
                    # missing key exactly as the docs treat None.
                    'values': {k: v for k, v in row['values'].items() if v is not None},
                }
                if row['markers']:
                    model['markers'] = row['markers']
                if row['status'] is not None:
                    model['status'] = row['status']
                if row['verified'] is not None:
                    model['verified'] = row['verified']
                if extra.get('source'):
                    model['source'] = extra['source']
                if extra.get('comments'):
                    model['comments'] = extra['comments']
                models.append(model)
            entries.append(
                {
                    'id': entry['id'],
                    'slug': slug(entry['id']),
                    'name': entry['name'],
                    'group': _group(entry['id']),
                    'pricingTier': entry['pricing_tier'],
                    'pricingUrls': [u for u in cast('list[Any]', urls) if isinstance(u, str)]
                    if isinstance(urls, list)
                    else [],
                    'yaml': files.get(entry['id']),
                    'stalenessDays': threshold if isinstance(threshold, int) else DEFAULT_STALENESS_DAYS,
                    'models': models,
                }
            )
        categories[category] = {
            'columns': [c._asdict() for c in CATEGORY_COLUMNS[category]],
            'tokenColumns': [c._asdict() for c in TOKEN_COLUMNS.get(category, ())],
            'providers': entries,
            # Each LiveKit row also names the direct model it is compared against, so the site can
            # price one pick both ways without re-deriving the alias table in JavaScript.
            # Provider-pinned duplicates (`...@openai`) are left out, so the site counts each model once,
            # the same way the README's generated numbers do.
            'comparison': [
                {**row, 'direct_ref': _direct_ref(row['id'], row['direct'])}
                for row in distinct_rows(comparison[category])
            ],
        }

    summary = gateway_summary(comparison)
    return {
        'repo': REPO_URL,
        'gatewaySummary': {
            'compared': summary['compared'],
            'atOrBelow': summary['at_or_below'],
            'scaleBelow': summary['scale_below'],
            'llmCompared': summary['llm_compared'],
            'llmIdentical': summary['llm_identical'],
        },
        'removalForm': REMOVAL_FORM_URL,
        'addProvider': ADD_PROVIDER_URL,
        'categories': categories,
    }


def render_site_data(data: list[dict[str, Any]]) -> str:
    return json.dumps(build_site_data(data), indent=1, ensure_ascii=False) + '\n'


def build_site(path: Path | None = None) -> None:
    """Write site/src/data/catalog.json for the prices.mahimai.ca site from prices/data.json."""
    path = path or SITE_DATA
    data = cast('list[dict[str, Any]]', json.loads(DATA_JSON.read_text()))
    content = render_site_data(data)
    if path.exists() and path.read_text() == content:
        print(f'{path} unchanged')
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    print(f'wrote {path}')
