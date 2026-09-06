import json
from datetime import date
from pathlib import Path

import pytest

from irsresolve.core.config import load_config
from irsresolve.core.derive import derive_all
from irsresolve.core.expr import Validator, compile_when, eval_when, resolved_repr
from irsresolve.core.facts import Facts, unwrap

CFG = load_config("irsresolve/config")
VAL = Validator(CFG)


def _fd(fixture):
    data = json.loads(Path(f"fixtures/{fixture}").read_text())
    f = unwrap(Facts.model_validate(data["facts"]))
    return f, derive_all(f, CFG, date(2026, 9, 6))


def _run(expr, fixture="case_a_simple_plan.json"):
    f, d = _fd(fixture)
    return eval_when(compile_when(expr, "t", VAL), "t", f, d, CFG)


def test_accepts_comparisons_bools_helpers():
    assert _run('d.total_debt <= cfg.simple_plan.individual.max_balance') is True
    assert _run('f.identity.taxpayer_type == "individual"') is True
    assert _run('intersects(f.enforcement.enforcement_flags, ["levy_wage", "levy_bank"])') is False
    assert _run('any_period(f.debt.tax_periods, "assessed_via_sfr")') is False
    assert _run('min(cfg.simple_plan.max_term_months, d.csed_months_remaining) >= 0') is True
    assert _run('f.compliance.returns_filed_all and not f.debt.liability_disputed') is True


def test_none_layer2_operand_does_not_match():
    # fixture A has no Layer 2 → disposable_income is None; comparison must be False, not error
    assert _run('d.disposable_income <= 0') is False


def test_division_and_term_test():
    assert _run('(d.total_debt / f.quick_capacity.proposed_monthly_payment) <= 120') is True


@pytest.mark.parametrize("expr", [
    "foo()",                                   # call to non-helper
    '__import__("os")',                        # import escape
    "os.system('x')",                          # method call / out-of-namespace
    "[x for x in f.debt.tax_periods]",         # comprehension
    "lambda x: x",                             # lambda
    "f.identity.nope",                         # unknown fact path
    "d.not_a_field > 0",                       # unknown derived path
    "cfg.simple_plan.nope",                    # unknown config key
])
def test_rejects_unsafe_or_unknown(expr):
    with pytest.raises(Exception):
        compile_when(expr, "t", VAL)


def test_resolved_repr_substitutes_values():
    f, d = _fd("case_a_simple_plan.json")
    tree = compile_when('d.total_debt <= cfg.simple_plan.individual.max_balance', "t", VAL)
    s = resolved_repr(tree, f, d, CFG)
    assert "23000" in s and "50000" in s and "→ True" in s
