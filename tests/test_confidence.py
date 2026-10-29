"""Правила уверены → LLM не спрашиваем: ответ мгновенный и без расхода токенов. Не уверены → спрашиваем."""
import json
import sys

import pytest

from app.nlu.confidence import rules_sure
from app.nlu.hybrid import HybridNLU
from app.nlu.rules import RuleNLU
from app.nlu.schema import Command, Intent, NLUContext
from tests.conftest import NOW, ROOT

SURE = ["Напомни мне завтра в 10 утра позвонить Ивану", "Добавь встречу с командой завтра в 15:00",
        "Что у меня сегодня?", "Какие задачи у меня на завтра?", "Найди задачу про презентацию",
        "Перенеси встречу с Марией на пятницу в 12", "Отметь задачу купить билеты выполненной",
        "Каждый понедельник в 10 у меня планёрка", "в следующую пятницу корпоратив с 19 до 23",
        "добавь задачу купить хлеб и молоко", "отмени последнее", "Что ты умеешь?"]
UNSURE = ["Слушай, закинь в список дел помыть машину в воскресенье",  # «закинь» правила не знают
          "перекинь стоматолога на следующую среду",                 # команда попала в название
          "запланируй пробежку каждое утро кроме выходных",          # «кроме выходных»
          "завтра созвон с Альфой но не в 10 а в 11",                # оговорка
          "перенеси всё с пятницы на понедельник",                   # массовый перенос
          "напомни позвонить маме когда приду домой",                # условие вместо времени
          "запиши что я должен Саше 500 рублей",
          "Привет!", "какая-то непонятная фраза про погоду на Марсе"]


@pytest.mark.parametrize("text", SURE)
def test_rules_sure(text):
    assert rules_sure(text, RuleNLU().parse(text, NLUContext(now=NOW)), NOW)


@pytest.mark.parametrize("text", UNSURE)
def test_rules_not_sure(text):
    assert not rules_sure(text, RuleNLU().parse(text, NLUContext(now=NOW)), NOW)


class CountingLLM:
    def __init__(self):
        self.calls = 0

    def parse(self, text, ctx):
        self.calls += 1
        return Command(intent=Intent.CREATE, title="Помыть машину", when="в воскресенье", source="llm")


def test_llm_is_called_only_when_rules_are_unsure():
    llm = CountingLLM()
    nlu = HybridNLU(RuleNLU(), llm)
    ctx = NLUContext(now=NOW)
    assert nlu.parse("Добавь встречу с командой завтра в 15:00", ctx).source == "rules-sure"
    assert llm.calls == 0
    cmd = nlu.parse("Слушай, закинь в список дел помыть машину в воскресенье", ctx)
    assert llm.calls == 1 and cmd.source == "llm" and cmd.title == "Помыть машину"
    always = HybridNLU(RuleNLU(), llm, always_llm=True)
    always.parse("Добавь встречу с командой завтра в 15:00", ctx)
    assert llm.calls == 2


def test_sure_answers_on_eval_sets_are_right():
    """На наборах фраз уверенные ответы правил верны (кроме одной фразы, где ошибаются и правила, и LLM)."""
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        from eval_nlu import NOW as EVAL_NOW
        from eval_nlu import check
    finally:
        sys.path.pop(0)
    sure = wrong = total = 0
    for name in ("phrases", "holdout", "fresh"):
        for line in (ROOT / "eval" / f"{name}.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            cmd = RuleNLU().parse(row["text"], NLUContext(now=EVAL_NOW))
            total += 1
            if rules_sure(row["text"], cmd, EVAL_NOW):
                sure += 1
                if check(cmd, row):
                    wrong += 1
                    assert row["text"] == "перенеси созвон с маркетингом на вторник"
    assert sure / total >= 0.7  # большинство обычных фраз обходятся без LLM
    assert wrong <= 1
