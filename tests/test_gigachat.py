"""Реальный SDK с подменённым HTTP: OAuth, обновление токена, JSON и безопасный откат."""
import json
import ssl
import time
from dataclasses import replace
from uuid import UUID

import httpx
import pytest

from app import main
from app.nlu.factory import create_llm
from app.nlu.gigachat import GigaChatNLU
from app.nlu.hybrid import HybridNLU
from app.nlu.rules import RuleNLU
from app.nlu.schema import Intent, NLUContext
from tests.conftest import NOW, make_settings

PHRASE = "Напомни мне завтра в 10 утра позвонить Ивану"
CREDENTIALS = "test-authorization-key"


@pytest.fixture
def giga():
    nlu = GigaChatNLU(CREDENTIALS)
    yield nlu
    nlu.close()


def mock_http(giga, chat_status=200, content=None):
    calls = {"auth": [], "chat": []}

    def handler(request):
        if request.url.path == "/api/v2/oauth":
            calls["auth"].append(request)
            return httpx.Response(200, json={"access_token": f"token-{len(calls['auth'])}",
                                             "expires_at": int(time.time() * 1000) + 1_800_000})
        assert request.url.path == "/v1/chat/completions"
        calls["chat"].append(request)
        if chat_status != 200:
            return httpx.Response(chat_status, json={"message": CREDENTIALS},
                                  headers={"Authorization": "Bearer private-token"})
        answer = content if content is not None else json.dumps({
            "intent": "create", "item_type": "reminder", "title": "Позвонить Ивану", "when": "завтра в 10 утра",
        }, ensure_ascii=False)
        return httpx.Response(200, json={
            "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
            "created": 1, "model": "GigaChat-2-Max", "object": "chat.completions",
            "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
        })

    transport = httpx.MockTransport(handler)
    giga.client._client_instance = httpx.Client(base_url="https://api.giga.chat/v1/", transport=transport)
    giga.client._auth_client_instance = httpx.Client(transport=transport)
    return calls


def test_sdk_authenticates_reuses_and_refreshes_token(giga):
    calls = mock_http(giga)
    ctx = NLUContext(now=NOW)
    first = giga.parse(PHRASE, ctx)
    giga.parse(PHRASE, ctx)
    assert first.intent == Intent.CREATE and first.source == "llm"
    assert len(calls["auth"]) == 1
    auth = calls["auth"][0]
    assert auth.headers["Authorization"] == f"Basic {CREDENTIALS}"
    assert UUID(auth.headers["RqUID"]).version == 4
    assert auth.content == b"scope=GIGACHAT_API_PERS"
    assert calls["chat"][0].headers["Authorization"] == "Bearer token-1"

    giga.client._access_token.expires_at = 1  # истёк: следующий запрос должен получить новый токен
    giga.parse(PHRASE, ctx)
    assert len(calls["auth"]) == 2
    assert calls["chat"][-1].headers["Authorization"] == "Bearer token-2"

    body = json.loads(calls["chat"][0].content)
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["strict"] is True
    assert "source" not in body["response_format"]["schema"]["properties"]
    assert body["messages"][-1]["content"] == PHRASE
    context = giga.client._settings.ssl_context
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


def test_api_failure_falls_back_without_logging_credentials(giga, caplog):
    mock_http(giga, chat_status=403)
    cmd = HybridNLU(RuleNLU(), giga, always_llm=True).parse(PHRASE, NLUContext(now=NOW))
    assert cmd.source == "rules-fallback" and cmd.title == "Позвонить Ивану"
    assert "HTTP 403" in caplog.text
    assert CREDENTIALS not in caplog.text and "private-token" not in caplog.text


def test_invalid_model_json_falls_back(giga):
    mock_http(giga, content="Это не JSON")
    cmd = HybridNLU(RuleNLU(), giga, always_llm=True).parse(PHRASE, NLUContext(now=NOW))
    assert cmd.source == "rules-fallback" and cmd.intent == Intent.CREATE


def test_provider_configuration_and_status(giga, monkeypatch):
    settings = replace(make_settings(False), llm_provider="gigachat", gigachat_credentials=CREDENTIALS)
    assert settings.llm_enabled and settings.active_llm_model == "GigaChat-2-Max"
    assert CREDENTIALS not in repr(settings)
    assert not replace(settings, gigachat_credentials="").llm_enabled
    assert create_llm(replace(settings, gigachat_credentials="")) is None
    monkeypatch.setattr("app.nlu.gigachat.GigaChatNLU", lambda *args: giga)
    from app.clock import FixedClock
    from app.container import build

    container = build(settings, FixedClock(NOW))
    monkeypatch.setattr(main, "container", container)
    assert container.nlu.llm is giga
    assert main.status()["llm_model"] == "GigaChat-2-Max"


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        replace(make_settings(False), llm_provider="giga_typo")


def _some_root_pem() -> str:
    import certifi

    text = open(certifi.where(), encoding="ascii", errors="ignore").read()
    start = text.index("-----BEGIN CERTIFICATE-----")
    end = text.index("-----END CERTIFICATE-----", start) + len("-----END CERTIFICATE-----")
    return text[start:end] + "\n"


@pytest.mark.parametrize("fmt", ["pem", "der"])
def test_ca_bundle_accepts_pem_and_der(tmp_path, fmt):
    pem = _some_root_pem()
    path = tmp_path / f"root.{'pem' if fmt == 'pem' else 'cer'}"
    if fmt == "pem":
        path.write_text(pem, encoding="ascii")
    else:
        path.write_bytes(ssl.PEM_cert_to_DER_cert(pem))
    nlu = GigaChatNLU(CREDENTIALS, ca_bundle_file=str(path))
    try:
        assert nlu.client._settings.ssl_context.cert_store_stats()["x509_ca"] >= 1
    finally:
        nlu.close()


def test_ca_bundle_path_from_other_computer_falls_back_to_certs_folder(tmp_path, monkeypatch, caplog):
    from app import config

    certs = tmp_path / ".certs"
    certs.mkdir()
    (certs / "russian_trusted_root_ca_pem.crt").write_text(_some_root_pem(), encoding="ascii")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "CERT_DIRS", (certs,))
    found = config.resolve_ca_bundle("C:/Users/Samir/Desktop/ХАКАТОН/voice-assistant/.certs/root.pem")
    assert found == str(certs / "russian_trusted_root_ca_pem.crt")
    assert "не найден" in caplog.text
    assert config.resolve_ca_bundle(".certs/russian_trusted_root_ca_pem.crt") == found  # относительный путь
    monkeypatch.setattr(config, "CERT_DIRS", (tmp_path / "empty",))
    assert config.resolve_ca_bundle("") == ""


def test_broken_llm_setup_does_not_stop_the_app(monkeypatch, caplog):
    from app.clock import FixedClock
    from app.container import build

    def broken(*_):
        raise FileNotFoundError("нет файла сертификата")

    monkeypatch.setattr("app.nlu.gigachat.GigaChatNLU", broken)
    settings = replace(make_settings(False), llm_provider="gigachat", gigachat_credentials=CREDENTIALS)
    container = build(settings, FixedClock(NOW))
    assert container.nlu.llm is None and container.nlu.mode == "rules"
    assert "работают правила" in caplog.text
    assert container.dialog.handle("s", "Напомни завтра в 10 позвонить Ивану").intent == "create"


def test_sdk_ignores_stale_ca_path_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("GIGACHAT_CA_BUNDLE_FILE", "C:/Users/Samir/Desktop/ХАКАТОН/voice-assistant/.certs/x.pem")
    nlu = GigaChatNLU(CREDENTIALS)
    try:
        assert not nlu.client._settings.ca_bundle_file
        calls = mock_http(nlu)
        assert nlu.parse(PHRASE, NLUContext(now=NOW)).source == "llm" and calls["chat"]
        from gigachat.client import _get_kwargs
        assert _get_kwargs(nlu.client._settings)["verify"] is nlu.client._settings.ssl_context
    finally:
        nlu.close()
