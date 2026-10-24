"""Прямое подключение GigaChat: официальный SDK получает и обновляет OAuth-токен.

Промпт и разбор ответа общие с остальными LLM. Запросы последовательны: freemium
для физлиц разрешает один поток. TLS проверяется, секреты не попадают в ошибки.
"""
from __future__ import annotations

import ssl
import threading

from gigachat import GigaChat
from gigachat.exceptions import ResponseError

from app.nlu.llm import LLMNLU
from app.nlu.schema import Command


class GigaChatNLU(LLMNLU):
    def __init__(self, credentials: str, model: str = "GigaChat-2-Max",
                 scope: str = "GIGACHAT_API_PERS", timeout: float = 20.0,
                 ca_bundle_file: str | None = None):
        self.model = model
        context = ssl.create_default_context()
        if ca_bundle_file:
            context.load_verify_locations(cafile=ca_bundle_file)
        self.client = GigaChat(
            credentials=credentials, model=model, scope=scope, timeout=timeout,
            base_url="https://api.giga.chat/v1", ssl_context=context, verify_ssl_certs=True,
        )
        self._lock = threading.Lock()
        self._schema = Command.model_json_schema()
        self._schema["properties"].pop("source")
        self._schema["additionalProperties"] = False

    def _complete(self, messages: list[dict]) -> str:
        try:
            with self._lock:
                response = self.client.chat({
                    "model": self.model, "messages": messages, "temperature": 0,
                    "max_tokens": 1000,
                    "response_format": {"type": "json_schema", "schema": self._schema, "strict": True},
                })
            choice = response.choices[0]
            if choice.finish_reason != "stop":
                raise ValueError("GigaChat не завершил ответ")
            return choice.message.content or ""
        except ResponseError as exc:
            hints = {401: "проверьте ключ авторизации и scope", 402: "проверьте остаток токенов",
                     403: "проверьте доступ к модели и scope", 429: "превышена квота запросов"}
            hint = hints.get(exc.status_code, "сервис вернул ошибку")
            raise RuntimeError(f"GigaChat: HTTP {exc.status_code}; {hint}") from None
        except Exception as exc:
            # Ответ OAuth/HTTP может содержать служебные данные: наружу только тип ошибки.
            raise RuntimeError(f"GigaChat: {type(exc).__name__}; проверьте ключ, scope и сертификаты") from None

    def close(self) -> None:
        self.client.close()
