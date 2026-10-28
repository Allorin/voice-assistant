"""LLM + правила: LLM разбирает свободную речь, правила страхуют.

- LLM не настроена → работают только правила.
- LLM упала (сеть, лимиты, мусор вместо JSON) → ответ правил, пользователь ничего не замечает.
- LLM вернула команду без времени или текста, а правила их нашли → поля дополняются.
- LLM не ответила два раза подряд → две минуты запросы к ней не отправляются: пользователь не ждёт таймаута
  на каждой реплике, пока сервис недоступен.
"""
from __future__ import annotations

import logging
import time

from app.nlu.rules import RuleNLU, is_time_correction
from app.nlu.schema import Command, Intent, NLUContext
from app.timeparse.repeat import parse_repeat
from app.timeparse.ru import parse as parse_time

log = logging.getLogger(__name__)


class HybridNLU:
    FAILURES_BEFORE_PAUSE = 2
    PAUSE_SECONDS = 120.0

    def __init__(self, rules: RuleNLU, llm=None):
        self.rules = rules
        self.llm = llm
        self._failures = 0
        self._paused_until = 0.0

    @property
    def mode(self) -> str:
        return "llm+rules" if self.llm else "rules"

    def parse(self, text: str, ctx: NLUContext) -> Command:
        by_rules = self.rules.parse(text, ctx)
        if by_rules.intent == Intent.DELETE:
            return by_rules  # область удаления и период определяются самой командой, а не догадкой LLM
        if by_rules.intent == Intent.UPDATE and is_time_correction(text):
            return by_rules  # поправка к последней записи не должна становиться новым созданием
        if self.llm is None:
            return by_rules
        if time.monotonic() < self._paused_until:
            by_rules.source = "rules-fallback"
            return by_rules
        try:
            cmd = self.llm.parse(text, ctx)
        except Exception as exc:
            self._failures += 1
            if self._failures >= self.FAILURES_BEFORE_PAUSE:
                self._paused_until = time.monotonic() + self.PAUSE_SECONDS
                log.warning("LLM не отвечает %s раза подряд, %.0f с работают только правила: %s",
                            self._failures, self.PAUSE_SECONDS, exc)
            else:
                log.warning("LLM недоступна, используются правила: %s", exc)
            by_rules.source = "rules-fallback"
            return by_rules
        self._failures = 0
        if cmd.intent == Intent.DELETE:
            by_rules.source = "rules-safety"
            return by_rules  # LLM не должна удалять без команды удаления в исходной реплике
        return self._merge(cmd, by_rules, ctx)

    @staticmethod
    def _merge(cmd: Command, by_rules: Command, ctx: NLUContext) -> Command:
        if cmd.intent == Intent.UNKNOWN and by_rules.intent != Intent.UNKNOWN:
            by_rules.source = "rules-fallback"
            return by_rules
        if cmd.intent == Intent.SEARCH and not (cmd.query or "").strip() and by_rules.intent == Intent.LIST:
            by_rules.source = "rules-fallback"
            return by_rules  # «что было в прошлую среду»: искать нечего, это список за период
        if cmd.intent in (Intent.CREATE, Intent.UPDATE) and by_rules.intent == cmd.intent:
            rules_time = parse_time(by_rules.when or "", ctx.now)
            if rules_time.duration_min is not None or rules_time.error:
                cmd.when = by_rules.when  # LLM может сохранить только конечный час интервала
                if rules_time.range_end and cmd.intent == Intent.CREATE and by_rules.item_type is not None:
                    cmd.item_type = by_rules.item_type
            # фраза о времени от LLM должна разбираться; иначе берём найденную правилами
            if not cmd.when or not parse_time(cmd.when, ctx.now).found:
                cmd.when = by_rules.when or cmd.when
            if cmd.intent == Intent.CREATE and not cmd.title:
                cmd.title = by_rules.title
            if cmd.intent == Intent.CREATE and by_rules.repeat and (
                    not cmd.repeat or parse_repeat(cmd.repeat).rule is None):
                cmd.repeat = by_rules.repeat  # LLM не заметила повторение или написала его по-своему
        if cmd.intent == Intent.CREATE and by_rules.intent == Intent.CREATE and by_rules.repeat:
            ours, theirs = parse_repeat(by_rules.repeat).rule, parse_repeat(cmd.repeat or "").rule
            if ours is not None and theirs is not None and len(ours.to_rrule()) > len(theirs.to_rrule()) \
                    and ours.to_rrule().startswith(theirs.to_rrule()):
                cmd.repeat = by_rules.repeat  # «каждый месяц» без числа, когда в реплике было «15 числа»
        if cmd.intent in (Intent.UPDATE, Intent.DELETE, Intent.COMPLETE, Intent.REOPEN) \
                and by_rules.intent == cmd.intent:
            cmd.occurrence = cmd.occurrence or by_rules.occurrence
            cmd.scope = cmd.scope or by_rules.scope
            if by_rules.target_last and not cmd.target_last and not cmd.query:
                cmd.target_last = True  # «перенеси её» — про последнюю запись из разговора
        if cmd.intent in (Intent.LIST, Intent.SEARCH) and cmd.period and not parse_time(cmd.period, ctx.now).found:
            cmd.period = by_rules.period
        if cmd.intent in (Intent.LIST, Intent.SEARCH) and by_rules.intent == cmd.intent:
            period = parse_time(by_rules.period or "", ctx.now)
            if period.range_end or period.error:
                cmd.period = by_rules.period
            # LLM пропустила поле, которое правила нашли в той же реплике
            cmd.period = cmd.period or by_rules.period
            cmd.query = cmd.query or by_rules.query
            cmd.status = cmd.status or by_rules.status
            cmd.item_type = cmd.item_type or by_rules.item_type  # «где задача про смету» — только задачи
            if cmd.tense in (None, "any") and by_rules.tense not in (None, "any"):
                cmd.tense = by_rules.tense
        return cmd
