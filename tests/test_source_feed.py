"""Tests for the provider pricing-feed reader (prices.source_feed).

The feed is a format other companies are being asked to implement, so the conversion grid and the
fail-closed behaviour are pinned hard: a silently mis-converted unit would publish a wrong price
under a provider's own name.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from prices.source_feed import (
    DATA_JSON,
    RATE_FIELDS,
    Feed,
    FeedModel,
    base_rates,
    compare,
    convert_rates,
    feed_check,
    load_feed,
    render_rate_grid,
    render_report,
)


def _rate(meter: str, unit: str, amount: str, **extra: Any) -> dict[str, Any]:
    return {'meter': meter, 'unit': unit, 'amount': amount, **extra}


# ---- conversion --------------------------------------------------------------


def test_every_meter_unit_pair_converts_to_its_documented_field():
    for (meter, unit), mapping in RATE_FIELDS.items():
        model = FeedModel.model_validate({'id': 'm', 'rates': [_rate(meter, unit, '1')]})
        fields, problems = convert_rates(model)
        assert problems == []
        assert list(fields) == [mapping.field], f'{meter}/{unit} landed in the wrong field'


def test_per_minute_audio_converts_exactly():
    # 0.015/min is Telnyx's published STT rate and must land on 0.25 exactly, not 0.2499999.
    model = FeedModel.model_validate({'id': 'm', 'rates': [_rate('audio_input', 'minute', '0.015')]})
    fields, _ = convert_rates(model)
    assert fields['input_audio_kseconds'] == Decimal('0.25')


def test_per_character_and_per_token_scale_correctly():
    model = FeedModel.model_validate(
        {
            'id': 'm',
            'rates': [_rate('text_input', 'character', '0.000003'), _rate('text_output', 'token', '0.000010')],
        }
    )
    fields, _ = convert_rates(model)
    assert fields['input_kchars'] == Decimal('0.003')  # x 1,000
    assert fields['output_mtok'] == Decimal('10')  # x 1,000,000


def test_unknown_meter_unit_pair_is_refused_not_guessed():
    # A per-minute text rate is meaningless. Guessing at it would publish a wrong number.
    model = FeedModel.model_validate({'id': 'm', 'rates': [_rate('text_input', 'minute', '5')]})
    fields, problems = convert_rates(model)
    assert fields == {}
    assert problems and 'no mapping' in problems[0]


def test_two_rates_competing_for_one_field_is_refused():
    model = FeedModel.model_validate(
        {'id': 'm', 'rates': [_rate('audio_input', 'second', '0.001'), _rate('audio_input', 'minute', '0.06')]}
    )
    fields, problems = convert_rates(model)
    assert problems and 'two base rates' in problems[0]
    assert len(fields) == 1  # the first one wins, the collision is reported rather than merged


def test_negative_amount_is_refused():
    model = FeedModel.model_validate({'id': 'm', 'rates': [_rate('text_input', 'character', '-0.1')]})
    fields, problems = convert_rates(model)
    assert fields == {}
    assert problems and 'negative' in problems[0]


def test_base_rates_excludes_plan_volume_and_voice_class_rates():
    # Only the unqualified rate is comparable to a catalog headline price.
    model = FeedModel.model_validate(
        {
            'id': 'm',
            'rates': [
                _rate('text_input', 'character', '0.000030'),
                _rate('text_input', 'character', '0.000024', plan='enterprise'),
                _rate('text_input', 'character', '0.000020', from_quantity=1_000_000_000),
                _rate('text_input', 'character', '0.000045', voice_class='cloned'),
            ],
        }
    )
    assert len(model.rates) == 4
    assert len(base_rates(model)) == 1
    fields, problems = convert_rates(model)
    assert problems == []
    assert fields == {'input_kchars': Decimal('0.03')}


# ---- validation --------------------------------------------------------------


def test_a_typo_in_a_rate_key_is_rejected():
    # Rates are the money, so extra keys are forbidden and a misspelling surfaces immediately.
    with pytest.raises(ValidationError):
        FeedModel.model_validate({'id': 'm', 'rates': [{'meter': 'text_input', 'unit': 'character', 'ammount': '1'}]})


def test_provider_metadata_on_a_model_is_allowed_through():
    # Extra keys at the model level are fine: a provider may carry their own fields.
    model = FeedModel.model_validate({'id': 'm', 'region': 'us-east', 'rates': [_rate('text_input', 'token', '1')]})
    assert model.id == 'm'


def test_an_unknown_meter_fails_validation():
    with pytest.raises(ValidationError):
        FeedModel.model_validate({'id': 'm', 'rates': [_rate('vibes', 'character', '1')]})


def test_load_feed_warns_when_an_amount_is_a_json_number(tmp_path: Path):
    path = tmp_path / 'pricing.json'
    path.write_text(
        json.dumps(
            {
                'provider': 'acme',
                'models': [{'id': 'a', 'rates': [{'meter': 'text_input', 'unit': 'character', 'amount': 0.000003}]}],
            }
        )
    )
    feed, warnings = load_feed(str(path))
    assert feed.provider == 'acme'
    assert warnings and 'strings' in warnings[0]


def test_load_feed_is_silent_when_amounts_are_strings(tmp_path: Path):
    path = tmp_path / 'pricing.json'
    path.write_text(
        json.dumps(
            {'provider': 'acme', 'models': [{'id': 'a', 'rates': [_rate('text_input', 'character', '0.000003')]}]}
        )
    )
    _, warnings = load_feed(str(path))
    assert warnings == []


# ---- comparison --------------------------------------------------------------

CATALOG: list[dict[str, Any]] = [
    {
        'id': 'acme',
        'name': 'Acme',
        'models': [
            {'id': 'acme-tts-1', 'prices': {'input_kchars': 0.03}},
            {'id': 'acme-tts-2', 'prices': {'input_kchars': 0.05}},
        ],
    }
]


def _feed(*models: dict[str, Any]) -> Feed:
    return Feed.model_validate({'provider': 'acme', 'models': list(models)})


def test_matching_rate_reports_match():
    feed = _feed({'id': 'acme-tts-1', 'rates': [_rate('text_input', 'character', '0.000030')]})
    kinds = {f.kind for f in compare(feed, CATALOG)}
    assert 'MATCH' in kinds
    assert 'DRIFT' not in kinds


def test_changed_rate_reports_drift_with_the_delta():
    feed = _feed({'id': 'acme-tts-1', 'rates': [_rate('text_input', 'character', '0.000045')]})
    drift = [f for f in compare(feed, CATALOG) if f.kind == 'DRIFT']
    assert len(drift) == 1
    assert '+50.0%' in drift[0].detail


def test_model_absent_from_the_catalog_reports_new():
    feed = _feed({'id': 'acme-tts-9', 'rates': [_rate('text_input', 'character', '0.00001')]})
    new = [f for f in compare(feed, CATALOG) if f.kind == 'NEW']
    assert [f.model_id for f in new] == ['acme-tts-9']


def test_model_absent_from_the_feed_reports_missing_in_the_same_modality():
    feed = _feed({'id': 'acme-tts-1', 'rates': [_rate('text_input', 'character', '0.000030')]})
    missing = [f for f in compare(feed, CATALOG) if f.kind == 'MISSING']
    assert [f.model_id for f in missing] == ['acme-tts-2']


def test_missing_is_scoped_to_modalities_the_feed_actually_covers():
    # A TTS-only feed must not report every LLM model the provider has as missing.
    catalog: list[dict[str, Any]] = [
        {
            'id': 'acme',
            'models': [
                {'id': 'acme-tts-1', 'prices': {'input_kchars': 0.03}},
                {'id': 'acme-llm-1', 'prices': {'input_mtok': 1.0, 'output_mtok': 2.0}},
            ],
        }
    ]
    feed = _feed({'id': 'acme-tts-1', 'rates': [_rate('text_input', 'character', '0.000030')]})
    assert [f for f in compare(feed, catalog) if f.kind == 'MISSING'] == []


def test_rounding_difference_is_not_reported_as_drift():
    # A per-minute rate cannot round-trip exactly through $/1k seconds. 0.0043/min lands on
    # 0.07166666..., and a catalog storing 0.071667 is the same price, not a drift.
    catalog: list[dict[str, Any]] = [
        {'id': 'acme', 'models': [{'id': 'a', 'prices': {'input_audio_kseconds': 0.071667}}]}
    ]
    feed = _feed({'id': 'a', 'rates': [_rate('audio_input', 'minute', '0.0043')]})
    assert [f for f in compare(feed, catalog) if f.kind == 'DRIFT'] == []


def test_unknown_provider_reports_everything_as_new_rather_than_crashing():
    feed = Feed.model_validate(
        {'provider': 'nobody', 'models': [{'id': 'x', 'rates': [_rate('text_input', 'character', '0.00001')]}]}
    )
    assert [f.kind for f in compare(feed, CATALOG)] == ['NEW']


# ---- round trip against real catalog data ------------------------------------


def test_a_feed_built_from_the_real_catalog_compares_clean():
    """The format has to be able to express prices this project already publishes.

    Takes Telnyx's committed rates, inverts them back into feed units, and asserts the feed round
    trips to zero drift. If the conversion grid is wrong in either direction, this fails.
    """
    data: list[dict[str, Any]] = json.loads(DATA_JSON.read_text())
    telnyx = next(p for p in data if p['id'] == 'telnyx')

    models: list[dict[str, Any]] = []
    for model in telnyx['models']:
        prices: dict[str, Any] = model['prices']
        if 'input_kchars' in prices:
            amount = Decimal(str(prices['input_kchars'])) / 1000  # $/1k chars -> $/char
            models.append({'id': model['id'], 'rates': [_rate('text_input', 'character', str(amount))]})
        elif 'input_audio_kseconds' in prices:
            amount = Decimal(str(prices['input_audio_kseconds'])) * 60 / 1000  # $/1k sec -> $/min
            models.append({'id': model['id'], 'rates': [_rate('audio_input', 'minute', str(amount))]})

    assert len(models) == 5  # Telnyx ships 4 TTS models and 1 STT model
    findings = compare(Feed.model_validate({'provider': 'telnyx', 'models': models}), data)
    assert [f for f in findings if f.kind != 'MATCH'] == []
    assert len(findings) == 5


# ---- report and CLI ----------------------------------------------------------


def test_render_rate_grid_documents_every_supported_pair():
    grid = render_rate_grid()
    for (meter, unit), mapping in RATE_FIELDS.items():
        assert f'`{meter}`' in grid
        assert f'`{mapping.field}`' in grid
        assert f'`{unit}`' in grid
    assert grid.count('\n') == len(RATE_FIELDS) + 1  # header + separator + one row each


def test_report_names_the_human_step_when_there_is_drift():
    feed = _feed({'id': 'acme-tts-1', 'rates': [_rate('text_input', 'character', '0.000045')]})
    report = render_report(feed, compare(feed, CATALOG), [])
    assert 'DRIFT' in report
    assert 'set prices_checked yourself' in report


def test_report_says_so_when_nothing_drifted():
    feed = _feed({'id': 'acme-tts-1', 'rates': [_rate('text_input', 'character', '0.000030')]})
    report = render_report(feed, compare(feed, CATALOG), [])
    assert 'no drift' in report


def test_feed_check_without_a_feed_env_var_explains_itself(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.delenv('FEED', raising=False)
    assert feed_check() == 1
    assert 'FEED' in capsys.readouterr().out


def test_feed_check_exits_non_zero_on_drift(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # Telnyx's real STT rate is $0.015/min; publish a different one and the check must fail.
    path = tmp_path / 'pricing.json'
    path.write_text(
        json.dumps(
            {'provider': 'telnyx', 'models': [{'id': 'telnyx-stt', 'rates': [_rate('audio_input', 'minute', '0.030')]}]}
        )
    )
    monkeypatch.setenv('FEED', str(path))
    assert feed_check() == 1


def test_feed_check_exits_zero_when_the_feed_agrees(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    path = tmp_path / 'pricing.json'
    path.write_text(
        json.dumps(
            {'provider': 'telnyx', 'models': [{'id': 'telnyx-stt', 'rates': [_rate('audio_input', 'minute', '0.015')]}]}
        )
    )
    monkeypatch.setenv('FEED', str(path))
    assert feed_check() == 0
