"""Прямое подключение GigaChat: официальный SDK получает и обновляет OAuth-токен.

Промпт и разбор ответа общие с остальными LLM. Запросы последовательны: freemium
для физлиц разрешает один поток. TLS проверяется, секреты не попадают в ошибки.
"""
from __future__ import annotations

import ssl
import threading
from pathlib import Path

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
            data = Path(ca_bundle_file).read_bytes()
            # с Госуслуг сертификат скачивается и в PEM, и в двоичном DER: ssl принимает оба через cadata
            context.load_verify_locations(cadata=data.decode("ascii") if b"-----BEGIN" in data else data)
        self.client = GigaChat(
            credentials=credentials, model=model, scope=scope, timeout=timeout,
            base_url="https://api.giga.chat/v1", ssl_context=context, verify_ssl_certs=True,
            # SDK сам читает GIGACHAT_CA_BUNDLE_FILE из окружения и тогда игнорирует ssl_context;
            # путь там может быть с чужого компьютера, поэтому сертификат передаётся только через context
            ca_bundle_file="",
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
            # Ответ OAuth/HTTP может содержать служебные данные: наружу только тип ошибки и известная причина.
            raise RuntimeError(f"GigaChat: {type(exc).__name__}; {_network_hint(exc)}") from None

    def close(self) -> None:
        self.client.close()


_NETWORK_HINTS = (
    ("CERTIFICATE_VERIFY_FAILED", "TLS-сертификат не принят: нужен корневой сертификат НУЦ Минцифры (папка .certs)"),
    ("timed out", "сервер не ответил вовремя: проверьте сеть, VPN или прокси"),
    ("getaddrinfo", "адрес сервера не найден: проверьте интернет и DNS"),
    ("Name or service not known", "адрес сервера не найден: проверьте интернет и DNS"),
    ("Connection refused", "соединение отклонено"),
    ("10054", "соединение сброшено: попробуйте без VPN или с другим маршрутом"),
    ("reset by peer", "соединение сброшено: попробуйте без VPN или с другим маршрутом"),
)


def _network_hint(exc: Exception) -> str:
    text = str(exc)
    for marker, hint in _NETWORK_HINTS:
        if marker in text:
            return hint
    return "проверьте ключ, scope, сеть и сертификаты"
