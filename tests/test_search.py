from app.models import ItemType
from app.storage.importer import classify_title


def titles(hits, n=5):
    return [h.item.title for h in hits[:n]]


def test_inflected_names(container):
    hits = container.service.search("demo", "Иван")
    assert hits and all("Иван" in t for t in titles(hits, 20))


def test_synonym_contract_finds_dogovor(container):
    hits = container.service.search("demo", "контракт")
    assert hits and all("договор" in t.lower() for t in titles(hits))


def test_typo(container):
    hits = container.service.search("demo", "презинтация")
    assert hits and all("презентац" in t.lower() for t in titles(hits))


def test_abbreviation(container):
    hits = container.service.search("demo", "ТЗ")
    assert hits and all("техническое задание" in t.lower() for t in titles(hits))


def test_type_filter_and_tense(container):
    hits = container.service.search("demo", "Иван", ItemType.MEETING, tense="past")
    assert hits
    assert all(h.item.type == ItemType.MEETING for h in hits)
    assert all(h.item.due_at < container.service.now() for h in hits)


def test_multiword_query_prefers_full_match(container):
    hits = container.service.search("demo", "встреча с Иваном по CRM")
    assert titles(hits, 1) == ["Встреча с Иваном по CRM"]


def test_csv_type_classification():
    assert classify_title("Встреча с Марией по презентации") == ItemType.MEETING
    assert classify_title("Созвон с Альфой") == ItemType.MEETING
    assert classify_title("Напомнить Анной про CRM") == ItemType.REMINDER
    assert classify_title("Подготовиться к встрече с Пульсом") == ItemType.TASK
