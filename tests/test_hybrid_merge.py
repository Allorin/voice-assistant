"""LLM пропустила поле, которое правила нашли в той же реплике: гибрид дополняет ответ модели."""
from app.nlu.hybrid import HybridNLU
from app.nlu.rules import RuleNLU
from app.nlu.schema import Command, Intent, NLUContext
from tests.conftest import NOW


class StubLLM:
    def __init__(self, cmd: Command):
        self.cmd = cmd

    def parse(self, text, ctx):
        return self.cmd.model_copy()


def merged(text: str, llm_cmd: Command) -> Command:
    return HybridNLU(RuleNLU(), StubLLM(llm_cmd)).parse(text, NLUContext(now=NOW))


def test_list_period_status_and_tense_are_filled_from_rules():
    cmd = merged("Чем я занят в пятницу?", Command(intent=Intent.LIST))
    assert cmd.period and "пятниц" in cmd.period
    cmd = merged("Что я уже сделал на этой неделе", Command(intent=Intent.LIST))
    assert cmd.status == "done" and cmd.period
    cmd = merged("Когда следующий созвон с Альфой?", Command(intent=Intent.SEARCH, query="созвон с Альфой"))
    assert cmd.tense == "future"
    cmd = merged("Поищи всё про CRM", Command(intent=Intent.SEARCH))
    assert "CRM" in cmd.query


def test_more_specific_repeat_from_rules_wins():
    cmd = merged("Поставь задачу оплачивать интернет 15 числа каждого месяца",
                 Command(intent=Intent.CREATE, title="Оплачивать интернет", repeat="каждый месяц"))
    assert "15" in cmd.repeat
    cmd = merged("Каждые две недели по пятницам созвон с Альфой",
                 Command(intent=Intent.CREATE, title="Созвон с Альфой", repeat="каждые две недели"))
    assert "пятниц" in cmd.repeat


def test_pronoun_targets_last_item():
    cmd = merged("А перенеси её на завтра", Command(intent=Intent.UPDATE, when="завтра"))
    assert cmd.target_last


def test_search_without_query_becomes_list():
    cmd = merged("что было в прошлую среду", Command(intent=Intent.SEARCH, period="в прошлую среду"))
    assert cmd.intent == Intent.LIST and cmd.source == "rules-fallback"


def test_item_type_from_rules_for_search():
    cmd = merged("Где у меня задача про смету?", Command(intent=Intent.SEARCH, query="смета"))
    assert cmd.item_type is not None and cmd.item_type.value == "task"


def test_llm_outage_pauses_requests(monkeypatch):
    calls = []

    class DownLLM:
        def parse(self, text, ctx):
            calls.append(text)
            raise RuntimeError("GigaChat: ConnectTimeout; сервер не ответил вовремя")

    clock = [1000.0]
    monkeypatch.setattr("app.nlu.hybrid.time.monotonic", lambda: clock[0])
    nlu = HybridNLU(RuleNLU(), DownLLM())
    ctx = NLUContext(now=NOW)
    for _ in range(5):
        cmd = nlu.parse("Напомни завтра в 10 позвонить Ивану", ctx)
        assert cmd.source == "rules-fallback" and cmd.title == "Позвонить Ивану"
    assert len(calls) == 2  # после двух сбоев подряд LLM не дёргается
    clock[0] += HybridNLU.PAUSE_SECONDS + 1
    nlu.parse("Что у меня сегодня?", ctx)
    assert len(calls) == 3  # пауза прошла — снова пробуем LLM
