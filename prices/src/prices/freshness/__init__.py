"""Voice pricing freshness check.

A build-side tool (not shipped in the published wheel) that re-verifies the
catalog's voice (TTS/STT) rates against each model's published pricing page and
proposes drift as a single rolling PR. See
docs/superpowers/specs/2026-05-29-pricing-freshness-check-design.md.
"""

from __future__ import annotations
