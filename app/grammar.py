"""Исправление падежей в названиях из исходного CSV.

Генератор тестовых данных подставлял имя всегда в творительном падеже, а тему — всегда в дательном,
независимо от того, чего требует глагол или предлог: «Позвонить Сергеем», «Отправить договор Натальей»,
«Подготовить вопросы для Иваном», «Обсудить договору с Ольгой», «Напомнить Анной про мобильному приложению».
Здесь падеж ставится по управляющему слову (pymorphy3):

- после предлога — падеж, которого требует предлог: «для Сергея», «про мобильное приложение»;
- имя без предлога в творительном после глагола или дополнения — получатель, дательный: «Позвонить Сергею»;
- неодушевлённое слово в дательном сразу после переходного глагола — прямое дополнение: «Обсудить договор»;
- «с» перед «ст-», «зд-», «шк-»… → «со»: «со Стартом».

Слово меняется, только если его форма невозможна в этом месте; правильные названия остаются как есть.
Применяется к импортируемым записям, а не к тому, что говорит пользователь: там распознанная речь.
"""
from __future__ import annotations

import re
from functools import lru_cache

from app.search.text import _morph

# Падежи после предлогов: первый — в который ставим, остальные тоже допустимы.
PREP_CASES: dict[str, tuple[str, ...]] = {
    "про": ("accs",), "через": ("accs",), "сквозь": ("accs",),
    "для": ("gent",), "без": ("gent",), "у": ("gent",), "от": ("gent",), "до": ("gent",), "из": ("gent",),
    "после": ("gent",), "около": ("gent",), "кроме": ("gent",), "вместо": ("gent",), "среди": ("gent",),
    "к": ("datv",), "ко": ("datv",), "благодаря": ("datv",),
    "о": ("loct",), "об": ("loct",), "обо": ("loct",), "при": ("loct",),
    "с": ("ablt", "gent"), "со": ("ablt", "gent"), "над": ("ablt",), "под": ("ablt", "accs"),
    "перед": ("ablt",), "между": ("ablt",), "за": ("accs", "ablt"),
    "на": ("accs", "loct"), "в": ("accs", "loct"), "во": ("accs", "loct"),
    "по": ("datv", "loct", "accs"),
}

# Падежи, в которые генератор ставил слова в любом месте фразы: такую форму считаем исходной.
_SLOT_CASES = ("ablt", "datv")
_PERSON = {"Name", "Surn", "Patr"}
_TOKEN = re.compile(r"^([«\"(]*)([A-Za-zА-Яа-яЁё0-9-]+)([»\")\.,!?;:]*)$")
_SO_BEFORE = re.compile(r"^[сзшжщ][^аеёиоуыэюя]", re.IGNORECASE)


def _parses(word: str):
    return _morph.parse(word)


def _observed(word: str):
    """Форма, в которой генератор, вероятно, оставил слово: творительный или дательный, ед. ч."""
    for case in _SLOT_CASES:
        found = [p for p in _parses(word) if p.tag.POS == "NOUN" and p.tag.case == case and p.tag.number == "sing"]
        if found:
            # в дательном у генератора темы, в творительном — люди; для незнакомых слов («хакатону»)
            # pymorphy первым предлагает «имя», поэтому выбираем разбор под слот
            want_person = case == "ablt"
            return next((p for p in found if bool(_PERSON & set(p.tag.grammemes)) == want_person), found[0])
    return None


def _is_person(word: str, case: str | None = None) -> bool:
    """Может ли слово быть именем (в падеже case). Для незнакомых слов pymorphy гадает «имя» — им не верим."""
    if case is not None and not _morph.word_is_known(word.lower()):
        return False
    return any(p.tag.POS == "NOUN" and _PERSON & set(p.tag.grammemes) and (case is None or p.tag.case == case)
               for p in _parses(word))


def _fits(word: str, cases: tuple[str, ...]) -> bool:
    """Может ли слово стоять в одном из падежей. Единственное число важнее: «про презентации» — это
    скорее неверный дательный, чем множественное число, а «про аналитику» — верный винительный."""
    nouns = [p for p in _parses(word) if p.tag.POS == "NOUN"]
    pool = [p for p in nouns if p.tag.number == "sing"] or nouns
    return any(p.tag.case in cases for p in pool)


def _same_case(original: str, new: str) -> str:
    if original[:1].isupper():
        new = new[:1].upper() + new[1:]
    # pymorphy может вернуть «е» вместо «ё» и наоборот — оставляем написание как в источнике
    return original if new.replace("ё", "е").lower() == original.replace("ё", "е").lower() else new


def _inflect_np(words: list[str], source, case: str) -> list[str] | None:
    """Поставить группу «прилагательные + существительное» в падеж case, сохранив число."""
    number = source.tag.number or "sing"
    head = source.inflect({case, number})
    if head is None:
        return None
    out = []
    for w in words[:-1]:
        adj = next((p for p in _parses(w) if p.tag.POS in ("ADJF", "PRTF") and p.tag.case == source.tag.case
                    and (number == "plur" or p.tag.gender == source.tag.gender)), None)
        if adj is None:
            return None
        want = {case, number}
        if number == "sing":
            want.add(source.tag.gender)
        if case == "accs" and (number == "plur" or source.tag.gender == "masc"):
            want.add(source.tag.animacy or "inan")
        form = adj.inflect(want)
        if form is None:
            return None
        out.append(_same_case(w, form.word))
    out.append(_same_case(words[-1], head.word))
    return out


def _chunks(words: list[str]) -> list[tuple[str, list[int]]]:
    """Разметка: verb / prep / np (прилагательные + существительное) / other."""
    out: list[tuple[str, list[int]]] = []
    adjs: list[int] = []
    for i, w in enumerate(words):
        low = w.lower()
        if not re.fullmatch(r"[а-яё-]+", low):
            adjs = []
            out.append(("np" if re.fullmatch(r"[a-z0-9-]+", low) else "other", [i]))  # CRM, 1С — несклоняемые
            continue
        p = _parses(w)[0]
        if low in PREP_CASES and "PREP" in p.tag:
            adjs = []
            out.append(("prep", [i]))
        elif i == 0 and p.tag.POS in ("INFN", "VERB"):
            out.append(("verb", [i]))
        elif p.tag.POS in ("ADJF", "PRTF") and i + 1 < len(words):
            adjs.append(i)  # «мобильному» в «мобильному приложению»
        elif p.tag.POS == "NOUN" or _observed(w) is not None:
            out.append(("np", adjs + [i]))
            adjs = []
        else:
            adjs = []
            out.append(("other", [i]))
    return out


@lru_cache(maxsize=4096)
def fix_cases(title: str) -> str:
    raw = title.split(" ")
    parts = [_TOKEN.match(t) for t in raw]
    if not all(parts):
        return title  # необычные символы — не трогаем
    words = [m.group(2) for m in parts]
    chunks = _chunks(words)
    verb = None
    if chunks and chunks[0][0] == "verb":
        verb = _parses(words[0])[0]

    prev: tuple[str, list[int]] | None = None
    first_after_verb = True
    for kind, idx in chunks:
        if kind == "np" and prev is not None:
            head = words[idx[-1]]
            np_words = [words[i] for i in idx]
            obs = _observed(head)
            target = None
            if obs is None:
                pass  # слово не в «ошибочном» падеже генератора — не трогаем
            elif prev[0] == "prep":
                allowed = PREP_CASES[words[prev[1][0]].lower()]
                if not _fits(head, allowed):
                    target = allowed[0]  # «для Сергеем» → «для Сергея», «про договору» → «про договор»
            elif obs.tag.case == "ablt" and _is_person(head) and prev[0] in ("verb", "np"):
                target = "datv"  # «Позвонить Сергеем», «Отправить договор Натальей» — кому
            elif (prev[0] == "verb" and first_after_verb and obs.tag.case == "datv" and verb is not None
                  and "tran" in verb.tag and not _fits(head, ("accs",)) and not _is_person(head, "datv")):
                target = "accs"  # «Обсудить договору» — что; «Напомнить Максиму» — кому, не трогаем
            if target is not None:
                fixed = _inflect_np(np_words, obs, target)
                if fixed is not None:
                    for i, w in zip(idx, fixed):
                        words[i] = w
        if kind != "verb":
            first_after_verb = first_after_verb and kind != "np"
        prev = (kind, idx)

    # «с Стартом» → «со Стартом»
    for i in range(len(words) - 1):
        if words[i].lower() == "с" and _SO_BEFORE.match(words[i + 1]):
            words[i] = "Со" if words[i] == "С" else "со"

    return " ".join(m.group(1) + w + m.group(3) for m, w in zip(parts, words))
