<div align="center">
  <h1>voice-prices</h1>
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
  Python package for <a href="https://github.com/mahimailabs/voice-prices">github.com/mahimailabs/voice-prices</a>.
</div>
<br/>

## Installation

```bash
uv add voice-prices
```

(or `pip install voice-prices` if you're old school)

To use the CLI with Rich output/help, install the optional CLI dependencies:

```bash
uv add "voice-prices[cli]"
```

(or `pip install "voice-prices[cli]"`)

## Warning: these prices will not be 100% accurate

See [the project README](https://github.com/mahimailabs/voice-prices?tab=readme-ov-file#warning) for more information.

## Usage

### `calc_price`

```python
from voice_prices import Usage, calc_price

price_data = calc_price(
    Usage(input_tokens=1000, output_tokens=100),
    model_ref='gpt-4o',
    provider_id='openai',
)
print(f"Total Price: ${price_data.total_price} (input: ${price_data.input_price}, output: ${price_data.output_price})")
```

### `extract_usage`

`extract_usage` can be used to extract usage data and the `model_ref` from response data,
which in turn can be used to calculate prices:

```py
from voice_prices import extract_usage

response_data = {
    'model': 'claude-sonnet-4-20250514',
    'usage': {
        'input_tokens': 504,
        'cache_creation_input_tokens': 123,
        'cache_read_input_tokens': 0,
        'output_tokens': 97,
    },
}
extracted_usage = extract_usage(response_data, provider_id='anthropic')
price = extracted_usage.calc_price()
print(price.total_price)
```

or with OpenAI where there are two API flavors:

```py
from voice_prices import extract_usage

response_data = {
    'model': 'gpt-5',
    'usage': {'prompt_tokens': 100, 'completion_tokens': 200},
}
extracted_usage = extract_usage(response_data, provider_id='openai', api_flavor='chat')
price = extracted_usage.calc_price()
print(price.total_price)
```

### `UpdatePrices`

`UpdatePrices` can be used to periodically update the price data by downloading it from GitHub

Please note:

- this functionality is explicitly opt-in
- we download data directly from GitHub (`https://raw.githubusercontent.com/mahimailabs/voice-prices/refs/heads/main/prices/data.json`) so we don't and can't monitor requests or gather telemetry

At the time of writing, the `data.json` file
downloaded by `UpdatePrices` is around 26KB when compressed, so is generally very quick to download.

By default `UpdatePrices` downloads price data immediately after it's started in the background, then every hour after that.

Usage with `UpdatePrices` as as context manager:

```py
from voice_prices import UpdatePrices, Usage, calc_price

with UpdatePrices() as update_prices:
    update_prices.wait()  # optionally wait for prices to have updated
    p = calc_price(Usage(input_tokens=123, output_tokens=456), 'gpt-5')
    print(p)
```

Usage with `UpdatePrices` as a simple class:

```py
from voice_prices import UpdatePrices, Usage, calc_price

update_prices = UpdatePrices()
update_prices.start(wait=True)  # start updating prices, optionally wait for prices to have updated
p = calc_price(Usage(input_tokens=123, output_tokens=456), 'gpt-5')
print(p)
update_prices.stop()  # stop updating prices
```

Only one `UpdatePrices` instance can be running at a time.

If you'd like to wait for prices to be updated without access to the `UpdatePrices` instance, you can use the `wait_prices_updated_sync` function:

```py
from voice_prices import wait_prices_updated_sync

wait_prices_updated_sync()
...
```

Or it's async variant, `wait_prices_updated_async`.

### CLI Usage

Run the CLI with:

```bash
uvx voice-prices --help
```

Or, if installed locally, make sure CLI extras are present:

```bash
pip install "voice-prices[cli]"
voice-prices --help
```

If local CLI extras are not installed, the command will print an install hint for `voice-prices[cli]`.

To list providers and models, run:

```bash
uvx voice-prices list
```

To calculate the price of models, run for example:

```bash
uvx voice-prices calc --input-tokens 100000 --output-tokens 3000 o1 o3 claude-opus-4
```

CLI output notes:

- Rich output is the default.
- Use `--plain` (`-p`) for legacy/plain output.
- Use `--no-color` to keep rich formatting without colors.
- Use `-T` / `--table` for wide table output.

## Further Documentation

We do not yet build API documentation for this package, but the source code is relatively simple and well documented.

If you need further information on the API, we encourage you to read the source code.
