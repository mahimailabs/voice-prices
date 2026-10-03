from decimal import Decimal

import pytest

from voice_prices import Usage, calc_price


@pytest.mark.parametrize(
    'model,usage,expected',
    [
        ('60db-fast-v01', Usage(characters=1_000), Decimal('0.02')),
        ('60db-quality-v01', Usage(characters=1_000), Decimal('0.02')),
        ('60db-stt-v01', Usage(audio_input_seconds=Decimal('60')), Decimal('0.0005')),
        ('60db-tiny', Usage(input_tokens=750, output_tokens=250), Decimal('0.02')),
        ('60db-decision-model-v1', Usage(input_tokens=1_000_000), Decimal('0.01')),
        ('60db-judge-model-v1', Usage(input_tokens=1_000_000), Decimal('0.01')),
    ],
)
def test_60db_published_rates(model: str, usage: Usage, expected: Decimal) -> None:
    results = (
        calc_price(usage, model_ref=model),
        calc_price(usage, model_ref=model, provider_id='60db.ai'),
        calc_price(usage, model_ref=model, provider_api_url='https://api.60db.ai/v1'),
    )
    for result in results:
        assert result.provider.id == '60db'
        assert result.model.id == model
        assert abs(result.total_price - expected) < Decimal('1e-18')
