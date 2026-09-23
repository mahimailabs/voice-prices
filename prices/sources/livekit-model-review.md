# LiveKit Inference model review — 2026-09-23

These are LiveKit gateway rates, checked against the [LiveKit pricing page](https://livekit.com/pricing/inference)
and its embedded structured pricing data (including serving-provider variants). Availability and retirement dates come from
[LiveKit's model table](https://docs.livekit.io/agents/models/inference/#models).
No direct-provider prices were substituted.

## Priced names

Use `provider_id='livekit'` for Build/Ship or `provider_id='livekit-scale'` for Scale.
The short names below and their vendor-qualified SDK names both resolve. The existing
`google/*-preview` catalog IDs remain stable; their SDK names without `-preview` are exact aliases.

| Requested name | Unit | Build/Ship USD | Scale USD | Note |
| --- | --- | --- | --- | --- |
| `kimi-k2.6` | 1M tokens, input / output | 0.95 / 4 | 0.95 / 4 | `moonshotai/kimi-k2.6`; deprecated, retires September 26. No cached-input rate published. |
| `gemini-3-flash` | 1M tokens, input / cached / output | 0.50 / 0.05 / 3 | 0.50 / 0.05 / 3 | Matches `google/gemini-3-flash-preview`. |
| `gemini-3.1-pro` | 1M tokens, input / cached / output | 4 / 0.40 / 18 | 4 / 0.40 / 18 | Matches `google/gemini-3.1-pro-preview`. |
| `universal-3-5-pro` | minute | 0.0075 | 0.0075 | `assemblyai/universal-3-5-pro`. |
| `flux-general` | minute | 0.0065 | 0.0057 | English; matches `deepgram/flux-general-en`. |
| `s2-pro` | 1M characters | 15 | 15 | `fishaudio/s2-pro`. |
| `s2.1-pro` | 1M characters | 15 | 15 | `fishaudio/s2.1-pro`. |
| `s2.1-pro-free` | 1M characters | 0 | 0 | Explicitly free; deprecated, retires September 25. |

The [Python inference SDK](https://github.com/livekit/agents/blob/main/livekit-agents/livekit/agents/inference/llm.py)
lists Gemini names without `-preview`, while the pricing feed uses `-preview`.
[LiveKit's Deepgram guide](https://docs.livekit.io/agents/models/stt/deepgram/#usage)
uses `deepgram/flux-general` with English. This catalog uses that English rate for the bare alias.
For multilingual usage, pass `deepgram/flux-general-multi` or `deepgram/flux-general:multi`:
$0.0078/minute on Build/Ship and $0.0068/minute on Scale. The price calculator has no separate language argument.

## Requested names without a current verified rate

| Requested ID | Finding | Catalog treatment |
| --- | --- | --- |
| `deepseek-ai/deepseek-v3` | Retired May 1, 2026. Absent from current pricing feed. | No new rate. |
| `deepseek-ai/deepseek-v3.2` | Retired March 7, 2026. Absent from current pricing feed. | No new rate. |
| `zai/glm-5.1` | Listed in the SDK, but no row in the current pricing page/feed. | No guessed rate. |
| `deepgram/aura` | Aura-1 retired February 13, 2026. | Not mapped to the distinct Aura-2 model. |
| `cartesia/sonic` | Retired June 1, 2026. | Not mapped to Sonic 2, 3, or Latest. |
| `inworld/inworld-stt-1` | Listed in the inference SDK, but no current LiveKit rate. The STT guide describes the direct plugin. | No direct rate substituted. |
| `inworld/inworld-tts-1` | Retired June 1, 2026. | Not restored from the old source snapshot. |
| `inworld/inworld-tts-1-max` | Retired June 1, 2026. | Not restored from the old source snapshot. |
| `inworld/inworld-tts-1.5` | Listed in the SDK, but the rate card distinguishes 1.5 Max and Mini without documenting this name's mapping. | No ambiguous alias. Existing Max and Mini entries remain available. |

SDK identifiers alone do not establish a rate or current availability. See the
[LLM source](https://github.com/livekit/agents/blob/main/livekit-agents/livekit/agents/inference/llm.py),
[STT source](https://github.com/livekit/agents/blob/main/livekit-agents/livekit/agents/inference/stt.py),
[TTS source](https://github.com/livekit/agents/blob/main/livekit-agents/livekit/agents/inference/tts.py),
and [Inworld STT guide](https://docs.livekit.io/agents/models/stt/inworld/).

## Full pricing-page coverage

All 107 visible pricing rows are covered: 58 LLM serving-provider rows, 22 STT rows,
and 27 TTS rows, representing 90 distinct model IDs. The snapshot excludes sunset models
and the unlisted Vercel Gemma route. Direct-provider catalogs are unchanged.

For provider-specific LLM billing use a catalog selector such as
`openai/gpt-5@azure`, `openai/gpt-5@openai`, or `openai/gpt-oss-120b@groq`,
with `provider_id='livekit'` (or `livekit-scale`). The `@provider` syntax belongs to this
price calculator; LiveKit's SDK takes its serving provider separately. Unqualified IDs retain
the first listed route's rate, which does not promise a rate for every automatic routing decision.
Cache-read and cache-write prices are retained separately where published; unavailable cache
rates are left unknown.

Gradium and Rime's zero-price promotions use timestamp-constrained rates. Gradium resumes
$48/$36 per million characters on Build/Scale at 2026-10-08 07:00 UTC. Rime resumes
$50/$50 for Coda and $30/$20 for Mist variants at 2026-10-01 07:00 UTC. Calculations
before, during, and after these windows use the corresponding rate; promotional models remain
in the slim dataset. Fish Audio's separately listed free model is explicitly free and is omitted
from the slim dataset according to existing policy.

Inworld TTS 2 now costs $25/$15, TTS 1.5 Max $35/$20, and TTS 1.5 Mini $15/$8 per
million characters on Build/Scale. Deepgram Flux TTS uses its post-promotion $45/$40.50 rate.
Models retired since the previous snapshot, including the ElevenLabs Inference rows, are removed.

## Source annotations and regeneration

`livekit_pricing.json` stores exact decimal strings, source model IDs, serving providers,
reviewed aliases, retirement dates, and promotion boundaries with regular rates.

- `REFRESH=1 make livekit-get` fetches the official page and normalizes its embedded JSON.
- `make livekit-get` regenerates offline from the committed snapshot.
- `make build` updates schemas, full/slim data, Python package data, and docs.

Both regeneration paths preserve manually maintained telephony entries. Build and Ship must
have equal prices; otherwise generation fails for review. Scale entries are emitted whenever
current or post-promotion rates differ, with fallback to the base provider for other models.

Deprecated but callable rows appear in the LiveKit docs with retirement notices. Regeneration
excludes them at their retirement date; an already installed catalog keeps its snapshot until
updated. Promotion expiry, in contrast, is evaluated automatically at request time.
