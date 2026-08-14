"""Tests for the contribution guard.

`check_model` is the whole of the logic and takes plain dicts, so every case here is a
unit test against it. The git plumbing (`changed_provider_files`, `base_version`) is
exercised by `test_check_contribution_on_head`, which runs the real CLI against the real
repository: on a clean checkout that finds nothing to check, which is the honest
assertion, since a green run must not depend on what happens to be on the branch.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from prices.check_contribution import (
    AGGREGATOR_HOSTS,
    MAX_CHECKED_AGE_DAYS,
    PRICE_BANDS,
    check_contribution,
    check_model,
    has_screenshot,
    is_api_backed,
)

TODAY = date(2026, 8, 14)
HOSTS = {'acme.example'}


def model(**overrides: Any) -> dict[str, Any]:
    """A model that passes every check, so each test changes exactly one thing."""
    base: dict[str, Any] = {
        'id': 'acme-1',
        'prices_checked': TODAY,
        'pricing_source_url': 'https://acme.example/pricing#acme-1',
        'price_comments': 'Source rate $0.006/minute. 0.006 * 1000 / 60 = 0.1 exactly.',
        'prices': {'input_audio_kseconds': 0.1},
    }
    base.update(overrides)
    return base


def messages(m: dict[str, Any], *, hosts: set[str] | None = None) -> list[str]:
    found = check_model('acme', m, pricing_hosts=HOSTS if hosts is None else hosts, today=TODAY)
    return [f.message for f in found]


def test_clean_model_passes():
    assert messages(model()) == []


def test_missing_pricing_source_url():
    (msg,) = messages(model(pricing_source_url=None))
    assert '`pricing_source_url`' in msg


def test_http_url_rejected():
    msgs = messages(model(pricing_source_url='http://acme.example/pricing'))
    assert any('not https' in m for m in msgs)


@pytest.mark.parametrize('host', sorted(AGGREGATOR_HOSTS))
def test_aggregator_source_rejected(host: str):
    msgs = messages(model(pricing_source_url=f'https://{host}/models/acme-1'), hosts=set())
    assert any('aggregator' in m for m in msgs), msgs


def test_aggregator_beats_the_host_mismatch_message():
    """An OpenRouter URL is wrong because it is an aggregator, not because of `pricing_urls`."""
    msgs = messages(model(pricing_source_url='https://openrouter.ai/acme/acme-1'))
    assert any('aggregator' in m for m in msgs)
    assert not any('pricing_urls' in m for m in msgs)


def test_www_prefix_is_not_a_host_mismatch():
    assert messages(model(pricing_source_url='https://www.acme.example/pricing')) == []


def test_offsite_url_flagged():
    (msg,) = messages(model(pricing_source_url='https://someblog.example/acme-prices'))
    assert 'not one of the provider `pricing_urls` hosts' in msg


def test_no_pricing_urls_means_no_host_check():
    """A provider with no `pricing_urls` cannot fail a host comparison against nothing."""
    assert messages(model(pricing_source_url='https://anywhere.example/x'), hosts=set()) == []


def test_missing_prices_checked():
    (msg,) = messages(model(prices_checked=None))
    assert '`prices_checked`' in msg


def test_future_prices_checked():
    (msg,) = messages(model(prices_checked=TODAY + timedelta(days=1)))
    assert 'in the future' in msg


def test_stale_prices_checked():
    old = TODAY - timedelta(days=MAX_CHECKED_AGE_DAYS + 1)
    (msg,) = messages(model(prices_checked=old))
    assert 'days old' in msg


def test_prices_checked_at_the_boundary_passes():
    assert messages(model(prices_checked=TODAY - timedelta(days=MAX_CHECKED_AGE_DAYS))) == []


def test_converted_field_needs_price_comments():
    (msg,) = messages(model(price_comments=None))
    assert '`price_comments`' in msg


def test_token_field_does_not_need_price_comments():
    """Vendors quote tokens per million already, so there is no conversion to show."""
    m = model(price_comments=None, prices={'input_mtok': 2.0, 'output_mtok': 8.0})
    assert messages(m) == []


@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('input_kchars', 30),  # $30/1M chars typed into a per-1k field
        ('input_audio_kseconds', 0.000001),
        ('output_mtok', 100000),
        ('input_audio_mtok', 0.0001),
    ],
)
def test_unit_error_caught(field: str, value: float):
    m = model(prices={field: value})
    assert any('plausible band' in msg for msg in messages(m)), messages(m)


@pytest.mark.parametrize(('field', 'band'), sorted(PRICE_BANDS.items()))
def test_band_endpoints_are_inclusive(field: str, band: tuple[Any, Any]):
    low, high = band
    for value in (low, high):
        m = model(prices={field: value})
        assert not any('plausible band' in msg for msg in messages(m)), (field, value)


def test_zero_is_not_a_unit_error():
    """A free or bundled rate is a human call. Flagging it as a unit error would be noise."""
    m = model(prices={'input_kchars': 0})
    assert not any('plausible band' in msg for msg in messages(m))


def test_unknown_field_is_not_banded():
    m = model(prices={'input_audio_kseconds': 0.1, 'some_future_field': 999999})
    assert messages(m) == []


def test_tiered_prices_are_skipped():
    """Tiered and conditional shapes need a human; this check must not block on them."""
    m = model(prices={'input_mtok': [{'from': 0, 'price': 1}]}, price_comments=None)
    assert messages(m) == []


def test_model_with_no_prices_is_skipped():
    assert messages(model(prices={}, pricing_source_url=None, prices_checked=None)) == []


def test_several_problems_are_all_reported():
    m = model(prices_checked=None, pricing_source_url=None, price_comments=None)
    assert len(messages(m)) == 3


def test_finding_str_names_the_model():
    (finding,) = check_model('acme', model(prices_checked=None), pricing_hosts=HOSTS, today=TODAY)
    assert str(finding).startswith('acme/acme-1: ')


def test_check_contribution_on_head(capsys: pytest.CaptureFixture[str]):
    """The real CLI against the real repo: a clean checkout has nothing to defend."""
    assert check_contribution() == 0
    assert 'model' in capsys.readouterr().out


@pytest.mark.parametrize(
    'body',
    [
        '![pricing page](https://example.com/shot.png)',
        'here it is <img src="x.png">',
        'https://github.com/user-attachments/assets/abc-123',
        'https://user-images.githubusercontent.com/1/2.png',
    ],
)
def test_screenshot_detected(body: str):
    assert has_screenshot(body)


@pytest.mark.parametrize(
    'body',
    [
        '',
        'I checked the pricing page, trust me',
        'see https://acme.example/pricing',
        '[a link](https://example.com/shot.png)',  # a link is not an embedded image
    ],
)
def test_screenshot_not_detected(body: str):
    assert not has_screenshot(body)


def test_api_backed_detection():
    assert is_api_backed({'provenance': {'api_backed': True}})
    assert not is_api_backed({'provenance': {'api_backed': False}})
    assert not is_api_backed({'provenance': {'source': 'imported'}})
    assert not is_api_backed({})
