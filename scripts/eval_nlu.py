"""Оценка качества понимания запросов на наборе eval/phrases.jsonl.

    python scripts/eval_nlu.py              # правила
    python scripts/eval_nlu.py --nlu llm    # LLM из .env (GigaChat или OpenAI-совместимый API) + страховка правилами
    python scripts/eval_nlu.py --nlu llm-only   # только LLM, без подстраховки правилами
    python scripts/eval_nlu.py --file eval/holdout.jsonl   # отложенный набор: на нём ничего не настраивали

«Сейчас» зафиксировано: вторник, 20.10.2026 19:59 — как в тестах.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.nlu.factory import create_llm  # noqa: E402
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
    ap.add_argument("--nlu", choices=["rules", "llm", "llm-only"], default="rules")
    ap.add_argument("--file", default=str(ROOT / "eval" / "phrases.jsonl"))
    ap.add_argument("--always-llm", action="store_true",
                    help="с --nlu llm: спрашивать LLM на каждой фразе, даже когда правила уверены")
    args = ap.parse_args()

    llm = None
    if args.nlu in ("llm", "llm-only"):
        s = get_settings()
        if not s.llm_enabled:
            sys.exit("LLM не настроена: заполните GIGACHAT_CREDENTIALS (LLM_PROVIDER=gigachat) "
                     "или LLM_BASE_URL и LLM_MODEL в .env")
        llm = create_llm(s)
    nlu = HybridNLU(RuleNLU(), llm, always_llm=args.always_llm)
    mode = nlu.mode if args.nlu != "llm-only" else "llm-only"
    if args.nlu == "llm":
        mode += " (LLM на каждой фразе)" if args.always_llm else " (LLM, только когда правила не уверены)"

    rows = [json.loads(line) for line in Path(args.file).read_text(encoding="utf-8").splitlines() if line.strip()]
    ok_intent = ok_full = 0
    llm_answers = llm_errors = rules_sure = 0
    started = time.perf_counter()
    for row in rows:
        ctx = NLUContext(now=NOW)
        if args.nlu == "llm-only":
            try:
                cmd = llm.parse(row["text"], ctx)
            except Exception as exc:  # ошибка сети или ответа: считаем фразу неразобранной
                llm_errors += 1
                print(f"ERR  {row['text']}\n       {exc}")
                continue
        else:
            cmd = nlu.parse(row["text"], ctx)
        llm_answers += cmd.source == "llm"
        rules_sure += cmd.source == "rules-sure"
        errors = check(cmd, row)
        ok_intent += cmd.intent.value == row["intent"]
        ok_full += not errors
        mark = "OK  " if not errors else "FAIL"
        print(f"{mark} {row['text']}" + ("" if not errors else f"\n       {'; '.join(errors)}"))
    n = len(rows)
    per_phrase = (time.perf_counter() - started) / max(n, 1)
    print(f"\nNLU: {mode}. Набор: {Path(args.file).name}. Фраз: {n}. "
          f"Интент верно: {ok_intent}/{n} ({ok_intent / n:.0%}). Все поля верно: {ok_full}/{n} ({ok_full / n:.0%}). "
          f"В среднем {per_phrase:.2f} с на фразу.")
    if args.nlu == "llm":
        print(f"LLM: ответов модели {llm_answers}/{n}; правила уверены, LLM не нужна: {rules_sure}/{n}; "
              f"ответов правил вместо модели (сбой или страховка): {n - llm_answers - rules_sure}/{n}.")
    if args.nlu == "llm-only":
        print(f"LLM: ошибок запроса {llm_errors}/{n}.")


if __name__ == "__main__":
    main()
