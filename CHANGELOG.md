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
