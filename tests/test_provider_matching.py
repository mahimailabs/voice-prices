from decimal import Decimal
from typing import Any

import pytest
from inline_snapshot import snapshot

from voice_prices import Usage, calc_price
from voice_prices.data import providers
from voice_prices.data_snapshot import find_provider_by_id


def test_find_providers_by_exact_id_match():
    """Test finding providers by exact ID match."""
    result = find_provider_by_id(providers, 'google')
    assert result is not None
    assert result.id == 'google'

    result = find_provider_by_id(providers, 'anthropic')
    assert result is not None
    assert result.id == 'anthropic'

    result = find_provider_by_id(providers, 'openai')
    assert result is not None
    assert result.id == 'openai'


def test_find_providers_by_provider_match_logic():
    """Test finding providers by provider_match logic."""
    result = find_provider_by_id(providers, 'google-gla')
    assert result is not None
    assert result.id == 'google'

    result = find_provider_by_id(providers, 'google-vertex')
    assert result is not None
    assert result.id == 'google'

    result = find_provider_by_id(providers, 'gemini')
    assert result is not None
    assert result.id == 'google'


def test_case_insensitive_matching():
    """Test case insensitive matching."""
    result = find_provider_by_id(providers, 'GOOGLE-GLA')
    assert result is not None
    assert result.id == 'google'

    result = find_provider_by_id(providers, 'ANTHROPIC')
    assert result is not None
    assert result.id == 'anthropic'


def test_whitespace_handling():
    """Test whitespace handling in provider names."""
    result = find_provider_by_id(providers, '  google-gla  ')
    assert result is not None
    assert result.id == 'google'

    result = find_provider_by_id(providers, 'openai ')
    assert result is not None
    assert result.id == 'openai'


def test_unknown_providers():
    """Test handling of unknown providers."""
    result = find_provider_by_id(providers, 'unknown-provider')
    assert result is None

    result = find_provider_by_id(providers, 'custom-ai')
    assert result is None

    result = find_provider_by_id(providers, 'claude')
    assert result is None

    result = find_provider_by_id(providers, 'gpt')
    assert result is None


@pytest.mark.parametrize(
    'provider_ref,provider_id',
    [
        ('openai', snapshot('openai')),
        ('anthropic', snapshot('anthropic')),
        ('google-gla', snapshot('google')),
        ('bedrock', snapshot('aws')),
        ('amazon', snapshot('aws')),
        ('google-vertex', snapshot('google')),
        ('groq', snapshot('groq')),
        ('gemini', snapshot('google')),
        ('mistral_ai', snapshot('mistral')),
        ('openrouter', snapshot('openrouter')),
        ('azure', snapshot('azure')),
        ('gcp.vertex.agent', snapshot('google')),
        ('perplexity', snapshot('perplexity')),
        ('Google', snapshot('google')),
        ('vertex_ai', snapshot('google')),
        ('google', snapshot('google')),
        ('xai', snapshot('x-ai')),
        ('anthropic.messages', snapshot('anthropic')),
        ('deepseek', snapshot('deepseek')),
        ('openai.chat', snapshot('openai')),
        ('aws.bedrock', snapshot('aws')),
        ('together_ai', snapshot('together')),
        ('cohere_chat', snapshot('cohere')),
    ],
)
def test_provider_matching(provider_ref: str, provider_id: str):
    result = find_provider_by_id(providers, provider_ref)
    assert result is not None
    assert result.id == provider_id


# Auto-routing regression for TTS + STT providers. Each catalog PR (Deepgram TTS,
# Cartesia, ElevenLabs, Deepgram STT) appends parametrize rows. Catches the
# failure mode where a model lands in the catalog but its prefix isn't covered by
# the provider's `model_match` clause, which silently breaks
# `calc_price(model_ref=...)` without `provider_id`.
#
# Each row carries its own Usage shape so the test stays self-documenting: a new
# provider PR appends `(model_ref, provider_id, usage_kwargs)` without touching
# the test body.
@pytest.mark.parametrize(
    'model_ref, expected_provider_id, usage_kwargs',
    [
        # TTS
        ('tts-1', 'openai', {'characters': 200}),
        ('tts-1-hd', 'openai', {'characters': 200}),
        ('aura-asteria-en', 'deepgram', {'characters': 200}),
        ('aura-2-helios-en', 'deepgram', {'characters': 200}),
        ('sonic-3', 'cartesia', {'characters': 200}),
        ('eleven_turbo_v2_5', 'elevenlabs', {'characters': 200}),
        ('eleven_flash_v2_5', 'elevenlabs', {'characters': 200}),
        ('eleven_multilingual_v2', 'elevenlabs', {'characters': 200}),
        # STT (new for v0.0.7)
        ('nova-3', 'deepgram', {'audio_input_seconds': Decimal('60')}),
        ('nova-3-batch', 'deepgram', {'audio_input_seconds': Decimal('60')}),
        ('nova-3-multilingual', 'deepgram', {'audio_input_seconds': Decimal('60')}),
        ('nova-3-multilingual-batch', 'deepgram', {'audio_input_seconds': Decimal('60')}),
        # STT (VoiceGateway providers: Deepgram nova-2/flux, AssemblyAI, OpenAI Whisper).
        # groq/whisper-large-v3 is not here: groq.yml has no model_match, so it resolves
        # by provider_id (how VoiceGateway calls it), not by a bare model_ref.
        ('nova-2', 'deepgram', {'audio_input_seconds': Decimal('60')}),
        ('flux-general', 'deepgram', {'audio_input_seconds': Decimal('60')}),
        ('universal-2', 'assemblyai', {'audio_input_seconds': Decimal('60')}),
        ('whisper-1', 'openai', {'audio_input_seconds': Decimal('60')}),
    ],
)
def test_audio_model_auto_routes_to_correct_provider(
    model_ref: str, expected_provider_id: str, usage_kwargs: dict[str, Any]
):
    result = calc_price(Usage(**usage_kwargs), model_ref=model_ref)
    assert result.provider.id == expected_provider_id


def test_nova_3_bare_ref_resolves_to_streaming_not_batch():
    """Bare `nova-3` must resolve to the monolingual streaming entry; batch and
    multilingual require explicit IDs (`nova-3-batch`, `nova-3-multilingual`).
    The `equals` matcher on each STT entry guarantees one-to-one resolution; the
    test locks the convention in.
    """
    r = calc_price(Usage(audio_input_seconds=Decimal('60')), model_ref='nova-3')
    assert r.model.id == 'nova-3'  # NOT nova-3-batch, NOT nova-3-multilingual
