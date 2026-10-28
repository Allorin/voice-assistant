"""Контрольные наборы фраз из eval/ не должны деградировать на правилах."""
import importlib.util
import json

import pytest

from app.nlu.rules import RuleNLU
from app.nlu.schema import NLUContext
from tests.conftest import ROOT

spec = importlib.util.spec_from_file_location("eval_nlu", ROOT / "scripts" / "eval_nlu.py")
eval_nlu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eval_nlu)
nlu = RuleNLU()


def rows(name):
    lines = (ROOT / "eval" / name).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


@pytest.mark.parametrize("name", ["phrases.jsonl", "holdout.jsonl"])
def test_eval_set_on_rules(name):
    failed = {}
    for row in rows(name):
        errors = eval_nlu.check(nlu.parse(row["text"], NLUContext(now=eval_nlu.NOW)), row)
        if errors:
            failed[row["text"]] = errors
    assert not failed
