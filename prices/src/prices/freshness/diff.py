"""Classify an extraction against the catalog, with deterministic safety guards.

A DRIFT (a proposed YAML change) must pass every guard in section 4.1 of the
design. Failing any guard downgrades the model to UNVERIFIED rather than letting
a fabricated or misread rate become a pre-filled diff.
"""

from __future__ import annotations

import re
from decimal import Decimal

from .models import Category, Extraction, Finding, RenderResult, WorkItem

# Units we refuse to treat as a per-use rate (would be a category error).
_DISALLOWED_UNIT_TOKENS = ('month', 'credit', 'subscription', 'token', 'plan', 'seat', 'year')

_NUMBER_RE = re.compile(r'\$?\s*(\d+(?:\.\d+)?)')
# A comma used as a decimal separator (e.g. "0,108") is ambiguous; reject it.
_COMMA_DECIMAL_RE = re.compile(r'\d,\d')


def parse_number(text: str) -> float | None:
    """Extract a single USD price magnitude from `text` using an en-US grammar.

    Prefers a $-prefixed number (so "Nova-3 $0.0077" reads 0.0077, not the 3),
    then "N cents", then a number adjacent to a per/unit word, then any number.
    Rejects comma-decimal locale forms (returns None) so "0,108" is never misread.
    """
    if _COMMA_DECIMAL_RE.search(text):
        return None
    dollars = re.search(r'\$\s*(\d+(?:\.\d+)?)', text)
    if dollars:
        return float(dollars.group(1))
    cents = re.search(r'(\d+(?:\.\d+)?)\s*cents?\b', text, re.IGNORECASE)
    if cents:
        return float(cents.group(1)) / 100
    adjacent = re.search(r'(\d+(?:\.\d+)?)\s*(?:per|/|usd|dollar)', text, re.IGNORECASE)
    if adjacent:
        return float(adjacent.group(1))
    m = _NUMBER_RE.search(text)
    return float(m.group(1)) if m else None


def to_our_unit(rate_value: float, rate_unit: str, field: str) -> float | None:
    """Convert a provider-quoted rate to our stored unit, or None if not convertible.

    per_ksec fields (input_audio_kseconds / output_audio_kseconds) are $/1,000 seconds;
    per_kchar fields (input_kchars) are $/1,000 characters. Monthly / credit / token /
    subscription quotes are rejected (None) so they can never become a DRIFT.
    """
    u = rate_unit.lower()
    if any(tok in u for tok in _DISALLOWED_UNIT_TOKENS):
        return None

    from .models import VOICE_FIELD_UNITS

    unit_class = VOICE_FIELD_UNITS.get(field)
    if unit_class == 'per_ksec':
        if 'hour' in u or 'hr' in u:
            return rate_value * 1000 / 3600
        if 'min' in u:
            return rate_value * 1000 / 60
        if 'sec' in u:
            return rate_value * 1000
        return None
    if unit_class == 'per_kchar':
        if 'million' in u or '1m' in u or 'mtok' in u:
            return rate_value / 1000
        if '1k' in u or '1,000' in u or '1000' in u or 'thousand' in u:
            return rate_value
        if 'char' in u:
            return rate_value * 1000
        return None
    return None


def _model_token(model_id: str) -> str:
    """A loose token to look for near the quote (last path segment, no version churn)."""
    return model_id.split('/')[-1].lower()


def _guards_pass(item: WorkItem, ex: Extraction, rendered_text: str) -> tuple[bool, str]:
    """Run the 4.1 guards. Returns (passed, reason-if-failed)."""
    if ex.currency and ex.currency.upper() not in ('USD', '$'):
        return False, f'non-USD currency {ex.currency!r}'
    if ex.evidence_quote not in rendered_text:
        return False, 'evidence_quote is not a substring of the rendered page'
    quoted_number = parse_number(ex.evidence_quote)
    if quoted_number is None or ex.rate_value is None:
        return False, 'could not parse a number from the evidence_quote'
    if abs(quoted_number - ex.rate_value) > 1e-9:
        return False, f'rate_value {ex.rate_value} does not match the quote number {quoted_number}'
    token = _model_token(item.model_id)
    haystack = f'{ex.matched_row_name} {ex.evidence_quote}'.lower()
    if token not in haystack and token.replace('-', ' ') not in haystack:
        return False, f'matched row {ex.matched_row_name!r} does not name the model {item.model_id!r}'
    if ex.rate_unit is None or to_our_unit(ex.rate_value, ex.rate_unit, item.field) is None:
        return False, f'rate_unit {ex.rate_unit!r} is not convertible to the expected unit class'
    return True, ''


def _within_tolerance(a: float, b: float) -> bool:
    return abs(a - b) <= max(1e-6, 1e-4 * abs(b))


def classify(item: WorkItem, render: RenderResult, extraction: Extraction | None) -> Finding:
    """Categorize one work item. Network/LLM results come in; a Finding goes out."""

    def finding(cat: Category, reason: str, proposed: Decimal | None = None, src: float | None = None) -> Finding:
        return Finding(
            item=item,
            category=cat,
            extraction=extraction,
            proposed_rate=proposed,
            observed_source_rate=src,
            observed_source_unit=(extraction.rate_unit if extraction else None),
            reason=reason,
        )

    # Page-level problems first.
    if not render.ok:
        if render.blocked or (render.http_status is not None and render.http_status != 200):
            return finding(
                Category.URL_STALE, f'page not usable (status={render.http_status}, blocked={render.blocked})'
            )
        return finding(Category.UNVERIFIED, 'page did not render')

    if extraction is None or not extraction.found:
        return finding(Category.GONE, 'model not found on a reachable page')

    if extraction.confidence != 'high':
        return finding(Category.UNVERIFIED, f'extraction confidence is {extraction.confidence}')

    passed, why = _guards_pass(item, extraction, render.text)
    if not passed:
        return finding(Category.UNVERIFIED, f'guard failed: {why}')

    assert extraction.rate_value is not None and extraction.rate_unit is not None
    our_value = to_our_unit(extraction.rate_value, extraction.rate_unit, item.field)
    assert our_value is not None  # guard 5 already ensured convertibility
    stored = float(item.current_rate)

    if _within_tolerance(our_value, stored):
        return finding(Category.MATCH, 'rate matches', src=extraction.rate_value)

    # A >10x swing is almost always a unit error, not a real price move.
    if stored > 0 and (our_value > 10 * stored or our_value < stored / 10):
        return finding(
            Category.UNVERIFIED,
            f'normalized rate {our_value:.6g} differs from stored {stored:.6g} by more than 10x (likely a unit error)',
            src=extraction.rate_value,
        )

    proposed = Decimal(format(our_value, '.6f')).normalize()
    return finding(
        Category.DRIFT,
        f'rate changed: stored {stored:.6g} -> observed {our_value:.6g}',
        proposed=proposed,
        src=extraction.rate_value,
    )
