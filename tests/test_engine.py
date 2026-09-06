import copy
import json
from datetime import date
from pathlib import Path

import pytest

from irsresolve.core.config import load_config
from irsresolve.core.engine import Engine
from irsresolve.core.errors import FactsError
from irsresolve.core.expr import Validator
from irsresolve.core.facts import Facts
from irsresolve.core.rules import load_rules

CFG = load_config("irsresolve/config")
RULES = load_rules("irsresolve/rules", Validator(CFG), CFG)
ENGINE = Engine(CFG, RULES)
AS_OF = date(2026, 9, 6)


def _facts(fixture):
    return json.loads(Path(f"fixtures/{fixture}").read_text())["facts"]


def test_layer1_termination_skips_layer2():
    r = ENGINE.evaluate(Facts.model_validate(_facts("case_a_simple_plan.json")), as_of=AS_OF)
    assert r.primary.outcome == "SIMPLE_PAYMENT_PLAN"
    assert not any(t.layer.startswith("L2") for t in r.trace)  # no Layer 2 evaluated
    assert r.primary.citations and r.primary.citations[0].url.startswith("https://")


def test_unattested_leaf_rejected_before_rules():
    bad = copy.deepcopy(_facts("case_a_simple_plan.json"))
    bad["compliance"]["returns_filed_all"] = {"value": True, "provenance": {"source": "user", "attested": False}}
    with pytest.raises(FactsError) as e:
        ENGINE.evaluate(Facts.model_validate(bad), as_of=AS_OF)
    assert "compliance.returns_filed_all" in str(e.value)


def test_needs_financial_disclosure_lists_missing_groups():
    facts = copy.deepcopy(_facts("case_a_simple_plan.json"))
    facts["debt"]["tax_periods"][0]["tax"] = 80000  # over Simple Plan threshold, no L2 facts
    r = ENGINE.evaluate(Facts.model_validate(facts), as_of=AS_OF)
    assert r.primary.outcome == "NEEDS_FINANCIAL_DISCLOSURE"
    assert set(r.primary.extras["missing_groups"]) == {"household", "income", "expenses", "assets"}
    assert r.excluded == []


def test_blocker_has_no_excluded_or_alternatives():
    r = ENGINE.evaluate(Facts.model_validate(_facts("case_c_business_blocked.json")), as_of=AS_OF)
    assert r.primary.outcome == "BLOCKER_COMPLIANCE"
    assert r.excluded == [] and r.alternatives == [] and r.urgent == []
