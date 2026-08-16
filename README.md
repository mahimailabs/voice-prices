<div align="center">
  <h1>Voice Prices</h1>
</div>
<div align="center">
  <a href="https://github.com/mahimailabs/voice-prices/actions/workflows/ci.yml?query=branch%3Amain"><img src="https://github.com/mahimailabs/voice-prices/actions/workflows/ci.yml/badge.svg?event=push" alt="CI"></a>
  <a href="https://coverage-badge.samuelcolvin.workers.dev/redirect/mahimailabs/voice-prices"><img src="https://coverage-badge.samuelcolvin.workers.dev/mahimailabs/voice-prices.svg" alt="Coverage"></a>
  <a href="https://pypi.python.org/pypi/voice-prices"><img src="https://img.shields.io/pypi/v/voice-prices.svg" alt="PyPI"></a>
  <a href="https://github.com/mahimailabs/voice-prices"><img src="https://img.shields.io/pypi/pyversions/voice-prices.svg" alt="versions"></a>
  <a href="https://github.com/mahimailabs/voice-prices/blob/main/LICENSE"><img src="https://img.shields.io/github/license/mahimailabs/voice-prices.svg" alt="license"></a>
</div>
<br/>
<div align="center">
  Open pricing data and cost calculation for every API a voice agent bills against: speech-to-text, LLM, text-to-speech, speech-to-speech, and voice activity detection.
</div>
<div align="center">
  <a href="https://prices.voicegateway.dev">Browse providers and prices</a>
</div>
<br/>

## Why voice-prices

A voice agent runs several meters at once (STT, an LLM, and TTS, or a single speech-to-speech model), each billed in a different unit. Route them through a gateway like [LiveKit Inference](https://livekit.io/pricing) and a fourth layer sits on top, quoted in its own units again. voice-prices is an open, dated source that puts **direct and gateway cost side by side, per model**.

It is not an argument against gateways. Of the 54 models where both a direct and a gateway rate exist, **33 are priced at or below going direct**, and on the discounted Scale tier 17 come in below the vendor's own price. The point is to make the number visible, whichever way it falls.

| Model | Direct | LiveKit (Build/Ship) | LiveKit Scale | vs direct |
|---|---|---|---|---|
| ElevenLabs Flash v2.5 (TTS, per 1M chars) | $50 | $150 | $60 | **+200%** |
| Cartesia Sonic 3 (TTS, per 1M chars) | $40 | $50 | $37.50 | **+25%**, but Scale is under direct |
| Deepgram Nova-2 (STT, per min) | $0.0059 | $0.0058 | $0.0047 | **-2%**, at cost |
| GPT-5 (LLM, per 1M input tokens) | $1.25 | $1.25 | n/a | **identical**, pass-through |
| GPT-5.4 (LLM, per 1M input tokens) | $2.50 | $5.00 | n/a | **+100%** |

Most LLM rates pass straight through (23 of the 27 comparable models are identical to the penny), so gateway markups mostly land on TTS and STT. The exception is worth knowing: the frontier models are not passed through. GPT-5.4, GPT-5.5, Gemini 2.5 Pro and Gemini 3.1 Pro are each +100%.

Every rate is dated, links to the vendor pricing page it came from, and is re-checked by an LLM-assisted freshness job that a human confirms. [Browse the full catalog ->](https://prices.voicegateway.dev)

## Quick start

```bash
uv add voice-prices   # or: pip install voice-prices
```

```python
from voice_prices import Usage, calc_price

# Price an LLM call going direct to the provider
direct = calc_price(Usage(input_tokens=1000, output_tokens=100), model_ref='gpt-4o', provider_id='openai')

# ...and through the LiveKit gateway, for the same model
gateway = calc_price(Usage(input_tokens=1000, output_tokens=100), model_ref='gpt-4o', provider_id='livekit')

print(direct.total_price, gateway.total_price)
```

voice-prices is a fork of [pydantic/genai-prices](https://github.com/pydantic/genai-prices) extended with first-class voice (TTS/STT) pricing and the direct-vs-gateway comparison. Disclosure: it is maintained alongside a voice infrastructure product (VoiceGateway); the dataset is open and neutral, and it flags its maintainer's own markups too.

## Features

- Advanced logic for matching on model and provider IDs to maximise the chance of using the correct model
- Support for historic prices and prices changes, e.g. we have the prices for o3 before and after its price changed
- Support for variable daily prices, e.g. we support calculating deepseek prices even with off-peak pricing
- tiered pricing support for Gemini models where you pay a separate price for very large contexts
- support for [identifying price discrepancies](prices/README.md) from other sources
- first-class voice billing units: TTS per character, STT and VAD per audio second, speech-to-speech per audio token
- direct vs gateway comparison per model, in the unit each category is actually quoted in
- automated [voice price freshness checks](#keeping-voice-prices-fresh): a manually-triggered GitHub Action re-verifies voice rates against each provider's pricing page and opens a PR when a rate has drifted
- Python package, CLI, and a [browsable catalog](https://prices.voicegateway.dev)
- TODO: hosted API

### Providers

Every provider in the catalog, and which categories it prices:

[comment]: <> (providers-start)

**39 providers, 1,229 priced models.** 48 STT, 1,104 LLM, 69 TTS, 6 S2S, 2 VAD.

| Provider | Models | Categories |
| --- | ---: | --- |
| [ai-coustics](prices/providers/ai_coustics.yml) | 2 | VAD |
| [Anthropic](prices/providers/anthropic.yml) | 18 | LLM |
| [AssemblyAI](prices/providers/assemblyai.yml) | 1 | STT |
| [Avian](prices/providers/avian.yml) | 4 | LLM |
| [AWS Bedrock](prices/providers/aws.yml) | 70 | LLM, S2S |
| [Microsoft Azure](prices/providers/azure.yml) | 18 | LLM |
| [Cartesia](prices/providers/cartesia.yml) | 1 | TTS |
| [Cerebras](prices/providers/cerebras.yml) | 4 | LLM |
| [Cohere](prices/providers/cohere.yml) | 6 | LLM |
| [Deepgram](prices/providers/deepgram.yml) | 10 | STT, TTS |
| [Deepseek](prices/providers/deepseek.yml) | 4 | LLM |
| [ElevenLabs](prices/providers/elevenlabs.yml) | 5 | TTS |
| [Fireworks](prices/providers/fireworks.yml) | 13 | LLM |
| [Google](prices/providers/google.yml) | 32 | LLM, S2S |
| [Groq](prices/providers/groq.yml) | 30 | STT, LLM |
| [HuggingFace (cerebras)](prices/providers/huggingface_cerebras.yml) | 1 | LLM |
| [HuggingFace (fireworks-ai)](prices/providers/huggingface_fireworks-ai.yml) | 3 | LLM |
| [HuggingFace (groq)](prices/providers/huggingface_groq.yml) | 5 | LLM |
| [HuggingFace (hyperbolic)](prices/providers/huggingface_hyperbolic.yml) | 12 | LLM |
| [HuggingFace (nebius)](prices/providers/huggingface_nebius.yml) | 26 | LLM |
| [HuggingFace (novita)](prices/providers/huggingface_novita.yml) | 61 | LLM |
| [HuggingFace (nscale)](prices/providers/huggingface_nscale.yml) | 20 | LLM |
| [HuggingFace (ovhcloud)](prices/providers/huggingface_ovhcloud.yml) | 7 | LLM |
| [HuggingFace (publicai)](prices/providers/huggingface_publicai.yml) | 8 | LLM |
| [HuggingFace (sambanova)](prices/providers/huggingface_sambanova.yml) | 8 | LLM |
| [HuggingFace (together)](prices/providers/huggingface_together.yml) | 23 | LLM |
| [Inworld](prices/providers/inworld.yml) | 4 | STT, TTS |
| [LiveKit Inference](prices/providers/livekit.yml) | 78 | STT, LLM, TTS |
| [LiveKit Inference (Scale)](prices/providers/livekit_scale.yml) | 36 | STT, TTS |
| [Mistral](prices/providers/mistral.yml) | 18 | LLM |
| [MoonshotAi](prices/providers/moonshotai.yml) | 9 | LLM |
| [Novita](prices/providers/novita.yml) | 37 | LLM, TTS |
| [OpenAI](prices/providers/openai.yml) | 73 | STT, LLM, TTS, S2S |
| [OpenRouter](prices/providers/openrouter.yml) | 461 | LLM |
| [OVHcloud AI Endpoints](prices/providers/ovhcloud.yml) | 15 | LLM |
| [Perplexity](prices/providers/perplexity.yml) | 8 | LLM |
| [Telnyx](prices/providers/telnyx.yml) | 14 | STT, LLM, TTS |
| [Together AI](prices/providers/together.yml) | 72 | LLM |
| [X AI](prices/providers/x_ai.yml) | 12 | LLM |

[comment]: <> (providers-end)

## Usage

### Python Package & CLI

See the [Python README](packages/python/README.md) for instructions on how to install and use the Python package and CLI.

### Download data

Price data is available in the following files:

- [`prices/data.json`](prices/data.json) - JSON file with all prices
- [`prices/data.schema.json`](prices/data.schema.json) - JSON Schema for `prices/data.json`
- [`prices/data_slim.json`](prices/data_slim.json) - JSON file long fields like descriptions removed and free models removed
- [`prices/data_slim.schema.json`](prices/data_slim.schema.json) - JSON Schema for `prices/data_slim.json`

Feel free to download these files and use them as you wish. We would be grateful if you would reference this
project wherever you use it and [contribute](#contributing) back to the project if you find any errors.

### API

Coming soon...

<h2 id="warning">⚠️ Warning: these prices will not be 100% accurate</h2>

This project is a best effort by the maintainers and community to provide an indicative
estimate of the price you might pay for calling a voice or LLM API.

The price data cannot be exactly correct because model providers do not provide exact price information for their APIs
in a format which can be reliably processed.

If you get a bill you weren't expecting, don't blame us!

If you're a lawyer, please read the [LICENSE](https://github.com/mahimailabs/voice-prices/blob/main/LICENSE) under which this project is developed, hosted and distributed.

If you're a developer, please [contribute](#contributing) to fix any missing or incorrect prices you find.

## Contributing

We welcome contributions from the community and especially model/inference providers!

**If you're a model provider:** serve a pricing endpoint and we will track your rates automatically
instead of reading your pricing page. It is one HTTPS GET returning JSON, it takes an afternoon, and
it permanently fixes pricing accuracy for every developer using your API.
[Here is exactly what we need](https://prices.voicegateway.dev/pricing-feed).

Otherwise, to contribute:

- See [`prices/README.md`](prices) for instructions on how to contribute to the price data.
- To add a new provider, follow [`CONTRIBUTING.md`](CONTRIBUTING.md) and copy the template for your modality (LLM, TTS, or STT), or open an [Add a provider](https://github.com/mahimailabs/voice-prices/issues/new?template=add-provider.yml) issue.
- Feel free to submit pull requests or issues about the Python package.
- If you need a library for another language, please create an issue, we'd be happy to discuss building it, hosting it here,
  or helping you maintain it elsewhere.

### Keeping voice prices fresh

LLM rates are cross-checked against external sources (Helicone, OpenRouter, LiteLLM, Simon Willison's llm-prices). Those sources don't cover voice (TTS/STT), so a maintainer-run GitHub Action keeps voice rates honest: **Pricing freshness** (under the Actions tab) re-verifies each voice model's rate against its `pricing_source_url` using a headless browser plus an LLM extractor, and opens a single rolling PR (`bot/pricing-freshness`) when a rate has drifted. It is manual-only (no schedule, so it costs nothing until run), needs an `OPENAI_API_KEY` repository secret, and every proposed change is reviewed by a human before merging.

## Thanks

voice-prices is a fork of [pydantic/genai-prices](https://github.com/pydantic/genai-prices), extended with first-class support for voice (TTS/STT) pricing and an automated freshness check for those rates. Huge thanks to that project and its maintainers for the engine, schema, and the initial LLM price database this builds on.

It also would not be possible without the following data sources, which the LLM price discrepancy pipeline pulls from:

- [Helicone](https://github.com/Helicone/helicone/tree/main/packages/cost)
- [Open Router](https://openrouter.ai/docs/api/api-reference/models/get-models)
- [LiteLLM](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json)
- Simon Willison's [llm-prices](https://github.com/simonw/llm-prices/pull/7)

Thanks to all those projects!
