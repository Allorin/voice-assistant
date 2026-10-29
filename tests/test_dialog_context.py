"""Обсуждаемый день: «а вечером?», «и ещё созвон в 14», «в этот день», «туда же» и вопрос «На пятницу или на завтра?».
Сейчас в тестах — вторник, 20 октября 2026, 19:59."""
from datetime import datetime

import pytest

from app.dialog.manager import FOCUS_TURNS, DialogManager


def created(reply):
    assert reply.intent == "create", reply.text
    item = reply.items[0]
    return item.title, item.due_at


def test_context_ref_strips_references_but_not_meaning():
    ref = DialogManager._context_ref
    assert ref("и ещё созвон с Анной в 14") == ("созвон с Анной в 14", "continue")
    assert ref("А вечером?") == ("вечером?", "continue")
    assert ref("напомни в этот день в 9 купить цветы") == ("напомни в 9 купить цветы", "explicit")
    assert ref("добавь туда же встречу в 16") == ("добавь встречу в 16", "explicit")
    assert ref("а что ещё в тот же день?") == ("что ещё ?", "explicit")
    assert ref("ещё раз покажи планы") == ("ещё раз покажи планы", None)
    assert ref("добавь встречу в пятницу") == ("добавь встречу в пятницу", None)


@pytest.mark.parametrize(("answer", "expected"), [
    ("на пятницу", datetime(2026, 10, 23, 12)),
    ("в пятницу", datetime(2026, 10, 23, 12)),
    ("первое", datetime(2026, 10, 23, 12)),
    ("туда", datetime(2026, 10, 23, 12)),
    ("да", datetime(2026, 10, 23, 12)),
    ("на завтра", datetime(2026, 10, 21, 12)),
    ("второе", datetime(2026, 10, 21, 12)),
    ("нет, на завтра", datetime(2026, 10, 21, 12)),
    ("лучше в субботу", datetime(2026, 10, 24, 12)),
])
def test_time_only_after_another_day_asks_which_day(say_empty, answer, expected):
    say_empty("что у меня в пятницу")
    question = say_empty("добавь встречу с Олегом в 12")
    assert question.intent == "clarify"
    assert question.text == "Поставить на пятницу, 23 октября, или на завтра?"
    assert created(say_empty(answer)) == ("Встреча с Олегом", expected)


def test_new_question_instead_of_day_answer(say_empty):
    say_empty("что у меня в пятницу")
    say_empty("добавь встречу с Олегом в 12")
    reply = say_empty("что у меня сегодня")
    assert reply.intent == "list"
    assert reply.text.startswith("«Встреча с Олегом» не сохранено: день не выбран.")
    assert say_empty("отмена").text in ("Хорошо.", "Хорошо, отменено.")


def test_no_question_when_day_is_clear(say_empty):
    say_empty("что у меня завтра")
    assert created(say_empty("добавь встречу в 12"))[1] == datetime(2026, 10, 21, 12)  # обсуждали тот же день
    say_empty("что у меня в пятницу")
    assert created(say_empty("добавь встречу с Иваном завтра в 15"))[1] == datetime(2026, 10, 21, 15)
    say_empty("что у меня в пятницу")
    assert created(say_empty("напомни через час выпить воды"))[1] == datetime(2026, 10, 20, 20, 59)
    say_empty("что у меня сегодня")  # 13:00 сегодня уже прошло — спрашивать про сегодня незачем
    assert created(say_empty("добавь встречу с Анной в 13"))[1] == datetime(2026, 10, 21, 13)


def test_continuation_uses_the_discussed_day(say_empty):
    assert created(say_empty("добавь встречу с дизайнером в четверг в 11"))[1] == datetime(2026, 10, 22, 11)
    assert created(say_empty("и ещё созвон с Анной в 14")) == ("Созвон с Анной", datetime(2026, 10, 22, 14))
    assert created(say_empty("а в 16 обед с командой"))[1] == datetime(2026, 10, 22, 16)


def test_explicit_same_day(say_empty):
    say_empty("добавь встречу с Марией 5 ноября в 10")
    assert created(say_empty("напомни в этот день в 9 купить цветы")) == ("Купить цветы", datetime(2026, 11, 5, 9))
    say_empty("добавь задачу на пятницу подготовить отчёт")
    assert created(say_empty("добавь туда же встречу в 16"))[1] == datetime(2026, 10, 23, 16)


def test_explicit_same_day_without_context_asks(say_empty):
    question = say_empty("добавь туда же встречу в 16")
    assert question.text == "На какой день поставить?"
    assert created(say_empty("в пятницу"))[1] == datetime(2026, 10, 23, 16)
    assert say_empty("а что ещё в этот день?").intent == "list"  # теперь день известен — пятница
    assert DialogManager._context_ref("что в этот день")[1] == "explicit"


def test_list_follow_ups(say_empty):
    say_empty("добавь встречу с Петром завтра в 10")
    say_empty("добавь ужин с семьёй завтра в 19")
    assert len(say_empty("что у меня завтра").items) == 2
    evening = say_empty("а вечером?")
    assert [it.title for it in evening.items] == ["Ужин с семьёй"]  # вечер завтрашнего дня, а не сегодняшнего

    say_empty("добавь встречу с бухгалтером 28 октября в 11")
    say_empty("покажи планы на следующую неделю")
    wednesday = say_empty("а что в среду?")
    assert [it.due_at for it in wednesday.items] == [datetime(2026, 10, 28, 11)]  # среда следующей недели

    say_empty("что у меня в четверг")
    assert say_empty("добавь встречу в 15").intent == "clarify"  # «в 15» — это завтра или обсуждаемый четверг?
    assert say_empty("на четверг").items[0].due_at == datetime(2026, 10, 22, 15)
    same_day = say_empty("а что ещё в этот день?")
    assert same_day.intent == "list" and all(it.due_at.date() == datetime(2026, 10, 22).date() for it in same_day.items)


def test_explicit_reference_in_list_without_context(say_empty):
    reply = say_empty("а что ещё в этот день?")
    assert reply.intent == "clarify" and "О каком дне речь" in reply.text


def test_discussed_day_is_forgotten_after_a_few_turns(say_empty):
    say_empty("что у меня в пятницу")
    for _ in range(FOCUS_TURNS):
        say_empty("привет")
    assert created(say_empty("добавь встречу с Олегом в 12"))[1] == datetime(2026, 10, 21, 12)
