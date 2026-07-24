"""Render a pricing page and extract the rate for each model on it.

The browser (Playwright) and the LLM are injected via the `render` and `extract`
callables, so the pipeline is fully testable with fakes and never calls a network
service in tests. The default extractor uses the OpenAI API (a small chat model)
and authenticates with an OPENAI_API_KEY; the model is overridable via the
FRESHNESS_OPENAI_MODEL env var. The default implementations run only in the
manually-triggered workflow.

Models that share a URL are extracted in a single pass so the LLM disambiguates
rows against each other rather than hunting for one in isolation.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast

from .models import Extraction, RenderResult, WorkItem

RenderFn = Callable[[str], RenderResult]
ExtractFn = Callable[[str, Sequence[WorkItem]], dict[str, Extraction]]

_LOGIN_MARKERS = ('log in', 'sign in', 'create account', 'access denied', 'just a moment')
_DEFAULT_OPENAI_MODEL = 'gpt-4o-mini'


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


def _run_openai(prompt: str) -> str:
    """Call the OpenAI chat API and return the model's text answer.

    Authenticates via OPENAI_API_KEY. The model is FRESHNESS_OPENAI_MODEL or a
    small default; JSON output is requested so the answer is a single object.
    """
    from openai import OpenAI

    client = OpenAI()
    completion = client.chat.completions.create(
        model=os.environ.get('FRESHNESS_OPENAI_MODEL', _DEFAULT_OPENAI_MODEL),
        messages=[{'role': 'user', 'content': prompt}],
        response_format={'type': 'json_object'},
        temperature=0,
    )
    return completion.choices[0].message.content or ''


def default_extract(rendered_text: str, items: Sequence[WorkItem]) -> dict[str, Extraction]:
    """Extract each model's rate via the OpenAI API."""
    return _parse_extractions(_run_openai(_extract_prompt(rendered_text, items)))


def _verify_prompt(rendered_text: str, items: Sequence[WorkItem]) -> str:
    """A deliberately different prompt from the extractor, so the two agents fail differently.

    Decorrelation (Codex #3): framed as an independent auditor that must locate each exact model row
    and bind the rate to that row and its unit, rather than scanning for any per-use number. Same JSON
    shape as the extractor so the same parser handles both.
    """
    names = '\n'.join(f'- {it.model_id} (expected unit class: {it.unit_class})' for it in items)
    return (
        'You are an independent pricing auditor double-checking specific speech models. For EACH model '
        'below, find the exact row that names that model on the page, and read its Pay-As-You-Go rate '
        'ONLY from that row. Do not report a number from a different tier, region, plan, or model. '
        'Ignore monthly/subscription/credit pricing. Report the rate in USD per use '
        '(per minute, per second, per hour, or per character).\n\n'
        f'Models to confirm:\n{names}\n\n'
        'Return ONLY a JSON object mapping each model id to an object with keys: found (bool), '
        'rate_value (number or null), rate_unit (string), currency (string), '
        'confidence ("high"|"medium"|"low"), evidence_quote (the exact row text you read the rate from, '
        'copied verbatim), matched_row_name (the row/model label). If you cannot find the model or bind '
        'the rate to its row, set found=false.\n\n'
        f'PAGE TEXT:\n{rendered_text[:20000]}'
    )


def _run_anthropic(prompt: str) -> str:  # pragma: no cover - network I/O
    """Call the Anthropic API and return the model's text answer.

    Imported dynamically (behind an Any boundary) so anthropic stays an optional, untyped extra
    rather than a hard dependency of the package.
    """
    import importlib

    anthropic = cast(Any, importlib.import_module('anthropic'))
    client = anthropic.Anthropic()
    message = client.messages.create(
        model=os.environ.get('FRESHNESS_VERIFIER_MODEL', 'claude-haiku-4-5'),
        max_tokens=1024,
        messages=[{'role': 'user', 'content': prompt}],
    )
    blocks = cast('list[Any]', message.content)
    return ''.join(str(block.text) for block in blocks if getattr(block, 'type', None) == 'text')


#: Providers the second verifier agent supports; run.py validates the env value against this
#: up front so an unsupported value never enables verify and then crashes mid-run.
SUPPORTED_VERIFIER_PROVIDERS = ('anthropic', 'openai')


def default_verify(
    rendered_text: str, items: Sequence[WorkItem]
) -> dict[str, Extraction]:  # pragma: no cover - network
    """Second-agent extraction. Provider is FRESHNESS_VERIFIER_PROVIDER (default a different vendor).

    A different provider gives real independence from the primary OpenAI extractor. 'openai' is
    supported for setups with only one key (decorrelation then rests on the distinct prompt + model).
    """
    provider = os.environ.get('FRESHNESS_VERIFIER_PROVIDER', 'anthropic')
    prompt = _verify_prompt(rendered_text, items)
    if provider == 'anthropic':
        raw = _run_anthropic(prompt)
    elif provider == 'openai':
        raw = _run_openai(prompt)
    else:
        raise ValueError(f'unsupported FRESHNESS_VERIFIER_PROVIDER {provider!r} (use anthropic or openai)')
    return _parse_extractions(raw)


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
