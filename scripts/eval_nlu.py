"""Оценка качества понимания запросов на наборе eval/phrases.jsonl.

    python scripts/eval_nlu.py              # правила
    python scripts/eval_nlu.py --nlu llm    # LLM из .env (LLM_BASE_URL, LLM_MODEL) + страховка правилами

«Сейчас» зафиксировано: вторник, 20.10.2026 19:59 — как в тестах.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.nlu.hybrid import HybridNLU  # noqa: E402
from app.nlu.rules import RuleNLU  # noqa: E402
from app.nlu.schema import NLUContext  # noqa: E402
from app.search.text import lemmas  # noqa: E402
from app.timeparse.repeat import parse_repeat  # noqa: E402
from app.timeparse.ru import creation_moment, parse  # noqa: E402

NOW = datetime(2026, 10, 20, 19, 59)


def check(cmd, exp: dict) -> list[str]:
    errors = []
    if cmd.intent.value != exp["intent"]:
        return [f"intent {cmd.intent.value} != {exp['intent']}"]
    if "item_type" in exp and (cmd.item_type.value if cmd.item_type else None) != exp["item_type"]:
        errors.append(f"type {cmd.item_type} != {exp['item_type']}")
    if "title" in exp and (cmd.title or "").lower().replace("ё", "е") != exp["title"].lower().replace("ё", "е"):
        errors.append(f"title {cmd.title!r} != {exp['title']!r}")
    if "due" in exp:
        due, _ = creation_moment(parse(cmd.when or "", NOW, "future"), NOW)
        got = due.strftime("%Y-%m-%dT%H:%M") if due else None
        if got != exp["due"]:
            errors.append(f"due {got} != {exp['due']} (when={cmd.when!r})")
    if "period_start" in exp:
        prefer = {"past": "past", "future": "future"}.get(cmd.tense, "nearest")
        rng = parse(cmd.period or "", NOW, prefer).query_range()
        got = rng[0].strftime("%Y-%m-%dT%H:%M") if rng else None
        if got != exp["period_start"]:
            errors.append(f"period {got} != {exp['period_start']} (period={cmd.period!r})")
    if "query" in exp and exp["query"] not in lemmas(cmd.query or ""):
        errors.append(f"query {cmd.query!r} не содержит «{exp['query']}»")
    if "tense" in exp and cmd.tense != exp["tense"]:
        errors.append(f"tense {cmd.tense} != {exp['tense']}")
    if "repeat" in exp:
        rule = parse_repeat(cmd.repeat or "").rule
        got = rule.to_rrule() if rule else None
        if got != exp["repeat"]:
            errors.append(f"repeat {got} != {exp['repeat']} (repeat={cmd.repeat!r})")
    if "status" in exp and cmd.status != exp["status"]:
        errors.append(f"status {cmd.status} != {exp['status']}")
    if "target_last" in exp and cmd.target_last != exp["target_last"]:
        errors.append(f"target_last {cmd.target_last}")
    return errors


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nlu", choices=["rules", "llm"], default="rules")
    ap.add_argument("--file", default=str(ROOT / "eval" / "phrases.jsonl"))
    args = ap.parse_args()

    llm = None
    if args.nlu == "llm":
        s = get_settings()
        if not s.llm_enabled:
            sys.exit("LLM не настроена: заполните LLM_BASE_URL и LLM_MODEL в .env")
        from app.nlu.llm import LLMNLU

        llm = LLMNLU(s.llm_base_url, s.llm_api_key, s.llm_model, s.llm_timeout)
    nlu = HybridNLU(RuleNLU(), llm)

    rows = [json.loads(line) for line in Path(args.file).read_text(encoding="utf-8").splitlines() if line.strip()]
    ok_intent = ok_full = 0
    for row in rows:
        cmd = nlu.parse(row["text"], NLUContext(now=NOW))
        errors = check(cmd, row)
        ok_intent += cmd.intent.value == row["intent"]
        ok_full += not errors
        mark = "OK  " if not errors else "FAIL"
        print(f"{mark} {row['text']}" + ("" if not errors else f"\n       {'; '.join(errors)}"))
    n = len(rows)
    print(f"\nNLU: {nlu.mode}. Фраз: {n}. Интент верно: {ok_intent}/{n} ({ok_intent / n:.0%}). "
          f"Все поля верно: {ok_full}/{n} ({ok_full / n:.0%}).")


if __name__ == "__main__":
    main()
