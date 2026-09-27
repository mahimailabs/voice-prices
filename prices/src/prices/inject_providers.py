from __future__ import annotations

import json
import re
from typing import Any, cast

from .build_docs import (
    CATEGORIES,
    CATEGORY_META,
    DATA_JSON,
    ComparisonRow,
    Modality,
    build_catalog,
    build_comparison,
    fmt_pct,
    fmt_usd,
    gateway_summary,
)
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
    text, count = re.subn(
        r'(\[comment\]: +<> +\(gateway-start\)).+(\[comment\]: +<> +\(gateway-end\))',
        gateway_block,
        text,
        flags=re.DOTALL,
    )
    assert count == 1, f'README.md contains {count} gateway sections, expected 1'

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


_UNIT = {'stt': 'per min', 'tts': 'per 1M chars', 'llm': 'per 1M input tokens'}


def _example_row(category: Modality, row: ComparisonRow) -> str:
    delta = round(row['delta'] or 0.0, 1)
    scale_below = row['scale'] is not None and row['direct'] is not None and row['scale'] < row['direct']
    verdict = f'**{fmt_pct(row["delta"])}**'
    if delta == 0:
        verdict += ', and Scale is under direct' if scale_below else ', pass-through'
    elif delta > 0 and scale_below:
        verdict += ', but Scale is under direct'
    unit = _UNIT.get(category, CATEGORY_META[category].tab)
    scale = fmt_usd(row['scale']) if row['scale'] is not None else 'n/a'
    return (
        f'| `{row["id"]}` ({CATEGORY_META[category].tab}, {unit}) | {fmt_usd(row["direct"])} | '
        f'{fmt_usd(row["livekit"])} | {scale} | {verdict} |'
    )


def gateway_block(m: re.Match[str]) -> str:
    """The README's direct-vs-LiveKit paragraph and example table, from the same comparison the
    docs and prices.mahimai.ca render, so the headline numbers cannot drift from the data."""
    open_comment, close_comment = m.groups()
    data = cast('list[dict[str, Any]]', json.loads(DATA_JSON.read_text()))
    s = gateway_summary(build_comparison(data))

    intro = (
        f'It is not an argument against gateways. Of the {s["compared"]} models where both a direct and '
        f'a gateway rate exist, **{s["at_or_below"]} are priced at or below going direct**, and on the '
        f"discounted Scale tier {s['scale_below']} come in below the vendor's own price. The point is to "
        'make the number visible, whichever way it falls.'
    )
    table = '\n'.join(
        [
            '| Model | Direct | LiveKit (Build/Ship) | LiveKit Scale | vs direct |',
            '|---|---|---|---|---|',
            *(_example_row(category, row) for category, row in s['examples']),
        ]
    )
    llm = (
        f'Most LLM rates pass straight through ({s["llm_identical"]} of the {s["llm_compared"]} '
        'comparable models are identical to the penny).'
    )
    doubled = s['llm_doubled']
    if doubled:
        names = doubled[0] if len(doubled) == 1 else f'{", ".join(doubled[:-1])} and {doubled[-1]}'
        llm += (
            ' The exception is worth knowing: the frontier models are not passed through. '
            f'{names} {"is" if len(doubled) == 1 else "are each"} priced at double the direct rate or more.'
        )
    return f'{open_comment}\n\n{intro}\n\n{table}\n\n{llm}\n\n{close_comment}'
