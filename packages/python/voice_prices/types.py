from __future__ import annotations as _annotations

import dataclasses
import re
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol, TypeGuard, TypeVar, cast, overload

import pydantic
from typing_extensions import TypedDict

if TYPE_CHECKING:
    from .confidence import Freshness

__all__ = (
    'ProviderID',
    'PriceBreakdown',
    'PriceCalculation',
    'AbstractUsage',
    'Usage',
    'Provider',
    'UsageExtractorMapping',
    'UsageExtractor',
    'ModelInfo',
    'Provenance',
    'AgentVotes',
    'SourceRate',
    'ModelPrice',
    'TieredPrices',
    'Tier',
    'ConditionalPrice',
    'StartDateConstraint',
    'TimeOfDateConstraint',
    'ClauseStartsWith',
    'ClauseEndsWith',
    'ClauseContains',
    'ClauseRegex',
    'ClauseEquals',
    'ClauseOr',
    'ClauseAnd',
    'MatchLogic',
    'ArrayMatch',
    'providers_schema',
)


# Define MatchLogic after __all__ to avoid forward reference issues
def clause_discriminator(v: Any) -> str | None:
    assert isinstance(v, dict), f'Expected dict, got {type(v)}'
    return next(iter(v))  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]


MatchLogic = Annotated[
    Annotated['ClauseStartsWith', pydantic.Tag('starts_with')]
    | Annotated['ClauseEndsWith', pydantic.Tag('ends_with')]
    | Annotated['ClauseContains', pydantic.Tag('contains')]
    | Annotated['ClauseRegex', pydantic.Tag('regex')]
    | Annotated['ClauseEquals', pydantic.Tag('equals')]
    | Annotated['ClauseOr', pydantic.Tag('or')]
    | Annotated['ClauseAnd', pydantic.Tag('and')],
    pydantic.Discriminator(clause_discriminator),
]

ProviderID = Literal[
    'avian',
    'groq',
    'openai',
    'novita',
    'fireworks',
    'deepseek',
    'mistral',
    'x-ai',
    'google',
    'perplexity',
    'aws',
    'together',
    'anthropic',
    'azure',
    'cohere',
    'openrouter',
]


@dataclass
class ArrayMatch:
    type: Literal['array-match']
    field: str
    match: MatchLogic

    def extract(self, items: Sequence[Any]) -> Mapping[str, Any] | None:
        for item in items:
            if _is_mapping(item) and (item_field := item.get(self.field)):
                if self.match.is_match(item_field):
                    return item


ExtractPath = str | Sequence[str | ArrayMatch]


@dataclass
class PriceBreakdown:
    """Per-component USD contributions that sum to total_price.

    Maintenance contract:
        Every Decimal-typed priced field on ModelPrice MUST have a matching
        Decimal field on PriceBreakdown. When ModelPrice gains a new priced
        field (e.g. upstream voice-prices adds `input_image_mtok`), add the
        corresponding `input_image_tokens: Decimal = Decimal(0)` here in the
        same PR. The test `test_breakdown_covers_priced_fields` walks
        ModelPrice fields at runtime and fails if any priced field is missing
        its PriceBreakdown counterpart, so drift cannot survive CI.
    """

    input_tokens: Decimal = Decimal(0)
    output_tokens: Decimal = Decimal(0)
    cache_read_tokens: Decimal = Decimal(0)
    cache_write_tokens: Decimal = Decimal(0)
    input_audio_tokens: Decimal = Decimal(0)
    output_audio_tokens: Decimal = Decimal(0)
    cache_audio_read_tokens: Decimal = Decimal(0)

    input_kchars: Decimal = Decimal(0)
    output_audio_kseconds: Decimal = Decimal(0)
    input_audio_kseconds: Decimal = Decimal(0)

    voice_class_input_adjustment: Decimal = Decimal(0)
    """USD adjustment caused by voice_multiplier on input_kchars.

    Equals 0 when no multiplier fired or multiplier == 1.0; positive when
    multiplier > 1.0; negative when multiplier < 1.0. Sums with input_kchars
    to give the post-multiplier input character cost.
    """

    voice_class_output_adjustment: Decimal = Decimal(0)
    """USD adjustment caused by voice_multiplier on output_audio_kseconds.

    Always Decimal(0) in v0.1 (no catalog entries set output_audio_kseconds);
    activates with v0.2 catalog expansion for PlayHT/Murf-style models.
    """

    agent_kminutes: Decimal = Decimal(0)
    """USD for bundled voice-agent session time.

    Neither input nor output: a bundled agent minute prices orchestration plus
    whatever STT/LLM/TTS the platform chose, in proportions it does not disclose.
    Attributing it to a direction would invent a split the vendor never published,
    so it sums into total_price alongside `requests`.
    """

    telephony_kminutes: Decimal = Decimal(0)
    """USD for connected call minutes on the phone network.

    Charged by the carrier for the call leg itself, on top of whatever the agent spends on
    speech and the model. Sums into total_price alongside `requests` and `agent_kminutes`:
    a carrier minute is not attributable to input or output.
    """

    requests: Decimal = Decimal(0)

    def sum(self) -> Decimal:
        """Sum of every contributing component. Equals total_price by invariant."""
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
            + self.input_audio_tokens
            + self.output_audio_tokens
            + self.cache_audio_read_tokens
            + self.input_kchars
            + self.output_audio_kseconds
            + self.input_audio_kseconds
            + self.voice_class_input_adjustment
            + self.voice_class_output_adjustment
            + self.agent_kminutes
            + self.telephony_kminutes
            + self.requests
        )


@dataclass(repr=False)
class PriceCalculation:
    input_price: Decimal
    output_price: Decimal
    total_price: Decimal
    breakdown: PriceBreakdown
    applied_voice_multiplier: Decimal | None
    """The raw multiplier value looked up for this call (e.g. Decimal('1.5')).

    None when the resolved ModelPrice has no voice_multipliers OR when the
    caller did not pass voice_class and there is no default to apply.
    """
    model: ModelInfo = dataclasses.field(repr=False)
    provider: Provider = dataclasses.field(repr=False)
    model_price: ModelPrice
    auto_update_timestamp: datetime | None
    unpriced_usage: tuple[str, ...] = ()
    """`Usage` field names the caller supplied that no rate on this model could price.

    Empty for the overwhelming majority of calls. When it is not empty, `total_price` is an
    undercount, and a zero `total_price` means "this model has no meter for what you
    measured", not "this call was free".

    That distinction has no other signal. A model can resolve, carry prices, and still
    return `Decimal('0')` when the caller measures in a unit the vendor does not bill in:
    OpenAI prices its audio models per token, a voice runtime measures seconds and
    characters, and nothing bridges the two. The result is a successful calculation with
    every breakdown field at zero, indistinguishable downstream from a genuinely zero-cost
    call. Read this field before trusting a zero.

    Names are `Usage` field names (`audio_input_seconds`), not `ModelPrice` rate names
    (`input_audio_kseconds`), because the caller supplied the former. Order is stable.
    """

    def __repr__(self) -> str:
        return (
            'PriceCalculation('
            f'input_price={self.input_price!r}, '
            f'output_price={self.output_price!r}, '
            f'total_price={self.total_price!r}, '
            f'breakdown={self.breakdown!r}, '
            f'applied_voice_multiplier={self.applied_voice_multiplier!r}, '
            f'model={self.model.summary()}, '
            f'provider={self.provider.summary()}, '
            f'model_price=ModelPrice({self.model_price}), '
            f'auto_update_timestamp={self.auto_update_timestamp!r}, '
            f'unpriced_usage={self.unpriced_usage!r})'
        )

    def freshness(self, *, today: date | None = None) -> Freshness:
        """How trustworthy this rate is: verification_status, a coarse confidence label, and staleness.

        Derived at call time from the model's provenance and the provider's staleness threshold.
        See ``voice_prices.confidence`` for the rules.
        """
        from .confidence import model_freshness

        return model_freshness(self.model, self.provider, today=today)


@dataclass(repr=False)
class ExtractedUsage:
    usage: Usage
    model: ModelInfo | None = dataclasses.field(repr=False)
    provider: Provider = dataclasses.field(repr=False)
    auto_update_timestamp: datetime | None

    def calc_price(
        self, *, genai_request_timestamp: datetime | None = None, model: ModelInfo | None = None
    ) -> PriceCalculation:
        """Calculate the price for the given usage.

        Args:
            genai_request_timestamp: The timestamp of the request to the GenAI service, use `None` to use the current
                time.
            model: The model to calculate the price for, if `None` the model from the response data is used.
        """
        model = model or self.model
        if model is None:
            raise ValueError('No model reference found in response data and model not provided')

        return model.calc_price(
            self.usage,
            self.provider,
            genai_request_timestamp=genai_request_timestamp,
            auto_update_timestamp=self.auto_update_timestamp,
        )

    def __repr__(self) -> str:
        return (
            'ExtractedUsage('
            f'usage={self.usage!r}, '
            f'model={self.model.summary() if self.model else None}, '
            f'provider={self.provider.summary()}, '
            f'auto_update_timestamp={self.auto_update_timestamp!r})'
        )

    def __add__(self, other: ExtractedUsage | Any) -> ExtractedUsage:
        """Accumulate inner Usage, handling nullable usage fields.

        Accumulating usage is useful for common streaming situations where user wants to save and compute costs for
        all the response chunks in a stream

        Args:
              other: The usage to accumulate with this usage extraction instance.
        """

        if not isinstance(other, ExtractedUsage):
            return NotImplemented  # will raise a TypeError

        models_match = self.model and other.model and other.model.id == self.model.id
        if not models_match:
            raise ValueError(f'Cannot add {other} to {self}, models do not match {other.model} != {self.model}')

        providers_match = self.provider and other.provider and other.provider.id == self.provider.id
        if not providers_match:
            raise ValueError(
                f'Cannot add {other} to {self}, providers do not match {other.provider} != {self.provider}'
            )

        return ExtractedUsage(
            model=self.model,
            provider=self.provider,
            auto_update_timestamp=self.auto_update_timestamp,
            usage=self.usage + other.usage,
        )

    def __radd__(self, other: ExtractedUsage | Any) -> ExtractedUsage:
        return self + other


class AbstractUsage(Protocol):
    """Abstract definition of data about token usage for a single LLM or voice call."""

    @property
    def input_tokens(self) -> int | None:
        """Total number of input/prompt tokens.

        Note this should INCLUDE both uncached and cached tokens.
        """

    @property
    def cache_write_tokens(self) -> int | None:
        """Number of tokens written to the cache."""

    @property
    def cache_read_tokens(self) -> int | None:
        """Number of tokens read from the cache.

        For many models this is described as just "cached tokens".
        """

    @property
    def output_tokens(self) -> int | None:
        """Number of output/completion tokens."""

    @property
    def input_audio_tokens(self) -> int | None:
        """Number of audio input tokens."""

    @property
    def cache_audio_read_tokens(self) -> int | None:
        """Number of audio tokens read from the cache."""

    @property
    def output_audio_tokens(self) -> int | None:
        """Number of output audio tokens."""

    @property
    def characters(self) -> int | None:
        """Number of input characters submitted to a TTS model."""

    @property
    def audio_output_seconds(self) -> Decimal | None:
        """Number of seconds of generated audio (TTS audio output).

        Migrated from int to Decimal in v0.0.7 for sub-second precision and
        symmetry with audio_input_seconds. Backwards-compatible at runtime:
        callers passing int continue to work via Decimal(int) coercion in the
        engine.
        """

    @property
    def audio_input_seconds(self) -> Decimal | None:
        """Duration (in seconds) of audio submitted to the model (STT input).

        ModelPrice puts direction before modality (input_audio_kseconds); Usage
        puts modality before direction (audio_input_seconds). Intentional;
        mirrors the existing output_audio_kseconds / audio_output_seconds pair.

        Decimal-typed so callers can express sub-second precision; Deepgram and
        AssemblyAI bill in 0.01s increments. Pass via Decimal('12.34') or
        convert from float at the call site.
        """

    @property
    def voice_class(self) -> str | None:
        """Voice class identifier used for this call.

        Matches a key in ModelPrice.voice_multipliers for the resolved
        provider+model. Strings are scoped to the provider+model; the same
        string may mean different things across providers. Callers construct
        the string from the voice they used and the model they called against,
        usually via a per-model lookup in their own config.
        """


@dataclass
class Usage:
    """Simple implementation of `AbstractUsage` as a dataclass."""

    input_tokens: int | None = None
    """Number of input/prompt tokens."""

    cache_write_tokens: int | None = None
    """Number of tokens written to the cache."""
    cache_read_tokens: int | None = None
    """Number of tokens read from the cache."""

    output_tokens: int | None = None
    """Number of output/completion tokens."""

    input_audio_tokens: int | None = None
    """Number of audio input tokens."""
    cache_audio_read_tokens: int | None = None
    """Number of audio tokens read from the cache."""
    output_audio_tokens: int | None = None
    """Number of output audio tokens."""

    characters: int | None = None
    """Number of input characters submitted to a TTS model."""

    audio_output_seconds: Decimal | None = None
    """Duration of generated audio (TTS audio output, schema-only until
    PlayHT/Murf land in catalog). Decimal-typed for sub-second precision.

    Backwards compatibility: the runtime engine wraps the value with
    Decimal(...) before computing cost, so v0.x consumers passing an int
    continue to work at runtime. Type checkers will flag the int under strict
    mode; pass Decimal(60) or Decimal('60') to silence them.
    """

    audio_input_seconds: Decimal | None = None
    """Duration (in seconds) of audio submitted to the model (STT input).

    ModelPrice puts direction before modality (input_audio_kseconds); Usage
    puts modality before direction (audio_input_seconds). Intentional;
    mirrors the existing output_audio_kseconds / audio_output_seconds pair.

    Decimal-typed so callers can express sub-second precision; Deepgram and
    AssemblyAI bill in 0.01s increments. Pass via Decimal('12.34') or convert
    from float at the call site.
    """

    agent_minutes: Decimal | None = None
    """Duration of a bundled voice-agent session, in minutes.

    For platforms that bill one blended per-minute rate covering STT, LLM, TTS and
    orchestration together (Vapi, Retell, Bland, ElevenLabs Agents and similar).
    Decimal-typed because calls are not whole minutes; most platforms bill per second
    and round at the end.
    """

    telephony_minutes: Decimal | None = None
    """Connected call minutes carried over the phone network.

    The carrier leg, billed whether or not the agent speaks. Independent of
    `agent_minutes`: a call on a bundled platform accrues both, one for the platform and
    one for the carrier. Decimal-typed because carriers bill per second and round at the end.
    """

    voice_class: str | None = None
    """Voice class identifier used for this call (matches a key in
    `ModelPrice.voice_multipliers` for the resolved provider+model).
    """

    def __add__(self, other: Usage | Any) -> Usage:
        if not isinstance(other, Usage):
            return NotImplemented

        def _add_int(a: int | None, b: int | None) -> int | None:
            return None if a is b is None else (a or 0) + (b or 0)

        def _add_decimal(a: Decimal | None, b: Decimal | None) -> Decimal | None:
            if a is None and b is None:
                return None
            a_val = Decimal(0) if a is None else a
            b_val = Decimal(0) if b is None else b
            return a_val + b_val

        def _coalesce_str(a: str | None, b: str | None) -> str | None:
            if a is None:
                return b
            if b is None:
                return a
            if a != b:
                raise ValueError(f'Cannot add Usages with conflicting voice_class values: {a!r} != {b!r}')
            return a

        return Usage(
            input_tokens=_add_int(self.input_tokens, other.input_tokens),
            cache_write_tokens=_add_int(self.cache_write_tokens, other.cache_write_tokens),
            cache_read_tokens=_add_int(self.cache_read_tokens, other.cache_read_tokens),
            output_tokens=_add_int(self.output_tokens, other.output_tokens),
            input_audio_tokens=_add_int(self.input_audio_tokens, other.input_audio_tokens),
            cache_audio_read_tokens=_add_int(self.cache_audio_read_tokens, other.cache_audio_read_tokens),
            output_audio_tokens=_add_int(self.output_audio_tokens, other.output_audio_tokens),
            characters=_add_int(self.characters, other.characters),
            audio_output_seconds=_add_decimal(self.audio_output_seconds, other.audio_output_seconds),
            audio_input_seconds=_add_decimal(self.audio_input_seconds, other.audio_input_seconds),
            agent_minutes=_add_decimal(self.agent_minutes, other.agent_minutes),
            telephony_minutes=_add_decimal(self.telephony_minutes, other.telephony_minutes),
            voice_class=_coalesce_str(self.voice_class, other.voice_class),
        )

    def __radd__(self, other: Usage) -> Usage:
        return self + other


@dataclass
class Provider:
    """Information about an LLM inference provider"""

    id: str
    """Unique identifier for the provider"""
    name: str
    """Link to pricing page for the provider"""
    api_pattern: str
    """Common name of the organization"""
    pricing_urls: list[str] | None = None
    """Pattern to identify provider via HTTP API URL."""
    description: str | None = None
    """Description of the provider"""
    price_comments: str | None = None
    """Comments about the pricing of this provider's models, especially challenges in representing the provider's pricing model."""
    model_match: MatchLogic | None = None
    """Logic to find a provider based on the model reference."""
    provider_match: MatchLogic | None = None
    """Logic to find a provider based on the provider identifier."""
    extractors: list[UsageExtractor] | None = None
    """Logic to extract usage information from the provider's API responses."""
    fallback_model_providers: list[str] | None = None
    """List of provider identifiers to fallback to to get prices if this provider doesn't have a price.

    This is used when one provider offers another provider's models, e.g. Google and AWS offer Anthropic models,
    Azure offers OpenAI models, etc.
    """
    staleness_threshold_days: int = 60
    """Recommended maximum age (in days) for `prices_checked` on this provider's models before consumers
    should re-verify against `pricing_source_url`. Metadata only; voice-prices itself does not warn or
    fail on stale entries.
    """
    pricing_tier: str | None = None
    """The vendor plan every rate from this provider is taken from, named as the vendor names it.

    Most vendors publish several prices for the same model: pay-as-you-go, one or more committed
    plans, and an enterprise rate. This catalog takes the cheapest tier a new account can reach
    with no spend commitment, and names it here so a consumer can tell whether the rate matches
    the plan they are actually on.

    Verbatim from the vendor (`Pay As You Go`, `On-Demand`, `Standard`), not a normalised enum,
    so a reader can find the same words on the pricing page. The literal `Single published rate`
    means the vendor publishes one price with no plan to choose from; `None` means no one has
    recorded it yet, which is not the same claim.
    """
    models: list[ModelInfo] = dataclasses.field(default_factory=list)
    """List of models supported by this provider"""

    def find_model(self, model_ref: str, *, all_providers: list[Provider] | None = None) -> ModelInfo | None:
        model_ref = model_ref.lower()
        for model in self.models:
            if model.is_match(model_ref):
                return model
        if self.fallback_model_providers and all_providers:
            for provider_id in self.fallback_model_providers:
                provider = next((p for p in all_providers if p.id == provider_id), None)
                if provider:
                    # don't pass all_providers when falling back, so we can only have one step of fallback
                    if model := provider.find_model(model_ref):
                        return model
        return None

    def extract_usage(self, response_data: Any, *, api_flavor: str = 'default') -> tuple[str | None, Usage]:
        """Extract model name and usage information from a response.

        Args:
            response_data: The response data from the provider's API.
            api_flavor: The flavor of API used for this request.

        Raises:
            ValueError: If the response data is invalid or the API flavor is not found.

        Returns:
            tuple[str, Usage]: The extracted model name and usage information.
        """
        if self.extractors is None:
            raise ValueError('No extraction logic defined for this provider')

        try:
            extractor = next(e for e in self.extractors if e.api_flavor == api_flavor)
        except StopIteration as e:
            fs = ', '.join(e.api_flavor for e in self.extractors)
            raise ValueError(f'Unknown api_flavor {api_flavor!r}, allowed values: {fs}') from e

        return extractor.extract(response_data)

    def summary(self) -> str:
        return f'Provider(id={self.id!r}, name={self.name!r}, ...)'


UsageField = Literal[
    'input_tokens',
    'cache_write_tokens',
    'cache_read_tokens',
    'output_tokens',
    'input_audio_tokens',
    'cache_audio_read_tokens',
    'output_audio_tokens',
    'characters',
    'audio_output_seconds',
    'audio_input_seconds',
    'agent_minutes',
    'telephony_minutes',
]


@dataclass
class UsageExtractorMapping:
    """Mappings from used to build usage."""

    path: ExtractPath
    """Path to the value to extract"""
    dest: UsageField
    """Destination field to store the extracted value.

    If multiple mappings point to the same destination, the values are summed.
    """
    required: bool = True
    """Whether the value is required to be present in the response"""


@dataclass
class UsageExtractor:
    """Logic for extracting usage information from a response."""

    root: ExtractPath
    """Path to the root of the usage information in the response, generally `usage`."""
    mappings: list[UsageExtractorMapping]
    """Mappings from used to build usage."""
    api_flavor: str = 'default'
    """Name of the API flavor, only needed when a provider has multiple flavors, e.g. OpenAI has `chat` and `responses`."""
    model_path: ExtractPath = 'model'
    """Path to the model name in the response."""

    def extract(self, response_data: Any) -> tuple[str | None, Usage]:
        """Extract model name and usage information from a response.

        Args:
            response_data: The response data to extract usage information from, generally the decoded JSON response.

        Raises:
            ValueError: If no usage information is found at the root.

        Returns:
            tuple[str, Usage]: The extracted model name and usage information.
        """
        model_name = _extract_path(self.model_path, response_data, str, False, [])

        root = self.root
        if isinstance(root, str):
            root = [root]

        usage_obj = cast(dict[str, Any], _extract_path(root, response_data, Mapping, True, []))

        usage = Usage()
        values_set = False
        for mapping in self.mappings:
            value = _extract_path(mapping.path, usage_obj, int, mapping.required, root)
            if value is not None:
                current_value = getattr(usage, mapping.dest) or 0
                setattr(usage, mapping.dest, current_value + value)
                values_set = True
        if not values_set:
            raise ValueError(f'No usage information found at {self.root}')
        return model_name, usage


E = TypeVar('E')


@overload
def _extract_path(
    path: ExtractPath, data: Any, extract_type: type[E], required: Literal[True], data_path: Sequence[str | ArrayMatch]
) -> E: ...


@overload
def _extract_path(
    path: ExtractPath,
    data: Any,
    extract_type: type[E],
    required: Literal[False],
    data_path: Sequence[str | ArrayMatch],
) -> E | None: ...


def _extract_path(
    path: ExtractPath, data: Any, extract_type: type[E], required: bool, data_path: Sequence[str | ArrayMatch]
) -> E | None:
    if isinstance(path, str):
        path = [path]

    *steps, last = path
    last = cast(str, last)

    error_path: list[str | ArrayMatch] = []
    for step in steps:
        error_path.append(step)
        if isinstance(step, ArrayMatch):
            if not _is_sequence(data):
                if required:
                    raise ValueError(
                        f'Expected `{_dot_path(data_path, error_path)}` value to be a sequence, got {_type_name(data)}'
                    )
                else:
                    return None
            if extracted_data := step.extract(data):
                data = extracted_data
            elif required:
                raise ValueError(f'Unable to find item at `{_dot_path(data_path, error_path)}`')
            else:
                return None
        else:
            if not _expect_mapping(data, required, data_path, error_path):
                return None
            try:
                data = data[step]
            except KeyError as e:
                if required:
                    raise ValueError(f'Missing value at `{_dot_path(data_path, error_path)}`') from e
                else:
                    return None

    if data is None and not required:
        return None

    if not _expect_mapping(data, required, data_path, error_path):
        return None

    try:
        value = data[last]
    except KeyError as e:
        if required:
            error_path.append(last)
            raise ValueError(f'Missing value at `{_dot_path(data_path, error_path)}`') from e
        else:
            return None
    else:
        if isinstance(value, extract_type):
            return value
        elif required:
            error_path.append(last)
            raise ValueError(
                f'Expected `{_dot_path(data_path, error_path)}` value to be a {extract_type.__name__}, got {_type_name(value)}'
            )


def _expect_mapping(
    data: Any, required: bool, data_path: Sequence[str | ArrayMatch], error_path: Sequence[str | ArrayMatch]
) -> TypeGuard[Mapping[str, Any]]:
    if _is_mapping(data):
        return True
    if required:
        raise ValueError(f'Expected `{_dot_path(data_path, error_path)}` value to be a dict, got {_type_name(data)}')
    return False


def _is_mapping(item: Any) -> TypeGuard[Mapping[str, Any]]:
    return isinstance(item, Mapping)


def _is_sequence(item: Any) -> TypeGuard[Sequence[Any]]:
    return isinstance(item, Sequence)


def _dot_path(data_path: Sequence[str | ArrayMatch], error_path: Sequence[str | ArrayMatch]) -> str:
    return '.'.join([str(p) for p in data_path] + [str(p) for p in error_path])


def _type_name(v: Any) -> str:
    return 'None' if v is None else type(v).__name__


@dataclass
class AgentVotes:
    """Verifier-agent vote tally recorded by the freshness bot (never a human date)."""

    approve: int
    total: int


@dataclass
class SourceRate:
    """The rate as observed on the vendor page, in the vendor's own unit, for audit."""

    value: Decimal
    unit: Literal['per_kchar', 'per_ksecond']


@dataclass
class Provenance:
    """Freshness provenance for a model's price.

    `verification_status` / `stale` / a coarse confidence label are derived at load time (see
    `voice_prices.confidence`) from `last_verified` plus the provider's `staleness_threshold_days`;
    they are not stored here. This block carries only the raw inputs.
    """

    source: Literal['imported', 'seed'] | None = None
    """Origin of an unverified rate: `imported` (from another catalog) or `seed` (bootstrap).
    A `verified` status is not stored: it is derived from `last_verified` being present."""
    api_backed: bool | None = None
    """Whether this rate is published by the vendor at a machine-readable endpoint.

    True means the rate can be re-read and compared automatically, so a reprice surfaces without a
    human re-reading a pricing page. Absent means it was read off a pricing page, and only a person
    (or the LLM-assisted freshness job) can confirm it still holds. Tri-state so the shipped data
    carries the flag only where it is true.
    """
    last_verified: date | None = None
    """Date the rate was last human-verified. Emitted at build from the model's `prices_checked`."""
    agent_votes: AgentVotes | None = None
    """Verifier-agent tally written by the freshness bot."""
    evidence: str | None = None
    """The exact price string an agent quoted from the vendor page, recorded on a consensus run."""
    source_rate: SourceRate | None = None
    """The observed rate in the vendor's own unit, for audit."""
    estimated_fields: list[str] | None = None
    """Priced fields whose value is the vendor's own published estimate, not the meter they bill on.

    Absent (the normal case) means every rate on the model is a billing rate. When present, a rate
    named here will not reconcile against an invoice: either it is a per-token bill restated per
    minute or per character, or it is a floor the vendor published instead of a rate ("starting
    at $0.005 per minute"), which bounds the invoice from below rather than stating it.

    OpenAI's `gpt-4o-transcribe` is the case this was added for. It bills per token and also
    prints "$0.006 / minute" under a column headed *Estimated cost*, derived from an assumed
    speech density. Both numbers are published; only one is charged.

    Per-field rather than per-model, because the split is per-field: the token rates on that model
    are billed and only `input_audio_kseconds` is estimated. Callers that must match an invoice
    should prefer a field not named here; callers doing capacity planning can use either.
    """


@dataclass
class ModelInfo:
    """Information about an LLM model"""

    id: str
    """Primary unique identifier for the model"""
    match: MatchLogic
    """Boolean logic for matching this model to any identifier which could be used to reference the model in API requests"""
    name: str | None = None
    """Name of the model"""
    description: str | None = None
    """Description of the model"""
    context_window: int | None = None
    """Maximum number of input tokens allowed for this model"""
    price_comments: str | None = None
    """Comments about the pricing of the model, especially challenges in representing the provider's pricing model."""
    pricing_source_url: str | None = None
    """Deep-link (as a string) to the row or anchor on the provider's pricing page that establishes
    this model's price. Per-model; distinct from `Provider.pricing_urls` which is provider-wide.
    """
    free: bool = False
    """The provider charges nothing for this model, so an empty `prices` block is the true rate.

    Distinguishes "free" from "nobody has entered a rate", which are otherwise the same bytes:
    both are a ModelPrice with every field None. When True, a zero `total_price` is correct and
    `unpriced_usage` is empty. When False and the model carries no rate, `unpriced_usage` names
    the fields that found no meter, and the zero should not be billed on.
    """
    deprecated: bool | None = None
    """Flag indicating this model is deprecated by the provider but still functional."""
    provenance: Provenance | None = None
    """Freshness provenance emitted to consumers (source, last_verified, agent_votes, evidence,
    source_rate). Derived status/confidence live in `voice_prices.confidence`."""

    prices: ModelPrice | list[ConditionalPrice] = dataclasses.field(default_factory=list)
    """Set of prices for using this model.

    When multiple `ConditionalPrice`s are used, they are tried last to first to find a pricing model to use.
    E.g. later conditional prices take precedence over earlier ones.

    If no conditional models match the conditions, the first one is used.
    """

    def is_match(self, model_ref: str) -> bool:
        return self.match.is_match(model_ref.lower())

    def get_prices(self, request_timestamp: datetime) -> ModelPrice:
        if isinstance(self.prices, ModelPrice):
            return self.prices
        else:
            # reversed because the last price takes precedence
            for conditional_price in reversed(self.prices):
                if conditional_price.constraint is None or conditional_price.constraint.active(request_timestamp):
                    return conditional_price.prices
            return self.prices[0].prices

    def calc_price(
        self,
        usage: AbstractUsage,
        provider: Provider,
        *,
        genai_request_timestamp: datetime | None = None,
        auto_update_timestamp: datetime | None = None,
    ) -> PriceCalculation:
        """Calculate the price for the given usage."""
        genai_request_timestamp = genai_request_timestamp or datetime.now(tz=timezone.utc)

        model_price = self.get_prices(genai_request_timestamp)
        price = model_price.calc_price(usage)
        return PriceCalculation(
            input_price=price['input_price'],
            output_price=price['output_price'],
            total_price=price['total_price'],
            breakdown=price['breakdown'],
            applied_voice_multiplier=price['applied_voice_multiplier'],
            model=self,
            provider=provider,
            model_price=model_price,
            auto_update_timestamp=auto_update_timestamp,
            # A free model has no gap to report: the zero IS the rate. Without this, every free
            # model would name its usage fields as unpriced and be indistinguishable from a row
            # nobody has entered a rate for, which is the ambiguity `free` exists to remove.
            unpriced_usage=() if self.free else price['unpriced_usage'],
        )

    def summary(self) -> str:
        return f'Model(id={self.id!r}, name={self.name!r}, ...)'


class CalcPrice(TypedDict):
    input_price: Decimal
    output_price: Decimal
    total_price: Decimal
    breakdown: PriceBreakdown
    applied_voice_multiplier: Decimal | None
    unpriced_usage: tuple[str, ...]


# For each `Usage` field, the `ModelPrice` rates that can pay for it, in the order the
# bucket logic in `ModelPrice.calc_price` actually falls back through. A field counts as
# unpriced only when EVERY rate in its chain is absent.
#
# Walking the chain is the whole point. "The matching rate is None" would be wrong: a model
# with no `cache_read_mtok` still prices `cache_read_tokens`, at `input_mtok`, because the
# bucket logic leaves those tokens inside the text-input remainder rather than dropping
# them. 250 models in the catalog have no cache rate; flagging all of them would make this
# field noise, and a noisy warning is a warning nobody reads.
#
# The token chains assume the caller follows the API convention these fields come from,
# where `input_tokens` is the total (audio and cached tokens included) and `output_tokens`
# likewise. A caller that reports audio tokens *outside* the totals gets a conservative
# answer: the audio rate covers the field, so nothing is reported.
_UNPRICED_RATE_CHAINS: dict[str, tuple[str, ...]] = {
    'input_tokens': ('input_mtok',),
    'cache_read_tokens': ('cache_read_mtok', 'input_mtok'),
    'cache_write_tokens': ('cache_write_mtok', 'input_mtok'),
    'input_audio_tokens': ('input_audio_mtok', 'input_mtok'),
    'cache_audio_read_tokens': ('cache_audio_read_mtok', 'cache_read_mtok', 'input_mtok'),
    'output_tokens': ('output_mtok',),
    'output_audio_tokens': ('output_audio_mtok', 'output_mtok'),
    'characters': ('input_kchars',),
    'audio_input_seconds': ('input_audio_kseconds',),
    'audio_output_seconds': ('output_audio_kseconds',),
    'agent_minutes': ('agent_kminutes',),
    'telephony_minutes': ('telephony_kminutes',),
}


@dataclass
class ModelPrice:
    """Set of prices for using a model"""

    input_mtok: Decimal | TieredPrices | None = None
    """price in USD per million uncached text input/prompt token"""

    cache_write_mtok: Decimal | TieredPrices | None = None
    """price in USD per million tokens written to the cache"""
    cache_read_mtok: Decimal | TieredPrices | None = None
    """price in USD per million tokens read from the cache"""

    output_mtok: Decimal | TieredPrices | None = None
    """price in USD per million output/completion tokens"""

    input_audio_mtok: Decimal | TieredPrices | None = None
    """price in USD per million audio input tokens"""
    cache_audio_read_mtok: Decimal | TieredPrices | None = None
    """price in USD per million audio tokens read from the cache"""
    output_audio_mtok: Decimal | TieredPrices | None = None
    """price in USD per million output audio tokens"""

    requests_kcount: Decimal | None = None
    """price in USD per thousand requests"""

    input_kchars: Decimal | None = None
    """price in USD per 1,000 input characters (TTS text input)"""

    output_audio_kseconds: Decimal | None = None
    """price in USD per 1,000 seconds of generated audio output.

    Reserved for v0.2 (PlayHT, Murf, similar). No v0.1 catalog entry sets this.
    """

    input_audio_kseconds: Decimal | None = None
    """price in USD per 1,000 seconds of input audio (STT audio input).

    ModelPrice puts direction before modality (input_audio_kseconds); Usage puts
    modality before direction (audio_input_seconds). Intentional; mirrors the
    existing output_audio_kseconds / audio_output_seconds pair.
    """

    telephony_kminutes: Decimal | None = None
    """USD per 1,000 connected call minutes on the phone network.

    Separate from `agent_kminutes` on purpose: a bundled agent minute already contains
    speech-to-text, the model and text-to-speech, while a carrier minute is the call leg
    itself. A phone call on a bundled platform pays both.
    """

    agent_kminutes: Decimal | None = None
    """price in USD per 1,000 minutes of bundled voice-agent session time.

    Set only by platforms that sell one blended per-minute rate covering STT, LLM,
    TTS and orchestration together. A model priced this way is filed under the
    `agent` modality and is deliberately not comparable with the component rates:
    what the bundle contains is the platform's choice and is rarely disclosed.
    """

    voice_multipliers: dict[str, Decimal] | None = None
    """Multiplicative adjustments keyed by voice class.

    Scales `input_kchars` + `output_audio_kseconds` only (not token-based fields, requests, or
    cached buckets). Must include a `default` key. See engine semantics for fallback behavior.
    """

    def calc_price(self, usage: AbstractUsage) -> CalcPrice:
        """Calculate the price of usage in USD with this model price."""
        breakdown = PriceBreakdown()

        total_input_tokens = usage.input_tokens or 0

        cache_read_tokens = usage.cache_read_tokens or 0
        cache_write_tokens = usage.cache_write_tokens or 0
        cache_audio_read_tokens = usage.cache_audio_read_tokens or 0
        input_audio_tokens = usage.input_audio_tokens or 0
        output_audio_tokens = usage.output_audio_tokens or 0

        # Parent/child bucket logic for token fields. See pre-refactor comment in git
        # history for the full worked example; the algorithm is unchanged here.
        priced_cache_audio_read_tokens = cache_audio_read_tokens if self.cache_audio_read_mtok is not None else 0
        cache_audio_read_tokens_priced_as_cache_read = (
            cache_audio_read_tokens if self.cache_audio_read_mtok is None and self.cache_read_mtok is not None else 0
        )

        priced_audio_input_tokens = 0
        if self.input_audio_mtok is not None:
            priced_audio_input_tokens = (
                input_audio_tokens - priced_cache_audio_read_tokens - cache_audio_read_tokens_priced_as_cache_read
            )

        if priced_audio_input_tokens < 0:  # pragma: no cover
            raise ValueError('cache_audio_read_tokens cannot be greater than input_audio_tokens')

        priced_cache_read_tokens = 0
        if self.cache_read_mtok is not None:
            priced_cache_read_tokens = cache_read_tokens - priced_cache_audio_read_tokens

        if priced_cache_read_tokens < 0:  # pragma: no cover
            raise ValueError('cache_audio_read_tokens cannot be greater than cache_read_tokens')

        priced_cache_write_tokens = cache_write_tokens if self.cache_write_mtok is not None else 0

        priced_text_input_tokens = 0
        if self.input_mtok is not None:
            priced_text_input_tokens = (
                total_input_tokens
                - priced_cache_read_tokens
                - priced_cache_write_tokens
                - priced_audio_input_tokens
                - priced_cache_audio_read_tokens
            )

        if priced_text_input_tokens < 0:  # pragma: no cover
            raise ValueError('Uncached text input tokens cannot be negative')

        # Token contributions into the breakdown
        breakdown.input_tokens = calc_mtok_price(self.input_mtok, priced_text_input_tokens, total_input_tokens)
        breakdown.cache_write_tokens = calc_mtok_price(
            self.cache_write_mtok, priced_cache_write_tokens, total_input_tokens
        )
        breakdown.cache_read_tokens = calc_mtok_price(
            self.cache_read_mtok, priced_cache_read_tokens, total_input_tokens
        )
        breakdown.input_audio_tokens = calc_mtok_price(
            self.input_audio_mtok, priced_audio_input_tokens, total_input_tokens
        )
        breakdown.cache_audio_read_tokens = calc_mtok_price(
            self.cache_audio_read_mtok, priced_cache_audio_read_tokens, total_input_tokens
        )

        priced_text_output_tokens = 0
        if self.output_mtok is not None:
            priced_text_output_tokens = (usage.output_tokens or 0) - (
                output_audio_tokens if self.output_audio_mtok is not None else 0
            )

        if priced_text_output_tokens < 0:  # pragma: no cover
            raise ValueError('output_audio_tokens cannot be greater than output_tokens')

        breakdown.output_tokens = calc_mtok_price(self.output_mtok, priced_text_output_tokens, total_input_tokens)
        breakdown.output_audio_tokens = calc_mtok_price(
            self.output_audio_mtok, usage.output_audio_tokens, total_input_tokens
        )

        # Per-1000-unit contributions: characters (TTS), audio seconds in both directions (STT),
        # bundled agent minutes, and carrier minutes. Five meters, one shape, so they go through
        # `_read_quantity` / `_per_thousand` rather than five near-identical branches.
        characters = _read_quantity(usage, 'characters')
        audio_output_seconds = _read_quantity(usage, 'audio_output_seconds')
        audio_input_seconds = _read_quantity(usage, 'audio_input_seconds')
        agent_minutes = _read_quantity(usage, 'agent_minutes')
        telephony_minutes = _read_quantity(usage, 'telephony_minutes')

        breakdown.input_kchars = _per_thousand(self.input_kchars, characters)
        breakdown.output_audio_kseconds = _per_thousand(self.output_audio_kseconds, audio_output_seconds)
        breakdown.input_audio_kseconds = _per_thousand(self.input_audio_kseconds, audio_input_seconds)
        breakdown.agent_kminutes = _per_thousand(self.agent_kminutes, agent_minutes)
        breakdown.telephony_kminutes = _per_thousand(self.telephony_kminutes, telephony_minutes)

        # Voice multiplier resolution and per-direction adjustment
        applied_voice_multiplier: Decimal | None = None
        if self.voice_multipliers is not None:
            voice_class = getattr(usage, 'voice_class', None)
            if voice_class is None:
                applied_voice_multiplier = self.voice_multipliers['default']
            elif voice_class in self.voice_multipliers:
                applied_voice_multiplier = self.voice_multipliers[voice_class]
            else:
                warnings.warn(
                    f'Unknown voice_class {voice_class!r}; falling back to default multiplier. '
                    f'Known classes: {sorted(self.voice_multipliers)}.',
                    stacklevel=2,
                )
                applied_voice_multiplier = self.voice_multipliers['default']

        if applied_voice_multiplier is not None:
            delta = applied_voice_multiplier - Decimal(1)
            breakdown.voice_class_input_adjustment = breakdown.input_kchars * delta
            breakdown.voice_class_output_adjustment = breakdown.output_audio_kseconds * delta

        # Requests
        if self.requests_kcount is not None:
            breakdown.requests = self.requests_kcount / Decimal(1000)

        # Usage the caller supplied that no rate here could pay for. Computed from the same
        # values the branches above consumed, so it cannot drift from what was actually priced.
        supplied: dict[str, Decimal | int] = {
            'input_tokens': total_input_tokens,
            'cache_read_tokens': cache_read_tokens,
            'cache_write_tokens': cache_write_tokens,
            'input_audio_tokens': input_audio_tokens,
            'cache_audio_read_tokens': cache_audio_read_tokens,
            'output_tokens': usage.output_tokens or 0,
            'output_audio_tokens': output_audio_tokens,
            'characters': characters,
            'audio_input_seconds': audio_input_seconds,
            'audio_output_seconds': audio_output_seconds,
            'agent_minutes': agent_minutes,
            'telephony_minutes': telephony_minutes,
        }
        unpriced_usage = tuple(
            field
            for field, chain in _UNPRICED_RATE_CHAINS.items()
            if supplied.get(field) and all(getattr(self, rate, None) is None for rate in chain)
        )

        # Assemble input/output/total from the breakdown so the invariant holds by construction
        input_price = (
            breakdown.input_tokens
            + breakdown.cache_read_tokens
            + breakdown.cache_write_tokens
            + breakdown.input_audio_tokens
            + breakdown.cache_audio_read_tokens
            + breakdown.input_kchars
            + breakdown.voice_class_input_adjustment
            + breakdown.input_audio_kseconds
        )
        output_price = (
            breakdown.output_tokens
            + breakdown.output_audio_tokens
            + breakdown.output_audio_kseconds
            + breakdown.voice_class_output_adjustment
        )
        total_price = (
            input_price + output_price + breakdown.requests + breakdown.agent_kminutes + breakdown.telephony_kminutes
        )

        return {
            'input_price': input_price,
            'output_price': output_price,
            'total_price': total_price,
            'breakdown': breakdown,
            'applied_voice_multiplier': applied_voice_multiplier,
            'unpriced_usage': unpriced_usage,
        }

    def __str__(self) -> str:
        parts: list[str] = []
        for field in dataclasses.fields(self):
            value = getattr(self, field.name)
            if value is not None:
                if field.name == 'requests_kcount':
                    parts.append(f'${value} / K requests')
                else:
                    name = field.name.replace('_mtok', '').replace('_', ' ')
                    if isinstance(value, TieredPrices):
                        parts.append(f'${value.base}/{name} MTok (+tiers)')
                    else:
                        parts.append(f'${value}/{name} MTok')

        return ', '.join(parts)


def _read_quantity(usage: AbstractUsage, field: str) -> Decimal:
    """A usage quantity as a Decimal, or zero when absent.

    Read with getattr so an `AbstractUsage` implementation predating a field (a user-defined
    dataclass, say) keeps working instead of raising. The explicit Decimal() coercion is the
    contract that lets callers pass a plain int for a Decimal-typed field, which several do
    (see test_int_audio_*_seconds_prices_correctly).
    """
    raw = getattr(usage, field, None)
    return Decimal(0) if raw is None else Decimal(raw)


def _per_thousand(rate: Decimal | None, quantity: Decimal) -> Decimal:
    """USD for `quantity` units at `rate` per 1,000 of them.

    Zero when the model has no such rate, which is what makes a missing meter a silent zero;
    `PriceCalculation.unpriced_usage` is what tells the caller that happened.
    """
    if rate is None or quantity <= 0:
        return Decimal(0)
    return rate * quantity / Decimal(1000)


def calc_mtok_price(
    field_mtok: Decimal | TieredPrices | None, token_count: int | None, total_input_tokens: int
) -> Decimal:
    """Calculate the price for a given number of tokens based on the price in USD per million tokens (mtok).

    For tiered pricing, uses threshold-based pricing where crossing a tier applies that rate to ALL tokens.
    This is the industry standard used by Anthropic, Google, OpenAI, and most other providers.

    Args:
        field_mtok: Price per million tokens, either flat rate or tiered
        token_count: Number of tokens of this specific type to price
        total_input_tokens: Total input tokens for tier determination (used only for tiered pricing)
    """
    if field_mtok is None or token_count is None:
        return Decimal(0)

    if isinstance(field_mtok, TieredPrices):
        # Threshold-based pricing: tier is determined by total_input_tokens
        # Find the highest tier that applies based on total input tokens
        # When total_input_tokens is 0, no tier condition is met, so base rate is used
        applicable_price = field_mtok.base
        for tier in reversed(field_mtok.tiers):
            if total_input_tokens > tier.start:
                applicable_price = tier.price
                break
        price = applicable_price * token_count
    else:
        price = field_mtok * token_count
    return price / 1_000_000


@dataclass
class TieredPrices:
    """Pricing model when the amount paid varies by number of tokens.

    Uses threshold-based pricing where crossing a tier applies that rate to ALL tokens.
    This is the industry standard "cliff" model used by most providers (Anthropic, Google, OpenAI, etc.).

    Example: For a tier starting at 200K tokens:
    - Using 199,999 tokens: all tokens pay base rate
    - Using 200,001 tokens: all tokens pay tier rate (not just the tokens above 200K)
    """

    base: Decimal
    """Base price in USD per million tokens, e.g. price until the first tier."""
    tiers: list[Tier]
    """Extra price tiers."""

    def __post_init__(self) -> None:
        """Ensure tiers are sorted in ascending order by start threshold."""
        self.tiers.sort(key=lambda tier: tier.start)


@dataclass
class Tier:
    """Price tier"""

    start: int
    """Start of the tier"""
    price: Decimal
    """Price for this tier"""


@dataclass
class ConditionalPrice:
    """Pricing together with constraints that define when those prices should be used.

    The last price active price (price where the constraints are met) is used.
    """

    constraint: StartDateConstraint | TimeOfDateConstraint | None = None
    """Timestamp when this price starts, None means this price is always valid."""

    _: dataclasses.KW_ONLY

    prices: ModelPrice
    """Prices for this condition."""


@dataclass
class StartDateConstraint:
    """Constraint that defines when this price starts, e.g. when a new price is introduced."""

    start_date: date
    """Date when this price starts"""

    def active(self, request_timestamp: datetime) -> bool:
        return request_timestamp.date() >= self.start_date


@dataclass
class TimeOfDateConstraint:
    """Constraint that defines a daily interval when a price applies, useful for off-peak pricing like deepseek."""

    start_time: time
    """Start time of the interval."""
    end_time: time
    """End time of the interval."""

    def active(self, request_timestamp: datetime) -> bool:
        return self.start_time <= request_timestamp.timetz() < self.end_time


@dataclass
class ClauseStartsWith:
    starts_with: str

    def is_match(self, text: str) -> bool:
        return text.lower().startswith(self.starts_with.lower())


@dataclass
class ClauseEndsWith:
    ends_with: str

    def is_match(self, text: str) -> bool:
        return text.lower().endswith(self.ends_with.lower())


@dataclass
class ClauseContains:
    contains: str

    def is_match(self, text: str) -> bool:
        return self.contains.lower() in text.lower()


@dataclass
class ClauseRegex:
    regex: str

    def is_match(self, text: str) -> bool:
        return bool(re.search(self.regex, text))


@dataclass
class ClauseEquals:
    equals: str

    def is_match(self, text: str) -> bool:
        return text.lower() == self.equals.lower()


@dataclass
class ClauseOr:
    or_: Annotated[list[MatchLogic], pydantic.Field(validation_alias='or')]

    def is_match(self, text: str) -> bool:
        return any(clause.is_match(text) for clause in self.or_)


@dataclass
class ClauseAnd:
    and_: Annotated[list[MatchLogic], pydantic.Field(validation_alias='and')]

    def is_match(self, text: str) -> bool:
        return all(clause.is_match(text) for clause in self.and_)


providers_schema = pydantic.TypeAdapter(list[Provider], config=pydantic.ConfigDict(defer_build=True))
