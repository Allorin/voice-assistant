"""Исправление падежей в названиях из исходного CSV."""
import csv
from datetime import timedelta

import pytest

from app.grammar import fix_cases
from app.models import ItemType, NewItem
from app.search.text import _morph
from app.storage.importer import read_csv
from tests.conftest import NOW, ROOT


@pytest.mark.parametrize("wrong, right", [
    ("Позвонить Сергеем", "Позвонить Сергею"),
    ("Отправить презентацию Натальей", "Отправить презентацию Наталье"),
    ("Отправить итоги встречи Марией", "Отправить итоги встречи Марии"),
    ("Отправить коммерческое предложение Ольгой", "Отправить коммерческое предложение Ольге"),
    ("Подготовить вопросы для Дмитрием", "Подготовить вопросы для Дмитрия"),
    ("Напомнить Сергеем про презентации", "Напомнить Сергею про презентацию"),
    ("Напомнить Анной про мобильному приложению", "Напомнить Анне про мобильное приложение"),
    ("Напомнить Иваном про новому продукту", "Напомнить Ивану про новый продукт"),
    ("Напомнить Еленой про хакатону", "Напомнить Елене про хакатон"),  # слова нет в словаре pymorphy
    ("Напомнить Дмитрием про CRM", "Напомнить Дмитрию про CRM"),
    ("Обсудить договору с Ольгой", "Обсудить договор с Ольгой"),
    ("Обсудить аналитике с Алексеем", "Обсудить аналитику с Алексеем"),
    ("Обсудить мобильному приложению с Иваном", "Обсудить мобильное приложение с Иваном"),
    ("Созвон с Стартом по CRM", "Созвон со Стартом по CRM"),
    ("Подготовиться к встрече с Стартом", "Подготовиться к встрече со Стартом"),
])
def test_generator_mistakes_are_fixed(wrong, right):
    assert fix_cases(wrong) == right


@pytest.mark.parametrize("title", [
    "Встреча с Натальей по презентации", "Проверить сроки по мобильному приложению", "Демо проекта по хакатону",
    "Передать техническое задание на согласование", "Проверить статус задачи по CRM", "Позвонить маме",
    "Поздравить Ивана", "Помочь Анне с переездом", "Купить подарок для мамы", "Напомнить про задачи",
    "Подумать о проекте", "Позвонить в банк", "Созвон с Севером по бюджету", "Обсудить CRM с Еленой",
    "Отправить отчёт", "Подготовиться к встрече с Партнёром",
])
def test_correct_titles_are_kept(title):
    assert fix_cases(title) == title


def test_whole_seed_has_no_generator_mistakes():
    with open(ROOT / "data" / "events_seed.csv", encoding="utf-8-sig") as f:
        titles = {r["text"].strip() for r in csv.DictReader(f)}
    fixed = {fix_cases(t) for t in titles}
    for title in fixed:
        words = title.split()
        for prev, word in zip(words, words[1:]):
            tags = [str(p.tag) for p in _morph.parse(word)]
            if all("Name" in t and "ablt" in t for t in tags):
                assert prev in ("с", "со"), title  # имя в творительном — только «с кем»
            if prev == "про":
                assert any("accs" in t for t in tags) or word.isascii(), title
    assert all(fix_cases(t) == t for t in fixed), "исправление не меняет уже исправленное"
    assert len(titles - fixed) > 100


def test_import_fixes_titles_and_keeps_types():
    items = {i.title: i.type for i in read_csv(ROOT / "data" / "events_seed.csv")}
    assert items["Позвонить Сергею"] == ItemType.TASK
    assert items["Напомнить Сергею про презентацию"] == ItemType.REMINDER
    assert items["Обсудить договор с Ольгой"] == ItemType.MEETING
    assert "Позвонить Сергеем" not in items


def test_existing_database_is_fixed_on_start(empty_container):
    repo = empty_container.repo
    old = [NewItem(type=ItemType.TASK, title="Позвонить Сергеем", due_at=NOW + timedelta(days=1), source="import"),
           NewItem(type=ItemType.TASK, title="Позвонить Сергеем", due_at=NOW + timedelta(days=2), source="import"),
           NewItem(type=ItemType.TASK, title="Позвонить Сергеем", due_at=NOW, source="user")]
    repo.add_many("demo", old, NOW)
    assert repo.rename_imported(fix_cases) == 2
    titles = sorted(i.title for i in repo.list_all("demo"))
    assert titles == ["Позвонить Сергеем", "Позвонить Сергею", "Позвонить Сергею"], "свои записи не трогаем"
    assert repo.rename_imported(fix_cases) == 0
