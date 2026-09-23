"""Import the structured rate card embedded in LiveKit's public pricing page."""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from typing import Any
from urllib.request import Request, urlopen

PRICING_URL = 'https://livekit.com/pricing/inference'


def page_data(html: str) -> dict[str, Any]:
    """Read the server-rendered JSON, failing rather than importing a partial table."""
    chunks: list[str] = []
    for match in re.finditer(r'self\.__next_f\.push\((.*?)\)</script>', html):
        part = json.loads(match[1])
        if len(part) > 1 and isinstance(part[1], str):
            chunks.append(part[1])
    payload = ''.join(chunks)
    start = payload.index('{"llmModels":')
    data, _ = json.JSONDecoder().raw_decode(payload[start:])
    for kind in ('llm', 'stt', 'tts'):
        if not data.get(kind + 'Models'):
            raise ValueError(f'LiveKit page has no {kind} rows')
    return data


def _rates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        expected = {'minute_usage': ('minute', 1), 'character_usage': ('characters', 1_000_000)}.get(
            row['metric'], ('tokens', 1_000_000)
        )
        if row['unit'] != {'currency': 'usd', 'measure': expected[0], 'per': expected[1]}:
            raise ValueError(f'Unexpected LiveKit billing unit: {row["unit"]}')
    return [
        {
            'metric': row['metric'],
            'unit': row['unit'],
            **{tier: row['unit_price'][tier]['amount'] for tier in ('build', 'ship', 'scale')},
        }
        for row in rows
    ]


def normalize(data: dict[str, Any], previous: dict[str, Any], checked: date) -> dict[str, Any]:
    """Keep every visible serving-provider row and preserve reviewed lookup aliases."""
    previous_rows = {e['model_id']: e for rows in previous.get('inference', {}).values() for e in rows}
    inference: dict[str, list[dict[str, Any]]] = {}
    for kind in ('stt', 'tts', 'llm'):
        inference[kind] = []
        seen: set[str] = set()
        for entry in data[kind + 'Models']:
            if entry.get('is_sunset') or entry.get('is_unlisted'):
                continue
            model_id, route = entry['model_id'], entry['provider_id']
            primary = model_id not in seen
            seen.add(model_id)
            catalog_id = model_id if primary else model_id + '@' + route
            aliases = list(previous_rows.get(model_id, {}).get('aliases', [])) if primary else []
            if primary and kind == 'llm':
                # Routing may change between refreshes; don't retain a stale primary alias.
                aliases = [alias for alias in aliases if '@' not in alias]
                aliases.append(model_id + '@' + route)
            row: dict[str, Any] = {
                'model_id': catalog_id,
                'model_label': entry['model_label'] + (f' ({entry["provider_label"]})' if kind == 'llm' else ''),
                'creator': entry['model_creator_label'],
                'provider': entry['provider_label'],
                'source_model_id': model_id,
                'serving_provider': route,
                'rates': _rates(entry['pricing_current']['rates']),
            }
            if aliases:
                row['aliases'] = list(dict.fromkeys(aliases))
            if entry.get('is_deprecated'):
                row.update(is_deprecated=True, retirement_date=entry['deprecation']['sunset_at'][:10])
            comments: list[str] = []
            rate = row['rates'][0]
            if kind == 'stt':
                comments.append(
                    f'Published Build/Ship ${rate["build"]}/minute and Scale ${rate["scale"]}/minute. Multiply by 1000/60 and round to six decimals for USD/1000 seconds.'
                )
            if kind == 'tts':
                comments.append(
                    f'Published Build/Ship ${rate["build"]} and Scale ${rate["scale"]} per million characters. Divide by 1000 for USD/1000 characters.'
                )
            if kind == 'llm':
                comments.append(
                    f'LiveKit serving provider: {route}. @provider suffixes are catalog lookup selectors, '
                    'not LiveKit SDK model IDs. The unqualified ID uses the first listed serving provider; '
                    'pass a qualified selector for provider-specific billing.'
                )
            promotions = [p for p in entry.get('promotions', []) if p['ends_at'][:10] >= checked.isoformat()]
            if len(promotions) > 1:
                raise ValueError(f'Multiple LiveKit promotions require review: {model_id}')
            if promotions:
                promo = promotions[0]
                if any(Decimal(r[t]) != 0 for r in row['rates'] for t in ('build', 'ship', 'scale')):
                    raise ValueError(f'Non-zero or future LiveKit promotion requires review: {model_id}')
                row['promotion'] = {
                    'starts_at': promo['starts_at'],
                    'ends_at': promo['ends_at'],
                    'regular_rates': _rates(promo['regular_rates']),
                }
                comments.append(
                    f'Zero-price promotion from {promo["starts_at"]} until {promo["ends_at"]}, '
                    'then regular rates resume.'
                )
            row['price_comments'] = ' '.join(comments)
            inference[kind].append(row)
    return {
        'source_url': PRICING_URL,
        'checked_date': checked.isoformat(),
        'source_format': 'Structured pricing data embedded in the official page; excludes sunset and unlisted rows.',
        'inference': inference,
    }


def refresh(previous: dict[str, Any], checked: date) -> dict[str, Any]:
    """Fetch the official page; ordinary generation remains offline and reproducible."""
    request = Request(PRICING_URL, headers={'User-Agent': 'voice-prices LiveKit pricing importer'})
    with urlopen(request, timeout=30) as response:
        return normalize(page_data(response.read().decode()), previous, checked)
