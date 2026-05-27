# Contributing a new TTS provider

A walkthrough for adding a new text-to-speech provider to voice-prices. Worked end-to-end against a fictional provider, "Murf TTS", so the steps stay evergreen as real providers come and go.

## When to use this guide

Use it when you want to add a new TTS provider (one not currently in `prices/providers/`).

If you only need to add a model to a provider that already exists, edit that provider's existing YAML directly: the per-model steps below still apply but you can skip the provider-metadata steps.

If you're adding an STT, LLM, or realtime-audio provider, this guide does not yet cover those modalities. Open an issue first.

## Prerequisites

- `make install` runs cleanly (this brings up `uv`, the workspace, and pre-commit hooks).
- You can run `make build` and `make test` locally.
- You have access to the provider's public pricing page (or their API pricing docs).

## Step 1: Discuss first

Open an issue at https://github.com/mahimailabs/voice-prices/issues describing the provider you want to add: name, link to their pricing page, which models. This is the project convention from `prices/README.md` and lets us catch duplicates, naming conflicts, or scope concerns before you spend time on the YAML.

## Step 2: Find the rates

For Murf TTS, the rates live at `https://murf.ai/pricing` (hypothetical for this guide). You're looking for three things:

1. **Per-character rate** for each model the provider exposes. TTS billing is dominated by `$ per 1,000 characters of input text` (input_kchars).
2. **Voice-tier multipliers**, if any. Some providers (Cartesia, ElevenLabs) charge more for premium or custom-cloned voices.
3. **The exact source URL with a deep anchor**, ideally pointing at the row in the pricing table for each model. You'll record this as `pricing_source_url` on every model.

If the provider uses a non-character billing unit (credits, seconds, tokens), see the FAQ at the end before proceeding.

## Step 3: Copy the template

From the repo root:

```bash
cp docs/templates/provider-tts.yml prices/providers/murf.yml
```

Naming convention for the file: lowercase, hyphens (no spaces), matches the provider's common name. The filename's stem becomes the provider's directory key, but the actual `id` field inside the YAML is what consumers reference.

## Step 4: Fill in provider metadata

Open `prices/providers/murf.yml` and replace the top-level fields:

```yaml
name: Murf
id: murf
pricing_urls:
  - https://murf.ai/pricing
api_pattern: 'https://api\.murf\.ai'
model_match:
  starts_with: murf-
provider_match:
  contains: murf
```

The two match clauses are how `calc_price` routes a user's request to your provider:

- `model_match` fires when the user calls `calc_price(Usage(...), model_ref='murf-something')` without `provider_id`. Pick a prefix or regex that uniquely identifies your provider's models. If your model IDs share a prefix with another provider in the catalog, use `or` with explicit alternatives.
- `provider_match` fires when the user passes `provider_id='murf'` directly. Usually a `contains` clause on a slug.

Getting `model_match` right is critical: a missing prefix means consumers see `LookupError: Unable to find provider matching '<your-model>'` even though your YAML is in the catalog.

## Step 5: Add the models

Replace the example models block. For each model:

```yaml
models:
  - id: murf-standard
    name: Murf Standard
    description: >-
      Murf's standard TTS model. Good for most use cases.
    match:
      equals: murf-standard
    prices_checked: 2026-05-27
    pricing_source_url: https://murf.ai/pricing#murf-standard
    prices:
      input_kchars: 0.020
```

Key rules:

- **`models` list must be sorted alphabetically by `id`**. The Pydantic validator enforces this and points you at the wrong-position entry if you get it wrong.
- **`prices_checked`** is the date you verified the rate against `pricing_source_url`. Set it to today's date.
- **`pricing_source_url`** is required for every model with `input_kchars` set (the `test_tts_entries_have_provenance` test enforces this).

## Step 6: (Optional) Voice multipliers

Skip this step if your provider charges a uniform per-character rate across all voices. Most do.

If your provider has premium / custom-clone / professional voice tiers that bill at a different per-character rate, add `voice_multipliers` to the affected model:

```yaml
voice_multipliers:
  standard: 1.0
  premium: 1.5    # premium voices are 1.5x standard
  default: 1.0    # REQUIRED. Used when caller passes no voice_class
                  # or passes a voice_class that isn't in the dict above.
```

The `default` key is mandatory. Other keys are free-form strings (consumers must pass the matching string in `Usage(voice_class='premium')` to get the multiplier).

If you set `voice_multipliers` but no `input_kchars` (or `output_audio_kseconds`), validation fails: multipliers must have something to scale.

## Step 7: Build and verify

From the repo root:

```bash
make build
make test
```

`make build` regenerates `prices/data.json`, `prices/data_slim.json`, the JSON schema file, and the runtime data module. `make test` runs the full suite including the parametrized YAML round-trip and provenance check that auto-cover your new YAML.

Then a 3-line smoke check from a Python REPL:

```python
from voice_prices import Usage, calc_price
result = calc_price(Usage(characters=200), model_ref='murf-standard')
print(result.total_price, result.provider.id)
# expected: 0.004 murf      (200 chars at $0.020 / 1000 chars)
```

If you get a `LookupError`, your `model_match` clause doesn't cover the model ID you passed. Go back to Step 4.

## Step 8: Open the PR

```bash
git checkout -b feat/add-murf-provider
git add prices/providers/murf.yml prices/data.json prices/data_slim.json prices/data.schema.json prices/data_slim.schema.json prices/providers/.schema.json packages/python/voice_prices/data.py
git commit -m "feat: add Murf TTS provider"
git push -u origin feat/add-murf-provider
gh pr create --fill --base main
```

PR body should include:

- The pricing-page URL you used for verification.
- The date you verified the rates.
- Any quirks: voice-tier behavior, non-standard billing units, deprecated models you intentionally excluded.

CI runs `pre-commit`, the Python matrix tests, coverage, and the consolidated `check` job. When green, merge to main. The new provider is live in the next release.

## FAQ / Troubleshooting

### `LookupError: Unable to find provider matching '<model-ref>'`

Your `model_match` clause doesn't cover the model ID the user passed. Update the prefix or regex in the provider-level `model_match` block (Step 4) and rebuild.

### Pydantic error citing `voice_multipliers must include a 'default' key`

You added `voice_multipliers` without a `default` entry. Add `default: 1.0` (or whatever your standard rate is, expressed as a multiplier of `input_kchars`).

### Pydantic error citing `voice_multipliers requires at least one scalable priced field`

You added `voice_multipliers` to a model that has no `input_kchars` or `output_audio_kseconds`. Multipliers only scale character and audio-second priced fields. Either add `input_kchars` to the model or remove `voice_multipliers`.

### Pydantic error mentioning `TieredPrices` and `input_kchars`

Tiered character pricing isn't supported in v0.1. If your provider tiers per-character pricing by volume, document the rate at the default tier and note the tiering in `price_comments` until tiered character pricing is added.

### "Models are not sorted by ID"

The validator enforces alphabetical sorting of the `models:` list by the `id:` field. Reorder the entries and try again. The error message tells you exactly which entry to move and where.

### My provider bills in credits / tokens / minutes, not characters

Pick a default subscription tier (usually the "pay as you go" or "Creator" tier) and convert the unit cost to `$ per 1,000 characters` using the documented credit-to-character ratio. Document the chosen tier and conversion formula in `price_comments` on each model so consumers on other tiers can derive their own rate.

For example, ElevenLabs Creator tier costs `$22/month for 100,000 credits` (`$0.00022 per credit`). Turbo v2.5 burns `0.5 credits per character`, so its `input_kchars` value is `$0.00022 * 0.5 * 1000 = $0.11`. The `price_comments` block on the entry calls this out explicitly.
