import pytest

from app.models import ItemType
from app.nlu.rules import RuleNLU
from app.nlu.schema import Intent, NLUContext
from tests.conftest import NOW

nlu = RuleNLU()
ctx = NLUContext(now=NOW)


@pytest.mark.parametrize("text, item_type, title, when", [
    ("Напомни мне завтра в 10 утра позвонить Ивану", ItemType.REMINDER, "Позвонить Ивану", "завтра в 10 утра"),
    ("В пятницу нужно отправить отчет", ItemType.TASK, "Отправить отчет", "В пятницу"),
    ("Поставь задачу купить билеты до конца недели", ItemType.TASK, "Купить билеты", "до конца недели"),
    ("Добавь встречу с командой завтра в 15:00", ItemType.MEETING, "Встреча с командой", "завтра в 15:00"),
    ("В четверг в 11 у меня встреча с дизайнером", ItemType.MEETING, "Встреча с дизайнером", "В четверг в 11"),
    ("Поставь встречу с Иваном завтра", ItemType.MEETING, "Встреча с Иваном", "завтра"),
    ("Напомни через два часа выключить духовку", ItemType.REMINDER, "Выключить духовку", "через два часа"),
    ("Сегодня вечером в 9 напомни выпить таблетку", ItemType.REMINDER, "Выпить таблетку", "Сегодня вечером в 9"),
    ("Мне нужно до пятницы сдать отчёт по проекту", ItemType.TASK, "Сдать отчёт по проекту", "до пятницы"),
    ("Созвон с Альфой в среду в 16:00", ItemType.MEETING, "Созвон с Альфой", "в среду в 16:00"),
    ("Запиши: купить молоко", ItemType.TASK, "Купить молоко", None),
])
def test_create(text, item_type, title, when):
    cmd = nlu.parse(text, ctx)
    assert cmd.intent == Intent.CREATE
    assert cmd.item_type == item_type
    assert cmd.title == title
    assert cmd.when == when


@pytest.mark.parametrize("text, item_type, period", [
    ("Что у меня сегодня?", None, "сегодня"),
    ("Какие планы на сегодня?", None, "на сегодня"),
    ("Покажи мои встречи на сегодня", ItemType.MEETING, "на сегодня"),
    ("Есть ли у меня встречи сегодня?", ItemType.MEETING, "сегодня"),
    ("Какие задачи у меня на завтра?", ItemType.TASK, "на завтра"),
    ("Что запланировано на эту неделю?", None, "на эту неделю"),
    ("У меня завтра встречи?", ItemType.MEETING, "завтра"),
    ("Напомни, что у меня завтра", None, "завтра"),
])
def test_list(text, item_type, period):
    cmd = nlu.parse(text, ctx)
    assert cmd.intent == Intent.LIST
    assert cmd.item_type == item_type
    assert cmd.period == period


@pytest.mark.parametrize("text, item_type, query, tense, ask_when", [
    ("Найди задачу про презентацию", ItemType.TASK, "презентацию", "any", False),
    ("Когда у меня была встреча с Иваном?", ItemType.MEETING, "Иваном", "past", True),
    ("Я когда-то ставил задачу по договору, найди ее", ItemType.TASK, "договору", "any", False),
])
def test_search(text, item_type, query, tense, ask_when):
    cmd = nlu.parse(text, ctx)
    assert cmd.intent == Intent.SEARCH
    assert (cmd.item_type, cmd.query, cmd.tense, cmd.ask_when) == (item_type, query, tense, ask_when)


@pytest.mark.parametrize("text, intent, target_last, query", [
    ("А перенеси ее на завтра", Intent.UPDATE, True, None),
    ("Перенеси встречу с Марией на пятницу в 12", Intent.UPDATE, False, "Марией"),
    ("Отметь задачу купить билеты выполненной", Intent.COMPLETE, False, "купить билеты"),
    ("Готово", Intent.COMPLETE, True, None),
    ("Удали встречу с дизайнером", Intent.DELETE, False, "дизайнером"),
    ("Отмени встречу с Иваном", Intent.DELETE, False, "Иваном"),
])
def test_targets(text, intent, target_last, query):
    cmd = nlu.parse(text, ctx)
    assert (cmd.intent, cmd.target_last, cmd.query) == (intent, target_last, query)


@pytest.mark.parametrize("text, intent", [
    ("Отмена", Intent.CANCEL), ("Привет", Intent.SMALLTALK), ("Что ты умеешь?", Intent.HELP),
])
def test_misc(text, intent):
    assert nlu.parse(text, ctx).intent == intent
