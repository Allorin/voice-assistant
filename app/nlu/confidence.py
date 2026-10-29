"""Когда разбору правилами можно верить без LLM.

LLM добавляет около секунды и расходует токены на каждую реплику, хотя обычные команды
(«напомни завтра в 10 позвонить Ивану», «что у меня в пятницу») правила разбирают точно.
Правила считаются уверенными, если каждое слово реплики объяснено: это время, название записи,
что искать, или служебное слово команды. Любое необъяснённое слово, подозрительное название
(с числами, словами о времени, «слушай…») или неразобранное время — и реплика уходит в LLM.
"""
from __future__ import annotations

import re
from datetime import datetime

from app.nlu.schema import Command, Intent
from app.search.text import STOP, lemma, morph_tag
from app.timeparse.ru import parse as parse_time

_WORD = re.compile(r"[a-zа-яё0-9]+(?::[0-9]+)?", re.IGNORECASE)

# Служебные слова команд (в леммах): глаголы-команды, типы записей, вопросы, местоимения
SERVICE = set("""
добавить добавлять поставить ставить записать запиши записывать напомнить напоминать создать запланировать
занести внести сохранить завести перенести переносить сдвинуть передвинуть переставить переназначить
отметить отмечать выполнить выполнять сделать закрыть снять снимать вернуть
показать покажи найти искать поискать посмотреть глянуть рассказать подсказать
задача встреча напоминание запись дело план планы событие
запланированный запланировать выполненный сделанный готовый галочка отметка статус
что какой когда где сколько ли есть у меня мне мой я
пожалуйста ещё еще также тоже же это этот тот её его их она он они все всё весь
""".split())

FILLER = set("""
слушай слушать ну так короче вот давай надо нужно бы пожалуйста мне меня я это ещё еще тоже также
""".split())

# Слова о времени в названии — признак, что разбор времени не справился
TIME_WORDS = set("""
утро вечер день ночь обед полдень полночь час минута неделя месяц год число завтра сегодня послезавтра
вчера позавчера через после понедельник вторник среда четверг пятница суббота воскресенье январь февраль
март апрель май июнь июль август сентябрь октябрь ноябрь декабрь утром вечером днём ночью
""".split())

MAX_TITLE_WORDS = 7
MAX_QUERY_WORDS = 4


def _lemmas(text: str | None) -> list[str]:
    return [lemma(w) for w in _WORD.findall(text or "")]


def _time_ok(phrase: str | None, now: datetime) -> bool:
    if not phrase:
        return True
    info = parse_time(phrase, now)
    return info.found and not info.error


def _title_ok(title: str | None) -> bool:
    words = _lemmas(title)
    if not words or len(words) > MAX_TITLE_WORDS:
        return False
    if words[0] in FILLER or any(w[0].isdigit() or w in TIME_WORDS for w in words):
        return False
    if re.search(r"[?]|\b(?:что|когда|какой|какие|где)\b", title or "", re.IGNORECASE):
        return False
    raw = _WORD.findall(title or "")
    tags = [morph_tag(w) for w in raw]
    if "impr" in tags[0]:
        return False  # «Перекинь стоматолога», «Отправь мне…» — это команда, а не название
    if tags[-1].startswith("PREP"):
        return False  # «Пробежку кроме» — хвост фразы потерялся
    # «но», «а», «не», «бы» внутри — оговорки и уточнения, которые правила не понимают
    return not any((t.startswith("CONJ") and w.lower() != "и") or t.startswith("PRCL") for w, t in zip(raw, tags))


def rules_sure(text: str, cmd: Command, now: datetime) -> bool:
    """Можно ли отвечать по разбору правил, не спрашивая LLM."""
    if cmd.intent in (Intent.DELETE, Intent.UNDO, Intent.CANCEL, Intent.HELP):
        return True
    if cmd.intent == Intent.UNKNOWN or cmd.intent == Intent.SMALLTALK:
        return False  # приветствие LLM ответит живее, а непонятое — может понять
    if cmd.intent == Intent.CREATE and not _title_ok(cmd.title):
        return False
    if cmd.intent in (Intent.SEARCH,) and not cmd.query:
        return False
    if cmd.query and len(_lemmas(cmd.query)) > MAX_QUERY_WORDS:
        return False
    if cmd.intent in (Intent.UPDATE, Intent.COMPLETE, Intent.REOPEN) and not (cmd.query or cmd.target_last):
        return False
    if cmd.intent not in (Intent.LIST, Intent.SEARCH) and re.search(r"\b(?:вс[её]|всех|кроме|если|когда\s+(?:приду|буду|закончу))\b",
                                                       text, re.IGNORECASE):
        return False  # «перенеси всё…», «кроме выходных», «когда приду домой» — правилам не по силам
    for phrase in (cmd.when, cmd.period, cmd.occurrence):
        if not _time_ok(phrase, now):
            return False

    # каждое слово реплики должно быть объяснено
    explained = set(_lemmas(" ".join(filter(None, (cmd.title, cmd.query, cmd.when, cmd.period, cmd.occurrence,
                                                   cmd.repeat)))))
    info = parse_time(text, now)
    explained.update(_lemmas(info.phrase))
    leftover = [w for w in _lemmas(text) if w not in explained and w not in STOP and w not in SERVICE
                and not w.isdigit()]
    return not leftover
