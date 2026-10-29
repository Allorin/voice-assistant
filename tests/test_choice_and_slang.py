"""Ответы из ручной проверки: «все»/«первую и вторую» при выборе, «убирай», команда вместо названия."""
import pytest

from app.dialog.manager import parse_choices


@pytest.mark.parametrize("text, expected", [
    ("все", [0, 1, 2]), ("обе", [0, 1, 2]), ("да, все", [0, 1, 2]), ("удали все", [0, 1, 2]),
    ("первую и вторую", [0, 1]), ("1 и 3", [0, 2]), ("вторую, третью", [1, 2]),
    ("первую", None), ("2", None), ("всю серию", None),
])
def test_parse_choices(text, expected):
    assert parse_choices(text, 3) == expected


def add_two(say):
    say("Добавь задачу играть в Лигу легенд завтра в 10")
    say("Добавь задачу лига легенд турнир завтра")


@pytest.mark.parametrize("answer", ["все", "первую и вторую"])
def test_delete_several_from_choice(say_empty, empty_container, answer):
    add_two(say_empty)
    assert say_empty("Удали все запросы с Лига легенд").awaiting == "choice"
    r = say_empty(answer)
    assert r.intent == "delete" and r.text.count("Удалено") == 2
    assert empty_container.repo.count() == 0


def test_complete_several_from_choice(say_empty, empty_container):
    add_two(say_empty)
    say_empty("Отметь выполненной задачу Лига легенд")
    r = say_empty("обе")
    assert r.intent == "complete"
    assert all(i.status.value == "done" for i in empty_container.repo.list_all("demo"))


@pytest.mark.parametrize("text", ["убирай завтра Лига легенд", "убираю играть Лигу легенд", "удаляю Лигу легенд"])
def test_colloquial_delete_is_not_a_new_task(say_empty, empty_container, text):
    say_empty("Добавь задачу играть в Лигу легенд завтра в 10")
    r = say_empty(text)
    assert r.intent == "delete" and "Играть в Лигу легенд" in r.text
    assert empty_container.repo.count() == 0


def test_title_answer_given_as_full_command(say_empty, empty_container):
    assert say_empty("на послезавтра добавь").awaiting == "title"
    r = say_empty("на послезавтра Добавь купить хлеб")
    assert r.intent == "create" and "«Купить хлеб»" in r.text
    assert empty_container.repo.list_all("demo")[0].title == "Купить хлеб"


@pytest.mark.parametrize("yes", ["да", "да да", "да, да", "да, удалить", "подтверждаю", "удаляй всё", "ага да"])
def test_bulk_delete_confirmations(say_empty, empty_container, yes):
    say_empty("Запиши купить хлеб завтра")
    assert say_empty("Удали все задачи на завтра").awaiting == "confirm"
    assert say_empty(yes).intent == "delete" and empty_container.repo.count() == 0


@pytest.mark.parametrize("text", ["встречи удали", "встечи удали", "все встречи удали"])
def test_plural_before_verb_and_typo_is_bulk_delete(say_empty, empty_container, text):
    say_empty("Добавь встречу с Иваном завтра в 10")
    say_empty("Добавь встречу с Олегом послезавтра в 12")
    say_empty("Запиши купить хлеб")
    r = say_empty(text)
    assert r.awaiting == "period" and "за всё время" in r.text  # одну запись наугад не удаляем
    r = say_empty("за всё время")
    assert r.awaiting == "confirm" and "2 встречи" in r.text
    assert say_empty("да").intent == "delete"
    assert [i.title for i in empty_container.repo.list_all("demo")] == ["Купить хлеб"]


@pytest.mark.parametrize("text, title", [
    ("добавь почту dani+dsa@gmail.com", "Почту dani+dsa@gmail.com"),
    ("добавь почту dani+dsa@gmail.com на завтра", "Почту dani+dsa@gmail.com"),
    ("запиши проверить https://example.com/a-b?x=1 завтра в 10", "Проверить https://example.com/a-b?x=1"),
    ("позвонить по номеру +7 (999) 123-45-67 завтра", "Позвонить по номеру +7 (999) 123-45-67"),
])
def test_emails_links_and_phones_stay_intact(text, title):
    from datetime import datetime

    from app.nlu.rules import RuleNLU
    from app.nlu.schema import NLUContext
    assert RuleNLU().parse(text, NLUContext(now=datetime(2026, 10, 29, 0, 40))).title == title


def test_command_word_removed_from_llm_title():
    from app.nlu.hybrid import HybridNLU
    from app.nlu.rules import RuleNLU
    from app.nlu.schema import Command, Intent, NLUContext
    from tests.conftest import NOW

    class LLM:
        def parse(self, text, ctx):
            return Command(intent=Intent.CREATE, title="Добавь почту dani+dsa@gmail.com", when="завтра")

    cmd = HybridNLU(RuleNLU(), LLM(), always_llm=True).parse("добавь почту dani+dsa@gmail.com на завтра", NLUContext(now=NOW))
    assert cmd.title == "Почту dani+dsa@gmail.com"


def test_existing_words_are_not_treated_as_typos():
    from app.nlu.rules import fix_key_typos
    assert fix_key_typos("встечи удали") == "встречи удали"
    for phrase in ("я удалил задачу", "встречаю маму", "созвон с альфой", "напомнил ивану"):
        assert fix_key_typos(phrase) == phrase
