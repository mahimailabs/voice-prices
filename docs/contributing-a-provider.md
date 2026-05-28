# Contributing a new provider (LLM, TTS, or STT)

A walkthrough for adding a new provider to voice-prices. It covers large language models (LLM), text-to-speech (TTS), and speech-to-text (STT). The shared steps are written once; only the model-pricing step (Step 5) forks by modality. It is worked end-to-end against three fictional providers, "Nimbus AI LLM", "Murf TTS", and "Acme Speech STT", so the steps stay evergreen as real providers come and go.

## When to use this guide

Use it when you want to add a new LLM, TTS, or STT provider (one not currently in `prices/providers/`).

If you only need to add a model to a provider that already exists, edit that provider's existing YAML directly: the per-model steps below still apply but you can skip the provider-metadata steps.

If you're adding a realtime-audio provider, open an issue first. Realtime audio is token-priced via `input_audio_mtok` / `output_audio_mtok` and those fields are documented in Advanced LLM pricing below, but implementing a realtime-audio provider (bidirectional streaming) needs engineering beyond YAML and is out of scope for this guide.

The modalities differ mainly in which priced field and which `Usage` field they use:

| Modality | Priced field | Usage field | Template |
| --- | --- | --- | --- |
| LLM | `input_mtok` / `output_mtok` ($ per 1M tokens), plus optional cache / audio / request fields | `Usage(input_tokens=..., output_tokens=...)` | `provider-llm.yml` |
| TTS | `input_kchars` ($ per 1k characters) | `Usage(characters=...)` | `provider-tts.yml` |
| STT | `input_audio_kseconds` ($ per 1k seconds) | `Usage(audio_input_seconds=Decimal(...))` | `provider-stt.yml` |

The table shows the dominant unit per modality. Other priced fields exist (for example `output_audio_kseconds` for TTS models that bill speech output, or `cache_read_mtok` / `cache_write_mtok` / audio-token / `requests_kcount` fields for LLM); price every unit your provider actually charges for. LLM advanced pricing (cache, tiers, daily rates, audio tokens) is documented in Advanced LLM pricing below.

## Prerequisites

- `make install` runs cleanly (this brings up `uv`, the workspace, and pre-commit hooks).
- You can run `make build` and `make test` locally.
- You have access to the provider's public pricing page (or their API pricing docs).

## Step 1: Discuss first

Open an issue at https://github.com/mahimailabs/voice-prices/issues describing the provider you want to add: name, link to their pricing page, which models, and which modality (TTS or STT). This is the project convention from `prices/README.md` and lets us catch duplicates, naming conflicts, or scope concerns before you spend time on the YAML.

## Step 2: Find the rates

You're looking for three things:

1. **The per-unit rate** for each model the provider exposes.
   - LLM billing is dominated by `$ per 1,000,000 tokens` (`input_mtok` for input/prompt tokens, `output_mtok` for output/completion tokens). Watch for separate cached-token rates (`cache_read_mtok`), tiered rates by context size, and time-of-day (off-peak) rates.
   - TTS billing is dominated by `$ per 1,000 characters of input text` (`input_kchars`).
   - STT billing is dominated by `$ per 1,000 seconds of input audio` (`input_audio_kseconds`). Most STT providers quote `$ per minute`: convert with `rate_per_min * 1000 / 60` and record the source rate in `price_comments`.
2. **Tier or mode differences**, if any. Some TTS providers (Cartesia, ElevenLabs) charge more for premium or custom-cloned voices. Many STT providers charge different rates for streaming (real-time) versus batch (prerecorded), and for monolingual versus multilingual transcription. Many LLM providers tier by context window, discount cached tokens, or vary rates by time of day.
3. **The exact source URL with a deep anchor**, ideally pointing at the row in the pricing table for each model. You'll record this as `pricing_source_url` on every model.

If the provider uses a non-standard billing unit (credits, tokens), see the FAQ at the end before proceeding.

## Step 3: Copy the template

From the repo root, copy the template for your modality:

```bash
# LLM provider
cp docs/templates/provider-llm.yml prices/providers/<your-provider>.yml

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

### Step 5c: LLM models

LLM models price per million tokens. For the worked example we use Nimbus AI (`id: nimbus`), an OpenAI-compatible provider, so the standard `extractors` block in `provider-llm.yml` works unchanged.

```yaml
models:
  - id: nimbus-large
    name: Nimbus Large
    description: >-
      Nimbus flagship model. OpenAI-compatible chat API, so the template's
      extractors block works unchanged.
    match:
      equals: nimbus-large
    context_window: 128000
    prices_checked: 2026-05-28
    pricing_source_url: https://nimbus.example/pricing#nimbus-large
    prices:
      input_mtok: 2.0
      output_mtok: 8.0
```

LLM specifics:

- **`input_mtok` / `output_mtok`** are `$ per 1,000,000 tokens` (input/prompt and output/completion respectively). These are the dominant LLM fields.
- **`context_window`** is the maximum input tokens the model accepts.
- **`match`**: prefer `equals` for one-to-one resolution. Use `or` with explicit clauses when a model has several aliases (see how `anthropic.yml` and `deepseek.yml` match their families). If your model IDs share a prefix with another provider in the catalog, disambiguate with `equals` or a `regex` rather than a broad `starts_with`.
- **Prompt caching**: add `cache_read_mtok` (and `cache_write_mtok` if the provider charges to write the cache) for the discounted cached-token rate. The template's second model shows `cache_read_mtok`.
- **Usage extractors**: the template ships a standard OpenAI-compatible `extractors` block that maps `prompt_tokens` / `completion_tokens` / cached tokens onto the canonical fields. Keep it as-is for OpenAI-compatible APIs. If your provider returns a different response shape, adjust it following "Advanced LLM pricing > Usage extractors" below, so the extractor never silently fails to populate usage.
- **Advanced pricing** (tiered, daily / off-peak, audio tokens) is covered in Advanced LLM pricing below.

## Step 6: (TTS only) Voice multipliers

Skip this step entirely if you're adding an STT or LLM provider: `voice_multipliers` are not supported for those modalities (they only scale TTS character and audio-second fields). Skip it for TTS too if your provider charges a uniform per-character rate across all voices. Most do.

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

Then a smoke check from a Python REPL, for your modality. The examples below use the fictional Nimbus, Murf, and Acme models; substitute your own `model_ref`. Routing by `model_ref` works once your YAML is in `prices/providers/` and `make build` has regenerated the runtime data:

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

```python
# LLM
from voice_prices import Usage, calc_price
result = calc_price(Usage(input_tokens=1000, output_tokens=1000), model_ref='nimbus-large')
print(result.total_price, result.provider.id)
# expected: 0.010 nimbus     (1000 in at $2/Mtok + 1000 out at $8/Mtok)
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

## Advanced LLM pricing

LLM providers often price more than flat input/output tokens. Each feature below shows the YAML shape and points at a real, tested provider for a complete example. You do not need any of these for a simple flat-rate provider.

### Usage extractors

`extractors` map a provider's raw API usage JSON onto the canonical token fields the engine prices. The template ships a standard OpenAI-compatible block; keep it if your provider's chat-completions response looks like OpenAI's. Each mapping is a `path` (a key, or a list of keys for a nested value) and a `dest` (a canonical field); set `required: false` for fields that may be absent.

```yaml
extractors:
  - api_flavor: chat              # only needed if a provider exposes several flavors
    root: usage                   # where the usage object lives in the response
    mappings:
      - path: prompt_tokens
        dest: input_tokens
      - path: [prompt_tokens_details, cached_tokens]   # nested path
        dest: cache_read_tokens
        required: false
      - path: completion_tokens
        dest: output_tokens
```

If your provider returns extra buckets or uses non-OpenAI field names, add or rename mappings. See `anthropic.yml`, which maps the native `cache_creation_input_tokens` onto `cache_write_tokens` and `cache_read_input_tokens` onto `cache_read_tokens`, and defines both a native flavor and a `chat` flavor.

### Tiered pricing

When a rate changes above a token threshold, a priced field becomes a `{base, tiers}` object instead of a scalar. Tiers must be listed in ascending order by `start`. This is a cliff model: crossing a tier applies that rate to all tokens of that type, not just the tokens above the threshold.

```yaml
prices:
  input_mtok:
    base: 1.25                    # rate up to the first tier
    tiers:
      - start: 200000             # at/above 200k tokens, this rate applies to all input tokens
        price: 2.50
```

See `google.yml` (Gemini context-window tiers).

### Conditional / daily (off-peak) pricing

When a provider charges different rates by time of day, `prices` becomes a list of `{constraint, prices}` blocks. The first block has no `constraint` and is the always-on fallback; later blocks whose constraint matches take precedence. Times must be timezone-aware.

```yaml
prices:
  - prices:                       # fallback, used when no constraint matches
      input_mtok: 0.135
      output_mtok: 0.550
  - constraint:
      start_time: 00:30:00Z       # daily window, UTC
      end_time: 16:30:00Z
    prices:
      input_mtok: 0.27
      output_mtok: 1.1
```

See `deepseek.yml` (off-peak pricing).

### Cache-write pricing

Some providers charge separately to write the prompt cache. Add `cache_write_mtok` alongside `cache_read_mtok`, and make sure your extractor populates `cache_write_tokens`.

```yaml
prices:
  input_mtok: 3.0
  cache_write_mtok: 3.75
  cache_read_mtok: 0.30
  output_mtok: 15.0
```

See `anthropic.yml`.

### Audio token pricing (OpenAI realtime example)

Realtime and multimodal models price audio as tokens: `input_audio_mtok`, `output_audio_mtok`, and `cache_audio_read_mtok` (all `$ per 1M tokens`).

```yaml
prices:
  input_mtok: 5.0
  output_mtok: 20.0
  input_audio_mtok: 40.0
  output_audio_mtok: 80.0
```

See `openai.yml`. This documents the token-field structure only; building a realtime-audio provider (bidirectional streaming) needs engineering beyond YAML and is deferred.

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

### (LLM) Costs come out zero / usage is not extracted

Your `extractors` block does not match the provider's API response shape, so no tokens are populated. Check `root` (where the usage object lives), each `path`, and each `dest`. Most OpenAI-compatible providers reuse the standard `chat` extractor in the template unchanged.

### (LLM) Where do cache prices go

Use `cache_read_mtok` for prompt-cache hits and `cache_write_mtok` for cache writes. Both need a matching extractor mapping so the cached / written token counts are populated (see `anthropic.yml`).

### (LLM) Daily / off-peak pricing

Use a `prices` list instead of a single `prices` block. The first block (no `constraint`) is the always-on fallback; later time-constrained blocks take precedence when their window matches. Times must be timezone-aware (see `deepseek.yml`).

### (LLM) `ValueError: Tiers must be in ascending order by start`

Reorder the `tiers` list so `start` increases. See Advanced LLM pricing > Tiered pricing.

### (LLM) `ValueError: Times must be timezone aware`

Add a timezone to `start_time` / `end_time` in a daily-pricing `constraint`, for example `00:30:00Z`.
