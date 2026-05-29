"""Dataclasses and enums shared across the freshness modules."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

# The voice priced fields the checker covers, mapped to their unit class.
# 'per_kchar': $ per 1,000 characters. 'per_ksec': $ per 1,000 seconds of audio.
VOICE_FIELD_UNITS: dict[str, str] = {
    'input_kchars': 'per_kchar',
    'output_audio_kseconds': 'per_ksec',
    'input_audio_kseconds': 'per_ksec',
}


class Category(str, Enum):
    MATCH = 'match'  # extracted rate agrees with the catalog
    DRIFT = 'drift'  # extracted rate differs and passed every guard
    UNVERIFIED = 'unverified'  # transient: blocked, low-confidence, not found, or failed a guard
    URL_STALE = 'url_stale'  # non-200, cross-host redirect, or login wall: the source url needs updating
    GONE = 'gone'  # model not present on an otherwise reachable page


@dataclass(frozen=True)
class WorkItem:
    """One voice model due for a freshness check."""

    provider_id: str
    model_id: str
    url: str
    field: str  # one of VOICE_FIELD_UNITS
    current_rate: Decimal  # the stored value of `field` (our unit, e.g. $/1k seconds)

    @property
    def unit_class(self) -> str:
        return VOICE_FIELD_UNITS[self.field]


@dataclass(frozen=True)
class RenderResult:
    """What the browser layer returns for a single URL."""

    ok: bool  # page rendered and is usable for extraction
    http_status: int | None
    final_url: str | None  # url after redirects
    text: str  # rendered page text ('' if not ok)
    screenshot_path: str | None
    blocked: bool = False  # bot-wall / login-wall / non-200 detected


@dataclass(frozen=True)
class Extraction:
    """The LLM's reading of one model's rate from a rendered page."""

    found: bool
    rate_value: float | None
    rate_unit: str | None  # provider's own unit string, e.g. 'per minute', 'per 1M tokens'
    currency: str | None  # e.g. 'USD'
    confidence: str  # 'high' | 'medium' | 'low'
    evidence_quote: str  # text the model claims it read the rate from
    matched_row_name: str  # the model/row name the model says it matched


@dataclass(frozen=True)
class Finding:
    """The classification of one work item after fetch + diff."""

    item: WorkItem
    category: Category
    extraction: Extraction | None
    proposed_rate: Decimal | None  # set only for DRIFT (our unit)
    observed_source_rate: float | None  # the provider-unit rate we read (for the PR note)
    observed_source_unit: str | None
    reason: str  # human-readable explanation (why UNVERIFIED, what changed, etc.)
