"""Tests for the Cerebras models-endpoint reader (prices.source_cerebras).

The load-bearing behaviour is the retirement check. Cerebras removed three of the four models this
catalog listed over nine months and nothing noticed, because LLM rates have no automated freshness
job. A model marked `api_backed` that stops being published must now be reported.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from prices.prices_types import ClauseEquals, ModelInfo, ModelPrice, Provenance
from prices.source_cerebras import (
    MODELS_API,
    CerebrasModel,
    convert_model,
    plan_import,
    price_diff,
    render_report,
)
from prices.update import ProviderYaml


def _row(**overrides: Any) -> CerebrasModel:
    base: dict[str, Any] = {
        'id': 'gpt-oss-120b',
        'name': 'GPT OSS 120B',
        'pricing': {'prompt': '0.00000035', 'completion': '0.00000075'},
        'limits': {'max_context_length': 131072},
        'deprecated': False,
        'preview': False,
    }
    return CerebrasModel.model_validate({**base, **overrides})


# ---- conversion --------------------------------------------------------------


def test_per_token_rates_convert_to_per_million():
    model, reason = convert_model(_row())
    assert reason is None and model is not None
    prices = model.prices
    assert isinstance(prices, ModelPrice)
    assert prices.input_mtok == Decimal('0.35')
    assert prices.output_mtok == Decimal('0.75')


def test_context_window_comes_from_limits():
    model, _ = convert_model(_row(limits={'max_context_length': 65536}))
    assert model is not None and model.context_window == 65536


def test_a_model_with_no_published_pricing_is_refused():
    # Absent pricing must not become a zero rate, which would read as free.
    model, reason = convert_model(_row(pricing=None))
    assert model is None
    assert reason is not None and 'no prompt/completion pricing' in reason


def test_a_zero_rate_is_refused_rather_than_published_as_free():
    model, reason = convert_model(_row(pricing={'prompt': '0', 'completion': '0.00000075'}))
    assert model is None
    assert reason is not None and 'non-positive' in reason


def test_converted_models_land_unverified_and_api_backed():
    model, _ = convert_model(_row())
    assert model is not None
    assert model.prices_checked is None  # a human sets the verified date
    assert model.provenance is not None
    assert model.provenance.source == 'imported'
    assert model.provenance.api_backed is True
    assert str(model.pricing_source_url) == MODELS_API


def test_the_match_clause_covers_the_prefixed_forms_the_catalog_already_uses():
    model, _ = convert_model(_row(id='gemma-4-31b'))
    assert model is not None
    assert model.match.is_match('gemma-4-31b')
    assert model.match.is_match('cerebras/gemma-4-31b')
    assert model.match.is_match('cerebras:gemma-4-31b')
    assert not model.match.is_match('gemma-4-31b-other')


def test_unknown_api_fields_do_not_break_the_read():
    model, reason = convert_model(_row(brand_new_field='surprise'))
    assert reason is None and model is not None


# ---- planning ----------------------------------------------------------------

CEREBRAS_YAML = """id: cerebras
name: Cerebras
api_pattern: 'https://api\\.cerebras\\.ai'
models:
  - id: gpt-oss-120b
    match:
      equals: gpt-oss-120b
    prices:
      input_mtok: 0.35
      output_mtok: 0.75
    provenance:
      api_backed: true
  - id: hand-read-model
    match:
      equals: hand-read-model
    prices:
      input_mtok: 5.0
      output_mtok: 6.0
  - id: retired-model
    match:
      equals: retired-model
    prices:
      input_mtok: 1.0
      output_mtok: 2.0
    provenance:
      api_backed: true
"""


def _provider_yaml(tmp_path: Path) -> ProviderYaml:
    path = tmp_path / 'cerebras.yml'
    path.write_text(CEREBRAS_YAML)
    return ProviderYaml(path)


def test_a_matching_rate_reports_unchanged(tmp_path: Path):
    plan = plan_import([_row()], _provider_yaml(tmp_path))
    assert plan.unchanged == ['gpt-oss-120b']
    assert plan.drifted == []
    assert plan.to_add == []


def test_a_changed_rate_reports_drift_and_is_never_written(tmp_path: Path):
    plan = plan_import([_row(pricing={'prompt': '0.0000007', 'completion': '0.00000075'})], _provider_yaml(tmp_path))
    assert plan.to_add == []
    assert [model_id for model_id, _ in plan.drifted] == ['gpt-oss-120b']
    assert 'input_mtok 0.35 -> 0.70' in plan.drifted[0][1]


def test_a_newly_published_model_is_offered_for_adding(tmp_path: Path):
    plan = plan_import([_row(), _row(id='gemma-4-31b')], _provider_yaml(tmp_path))
    assert [m.id for m in plan.to_add] == ['gemma-4-31b']


def test_an_api_backed_model_that_stops_being_published_is_reported_retired(tmp_path: Path):
    # The whole reason this module exists: three Cerebras models disappeared over nine months and
    # the catalog kept publishing them.
    plan = plan_import([_row()], _provider_yaml(tmp_path))
    assert plan.retired == ['retired-model']


def test_a_hand_read_model_is_not_reported_retired(tmp_path: Path):
    # Only models this catalog claims to re-read automatically can be checked this way. A rate read
    # off a pricing page is absent from the endpoint by design, not by retirement.
    plan = plan_import([_row()], _provider_yaml(tmp_path))
    assert 'hand-read-model' not in plan.retired


def test_a_model_already_marked_removed_is_not_reported_retired(tmp_path: Path):
    path = tmp_path / 'cerebras.yml'
    path.write_text(
        CEREBRAS_YAML.replace(
            '  - id: retired-model\n    match:',
            '  - id: retired-model\n    removed: true\n    match:',
        )
    )
    plan = plan_import([_row()], ProviderYaml(path))
    assert plan.retired == []  # already dealt with, so not nagged about every run


def test_price_diff_refuses_to_compare_a_non_flat_catalog_entry():
    from prices.prices_types import ConditionalPrice

    incoming = ModelInfo(
        id='m', match=ClauseEquals(equals='m'), prices=ModelPrice(input_mtok=Decimal('1')), provenance=Provenance()
    )
    existing = ModelInfo(
        id='m', match=ClauseEquals(equals='m'), prices=[ConditionalPrice(prices=ModelPrice(input_mtok=Decimal('2')))]
    )
    assert 'cannot compare' in price_diff(existing, incoming)


# ---- report ------------------------------------------------------------------


def test_report_tells_a_maintainer_what_to_do_about_a_retirement(tmp_path: Path):
    report = render_report(plan_import([_row()], _provider_yaml(tmp_path)), 1)
    assert 'RETIRED' in report
    assert 'no longer exists upstream' in report
    assert 'removed: true' in report


def test_report_says_drift_is_not_written(tmp_path: Path):
    rows = [_row(pricing={'prompt': '0.0000007', 'completion': '0.00000075'})]
    report = render_report(plan_import(rows, _provider_yaml(tmp_path)), len(rows))
    assert 'DRIFT' in report
    assert 'NOT written' in report


# ---- the committed catalog ---------------------------------------------------


def test_every_api_backed_cerebras_model_is_still_published_upstream():
    """Guards the exact failure that motivated this module.

    Offline: it checks the committed YAML is internally consistent, i.e. every model claiming
    api_backed carries the endpoint as its source. `make cerebras-get` does the live check.
    """
    from prices.utils import package_dir

    provider = ProviderYaml(Path(package_dir) / 'providers' / 'cerebras.yml').provider
    backed = [m for m in provider.models if m.provenance is not None and m.provenance.api_backed]
    assert len(backed) == 3
    for model in backed:
        assert str(model.pricing_source_url) == MODELS_API, model.id
        assert model.prices_checked is not None, model.id
        assert not model.removed, model.id
