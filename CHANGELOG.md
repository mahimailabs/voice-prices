# Changelog

What changed between releases, with **behaviour changes called out separately from additions**.

That split is the point of this file. Most releases only add providers and models, which cannot
surprise you. Occasionally a release changes what an existing `model_ref` *answers*, and on the
`>=0.x,<1` pin most consumers carry, that arrives silently. Those go under **Behaviour changes**
so they can be audited rather than discovered in a bill.

Rate corrections are listed too. A repriced model is not a behaviour change in the API sense,
but it is one for anything that budgeted against the old number.

The auto-generated release notes on each GitHub release list every merged pull request. This
file only carries what a consumer needs to act on.

## Unreleased

### Behaviour changes

- **`ModelInfo.description` is now `None` on every LLM row.** 175 descriptions across 14 providers
  were OpenRouter's paraphrase of the vendor's marketing copy, sometimes verbatim ("Our most
  capable model...", in the vendor's own first person), written into each provider's YAML by the
  OpenRouter importer. A pricing catalog does not need a blurb, and third-party prose was the one
  thing in this repository that was expression rather than fact. The importer no longer writes
  descriptions on the per-vendor path. If you displayed `description`, expect `None` for LLMs.

  Voice rows keep theirs: those are written here and say operational things, such as which id to
  use for batch. Six descriptions on unpriced rows (retired models, free endpoints) stay for the
  same reason.

### Fixed rates

- Nothing repriced in this release.

### Added

- **Providers: Recall.ai** (`recall-transcription`, built-in transcription for the Meeting Bot API
  and Desktop Recording SDK) and **Gladia** (`solaria-1` real-time, `solaria-1-batch` async).
  Recall's separate $0.50/hour recording charge is deliberately excluded: it is not part of the
  transcription meter, and folding it in would overstate transcription by more than 4x.
- **xAI Voice API**: `grok-stt`, `grok-stt-batch`, `grok-tts`, and `grok-voice-think-fast-2.0`.
  The last is speech-to-speech billed per minute of audio, so it carries `agent_kminutes` the way
  Ultravox does rather than the per-audio-token fields the other speech-to-speech rows use. Note
  that xAI's models page advertises this row as "Starting at $0.05 / min"; that figure is
  `grok-voice-think-fast-1.0`, which its pricing page marks deprecated. The live model is $0.08.
- **Sarvam AI is recorded as unpriceable, not unexamined.** It publishes a complete public rate
  card entirely in INR, and this catalog is USD-only and does not convert, so the rows cannot be
  carried. The plugin registry gains a `non_usd_currency` status for exactly this case, because
  "no public rate" would be a false statement about a vendor that publishes one clearly.
- A correction and removal path for vendors: every page that shows a rate, both READMEs and the
  docs landing page link to a form, with a commitment to correct or remove a row within 24 hours.
- The freshness browser and every API importer now identify themselves with one `User-Agent`
  naming this repository and where to reach it, and the browser honours `robots.txt` before
  loading a vendor page. A disallowed URL is reported as a deliberate skip, not a broken link.

### Fixed

- **The Telnyx importer was completely broken and is now working again.** Telnyx moved its
  inference rates from flat `input_rate` / `output_rate` fields to a nested
  `rates.values.{input,cached_input,output}` shape, and every row had been failing validation.
  The guard that refuses a model whose tier rates disagree with each other is preserved, so a
  model that prices `$0.00` at the top level and higher in a tier is still refused rather than
  published as free. Thanks to @mikemikimike.

## 0.9.1

### Fixed rates

- **Cartesia `sonic-3` was 25% under the real price: $0.04 -> $0.05 per 1,000 characters.**
  Cartesia bills in credits, and this catalog converts using the Pro plan's credit price. Cartesia
  raised every plan between 2026-05-27 and 2026-08-21 (Pro $4 -> $5, Startup $39 -> $49, Scale
  $239 -> $299) while leaving the included credit counts unchanged. The credit cost of a character
  never moved; the plan price did. **If you budgeted against the old number, re-check it.**

  The vendor also relabelled the model Sonic-3.6. The id stays `sonic-3`, since `starts_with:
  sonic` matches either and renaming an id over a point release would break anything pinned to it.

### Added

- **Voice rates for three vendors previously carried for LLM only**, which meant their rows looked
  covered while the modality the vendor is actually used for had no rate:
  - **AWS**: Transcribe ($0.01/min streaming, $0.006/min batch) and Polly
    ($4 / $16 / $30 / $100 per 1M characters for Standard / Neural / Generative / Long-Form).
    The provider is renamed from "AWS Bedrock", since it is no longer only Bedrock.
  - **Google**: Cloud Speech-to-Text ($0.016/min, $0.003/min dynamic batch) and Cloud
    Text-to-Speech ($4 / $16 / $30 / $160 per 1M characters).
  - **Azure**: Speech ($1/hour real-time, $0.18/hour batch) and Text to Speech ($15 per 1M
    characters).
- Cartesia `ink-2` speech-to-text, and Speechmatics `tts`.
- A generated [LiveKit plugin coverage page](https://prices.voicegateway.dev/livekit-coverage)
  reporting which plugins have a rate here and why the rest do not, counted per
  (plugin, modality) pair rather than per vendor.
- A reference-only disclaimer on every page that shows a rate. It was previously only on the
  README, the PyPI page and /how-fresh, none of which is where a reader lands.

## 0.9.0

Published in error and identical to 0.8.0. The tag was created before the pull request it was
meant to carry had merged, so it shipped no changes. Use 0.9.1 or later.

## 0.8.0

### Added

- `ModelInfo.free`. Marks a model the provider charges nothing for, so an empty `prices` block
  is a real zero rather than a missing rate.
- Providers: Rime, Speechmatics, Soniox, LMNT, Ultravox, Hume, ElevenLabs Scribe (speech-to-text
  from a vendor previously only priced for text-to-speech).
- Ultravox is priced with `agent_kminutes`: one per-minute number covering understanding the
  caller, the model and speaking back. Worth contrasting with Vapi's identical-looking
  $0.05/minute, which EXCLUDES the model and speech.

### Behaviour changes

- **A zero now says which kind of zero it is.** Previously a model the provider gives away and a
  model nobody had entered a rate for were the same bytes: a `ModelPrice` with every field
  `None`. The only hint was a `:free` suffix in the model id, which is a naming convention, not
  data. Now:

  | | `total_price` | `unpriced_usage` | `model.free` |
  | --- | --- | --- | --- |
  | Provider charges nothing | `0` | `()` | `True` |
  | No rate recorded | `0` | names the fields | `False` |

  If you treat a zero as authoritative, gate on `model.free` or on an empty `unpriced_usage`.

- **`data_slim.json` and `data.json` no longer disagree.** `exclude_free` decided what to drop by
  asking whether every rate was `None`, which is true of both kinds of zero above. Unpriced
  models were therefore dropped from the slim dataset, so `deepgram/nova-general` returned `0`
  from `data.json` and raised `LookupError` from `data_slim.json`. It now keys on the explicit
  `free` flag: free models are still dropped from slim, unpriced ones are kept.

## 0.7.0

### Added

- Novita text-to-speech; Rime, Speechmatics.
- `Provider.pricing_tier`, naming the vendor plan every rate in a file comes from. CI rejects a
  provider that adds or reprices a model without one.

## 0.6.0

### Added

- **Telephony**, a new modality: `ModelPrice.telephony_kminutes` and `Usage.telephony_minutes`,
  with Twilio, Telnyx and LiveKit SIP. A carrier minute is billed *on top of* speech and the
  model, so it is a separate field from `agent_kminutes`; a phone call on a bundled platform
  pays both.
- `provenance.estimated_fields`, naming rates that are not the vendor's billing meter (a
  per-token bill restated per minute, or a published floor). Docs mark those rows `estimated`.

### Behaviour changes

- **Deepgram domain variants now resolve.** `nova-2-phonecall`, `nova-3-general` and the rest
  raised `LookupError` in 0.3.0 and now price at their tier's rate. Deepgram bills the tier, so
  this is new coverage, not a changed rate.

- **`deepgram/nova-general` and `whisper-tiny` changed from `LookupError` to a zero.** Nova-1 and
  the smaller Deepgram-hosted Whisper sizes are real, callable models with no published rate, so
  they resolve unpriced rather than claiming not to exist. Read `unpriced_usage`, and from the
  Unreleased section above, `model.free`. Only the ten ids Deepgram documents are affected; the
  matchers are explicit `equals` clauses, so an unlisted ref still raises.

- **`gpt-4o-transcribe` and `gpt-4o-mini-transcribe` stopped billing at zero.** They carry
  per-token rates, and a caller measuring seconds got a confident `Decimal('0')`. They now carry
  `input_audio_kseconds`, flagged in `provenance.estimated_fields` because OpenAI publishes that
  figure under a column headed *Estimated cost*, so it will not reconcile against an invoice.

### Fixed rates

- **Deepgram `nova-3-batch` `0.12833` to `0.071667`, `nova-3-multilingual-batch` `0.15333` to
  `0.086667`.** Both held the *undiscounted streaming* price from Deepgram's promotional
  two-price cells, not a prerecorded rate. If you budgeted against the old numbers you were
  ~79% and ~77% high.
- **`flux-general-batch` removed.** Flux is WebSocket-only and has no prerecorded endpoint; the
  row priced a product that does not exist.
- **Deepgram `nova-2` `0.098333` to `0.097222`**, against the rate Deepgram now publishes in its
  pricing FAQ. The old figure came from third-party trackers and was ~1.1% high.

## 0.5.0

### Added

- `PriceCalculation.unpriced_usage`, naming any `Usage` field the matched model had no rate for.
  A zero `total_price` with a non-empty `unpriced_usage` is an undercount, not a free call.

## 0.4.0

Tagged but never published: a skipped job in the release workflow suppressed the upload. Nothing
was released under this version, and PyPI goes 0.3.0 to 0.5.0. Fixed in 0.5.0.
