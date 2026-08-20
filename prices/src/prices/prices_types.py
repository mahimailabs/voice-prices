from __future__ import annotations

import re
from datetime import date, time
from decimal import Decimal
from typing import Annotated, Any, Literal

from annotated_types import Gt, MaxLen
from pydantic import (
    AfterValidator,
    BaseModel,
    Discriminator,
    Field,
    HttpUrl,
    PlainSerializer,
    Tag,
    TypeAdapter,
    ValidationInfo,
    WithJsonSchema,
    field_validator,
    model_validator,
)

from .utils import check_unique


class _Model(BaseModel, extra='forbid', use_attribute_docstrings=True):
    """Custom abstract based model with config"""


IdField = Annotated[str, MaxLen(100), Field(pattern=r'^\S+$')]
NameField = Annotated[str, MaxLen(100)]
DescriptionField = Annotated[str, MaxLen(1000)]


class Provider(_Model):
    """Information about an LLM inference provider"""

    id: IdField
    """Unique identifier for the provider"""
    name: NameField
    """Common name of the organization"""
    pricing_urls: list[HttpUrl] | None = None
    """Link to pricing page for the provider"""
    pricing_feed_url: HttpUrl | None = Field(default=None, exclude=True)
    """Machine-readable pricing endpoint this provider publishes, in the format documented at
    `docs/pricing-feed.mdx`.

    When set, `make feed-sync` polls it and reports any rate that has moved away from the catalog.
    Build-side only (excluded from data.json): consumers get the resulting rates, not the plumbing.
    """
    api_pattern: str
    """Pattern to identify provider via HTTP API URL."""
    description: DescriptionField | None = None
    """Description of the provider"""
    price_comments: DescriptionField | None = None
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
    """Recommended maximum age (in days) for `prices_checked` on this provider's models before consumers should
    re-verify against `pricing_source_url`.

    voice-prices itself does not warn or fail on stale entries; this is metadata for consumers like VoiceGateway
    to drive their own freshness UX. Default 60.
    """
    models: list[ModelInfo]
    """List of models supported by this provider"""

    @field_validator('pricing_feed_url', mode='after')
    @classmethod
    def validate_pricing_feed_url(cls, url: HttpUrl | None) -> HttpUrl | None:
        """Enforce the two properties the endpoint contract actually depends on.

        Plaintext matters here more than usual: this catalog's whole value is that its numbers are
        right, so an endpoint whose response can be rewritten in transit is worse than no endpoint.
        Credentials matter because provider files are public and end up in a published schema, so a
        URL carrying a password would leak it the moment it was merged.
        """
        if url is None:
            return url
        if url.scheme != 'https':
            raise ValueError('pricing_feed_url must be https, so a polled rate cannot be rewritten in transit')
        if url.username or url.password:
            raise ValueError('pricing_feed_url must not embed credentials: provider files are public')
        return url

    @field_validator('extractors', mode='after')
    @classmethod
    def validate_extract(cls, extract: list[UsageExtractor] | None) -> list[UsageExtractor] | None:
        if extract:
            unique_flavors: set[str] = set()
            duplicates: list[str] = []
            for extraction in extract:
                if extraction.api_flavor in unique_flavors:
                    duplicates.append(extraction.api_flavor)
                unique_flavors.add(extraction.api_flavor)
            if duplicates:
                raise ValueError(f'Duplicate extraction api_flavor: {duplicates}')
        return extract

    @field_validator('models', mode='after')
    @classmethod
    def validate_id(cls, models: list[ModelInfo]) -> list[ModelInfo]:
        unique_ids: set[str] = set()
        duplicates: list[str] = []
        for model in models:
            model_ids = {model.id, *get_model_ids(model.match)}
            other_matches = [m.id for m in models if m != model and any(m.is_match(model_id) for model_id in model_ids)]
            if other_matches:
                raise AssertionError(f'Model `{model.id}` matches other model ids: {other_matches}')
            if model.id in unique_ids:
                duplicates.append(model.id)
            unique_ids.add(model.id)

        if duplicates:
            raise ValueError(f'Duplicate model ids: {duplicates}')

        # check models are sorted by ID
        ids = [model.id for model in models]
        # try to find the first model id with the wrong position and point directly to that to fix
        sorted_ids = sorted(ids)
        for current_index, current_id in enumerate(ids):
            for expected_index, expected_id in enumerate(sorted_ids):
                if current_id == expected_id and current_index != expected_index:
                    msg = f'Models are not sorted by ID: move `{current_id}` {current_index} -> {expected_index}'
                    if expected_index > 0:
                        msg += f' after `{sorted_ids[expected_index - 1]}`'
                    raise ValueError(msg)

        return models

    def find_model(self, model_id: str) -> ModelInfo | None:
        for model in self.models:
            if model.is_match(model_id):
                return model
        return None

    def exclude_removed(self):
        self.models[:] = [model for model in self.models if not model.removed]

    def exclude_free(self):
        self.models[:] = [model for model in self.models if not model.is_free()]


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
]


class UsageExtractorMapping(_Model):
    """Mappings from used to build usage."""

    path: ExtractPath
    """Path to the value to extract"""
    dest: UsageField
    """Destination field to store the extracted value.

    If multiple mappings point to the same destination, the values are summed.
    """
    required: bool = True
    """Whether the value is required to be present in the response"""


class UsageExtractor(_Model):
    """Logic for extracting usage information from a response."""

    api_flavor: str = 'default'
    """Name of the API flavor, only needed when a provider has multiple flavors, e.g. OpenAI has `chat` and `responses`."""
    root: ExtractPath
    """Path to the root of the usage information in the response, generally `usage`."""
    model_path: ExtractPath = 'model'
    """Path to the model name in the response.

    Almost all APIs return this in the 'model' field, hence the default value.
    """
    mappings: list[UsageExtractorMapping]
    """Mappings from used to build usage."""


class AgentVotes(_Model):
    """Verifier-agent vote tally recorded by the freshness bot (never a human date)."""

    approve: int
    """Number of independent agents that approved the extracted rate."""
    total: int
    """Total number of agents that voted."""


class SourceRate(_Model):
    """The rate as observed on the vendor page, in the vendor's own unit, for audit."""

    value: DollarPrice
    """Observed rate value in the source unit."""
    unit: Literal['per_kchar', 'per_ksecond']
    """Source unit: `per_kchar` maps to `input_kchars` (TTS), `per_ksecond` maps to `input_audio_kseconds` (STT)."""


class Provenance(_Model):
    """Freshness provenance for a model's price.

    Consumer-facing verification signal. `verification_status` / `stale` / a coarse confidence
    label are derived at load time (in the package) from `last_verified` plus the provider's
    `staleness_threshold_days`; they are not stored here. This block carries only the raw inputs.
    """

    source: Literal['imported', 'seed'] | None = None
    """Origin of an unverified rate: `imported` (from another catalog, e.g. PriceToken) or `seed`
    (bootstrap). A `verified` status is not stored: it is derived from `last_verified` being present."""
    api_backed: bool | None = None
    """Whether this rate is published by the vendor at a machine-readable endpoint.

    Tri-state so that only `true` is ever written: a `false` on every one of the twelve hundred
    models that are not API-backed would be a byte of noise per model and no information, since
    absent already means "read off a pricing page".

    True means the rate can be re-read and compared automatically, so a reprice surfaces without a
    human re-reading a pricing page. False means it was read off a pricing page and only a person
    (or the LLM-assisted freshness job) can confirm it still holds. This is the split the docs
    expose, and the reason `docs/pricing-feed.mdx` exists.
    """
    last_verified: date | None = None
    """Date the rate was last human-verified. Populated at BUILD from the model's `prices_checked`
    (which is itself excluded from output); never set by hand in YAML and never written by the bot."""
    agent_votes: AgentVotes | None = None
    """Verifier-agent tally written by the freshness bot."""
    evidence: str | None = None
    """The exact price string an agent quoted from the vendor page, recorded on a consensus run
    (the freshness bot writes this only when a verifier agent ran alongside the extractor)."""
    source_rate: SourceRate | None = None
    """The observed rate in the vendor's own unit, for audit."""
    estimated_fields: list[str] | None = None
    """Priced fields whose value is the vendor's own published *estimate*, not the meter they bill on.

    Absent (the normal case) means every rate on the model is a billing rate.

    This exists because a vendor can publish two numbers for the same model in two different
    units, and only one of them is the invoice. OpenAI prices `gpt-4o-transcribe` per token and
    also prints "$0.006 / minute" in a column headed *Estimated cost*, derived from an assumed
    speech density. Both numbers are real and published; only the token rate is charged. Storing
    the per-minute figure without saying so would let a consumer bill from it and quietly disagree
    with the invoice, which is the same class of error as the silent zero it was added to fix.

    Per-field rather than a whole-row boolean, because the distinction is per-field: on
    `gpt-4o-transcribe` the token rates are billed and only `input_audio_kseconds` is estimated.
    A row-level flag would misdescribe both halves.
    """

    @field_validator('estimated_fields', mode='after')
    @classmethod
    def validate_estimated_fields(cls, fields: list[str] | None) -> list[str] | None:
        """Names must be real priced fields, so a typo cannot silently claim nothing."""
        if fields is None:
            return None
        if not fields:
            raise ValueError('`estimated_fields` must be omitted rather than empty')
        known = set(ModelPrice.model_fields)
        if unknown := sorted(set(fields) - known):
            raise ValueError(f'`estimated_fields` names fields that do not exist on ModelPrice: {unknown}')
        if len(set(fields)) != len(fields):
            raise ValueError('`estimated_fields` contains duplicates')
        return fields


class ModelInfo(_Model):
    """Information about an LLM model"""

    id: IdField
    """Primary unique identifier for the model"""
    name: NameField | None = None
    """Name of the model"""
    description: DescriptionField | None = None
    """Description of the model"""
    match: MatchLogic
    """Boolean logic for matching this model to any identifier which could be used to reference the model in API requests"""
    context_window: int | None = None
    """Maximum number of input tokens allowed for this model"""
    price_comments: DescriptionField | None = None
    """Comments about the pricing of the model, especially challenges in representing the provider's pricing model."""
    pricing_source_url: HttpUrl | None = None
    """Deep-link to the row or anchor on the provider's pricing page that establishes this model's price.

    Per-model, distinct from `Provider.pricing_urls` (which is too coarse for TTS pages that list
    dozens of models in one table).
    """
    prices: ModelPrice | list[ConditionalPrice]
    """Set of prices for using this model.

    When multiple `ConditionalPrice`s are used, they are tried last to first to find a pricing model to use.
    E.g. later conditional prices take precedence over earlier ones.

    If no conditional models match the conditions, the first one is used.
    """
    price_discrepancies: dict[str, Any] | None = Field(default=None, exclude=True)
    """List of price discrepancies based on external sources."""
    prices_checked: date | None = Field(default=None, exclude=True)
    """Date indicating when the prices were last checked for discrepancies."""
    provenance: Provenance | None = None
    """Freshness provenance emitted to consumers. `last_verified` is build-populated from
    `prices_checked` and `source` marks the origin; the freshness bot may add consensus details.
    See `Provenance`. (`data_slim` keeps only `source` + `last_verified`.)"""
    collapse: bool = Field(default=True, exclude=True)
    """Flag indicating whether this price should be collapsed into other prices."""
    deprecated: bool | None = None
    """Flag indicating this model is deprecated by the provider but still functional."""
    removed: bool = Field(default=False, exclude=True)
    """Flag indicating this model has been removed and is no longer available. Excluded from data.json."""

    def is_match(self, model_id: str) -> bool:
        return self.match.is_match(model_id)

    @field_validator('prices_checked', mode='after')
    @classmethod
    def validate_prices_checked(cls, prices_checked: date | None, info: ValidationInfo) -> date | None:
        if prices_checked is not None and info.data.get('price_discrepancies'):
            raise ValueError('`price_discrepancies` should be removed when `prices_checked` is set')
        return prices_checked

    @model_validator(mode='after')
    def validate_estimated_fields_are_priced(self) -> ModelInfo:
        """A field cannot be flagged as an estimate unless this model actually carries it.

        Without this, deleting a rate and leaving the flag behind turns the provenance into a
        claim about a price that is not there, which is worse than no claim at all.
        """
        named = (self.provenance.estimated_fields if self.provenance else None) or []
        if not named:
            return self
        price_sets = self.prices if isinstance(self.prices, list) else [self.prices]
        priced: set[str] = set()
        for entry in price_sets:
            model_price = entry.prices if isinstance(entry, ConditionalPrice) else entry
            priced |= {name for name in ModelPrice.model_fields if getattr(model_price, name, None) is not None}
        if missing := sorted(set(named) - priced):
            raise ValueError(
                f'`provenance.estimated_fields` names {missing}, which this model does not price. '
                'Remove the name, or add the rate it refers to.'
            )
        return self

    @field_validator('prices', mode='after')
    @classmethod
    def prices_not_empty(cls, prices: ModelPrice | list[ConditionalPrice]) -> ModelPrice | list[ConditionalPrice]:
        if isinstance(prices, list):
            if len(prices) == 0:
                raise ValueError('model prices may not be empty')
            if sum(p.constraint is None for p in prices) != 1:
                raise ValueError('When multiple prices are provided, exactly one price must not have a constraint')
        return prices

    def is_free(self) -> bool:
        if isinstance(self.prices, list):
            return all(price.prices.is_free() for price in self.prices)
        else:
            return self.prices.is_free()


def serialize_decimal(v: Decimal) -> float | int:
    return float(v) if v % 1 != 0 else int(v)


DollarPrice = Annotated[
    Decimal,
    Gt(0),
    WithJsonSchema({'type': 'number'}),
    PlainSerializer(serialize_decimal, return_type=float | int, when_used='json'),
]

VoiceMultiplier = Annotated[
    Decimal,
    Gt(0),
    WithJsonSchema({'type': 'number'}),
    PlainSerializer(serialize_decimal, return_type=float | int, when_used='json'),
]


def _require_default_key(value: dict[str, Decimal]) -> dict[str, Decimal]:
    if 'default' not in value:
        raise ValueError("voice_multipliers must include a 'default' key")
    return value


VoiceMultipliers = Annotated[
    dict[str, VoiceMultiplier],
    AfterValidator(_require_default_key),
]


class ModelPrice(_Model):
    """Set of prices for using a model"""

    input_mtok: DollarPrice | TieredPrices | None = None
    """price in USD per million uncached text input/prompt token"""

    cache_write_mtok: DollarPrice | TieredPrices | None = None
    """price in USD per million tokens written to the cache"""
    cache_read_mtok: DollarPrice | TieredPrices | None = None
    """price in USD per million tokens read from the cache"""

    output_mtok: DollarPrice | TieredPrices | None = None
    """price in USD per million output/completion tokens"""

    input_audio_mtok: DollarPrice | TieredPrices | None = None
    """price in USD per million audio input tokens"""
    cache_audio_read_mtok: DollarPrice | TieredPrices | None = None
    """price in USD per million audio tokens read from the cache"""
    output_audio_mtok: DollarPrice | TieredPrices | None = None
    """price in USD per million output audio tokens"""

    requests_kcount: DollarPrice | None = None
    """price in USD per thousand requests"""

    input_kchars: DollarPrice | None = None
    """price in USD per 1,000 input characters (TTS text input)"""

    output_audio_kseconds: DollarPrice | None = None
    """price in USD per 1,000 seconds of generated audio output.

    Reserved for v0.2 (PlayHT, Murf, similar). No v0.1 catalog entry sets this.
    """

    input_audio_kseconds: DollarPrice | None = None
    """price in USD per 1,000 seconds of input audio (STT audio input).

    ModelPrice puts direction before modality (input_audio_kseconds); Usage puts
    modality before direction (audio_input_seconds). Intentional; mirrors the
    existing output_audio_kseconds / audio_output_seconds pair.
    """

    agent_kminutes: DollarPrice | None = None
    """price in USD per 1,000 minutes of bundled voice-agent session time.

    Set only by platforms that sell one blended per-minute rate covering STT, LLM, TTS
    and orchestration together (Vapi, Retell, Bland, ElevenLabs Agents and similar). A
    model priced this way is filed under the `agent` modality.

    Deliberately not comparable with the component rates elsewhere in this catalog:
    what the bundle contains is the platform's choice and is rarely disclosed, so the
    number answers "what does a minute cost me here", not "what does the speech cost".
    """

    voice_multipliers: VoiceMultipliers | None = None
    """Multiplicative adjustments to the priced fields above, keyed by voice class.

    The multiplier scales the summed cost of `input_kchars` and `output_audio_kseconds`
    only (not token-based fields, request counts, or cached buckets). Must include a
    `default` key; other keys are free-form (e.g. `library`, `premium`).
    """

    def is_free(self) -> bool:
        """Whether all values are zero or unset"""
        for field_name in self.__pydantic_fields__:
            if getattr(self, field_name):
                return False
        return True

    @model_validator(mode='after')
    def validate_voice_multipliers_have_scalable_field(self) -> ModelPrice:
        if self.voice_multipliers is not None:
            scalable = self.input_kchars is not None or self.output_audio_kseconds is not None
            if not scalable:
                raise ValueError(
                    'voice_multipliers requires at least one scalable priced field '
                    '(input_kchars or output_audio_kseconds). The engine only scales character '
                    'and audio-second priced fields in the TTS output direction; '
                    'input_audio_mtok / output_audio_mtok (token-based) and input_audio_kseconds '
                    '(STT input duration) are intentionally multiplier-exempt. '
                    'Language-tier multipliers for STT were deferred from the v0.x design.'
                )
        return self


class TieredPrices(_Model):
    """Pricing model when the amount paid varies by number of tokens.

    Uses threshold-based pricing where *input tokens* crossing a tier applies that rate to ALL tokens of this type.
    This is the industry standard "cliff" model used by most providers (Anthropic, Google, OpenAI, etc.).

    Example: For a tier starting at 200K tokens:
    - Using 199,999 tokens: all tokens pay base rate
    - Using 200,001 tokens: all tokens pay tier rate (not just the tokens above 200K)
    """

    base: DollarPrice
    """Base price in USD per million tokens, e.g. price until the first tier."""
    tiers: list[Tier]
    """Extra price tiers."""

    @field_validator('tiers', mode='after')
    @classmethod
    def tiers_assending(cls, data: list[Tier]) -> list[Tier]:
        if data != sorted(data, key=lambda t: t.start):
            raise ValueError('Tiers must be in ascending order by start')
        return data


class Tier(_Model):
    """Price tier"""

    start: int
    """Start of the tier"""
    price: DollarPrice
    """Price for this tier"""


class ConditionalPrice(_Model):
    """Pricing together with constraints that define when those prices should be used.

    The last price active price (price where the constraints are met) is used.
    """

    constraint: StartDateConstraint | TimeOfDateConstraint | None = None
    """Timestamp when this price starts, None means this price is always valid."""
    prices: ModelPrice
    """Prices for this condition."""


class StartDateConstraint(_Model):
    """Constraint that defines when this price starts, e.g. when a new price is introduced."""

    start_date: date
    """Date when this price starts"""


class TimeOfDateConstraint(_Model):
    """Constraint that defines a daily interval when a price applies, useful for off-peak pricing like deepseek."""

    start_time: time
    """Start time of the interval."""
    end_time: time
    """End time of the interval."""

    @field_validator('start_time', 'end_time', mode='after')
    @classmethod
    def enforce_tz(cls, time_of_date: time) -> time:
        if time_of_date.tzinfo is None:
            raise ValueError('Times must be timezone aware')
        return time_of_date


class ClauseStartsWith(_Model):
    starts_with: str

    def is_match(self, text: str) -> bool:
        return text.lower().startswith(self.starts_with.lower())


class ClauseEndsWith(_Model):
    ends_with: str

    def is_match(self, text: str) -> bool:
        return text.lower().endswith(self.ends_with.lower())


class ClauseContains(_Model):
    contains: str

    def is_match(self, text: str) -> bool:
        return self.contains.lower() in text.lower()


class ClauseRegex(_Model):
    regex: re.Pattern[str]

    def is_match(self, text: str) -> bool:
        return bool(self.regex.search(text))


class ClauseEquals(_Model):
    equals: str

    def is_match(self, text: str) -> bool:
        return text.lower() == self.equals.lower()


class ClauseOr(_Model, populate_by_name=True):
    or_: Annotated[list[MatchLogic], AfterValidator(check_unique)] = Field(alias='or')

    def is_match(self, text: str) -> bool:
        return any(clause.is_match(text) for clause in self.or_)


class ClauseAnd(_Model, populate_by_name=True):
    and_: Annotated[list[MatchLogic], AfterValidator(check_unique)] = Field(alias='and')

    def is_match(self, text: str) -> bool:
        return all(clause.is_match(text) for clause in self.and_)


def clause_discriminator(v: Any) -> str | None:
    if isinstance(v, dict):
        # return the first key
        return next(iter(v))  # pyright: ignore[reportUnknownVariableType,reportUnknownArgumentType]
    elif isinstance(v, BaseModel):
        tag = next(iter(v.__pydantic_fields__))
        if tag.endswith('_'):
            tag = tag[:-1]
        return tag
    else:
        return None


MatchLogic = Annotated[
    Annotated[ClauseStartsWith, Tag('starts_with')]
    | Annotated[ClauseEndsWith, Tag('ends_with')]
    | Annotated[ClauseContains, Tag('contains')]
    | Annotated[ClauseRegex, Tag('regex')]
    | Annotated[ClauseEquals, Tag('equals')]
    | Annotated[ClauseOr, Tag('or')]
    | Annotated[ClauseAnd, Tag('and')],
    Discriminator(clause_discriminator),
]
match_logic_schema: TypeAdapter[MatchLogic] = TypeAdapter(MatchLogic)


class ArrayMatch(_Model):
    type: Literal['array-match']
    field: str
    match: MatchLogic


def doesnt_end_with_find_item(path: str | list[str | ArrayMatch]) -> str | list[str | ArrayMatch]:
    if isinstance(path, list):
        if not path:
            raise ValueError('ExtractPath should not be empty')
        if isinstance(path[-1], ArrayMatch):
            raise ValueError('ExtractPath should not end with a `ArrayMatch` object')
    return path


ExtractPath = Annotated[str | list[str | ArrayMatch], AfterValidator(doesnt_end_with_find_item)]

providers_schema = TypeAdapter(list[Provider])


def get_model_ids(match: MatchLogic) -> list[str]:
    """Get a list of strings that would match the given MatchLogic."""
    if isinstance(match, ClauseEquals):
        return [match.equals]
    elif isinstance(match, ClauseStartsWith):
        return [match.starts_with]
    elif isinstance(match, ClauseEndsWith):
        return [match.ends_with]
    elif isinstance(match, ClauseContains):
        return [match.contains]
    elif isinstance(match, ClauseRegex):
        return [match.regex.pattern]
    elif isinstance(match, ClauseOr):
        return [id_ for clause in match.or_ for id_ in get_model_ids(clause)]
    else:
        return [id_ for clause in match.and_ for id_ in get_model_ids(clause)]
