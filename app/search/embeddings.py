"""Опциональные эмбеддинги через OpenAI-совместимый /embeddings (Ollama, Jina, OpenAI и др.).

Векторы заголовков кешируются в памяти — для тысяч записей это один-два запроса при первом поиске.
"""
from __future__ import annotations

import logging
import math

log = logging.getLogger(__name__)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class OpenAICompatibleEmbedder:
    threshold = 0.8  # без лексического совпадения запись попадает в выдачу только при высокой близости

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 20.0):
        from openai import OpenAI

        self.client = OpenAI(base_url=base_url, api_key=api_key or "none", timeout=timeout, max_retries=1)
        self.model = model
        self._cache: dict[str, list[float]] = {}

    def _embed(self, texts: list[str]) -> None:
        missing = [t for t in dict.fromkeys(texts) if t not in self._cache]
        for i in range(0, len(missing), 100):
            batch = missing[i : i + 100]
            resp = self.client.embeddings.create(model=self.model, input=batch)
            for text, row in zip(batch, resp.data):
                self._cache[text] = row.embedding

    def similarities(self, query: str, texts: list[str]) -> list[float]:
        self._embed([query, *texts])
        q = self._cache[query]
        return [_cosine(q, self._cache[t]) for t in texts]
