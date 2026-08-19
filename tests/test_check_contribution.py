"""Tests for the contribution guard.

`check_model` holds the per-model rules and takes plain dicts, so those cases are unit
tests against it directly.

The CLI walks real git history, so the `repo` fixture builds a throwaway repository with a
base commit and a feature branch and runs the real thing against it. An earlier version of
this file ran the CLI against *this* checkout instead. It passed locally and failed in CI,
because the test job clones shallowly and has no `origin/main`, which sent the code down a
fallback that reported all 1,369 catalogued models as newly added. Both the fallback and
the test were wrong; the fixture is why that cannot recur.
"""

from __future__ import annotations

import subprocess
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from prices.check_contribution import (
    AGGREGATOR_HOSTS,
    MAX_CHECKED_AGE_DAYS,
    PRICE_BANDS,
    check_contribution,
    check_model,
    declares_unverified,
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


def _run(*args: str, cwd: Path) -> None:
    subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True)


def _provider_yaml(rate: str, *, checked: str, url: str = 'https://acme.example/pricing') -> str:
    return (
        'name: Acme\n'
        'id: acme\n'
        'pricing_urls:\n'
        '  - https://acme.example/pricing\n'
        'models:\n'
        '  - id: acme-1\n'
        f'    prices_checked: {checked}\n'
        f'    pricing_source_url: {url}\n'
        '    price_comments: Source rate $0.006/minute. 0.006 * 1000 / 60 = 0.1 exactly.\n'
        '    prices:\n'
        f'      input_audio_kseconds: {rate}\n'
    )


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway git repo with one committed provider, checked out on a branch.

    The CLI walks real git history, so the only honest way to test it is against a real
    repository. Using this checkout instead would couple the result to whatever happens to
    be on the current branch, and to whether CI cloned deeply enough to have `origin/main`.
    """
    _run('init', '-q', '-b', 'main', cwd=tmp_path)
    _run('config', 'user.email', 't@example.com', cwd=tmp_path)
    _run('config', 'user.name', 'Test', cwd=tmp_path)
    providers = tmp_path / 'prices' / 'providers'
    providers.mkdir(parents=True)
    (providers / 'acme.yml').write_text(_provider_yaml('0.1', checked=str(TODAY)))
    _run('add', '-A', cwd=tmp_path)
    _run('commit', '-qm', 'base', cwd=tmp_path)
    _run('checkout', '-q', '-b', 'feature', cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('BASE_REF', 'main')
    monkeypatch.delenv('PR_BODY', raising=False)
    monkeypatch.delenv('REQUIRE_BASE', raising=False)
    return tmp_path


@pytest.mark.usefixtures('repo')
def test_unchanged_branch_checks_nothing(capsys: pytest.CaptureFixture[str]):
    assert check_contribution() == 0
    assert 'no added or repriced models' in capsys.readouterr().out


def test_repriced_model_is_checked(repo: Path, capsys: pytest.CaptureFixture[str]):
    (repo / 'prices/providers/acme.yml').write_text(_provider_yaml('0.2', checked=str(TODAY)))
    _run('commit', '-qam', 'reprice', cwd=repo)
    assert check_contribution() == 0
    assert 'checked 1 added or repriced model' in capsys.readouterr().out


def test_repriced_model_with_a_unit_error_fails(repo: Path, capsys: pytest.CaptureFixture[str]):
    (repo / 'prices/providers/acme.yml').write_text(_provider_yaml('600', checked=str(TODAY)))
    _run('commit', '-qam', 'slop', cwd=repo)
    assert check_contribution() == 1
    assert 'plausible band' in capsys.readouterr().out


def test_untouched_model_is_not_re_litigated(repo: Path, capsys: pytest.CaptureFixture[str]):
    """A branch that only edits prose must not inherit an old model's problems."""
    (repo / 'prices/providers/acme.yml').write_text(
        _provider_yaml('0.1', checked=str(TODAY)).replace('name: Acme', 'name: Acme Speech')
    )
    _run('commit', '-qam', 'rename', cwd=repo)
    assert check_contribution() == 0
    assert 'no added or repriced models' in capsys.readouterr().out


@pytest.mark.usefixtures('repo')
def test_missing_base_ref_skips_by_default(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    """A shallow clone must not report the whole catalog as newly added."""
    monkeypatch.setenv('BASE_REF', 'origin/nope')
    assert check_contribution() == 0
    assert 'not available' in capsys.readouterr().out


@pytest.mark.usefixtures('repo')
def test_missing_base_ref_fails_when_required(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    """In CI the base ref is guaranteed, so its absence is a broken guard, not a shallow clone."""
    monkeypatch.setenv('BASE_REF', 'origin/nope')
    monkeypatch.setenv('REQUIRE_BASE', '1')
    assert check_contribution() == 1
    assert 'REQUIRE_BASE=1' in capsys.readouterr().out


def test_screenshot_required_for_hand_read_rate(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    (repo / 'prices/providers/acme.yml').write_text(_provider_yaml('0.2', checked=str(TODAY)))
    _run('commit', '-qam', 'reprice', cwd=repo)
    monkeypatch.setenv('PR_BODY', 'I read the page, honest')
    assert check_contribution() == 1
    assert 'No screenshot' in capsys.readouterr().out


def test_screenshot_satisfies_the_check(repo: Path, monkeypatch: pytest.MonkeyPatch):
    (repo / 'prices/providers/acme.yml').write_text(_provider_yaml('0.2', checked=str(TODAY)))
    _run('commit', '-qam', 'reprice', cwd=repo)
    monkeypatch.setenv('PR_BODY', 'proof: ![page](https://github.com/user-attachments/assets/x.png)')
    assert check_contribution() == 0


def test_importer_rows_are_exempt_from_the_screenshot(repo: Path, monkeypatch: pytest.MonkeyPatch):
    """No human reads a page for an api_backed row, so demanding evidence of one is theatre."""
    yaml_text = _provider_yaml('0.2', checked=str(TODAY)).replace(
        '    prices:', '    provenance:\n      source: imported\n      api_backed: true\n    prices:'
    )
    (repo / 'prices/providers/acme.yml').write_text(yaml_text)
    _run('commit', '-qam', 'importer', cwd=repo)
    monkeypatch.setenv('PR_BODY', 'no image here')
    assert check_contribution() == 0


def test_new_provider_file_is_all_new_models(repo: Path, capsys: pytest.CaptureFixture[str]):
    (repo / 'prices/providers/nimbus.yml').write_text(
        _provider_yaml('0.1', checked=str(TODAY)).replace('acme', 'nimbus').replace('Acme', 'Nimbus')
    )
    _run('add', '-A', cwd=repo)
    _run('commit', '-qm', 'new provider', cwd=repo)
    assert check_contribution() == 0
    assert 'checked 1 added or repriced model' in capsys.readouterr().out


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


# ---- rows that do not claim human verification -------------------------------


def test_imported_row_needs_no_prices_checked():
    """An importer-written row deliberately carries no date. Forcing one would manufacture
    the false verification claim this project exists to prevent."""
    m = model(prices_checked=None, provenance={'source': 'imported'})
    assert messages(m) == []


def test_seed_row_needs_no_prices_checked():
    m = model(prices_checked=None, provenance={'source': 'seed'})
    assert messages(m) == []


def test_row_claiming_verification_still_needs_a_date():
    """No provenance means the row is claiming a human read the page, so the date is required."""
    (msg,) = messages(model(prices_checked=None))
    assert '`prices_checked`' in msg
    assert 'provenance.source' in msg


def test_api_backed_alone_still_needs_a_date_or_a_source():
    """api_backed says the rate is machine-readable, not that it is unverified."""
    m = model(prices_checked=None, provenance={'api_backed': True})
    assert any('`prices_checked`' in msg for msg in messages(m))


@pytest.mark.parametrize(
    ('provenance', 'expected'),
    [
        ({'source': 'imported'}, True),
        ({'source': 'seed'}, True),
        ({'source': 'imported', 'api_backed': True}, True),
        ({'api_backed': True}, False),
        ({}, False),
        (None, False),
    ],
)
def test_declares_unverified(provenance: Any, expected: bool):
    assert declares_unverified({'provenance': provenance} if provenance is not None else {}) is expected


def test_unverified_row_is_exempt_from_the_screenshot(repo: Path, monkeypatch: pytest.MonkeyPatch):
    yaml_text = _provider_yaml('0.2', checked=str(TODAY)).replace(
        f'    prices_checked: {TODAY}\n', '    provenance:\n      source: imported\n'
    )
    (repo / 'prices/providers/acme.yml').write_text(yaml_text)
    _run('commit', '-qam', 'imported', cwd=repo)
    monkeypatch.setenv('PR_BODY', 'no image here')
    assert check_contribution() == 0


# ---- the aggregator is sometimes the vendor ----------------------------------


def test_aggregator_may_cite_itself_when_it_is_the_provider():
    """openrouter.yml prices what OpenRouter charges, and openrouter.ai is the only page that
    publishes it. Banning the citation there forces the rate to be misattributed elsewhere."""
    m = model(pricing_source_url='https://openrouter.ai/openai/gpt-audio')
    assert messages(m, hosts={'openrouter.ai'}) == []


def test_aggregator_citation_still_banned_for_a_different_vendor():
    """The rule that matters: acme's rate may not be sourced from OpenRouter."""
    msgs = messages(model(pricing_source_url='https://openrouter.ai/acme/acme-1'))
    assert any('aggregator' in msg for msg in msgs)


def test_huggingface_may_cite_itself():
    m = model(pricing_source_url='https://huggingface.co/pricing')
    assert messages(m, hosts={'huggingface.co'}) == []
