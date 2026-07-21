from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from prices.build import SLIM_EXCLUDE, populate_provenance
from prices.prices_types import (
    AgentVotes,
    ClauseEquals,
    ModelInfo,
    ModelPrice,
    Provenance,
    Provider,
    SourceRate,
    providers_schema,
)


def _provider(models: list[ModelInfo]) -> Provider:
    return Provider(id='testing', name='Testing', api_pattern=r'.*testing\.example\.com', models=models)


def _model(**kwargs: object) -> ModelInfo:
    defaults: dict[str, object] = {
        'id': 'foobar',
        'match': ClauseEquals(equals='foobar'),
        'prices': ModelPrice(input_kchars=Decimal('0.03')),
    }
    defaults.update(kwargs)
    return ModelInfo(**defaults)  # type: ignore[arg-type]


def test_populate_mirrors_prices_checked_into_last_verified():
    provider = _provider([_model(prices_checked=date(2026, 5, 12))])
    populate_provenance(provider)
    assert provider.models[0].provenance is not None
    assert provider.models[0].provenance.last_verified == date(2026, 5, 12)


def test_populate_overwrites_existing_last_verified():
    # prices_checked is the single source of truth; a hand-set provenance.last_verified is overwritten.
    provider = _provider(
        [_model(prices_checked=date(2026, 5, 12), provenance=Provenance(last_verified=date(2000, 1, 1)))]
    )
    populate_provenance(provider)
    assert provider.models[0].provenance is not None
    assert provider.models[0].provenance.last_verified == date(2026, 5, 12)


def test_populate_preserves_other_provenance_fields():
    provider = _provider(
        [
            _model(
                prices_checked=date(2026, 5, 12), provenance=Provenance(source='imported', evidence='$0.03 / 1k chars')
            )
        ]
    )
    populate_provenance(provider)
    provenance = provider.models[0].provenance
    assert provenance is not None
    assert provenance.source == 'imported'
    assert provenance.evidence == '$0.03 / 1k chars'
    assert provenance.last_verified == date(2026, 5, 12)


def test_populate_leaves_unverified_model_without_provenance():
    provider = _provider([_model()])  # no prices_checked
    populate_provenance(provider)
    assert provider.models[0].provenance is None


def _dump(provider: Provider, *, slim: bool) -> dict[str, object]:
    exclude = SLIM_EXCLUDE if slim else None
    raw = providers_schema.dump_json([provider], by_alias=True, exclude_none=True, exclude=exclude)
    return json.loads(raw)[0]['models'][0]['provenance']


def test_full_dump_keeps_all_provenance_fields():
    provenance = Provenance(
        source='imported',
        last_verified=date(2026, 5, 12),
        agent_votes=AgentVotes(approve=2, total=2),
        evidence='$0.03 per 1,000 characters',
        source_rate=SourceRate(value=Decimal('30'), unit='per_kchar'),
    )
    provider = _provider([_model(provenance=provenance)])
    out = _dump(provider, slim=False)
    assert set(out) == {'source', 'last_verified', 'agent_votes', 'evidence', 'source_rate'}


def test_slim_dump_drops_heavy_provenance_fields():
    provenance = Provenance(
        source='imported',
        last_verified=date(2026, 5, 12),
        agent_votes=AgentVotes(approve=2, total=2),
        evidence='$0.03 per 1,000 characters',
        source_rate=SourceRate(value=Decimal('30'), unit='per_kchar'),
    )
    provider = _provider([_model(provenance=provenance)])
    out = _dump(provider, slim=True)
    # kept for client-side status derivation:
    assert out['source'] == 'imported'
    assert out['last_verified'] == '2026-05-12'
    # heavy audit fields dropped:
    assert 'evidence' not in out
    assert 'source_rate' not in out
    assert 'agent_votes' not in out
