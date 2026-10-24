"""LLM + правила: LLM разбирает свободную речь, правила страхуют.

- LLM не настроена → работают только правила.
- LLM упала (сеть, лимиты, мусор вместо JSON) → ответ правил, пользователь ничего не замечает.
- LLM вернула команду без времени или текста, а правила их нашли → поля дополняются.
"""
from __future__ import annotations

import logging

from app.nlu.rules import RuleNLU
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
        if self.llm is None:
            return by_rules
        try:
            cmd = self.llm.parse(text, ctx)
        except Exception as exc:
            log.warning("LLM недоступна, используются правила: %s", exc)
            by_rules.source = "rules-fallback"
            return by_rules
        return self._merge(cmd, by_rules, ctx)

    @staticmethod
    def _merge(cmd: Command, by_rules: Command, ctx: NLUContext) -> Command:
        if cmd.intent == Intent.UNKNOWN and by_rules.intent != Intent.UNKNOWN:
            by_rules.source = "rules-fallback"
            return by_rules
        if cmd.intent in (Intent.CREATE, Intent.UPDATE) and by_rules.intent == cmd.intent:
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
        return cmd
