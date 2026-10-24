"""LLM + правила: LLM разбирает свободную речь, правила страхуют.

- LLM не настроена → работают только правила.
- LLM упала (сеть, лимиты, мусор вместо JSON) → ответ правил, пользователь ничего не замечает.
- LLM вернула команду без времени или текста, а правила их нашли → поля дополняются.
"""
from __future__ import annotations

import logging

from app.nlu.rules import RuleNLU, is_time_correction
from app.nlu.schema import Command, Intent, NLUContext
from app.timeparse.repeat import parse_repeat
from app.timeparse.ru import parse as parse_time

log = logging.getLogger(__name__)


class HybridNLU:
    def __init__(self, rules: RuleNLU, llm=None):
        self.rules = rules
        self.llm = llm

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
        try:
            cmd = self.llm.parse(text, ctx)
        except Exception as exc:
            log.warning("LLM недоступна, используются правила: %s", exc)
            by_rules.source = "rules-fallback"
            return by_rules
        if cmd.intent == Intent.DELETE:
            by_rules.source = "rules-safety"
            return by_rules  # LLM не должна удалять без команды удаления в исходной реплике
        return self._merge(cmd, by_rules, ctx)

    @staticmethod
    def _merge(cmd: Command, by_rules: Command, ctx: NLUContext) -> Command:
        if cmd.intent == Intent.UNKNOWN and by_rules.intent != Intent.UNKNOWN:
            by_rules.source = "rules-fallback"
            return by_rules
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
        if cmd.intent in (Intent.UPDATE, Intent.DELETE, Intent.COMPLETE, Intent.REOPEN) \
                and by_rules.intent == cmd.intent:
            cmd.occurrence = cmd.occurrence or by_rules.occurrence
            cmd.scope = cmd.scope or by_rules.scope
        if cmd.intent in (Intent.LIST, Intent.SEARCH) and cmd.period and not parse_time(cmd.period, ctx.now).found:
            cmd.period = by_rules.period
        if cmd.intent in (Intent.LIST, Intent.SEARCH) and by_rules.intent == cmd.intent:
            period = parse_time(by_rules.period or "", ctx.now)
            if period.range_end or period.error:
                cmd.period = by_rules.period
        return cmd
