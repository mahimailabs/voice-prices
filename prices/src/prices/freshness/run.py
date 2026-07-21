"""Orchestrate the freshness check and the CLI entry point.

`run_freshness_check` is the testable core (inject `render`/`extract`/`items`).
`freshness_check` is the no-arg CLI action registered in prices.__main__; it wires
the real Playwright/Anthropic defaults and writes the workflow's output files.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from prices.update import ProviderYaml, get_providers_yaml

from .diff import classify
from .fetch import ExtractFn, RenderFn, default_extract, default_render, default_verify, fetch_page
from .models import Extraction, Finding, RenderResult, WorkItem
from .report import Report, apply_edits, build_report
from .select import select_stale


def run_freshness_check(
    today: date,
    items: Sequence[WorkItem],
    *,
    render: RenderFn,
    extract: ExtractFn,
    verify: ExtractFn | None = None,
    apply_providers: dict[str, ProviderYaml] | None = None,
) -> Report:
    """Fetch + diff every item, build the report, and optionally apply DRIFT edits.

    When `verify` is given, a second independent agent reads the same page and a MATCH/DRIFT is only
    proposed on consensus. When it is None, this is exactly the single-agent pipeline (no behavior change).
    """
    by_url: dict[str, list[WorkItem]] = defaultdict(list)
    for it in items:
        by_url[it.url].append(it)

    findings: list[Finding] = []
    for url, group in by_url.items():
        rendered, extractions = fetch_page(url, group, render=render, extract=extract)
        verifier_extractions: dict[str, Extraction] = {}
        if verify is not None and rendered.ok:
            verifier_extractions = verify(rendered.text, group)
        for it in group:
            findings.append(
                classify(
                    it,
                    rendered,
                    extractions.get(it.model_id),
                    verifier_extractions.get(it.model_id),
                    require_verifier=verify is not None,
                )
            )

    report = build_report(findings, today)
    if report.drift_edits and apply_providers is not None:
        apply_edits(report, apply_providers)
    return report


def freshness_check() -> int:
    """CLI action: run the real check and write summary.json + pr-body.md."""
    today = date.today()
    run_all = os.environ.get('FRESHNESS_ALL') == '1'
    output_dir = Path(os.environ.get('FRESHNESS_OUTPUT_DIR', 'freshness-output'))
    output_dir.mkdir(parents=True, exist_ok=True)

    items = select_stale(today, all=run_all)
    if not items:
        print('no voice models are due for a freshness check')
        (output_dir / 'summary.json').write_text(json.dumps({'actionable': False, 'counts': {}}))
        return 0

    def render(url: str) -> RenderResult:
        return default_render(url, screenshot_dir=output_dir / 'screenshots')

    # Opt-in second agent: enabled only when a verifier provider is configured, so the default run
    # stays single-agent (and free of a second key/cost) unless the workflow asks for consensus.
    verify = default_verify if os.environ.get('FRESHNESS_VERIFIER_PROVIDER') else None

    report = run_freshness_check(
        today,
        items,
        render=render,
        extract=default_extract,
        verify=verify,
        apply_providers=get_providers_yaml(),
    )

    (output_dir / 'pr-body.md').write_text(report.pr_body)
    (output_dir / 'summary.json').write_text(
        json.dumps({'actionable': report.actionable, 'counts': {c.value: n for c, n in report.counts.items()}})
    )
    print(
        f'freshness check complete: actionable={report.actionable} counts={ {c.value: n for c, n in report.counts.items()} }'
    )
    return 0
