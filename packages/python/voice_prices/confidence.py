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
from typing import Literal

from .types import ModelInfo, Provider

__all__ = ('Freshness', 'ConfidenceLevel', 'VerificationStatus', 'model_freshness')

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
    agent_votes = provenance.agent_votes if provenance is not None else None

    if last_verified is not None:
        age_days = (today - last_verified).days
        stale = age_days > provider.staleness_threshold_days
        status: VerificationStatus = 'stale' if stale else 'verified'
    else:
        # Never human-confirmed: not "stale" (that implies it was once verified), just unverified.
        age_days = None
        stale = False
        status = source or 'seed'

    agents_unanimous = agent_votes is not None and agent_votes.total > 0 and agent_votes.approve == agent_votes.total

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
