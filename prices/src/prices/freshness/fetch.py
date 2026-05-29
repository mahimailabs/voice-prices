"""Render a pricing page and extract the rate for each model on it.

The browser (Playwright) and the LLM are injected via the `render` and `extract`
callables, so the pipeline is fully testable with fakes and never shells out in
tests. The default extractor drives the model through Claude Code headlessly
(`claude -p`), which authenticates with a Claude Pro/Max subscription via the
CLAUDE_CODE_OAUTH_TOKEN env var (set from `claude setup-token`); no Anthropic API
key is used. The default implementations run only in the scheduled workflow.

Models that share a URL are extracted in a single pass so the LLM disambiguates
rows against each other rather than hunting for one in isolation.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast

from .models import Extraction, RenderResult, WorkItem

RenderFn = Callable[[str], RenderResult]
ExtractFn = Callable[[str, Sequence[WorkItem]], dict[str, Extraction]]

_LOGIN_MARKERS = ('log in', 'sign in', 'create account', 'access denied', 'just a moment')
_EXTRACT_MODEL = 'claude-haiku-4-5-20251001'


def default_render(url: str, *, screenshot_dir: Path | None = None) -> RenderResult:
    """Render `url` in a headless US-locale Chromium and capture text + screenshot."""
    from playwright.sync_api import sync_playwright

    screenshot_path: str | None = None
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            locale='en-US',
            timezone_id='America/New_York',
            extra_http_headers={'Accept-Language': 'en-US,en;q=0.9'},
        )
        page = context.new_page()
        try:
            response = page.goto(url, wait_until='networkidle', timeout=45000)
            status = response.status if response else None
            final_url = page.url
            text = page.inner_text('body')
            if screenshot_dir is not None:
                screenshot_dir.mkdir(parents=True, exist_ok=True)
                screenshot_path = str(screenshot_dir / (url.replace('://', '_').replace('/', '_')[:120] + '.png'))
                page.screenshot(path=screenshot_path, full_page=True)
        finally:
            browser.close()

    lowered = text.lower()
    blocked = (status is not None and status != 200) or any(m in lowered for m in _LOGIN_MARKERS) or len(text) < 200
    return RenderResult(
        ok=not blocked,
        http_status=status,
        final_url=final_url,
        text=text,
        screenshot_path=screenshot_path,
        blocked=blocked,
    )


def _extract_prompt(rendered_text: str, items: Sequence[WorkItem]) -> str:
    names = '\n'.join(f'- {it.model_id} (expected unit class: {it.unit_class})' for it in items)
    return (
        'You are reading a provider pricing page to extract the current Pay-As-You-Go rate '
        'for specific speech models. Ignore monthly/subscription/credit/plan pricing entirely; '
        'only report the per-use rate (per minute, per second, per hour, or per character) in USD.\n\n'
        f'Models to find:\n{names}\n\n'
        'Return ONLY a JSON object mapping each model id to an object with keys: '
        'found (bool), rate_value (number or null), rate_unit (string, e.g. "per minute"), '
        'currency (string, e.g. "USD"), confidence ("high"|"medium"|"low"), '
        'evidence_quote (the exact text you read the rate from, copied verbatim), '
        'matched_row_name (the row/model label on the page). If a model is not on the page, '
        'set found=false.\n\n'
        f'PAGE TEXT:\n{rendered_text[:20000]}'
    )


def _parse_extractions(raw: str) -> dict[str, Extraction]:
    """Parse the model's JSON answer (the first {...} block) into Extractions."""
    start, end = raw.find('{'), raw.rfind('}')
    payload: dict[str, Any] = json.loads(raw[start : end + 1]) if start != -1 and end != -1 else {}

    out: dict[str, Extraction] = {}
    for model_id, v in payload.items():
        if not isinstance(v, dict):
            continue
        d = cast(dict[str, Any], v)
        out[model_id] = Extraction(
            found=bool(d.get('found')),
            rate_value=(float(d['rate_value']) if isinstance(d.get('rate_value'), int | float) else None),
            rate_unit=(str(d['rate_unit']) if d.get('rate_unit') is not None else None),
            currency=(str(d['currency']) if d.get('currency') is not None else None),
            confidence=str(d.get('confidence', 'low')),
            evidence_quote=str(d.get('evidence_quote', '')),
            matched_row_name=str(d.get('matched_row_name', '')),
        )
    return out


def _run_claude(prompt: str) -> str:
    """Run `claude -p` headlessly and return the model's text answer.

    Authenticates via the CLAUDE_CODE_OAUTH_TOKEN env var (a `claude setup-token`
    token tied to a Claude Pro/Max subscription), so no Anthropic API key is needed.
    Returns the `result` field of the `--output-format json` envelope.
    """
    exe = shutil.which('claude') or 'claude'
    proc = subprocess.run(
        [exe, '-p', prompt, '--output-format', 'json', '--model', _EXTRACT_MODEL],
        capture_output=True,
        text=True,
        timeout=180,
        check=True,
    )
    envelope = cast(dict[str, Any], json.loads(proc.stdout))
    return str(envelope.get('result', ''))


def default_extract(rendered_text: str, items: Sequence[WorkItem]) -> dict[str, Extraction]:
    """Extract each model's rate via Claude Code headless (`claude -p`)."""
    return _parse_extractions(_run_claude(_extract_prompt(rendered_text, items)))


def fetch_page(
    url: str,
    items: Sequence[WorkItem],
    *,
    render: RenderFn,
    extract: ExtractFn,
) -> tuple[RenderResult, dict[str, Extraction]]:
    """Render one URL and extract every requested model on it in a single pass."""
    rendered = render(url)
    if not rendered.ok:
        return rendered, {}
    return rendered, extract(rendered.text, items)
