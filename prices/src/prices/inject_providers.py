from __future__ import annotations

import json
import re
from typing import Any, cast

from .build_docs import CATEGORIES, CATEGORY_META, DATA_JSON, build_catalog
from .update import get_providers_yaml
from .utils import root_dir


def inject_providers():
    readme_path = root_dir / 'README.md'
    readme_content = readme_path.read_text()
    text, count = re.subn(
        r'(\[comment\]: +<> +\(providers-start\)).+(\[comment\]: +<> +\(providers-end\))',
        providers_list,
        readme_content,
        flags=re.DOTALL,
    )
    assert count == 1, f'README.md contains {count} providers sections, expected 1'

    if text != readme_content:
        readme_path.write_text(text)
        print('README.md updated with providers list')
    else:
        print('README.md already up to date')


def providers_list(m: re.Match[str]):
    """Render the provider table: what each one prices, and in which categories.

    Counts come from the built catalog rather than the raw YAML, so they match the docs site and
    exclude unpriced and deprecated entries. A provider whose models are all unpriced still appears,
    with no categories, rather than silently vanishing from the README.
    """
    open_comment, close_comment = m.groups()
    providers_yml = get_providers_yaml()

    data = cast('list[dict[str, Any]]', json.loads(DATA_JSON.read_text()))
    catalog = build_catalog(data)

    priced: dict[str, dict[str, int]] = {}
    for category in CATEGORIES:
        for entry in catalog[category]:
            priced.setdefault(entry['id'], {})[category] = len(entry['models'])

    rows: list[str] = []
    for provider_yml in sorted(providers_yml.values(), key=lambda x: x.provider.id):
        provider = provider_yml.provider
        by_category = priced.get(provider.id, {})
        total = sum(by_category.values())
        labels = ', '.join(CATEGORY_META[c].tab for c in CATEGORIES if c in by_category) or '-'
        link = f'[{provider.name}](prices/providers/{provider_yml.path.name})'
        rows.append(f'| {link} | {total or len(provider.models)} | {labels} |')

    totals = {c: sum(len(e['models']) for e in catalog[c]) for c in CATEGORIES}
    summary = (
        f'**{len(providers_yml)} providers, {sum(totals.values()):,} priced models.** '
        + ', '.join(f'{totals[c]:,} {CATEGORY_META[c].tab}' for c in CATEGORIES if totals[c])
        + '.'
    )
    table = '\n'.join(['| Provider | Models | Categories |', '| --- | ---: | --- |', *rows])
    return f'{open_comment}\n\n{summary}\n\n{table}\n\n{close_comment}'
