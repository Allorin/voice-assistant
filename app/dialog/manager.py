"""Диалог-менеджер: связывает разбор команды, бизнес-логику и формулировку ответа.

Отвечает за UX-часть: уточняющие вопросы («На какое время поставить встречу?»), контекст
(«А перенеси её на завтра»), выбор из нескольких вариантов, предупреждение о конфликтах и прошедшем времени.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from app.dialog import phrases as ph
from app.dialog.session import Draft, Pending, Session, SessionStore
from app.models import TYPE_FORMS, Item, ItemStatus, ItemType, NewItem
from app.nlu.rules import SHIFT_RX as _SHIFT
from app.nlu.rules import RuleNLU, clean_title, has_create_trigger
from app.nlu.schema import Command, Intent, NLUContext
from app.search.text import lemma
from app.search.engine import Hit
from app.services.items import ItemService, expand_types
from app.timeparse.repeat import parse_repeat
from app.timeparse.ru import TimeInfo, creation_moment, parse, tokenize

_YES = re.compile(r"^(?:да|ага|угу|конечно|давай|ок|окей|хорошо|подтверждаю|верно|всё\s+равно|все\s+равно"
                  r"|добавляй|добавь|оставь|сохраняй|сохрани|yes)\b", re.IGNORECASE)
_NO = re.compile(r"^(?:нет|неа|не\s+надо|не\s+нужно|не\s+стоит|отмена|отмени|no)\b", re.IGNORECASE)
_CANCEL = re.compile(r"^(?:(?:отмена|отмени(?:ть)?|стоп|хватит|не\s+надо|не\s+нужно|забудь|неважно)\b[\s,.!]*)+$",
                     re.IGNORECASE)
_THANKS = re.compile(r"^(?:спасибо|благодарю|спс|отлично|супер|класс|круто)", re.IGNORECASE)
_SCOPE_SERIES_ANSWER = re.compile(r"\b(?:вс[её]|всю|всех|сери\w*|целиком|всегда|навсегда|насовсем|каждую|каждый"
                                  r"|полностью|остальные)\b", re.IGNORECASE)
_SCOPE_ONE_ANSWER = re.compile(r"\b(?:только|одн\w*|один|ближайш\w*|эту|это|этот|е[её]|его|перв\w*|разово|раз)\b",
                               re.IGNORECASE)
_ORDINAL_WORDS = {"перв": 0, "втор": 1, "трет": 2, "четв": 3, "пят": 4}
_BULK_YES = re.compile(r"^(?:да(?:[,\s]+(?:удали(?:ть)?|удаляй)(?:\s+вс[её])?)?|подтверждаю(?:\s+удаление)?"
                       r"|удали(?:ть)?|удаляй|yes)[\s.!]*$", re.IGNORECASE)

_QUESTION_FOR = {Intent.UPDATE: "Какую перенести?", Intent.DELETE: "Какую удалить?",
                 Intent.COMPLETE: "Какую отметить выполненной?", Intent.REOPEN: "С какой снять отметку?"}
_ACT_INTENTS = (Intent.UPDATE, Intent.DELETE, Intent.COMPLETE, Intent.REOPEN)
_WHEN_HEAD = {
    ItemType.MEETING: ("Последняя встреча была", "Ближайшая встреча —"),
    ItemType.TASK: ("Последняя задача была", "Ближайшая задача —"),
    ItemType.REMINDER: ("Последнее напоминание было", "Ближайшее напоминание —"),
    None: ("Последний раз —", "Ближайшая запись —"),
}


@dataclass
class Reply:
    text: str
    speech: str | None = None
    items: list[Item] = field(default_factory=list)
    intent: str = ""
    awaiting: str | None = None   # чего ассистент ждёт в ответ: time / datetime / title / choice / confirm / query
    nlu: str = ""
    changed: list[int] = field(default_factory=list)

    def __post_init__(self):
        if self.speech is None:
            first = self.text.split("\n")[0]
            self.speech = first.replace("•", "").strip()


def parse_ordinal(text: str, n: int) -> int | None:
    low = text.lower().replace("ё", "е")
    idx = None
    if re.search(r"\bпоследн", low):
        idx = n - 1
    m = re.search(r"(?:^|\s|№|номер\s*)(\d)(?:\s|$|[.,!?])", low)
    if m:
        idx = int(m.group(1)) - 1
    m = re.search(r"\b(перв|втор|трет|четв[её]рт|пят)(?:ая|ую|ый|ое|ой|ья|ью|ье|ий|ого)\b", low)
    if m:
        idx = _ORDINAL_WORDS[m.group(1)[:4] if m.group(1).startswith("четв") else m.group(1)]
    if idx is not None and 0 <= idx < n:
        return idx
    return None


def query_label(query: str) -> str:
    """«Иваном» → «Иван», «презентацию» → «презентация» — для аккуратного ответа."""
    words = []
    for w in re.findall(r"[\w-]+", query):
        lw = lemma(w)
        words.append(lw[:1].upper() + lw[1:] if w[:1].isupper() else lw)
    return " ".join(words) or query


def merge_when(item: Item, info: TimeInfo, now: datetime) -> tuple[datetime | None, bool]:
    """Новое время для переноса: «на завтра» сохраняет час встречи, «на 16:00» — её день."""
    if info.has_date and info.has_time:
        return info.moment, True
    if info.has_date and info.moment is not None:
        if item.has_time and item.due_at:
            return datetime.combine(info.moment.date(), item.due_at.time()), True
        return info.moment, False
    if info.has_time and info.moment is not None:
        base = item.due_at.date() if item.due_at else info.moment.date()
        return datetime.combine(base, info.moment.time()), True
    return creation_moment(info, now)


class DialogManager:
    def __init__(self, nlu, service: ItemService, sessions: SessionStore, default_user: str = "demo"):
        self.nlu = nlu
        self.rules = RuleNLU()
        self.service = service
        self.sessions = sessions
        self.default_user = default_user

    # ================================================================ вход

    def handle(self, session_id: str, text: str, user: str | None = None, *, collect_details: bool = False) -> Reply:
        user = user or self.default_user
        s = self.sessions.get(session_id, user)
        s.collect_details = collect_details
        now = self.service.now()
        text = (text or "").strip()
        if not text:
            return Reply("Не удалось разобрать запрос. Повторите, пожалуйста.", intent="unknown")

        reply = self._continue_pending(s, user, text, now) if s.pending else None
        if reply is None:
            ctx = NLUContext(now=now, last_item_title=self._last_title(s, user), history=list(s.history))
            cmd = self.nlu.parse(text, ctx)
            reply = self._dispatch(s, user, cmd, text, now)
            reply.nlu = cmd.source
        if s.carry is not None:
            reply = self._with_carry(s.carry, reply)
            s.carry = None
        s.history = (s.history + [(text, reply.text)])[-6:]
        return reply

    @staticmethod
    def _with_carry(before: Reply, reply: Reply) -> Reply:
        """Ответ на новую команду, перед которым сказано, чем закончилось прерванное уточнение."""
        return Reply(f"{before.text}\n\n{reply.text}", f"{before.speech} {reply.speech}", items=reply.items,
                     intent=reply.intent, awaiting=reply.awaiting, nlu=reply.nlu,
                     changed=[*before.changed, *reply.changed])

    def _dispatch(self, s: Session, user: str, cmd: Command, text: str, now: datetime) -> Reply:
        if cmd.intent == Intent.CREATE:
            return self._create(s, user, cmd, now)
        if cmd.intent == Intent.LIST:
            return self._list(s, user, cmd, now)
        if cmd.intent == Intent.SEARCH:
            return self._search(s, user, cmd, now)
        if cmd.intent in _ACT_INTENTS:
            if cmd.intent == Intent.DELETE and cmd.bulk:
                return self._bulk_delete(s, user, cmd, now)
            return self._act(s, user, cmd, text, now)
        if cmd.intent == Intent.CANCEL:
            had = s.pending is not None
            s.pending = None
            return Reply("Хорошо, отменено." if had else "Хорошо.", intent="cancel")
        if cmd.intent == Intent.HELP:
            return Reply(ph.HELP_TEXT, ph.HELP_SPEECH, intent="help")
        if cmd.intent == Intent.SMALLTALK:
            if cmd.reply:
                return Reply(cmd.reply, intent="smalltalk")
            if _THANKS.match(text):
                return Reply("Пожалуйста! Если что-то ещё — говорите.", intent="smalltalk")
            return Reply("Привет! Могу добавить задачу, напоминание или встречу и рассказать, что запланировано.",
                         intent="smalltalk")
        return Reply("Не получилось понять запрос. Скажите, например: «Напомни завтра в 10 позвонить Ивану» "
                     "или «Что у меня сегодня?»", intent="unknown")

    # ================================================================ создание

    def _create(self, s: Session, user: str, cmd: Command, now: datetime) -> Reply:
        when = cmd.when or ""
        rep = parse_repeat(cmd.repeat) if cmd.repeat else None
        if rep is None or rep.rule is None:
            # LLM могла положить «каждый понедельник в 10» целиком в when
            in_when = parse_repeat(when)
            if in_when.rule is not None:
                rep, when = in_when, in_when.rest
        info = parse(when, now, "future")
        due, has_time = creation_moment(info, now)
        rule = rep.rule if rep else None
        if rule is not None and not has_time and rep.default_time:
            due, has_time = datetime.combine(due.date() if due else now.date(), rep.default_time), True
        draft = Draft(
            type=cmd.item_type or ItemType.TASK,
            title=(cmd.title or "").strip(),
            due=due,
            has_time=has_time,
            has_date=info.has_date or (info.period is not None and rule is None),
            deadline=info.deadline and rule is None,
            duration=info.duration_min,
            recurrence=rule,
            collect_details=s.collect_details,
        )
        if info.error:
            return self._ask(s, info.error, Pending("slot", "create", info.error, slot="datetime", draft=draft),
                             "datetime")
        return self._proceed(s, user, draft, now)

    def _ask(self, s: Session, question: str, pending: Pending, awaiting: str, speech: str | None = None) -> Reply:
        s.pending = pending
        return Reply(question, speech, intent="clarify", awaiting=awaiting)

    def _proceed(self, s: Session, user: str, d: Draft, now: datetime) -> Reply:
        today = now.date()
        if not d.title:
            if d.type == ItemType.MEETING:
                d.title = "Встреча"
            else:
                q = "О чём напомнить?" if d.type == ItemType.REMINDER else "Что нужно сделать?"
                return self._ask(s, q, Pending("slot", "create", q, slot="title", draft=d), "title")
        if d.recurrence is not None:
            # первое повторение серии считается от правила и не может быть в прошлом
            base = d.due.date() if (d.due is not None and d.has_date) else today
            t = d.due.time() if (d.due is not None and d.has_time) else time()
            d.due = self.service.first_occurrence(d.recurrence, datetime.combine(base, t), d.has_time)
        if d.type in (ItemType.MEETING, ItemType.REMINDER):
            if d.due is None:
                q = ("Когда назначить встречу? Назовите день и время." if d.type == ItemType.MEETING
                     else "Когда напомнить? Назовите день и время.")
                return self._ask(s, q, Pending("slot", "create", q, slot="datetime", draft=d), "datetime")
            if not d.has_time:
                q = "На какое время поставить встречу?" if d.type == ItemType.MEETING else "Во сколько напомнить?"
                return self._ask(s, q, Pending("slot", "create", q, slot="time", draft=d), "time")
        if d.due is not None and d.recurrence is None:
            if d.has_time and d.due < now - timedelta(minutes=1):
                q = f"Это время уже прошло ({ph.when_phrase(d.due, True, today)}). На когда поставить?"
                return self._ask(s, q, Pending("slot", "create", q, slot="datetime", draft=d), "datetime")
            if not d.has_time and d.due.date() < today:
                q = f"Эта дата уже прошла ({ph.day_phrase(d.due.date(), today)}). На какой день поставить?"
                return self._ask(s, q, Pending("slot", "create", q, slot="date", draft=d), "date")
        conflict_note = ""
        if d.collect_details and d.type in (ItemType.MEETING, ItemType.REMINDER):
            if not d.details_done:
                if d.type == ItemType.MEETING and d.title.lower() == "встреча":
                    q = "С кем и по какому поводу встреча? Можно также указать место или ссылку."
                else:
                    q = f"Что ещё напомнить о записи «{d.title}»? Например, место, участников или что подготовить."
                q += " Напишите подробности или «пропустить». Чтобы сразу сохранить, скажите «сохранить»."
                return self._ask(s, q, Pending("enrich", "create", q, slot="details", draft=d), "details")
            if not d.lead_done:
                q = ("За сколько времени напомнить? Например, «за 15 минут», «за час» или «в момент начала». "
                     "Можно ответить «пропустить» — напомню в назначенное время.")
                return self._ask(s, q, Pending("enrich", "create", q, slot="remind_before", draft=d), "remind_before")
            if d.remind_before_min and d.due - timedelta(minutes=d.remind_before_min) < now:
                d.remind_before_min = 0
                d.lead_done = False
                q = ("Момент раннего напоминания уже прошёл. За сколько минут напомнить? "
                     "Выберите меньший интервал или скажите «в момент начала».")
                return self._ask(s, q, Pending("enrich", "create", q, slot="remind_before", draft=d), "remind_before")
        if d.type == ItemType.MEETING and d.has_time and d.due is not None:
            conflicts = self.service.conflicts(user, d.due, d.duration)
            if conflicts and not d.conflicts_ok:
                c = conflicts[0]
                q = (f"В это время уже есть встреча «{c.title}» ({ph.when_phrase(c.due_at, True, today, c.duration_min)}). "
                     f"Всё равно добавить?")
                return self._ask(s, q, Pending("confirm", "create", q, draft=d), "confirm")
            if conflicts:
                conflict_note = f" Пересекается с «{conflicts[0].title}»."

        item = self.service.create(user, NewItem(
            type=d.type, title=d.title, due_at=d.due, has_time=d.has_time, duration_min=d.duration,
            notes=d.notes, remind_before_min=d.remind_before_min,
            recurrence=d.recurrence.to_rrule() if d.recurrence else None))
        s.pending = None
        s.last_ids = [item.id]
        extra = f" Напомню за {d.remind_before_min} {ph.plural(d.remind_before_min, ('минуту', 'минуты', 'минут'))}." \
            if d.remind_before_min else ""
        return Reply(self._created_text(item, d, today) + extra + conflict_note, items=[item], intent="create",
                     changed=[item.id])

    @staticmethod
    def _created_text(item: Item, d: Draft, today: date) -> str:
        if item.recurrence:
            sched = ph.series_when(item)
            first = ph.day_phrase(item.due_at.date(), today)
            if item.type == ItemType.REMINDER:
                return f"Хорошо, буду напоминать {sched}: «{item.title}». Первый раз — {first}."
            if item.type == ItemType.MEETING:
                return f"Встреча добавлена: «{item.title}», {sched}. Ближайшая — {first}."
            return f"Добавлена повторяющаяся задача «{item.title}»: {sched}. Ближайший раз — {first}."
        if item.type == ItemType.REMINDER:
            return f"Хорошо, напомню {ph.when_phrase(item.due_at, True, today, item.duration_min)}: «{item.title}»."
        if item.type == ItemType.MEETING:
            extra = ""
            if item.duration_min and item.duration_min != 60:
                hours, mins = divmod(item.duration_min, 60)
                extra = f" (на {hours} ч {mins} мин)" if mins and hours else (
                    f" (на {hours} ч)" if hours else f" (на {mins} мин)")
            return f"Встреча добавлена: «{item.title}», {ph.when_phrase(item.due_at, True, today, item.duration_min)}{extra}."
        if item.due_at is None:
            return f"Добавлена задача «{item.title}» без срока."
        if d.deadline:
            return f"Добавлена задача «{item.title}», срок {ph.deadline_phrase(item.due_at, today)}."
        when = ph.on_day_phrase(item.due_at.date(), today) + (f", {ph.hm(item.due_at)}" if item.has_time else "")
        return f"Добавлена задача «{item.title}» {when}."

    # ================================================================ уточнения

    def _is_new_command(self, text: str, now: datetime, expecting_value: bool = True) -> Command | None:
        """Не ответ на уточнение, а новая команда? expecting_value=False — ждали выбор, а не время/текст."""
        cmd = self.rules.parse(text, NLUContext(now=now))
        if cmd.intent in (Intent.LIST, Intent.SEARCH, Intent.HELP, *_ACT_INTENTS):
            return cmd
        if cmd.intent == Intent.CREATE and cmd.title and (
                has_create_trigger(text) or cmd.repeat or (not expecting_value and cmd.when)):
            return cmd
        return None

    def _continue_pending(self, s: Session, user: str, text: str, now: datetime) -> Reply | None:
        p = s.pending
        assert p is not None
        if p.action == "bulk_delete":
            return self._continue_bulk_delete(s, user, p, text, now)
        if p.kind == "enrich":
            return self._continue_enrichment(s, user, p, text, now)
        if _CANCEL.match(text) or (p.kind != "confirm" and _NO.match(text) and len(text.split()) <= 3):
            s.pending = None
            return Reply("Хорошо, отменено.", intent="cancel")

        if p.kind == "choice":
            return self._continue_choice(s, user, p, text, now)

        if p.kind == "scope":
            return self._continue_scope(s, user, p, text, now)

        if p.kind == "confirm":
            new_command = self._is_new_command(text, now)
            if new_command is not None and new_command.intent in (Intent.CREATE, Intent.LIST, Intent.SEARCH,
                                                                  Intent.HELP):
                # вместо «да/нет» — новый вопрос или запись: неподтверждённое действие не выполняется
                s.pending = None
                if p.action == "create" and p.draft is not None:
                    s.carry = Reply(f"«{p.draft.title}» не сохранено: пересечение не подтверждено.", intent="cancel")
                elif p.action == "update" and p.candidates:
                    s.carry = Reply(f"Перенос «{p.candidates[0].title}» отменён.", intent="cancel")
                return None

        if p.kind == "confirm" and p.action == "update" and p.candidates:
            cmd = p.command
            assert isinstance(cmd, Command)
            when = self._move_reply_time(p, text, now)
            info = parse(when, now)
            if info.found or info.error:
                s.pending = None
                return self._apply(s, user, Intent.UPDATE, p.candidates[0], cmd.model_copy(update={"when": when}),
                                   text, now, occ_day=p.day)
            if _YES.match(text):
                s.pending = None
                return self._apply(s, user, Intent.UPDATE, p.candidates[0], cmd, p.text, now,
                                   occ_day=p.day, conflicts_ok=True)
            if _NO.match(text):
                q = "Хорошо. На какое время тогда перенести?"
                return self._ask(s, q, Pending("slot", "update", q, slot="datetime", candidates=p.candidates,
                                               command=cmd, day=p.day, proposed_due=p.proposed_due), "datetime")
            if self._is_new_command(text, now, expecting_value=False):
                s.pending = None
                return None
            return Reply(p.question, intent="clarify", awaiting="confirm")

        if p.kind == "confirm" and p.draft is not None:
            info = parse(text, now, "future")
            if info.found or info.error:
                return self._fill_time(s, user, p.draft, info, now)
            if _YES.match(text):
                p.draft.conflicts_ok = True
                return self._proceed(s, user, p.draft, now)
            if _NO.match(text):
                p.draft.has_time = False
                q = "Хорошо. На какое время тогда поставить?"
                return self._ask(s, q, Pending("slot", "create", q, slot="time", draft=p.draft), "time")
            s.pending = None
            return None

        if p.kind == "slot":
            if self._is_new_command(text, now):
                s.pending = None
                return None
            if p.action == "create" and p.draft is not None:
                if p.slot == "title":
                    p.draft.title = clean_title(text, p.draft.type) or text.strip()
                    return self._proceed(s, user, p.draft, now)
                info = parse(text, now, "future")
                if info.found or info.error:
                    return self._fill_time(s, user, p.draft, info, now)
            elif p.action == "update" and p.candidates:
                scope = p.command.scope if isinstance(p.command, Command) else None
                when = self._move_reply_time(p, text, now)
                cmd = Command(intent=Intent.UPDATE, when=when, scope=scope)
                info = parse(when, now)
                if info.found or info.error or _SHIFT.search(self._norm(text)):
                    s.pending = None
                    return self._apply(s, user, Intent.UPDATE, p.candidates[0], cmd, text, now, occ_day=p.day)
            elif p.action == "search" and isinstance(p.command, Command):
                cmd = p.command.model_copy(update={"query": text})
                s.pending = None
                return self._search(s, user, cmd, now)
            p.attempts += 1
            if p.attempts >= 2:
                s.pending = None
                return Reply("Не получилось разобрать ответ, действие отменено. Попробуйте сказать запрос целиком, "
                             "например: «встреча с Иваном завтра в 15:00».", intent="cancel")
            hint = " Например: «в 15:00» или «завтра в 10 утра»." if p.slot in ("time", "datetime", "date") else ""
            return Reply(f"Не получилось разобрать. {p.question}{hint}", intent="clarify", awaiting=p.slot)
        s.pending = None
        return None

    def _continue_enrichment(self, s: Session, user: str, p: Pending, text: str, now: datetime) -> Reply | None:
        d = p.draft
        assert d is not None
        low = text.lower().strip(" .!?,").replace("ё", "е")
        if low in ("отмена", "отмени", "стоп", "отменить"):
            s.pending = None
            return Reply("Хорошо, отменено.", intent="cancel")
        save = low in ("сохранить", "сохрани", "сохраняй", "без уточнений", "сразу сохранить")
        skip = save or low in ("пропустить", "пропусти", "нет", "не надо", "не нужно", "дальше",
                              "без подробностей", "пока не надо")
        lead_answer = p.slot == "remind_before" and re.match(r"^(?:напомни(?:ть)?(?: мне)?\s+)?за\b", low)
        if not skip and not lead_answer and self._is_new_command(text, now):
            return self._save_interrupted(s, user, d, now)
        if save:
            d.details_done = d.lead_done = True
        elif p.slot == "details":
            if low in ("да", "ага", "хорошо"):
                return Reply(p.question, intent="clarify", awaiting=p.slot)
            d.notes = "" if skip else text.strip()
            d.details_done = True
        else:
            minutes = 0 if skip or low in ("в момент начала", "в назначенное время", "сразу", "вовремя",
                                           "в момент", "без опережения") else self._lead_minutes(text)
            if minutes is None:
                if self._is_new_command(text, now):
                    return self._save_interrupted(s, user, d, now)
                return Reply("Назовите интервал от 0 минут до 7 дней, например «за 15 минут», "
                             "или ответьте «пропустить».", intent="clarify", awaiting=p.slot)
            d.remind_before_min = minutes
            d.lead_done = True
        return self._proceed(s, user, d, now)

    def _save_interrupted(self, s: Session, user: str, d: Draft, now: datetime) -> None:
        """Подробности не дождались новой команды: запись сохраняется как есть, затем выполняется команда."""
        d.details_done = d.lead_done = True
        d.conflicts_ok = True  # о пересечении скажет текст ответа
        saved = self._proceed(s, user, d, now)
        s.pending = None
        if saved.intent == "create":
            s.carry = saved
        return None

    @classmethod
    def _lead_minutes(cls, text: str) -> int | None:
        if re.search(r"-\s*\d", text):
            return None
        norm = cls._norm(text)
        if "минус" in norm:
            return None
        if "полчаса" in norm:
            return 30
        if "полтора часа" in norm:
            return 90
        if re.fullmatch(r"(?:за )?\d+", norm):
            value = int(norm.split()[-1])
        else:
            parts = re.findall(r"(?:(\d+)\s*)?(минут\w*|мин|час\w*|день|дня|дней)\b", norm)
            if not parts:
                return None
            value = sum(int(n or 1) * (60 if unit.startswith("час") else 1440 if unit.startswith("д") else 1)
                        for n, unit in parts)
        return value if 0 <= value <= 10080 else None

    @staticmethod
    def _move_reply_time(p: Pending, text: str, now: datetime) -> str:
        info = parse(text, now)
        if p.proposed_due is not None and info.has_time and not info.has_date:
            return f"{p.proposed_due.date().isoformat()} {text}"
        return text

    def _fill_time(self, s: Session, user: str, d: Draft, info: TimeInfo, now: datetime) -> Reply:
        if info.error:
            return self._ask(s, info.error, Pending("slot", "create", info.error, slot="datetime", draft=d), "datetime")
        if info.has_date and info.has_time:
            d.due, d.has_time = info.moment, True
        elif info.has_time and info.moment is not None:
            day = d.due.date() if (d.due is not None and d.has_date) else info.moment.date()
            d.due, d.has_time = datetime.combine(day, info.moment.time()), True
        else:
            due, _ = creation_moment(info, now)
            if due is not None:
                d.due = datetime.combine(due.date(), d.due.time()) if (d.has_time and d.due) else due
            d.has_date = True
        if info.duration_min:
            d.duration = info.duration_min
        d.conflicts_ok = False
        return self._proceed(s, user, d, now)

    def _continue_scope(self, s: Session, user: str, p: Pending, text: str, now: datetime) -> Reply | None:
        """Ответ на «Только ближайшую или всю серию?»."""
        cmd = p.command if isinstance(p.command, Command) else Command(intent=Intent(p.action))
        item_id = p.candidates[0]
        day: date | None = None
        if _SCOPE_SERIES_ANSWER.search(text):
            scope = "series"
        elif _SCOPE_ONE_ANSWER.search(text):
            scope, day = "one", p.day
        elif self._is_new_command(text, now, expecting_value=False):
            s.pending = None
            return None
        else:
            info = parse(text, now, "nearest")
            master = self.service.get(user, item_id)
            if info.moment is not None and master and self.service.occurrence_on(master, info.moment.date()):
                scope, day = "one", info.moment.date()
            else:
                p.attempts += 1
                if p.attempts >= 2:
                    s.pending = None
                    return Reply("Хорошо, ничего не меняю.", intent="cancel")
                return Reply("Скажите «только эту» или «всю серию».", intent="clarify", awaiting="scope")
        s.pending = None
        return self._apply(s, user, cmd.intent, item_id, cmd.model_copy(update={"scope": scope}), p.text, now,
                           occ_day=day)

    def _continue_choice(self, s: Session, user: str, p: Pending, text: str, now: datetime) -> Reply | None:
        idx = parse_ordinal(text, len(p.candidates))
        cmd = p.command if isinstance(p.command, Command) else Command(intent=Intent(p.action))
        items = [it for it in (self.service.get(user, i) for i in p.candidates) if it]
        if idx is None and _SCOPE_SERIES_ANSWER.search(text):
            # «всю серию» в ответ на «какую?» — выбираем повторяющуюся запись
            series = [i for i, it in enumerate(items) if it.recurrence]
            if len(series) == 1:
                idx = p.candidates.index(items[series[0]].id)
                cmd = cmd.model_copy(update={"scope": "series"})
        if idx is None and self._is_new_command(text, now, expecting_value=False):
            s.pending = None  # «Отметь зарядку выполненной» посреди выбора — это новая команда
            return None
        if idx is None:
            hits = self.service.search_engine.rank(text, items)
            if hits and (len(hits) == 1 or hits[0].score > 1.3 * hits[1].score):
                idx = p.candidates.index(hits[0].item.id)
        if idx is None:
            p.attempts += 1
            if p.attempts >= 2:
                s.pending = None
                return Reply("Хорошо, ничего не меняю.", intent="cancel")
            return Reply("Назовите номер варианта — например, «первую» или «2».", intent="clarify",
                         awaiting="choice")
        s.pending = None
        return self._apply(s, user, cmd.intent, p.candidates[idx], cmd, p.text or text, now,
                           occ_day=self._phrase_day(cmd.occurrence, now))

    # ================================================================ список и поиск

    def _list(self, s: Session, user: str, cmd: Command, now: datetime) -> Reply:
        today = now.date()
        prefer = {"past": "past", "future": "future"}.get(cmd.tense, "nearest")
        info = parse(cmd.period or "", now, prefer)
        if info.error:
            return Reply(info.error, intent="clarify")
        rng = info.query_range()
        if rng is None:
            if cmd.query:
                return self._search(s, user, cmd, now)
            start = datetime.combine(today, time())
            if cmd.status:  # «что не выполнено?» — последняя неделя по сегодня включительно
                rng, label = (start - timedelta(days=7), start + timedelta(days=1)), "За последнюю неделю"
            else:
                rng, label = (start, start + timedelta(days=7)), "В ближайшие 7 дней"
        else:
            label = ph.period_label(rng[0], rng[1], today)

        items = self.service.list_period(user, rng[0], rng[1], cmd.item_type)
        if cmd.status:
            wanted = ItemStatus.OPEN if cmd.status == "open" else ItemStatus.DONE
            items = [it for it in items if it.status == wanted]
            label += ", не выполнено" if cmd.status == "open" else ", выполнено"
        if cmd.query:
            hits = self.service.search_engine.rank(cmd.query, items)
            items = sorted((h.item for h in hits), key=lambda it: it.due_at or datetime.max)

        t = cmd.item_type
        if not items:
            if cmd.query:
                text = f"{label} ничего не нашлось по запросу «{query_label(cmd.query)}»."
            elif cmd.status == "open":
                text = f"{label.split(',')[0]} всё выполнено."
            elif cmd.status == "done":
                text = f"{label.split(',')[0]} выполненных {TYPE_FORMS[t][2] if t else 'записей'} нет."
            elif t:
                text = f"{label} {TYPE_FORMS[t][2]} нет."
            else:
                text = f"{label} ничего не запланировано."
            s.last_ids = []
            return Reply(text, intent="list")

        multi_day = (rng[1] - rng[0]) > timedelta(days=1) or rng[0].date() != (rng[1] - timedelta(seconds=1)).date()
        n = len(items)
        lines = [f"{label} — {ph.count_phrase(n, t)}:"]
        shown = items[:30]
        if multi_day:
            for day, day_items in ph.group_by_day(shown):
                lines.append(f"\n{ph.day_header(day, today)}")
                lines += [f"• {ph.item_line(it, today, with_date=False, with_type=t is None)}" for it in day_items]
        else:
            lines += [f"• {ph.item_line(it, today, with_date=False, with_type=t is None)}" for it in shown]
        if n > len(shown):
            lines.append(f"…и ещё {n - len(shown)}")

        spoken = "; ".join(ph.spoken_item(it, today, with_date=multi_day) for it in items[:3])
        speech = (f"{label} — {ph.count_phrase(n, t, spoken=True)}. {ph.cap(spoken)}." if cmd.status
                  else f"{label} у вас {ph.count_phrase(n, t, spoken=True)}. {ph.cap(spoken)}.")
        if n > 3:
            speech += f" И ещё {n - 3} — на экране."
        self._remember(s, items[:10])
        return Reply("\n".join(lines), speech, items=items, intent="list")

    def _search(self, s: Session, user: str, cmd: Command, now: datetime) -> Reply:
        today = now.date()
        query = (cmd.query or "").strip()
        prefer = {"past": "past", "future": "future"}.get(cmd.tense, "nearest")
        info = parse(cmd.period or "", now, prefer)
        if info.error:
            return Reply(info.error, intent="clarify")
        rng = info.query_range()
        if not query:
            if rng is not None:
                return self._list(s, user, cmd, now)
            q = "Что найти? Назовите тему или человека."
            return self._ask(s, q, Pending("slot", "search", q, slot="query", command=cmd), "query")

        t = cmd.item_type
        flags = {"only_open": cmd.status == "open", "only_done": cmd.status == "done"}
        hits = self.service.search(user, query, t, rng, cmd.tense, **flags)
        relaxed = False
        if not hits and t is not None:
            hits, relaxed = self.service.search(user, query, None, rng, cmd.tense), True
        if not hits and cmd.tense != "any":
            hits = self.service.search(user, query, None if relaxed else t, rng, "any")
        label = query_label(query)
        if not hits:
            s.last_ids = []
            return Reply(f"Ничего не нашлось по запросу «{label}». Попробуйте сказать иначе.", intent="search")

        forms = ph.RECORD_FORMS if (t is None or relaxed) else TYPE_FORMS[t]
        prefix = f"{ph.cap(TYPE_FORMS[t][2])} не нашлось, но есть похожие записи. " if relaxed else ""
        n = len(hits)

        if cmd.ask_when:
            best_pool = [h for h in hits if h.score >= 0.6 * max(x.score for x in hits)]
            past = sorted((h for h in best_pool if h.item.due_at and h.item.due_at < now),
                          key=lambda h: h.item.due_at, reverse=True)
            future = sorted((h for h in best_pool if h.item.due_at and h.item.due_at >= now),
                            key=lambda h: h.item.due_at)
            if cmd.tense == "past":
                best = (past or future or best_pool)[0]
            else:
                best = (future or past or best_pool)[0]
            heads = _WHEN_HEAD[None if relaxed else t]
            is_past = best.item.due_at is not None and best.item.due_at < now
            head = heads[0] if is_past else heads[1]
            text = (f"{prefix}{head} {ph.when_phrase(best.item.due_at, best.item.has_time, today)}: "
                    f"«{best.item.title}».")
            others = [h.item for h in (past if is_past else future) if h.item.id != best.item.id][:4]
            if others:
                text += ("\nРанее:\n" if is_past else "\nДальше:\n") + "\n".join(
                    f"• {ph.item_line(it, today)}" for it in others)
            self._remember(s, [best.item])
            return Reply(text, text.split("\n")[0], items=[best.item, *others], intent="search")

        top = [h.item for h in hits[:5]]
        tail = " Самые подходящие и ближайшие:" if n > 5 else ""
        lines = [f"{prefix}Найдено: {ph.count_phrase(n, None if forms is ph.RECORD_FORMS else t)} "
                 f"по запросу «{label}».{tail}"]
        lines += [f"• {ph.item_line(it, today)}" for it in top]
        speech = (f"{prefix}Найдено: {ph.count_phrase(n, None if forms is ph.RECORD_FORMS else t, spoken=True)}. "
                  f"Первая: {ph.spoken_item(top[0], today, with_date=True)}.")
        self._remember(s, top)
        return Reply("\n".join(lines), speech, items=top, intent="search")

    # ================================================================ массовое удаление

    @staticmethod
    def _bulk_label(period: tuple[datetime, datetime], now: datetime) -> str:
        start, end = period
        last_day = (end - timedelta(microseconds=1)).date()
        dates = start.strftime("%d.%m.%Y")
        if last_day != start.date():
            dates += "–" + last_day.strftime("%d.%m.%Y")
        if start.time() != time() or end.time() != time():
            dates = f"{start:%d.%m.%Y %H:%M}–{end:%d.%m.%Y %H:%M}"
        return f"{ph.period_label(start, end, now.date())} ({dates})"

    def _bulk_items(self, user: str, cmd: Command, period: tuple[datetime, datetime]) -> list[Item]:
        items = self.service.list_period(user, *period, cmd.item_type)
        if cmd.status:
            items = [it for it in items if it.status.value == cmd.status]
        if cmd.query:
            matches = {hit.item.id for hit in self.service.search_engine.rank(cmd.query, items)}
            items = [it for it in items if it.id in matches]
        return items

    def _bulk_delete(self, s: Session, user: str, cmd: Command, now: datetime,
                     period: tuple[datetime, datetime] | None = None, note: str = "") -> Reply:
        if period is None:
            info = parse(cmd.period or "", now, "nearest")
            period = info.query_range()
            if info.error or period is None or (not info.has_date and info.period is None):
                q = info.error or "За какой период удалить записи? Например, «сегодня», «завтра» или «на этой неделе»."
                return self._ask(s, q, Pending("slot", "bulk_delete", q, slot="period", command=cmd), "period")
        if period[1] - period[0] > timedelta(days=366):
            q = "Выберите период не длиннее года. За какой период удалить записи?"
            return self._ask(s, q, Pending("slot", "bulk_delete", q, slot="period", command=cmd), "period")
        items = self._bulk_items(user, cmd, period)
        label = self._bulk_label(period, now)
        if not items:
            s.pending = None
            return Reply(f"За период «{label}» подходящих записей нет. Ничего не удалено.", intent="delete")
        lines = [f"{note}Удалить записи за период «{label}»? Найдено: {ph.count_phrase(len(items), cmd.item_type)}."]
        lines += [f"• {ph.item_line(it, now.date())}" for it in items[:5]]
        if len(items) > 5:
            lines.append(f"…и ещё {len(items) - 5}.")
        if any(it.occurrence for it in items):
            lines.append("У повторяющихся записей будут удалены только даты в этом периоде.")
        lines.append("Подтвердите: «да, удалить» или «нет».")
        q = "\n".join(lines)
        pending = Pending("confirm", "bulk_delete", q, command=cmd, selection=items, period=period)
        self._remember(s, items[:5])
        return self._ask(s, q, pending, "confirm", lines[0] + " Подтвердите: да, удалить, или нет.")

    def _continue_bulk_delete(self, s: Session, user: str, p: Pending, text: str, now: datetime) -> Reply | None:
        cmd = p.command
        assert isinstance(cmd, Command)
        answer = text.strip(" \t\r\n.!?")
        if _CANCEL.match(text) or _NO.fullmatch(answer):
            s.pending = None
            return Reply("Удаление отменено.", intent="cancel")
        if p.kind == "confirm" and _BULK_YES.fullmatch(answer):
            assert p.period is not None
            current = self._bulk_items(user, cmd, p.period)
            if current != p.selection or not self.service.delete_confirmed(user, p.selection):
                return self._bulk_delete(s, user, cmd, now, p.period,
                                         "Список записей изменился. Проверьте его ещё раз. ")
            s.pending = None
            s.last_ids = []
            s.occ_dates = {}
            return Reply(f"Удалено: {ph.count_phrase(len(p.selection), cmd.item_type)}. "
                         f"Период: {self._bulk_label(p.period, now)}.", intent="delete",
                         changed=list(dict.fromkeys(it.id for it in p.selection)))
        if self._is_new_command(text, now):
            s.pending = None
            return None
        info = parse(text, now, "nearest")
        if info.found or info.error:
            return self._bulk_delete(s, user, cmd.model_copy(update={"period": info.phrase}), now)
        return Reply(p.question, intent="clarify", awaiting="period" if p.kind == "slot" else "confirm")

    # ================================================================ изменения

    @staticmethod
    def _is_done(item: Item) -> bool:
        return item.status == ItemStatus.DONE or bool(item.recurrence and item.done_dates)

    @staticmethod
    def _remember(s: Session, items: list[Item]) -> None:
        """Запомнить показанные записи для «её», «эту», а у серий — какое повторение было на экране."""
        s.last_ids = list(dict.fromkeys(it.id for it in items))[:10]
        s.occ_dates = {}
        for it in items:
            if it.occurrence and it.due_at and it.id not in s.occ_dates:
                s.occ_dates[it.id] = it.due_at.date()

    @staticmethod
    def _phrase_day(phrase: str | None, now: datetime) -> date | None:
        if not phrase:
            return None
        info = parse(phrase, now, "nearest")
        return info.moment.date() if info.moment is not None else None

    def _on_day(self, user: str, item: Item, day: date) -> bool:
        if item.recurrence:
            master = self.service.get(user, item.id)
            return bool(master and self.service.occurrence_on(master, day))
        return item.due_at is not None and item.due_at.date() == day

    def _act(self, s: Session, user: str, cmd: Command, text: str, now: datetime) -> Reply:
        today = now.date()
        question = _QUESTION_FOR[cmd.intent]
        day = self._phrase_day(cmd.occurrence, now)
        constraint = parse(cmd.occurrence or cmd.period or "", now, "nearest")
        if constraint.error:
            return Reply(constraint.error, intent="clarify")
        period = constraint.query_range()
        if constraint.has_time and constraint.moment and not constraint.range_end and not constraint.period:
            period = (constraint.moment, constraint.moment + timedelta(minutes=1))
        candidates: list[int] = []
        if cmd.target_last or (not cmd.query and period is None):
            ids = s.last_ids
            allowed = expand_types(cmd.item_type)
            ids = [item_id for item_id in ids if (item := self.service.get(user, item_id))
                   and (allowed is None or item.type in allowed)
                   and self._in_target_period(user, item, period)]
            if cmd.intent == Intent.REOPEN:
                # «сними отметку» — из обсуждавшихся только отмеченные, иначе последняя отмеченная запись
                ids = [i for i in ids if (it := self.service.get(user, i)) and self._is_done(it)]
                if not ids:
                    last = self.service.last_completed(user)
                    if last is None:
                        return Reply("Нет записей, отмеченных как выполненные.", intent="reopen")
                    return self._apply(s, user, cmd.intent, last.id, cmd, text, now, occ_day=day)
            if not ids:
                if period is not None:
                    return Reply("В указанном периоде обсуждавшаяся запись не найдена. Ничего не изменено.",
                                 intent="clarify")
                return Reply("Уточните, о какой записи речь — например: «перенеси встречу с Иваном на пятницу».",
                             intent="clarify")
            idx = parse_ordinal(text, len(ids))
            if idx is None and len(ids) == 1:
                idx = 0
            if idx is not None:
                return self._apply(s, user, cmd.intent, ids[idx], cmd, text, now, occ_day=day,
                                   ctx_day=s.occ_dates.get(ids[idx]))
            candidates = ids[:5]
        else:
            if cmd.query:
                hits = self.service.find_target(user, cmd.query, cmd.item_type, done=cmd.intent == Intent.REOPEN,
                                                period=period, strict_type=cmd.intent == Intent.DELETE)
            else:
                hits = [Hit(it, 1.0) for it in self.service.list_period(user, *period, cmd.item_type)]
            if cmd.scope == "series":
                hits = [h for h in hits if h.item.recurrence] or hits
            hits = [h for h in hits if self._in_target_period(user, h.item, period)]
            if not hits:
                if day is not None and cmd.query:
                    nearby = self.service.find_target(user, cmd.query, cmd.item_type, strict_type=True)
                    series = [h.item for h in nearby if h.item.recurrence]
                    if len({it.id for it in series}) == 1 and not self._on_day(user, series[0], day):
                        return Reply(f"{ph.cap(ph.day_phrase(day, today))} повторения «{series[0].title}» нет — "
                                     f"оно бывает {ph.series_when(series[0])}.", intent=cmd.intent.value)
                what = "отмеченной записи" if cmd.intent == Intent.REOPEN else "подходящей записи"
                label = f" по запросу «{query_label(cmd.query)}»" if cmd.query else ""
                bound = f" за период «{self._bulk_label(period, now)}»" if period else ""
                return Reply(f"Не нашлось {what}{label}{bound}. Ничего не изменено.", intent=cmd.intent.value)
            best = max(h.score for h in hits)
            strong = [h for h in hits if h.score >= 0.85 * best]
            if day is None:
                # перенесённое повторение и его серия — одна «планёрка»: без названного дня берём серию
                ids = {h.item.id for h in strong}
                strong = [h for h in strong if not (h.item.series_id and h.item.series_id in ids)] or strong
            if len({h.item.id for h in strong}) == 1:
                return self._apply(s, user, cmd.intent, strong[0].item.id, cmd, text, now, occ_day=day)
            candidates = list(dict.fromkeys(h.item.id for h in strong))[:5]

        items = [it for it in (self.service.get(user, i) for i in candidates) if it]
        lines = [f"Подходит несколько записей ({len(items)}):"]
        lines += [f"{i}. {self._line(it, today)}" for i, it in enumerate(items, 1)]
        lines.append(question)
        spoken = "; ".join(f"{['первая', 'вторая', 'третья', 'четвёртая', 'пятая'][i]} — "
                           f"{ph.spoken_item(it, today, with_date=True)}" for i, it in enumerate(items[:3]))
        q = "\n".join(lines)
        pending = Pending("choice", cmd.intent.value, q, candidates=[it.id for it in items], command=cmd, text=text)
        return self._ask(s, q, pending, "choice", f"Подходит несколько записей. {ph.cap(spoken)}. {question}")

    def _in_target_period(self, user: str, item: Item, period: tuple[datetime, datetime] | None) -> bool:
        if item.user_id != user:
            return False
        if period is None:
            return True
        if item.recurrence and not item.occurrence:
            return bool(self.service.occurrences(item, *period))
        return item.due_at is not None and period[0] <= item.due_at < period[1]

    @staticmethod
    def _line(item: Item, today: date) -> str:
        """Строка для выбора: у серии — расписание, а не дата первого повторения."""
        if item.recurrence and not item.occurrence:
            return f"{ph.series_when(item)} — {item.title} ({ph.type_label(item.type)})"
        return ph.item_line(item, today)

    @staticmethod
    def _norm(text: str) -> str:
        return " ".join(t.norm for t in tokenize(text) if t.norm)

    def _new_time(self, base: Item, cmd: Command, text: str, now: datetime) -> tuple[datetime | None, bool] | None:
        """Куда переносить: сдвиг («на час позже») или новое время. None — время не названо."""
        info = parse(cmd.when or "", now, "future")
        shift = _SHIFT.search(self._norm(text))
        if shift and base.due_at is not None:
            n = float(shift.group("n") or 1)
            unit = shift.group("unit")
            delta = (timedelta(hours=n) if unit.startswith("час") else timedelta(minutes=n) if unit.startswith("мин")
                     else timedelta(weeks=n) if unit.startswith("недел") else timedelta(days=n))
            if shift.group("dir") in ("раньше", "назад"):
                delta = -delta
            return base.due_at + delta, base.has_time
        if info.found:
            return merge_when(base, info, now)
        return None

    def _apply(self, s: Session, user: str, intent: Intent, item_id: int, cmd: Command, text: str,
               now: datetime, occ_day: date | None = None, ctx_day: date | None = None,
               conflicts_ok: bool = False) -> Reply:
        """Выполнить действие над записью. occ_day — повторение серии, названное в команде;
        ctx_day — повторение, которое было на экране, когда сказали «её»."""
        today = now.date()
        item = self.service.get(user, item_id)
        if item is None:
            s.last_ids = [i for i in s.last_ids if i != item_id]
            return Reply("Этой записи уже нет.", intent=intent.value)
        if intent == Intent.DELETE:
            info = parse(cmd.occurrence or cmd.period or "", now, "nearest")
            if info.error:
                return Reply(info.error, intent="clarify")
            period = info.query_range()
            if info.has_time and info.moment and not info.range_end and not info.period:
                period = (info.moment, info.moment + timedelta(minutes=1))
            if not self._in_target_period(user, item, period):
                return Reply("Эта запись не относится к указанному периоду. Ничего не удалено.", intent="delete")
        series = item.rule is not None
        if series and occ_day is not None and self.service.occurrence_on(item, occ_day) is None:
            return Reply(f"{ph.cap(ph.day_phrase(occ_day, today))} повторения «{item.title}» нет — "
                         f"оно бывает {ph.series_when(item)}.", items=[item], intent=intent.value)

        if series and intent in (Intent.UPDATE, Intent.DELETE):
            scope = cmd.scope or ("one" if occ_day else None)
            if scope is None and intent == Intent.UPDATE and cmd.title and not cmd.when:
                scope = "series"  # переименование по умолчанию — всей серии
            if scope is None:
                day = ctx_day or self.service.nearest_day(item)
                verb = "Перенести" if intent == Intent.UPDATE else "Удалить"
                q = (f"«{item.title}» повторяется {ph.series_when(item)}. {verb} только "
                     f"{ph.day_phrase(day, today) if day else 'ближайшую'} или всю серию?")
                pending = Pending("scope", intent.value, q, candidates=[item.id], command=cmd, day=day, text=text)
                s.last_ids = [item.id]
                if day:
                    s.occ_dates[item.id] = day
                return self._ask(s, q, pending, "scope")
            if scope == "one":
                return self._apply_one(s, user, intent, item, cmd, text, now, occ_day or ctx_day
                                       or self.service.nearest_day(item), conflicts_ok=conflicts_ok)

        if intent == Intent.DELETE:
            self.service.delete(user, item_id)
            s.last_ids = []
            if series:
                return Reply(f"Удалена серия: {ph.type_label(item.type)} «{item.title}» ({ph.series_when(item)}).",
                             items=[item], intent="delete", changed=[item.id])
            return Reply(f"Удалено: {ph.type_label(item.type)} «{item.title}» "
                         f"({ph.when_phrase(item.due_at, item.has_time, today)}).",
                         items=[item], intent="delete", changed=[item.id])

        if intent == Intent.COMPLETE:
            s.last_ids = [item.id]
            if series:
                occ = self.service.complete(user, item_id, occ_day or ctx_day)
                if occ is None or not occ.occurrence:
                    return Reply(f"Все ближайшие повторения «{item.title}» уже отмечены.", items=[item],
                                 intent="complete")
                s.occ_dates[item.id] = occ.due_at.date()
                master = self.service.get(user, item_id)
                nxt = self.service.next_open(master, occ.due_at + timedelta(minutes=1)) if master else None
                text_ = f"Отмечено как выполненное: «{item.title}» ({ph.day_phrase(occ.due_at.date(), today)})."
                if nxt is not None:
                    text_ += f" Следующий раз — {ph.when_phrase(nxt.due_at, nxt.has_time, today)}."
                return Reply(text_, items=[occ], intent="complete", changed=[item.id])
            if item.status == ItemStatus.DONE:
                return Reply(f"Уже отмечено как выполненное: «{item.title}».", items=[item], intent="complete")
            done = self.service.complete(user, item_id)
            return Reply(f"Отмечено как выполненное: «{item.title}».", items=[done], intent="complete",
                         changed=[item.id])

        if intent == Intent.REOPEN:
            s.last_ids = [item.id]
            day = occ_day or (ctx_day if ctx_day in item.done_dates else None)
            if not self._is_done(item) or (series and day is not None and day not in item.done_dates):
                return Reply(f"«{item.title}» и так не отмечена как выполненная.", items=[item], intent="reopen")
            reopened = self.service.reopen(user, item_id, day)
            when = f" ({ph.day_phrase(reopened.due_at.date(), today)})" if series and reopened.due_at else ""
            return Reply(f"Отметка снята: «{item.title}»{when} снова в планах.", items=[reopened], intent="reopen",
                         changed=[item.id])

        # UPDATE: перенос и/или переименование всей записи (или всей серии)
        s.last_ids = [item.id]
        info = parse(cmd.when or "", now)
        if info.error:
            return self._ask(s, info.error, Pending("slot", "update", info.error, slot="datetime",
                                                   candidates=[item.id], command=cmd), "datetime")
        new = self._new_time(item, cmd, text, now)
        if cmd.title and new is None:
            renamed = self.service.rename(user, item_id, cmd.title.strip())
            return Reply(f"Переименовано: «{item.title}» → «{renamed.title}».", items=[renamed], intent="update",
                         changed=[item.id])
        if new is None:
            q = f"На когда перенести «{item.title}»?"
            return self._ask(s, q, Pending("slot", "update", q, slot="datetime", candidates=[item.id], command=cmd),
                             "datetime")
        due, has_time = new
        duration = info.duration_min if info.duration_min is not None else item.duration_min
        candidate = due
        if series and due is not None:
            candidate = self.service.first_occurrence(item.rule.moved_to(due, item.due_at), due, has_time)
        if not conflicts_ok:
            question = self._confirm_move(s, user, item, cmd.model_copy(update={"scope": "series"}) if series else cmd,
                                          text, now, candidate, has_time, duration)
            if question:
                return question
        updated = self.service.reschedule(user, item_id, due, has_time, info.duration_min)
        if cmd.title:
            updated = self.service.rename(user, item_id, cmd.title.strip())
        due = updated.due_at
        note = self._move_note(user, updated, due, has_time, now, exclude_id=item_id, check_past=not series)
        if series:
            return Reply(f"Перенесено: «{updated.title}» — теперь {ph.series_when(updated)}. "
                         f"Ближайший раз — {ph.day_phrase(due.date(), today)}.{note}",
                         items=[updated], intent="update", changed=[item.id])
        return Reply(f"Перенесено: «{updated.title}» — теперь {ph.when_phrase(due, has_time, today, updated.duration_min)}.{note}",
                     items=[updated], intent="update", changed=[item.id])

    def _confirm_move(self, s: Session, user: str, item: Item, cmd: Command, text: str, now: datetime,
                      due: datetime | None, has_time: bool, duration: int | None,
                      occ_day: date | None = None) -> Reply | None:
        if item.type != ItemType.MEETING or not has_time or due is None:
            return None
        conflicts = self.service.conflicts(user, due, duration, exclude_id=item.id)
        if not conflicts:
            return None
        other = conflicts[0]
        when = ph.when_phrase(other.due_at, True, now.date(), other.duration_min)
        q = f"В это время уже есть встреча «{other.title}» ({when}). Всё равно перенести?"
        return self._ask(s, q, Pending("confirm", "update", q, candidates=[item.id], command=cmd,
                                       day=occ_day, text=text, proposed_due=due), "confirm")

    def _move_note(self, user: str, item: Item, due: datetime | None, has_time: bool, now: datetime,
                   exclude_id: int, check_past: bool = True) -> str:
        note = ""
        if check_past and due is not None and has_time and due < now:
            note = " Обратите внимание: это время уже прошло."
        if item.type == ItemType.MEETING and has_time and due is not None:
            conflicts = self.service.conflicts(user, due, item.duration_min, exclude_id=exclude_id)
            if conflicts:
                note += f" Пересекается с «{conflicts[0].title}»."
        return note

    def _apply_one(self, s: Session, user: str, intent: Intent, master: Item, cmd: Command, text: str,
                   now: datetime, day: date | None, conflicts_ok: bool = False) -> Reply:
        """Перенос или удаление одного повторения серии; остальные не меняются."""
        today = now.date()
        occ = self.service.occurrence_on(master, day) if day else None
        if occ is None:
            return Reply(f"Не нашлось повторения «{master.title}», которое можно изменить.", items=[master],
                         intent=intent.value)
        when_old = ph.when_phrase(occ.due_at, occ.has_time, today)
        if intent == Intent.DELETE:
            self.service.skip_occurrence(user, master.id, day)
            s.last_ids = [master.id]
            return Reply(f"Удалено одно повторение: «{master.title}» {when_old}. "
                         f"Остальные остаются — {ph.series_when(master)}.", items=[occ], intent="delete",
                         changed=[master.id])
        info = parse(cmd.when or "", now)
        if info.error:
            return self._ask(s, info.error, Pending("slot", "update", info.error, slot="datetime", candidates=[master.id],
                                                   command=cmd.model_copy(update={"scope": "one"}), day=day), "datetime")
        new = self._new_time(occ, cmd, text, now)
        if new is None and not cmd.title:
            q = f"На когда перенести «{master.title}» ({when_old})?"
            # уточняем время уже для отдельной записи-повторения: создадим её после ответа
            return self._ask(s, q, Pending("slot", "update", q, slot="datetime", candidates=[master.id],
                                           command=cmd.model_copy(update={"scope": "one"}), day=day, text=text),
                             "datetime")
        due, has_time = new if new else (occ.due_at, occ.has_time)
        duration = info.duration_min if info.duration_min is not None else occ.duration_min
        if new is not None and not conflicts_ok:
            question = self._confirm_move(s, user, master, cmd.model_copy(update={"scope": "one"}), text, now,
                                          due, has_time, duration, day)
            if question:
                return question
        moved = self.service.detach_occurrence(user, master.id, day, due, has_time, (cmd.title or "").strip() or None,
                                               info.duration_min)
        s.last_ids = [moved.id]
        note = self._move_note(user, moved, due, has_time, now, exclude_id=moved.id)
        if cmd.title and new is None:
            head = f"Переименовано только {when_old}: «{master.title}» → «{moved.title}»."
        else:
            head = f"Перенесено только {when_old}: «{moved.title}» — теперь {ph.when_phrase(due, has_time, today, moved.duration_min)}."
        return Reply(f"{head} Остальные повторения без изменений.{note}", items=[moved], intent="update",
                     changed=[master.id, moved.id])

    # ================================================================ служебное

    def _last_title(self, s: Session, user: str) -> str | None:
        if not s.last_ids:
            return None
        item = self.service.get(user, s.last_ids[0])
        return item.title if item else None
