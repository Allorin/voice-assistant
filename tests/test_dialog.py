"""Сквозные сценарии из ТЗ: фраза → ответ ассистента → состояние БД."""
from datetime import datetime

from app.models import ItemStatus, ItemType


def test_create_reminder(say_empty, empty_container):
    r = say_empty("Напомни мне завтра в 10 утра позвонить Ивану")
    assert r.intent == "create"
    assert "завтра в 10:00" in r.text
    item = r.items[0]
    assert (item.type, item.title, item.due_at) == (ItemType.REMINDER, "Позвонить Ивану", datetime(2026, 10, 21, 10))
    assert empty_container.repo.count("demo") == 1


def test_create_task_with_deadline(say_empty):
    r = say_empty("Поставь задачу купить билеты до конца недели")
    assert r.items[0].due_at == datetime(2026, 10, 25) and not r.items[0].has_time
    assert "до воскресенья" in r.text


def test_meeting_asks_for_time_then_creates(say_empty):
    r = say_empty("Поставь встречу с Иваном завтра")
    assert r.awaiting == "time" and r.text == "На какое время поставить встречу?"
    assert not r.items
    r = say_empty("давай в 16:30")
    assert r.intent == "create"
    assert r.items[0].due_at == datetime(2026, 10, 21, 16, 30)
    assert r.items[0].title == "Встреча с Иваном"


def test_context_reschedule(say_empty):
    say_empty("Добавь встречу с командой завтра в 15:00")
    r = say_empty("А перенеси её на послезавтра")
    assert r.intent == "update"
    assert r.items[0].due_at == datetime(2026, 10, 22, 15, 0)  # время встречи сохранилось
    r = say_empty("Перенеси её на час позже")
    assert r.items[0].due_at == datetime(2026, 10, 22, 16, 0)


def test_conflict_is_reported(say_empty):
    say_empty("Добавь встречу с командой завтра в 15:00")
    r = say_empty("Добавь встречу с Марией завтра в 15:30")
    assert r.awaiting == "confirm" and "уже есть встреча" in r.text
    r = say_empty("нет")
    assert r.awaiting == "time"
    r = say_empty("в 17")
    assert r.items[0].due_at == datetime(2026, 10, 21, 17, 0)


def test_past_time_is_questioned(say_empty):
    r = say_empty("Напомни сегодня в 9 утра выпить витамины")
    assert r.awaiting == "datetime" and "уже прошло" in r.text


def test_list_today_and_week(say):
    r = say("Что у меня сегодня?")
    assert r.intent == "list" and "Обсудить бюджет по презентации" in r.text
    r = say("Покажи мои встречи на сегодня")
    assert all(i.type == ItemType.MEETING for i in r.items)
    r = say("Что запланировано на эту неделю?")
    assert r.text.startswith("На этой неделе")
    assert all(datetime(2026, 10, 19) <= i.due_at < datetime(2026, 10, 26) for i in r.items)


def test_list_empty_day(say_empty):
    r = say_empty("Какие задачи у меня на завтра?")
    assert r.text == "Завтра задач нет."


def test_search_when_was_meeting(say):
    r = say("Когда у меня была встреча с Иваном?")
    assert r.intent == "search"
    assert r.text.startswith("Последняя встреча была")
    best = r.items[0]
    assert "Иван" in best.title and best.type == ItemType.MEETING and best.due_at < datetime(2026, 10, 20, 19, 59)


def test_search_by_topic(say):
    r = say("Найди задачу про презентацию")
    assert r.intent == "search" and "презентация" in r.text
    assert all("презентац" in i.title.lower() for i in r.items)


def test_choice_between_candidates(say):
    say("Добавь встречу с Марией завтра в 12")
    r = say("Перенеси встречу с Марией на пятницу")
    assert r.awaiting == "choice"
    r = say("вторую")
    assert r.intent == "update" and r.items[0].due_at.date() == datetime(2026, 10, 23).date()


def test_complete_and_delete(say_empty, empty_container):
    say_empty("Поставь задачу купить билеты до конца недели")
    r = say_empty("Отметь задачу купить билеты выполненной")
    assert r.items[0].status == ItemStatus.DONE
    say_empty("Добавь встречу с дизайнером в четверг в 11")
    r = say_empty("Удали встречу с дизайнером")
    assert r.intent == "delete"
    assert [i.title for i in empty_container.repo.list_all("demo")] == ["Купить билеты"]


def test_new_command_interrupts_clarification(say_empty):
    say_empty("Поставь встречу с Иваном завтра")
    r = say_empty("Что у меня сегодня?")
    assert r.intent == "list"


def test_cancel_clarification(say_empty, empty_container):
    say_empty("Поставь встречу с Иваном завтра")
    r = say_empty("отмена")
    assert r.intent == "cancel" and empty_container.repo.count("demo") == 0


def test_answer_with_command_word_fills_time(say_empty):
    """«Напомни в 10» в ответ на «Во сколько напомнить?» — это время, а не новое напоминание «Напомни»."""
    r = say_empty("Напомни завтра позвонить Ивану")
    assert r.awaiting == "time"
    r = say_empty("Напомни в 10")
    assert r.intent == "create"
    assert (r.items[0].title, r.items[0].due_at) == ("Позвонить Ивану", datetime(2026, 10, 21, 10, 0))


def test_answer_variants_for_time(say_empty):
    for answer in ("Поставь на 10", "давай в 10", "в 10 утра"):
        say_empty("Напомни завтра позвонить Ивану")
        r = say_empty(answer)
        assert r.items and r.items[0].title == "Позвонить Ивану", answer


def test_bare_command_word_is_not_a_title(say_empty):
    r = say_empty("Напомни")
    assert r.awaiting == "title"
