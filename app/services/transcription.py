"""
Transcription service — desacoplado del pipeline de intake.

Interface: Transcriber (Protocol, runtime-checkable)
Implementation: WhisperTranscriber (OpenAI Whisper API)
Factory: get_transcriber() — returns None when OPENAI_API_KEY is not configured.

Design constraints:
- The transcriber is a lazy singleton: instantiated once, on first call
  to get_transcriber().
- Failures in transcription must NOT propagate to the caller — handle them
  in _attempt_transcription (message_intake.py). This module only raises on
  hard initialization errors.
- Audio format: Telegram voice notes are OGG/Opus. Whisper accepts OGG directly.
"""

import logging
from typing import Protocol, runtime_checkable

from app.core.config import settings

logger = logging.getLogger(__name__)


@runtime_checkable
class Transcriber(Protocol):
    """Structural interface for transcription backends."""

    async def transcribe(self, audio_path: str) -> str:
        """
        Transcribe the audio file at `audio_path`.
        Returns the raw transcript as a string.
        Raises on unrecoverable errors (network, invalid file, API).
        """
        ...


class WhisperTranscriber:
    """
    OpenAI Whisper transcription via the official async SDK.

    The `openai` import is deferred to __init__ to avoid a hard import
    failure in environments where openai is installed but the key is absent.
    """

    def __init__(self, api_key: str, model: str = "whisper-1") -> None:
        from openai import AsyncOpenAI  # deferred — avoids import-time side effects

        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model

    async def transcribe(self, audio_path: str) -> str:
        with open(audio_path, "rb") as audio_file:
            response = await self._client.audio.transcriptions.create(
                model=self._model,
                file=audio_file,
            )
        return response.text


_transcriber: WhisperTranscriber | None = None


def get_transcriber() -> WhisperTranscriber | None:
    """
    Returns the configured transcription backend, or None if unavailable.

    Returns None silently when OPENAI_API_KEY is not set.
    Callers are expected to handle None gracefully — transcription is optional,
    not a hard dependency of the intake pipeline.
    """
    global _transcriber
    if _transcriber is None and settings.openai_api_key:
        _transcriber = WhisperTranscriber(
            api_key=settings.openai_api_key,
            model=settings.whisper_model,
        )
        logger.info(
            "WhisperTranscriber initialized (model=%s).", settings.whisper_model
        )
    return _transcriber
