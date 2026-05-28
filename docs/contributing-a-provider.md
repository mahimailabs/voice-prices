# Contributing a new voice provider (TTS or STT)

A walkthrough for adding a new voice provider to voice-prices. It covers both text-to-speech (TTS) and speech-to-text (STT). The shared steps are written once; only the model-pricing step (Step 5) forks by modality. It is worked end-to-end against two fictional providers, "Murf TTS" and "Acme Speech STT", so the steps stay evergreen as real providers come and go.

## When to use this guide

Use it when you want to add a new TTS or STT provider (one not currently in `prices/providers/`).

If you only need to add a model to a provider that already exists, edit that provider's existing YAML directly: the per-model steps below still apply but you can skip the provider-metadata steps.

If you're adding an LLM or realtime-audio provider, this guide does not yet cover those modalities. Open an issue first.

The two voice modalities differ only in which priced field and which `Usage` field they use:

| Modality | Priced field | Usage field | Template |
| --- | --- | --- | --- |
| TTS | `input_kchars` ($ per 1k characters) | `Usage(characters=...)` | `provider-tts.yml` |
| STT | `input_audio_kseconds` ($ per 1k seconds) | `Usage(audio_input_seconds=Decimal(...))` | `provider-stt.yml` |

The table shows the dominant unit per modality. Other priced fields exist (for example `output_audio_kseconds` for TTS models that bill speech output); price every unit your provider actually charges for.

## Prerequisites

- `make install` runs cleanly (this brings up `uv`, the workspace, and pre-commit hooks).
- You can run `make build` and `make test` locally.
- You have access to the provider's public pricing page (or their API pricing docs).

## Step 1: Discuss first

Open an issue at https://github.com/mahimailabs/voice-prices/issues describing the provider you want to add: name, link to their pricing page, which models, and which modality (TTS or STT). This is the project convention from `prices/README.md` and lets us catch duplicates, naming conflicts, or scope concerns before you spend time on the YAML.

## Step 2: Find the rates

You're looking for three things:

1. **The per-unit rate** for each model the provider exposes.
   - TTS billing is dominated by `$ per 1,000 characters of input text` (`input_kchars`).
   - STT billing is dominated by `$ per 1,000 seconds of input audio` (`input_audio_kseconds`). Most STT providers quote `$ per minute`: convert with `rate_per_min * 1000 / 60` and record the source rate in `price_comments`.
2. **Tier or mode differences**, if any. Some TTS providers (Cartesia, ElevenLabs) charge more for premium or custom-cloned voices. Many STT providers charge different rates for streaming (real-time) versus batch (prerecorded), and for monolingual versus multilingual transcription.
3. **The exact source URL with a deep anchor**, ideally pointing at the row in the pricing table for each model. You'll record this as `pricing_source_url` on every model.

If the provider uses a non-standard billing unit (credits, tokens), see the FAQ at the end before proceeding.

## Step 3: Copy the template

From the repo root, copy the template for your modality:

```bash
# TTS provider
cp docs/templates/provider-tts.yml prices/providers/<your-provider>.yml

# STT provider
cp docs/templates/provider-stt.yml prices/providers/<your-provider>.yml
```

Naming convention for the file: lowercase, hyphens (no spaces), matches the provider's common name. The filename's stem becomes the provider's directory key, but the actual `id` field inside the YAML is what consumers reference.

## Step 4: Fill in provider metadata

The provider metadata is identical across modalities. Open your new file and replace the top-level fields. Using Murf as the running TTS example:

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

An STT provider fills in exactly the same fields (we use Acme Speech, `id: acme`, `model_match: starts_with: acme-`, starting in Step 5b).

The two match clauses are how `calc_price` routes a user's request to your provider:

- `model_match` fires when the user calls `calc_price(Usage(...), model_ref='murf-something')` without `provider_id`. Pick a prefix or regex that uniquely identifies your provider's models. If your model IDs share a prefix with another provider in the catalog, use `or` with explicit alternatives.
- `provider_match` fires when the user passes `provider_id='murf'` directly. Usually a `contains` clause on a slug.

Getting `model_match` right is critical: a missing prefix means consumers see `LookupError: Unable to find provider matching '<your-model>'` even though your YAML is in the catalog.

## Step 5: Add the models

This is the only step that differs by modality. Follow Step 5a for TTS or Step 5b for STT.

In both cases:

- **The `models` list must be sorted alphabetically by `id`**. The Pydantic validator enforces this and points you at the wrong-position entry if you get it wrong.
- **`prices_checked`** is the date you verified the rate against `pricing_source_url`. Set it to today's date.
- **`pricing_source_url`** is required for every model with a voice priced field set (`input_kchars`, `input_audio_kseconds`, or `output_audio_kseconds`). The `test_audio_priced_entries_have_provenance` test enforces this.

### Step 5a: TTS models

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

### Step 5b: STT models

STT models price per second of input audio via `input_audio_kseconds`. For the worked example we use Acme Speech (`id: acme`), whose provider metadata follows the same Step 4 pattern (`model_match: starts_with: acme-`).

The most error-prone part of STT is the **streaming versus batch mode split**. When a provider charges different rates for real-time (streaming) and prerecorded (batch) transcription of the same model, ship them as separate entries:

```yaml
models:
  - id: acme-1
    name: Acme Speech 1 (streaming)
    description: >-
      Acme's real-time streaming STT model. Use acme-1 for streaming,
      acme-1-batch for prerecorded audio.
    match:
      equals: acme-1
    prices_checked: 2026-05-28
    pricing_source_url: https://acme.example/pricing#acme-1
    price_comments: >-
      Source rate $0.006/minute. Converted to $/k seconds: 0.006 * 1000 / 60 = 0.1 exactly.
    prices:
      input_audio_kseconds: 0.1

  - id: acme-1-batch
    name: Acme Speech 1 (batch)
    description: >-
      Acme's prerecorded / batch STT model. Higher per-minute than streaming.
    match:
      equals: acme-1-batch
    prices_checked: 2026-05-28
    pricing_source_url: https://acme.example/pricing#acme-1-batch
    price_comments: >-
      Source rate $0.0042/minute. Converted to $/k seconds: 0.0042 * 1000 / 60 = 0.07 exactly.
    prices:
      input_audio_kseconds: 0.07
```

STT mode-split rules:

- Name the more common mode bare (`acme-1`) and the less common one with a `-<mode>` suffix (`acme-1-batch`). If both modes are equally common, ship both names explicitly and let a bare `<model>` resolve to nothing (`LookupError`, never a silent wrong-row pick).
- **Always use `match: equals: <id>`** for both entries, never `starts_with`. The `equals` matcher guarantees one-to-one resolution: a `model_ref` either hits exactly one catalog entry or fails loudly. `starts_with` makes silent wrong-row picks possible the moment a sibling entry shares a prefix.
- **Language tiers ship as separate model entries.** If your provider charges different rates for monolingual versus multilingual transcription, add distinct IDs (Deepgram does this with `nova-3` and `nova-3-multilingual`). Do not try to use `voice_multipliers`: they are not supported for STT (see the FAQ).
- `audio_input_seconds` is `Decimal`-typed so callers can express sub-second precision. Deepgram and AssemblyAI bill in 0.01s increments. Pass via `Decimal('12.34')` or convert from float at the call site.

## Step 6: (TTS only) Voice multipliers

Skip this step entirely if you're adding an STT provider: `voice_multipliers` are not supported for STT. Skip it for TTS too if your provider charges a uniform per-character rate across all voices. Most do.

If your TTS provider has premium / custom-clone / professional voice tiers that bill at a different per-character rate, add `voice_multipliers` to the affected model:

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

Then a smoke check from a Python REPL, for your modality. The examples below use the fictional Murf and Acme models; substitute your own `model_ref`. Routing by `model_ref` works once your YAML is in `prices/providers/` and `make build` has regenerated the runtime data:

```python
# TTS
from voice_prices import Usage, calc_price
result = calc_price(Usage(characters=200), model_ref='murf-standard')
print(result.total_price, result.provider.id)
# expected: 0.004 murf      (200 chars at $0.020 / 1000 chars)
```

```python
# STT
from decimal import Decimal
from voice_prices import Usage, calc_price
result = calc_price(Usage(audio_input_seconds=Decimal('60')), model_ref='acme-1')
print(result.total_price, result.provider.id)
# expected: 0.006 acme      (60s at $0.1 / 1000s)
```

If you get a `LookupError`, your `model_match` clause doesn't cover the model ID you passed. Go back to Step 4.

## Step 8: Open the PR

```bash
git checkout -b feat/add-<your-provider>-provider
git add prices/providers/<your-provider>.yml prices/data.json prices/data_slim.json prices/data.schema.json prices/data_slim.schema.json prices/providers/.schema.json packages/python/voice_prices/data.py
git commit -m "feat: add <your-provider> provider"
git push -u origin feat/add-<your-provider>-provider
gh pr create --base main
```

PR body should include:

- The pricing-page URL you used for verification.
- The date you verified the rates.
- Any quirks: voice-tier behavior (TTS), streaming/batch or language-tier splits (STT), non-standard billing units, deprecated models you intentionally excluded.

CI runs `pre-commit`, the Python matrix tests, coverage, and the consolidated `check` job. When green, merge to main. The new provider is live in the next release.

## FAQ / Troubleshooting

Each entry notes the modality it applies to.

### (Both) `LookupError: Unable to find provider matching '<model-ref>'`

Your `model_match` clause doesn't cover the model ID the user passed. Update the prefix or regex in the provider-level `model_match` block (Step 4) and rebuild.

### (TTS only) Pydantic error citing `voice_multipliers must include a 'default' key`

You added `voice_multipliers` without a `default` entry. Add `default: 1.0` (or whatever your standard rate is, expressed as a multiplier of `input_kchars`). STT contributors do not hit this error, because `voice_multipliers` are rejected outright for STT models (see the STT entry below).

### (TTS only) Pydantic error citing `voice_multipliers requires at least one scalable priced field`

You added `voice_multipliers` to a model that has no `input_kchars` or `output_audio_kseconds`. Multipliers only scale character and audio-second output priced fields. Either add `input_kchars` to the model or remove `voice_multipliers`.

### (STT only) `voice_multipliers` are not supported for STT

Setting `voice_multipliers` on a model whose only priced field is `input_audio_kseconds` fails Pydantic validation by design: language-tier multipliers for STT were deferred. If your provider charges different rates by language (for example English-only versus multilingual), ship them as separate model entries with distinct IDs, the way Deepgram does with `nova-3` and `nova-3-multilingual`.

### (TTS only) Pydantic error mentioning `TieredPrices` and `input_kchars`

Tiered character pricing isn't supported in v0.1. If your provider tiers per-character pricing by volume, document the rate at the default tier and note the tiering in `price_comments` until tiered character pricing is added.

### (Both) "Models are not sorted by ID"

The validator enforces alphabetical sorting of the `models:` list by the `id:` field. Reorder the entries and try again. The error message tells you exactly which entry to move and where.

### (Both) My provider bills in credits / tokens / minutes, not the native unit

Pick a default subscription tier (usually the "pay as you go" or "Creator" tier) and convert the unit cost to the native priced field. Document the chosen tier and conversion formula in `price_comments` on each model so consumers on other tiers can derive their own rate.

- TTS example: ElevenLabs Creator tier costs `$22/month for 100,000 credits` (`$0.00022 per credit`). Turbo v2.5 burns `0.5 credits per character`, so its `input_kchars` value is `$0.00022 * 0.5 * 1000 = $0.11`. The `price_comments` block on the entry calls this out explicitly.
- STT example: a provider quoting `$0.006 per minute` of audio converts to `input_audio_kseconds = 0.006 * 1000 / 60 = 0.1`. Record the source per-minute rate in `price_comments`.

### (STT only) Streaming versus batch / realtime versus async / sync versus queued

If your provider charges different rates for two modes of the same model, name the more common mode bare (`<model>`) and the less common one with a `-<mode>` suffix (`<model>-<mode>`). Example: Deepgram `nova-3` (streaming default) and `nova-3-batch`. Always use `match: equals: <id>` for both entries so a `model_ref` resolves to exactly one row or fails loudly.

### (STT only) Sub-second precision

`audio_input_seconds` and `audio_output_seconds` are `Decimal`-typed so callers can express sub-second precision. Pass via `Decimal('12.34')` or convert from float at the call site.
