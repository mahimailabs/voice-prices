"""Tests for the Telnyx Inference importer (prices.source_telnyx).

The load-bearing behaviour is the refusal: ten of the eighteen models Telnyx publishes report a
headline rate of 0.00 while pricing the same dimension in their top tier. Importing those at face
value would publish paid models as free, so the flatness check is pinned hard.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from prices.prices_types import ClauseEquals, ClauseOr, ConditionalPrice, ModelInfo, ModelPrice
from prices.source_telnyx import (
    PRICING_SOURCE_URL,
    InferenceRow,
    Tier,
    convert_row,
    flat_rate,
    plan_import,
    price_diff,
    render_report,
)
from prices.update import ProviderYaml


def _row(**overrides: Any) -> InferenceRow:
    base: dict[str, Any] = {
        'model': 'acme-1',
        'input_rate': '0.0025',
        'cached_input_rate': '0.00125',
        'output_rate': '0.01',
        'input_tiers': [{'min': 0, 'max': None, 'rate': '0.0025'}],
        'cached_input_tiers': [{'min': 0, 'max': None, 'rate': '0.00125'}],
        'output_tiers': [{'min': 0, 'max': None, 'rate': '0.01'}],
        'unit': 'per_1k_tokens',
        'currency': 'USD',
    }
    merged = {**base, **overrides}
    # Overriding a headline rate without also restating its tiers would make the row internally
    # inconsistent and be refused, which is not what those tests are exercising.
    for rate_key, tier_key in (
        ('input_rate', 'input_tiers'),
        ('output_rate', 'output_tiers'),
        ('cached_input_rate', 'cached_input_tiers'),
    ):
        if rate_key in overrides and tier_key not in overrides:
            merged[tier_key] = [{'min': 0, 'max': None, 'rate': overrides[rate_key]}]
    return InferenceRow.model_validate(merged)


# ---- flatness ----------------------------------------------------------------


def test_flat_rate_accepts_tiers_that_agree_with_the_headline():
    tiers = [Tier(min=0, max=100, rate=Decimal('0.5')), Tier(min=100, max=None, rate=Decimal('0.5'))]
    assert flat_rate(Decimal('0.5'), tiers) == Decimal('0.5')


def test_flat_rate_rejects_a_headline_that_disagrees_with_a_tier():
    # This is the real shape: free at the top level, priced above 1M units.
    tiers = [Tier(min=0, max=1_000_000, rate=Decimal('0')), Tier(min=1_000_000, max=None, rate=Decimal('0.001'))]
    assert flat_rate(Decimal('0'), tiers) is None


def test_flat_rate_with_no_tiers_is_the_headline():
    assert flat_rate(Decimal('0.002'), []) == Decimal('0.002')


# ---- conversion --------------------------------------------------------------


def test_per_1k_tokens_converts_to_per_million():
    model, reason = convert_row(_row())
    assert reason is None
    assert model is not None
    prices = model.prices
    assert isinstance(prices, ModelPrice)
    assert prices.input_mtok == Decimal('2.5')
    assert prices.output_mtok == Decimal('10')
    assert prices.cache_read_mtok == Decimal('1.25')


def test_imported_models_land_unverified():
    # A human sets prices_checked. The importer never does, so a re-run cannot fake verification.
    model, _ = convert_row(_row())
    assert model is not None
    assert model.prices_checked is None
    assert model.provenance is not None and model.provenance.source == 'imported'
    assert str(model.pricing_source_url) == PRICING_SOURCE_URL


def test_model_id_is_used_verbatim_as_the_match():
    model, _ = convert_row(_row(model='anthropic-claude-haiku-4.5'))
    assert model is not None
    assert model.id == 'anthropic-claude-haiku-4.5'
    assert model.match.equals == 'anthropic-claude-haiku-4.5'  # type: ignore[union-attr]


def test_a_flat_zero_cached_rate_is_left_unset_rather_than_published_as_free():
    # Zero reads the same whether cache reads are free or the model has no cache, and the API does
    # not distinguish them, so the field is left unset rather than asserting one reading.
    model, reason = convert_row(
        _row(cached_input_rate='0.00', cached_input_tiers=[{'min': 0, 'max': None, 'rate': '0.00'}])
    )
    assert reason is None and model is not None
    assert isinstance(model.prices, ModelPrice)
    assert model.prices.cache_read_mtok is None


def test_a_zero_cached_headline_hiding_a_tiered_rate_is_refused():
    # Decimal('0') is falsy, so a truthiness check here would skip tier validation entirely and let
    # a tiered cached price through unchecked while input and output were being verified.
    model, reason = convert_row(
        _row(
            cached_input_rate='0.00',
            cached_input_tiers=[
                {'min': 0, 'max': 1_000_000, 'rate': '0.00'},
                {'min': 1_000_000, 'max': None, 'rate': '0.0004'},
            ],
        )
    )
    assert model is None
    assert reason is not None and reason.startswith('cached input headline rate')
    assert '0.0004' in reason


def test_input_headline_disagreeing_with_tiers_is_refused():
    model, reason = convert_row(
        _row(
            input_rate='0.00',
            input_tiers=[
                {'min': 0, 'max': 1_000_000, 'rate': '0.00'},
                {'min': 1_000_000, 'max': None, 'rate': '0.001'},
            ],
        )
    )
    assert model is None
    assert reason is not None
    assert 'input headline rate 0.00 disagrees' in reason
    assert '0.001' in reason


def test_output_headline_disagreeing_with_tiers_is_refused():
    model, reason = convert_row(
        _row(
            output_rate='0.00',
            output_tiers=[
                {'min': 0, 'max': 1_000_000, 'rate': '0.00'},
                {'min': 1_000_000, 'max': None, 'rate': '0.0032'},
            ],
        )
    )
    assert model is None
    assert reason is not None and reason.startswith('output headline rate')


def test_an_unknown_unit_is_refused_rather_than_assumed():
    model, reason = convert_row(_row(unit='per_minute'))
    assert model is None
    assert reason is not None and 'per_minute' in reason


def test_a_non_usd_currency_is_refused():
    model, reason = convert_row(_row(currency='EUR'))
    assert model is None
    assert reason is not None and 'EUR' in reason


def test_unknown_api_fields_do_not_break_the_import():
    # Telnyx adding a field must not stop the importer working.
    model, reason = convert_row(_row(brand_new_field='surprise'))
    assert reason is None and model is not None


# ---- planning ----------------------------------------------------------------

TELNYX_YAML = """id: telnyx
name: Telnyx
api_pattern: 'https://api\\.telnyx\\.com'
models:
  - id: natural
    match:
      starts_with: telnyx.natural.
    prices:
      input_kchars: 0.003
  - id: telnyx-stt
    match:
      equals: telnyx
    prices:
      input_audio_kseconds: 0.25
"""


def _provider_yaml(tmp_path: Path) -> ProviderYaml:
    path = tmp_path / 'telnyx.yml'
    path.write_text(TELNYX_YAML)
    return ProviderYaml(path)


def _with_models(tmp_path: Path, *models: ModelInfo) -> ProviderYaml:
    """A provider file that already contains `models`.

    add_model stages into _extra_prices and only reaches `provider.models` after save(), so the
    file has to be written and re-opened. That is the real flow too: import, save, re-run.
    """
    provider_yaml = _provider_yaml(tmp_path)
    for model in models:
        provider_yaml.add_model(model)
    provider_yaml.save()
    return ProviderYaml(provider_yaml.path)


def test_plan_adds_new_models_and_reports_refusals(tmp_path: Path):
    rows = [
        _row(model='gpt-4o'),
        _row(model='glm-5', input_rate='0.00', input_tiers=[{'min': 0, 'max': None, 'rate': '0.002'}]),
    ]
    plan = plan_import(rows, _provider_yaml(tmp_path))
    assert [m.id for m in plan.to_add] == ['gpt-4o']
    assert [model_id for model_id, _ in plan.refused] == ['glm-5']


def test_plan_never_clobbers_an_existing_hand_verified_model(tmp_path: Path):
    # telnyx-stt is hand-verified against the pricing page. An import must not touch it. Its id does
    # not match its own clause (`equals: telnyx`), so find_model alone would miss it and re-add a
    # duplicate id; the by-id lookup is what catches this.
    plan = plan_import([_row(model='telnyx-stt')], _provider_yaml(tmp_path))
    assert plan.to_add == []
    assert plan.drifted, 'an STT model priced as an LLM should read as drift, not as unchanged'


def test_plan_skips_an_id_matched_by_an_existing_starts_with_clause(tmp_path: Path):
    # `natural` matches `starts_with: telnyx.natural.`, so importing that id would make the file
    # schema-invalid on the next build (two models matching one reference).
    plan = plan_import([_row(model='telnyx.natural.astra')], _provider_yaml(tmp_path))
    assert plan.to_add == []


def test_an_unchanged_price_is_reported_as_unchanged_not_re_added(tmp_path: Path):
    imported = plan_import([_row(model='gpt-4o')], _provider_yaml(tmp_path)).to_add[0]
    again = plan_import([_row(model='gpt-4o')], _with_models(tmp_path, imported))
    assert again.to_add == []
    assert again.unchanged == ['gpt-4o']
    assert again.drifted == []


def test_a_changed_published_rate_is_reported_as_drift_and_never_written(tmp_path: Path):
    # The whole point of reading a live API: a reprice has to surface. It is reported, not applied,
    # because a human confirms a price change.
    imported = plan_import([_row(model='gpt-4o')], _provider_yaml(tmp_path)).to_add[0]
    plan = plan_import([_row(model='gpt-4o', input_rate='0.005')], _with_models(tmp_path, imported))
    assert plan.to_add == []
    assert plan.unchanged == []
    assert [model_id for model_id, _ in plan.drifted] == ['gpt-4o']
    assert 'input_mtok 2.5 -> 5' in plan.drifted[0][1]


def test_drift_is_detected_through_a_collapsed_match_clause(tmp_path: Path):
    # `collapse-models` merges identically priced models into one entry with an `or_` clause, so
    # gpt-5.1 resolves to the merged gpt-5 row. A later divergence must still be caught.
    merged = plan_import([_row(model='gpt-5')], _provider_yaml(tmp_path)).to_add[0]
    merged.match = ClauseOr.model_validate({'or': [{'equals': 'gpt-5'}, {'equals': 'gpt-5.1'}]})

    plan = plan_import([_row(model='gpt-5.1', input_rate='0.009')], _with_models(tmp_path, merged))
    assert plan.to_add == []
    assert [model_id for model_id, _ in plan.drifted] == ['gpt-5.1']


def test_price_diff_refuses_to_compare_a_tiered_catalog_entry(tmp_path: Path):
    incoming = plan_import([_row(model='gpt-4o')], _provider_yaml(tmp_path)).to_add[0]
    existing = ModelInfo(
        id='gpt-4o',
        match=ClauseEquals(equals='gpt-4o'),
        prices=[ConditionalPrice(prices=ModelPrice(input_mtok=Decimal('1')))],
    )
    assert 'cannot compare' in price_diff(existing, incoming)


def test_report_names_the_question_to_ask_telnyx(tmp_path: Path):
    rows = [_row(model='glm-5', input_rate='0.00', input_tiers=[{'min': 0, 'max': None, 'rate': '0.002'}])]
    report = render_report(plan_import(rows, _provider_yaml(tmp_path)), len(rows))
    assert 'REFUSED' in report
    assert 'not free' in report
    assert 'tier bounds measure' in report


def test_report_says_drift_is_not_written(tmp_path: Path):
    imported = plan_import([_row(model='gpt-4o')], _provider_yaml(tmp_path)).to_add[0]
    rows = [_row(model='gpt-4o', input_rate='0.005')]
    report = render_report(plan_import(rows, _with_models(tmp_path, imported)), len(rows))
    assert 'DRIFT' in report
    assert 'NOT written' in report
    assert 'set prices_checked yourself' in report


# ---- the committed catalog ---------------------------------------------------


def test_imported_llm_models_coexist_with_the_hand_verified_voice_models():
    """The real telnyx.yml carries both, and the LLM ids must not collide with the TTS clauses."""
    from prices.utils import package_dir

    provider = ProviderYaml(Path(package_dir) / 'providers' / 'telnyx.yml').provider
    by_id = {model.id: model for model in provider.models}

    # Hand-verified voice models still carry their own source and verified date.
    assert by_id['telnyx-stt'].prices_checked is not None
    assert 'telnyx.com/pricing/speech-to-text' in str(by_id['telnyx-stt'].pricing_source_url)

    # Imported LLM models are present, unverified, and sourced from the API.
    imported = [m for m in provider.models if m.provenance is not None and m.provenance.source == 'imported']
    assert imported
    for model in imported:
        assert model.prices_checked is None
        assert str(model.pricing_source_url) == PRICING_SOURCE_URL

    # `collapse-models` merges identically priced entries, so the eight importable models Telnyx
    # publishes are carried by fewer rows (gpt-5.1 shares gpt-5's row through an `or_` clause). The
    # invariant that matters is not the row count: it is that every published id still routes.
    for published in (
        'anthropic-claude-haiku-4.5',
        'gpt-4.1',
        'gpt-4o',
        'gpt-5',
        'gpt-5.1',
        'gpt-5.2',
        'gpt-5.4',
        'openai-gpt-5.4-mini',
    ):
        assert provider.find_model(published) is not None, f'{published} no longer resolves'

    # No model's id is swallowed by a DIFFERENT model's clause, which is what would make the file
    # ambiguous. Resolving to None is fine and expected: `natural` is keyed by id but routed by
    # `starts_with: telnyx.natural.`, so its own bare id deliberately does not match its own clause.
    for model in provider.models:
        resolved = provider.find_model(model.id)
        assert resolved is None or resolved is model, f'{model.id} resolves to another model'
