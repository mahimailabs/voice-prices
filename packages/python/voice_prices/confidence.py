"""Deterministic freshness + confidence for a model's price.

No probability math and no continuous score: v1 exposes an honest
``verification_status``, a ``stale`` flag, and a coarse ``high``/``medium``/``low``
label, all derived from the model's ``provenance.last_verified`` (emitted at build
from the human-owned ``prices_checked``) plus the provider's
``staleness_threshold_days``. A Bayesian score was deliberately deferred until there
is observed bot-accuracy data to calibrate it against.

Computed at load time so ``stale`` and the label decay with wall-clock age as a
shipped snapshot ages between releases, rather than being frozen at build time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, cast

from .types import ModelInfo, Provider

__all__ = (
    'Freshness',
    'ConfidenceLevel',
    'VerificationStatus',
    'model_freshness',
    'freshness_from_provenance',
)

ConfidenceLevel = Literal['high', 'medium', 'low']
VerificationStatus = Literal['verified', 'stale', 'imported', 'seed']


@dataclass
class Freshness:
    """Consumer-facing freshness signal for a single model's price."""

    verification_status: VerificationStatus
    """`verified` (a human confirmed it and it is fresh), `stale` (confirmed but older than the
    provider's staleness threshold), `imported` (from another catalog, not yet human-confirmed),
    or `seed` (bootstrap data, never confirmed)."""
    confidence: ConfidenceLevel
    """Coarse `high` / `medium` / `low` label. Not a probability."""
    last_verified: date | None
    """Date a human last confirmed the price, or None if never confirmed."""
    age_days: int | None
    """Whole days since `last_verified`, or None if never confirmed."""
    stale: bool
    """True when a verified price is older than the provider's `staleness_threshold_days`."""


def _compute(
    source: str | None,
    last_verified: date | None,
    agents_unanimous: bool,
    staleness_threshold_days: int,
    today: date,
) -> Freshness:
    """The deterministic core, shared by the typed and dict-based entry points."""
    if last_verified is not None:
        age_days = (today - last_verified).days
        stale = age_days > staleness_threshold_days
        status: VerificationStatus = 'stale' if stale else 'verified'
    else:
        # Never human-confirmed: not "stale" (that implies it was once verified), just unverified.
        age_days = None
        stale = False
        status = 'imported' if source == 'imported' else 'seed'

    if status == 'verified':
        confidence: ConfidenceLevel = 'high'
    elif status == 'stale' or (status == 'imported' and agents_unanimous):
        confidence = 'medium'
    else:
        confidence = 'low'

    return Freshness(
        verification_status=status,
        confidence=confidence,
        last_verified=last_verified,
        age_days=age_days,
        stale=stale,
    )


def model_freshness(model: ModelInfo, provider: Provider, *, today: date | None = None) -> Freshness:
    """Derive the freshness signal for ``model`` under ``provider``.

    Args:
        model: The model whose price freshness to describe.
        provider: The owning provider (supplies ``staleness_threshold_days``).
        today: Reference date for the age calculation; defaults to ``date.today()``.
            Pass an explicit date for deterministic behavior (e.g. in tests).
    """
    today = today or date.today()
    provenance = model.provenance
    last_verified = provenance.last_verified if provenance is not None else None
    source = provenance.source if provenance is not None else None
    votes = provenance.agent_votes if provenance is not None else None
    agents_unanimous = votes is not None and votes.total > 0 and votes.approve == votes.total
    return _compute(source, last_verified, agents_unanimous, provider.staleness_threshold_days, today)


def freshness_from_provenance(
    provenance: dict[str, Any] | None, staleness_threshold_days: int, *, today: date | None = None
) -> Freshness:
    """Derive the freshness signal from a raw ``data.json`` provenance dict.

    Lets dict-based consumers (e.g. the docs-site builder) share the exact status/confidence rules
    without reconstructing ``ModelInfo``. Accepts ``last_verified`` as an ISO string or ``date``.
    """
    today = today or date.today()
    provenance = provenance or {}
    source = provenance.get('source')
    raw_last_verified = provenance.get('last_verified')
    if isinstance(raw_last_verified, date):
        last_verified = raw_last_verified
    elif isinstance(raw_last_verified, str):
        try:
            last_verified = date.fromisoformat(raw_last_verified)
        except ValueError:
            last_verified = None  # a malformed date is treated as never-verified, not a crash
    else:
        last_verified = None
    votes = provenance.get('agent_votes')
    if isinstance(votes, dict):
        votes_dict = cast('dict[str, Any]', votes)
        total = votes_dict.get('total')
        approve = votes_dict.get('approve')
        agents_unanimous = isinstance(total, int) and total > 0 and approve == total
    else:
        agents_unanimous = False
    return _compute(
        source if isinstance(source, str) else None,
        last_verified,
        agents_unanimous,
        staleness_threshold_days,
        today,
    )
