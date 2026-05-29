"""Tests for the voice pricing freshness check (network and LLM mocked)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from prices.freshness.diff import classify, parse_number, to_our_unit
from prices.freshness.models import Category, Extraction, RenderResult, WorkItem
from prices.freshness.report import DriftEdit, Report, apply_edits, build_report
from prices.freshness.run import run_freshness_check
from prices.freshness.select import _voice_field, is_stale, select_stale
from prices.prices_types import ModelPrice
from prices.update import ProviderYaml

# ---- helpers ----------------------------------------------------------------


def item(model_id: str = 'nova-x', field: str = 'input_audio_kseconds', rate: str = '0.08') -> WorkItem:
    return WorkItem('deepgram', model_id, 'https://deepgram.com/pricing', field, Decimal(rate))


def good_extraction(rate_value: float, unit: str, *, quote: str, row: str, conf: str = 'high') -> Extraction:
    return Extraction(
        found=True,
        rate_value=rate_value,
        rate_unit=unit,
        currency='USD',
        confidence=conf,
        evidence_quote=quote,
        matched_row_name=row,
    )


OK_RENDER = RenderResult(
    ok=True, http_status=200, final_url='https://deepgram.com/pricing', text='', screenshot_path=None
)


def render_with(text: str) -> RenderResult:
    return RenderResult(
        ok=True, http_status=200, final_url='https://deepgram.com/pricing', text=text, screenshot_path=None
    )


# ---- select -----------------------------------------------------------------


def test_select_real_catalog_staleness_boundary():
    # Before the catalog's check dates, nothing is stale.
    assert select_stale(date(2020, 1, 1)) == []
    # Far in the future, every voice entry is stale; all=True ignores staleness.
    future = select_stale(date(2030, 1, 1))
    every = select_stale(date(2020, 1, 1), all=True)
    assert len(future) == len(every) == 19
    providers = {w.provider_id for w in every}
    assert providers == {'deepgram', 'elevenlabs', 'openai', 'assemblyai', 'cartesia', 'groq'}
    for w in every:
        assert w.field in ('input_kchars', 'input_audio_kseconds', 'output_audio_kseconds')
        assert w.url.startswith('http')
        assert w.current_rate > 0


def test_is_stale_boundary():
    from prices.prices_types import ClauseEquals, ModelInfo

    price = ModelPrice(input_audio_kseconds=Decimal('0.08'))
    checked = ModelInfo(id='x', match=ClauseEquals(equals='x'), prices=price, prices_checked=date(2026, 1, 1))
    assert is_stale(checked, 60, date(2026, 1, 30)) is False  # within threshold
    assert is_stale(checked, 60, date(2026, 5, 1)) is True  # past threshold
    never = ModelInfo(id='x', match=ClauseEquals(equals='x'), prices=price)
    assert is_stale(never, 60, date(2026, 1, 1)) is True  # never checked


def test_voice_field_forward_compat_output_audio_kseconds():
    # No catalog entry uses output_audio_kseconds yet; lock in that it would be selected.
    price = ModelPrice(output_audio_kseconds=Decimal('0.5'))
    assert _voice_field(price) == ('output_audio_kseconds', Decimal('0.5'))


# ---- parse_number / to_our_unit ---------------------------------------------


def test_parse_number():
    assert parse_number('$0.0065 per minute') == 0.0065
    assert parse_number('65 cents per minute') == 0.65
    assert parse_number('0,108 per minute') is None  # comma-decimal rejected
    assert parse_number('no number here') is None


def test_to_our_unit_conversions():
    assert to_our_unit(0.0065, 'per minute', 'input_audio_kseconds') == 0.0065 * 1000 / 60
    assert to_our_unit(0.111, 'per hour', 'input_audio_kseconds') == 0.111 * 1000 / 3600
    assert to_our_unit(0.000048, 'per second', 'input_audio_kseconds') == 0.000048 * 1000
    assert to_our_unit(15.0, 'per 1M characters', 'input_kchars') == 15.0 / 1000
    assert to_our_unit(0.015, 'per 1k characters', 'input_kchars') == 0.015
    # disallowed / non-convertible units -> None
    assert to_our_unit(22.0, 'per month', 'input_audio_kseconds') is None
    assert to_our_unit(5.0, 'per 1M tokens', 'input_audio_kseconds') is None


# ---- diff.classify ----------------------------------------------------------


def test_classify_match_handles_truncated_stored_values():
    # Flux stored 0.108333 vs $0.0065/min, and nova-3-batch stored 0.12833 vs $0.0077/min:
    # tolerance comparison must treat both as MATCH (no spurious DRIFT from rounding).
    flux = item('flux-general', rate='0.108333')
    ex = good_extraction(0.0065, 'per minute', quote='Flux $0.0065 per minute', row='flux-general')
    assert classify(flux, render_with(ex.evidence_quote), ex).category is Category.MATCH

    batch = item('nova-3-batch', rate='0.12833')
    ex2 = good_extraction(0.0077, 'per minute', quote='Nova-3 batch $0.0077 per minute', row='nova-3-batch')
    assert classify(batch, render_with(ex2.evidence_quote), ex2).category is Category.MATCH


def test_classify_drift_proposes_new_rate():
    it = item('nova-x', rate='0.08')  # = $0.0048/min
    ex = good_extraction(0.006, 'per minute', quote='Nova-x $0.006 per minute', row='nova-x')
    f = classify(it, render_with(ex.evidence_quote), ex)
    assert f.category is Category.DRIFT
    assert f.proposed_rate == Decimal('0.1')


def test_classify_magnitude_guard_routes_unit_error_to_unverified():
    it = item('nova-x', rate='0.08')
    # $0.0048 but mislabeled "per second" -> 4.8/1k-sec, a 60x swing -> UNVERIFIED not DRIFT.
    ex = good_extraction(0.0048, 'per second', quote='$0.0048 per second', row='nova-x')
    assert classify(it, render_with(ex.evidence_quote), ex).category is Category.UNVERIFIED


def test_classify_guard_failures_are_unverified():
    it = item('nova-x', rate='0.08')
    rendered = render_with('Nova-x $0.006 per minute')
    # fabricated quote (not a substring of the page)
    ex = good_extraction(0.006, 'per minute', quote='Nova-x $0.006 per minute (fake)', row='nova-x')
    assert classify(it, rendered, ex).category is Category.UNVERIFIED
    # number in quote does not match rate_value
    ex = good_extraction(0.006, 'per minute', quote='Nova-x $0.009 per minute', row='nova-x')
    assert classify(it, render_with(ex.evidence_quote), ex).category is Category.UNVERIFIED
    # wrong currency
    ex = Extraction(True, 0.006, 'per minute', 'EUR', 'high', 'Nova-x 0.006 per minute', 'nova-x')
    assert classify(it, render_with(ex.evidence_quote), ex).category is Category.UNVERIFIED
    # wrong row name (does not name the model)
    ex = good_extraction(0.006, 'per minute', quote='Aura $0.006 per minute', row='aura-2')
    assert classify(it, render_with(ex.evidence_quote), ex).category is Category.UNVERIFIED
    # low confidence
    ex = good_extraction(0.006, 'per minute', quote='Nova-x $0.006 per minute', row='nova-x', conf='medium')
    assert classify(it, render_with(ex.evidence_quote), ex).category is Category.UNVERIFIED


def test_classify_url_stale_and_gone():
    blocked = RenderResult(ok=False, http_status=403, final_url='x', text='', screenshot_path=None, blocked=True)
    assert classify(item(), blocked, None).category is Category.URL_STALE
    assert classify(item(), OK_RENDER, Extraction(False, None, None, None, 'low', '', '')).category is Category.GONE


# ---- report -----------------------------------------------------------------


def test_build_report_actionable_only_on_non_match():
    match = classify(
        item('nova-x', rate='0.08'),
        render_with('Nova-x $0.0048 per minute'),
        good_extraction(0.0048, 'per minute', quote='Nova-x $0.0048 per minute', row='nova-x'),
    )
    assert match.category is Category.MATCH
    assert build_report([match], date(2026, 5, 29)).actionable is False

    drift = classify(
        item('nova-x', rate='0.08'),
        render_with('Nova-x $0.006 per minute'),
        good_extraction(0.006, 'per minute', quote='Nova-x $0.006 per minute', row='nova-x'),
    )
    rep = build_report([drift], date(2026, 5, 29))
    assert rep.actionable is True
    assert len(rep.drift_edits) == 1
    assert 'Proposed rate changes' in rep.pr_body


def test_apply_edits_changes_rate_and_comment_not_prices_checked(tmp_path: Path):
    yml = tmp_path / 'testgram.yml'
    yml.write_text(
        'name: Testgram\n'
        'id: testgram\n'
        'pricing_urls:\n  - https://t.example/pricing\n'
        "api_pattern: 'https://api\\.t\\.example'\n"
        'model_match:\n  starts_with: tnova-\n'
        'provider_match:\n  contains: testgram\n'
        'models:\n'
        '  - id: tnova-1\n'
        '    name: TNova\n'
        '    match:\n      equals: tnova-1\n'
        '    prices_checked: 2026-05-01\n'
        '    pricing_source_url: https://t.example/pricing#tnova-1\n'
        '    prices:\n      input_audio_kseconds: 0.08\n'
    )
    report = Report(drift_edits=[DriftEdit('testgram', 'tnova-1', 'input_audio_kseconds', Decimal('0.1'), 'bot note')])
    changed = apply_edits(report, {'testgram': ProviderYaml(yml)})
    assert changed == {'testgram'}

    reloaded = ProviderYaml(yml)
    model = reloaded.provider.find_model('tnova-1')
    assert model is not None and isinstance(model.prices, ModelPrice)
    assert model.prices.input_audio_kseconds == Decimal('0.1')
    assert model.prices_checked == date(2026, 5, 1)  # bot never touches prices_checked
    assert 'bot note' in yml.read_text()


# ---- run (shared-URL single pass, no false DRIFT for an absent model) -------


def test_run_shared_url_single_pass_absent_model_is_gone_not_drift():
    items = [item('nova-3', rate='0.08'), item('nova-2', rate='0.098333')]  # same deepgram url
    render_calls: list[str] = []

    def fake_render(url: str) -> RenderResult:
        render_calls.append(url)
        return render_with('Nova-3 $0.0048 per minute')

    def fake_extract(_text: str, _work: object) -> dict[str, Extraction]:
        # nova-3 present and matching; nova-2 absent from this page.
        return {'nova-3': good_extraction(0.0048, 'per minute', quote='Nova-3 $0.0048 per minute', row='nova-3')}

    report = run_freshness_check(date(2026, 5, 29), items, render=fake_render, extract=fake_extract)
    assert render_calls == ['https://deepgram.com/pricing']  # one render for the shared url
    assert report.counts[Category.MATCH] == 1
    assert report.counts[Category.GONE] == 1
    assert report.counts[Category.DRIFT] == 0  # absent model never becomes a false DRIFT
