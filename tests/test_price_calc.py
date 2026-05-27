from datetime import datetime, timezone
from decimal import Decimal

import pytest
from inline_snapshot import snapshot

from voice_prices import Usage, calc_price
from voice_prices.types import ModelPrice, TieredPrices

pytestmark = pytest.mark.anyio


def test_sync_success_with_provider():
    price = calc_price(Usage(input_tokens=1000, output_tokens=100), model_ref='gpt-4o', provider_id='openai')

    assert price.input_price == snapshot(Decimal('0.0025'))
    assert price.output_price == snapshot(Decimal('0.001'))
    assert price.total_price == snapshot(Decimal('0.0035'))
    assert price.model.name == snapshot('gpt 4o')
    assert price.provider.id == snapshot('openai')
    assert price.auto_update_timestamp is None


def test_sync_success_with_url():
    price = calc_price(
        Usage(input_tokens=1000, output_tokens=100, cache_write_tokens=20, cache_read_tokens=30),
        model_ref='claude-3.5-sonnet@abc',
        provider_api_url='https://api.anthropic.com/foo/bar',
    )
    assert price.input_price == snapshot(Decimal('0.002934'))
    assert price.output_price == snapshot(Decimal('0.0015'))
    assert price.total_price == snapshot(Decimal('0.004434'))
    assert price.model.name == snapshot('Claude Sonnet 3.5')
    assert price.provider.name == snapshot('Anthropic')
    assert price.auto_update_timestamp is None


def test_sync_success_with_model():
    price = calc_price(Usage(input_tokens=1000, output_tokens=100), model_ref='gpt-4o')

    assert price.input_price == snapshot(Decimal('0.0025'))
    assert price.output_price == snapshot(Decimal('0.001'))
    assert price.total_price == snapshot(Decimal('0.0035'))
    assert price.model.name == snapshot('gpt 4o')
    assert price.provider.id == snapshot('openai')
    assert price.auto_update_timestamp is None


def test_sync_success_with_model_regex():
    price = calc_price(Usage(input_tokens=1000, output_tokens=100), model_ref='o3')

    assert price.input_price == snapshot(Decimal('0.002'))
    assert price.output_price == snapshot(Decimal('0.0008'))
    assert price.total_price == snapshot(Decimal('0.0028'))
    assert price.model.name == snapshot('o3')
    assert price.provider.id == snapshot('openai')


def test_openrouter_deepseek_v32_price():
    price = calc_price(
        Usage(input_tokens=2_000_000, output_tokens=1_000_000, cache_read_tokens=1_000_000),
        model_ref='deepseek/deepseek-v3.2',
        provider_id='openrouter',
    )

    assert price.input_price == snapshot(Decimal('0.2772'))
    assert price.output_price == snapshot(Decimal('0.3780'))
    assert price.total_price == snapshot(Decimal('0.6552'))
    assert price.model.name == snapshot('DeepSeek V3.2')
    assert price.provider.id == snapshot('openrouter')


def test_tiered_prices():
    price = calc_price(Usage(input_tokens=500_000), model_ref='gemini-1.5-flash', provider_id='google')
    # Google uses threshold-based pricing: if context > 128K, ALL tokens charged at tier price
    # (0.15 * 500000) / 1_000_000 = 0.075

    assert price.input_price == snapshot(Decimal('0.075'))
    assert price.output_price == snapshot(Decimal('0'))
    assert price.total_price == snapshot(Decimal('0.075'))
    assert price.model.name == snapshot('gemini 1.5 flash')
    assert price.provider.id == snapshot('google')


def test_model_price_str_tiered_prices_include_dollar_prefix():
    model_price = ModelPrice(input_mtok=TieredPrices(base=Decimal('2.5'), tiers=[]))
    assert str(model_price) == '$2.5/input MTok (+tiers)'


def test_requests_kcount_prices():
    # request count defaults to 1
    price = calc_price(Usage(), model_ref='sonar', provider_id='perplexity')
    assert price.input_price == snapshot(Decimal('0'))
    assert price.output_price == snapshot(Decimal('0'))
    assert price.total_price == snapshot(Decimal('0.012'))
    assert price.model.name == snapshot('Sonar')
    assert price.provider.name == snapshot('Perplexity')


def test_price_constraint_before():
    price = calc_price(Usage(input_tokens=1000), model_ref='o3', genai_request_timestamp=datetime(2025, 6, 1))
    assert price.input_price == snapshot(Decimal('0.01'))
    assert price.output_price == snapshot(Decimal('0'))
    assert price.total_price == snapshot(Decimal('0.01'))
    assert price.model.name == snapshot('o3')
    assert price.provider.name == snapshot('OpenAI')


def test_price_constraint_after():
    price = calc_price(Usage(input_tokens=1000), model_ref='o3')
    assert price.input_price == snapshot(Decimal('0.002'))
    assert price.output_price == snapshot(Decimal('0'))
    assert price.total_price == snapshot(Decimal('0.002'))
    assert price.model.name == snapshot('o3')
    assert price.provider.name == snapshot('OpenAI')


def test_price_constraint_time_of_date():
    price = calc_price(
        Usage(input_tokens=100_000_000),
        model_ref='deepseek-chat',
        genai_request_timestamp=datetime(2025, 6, 1, 16, tzinfo=timezone.utc),
    )
    assert price.input_price == snapshot(Decimal('27.00'))
    assert price.output_price == snapshot(Decimal('0'))
    assert price.total_price == snapshot(Decimal('27'))
    assert price.model.name == snapshot('DeepSeek Chat')
    assert price.provider.name == snapshot('Deepseek')
    price = calc_price(
        Usage(input_tokens=100_000_000),
        model_ref='deepseek-chat',
        genai_request_timestamp=datetime(2025, 6, 1, 17, tzinfo=timezone.utc),
    )
    assert price.input_price == snapshot(Decimal('13.500'))
    assert price.output_price == snapshot(Decimal('0'))
    assert price.total_price == snapshot(Decimal('13.5'))
    assert price.model.name == snapshot('DeepSeek Chat')
    assert price.provider.name == snapshot('Deepseek')


def test_provider_not_found_id():
    with pytest.raises(LookupError, match="Unable to find provider provider_id='foobar'"):
        calc_price(Usage(input_tokens=500_000), model_ref='gemini-1.5-flash', provider_id='foobar')


def test_provider_not_found_url():
    with pytest.raises(LookupError, match="Unable to find provider provider_api_url='foobar'"):
        calc_price(Usage(input_tokens=500_000), model_ref='gemini-1.5-flash', provider_api_url='foobar')


def test_provider_not_found_model_ref():
    with pytest.raises(LookupError, match="Unable to find provider with model matching 'llama2-70b-4096'"):
        calc_price(Usage(input_tokens=500_000), model_ref='llama2-70b-4096')


def test_model_not_found():
    with pytest.raises(LookupError, match="Unable to find model with model_ref='wrong' in google"):
        calc_price(Usage(input_tokens=500_000), model_ref='wrong', provider_id='google')


EXAMPLES: list[tuple[str, str]] = [
    # ('openrouter', 'amazon/us.amazon.nova-micro-v1:0'),
    # ('openrouter', 'amazon/us.amazon.nova-pro-v1:0'),
    ('anthropic', 'anthropic.claude-v2'),
    ('anthropic', 'claude-3-5-haiku-123'),
    ('anthropic', 'claude-3-5-haiku-20241022'),
    ('anthropic', 'claude-3-5-haiku-latest'),
    ('anthropic', 'claude-3-5-sonnet-20241022'),
    ('anthropic', 'claude-3-5-sonnet-latest'),
    ('anthropic', 'claude-3-7-sonnet-20250219'),
    ('anthropic', 'claude-3-7-sonnet-latest'),
    ('anthropic', 'claude-3-opus-20240229'),
    ('anthropic', 'claude-opus-4-20250514'),
    ('anthropic', 'claude-opus-4-20250514'),
    ('anthropic', 'claude-opus-4-0'),
    ('cohere', 'command-r7b-12-2024'),
    ('deepseek', 'deepseek-r1-distill-llama-70b'),
    ('google', 'gemini-1.5-flash-002'),
    ('google', 'gemini-1.5-flash-123'),
    ('google', 'gemini-1.5-flash'),
    ('google', 'gemini-1.5-pro-002'),
    ('google', 'gemini-2.0-flash-exp'),
    ('google', 'gemini-2.0-flash-thinking-exp-01-21'),
    ('google', 'gemini-2.0-flash'),
    ('google', 'gemini-2.5-pro-preview-03-25'),
    # ('openrouter', 'meta-llama/llama-3.3-70b-versatile'),
    # ('openrouter', 'meta-llama/llama-4-scout-17b-16e-instruct'),
    ('mistral', 'mistral-small-latest'),
    ('mistral', 'pixtral-12b-latest'),
    ('openai', 'gpt-3.5-turbo-0125'),
    ('openai', 'gpt-3.5-turbo-instruct:20230824-v2'),
    ('openai', 'gpt-4-0613'),
    ('openai', 'gpt-4.1-2025-04-14'),
    ('openai', 'gpt-4.1-mini-2025-04-14'),
    ('openai', 'gpt-4.1-mini'),
    ('openai', 'gpt-4.1-nano-2025-04-14'),
    ('openai', 'gpt-4.5-preview-2025-02-27'),
    ('openai', 'gpt-4o-2024-08-06'),
    ('openai', 'gpt-4o-2024-11-20'),
    ('openai', 'gpt-4o-audio-preview-2024-10-01'),
    ('openai', 'gpt-4o-audio-preview-2024-12-17'),
    ('openai', 'gpt-4o-mini-2024-07-18'),
    ('openai', 'gpt-4o-mini'),
    ('openai', 'gpt-4o'),
    ('openai', 'o3-mini-2025-01-31'),
    ('openai', 'gpt-5.4'),
    ('openai', 'gpt-5.4-pro'),
    ('openai', 'text-embedding-3-small'),
]


@pytest.mark.parametrize('provider,model', EXAMPLES)
def test_models_found(provider: str, model: str):
    calc_price(Usage(input_tokens=1000, output_tokens=100), model_ref=model, provider_id=provider)


def test_complex_usage():
    # Based on https://ai.google.dev/gemini-api/docs/pricing#gemini-2.5-flash
    # Input price
    #   $0.30 (text / image / video)
    #   $1.00 (audio)
    # Output price (including thinking tokens)
    #   $2.50
    # Context caching price
    #   $0.03 (text / image / video)
    #   $0.10 (audio)

    mil = 1_000_000
    assert calc_price(
        Usage(input_tokens=mil),
        'gemini-2.5-flash',
    ).total_price == snapshot(Decimal('0.3'))

    # input_audio_tokens == input_tokens means all tokens are audio tokens
    assert calc_price(
        Usage(input_tokens=mil, input_audio_tokens=mil),
        'gemini-2.5-flash',
    ).total_price == snapshot(Decimal('1.0'))

    assert calc_price(
        Usage(output_tokens=mil),
        'gemini-2.5-flash',
    ).total_price == snapshot(Decimal('2.5'))

    # All cached text tokens
    assert calc_price(
        Usage(input_tokens=mil, cache_read_tokens=mil),
        'gemini-2.5-flash',
    ).total_price == snapshot(Decimal('0.03'))

    # All cached audio tokens
    assert calc_price(
        Usage(input_tokens=mil, input_audio_tokens=mil, cache_read_tokens=mil, cache_audio_read_tokens=mil),
        'gemini-2.5-flash',
    ).total_price == snapshot(Decimal('0.10'))

    cached_text_tokens = 1
    uncached_text_tokens = 1_000
    cached_audio_tokens = 1_000_000
    uncached_audio_tokens = 1_000_000_000
    cached_tokens = cached_text_tokens + cached_audio_tokens
    audio_tokens = uncached_audio_tokens + cached_audio_tokens
    total_input_tokens = cached_text_tokens + uncached_text_tokens + cached_audio_tokens + uncached_audio_tokens
    assert total_input_tokens == 1_001_001_001

    assert (
        calc_price(
            Usage(
                input_tokens=total_input_tokens,
                input_audio_tokens=audio_tokens,
                cache_read_tokens=cached_tokens,
                cache_audio_read_tokens=cached_audio_tokens,
            ),
            'gemini-2.5-flash',
        ).total_price
        == snapshot(Decimal('1000.100_300_03'))
        == Decimal('0.03') * cached_text_tokens / mil
        + Decimal('0.3') * uncached_text_tokens / mil
        + Decimal('0.1') * cached_audio_tokens / mil
        + Decimal('1.0') * uncached_audio_tokens / mil
    )


def test_output_audio_usage():
    mil = 1_000_000

    assert calc_price(
        Usage(output_tokens=mil),
        'gpt-4o-realtime-preview',
    ).total_price == snapshot(Decimal('20.0'))

    # All audio tokens
    assert calc_price(
        Usage(output_tokens=mil, output_audio_tokens=mil),
        'gpt-4o-realtime-preview',
    ).total_price == snapshot(Decimal('80.0'))

    output_text_tokens = mil
    output_audio_tokens = mil * 1000
    total_output_tokens = output_text_tokens + output_audio_tokens
    assert (
        calc_price(
            Usage(output_tokens=total_output_tokens, output_audio_tokens=output_audio_tokens),
            'gpt-4o-realtime-preview',
        ).total_price
        == snapshot(Decimal('80020.0'))
        == Decimal('20') * output_text_tokens / mil + Decimal('80') * output_audio_tokens / mil
    )


# ----------------------------------------------------------------------------
# Section 3 engine semantics: voice_class edge cases (9 deterministic cases)
# ----------------------------------------------------------------------------


import random as _random  # noqa: E402  (kept near use-site to make the seed obvious)
import warnings as _warnings  # noqa: E402


def _tts_model(**price_kwargs: object) -> ModelPrice:
    """Build a ModelPrice for TTS-side engine tests. Defaults to $0.18/kchar."""
    defaults: dict[str, object] = {'input_kchars': Decimal('0.18')}
    defaults.update(price_kwargs)
    return ModelPrice(**defaults)  # type: ignore[arg-type]  # kwargs forwarded to dataclass


def test_voice_class_edge_case_1_missing_falls_back_to_default_silently():
    """Edge case 1: missing voice_class with multipliers present -> silent default."""
    mp = _tts_model(voice_multipliers={'default': Decimal('1.0'), 'premium': Decimal('1.5')})
    with _warnings.catch_warnings():
        _warnings.simplefilter('error')  # any warning becomes an exception
        result = mp.calc_price(Usage(characters=200))
    assert result['applied_voice_multiplier'] == Decimal('1.0')
    assert result['total_price'] == Decimal('0.036')


def test_voice_class_edge_case_2_unknown_emits_warning_and_falls_back():
    """Edge case 2: unknown voice_class emits warning, falls back to default."""
    mp = _tts_model(voice_multipliers={'default': Decimal('1.0'), 'premium': Decimal('1.5')})
    with _warnings.catch_warnings(record=True) as caught:
        _warnings.simplefilter('always')
        result = mp.calc_price(Usage(characters=200, voice_class='typoed'))
    assert len(caught) == 1
    assert 'typoed' in str(caught[0].message)
    assert result['applied_voice_multiplier'] == Decimal('1.0')


def test_voice_class_edge_case_3_class_provided_but_no_multipliers():
    """Edge case 3: voice_class on Usage, multipliers unset on model -> silently ignored."""
    mp = _tts_model()  # no voice_multipliers
    with _warnings.catch_warnings():
        _warnings.simplefilter('error')
        result = mp.calc_price(Usage(characters=200, voice_class='premium'))
    assert result['applied_voice_multiplier'] is None
    assert result['breakdown'].voice_class_input_adjustment == Decimal('0')


def test_voice_class_edge_case_4_multipliers_set_but_no_char_or_sec_usage():
    """Edge case 4: multipliers set, no char/sec in Usage -> adjustments are 0,
    applied_voice_multiplier still reports the looked-up value.
    """
    mp = ModelPrice(
        input_audio_mtok=Decimal('40'),
        voice_multipliers={'default': Decimal('1.0'), 'premium': Decimal('1.5')},
    )
    result = mp.calc_price(Usage(input_audio_tokens=1000, voice_class='premium'))
    assert result['applied_voice_multiplier'] == Decimal('1.5')  # reported even though nothing scaled
    assert result['breakdown'].voice_class_input_adjustment == Decimal('0')
    assert result['breakdown'].voice_class_output_adjustment == Decimal('0')


def test_voice_class_edge_case_5_input_mtok_and_input_kchars_coexist():
    """Edge case 5: both input_mtok and input_kchars set; they read disjoint Usage fields."""
    mp = ModelPrice(input_mtok=Decimal('1.0'), input_kchars=Decimal('0.18'))
    result = mp.calc_price(Usage(input_tokens=1_000_000, characters=1000))
    # input_mtok: 1.0 / mil * 1_000_000 = 1.0
    # input_kchars: 0.18 / 1000 * 1000 = 0.18
    assert result['breakdown'].input_tokens == Decimal('1.0')
    assert result['breakdown'].input_kchars == Decimal('0.18')
    assert result['input_price'] == Decimal('1.18')


def test_voice_class_edge_case_6_zero_characters_with_multipliers():
    """Edge case 6: zero characters, multipliers set -> input_kchars = 0, adjustment = 0."""
    mp = _tts_model(voice_multipliers={'default': Decimal('1.0'), 'premium': Decimal('1.5')})
    result = mp.calc_price(Usage(characters=0, voice_class='premium'))
    assert result['breakdown'].input_kchars == Decimal('0')
    assert result['breakdown'].voice_class_input_adjustment == Decimal('0')
    assert result['applied_voice_multiplier'] == Decimal('1.5')


def test_voice_class_edge_case_7_conditional_price_picks_correct_multipliers():
    """Edge case 7: ConditionalPrice with per-conditional voice_multipliers.

    Two date-effective ModelPrice blocks, each with its own multipliers; calc_price
    runs the multiplier resolution against the active block.
    """
    from datetime import date

    from voice_prices.types import (
        ClauseEquals as _ClauseEquals,
        ConditionalPrice,
        ModelInfo,
        Provider,
        StartDateConstraint,
    )

    old_block = ModelPrice(
        input_kchars=Decimal('0.18'),
        voice_multipliers={'default': Decimal('1.0'), 'premium': Decimal('2.0')},
    )
    new_block = ModelPrice(
        input_kchars=Decimal('0.18'),
        voice_multipliers={'default': Decimal('1.0'), 'premium': Decimal('1.5')},
    )
    model = ModelInfo(
        id='x',
        match=_ClauseEquals('x'),
        prices=[
            ConditionalPrice(constraint=None, prices=old_block),
            ConditionalPrice(constraint=StartDateConstraint(start_date=date(2026, 1, 1)), prices=new_block),
        ],
    )
    provider = Provider(id='p', name='P', api_pattern=r'.*')

    # Before 2026-01-01 -> old block, 2.0x multiplier
    pre = model.calc_price(
        Usage(characters=200, voice_class='premium'),
        provider,
        genai_request_timestamp=datetime(2025, 12, 31, tzinfo=timezone.utc),
    )
    assert pre.applied_voice_multiplier == Decimal('2.0')

    # After 2026-01-01 -> new block, 1.5x multiplier
    post = model.calc_price(
        Usage(characters=200, voice_class='premium'),
        provider,
        genai_request_timestamp=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    assert post.applied_voice_multiplier == Decimal('1.5')


def test_voice_class_edge_case_8_tiered_prices_on_kchars_rejected_at_schema_layer():
    """Edge case 8: TieredPrices on input_kchars is rejected at schema validation.

    Engine never sees this; the Pydantic schema in prices_types.py rejects it. See
    test_schema_validation.py::test_no_tiers_on_char_or_sec_fields.
    """
    # Sanity placeholder. The validator lives on the Pydantic schema, exercised in
    # the schema-validation suite. Engine has no special handling needed.
    pass


def test_voice_class_edge_case_9_negative_multiplier_rejected_at_schema_layer():
    """Edge case 9: zero or negative multiplier rejected by Gt(0) constraint.

    Engine never sees this; see test_schema_validation.py::test_voice_multiplier_values_positive.
    """
    pass


# ----------------------------------------------------------------------------
# Math invariant: total_price == breakdown.sum() over a deterministic matrix
# ----------------------------------------------------------------------------


def test_invariant_breakdown_sums_to_total():
    """Section 5: seeded matrix over (chars, kchars_rate, multiplier, audio_secs, kseconds_rate).

    For each random (rate, usage, multiplier) tuple, the math invariant must hold:
    `total_price == sum(every Decimal field on PriceBreakdown)`.
    """
    rng = _random.Random(20260527)  # seed pinned in design Section 5.2

    def _rand_decimal(low: float, high: float, places: int = 6) -> Decimal:
        # quantize to a Decimal with `places` fractional digits to avoid float noise
        return Decimal(str(round(rng.uniform(low, high), places)))

    failures: list[str] = []
    for _ in range(500):
        characters = rng.randint(0, 1_000_000)
        kchars_rate = _rand_decimal(0.001, 1.0)

        audio_seconds = rng.randint(0, 100_000)
        kseconds_rate = _rand_decimal(0.001, 1.0)

        # Multiplier sometimes None (no voice_multipliers), sometimes in [0.1, 5.0].
        use_multipliers = rng.random() < 0.7
        if use_multipliers:
            multiplier_value = _rand_decimal(0.1, 5.0, places=3)
            voice_multipliers = {'default': multiplier_value}
            voice_class = None
        else:
            voice_multipliers = None
            voice_class = None

        # Optional token rate to mix with TTS.
        input_mtok = _rand_decimal(0.1, 50.0) if rng.random() < 0.5 else None
        input_tokens = rng.randint(0, 1_000_000) if input_mtok is not None else 0

        mp = ModelPrice(
            input_mtok=input_mtok,
            input_kchars=kchars_rate,
            output_audio_kseconds=kseconds_rate,
            voice_multipliers=voice_multipliers,
        )
        usage = Usage(
            input_tokens=input_tokens if input_tokens > 0 else None,
            characters=characters,
            audio_output_seconds=audio_seconds,
            voice_class=voice_class,
        )
        result = mp.calc_price(usage)

        total = result['total_price']
        breakdown_sum = result['breakdown'].sum()
        if total != breakdown_sum:
            failures.append(
                f'mismatch: total={total} sum={breakdown_sum} usage={usage} mp.voice_multipliers={voice_multipliers}'
            )

    assert not failures, '\n'.join(failures[:5])
