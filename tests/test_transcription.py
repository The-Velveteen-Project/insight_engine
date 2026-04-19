"""
Tests for the transcription service.

No network, no audio files, no real API calls.
Tests focus on contracts and configuration behaviour.
"""

from app.services.transcription import Transcriber, WhisperTranscriber, get_transcriber


def test_get_transcriber_returns_none_without_api_key() -> None:
    """
    In the test environment OPENAI_API_KEY is empty — get_transcriber() must
    return None so the intake pipeline degrades gracefully.
    """
    result = get_transcriber()
    assert result is None


def test_whisper_transcriber_satisfies_protocol() -> None:
    """
    WhisperTranscriber must satisfy the Transcriber Protocol at runtime.
    Uses a placeholder API key — no network call is made at instantiation.
    """
    transcriber = WhisperTranscriber(api_key="test-key-placeholder")
    assert isinstance(transcriber, Transcriber)


def test_whisper_transcriber_has_transcribe_method() -> None:
    import inspect

    transcriber = WhisperTranscriber(api_key="test-key-placeholder")
    assert hasattr(transcriber, "transcribe")
    assert inspect.iscoroutinefunction(transcriber.transcribe)
