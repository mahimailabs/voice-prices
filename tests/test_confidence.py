from __future__ import annotations

from datetime import date
from decimal import Decimal

from voice_prices import Freshness, Usage, model_freshness, types
from voice_prices.confidence import freshness_from_provenance


def _provider(*, threshold: int = 60, provenance: types.Provenance | None = None) -> types.Provider:
    return types.Provider(
        id='testing',
        name='Testing',
        api_pattern=r'.*testing\.example\.com',
        staleness_threshold_days=threshold,
        models=[
            types.ModelInfo(
                id='foobar',
                match=types.ClauseEquals('foobar'),
                prices=types.ModelPrice(input_mtok=Decimal('1'), output_mtok=Decimal('2')),
                provenance=provenance,
            )
        ],
    )


def _freshness(provider: types.Provider, *, today: date) -> Freshness:
    return model_freshness(provider.models[0], provider, today=today)


def test_verified_fresh_is_high():
    provider = _provider(provenance=types.Provenance(last_verified=date(2026, 7, 1)))
    fresh = _freshness(provider, today=date(2026, 7, 21))
    assert fresh.verification_status == 'verified'
    assert fresh.confidence == 'high'
    assert fresh.age_days == 20
    assert fresh.stale is False


def test_verified_stale_is_medium():
    provider = _provider(threshold=30, provenance=types.Provenance(last_verified=date(2026, 1, 1)))
    fresh = _freshness(provider, today=date(2026, 7, 21))
    assert fresh.verification_status == 'stale'
    assert fresh.confidence == 'medium'
    assert fresh.stale is True


def test_no_provenance_is_seed_low():
    provider = _provider(provenance=None)
    fresh = _freshness(provider, today=date(2026, 7, 21))
    assert fresh.verification_status == 'seed'
    assert fresh.confidence == 'low'
    assert fresh.last_verified is None
    assert fresh.age_days is None
    assert fresh.stale is False


def test_imported_without_votes_is_low():
    provider = _provider(provenance=types.Provenance(source='imported'))
    fresh = _freshness(provider, today=date(2026, 7, 21))
    assert fresh.verification_status == 'imported'
    assert fresh.confidence == 'low'


def test_imported_with_unanimous_votes_is_medium():
    provenance = types.Provenance(source='imported', agent_votes=types.AgentVotes(approve=2, total=2))
    provider = _provider(provenance=provenance)
    fresh = _freshness(provider, today=date(2026, 7, 21))
    assert fresh.verification_status == 'imported'
    assert fresh.confidence == 'medium'


def test_imported_with_split_votes_is_low():
    provenance = types.Provenance(source='imported', agent_votes=types.AgentVotes(approve=1, total=2))
    provider = _provider(provenance=provenance)
    fresh = _freshness(provider, today=date(2026, 7, 21))
    assert fresh.confidence == 'low'


def test_staleness_boundary_is_not_stale_at_threshold():
    # age exactly == threshold is NOT stale; threshold + 1 is stale.
    verified = date(2026, 6, 21)
    provider = _provider(threshold=30, provenance=types.Provenance(last_verified=verified))
    at_threshold = _freshness(provider, today=date(2026, 7, 21))  # 30 days
    assert at_threshold.age_days == 30
    assert at_threshold.stale is False
    assert at_threshold.verification_status == 'verified'

    just_over = _freshness(provider, today=date(2026, 7, 22))  # 31 days
    assert just_over.age_days == 31
    assert just_over.stale is True
    assert just_over.verification_status == 'stale'


def test_seed_source_with_no_date_is_seed_low():
    provider = _provider(provenance=types.Provenance(source='seed'))
    fresh = _freshness(provider, today=date(2026, 7, 21))
    assert fresh.verification_status == 'seed'
    assert fresh.confidence == 'low'


def test_default_today_smoke():
    # Calling without an explicit today must still return a Freshness (uses date.today()).
    provider = _provider(provenance=types.Provenance(last_verified=date(2020, 1, 1)))
    fresh = model_freshness(provider.models[0], provider)
    assert isinstance(fresh, Freshness)
    # a 2020 verification is far past any sane threshold, so it is stale.
    assert fresh.stale is True

# ---- freshness_from_provenance (dict entry point, used by the docs site) -----


def test_freshness_from_provenance_matches_typed_path():
    # The dict entry point must produce the FULL Freshness the typed model_freshness does, across
    # every status kind (not just 'verified'), so the two callers can never silently diverge.
    today = date(2026, 7, 21)
    cases: list[tuple[int, dict[str, object], types.Provenance]] = [
        (60, {'last_verified': '2026-07-01'}, types.Provenance(last_verified=date(2026, 7, 1))),  # verified
        (30, {'last_verified': '2026-01-01'}, types.Provenance(last_verified=date(2026, 1, 1))),  # stale
        (
            60,
            {'source': 'imported', 'agent_votes': {'approve': 2, 'total': 2}},
            types.Provenance(source='imported', agent_votes=types.AgentVotes(approve=2, total=2)),
        ),  # imported + unanimous
        (60, {'source': 'seed'}, types.Provenance(source='seed')),  # seed
    ]
    for threshold, prov_dict, prov_typed in cases:
        dict_result = freshness_from_provenance(prov_dict, threshold, today=today)
        provider = _provider(threshold=threshold, provenance=prov_typed)
        typed_result = model_freshness(provider.models[0], provider, today=today)
        assert dict_result == typed_result, f'diverged for {prov_dict}'  # full dataclass equality


def test_freshness_from_provenance_imported_unanimous_is_medium():
    prov = {'source': 'imported', 'agent_votes': {'approve': 2, 'total': 2}}
    fresh = freshness_from_provenance(prov, 60, today=date(2026, 7, 21))
    assert fresh.verification_status == 'imported'
    assert fresh.confidence == 'medium'


def test_freshness_from_provenance_none_is_seed_low():
    fresh = freshness_from_provenance(None, 60, today=date(2026, 7, 21))
    assert fresh.verification_status == 'seed'
    assert fresh.confidence == 'low'


def test_freshness_from_provenance_stale_string_date():
    fresh = freshness_from_provenance({'last_verified': '2026-01-01'}, 30, today=date(2026, 7, 21))
    assert fresh.verification_status == 'stale'
    assert fresh.confidence == 'medium'


def test_freshness_from_provenance_malformed_date_is_treated_as_unverified():
    # a garbage last_verified string must not crash: treat it as never-verified (seed/low).
    fresh = freshness_from_provenance({'last_verified': 'not-a-date'}, 60, today=date(2026, 7, 21))
    assert fresh.last_verified is None
    assert fresh.verification_status == 'seed'
    assert fresh.confidence == 'low'


def test_freshness_from_provenance_accepts_date_object():
    # the docstring promises last_verified may be a date, not only an ISO string
    fresh = freshness_from_provenance({'last_verified': date(2026, 7, 1)}, 60, today=date(2026, 7, 21))
    assert fresh.verification_status == 'verified'
    assert fresh.age_days == 20


# ---- PriceCalculation.freshness() accessor -----------------------------------


def test_price_calculation_freshness_accessor():
    provider = _provider(provenance=types.Provenance(last_verified=date(2026, 7, 1)))
    calc = provider.models[0].calc_price(Usage(), provider)
    fresh = calc.freshness(today=date(2026, 7, 21))
    assert isinstance(fresh, Freshness)
    assert fresh.verification_status == 'verified'
    assert fresh.confidence == 'high'

def test_model_freshness_on_shipped_data():
    # Round-trip guard: the build emits provenance.last_verified into the shipped data.py, and it
    # must deserialize back through the package types and produce a valid Freshness.
    from voice_prices.data import providers

    checked: list[str] = []
    for provider in providers:
        for model in provider.models:
            if model.provenance is not None and model.provenance.last_verified is not None:
                fresh = model_freshness(model, provider, today=date(2026, 7, 21))
                assert fresh.last_verified == model.provenance.last_verified
                assert fresh.verification_status in ('verified', 'stale')
                assert fresh.confidence in ('high', 'medium', 'low')
                checked.append(model.id)
    assert checked, 'expected at least one shipped model to carry provenance.last_verified'
