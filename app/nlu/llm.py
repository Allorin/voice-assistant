"""Разбор команд через LLM (любой провайдер с OpenAI-совместимым API).

Модель возвращает JSON по схеме Command. Время она НЕ вычисляет — только копирует фразу,
дальше работает детерминированный парсер. Это делает поведение стабильным даже на маленьких бесплатных моделях.
"""
from __future__ import annotations

import json
import logging
import re

from app.models import ItemType
from app.nlu.schema import Command, Intent, NLUContext

log = logging.getLogger(__name__)

_WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

SYSTEM_PROMPT = """Ты — модуль понимания запросов голосового ассистента для задач, напоминаний и встреч.
Превращай реплику пользователя в JSON-команду. Ничего не выполняй сам и не придумывай данные.

Сейчас: {now} ({weekday}).
{context}
Верни ровно один JSON-объект без пояснений и без markdown:
{{"intent": "create|list|search|update|delete|complete|reopen|cancel|help|smalltalk|unknown",
 "item_type": "task|meeting|reminder|null",
 "title": "строка или null",
 "when": "строка или null",
 "repeat": "строка или null",
 "period": "строка или null",
 "bulk": false,
 "query": "строка или null",
 "occurrence": "строка или null",
 "scope": "one|series|null",
 "target_last": false,
 "tense": "past|future|any",
 "ask_when": false,
 "status": "open|done|null",
 "reply": "строка или null"}}

Правила:
- create — просят поставить, добавить, запомнить, напомнить или сообщают о будущей встрече/деле.
  item_type: "meeting" для встреч, созвонов, планёрок, совещаний; "reminder", если просят напомнить; иначе "task".
  title — суть без слов-команд и без времени, с заглавной буквы: «Напомни завтра позвонить Ивану» → «Позвонить Ивану»;
  для встречи — «Встреча с командой».
- when и period — фраза о времени, скопированная ДОСЛОВНО из реплики («завтра в 10 утра», «в пятницу»,
  «через два часа», «до конца недели», «на этой неделе»). НЕ вычисляй даты и не переводи слова в числа.
  Для интервала копируй ОБЕ границы: «завтра с 13:40 до 13:50». Нельзя оставлять только «в 13:50».
  «Напомни, что завтра с 13:40 до 13:50 у меня встреча» — meeting: встреча занимает весь интервал.
- repeat — только для повторяющихся записей: фраза о повторении дословно («каждый понедельник», «по будням»,
  «каждые 2 недели», «15 числа каждого месяца», «каждое утро»). Время суток при этом остаётся в when («в 10»).
- list — спрашивают, что запланировано («что у меня сегодня», «какие планы на завтра», «покажи встречи на неделю»).
  item_type — фильтр, если спросили только про встречи, задачи или напоминания; иначе null.
- search — найти запись по теме или человеку («найди задачу про презентацию», «когда была встреча с Иваном»).
  query — только ключевые слова (тема, имя), без «найди», «задача», «встреча». tense: "past" — о прошлом («была»),
  "future" — о будущем («будет»), иначе "any". ask_when=true, если вопрос «когда…?».
- status для list/search: "open" — спрашивают о невыполненном («что не сделано», «что осталось»),
  "done" — о выполненном («что я уже сделал»), иначе null.
- update — перенести или переименовать запись. when — новое время, title — новое название (только при переименовании).
  Поправки «нет, неправильно, с 13:40 до 13:50», «не так, завтра в 10» — update, target_last=true.
  Исправляй последнюю обсуждавшуюся запись; не создавай новую только из-за поправки времени.
- Повторяющиеся записи. occurrence — какое именно повторение имеется в виду, фраза дословно («в понедельник»,
  «завтрашнюю», «2 ноября»); when — куда перенести. «Перенеси планёрку в понедельник на 12» → occurrence
  «в понедельник», when «на 12». scope: "one" — только одно повторение («только эту», «в этот раз»,
  «в пятницу созвона не будет» — это delete), "series" — вся серия («всю серию», «все планёрки», «навсегда»),
  иначе null — ассистент сам уточнит.
- reopen — снять отметку о выполнении («сними галочку», «верни в работу», «я случайно отметил»).
- update, delete, complete, reopen: если запись не названа («перенеси её», «удали эту», «готово») — target_last=true;
  иначе query — как найти запись («встреча с Марией» → query «Мария», item_type «meeting»).
- «Удали все планы на сегодня», «удали все встречи на завтра» — delete, bulk=true,
  period — дословный период, target_last=false. Для всех планов query=null и item_type=null.
  Никогда не заменяй «все планы» последней обсуждавшейся записью. Отсутствующий период не придумывай.
- cancel — «отмена», «не надо». help — «что ты умеешь». smalltalk — приветствие или благодарность,
  тогда reply — короткий дружелюбный ответ на русском.
- unknown — запрос не про задачи, встречи и напоминания."""

FEW_SHOT: list[tuple[str, dict]] = [
    ("Напомни мне завтра в 10 утра позвонить Ивану",
     {"intent": "create", "item_type": "reminder", "title": "Позвонить Ивану", "when": "завтра в 10 утра"}),
    ("В четверг в 11 у меня встреча с дизайнером",
     {"intent": "create", "item_type": "meeting", "title": "Встреча с дизайнером", "when": "в четверг в 11"}),
    ("Какие планы на сегодня?", {"intent": "list", "period": "на сегодня"}),
    ("Когда у меня была встреча с Иваном?",
     {"intent": "search", "item_type": "meeting", "query": "Иван", "tense": "past", "ask_when": True}),
    ("А перенеси её на пятницу", {"intent": "update", "when": "на пятницу", "target_last": True}),
    ("Отметь задачу про отчёт выполненной", {"intent": "complete", "item_type": "task", "query": "отчёт"}),
    ("Каждый понедельник в 10 у меня планёрка",
     {"intent": "create", "item_type": "meeting", "title": "Планёрка", "when": "в 10",
      "repeat": "каждый понедельник"}),
    ("Сними галочку с задачи про билеты", {"intent": "reopen", "item_type": "task", "query": "билеты"}),
    ("Перенеси планёрку в понедельник на 12",
     {"intent": "update", "item_type": "meeting", "query": "планёрка", "occurrence": "в понедельник", "when": "на 12"}),
    ("В пятницу созвона с Альфой не будет",
     {"intent": "delete", "item_type": "meeting", "query": "Альфа", "occurrence": "в пятницу", "scope": "one"}),
]

_DEFAULTS = {"item_type": None, "title": None, "when": None, "repeat": None, "period": None, "query": None,
             "bulk": False,
             "occurrence": None, "scope": None, "target_last": False, "tense": "any", "ask_when": False,
             "status": None, "reply": None}


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"в ответе модели нет JSON: {text[:200]!r}")
    return json.loads(text[start : end + 1])


def _normalize(data: dict) -> dict:
    out = dict(_DEFAULTS)
    for key, value in data.items():
        if key not in out and key != "intent":
            continue
        if isinstance(value, str) and value.strip().lower() in ("", "null", "none"):
            value = None
        out[key] = value
    intent = str(data.get("intent") or "unknown").lower().strip()
    out["intent"] = intent if intent in Intent._value2member_map_ else "unknown"
    if out["item_type"] not in ItemType._value2member_map_:
        out["item_type"] = None
    if out["tense"] not in ("past", "future", "any"):
        out["tense"] = "any"
    if out["status"] not in ("open", "done"):
        out["status"] = None
    if out["scope"] not in ("one", "series"):
        out["scope"] = None
    out["target_last"] = bool(out["target_last"])
    out["bulk"] = out["bulk"] is True
    out["ask_when"] = bool(out["ask_when"])
    return out


class LLMNLU:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 20.0):
        from openai import OpenAI

        self.model = model
        self.client = OpenAI(base_url=base_url, api_key=api_key or "none", timeout=timeout, max_retries=1)
        self._json_mode = True

    def _messages(self, text: str, ctx: NLUContext) -> list[dict]:
        context = ""
        if ctx.last_item_title:
            context += f"Последняя обсуждавшаяся запись: «{ctx.last_item_title}».\n"
        if ctx.history:
            lines = [f"Пользователь: {u}\nАссистент: {a[:200]}" for u, a in ctx.history[-2:]]
            context += "Недавний диалог (для понимания «её», «туда» и т.п.):\n" + "\n".join(lines) + "\n"
        system = SYSTEM_PROMPT.format(
            now=ctx.now.strftime("%Y-%m-%d %H:%M"), weekday=_WEEKDAYS[ctx.now.weekday()], context=context
        )
        messages: list[dict] = [{"role": "system", "content": system}]
        for user_text, answer in FEW_SHOT:
            messages.append({"role": "user", "content": user_text})
            messages.append({"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)})
        messages.append({"role": "user", "content": text})
        return messages

    def _complete(self, messages: list[dict]) -> str:
        kwargs = {"model": self.model, "messages": messages, "temperature": 0}
        if self._json_mode:
            try:
                resp = self.client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
                return resp.choices[0].message.content or ""
            except Exception as exc:  # провайдер может не поддерживать json-режим
                log.info("json_object не поддерживается (%s), повтор без него", exc)
                self._json_mode = False
        resp = self.client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""

    def parse(self, text: str, ctx: NLUContext) -> Command:
        raw = self._complete(self._messages(text, ctx))
        cmd = Command.model_validate(_normalize(_extract_json(raw)))
        cmd.source = "llm"
        return cmd

    def close(self) -> None:
        self.client.close()
