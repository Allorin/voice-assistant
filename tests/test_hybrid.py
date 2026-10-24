"""LLM-слой без сети: поддельная модель проверяет разбор JSON и откат на правила."""
from app.models import ItemType
from app.nlu.hybrid import HybridNLU
from app.nlu.llm import _extract_json, _normalize
from app.nlu.rules import RuleNLU
from app.nlu.schema import Command, Intent, NLUContext
from tests.conftest import NOW

ctx = NLUContext(now=NOW)


class FakeLLM:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    def parse(self, text, ctx):
        if self.error:
            raise self.error
        cmd = Command.model_validate(_normalize(self.result))
        cmd.source = "llm"
        return cmd


def test_extract_json_from_markdown():
    raw = 'Вот ответ:\n```json\n{"intent": "list", "period": "на сегодня", "item_type": "null"}\n```'
    data = _normalize(_extract_json(raw))
    assert data["intent"] == "list" and data["period"] == "на сегодня" and data["item_type"] is None


def test_normalize_garbage_values():
    data = _normalize({"intent": "CREATE", "item_type": "event", "tense": "soon", "target_last": "yes"})
    assert data["intent"] == "create" and data["item_type"] is None and data["tense"] == "any"


def test_llm_failure_falls_back_to_rules():
    nlu = HybridNLU(RuleNLU(), FakeLLM(error=TimeoutError("timeout")))
    cmd = nlu.parse("Напомни мне завтра в 10 утра позвонить Ивану", ctx)
    assert cmd.source == "rules-fallback" and cmd.intent == Intent.CREATE and cmd.title == "Позвонить Ивану"


def test_llm_result_is_used_and_completed_by_rules():
    llm = FakeLLM({"intent": "create", "item_type": "reminder", "title": "Позвонить Ивану", "when": None})
    cmd = HybridNLU(RuleNLU(), llm).parse("Напомни мне завтра в 10 утра позвонить Ивану", ctx)
    assert cmd.source == "llm" and cmd.item_type == ItemType.REMINDER
    assert cmd.when == "завтра в 10 утра"  # LLM потеряла время — правила дополнили


def test_llm_unknown_but_rules_understand():
    cmd = HybridNLU(RuleNLU(), FakeLLM({"intent": "unknown"})).parse("Что у меня сегодня?", ctx)
    assert cmd.intent == Intent.LIST
