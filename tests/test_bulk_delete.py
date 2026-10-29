"""Удаление по периоду: явный выбор, подтверждение и защита от устаревшего контекста."""
from datetime import datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient

from app import main
from app.models import ItemStatus, ItemType, NewItem
from app.nlu.hybrid import HybridNLU
from app.nlu.rules import RuleNLU
from app.nlu.schema import Command, Intent, NLUContext
from tests.conftest import NOW


def add(c, title, days=0, item_type=ItemType.TASK, user="demo", **extra):
    due = datetime.combine(NOW.date() + timedelta(days=days), time(10))
    return c.service.create(user, NewItem(type=item_type, title=title, due_at=due, has_time=True, **extra))


@pytest.mark.parametrize(("phrase", "item_type", "query"), [
    ("Удали все планы на сегодня", None, None),
    ("Удали всё на сегодня", None, None),
    ("Убери все мои записи на завтра", None, None),
    ("Удали встречи на сегодня", ItemType.MEETING, None),
    ("Удали все задачи на этой неделе", ItemType.TASK, None),
    ("Удали все встречи с Гошей на сегодня", ItemType.MEETING, "Гошей"),
])
def test_bulk_command_keeps_scope_and_period(phrase, item_type, query):
    cmd = RuleNLU().parse(phrase, NLUContext(NOW))
    assert cmd.intent == Intent.DELETE and cmd.bulk
    assert cmd.item_type == item_type and cmd.query == query
    assert cmd.period and not cmd.target_last and cmd.scope is None and cmd.occurrence is None


def test_screenshot_does_not_delete_last_meeting_tomorrow(empty_container):
    c = empty_container
    first = add(c, "Отчёт")
    second = add(c, "Позвонить Ивану", item_type=ItemType.REMINDER)
    tomorrow = c.dialog.handle("s", "Добавь встречу с Гошей завтра в 16:15").items[0]
    response = c.dialog.handle("s", "Удали все планы на сегодня")
    assert response.awaiting == "confirm"
    assert "2 записи" in response.text and "20.10.2026" in response.text
    assert "Отчёт" in response.text and "Гошей" not in response.text
    assert c.repo.count() == 3
    response = c.dialog.handle("s", "да, удалить")
    assert response.intent == "delete" and set(response.changed) == {first.id, second.id}
    assert [it.id for it in c.repo.list_all("demo")] == [tomorrow.id]


def test_empty_day_never_falls_back_to_context(empty_container):
    c = empty_container
    tomorrow = c.dialog.handle("s", "Добавь встречу с Гошей завтра в 16:15").items[0]
    response = c.dialog.handle("s", "Удали все планы на сегодня")
    assert response.awaiting is None and not response.changed and "Ничего не удалено" in response.text
    assert c.service.get("demo", tomorrow.id) is not None


@pytest.mark.parametrize("answer", ["нет", "отмена", "да, но только встречи", "давай посмотрим"])
def test_bulk_requires_unambiguous_confirmation(empty_container, answer):
    c = empty_container
    item = add(c, "Отчёт")
    c.dialog.handle("s", "Удали все планы на сегодня")
    c.dialog.handle("s", answer)
    assert c.service.get("demo", item.id) is not None


def test_bulk_without_period_asks_instead_of_deleting_everything(empty_container):
    c = empty_container
    add(c, "Сегодня")
    tomorrow = add(c, "Завтра", days=1)
    assert c.dialog.handle("s", "Удали всё").awaiting == "period"
    assert c.dialog.handle("s", "завтра").awaiting == "confirm"
    assert c.repo.count() == 2
    response = c.dialog.handle("s", "да")
    assert response.changed == [tomorrow.id] and c.repo.count() == 1


def test_bulk_keeps_type_topic_status_and_user_boundaries(empty_container):
    c = empty_container
    selected = add(c, "Встреча с Гошей", item_type=ItemType.MEETING, status=ItemStatus.DONE)
    add(c, "Встреча с Гошей", item_type=ItemType.MEETING)
    add(c, "Встреча с Марией", item_type=ItemType.MEETING, status=ItemStatus.DONE)
    add(c, "Позвонить Гоше", status=ItemStatus.DONE)
    add(c, "Встреча с Гошей", item_type=ItemType.MEETING, status=ItemStatus.DONE, user="other")
    response = c.dialog.handle("s", "Удали все выполненные встречи с Гошей на сегодня")
    assert response.awaiting == "confirm" and "1 встреча" in response.text
    assert c.dialog.handle("s", "да").changed == [selected.id]
    assert c.repo.count() == 4


def test_series_only_loses_occurrences_inside_period(empty_container):
    c = empty_container
    master = add(c, "Зарядка", days=-1, item_type=ItemType.REMINDER, recurrence="FREQ=DAILY")
    c.dialog.handle("s", "Удали все планы на сегодня")
    response = c.dialog.handle("s", "да")
    assert response.changed == [master.id]
    current = c.service.get("demo", master.id)
    assert current.skipped_dates == [NOW.date()]
    assert c.service.occurrence_on(current, NOW.date()) is None
    assert c.service.occurrence_on(current, NOW.date() + timedelta(days=1)) is not None
    assert c.service.occurrence_on(current, NOW.date() - timedelta(days=1)) is not None


def test_bulk_week_can_skip_multiple_dates_of_same_series(empty_container):
    c = empty_container
    master = add(c, "Зарядка", days=-1, item_type=ItemType.REMINDER, recurrence="FREQ=DAILY")
    response = c.dialog.handle("s", "Удали все планы на этой неделе")
    assert response.awaiting == "confirm" and "7 записей" in response.text
    c.dialog.handle("s", "да")
    current = c.service.get("demo", master.id)
    assert len(current.skipped_dates) == 7
    assert c.service.occurrence_on(current, NOW.date() + timedelta(days=6)) is not None


def test_changed_selection_requires_another_confirmation(empty_container):
    c = empty_container
    add(c, "Отчёт")
    c.dialog.handle("s", "Удали все планы на сегодня")
    add(c, "Новая задача")
    response = c.dialog.handle("s", "да")
    assert response.awaiting == "confirm" and "изменился" in response.text
    assert "2 записи" in response.text and c.repo.count() == 2
    assert c.dialog.handle("s", "да").intent == "delete" and c.repo.count() == 0


def test_repository_rejects_stale_snapshot_without_partial_deletion(empty_container):
    c = empty_container
    first, second = add(c, "Первая"), add(c, "Вторая")
    c.service.rename("demo", second.id, "Изменённая")
    assert not c.service.delete_confirmed("demo", [first, second])
    assert c.repo.count() == 2 and c.service.get("demo", first.id) is not None


def test_confirmation_keeps_original_dates_across_midnight(empty_container):
    c = empty_container
    today, tomorrow = add(c, "Сегодня"), add(c, "Завтра", days=1)
    c.dialog.handle("s", "Удали все планы на сегодня")
    c.service.clock.fixed += timedelta(days=1)
    assert c.dialog.handle("s", "да").changed == [today.id]
    assert c.service.get("demo", tomorrow.id) is not None


def test_single_delete_with_missing_date_does_not_relax_to_tomorrow(empty_container):
    c = empty_container
    tomorrow = add(c, "Встреча с Гошей", days=1, item_type=ItemType.MEETING)
    response = c.dialog.handle("s", "Удали встречу с Гошей на сегодня")
    assert not response.changed and "Не нашлось" in response.text
    assert c.service.get("demo", tomorrow.id) is not None
    c.dialog.sessions.get("s").last_ids = [tomorrow.id]
    response = c.dialog.handle("s", "Удали её на сегодня")
    assert not response.changed and c.repo.count() == 1


def test_single_generic_delete_uses_date_instead_of_old_context(empty_container):
    c = empty_container
    today = add(c, "Сегодняшняя встреча", item_type=ItemType.MEETING)
    tomorrow = add(c, "Завтрашняя встреча", days=1, item_type=ItemType.MEETING)
    c.dialog.sessions.get("s").last_ids = [tomorrow.id]
    assert c.dialog.handle("s", "Удали встречу на сегодня").changed == [today.id]
    assert c.service.get("demo", tomorrow.id) is not None


def test_choice_is_rechecked_if_item_moves_to_another_day(empty_container):
    c = empty_container
    first = add(c, "Встреча с Гошей", item_type=ItemType.MEETING)
    add(c, "Встреча с Гошей", item_type=ItemType.MEETING)
    assert c.dialog.handle("s", "Удали встречу с Гошей на сегодня").awaiting == "choice"
    c.service.reschedule("demo", first.id, first.due_at + timedelta(days=1), True)
    response = c.dialog.handle("s", "первую")
    assert not response.changed and c.repo.count() == 2


def test_new_command_interrupts_bulk_confirmation(empty_container):
    c = empty_container
    add(c, "Сегодня")
    c.dialog.handle("s", "Удали все планы на сегодня")
    assert c.dialog.handle("s", "Что у меня сегодня?").intent == "list"
    assert c.repo.count() == 1


class WrongDeleteLLM:
    calls = 0

    def parse(self, text, ctx):
        self.calls += 1
        return Command(intent=Intent.DELETE, target_last=True)


def test_model_cannot_replace_bulk_scope_with_last_item(empty_container):
    c = empty_container
    model = WrongDeleteLLM()
    c.dialog.nlu = HybridNLU(RuleNLU(), model, always_llm=True)
    add(c, "Сегодня")
    tomorrow = add(c, "Завтра", days=1)
    c.dialog.sessions.get("s").last_ids = [tomorrow.id]
    assert c.dialog.handle("s", "Удали все планы на сегодня").awaiting == "confirm"
    assert c.repo.count() == 2 and model.calls == 0
    assert c.dialog.handle("s", "Что у меня завтра?").intent == "list"
    assert c.repo.count() == 2


def test_delete_word_inside_new_task_is_not_executed(empty_container):
    c = empty_container
    add(c, "Старые файлы")
    response = c.dialog.handle("s", "Добавь задачу удалить все старые файлы завтра")
    assert response.intent == "create" and c.repo.count() == 2


@pytest.mark.parametrize("phrase", ["Найди задачу удалить старые файлы", "Покажи задачи удалить старые файлы",
                                  "Не удаляй все планы на сегодня", "Как удалить все планы на сегодня?",
                                  "Я удалил встречу с Гошей на сегодня"])
def test_deletion_mentioned_in_question_search_or_negation_is_not_executed(empty_container, phrase):
    c = empty_container
    add(c, "Удалить старые файлы")
    add(c, "Встреча с Гошей", item_type=ItemType.MEETING)
    response = c.dialog.handle("s", phrase)
    assert response.intent != "delete" and not response.changed and c.repo.count() == 2


def test_invalid_interval_and_change_of_period_require_fresh_preview(empty_container):
    c = empty_container
    today, tomorrow = add(c, "Сегодня"), add(c, "Завтра", days=1)
    response = c.dialog.handle("s", "Удали все планы сегодня с 13:50 до 13:40")
    assert response.awaiting == "period" and c.repo.count() == 2
    assert c.dialog.handle("s", "сегодня").awaiting == "confirm"
    response = c.dialog.handle("s", "нет, завтра")
    assert response.awaiting == "confirm" and "21.10.2026" in response.text
    assert c.repo.count() == 2
    assert c.dialog.handle("s", "да").changed == [tomorrow.id]
    assert c.service.get("demo", today.id) is not None


def test_api_confirmation_and_sessions_are_isolated_per_user(empty_container):
    c = empty_container
    main.container = c
    client = TestClient(main.app)
    alice = add(c, "Отчёт Alice", user="alice")
    bob = add(c, "Отчёт Bob", user="bob")
    def say(user, text):
        return client.post("/api/chat", json={"text": text, "session_id": "shared"}, headers={"X-User-Id": user}).json()
    assert say("alice", "Удали все планы на сегодня")["awaiting"] == "confirm"
    assert not say("bob", "да")["changed"] and c.repo.count() == 2
    assert say("alice", "да")["changed"] == [alice.id]
    assert c.service.get("bob", bob.id) is not None
    say("bob", "Удали все планы на сегодня")
    client.post("/api/session/reset", json={"text": "", "session_id": "shared"}, headers={"X-User-Id": "alice"})
    assert say("bob", "да")["changed"] == [bob.id]
