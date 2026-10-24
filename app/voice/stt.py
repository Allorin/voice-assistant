"""Серверное распознавание речи через OpenAI-совместимый /audio/transcriptions
(например, Groq whisper-large-v3-turbo — есть бесплатный лимит, или локальный faster-whisper-server).

По умолчанию не нужно: страница распознаёт речь средствами браузера (Web Speech API).
Серверный вариант — запасной для браузеров без Web Speech API (Firefox) и для лучшего качества.
"""
from __future__ import annotations


class SpeechToText:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 60.0):
        from openai import OpenAI

        self.client = OpenAI(base_url=base_url, api_key=api_key or "none", timeout=timeout, max_retries=1)
        self.model = model

    def transcribe(self, filename: str, data: bytes, content_type: str | None = None) -> str:
        resp = self.client.audio.transcriptions.create(
            model=self.model,
            file=(filename, data, content_type or "audio/webm"),
            language="ru",
        )
        return (getattr(resp, "text", None) or "").strip()
