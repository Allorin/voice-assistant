"""Нормализация текста для поиска: леммы (pymorphy3), стоп-слова, синонимы.

Лемматизация нужна, потому что в данных и запросах слова в разных падежах:
«Позвонить Ивану» / «встреча с Иваном» / «про Ивана» → везде «иван».
"""
from __future__ import annotations

import re
from functools import lru_cache

import pymorphy3

_morph = pymorphy3.MorphAnalyzer()
_WORD = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)

STOP = set("""
и или а но же ли бы то не ни у в во на с со к ко по о об обо про для за из от до при над под без через
я мы ты вы он она оно они меня мне мной нас нам нами мой моя моё мое мои свой своя
это этот эта эту эти тот та те то там тут где когда что как какой какая какие который
есть был была было были будет будут быть все всё весь найти найди поищи показать покажи
""".split())

# Группы близких по смыслу слов (в леммах). Совпадение по синониму весит меньше прямого.
SYNONYM_GROUPS: list[set[str]] = [
    {"встреча", "созвон", "совещание", "планерка", "митинг", "собрание", "переговоры", "звонок"},
    {"позвонить", "звонок", "созвон", "созвониться", "созваниваться", "набрать"},
    {"договор", "контракт", "соглашение", "договоренность"},
    {"отчет", "отчетность", "доклад", "итог"},
    {"презентация", "слайд", "питч", "демо"},
    {"смета", "бюджет", "расход", "стоимость"},
    {"протокол", "итог", "резюме", "заметка"},
    {"согласовать", "утвердить", "одобрить", "согласование"},
    {"проверить", "посмотреть", "просмотреть", "ревью", "проверка"},
    {"отправить", "выслать", "переслать", "скинуть", "послать", "передать"},
    {"подготовить", "сделать", "готовить", "подготовиться"},
    {"билет", "бронь", "бронирование"},
    {"обратный", "отзыв", "фидбек"},
    {"приложение", "мобильный", "app"},
    {"crm", "срм"},
]

# Сокращения, которые раскрываются в несколько слов
EXPANSIONS: dict[str, set[str]] = {
    "тз": {"технический", "задание"},
    "техзадание": {"технический", "задание"},
    "кп": {"коммерческий", "предложение"},
    "мп": {"мобильный", "приложение"},
}

_SYN: dict[str, set[str]] = {}
for _group in SYNONYM_GROUPS:
    for _w in _group:
        _SYN.setdefault(_w, set()).update(_group - {_w})
for _w, _exp in EXPANSIONS.items():
    _SYN.setdefault(_w, set()).update(_exp)


@lru_cache(maxsize=100_000)
def is_known_word(word: str) -> bool:
    return _morph.word_is_known(word)


def morph_tag(word: str) -> str:
    """Грамматические признаки самого вероятного разбора слова: «VERB,perf,tran sing,impr»."""
    return str(_morph.parse(word.lower().replace("ё", "е"))[0].tag)


def lemma(word: str) -> str:
    w = word.lower().replace("ё", "е")
    if w.isdigit() or not re.search(r"[а-я]", w):
        return w
    return _morph.parse(w)[0].normal_form.replace("ё", "е")


def lemmas(text: str, drop_stop: bool = True) -> list[str]:
    out = []
    for w in _WORD.findall(text or ""):
        lw = w.lower().replace("ё", "е")
        if drop_stop and lw in STOP:
            continue
        lm = lemma(lw)
        if drop_stop and lm in STOP:
            continue
        out.append(lm)
    return out


def synonyms(term: str) -> set[str]:
    return _SYN.get(term, set())
