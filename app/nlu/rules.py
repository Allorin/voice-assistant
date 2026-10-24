"""Разбор команд на правилах. Работает без интернета и ключей.

Используется как основной разборщик, если LLM не настроена, и как страховка, если LLM недоступна
или вернула мусор. Покрывает все сценарии из ТЗ и типовые перефразировки.
"""
from __future__ import annotations

import re

from app.models import ItemType
from app.nlu.schema import Command, Intent, NLUContext
from app.timeparse.repeat import parse_repeat
from app.timeparse.ru import TimeInfo, _Text, join_tokens, parse, tokenize


def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)


_CORRECTION = _rx(r"^(?:нет[\s,]+(?:неправильно|не\s+так)|неправильно|не\s+так|точнее|я\s+имел[а]?\s+в\s*виду)[\s,:—-]*")


def is_time_correction(text: str) -> bool:
    return bool(_CORRECTION.match(text.strip()))


_LEAD = r"^(?:(?:а|и|ну|так|слушай|пожалуйста|окей|ок|эй|ассистент)[\s,]+)*"

_CANCEL = _rx(r"^(?:отмена|отмени(?:ть)?|стоп|хватит|не\s+надо|не\s+нужно|забудь|неважно|ничего)[\s.!]*$")
_GREET = _rx(r"^(?:привет\w*|здравствуй\w*|добр\w+\s+(?:день|утро|вечер)|хай|hello|hi)\b")
_THANKS = _rx(r"^(?:спасибо|благодарю|спс|отлично|супер|класс|круто|понятно|ясно)\b")
_HELP = _rx(r"(?:что\s+ты\s+умеешь|что\s+умеешь|^помощь|^помоги\s*$|как\s+(?:тобой\s+)?пользоваться"
            r"|какие\s+команды|^help)")

_REMIND_START = _rx(_LEAD + r"(?:напомни\w*|напоминай\w*)\b")
_REMIND_QUESTION = _rx(_LEAD + r"напомни\w*[\s,]+(?:мне[\s,]+)?(?:что|какие|какая|какой|о\s+ч[её]м|сколько)\b")
_REOPEN = _rx(r"(?:\b(?:сними|снять|снимите|убери|убрать|сбрось|сбросить|отмени|отменить)\s+(?:\w+\s+){0,3}?"
              r"(?:отметк\w*|галочк\w*|выполнени\w*|статус\w*)"
              r"|\bверни\w*\b.*\b(?:в\s+работу|обратно|в\s+список|невыполн\w*|в\s+активные)"
              r"|\b(?:отметь|пометь|сделай|поставь)\b.*\b(?:не\s*выполнен\w*|не\s*сделан\w*)"
              r"|\b(?:ещ[её]|пока)\s+не\s+(?:сделал\w*|выполнил\w*|готов\w*|сделан\w*|выполнен\w*)"
              r"|\bслучайно\s+(?:отметил\w*|нажал\w*|поставил\w*|закрыл\w*))")
_STATUS_OPEN = _rx(r"\bне\s*(?:выполнен|сделан|заверш[её]н|закрыт)\w*|\bневыполн\w*|\bнезаверш\w*|\bнесделан\w*"
                   r"|\bоткрыт(?:ые|ых)\b|\bактивн(?:ые|ых)\b|\bосталось\s+сделать|\bещ[её]\s+не\b")
_STATUS_DONE = _rx(r"\b(?:выполненн|сделанн|заверш[её]нн|закрыт)\w*|\b(?:выполнены|сделаны|завершены|закрыты)\b"
                   r"|\bуже\s+(?:сделал|выполнил)\w*")
# «только эту», «в этот раз», «в пятницу созвона не будет» — одно повторение; «всю серию», «все планёрки» — серия
_SCOPE_SERIES = _rx(r"\b(?:всю\s+серию|всей\s+серии|серию|вс[её]\s+повторени\w*"
                    r"|все\s+(?:встреч|планерк|планёрк|созвон|напоминани|задач|повторени|занят|тренировк)\w*"
                    r"|навсегда|насовсем|теперь\s+всегда|целиком"
                    r"|каждый\s+раз|все\s+последующие|все\s+будущие)")
_SCOPE_ONE = _rx(r"\b(?:только\s+(?:эту|это|этот|ее|её|его|ближайш\w*|одн\w*|один|сегодняшн\w*|завтрашн\w*"
                 r"|в\s+этот\s+раз|на\s+этой\s+неделе|раз)|в\s+этот\s+раз|один\s+раз|разово|не\s+будет|не\s+состоится|отменяется"
                 r"|пропусти\w*|пропустить)\b")
SHIFT_RX = _rx(r"на\s+(?:(?P<n>\d+(?:\.5)?)\s+)?(?P<unit>час\w*|минут\w*|мин|дн\w*|день|недел\w*)\s+"
               r"(?P<dir>позже|раньше|вперед|вперёд|назад)")
_ON_WORD = re.compile(r"(?<![а-яё])на(?![а-яё])", re.IGNORECASE)
_QUESTION_HEAD = _rx(r"^(?:какие|какая|какой|что|сколько|покажи\w*|есть\s+ли|список)\b")
_DELETE = _rx(r"\b(?:удали\w*|удалить|убери|уберите|убрать|сотри|стереть|вычеркни|пропусти\w*|пропустить"
              r"|отмени(?:ть)?\s+(?:мне\s+)?(?:встреч|задач|напомин|созвон|планерк|планёрк|ее|её|его|это|эту|последн"
              r"|только|ближайш|завтрашн|сегодняшн))")
# «в пятницу созвона не будет» — удаление, только если названо, чего именно не будет
_NOT_HAPPEN = _rx(r"\bне\s+будет\b|\bне\s+состоится|\bотменяется\b")
_COMPLETE = _rx(r"(?:\b(?:отметь|пометь|отмечай|помечай|поставь|сделай)\b.*\b(?:выполн|сделан|готов|заверш)"
                r"|^(?:задач\w*|встреч\w*|напоминани\w*|это|она|оно|он)\b.*\b(?:сделан[аоы]?|выполнен[аоы]?|готов[аоы]?"
                r"|заверш[её]н[аоы]?|закрыт[аоы]?)[.!\s]*$"
                r"|\b(?:выполнил\w*|сделал\w*|завершил\w*|закрой|закрыть|закрыл\w*)\b|^(?:готово|сделано|выполнено)\b)")
_UPDATE = _rx(r"\b(?:перенеси\w*|передвинь|сдвинь|подвинь|отложи|перемести\w*|смести|измени\w*|поменяй|переименуй"
              r"|исправь|назови|(?:можешь|можно|давай|надо|нужно)\s+(?:перенести|сдвинуть|передвинуть|изменить|"
              r"поменять|переименовать))\b")
_CREATE_START = _rx(_LEAD + r"(?:(?:можешь|давай|пожалуйста)\s+)?(?:поставь|поставить|добавь|добавить|создай|создать"
                    r"|запиши|записать|запланируй|запланировать|назначь|назначить|внеси|заведи|сделай|зафиксируй|закинь|кинь|занеси"
                    r"|забей|вбей)\b")
_SEARCH = _rx(r"(?:\b(?:найди|найти|поищи|поиск|ищи|отыщи|разыщи|вспомни)\b|\bкогда\b|\b(?:был[аио]?|были)\s+ли\b"
              r"|\b(?:ставил|ставила|создавал|создавала|записывал|записывала|добавлял|добавляла)\b|\bгде\b)")
_LIST = _rx(r"(?:^что\b|\bчто\s+(?:у\s+меня|мне|запланирован|на\s|в\s|по\s|сегодня|завтра|намечен|будет|было)"
            r"|\bкакие\b|\bкакой\b|\bкакая\b|\bпокажи\w*|\bпоказать\b|\bрасписани|\bпланы\b|\bесть\s+ли\b"
            r"|\bчем\s+я\s+занят|\bсписок\b|\bплан\s+на\b|\bповестк|\bсколько\b|\bчто\s+(?:еще|ещё)\b"
            r"|\bу\s+меня\s+(?:есть\s+)?(?:встреч|задач|дела|планы|напомин)\w*.*\?\s*$)")
_MEETING_STMT = _rx(r"\bу\s+(?:меня|нас)\s+(?:будет\s+|запланирован\w*\s+)?(?:встреч|созвон|планерк|планёрк|совещан"
                    r"|звонок|митинг|собрани|переговор|демо)")
_NEED = _rx(r"\b(?:нужно|надо|необходимо|не\s+забыть|не\s+забудь|должен|должна|хочу|следует)\b")
_CREATE_ANY = _rx(r"\b(?:напомни\w*|напоминай\w*|нужно|надо|необходимо|не\s+забыть|не\s+забудь|должен|должна"
                  r"|хочу|следует)\b")

_MEETING_HEAD = _rx(r"(?:^|\s)(?:встреча|встречу|созвон|планерка|планерку|планёрка|планёрку|совещание|митинг|"
                    r"собрание|переговоры|демо|звонок\s+с|конференция|конференцию|собеседование|интервью"
                    r"|(?:обед|ужин|завтрак)\s+с)\b")
_TYPE_WORD_MEETING = _rx(r"\b(?:встреч\w*|созвон\w*|совещани\w*|планерк\w*|планёрк\w*|митинг\w*|переговор\w*"
                         r"|собрани\w*)")
_TYPE_WORD_REMINDER = _rx(r"\bнапоминани\w*")
_TYPE_WORD_TASK = _rx(r"\b(?:задач\w*|дел[ао]\b|дела\b|задани\w*|туду|todo)")
_EXPLICIT_TYPE_AFTER_VERB = _rx(r"(?:поставь|поставить|добавь|добавить|создай|создать|запиши|записать|заведи|внеси|"
                                r"запланируй|назначь|сделай)\s+(?:мне\s+|новую\s+|новое\s+|новую\s+)?"
                                r"(?P<t>задач\w*|напоминани\w*|встреч\w*|созвон\w*|планерк\w*|планёрк\w*|дело)")

_PAST = _rx(r"\b(?:был|была|было|были|прошл\w*|прошедш\w*|вчера|позавчера|последний\s+раз|созванивал\w*"
            r"|встречал\w*|общал\w*|виделс\w*|виделись)\b")
_FUTURE = _rx(r"\b(?:будет|будут|предстоящ\w*|следующ\w*|ближайш\w*|скоро|грядущ\w*)\b")
_PRONOUN = _rx(r"\b(?:ее|её|его|эту|это|этот|эта|последн\w*|только\s+что|нее|неё|нее)\b")
_RENAME = _rx(r"\b(?:переименуй|назови|измени\s+название|поменяй\s+название)\b.*?\s(?:в|на)\s+"
              r"[«\"']?(?P<t>.+?)[»\"']?\s*$")

# Фразы-обёртки, которые не являются содержанием задачи
_TITLE_PREFIXES = [
    _rx(r"^(?:то\s+есть|значит)(?:[\s,:]+|$)"),
    # (?:[\s,:]+|$) — слово снимается и тогда, когда после него ничего нет: «Напомни» само по себе — не текст задачи
    _rx(r"^(?:(?:а|и|ну|так|слушай|пожалуйста|окей|ок|эй|ассистент|можешь|можно|давай|плиз)(?:[\s,]+|$))"),
    _rx(r"^(?:напомни\w*|напоминай\w*|поставь|поставить|добавь|добавить|создай|создать|запиши|записать|запланируй"
        r"|запланировать|назначь|назначить|внеси|заведи|сделай|зафиксируй|закинь|кинь|занеси|забей|вбей)(?:[\s,:]+|$)"),
    _rx(r"^(?:мне|себе|нам|пожалуйста)(?:[\s,]+|$)"),
    _rx(r"^(?:новую\s+|новое\s+)?(?:задачу|задача|напоминание|дело|задание)(?:[\s,:]+|$)"),
    _rx(r"^(?:в\s+(?:календарь|список(?:\s+дел|\s+задач)?|задачи|планы|дела))[\s,:]+"),
    _rx(r"^(?:о\s+том\s*,?\s*)?(?:что|чтобы|про|о|об)\s+"),
    _rx(r"^(?:у\s+(?:меня|нас))\s+"),
    _rx(r"^(?:будет|есть|запланирован\w*|назначен\w*)\s+"),
    _rx(r"^(?:мне|я|нам|мы)\s+"),
    _rx(r"^(?:нужно|надо|необходимо|не\s+забыть|не\s+забудь|должен|должна|хочу|следует|стоит|бы)\s+"),
]
_MEETING_NOUN_NOMINATIVE = {
    "встречу": "встреча", "планерку": "планерка", "планёрку": "планёрка", "конференцию": "конференция",
}

_QUERY_STOP = set("""
найди найти поищи поиск ищи отыщи разыщи вспомни покажи показать скажи подскажи проверь посмотри глянь
когда где что какие какой какая какое каких сколько есть ли был была было были будет будут
у меня мне мой моя мои моё мое мою моих моем я нас нам мы про о об по на в во с со к ко для за и или а же бы то
это эту этот эта ее её его их там тот та ту все всё весь когда-то когда-нибудь нибудь какие-то какую-то
ставил ставила создавал создавала записывал записывала добавлял добавляла
запланировано запланирован запланированы запланированная планы план расписание список
дела дело задача задачу задачи задач задание напоминание напоминания напоминаний запись записи записей
встреча встречу встречи встреч встрече встречей встречах
пожалуйста еще ещё ну так давно раньше недавно ранее старую старые старой такой такая такую
перенеси перенести передвинь сдвинь подвинь отложи перемести смести измени изменить поменяй поменять исправь
удали удалить убери убрать сотри вычеркни отмени отменить отметь пометь отмечай помечай поставь сделай
выполненной выполненным выполненную выполнено выполнена выполнен выполнил выполнила сделанной сделано
сделал сделала готово готовой завершенной завершённой завершил завершила закрой закрыть закрыл закрыла
сделана сделан выполнена готова закрыта закрыт напомни напомнить
чем занят занята делать сделать что-то что-нибудь кое-что писал писала говорил говорила раз
созванивался созванивалась встречался встречалась общался общалась
как нее неё последнюю последняя последний только нужно надо можешь можно давай
сними снять снимите сбрось сбросить верни верните вернуть отметку отметки галочку галочки статус
работу обратно активные невыполненной невыполненным невыполненную невыполненная выполнение
выполнены выполненные выполненных сделаны сделанные сделанных завершены завершенные завершённые закрытые закрыты
открытые открытых активных невыполненные невыполненных несделанные незавершенные незавершённые осталось уже
не пока случайно отметил отметила нажал нажала поставил поставила закрыл закрыла
только серию серии всю всей целиком навсегда насовсем всегда каждый раз один одну одно ближайшую ближайшее
ближайший разово повторение повторения повторений пропусти пропустить состоится отменяется последующие будущие
""".split())


def _detect_list_type(low: str) -> ItemType | None:
    found = []
    if _TYPE_WORD_MEETING.search(low):
        found.append(ItemType.MEETING)
    if _TYPE_WORD_REMINDER.search(low):
        found.append(ItemType.REMINDER)
    if _TYPE_WORD_TASK.search(low):
        found.append(ItemType.TASK)
    return found[0] if len(found) == 1 else None


def _detect_create_type(text: str, low: str) -> ItemType:
    m = _EXPLICIT_TYPE_AFTER_VERB.search(low)
    if m:
        t = m.group("t")
        if t.startswith("задач") or t == "дело":
            return ItemType.TASK
        if t.startswith("напомин"):
            return ItemType.REMINDER
        return ItemType.MEETING
    if re.search(r"\b(?:напомни|напоминай)", low):
        return ItemType.REMINDER
    if _MEETING_STMT.search(low) or _MEETING_HEAD.search(low):
        return ItemType.MEETING
    return ItemType.TASK


def clean_title(raw: str, item_type: ItemType | None) -> str:
    title = re.sub(r"\s+", " ", raw).strip(" ,.;:!?-—")
    title = re.sub(r"\bпожалуйста\b", "", title, flags=re.IGNORECASE).strip(" ,")
    changed = True
    while changed and title:
        changed = False
        for rx in _TITLE_PREFIXES:
            new = rx.sub("", title, count=1).strip(" ,.;:!?-—")
            if new != title:
                title, changed = new, True
    title = re.sub(r"[\s,]+(?:напомни\w*|напоминай\w*|пожалуйста|запиши|добавь|поставь)(?:\s+(?:мне|об?\s+этом))?$",
                   "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+", " ", title).strip(" ,.;:!?-—")
    if item_type == ItemType.MEETING:
        first, _, rest = title.partition(" ")
        if first.lower() in _MEETING_NOUN_NOMINATIVE:
            title = (_MEETING_NOUN_NOMINATIVE[first.lower()] + (" " + rest if rest else "")).strip()
        elif re.match(r"^(?:с|со)\s", title, re.IGNORECASE):
            title = "встреча " + title
        if not title:
            title = "встреча"
    return title[:1].upper() + title[1:] if title else ""


def has_create_trigger(text: str) -> bool:
    """Есть ли во фразе явная команда создания («поставь», «напомни», «у меня встреча…»)."""
    low = text.lower().replace("ё", "е")
    return bool(_CREATE_START.match(low) or _REMIND_START.match(low) or _MEETING_STMT.search(low))


def extract_query(info: TimeInfo) -> str:
    """Ключевые слова для поиска: всё, что осталось после времени, служебных слов и команд."""
    words = []
    for i, t in enumerate(info.tokens):
        if i in info.consumed:
            continue
        w = t.text.lower().replace("ё", "е")
        if not re.search(r"\w", w) or w in _QUERY_STOP:
            continue
        words.append(t)
    return join_tokens(words)


def without_shift(text: str) -> str:
    """Убрать сдвиг («на два часа позже»), чтобы он не попал в поисковый запрос."""
    toks = tokenize(text)
    t = _Text(toks)
    m = SHIFT_RX.search(t.s)
    if not m:
        return text
    t.consume(m.start(), m.end())
    return join_tokens([tok for i, tok in enumerate(toks) if i not in t.consumed])


def split_target(text: str, now) -> tuple[str, str | None]:
    """Разделить «откуда» и «куда» по первому «на», после которого идёт время или сдвиг («на 12», «на час позже»)."""
    for m in _ON_WORD.finditer(text):
        right = text[m.start():]
        norm = " ".join(t.norm for t in tokenize(right) if t.norm)
        if parse(right, now, "future").found or SHIFT_RX.search(norm):
            return text[: m.start()], right
    return text, None


def _ordinal_free(query: str) -> str:
    return re.sub(r"\b(?:перв|втор|трет|четв[её]рт|пят)(?:ая|ую|ый|ое|ой|ья|ью|ье)\b", "", query,
                  flags=re.IGNORECASE).strip()


class RuleNLU:
    def parse(self, text: str, ctx: NLUContext) -> Command:
        text = text.strip()
        low = re.sub(r"\s+", " ", text.lower().replace("ё", "е")).strip()
        if not low:
            return Command(intent=Intent.UNKNOWN)

        correction = _CORRECTION.match(text)
        if correction:
            rest = text[correction.end():]
            info = parse(rest, ctx.now)
            if (info.found or info.error) and not (_CREATE_START.match(rest) or _REMIND_START.match(rest)):
                return Command(intent=Intent.UPDATE, target_last=True, when=info.phrase)

        if _CANCEL.match(low):
            return Command(intent=Intent.CANCEL)
        short = len(low.split()) <= 5
        if short and _HELP.search(low):
            return Command(intent=Intent.HELP)
        if short and (_GREET.match(low) or _THANKS.match(low)) and not _CREATE_START.search(low):
            return Command(intent=Intent.SMALLTALK)
        if _HELP.search(low):
            return Command(intent=Intent.HELP)

        if _REMIND_START.match(low):
            # «Напомни, что у меня завтра?» — вопрос; «Напомни, что завтра надо забрать посылку» — задача
            if _REMIND_QUESTION.match(low) and not _NEED.search(low):
                cmd = self._list_or_search(text, low, ctx, Intent.LIST)
                if not cmd.query:
                    return cmd
            return self._create(text, low, ctx)
        if _REOPEN.search(low) and not _QUESTION_HEAD.match(low):
            return self._target(text, low, ctx, Intent.REOPEN)
        if _DELETE.search(low):
            return self._target(text, low, ctx, Intent.DELETE)
        if _NOT_HAPPEN.search(low) and not _QUESTION_HEAD.match(low):
            cmd = self._target(text, low, ctx, Intent.DELETE)
            if not cmd.target_last:
                return cmd
        if _COMPLETE.search(low) and not _QUESTION_HEAD.match(low):  # «что я уже сделал?» — вопрос
            return self._target(text, low, ctx, Intent.COMPLETE)
        if _UPDATE.search(low):
            return self._target(text, low, ctx, Intent.UPDATE)
        if _CREATE_START.match(low):
            return self._create(text, low, ctx)
        if _SEARCH.search(low):
            return self._list_or_search(text, low, ctx, Intent.SEARCH)
        if _LIST.search(low):
            return self._list_or_search(text, low, ctx, Intent.LIST)
        if low.endswith("?"):
            return self._list_or_search(text, low, ctx, Intent.LIST)
        if _MEETING_STMT.search(low) or _CREATE_ANY.search(low):
            return self._create(text, low, ctx)

        info = parse(text, ctx.now)
        if info.found and info.remaining_text().strip(" ?.,!"):
            return self._create(text, low, ctx)
        rep = parse_repeat(text)  # «по будням зарядка» — повторение без глагола и времени
        if rep.rule is not None and rep.rest.strip(" ?.,!"):
            return self._create(text, low, ctx)
        return Command(intent=Intent.UNKNOWN)

    # ---------------------------------------------------------------- сценарии

    def _create(self, text: str, low: str, ctx: NLUContext) -> Command:
        rep = parse_repeat(text)  # «каждый понедельник» вырезается до разбора даты
        info = parse(rep.rest, ctx.now, "future")
        item_type = _detect_create_type(text, low)
        if item_type == ItemType.REMINDER and info.range_end and _MEETING_HEAD.search(low):
            item_type = ItemType.MEETING  # явно заданная встреча занимает весь названный интервал
        title = clean_title(info.remaining_text(), item_type)
        return Command(intent=Intent.CREATE, item_type=item_type, title=title or None,
                       when=info.phrase or None, repeat=rep.phrase or None)

    def _list_or_search(self, text: str, low: str, ctx: NLUContext, intent: Intent) -> Command:
        tense = "past" if _PAST.search(low) else "future" if _FUTURE.search(low) else "any"
        prefer = {"past": "past", "future": "future"}.get(tense, "nearest")
        info = parse(text, ctx.now, prefer)
        query = extract_query(info)
        return Command(
            intent=intent,
            item_type=_detect_list_type(low),
            period=info.phrase or None,
            query=query or None,
            tense=tense,
            ask_when=bool(re.search(r"\bкогда\b", low)) and not re.search(r"когда-", low),
            status="open" if _STATUS_OPEN.search(low) else "done" if _STATUS_DONE.search(low) else None,
        )

    def _target(self, text: str, low: str, ctx: NLUContext, intent: Intent) -> Command:
        info = parse(without_shift(text), ctx.now, "future")
        cmd = Command(intent=intent, item_type=_detect_list_type(low))
        cmd.scope = "series" if _SCOPE_SERIES.search(low) else "one" if _SCOPE_ONE.search(low) else None
        if intent == Intent.UPDATE:
            rename = _RENAME.search(text)
            if rename:
                cmd.title = rename.group("t").strip()
                head = text[: rename.start("t")]
                info = parse(head, ctx.now, "future")
                cmd.occurrence = info.phrase or None
            else:
                left, right = split_target(text, ctx.now)
                if right is not None:
                    # «перенеси планёрку в понедельник на 12»: «в понедельник» — какое повторение, «на 12» — куда
                    cmd.occurrence = parse(left, ctx.now, "nearest").phrase or None
                    target = parse(right, ctx.now, "future")
                    cmd.when = target.phrase if target.found else None  # сдвиг «на час позже» считает диалог
                else:
                    cmd.when = info.phrase or None
        else:
            cmd.occurrence = info.phrase or None
        query = _ordinal_free(extract_query(info))
        if cmd.title:
            query = re.sub(r"\b(?:переименуй|назови|название)\b", "", query, flags=re.IGNORECASE).strip()
        cmd.target_last = bool(_PRONOUN.search(low)) or not query
        cmd.query = None if cmd.target_last else query
        return cmd
