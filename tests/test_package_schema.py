from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
import ruamel.yaml
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaValue
from pydantic_core import core_schema, from_json

from genai_prices.data import providers_schema
from genai_prices.types import Usage as _RuntimeUsage
from prices.prices_types import Provider as _PydanticProvider
from prices.utils import package_dir as prices_package_dir, simplify_json_schema

_PROVIDERS_DIR = prices_package_dir / 'providers'

_yaml = ruamel.yaml.YAML(typ='safe')
_yaml.constructor.add_constructor(  # pyright: ignore[reportUnknownMemberType]
    'tag:yaml.org,2002:float',
    lambda loader, node: Decimal(loader.construct_scalar(node)),  # pyright: ignore[reportUnknownLambdaType, reportUnknownMemberType, reportUnknownArgumentType]
)


def _yaml_files() -> list[Path]:
    return sorted(p for p in _PROVIDERS_DIR.iterdir() if p.suffix in ('.yml', '.yaml'))


# Models in these providers' files that ship as TTS entries (input_kchars-priced).
# Used by the provenance test to enforce per-entry pricing_source_url + prices_checked.
# Empty in the initial schema-only commit; populated as catalog YAMLs land.
_TTS_PROVIDER_IDS: set[str] = set()


class CustomGenerateJsonSchema(GenerateJsonSchema):
    def decimal_schema(self, schema: core_schema.DecimalSchema) -> JsonSchemaValue:
        return self.float_schema(core_schema.float_schema())


def remove_ignored_fields(json_schema: Any):
    if isinstance(json_schema, dict):
        json_schema = cast(dict[str, Any], json_schema)

        for f in 'description', 'maxLength', 'minLength', 'pattern', 'additionalProperties':
            json_schema.pop(f, None)

        for value in json_schema.values():
            remove_ignored_fields(value)
    elif isinstance(json_schema, list):
        for item in cast(list[Any], json_schema):
            remove_ignored_fields(item)


@pytest.mark.requires_latest_pydantic
def test_package_schema():
    package_schema = simplify_json_schema(providers_schema.json_schema(schema_generator=CustomGenerateJsonSchema))
    remove_ignored_fields(package_schema)

    # prices is not required in the model info package schema for simplicity
    package_schema['$defs']['ModelInfo']['required'].append('prices')

    # models is not required in the provider package schema for simplicity
    package_schema['$defs']['Provider']['required'].append('models')
    package_schema['$defs']['Provider']['properties']['pricing_urls']['items']['format'] = 'uri'
    # ModelInfo.pricing_source_url is HttpUrl on the Pydantic side, str on the runtime side
    package_schema['$defs']['ModelInfo']['properties']['pricing_source_url']['format'] = 'uri'

    # work around for hack on ConditionalPrice
    package_schema['$defs']['ConditionalPrice']['required'] = ['prices']

    package_schema['$defs']['ClauseRegex']['properties']['regex']['format'] = 'regex'

    prices_schema_path = prices_package_dir / 'data.schema.json'
    prices_schema = from_json(prices_schema_path.read_bytes())

    remove_ignored_fields(prices_schema)

    assert prices_schema == package_schema


@pytest.mark.parametrize('yaml_file', _yaml_files(), ids=lambda p: p.stem)
def test_yaml_roundtrip(yaml_file: Path):
    """Every catalog YAML parses with the Pydantic schema and exercises calc_price.

    Parametrized across `glob('prices/providers/*.yml')` so new provider PRs surface
    as named test rows in CI. The synthetic Usage is shaped per the model's priced
    fields, just enough to exercise the engine without asserting specific numbers.
    """
    import pydantic_core

    with yaml_file.open('rb') as f:
        data = _yaml.load(f)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

    # Validate against the Pydantic schema; raises on bad data.
    provider = _PydanticProvider.model_validate_json(pydantic_core.to_json(data), strict=True)
    assert provider.id, f'{yaml_file.name} has no provider id'

    # Round-trip through the runtime provider schema as well, to confirm the JSON the
    # build emits parses with the consumer-side dataclasses. We serialize the build-side
    # Pydantic model to JSON, then validate that JSON with the runtime schema.
    runtime_payload = b'[' + provider.model_dump_json(by_alias=True, exclude_none=True).encode() + b']'
    runtime_providers = providers_schema.validate_json(runtime_payload)
    assert len(runtime_providers) == 1
    runtime_provider = runtime_providers[0]

    # Exercise calc_price on at least one model per provider with a shape matching
    # the model's priced fields. We don't assert numbers; we just confirm calc_price
    # runs without raising for valid data shapes.
    if runtime_provider.models:
        first_model = runtime_provider.models[0]
        first_price_block = (
            first_model.prices if not isinstance(first_model.prices, list) else first_model.prices[0].prices
        )

        synthetic = _RuntimeUsage()
        if first_price_block.input_mtok is not None or first_price_block.input_audio_mtok is not None:
            synthetic.input_tokens = 1000
        if first_price_block.input_audio_mtok is not None:
            # input_tokens carries the parent-bucket total; input_audio_tokens is the
            # disjoint priced bucket the audio-mtok rate actually multiplies. Set it
            # so the audio-mtok code path runs through calc_mtok_price instead of
            # short-circuiting at zero tokens.
            synthetic.input_audio_tokens = 1000
        if first_price_block.output_mtok is not None or first_price_block.output_audio_mtok is not None:
            synthetic.output_tokens = 100
        if first_price_block.output_audio_mtok is not None:
            synthetic.output_audio_tokens = 100
        if first_price_block.input_kchars is not None:
            synthetic.characters = 1000
        if first_price_block.output_audio_kseconds is not None:
            synthetic.audio_output_seconds = 60
        # If no priced field is recognized, fall back to a token-shaped Usage so calc still runs.
        if all(
            getattr(synthetic, f) is None
            for f in ('input_tokens', 'output_tokens', 'characters', 'audio_output_seconds')
        ):
            synthetic.input_tokens = 1

        result = first_model.calc_price(synthetic, runtime_provider)
        assert result.total_price >= Decimal(0)


def test_tts_entries_have_provenance():
    """Every model that uses `input_kchars` MUST carry pricing_source_url + prices_checked.

    Section 4.6 contribution requirement. Enforced across the catalog so a v0.1 TTS PR
    cannot land without provenance, regardless of which provider file it modifies.
    """
    import pydantic_core

    from prices.prices_types import ModelPrice as _PydanticModelPrice

    missing: list[str] = []
    for yaml_file in _yaml_files():
        with yaml_file.open('rb') as f:
            data = _yaml.load(f)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        provider = _PydanticProvider.model_validate_json(pydantic_core.to_json(data), strict=True)

        for model in provider.models:
            if isinstance(model.prices, list):
                price_blocks: list[_PydanticModelPrice] = [cp.prices for cp in model.prices]
            else:
                price_blocks = [model.prices]

            uses_kchars = any(
                block.input_kchars is not None or block.output_audio_kseconds is not None for block in price_blocks
            )
            if not uses_kchars:
                continue

            if model.pricing_source_url is None:
                missing.append(f'{provider.id}/{model.id}: missing pricing_source_url')
            if model.prices_checked is None:
                missing.append(f'{provider.id}/{model.id}: missing prices_checked')

    assert not missing, '\n'.join(missing)
