"""Integration: each fixture's engine result must match its `_expected` block."""

import json
from datetime import date
from pathlib import Path

import pytest

from irsresolve.cli import _check_expected
from irsresolve.core.config import load_config
from irsresolve.core.engine import Engine
from irsresolve.core.expr import Validator
from irsresolve.core.facts import Facts
from irsresolve.core.rules import load_rules

CFG = load_config("irsresolve/config")
ENGINE = Engine(CFG, load_rules("irsresolve/rules", Validator(CFG), CFG))
FIXTURES = sorted(p.name for p in Path("fixtures").glob("case_*.json"))


@pytest.mark.parametrize("fixture", FIXTURES)
def test_fixture_matches_expected(fixture):
    data = json.loads(Path(f"fixtures/{fixture}").read_text())
    result = ENGINE.evaluate(Facts.model_validate(data["facts"]), as_of=date.fromisoformat(data["as_of"]))
    problems = _check_expected(result, data["_expected"])
    assert not problems, f"{fixture}: {problems}"


def test_every_determination_has_a_citation_with_url():
    for fixture in FIXTURES:
        data = json.loads(Path(f"fixtures/{fixture}").read_text())
        r = ENGINE.evaluate(Facts.model_validate(data["facts"]), as_of=date.fromisoformat(data["as_of"]))
        dets = [r.primary, *r.urgent, *r.alternatives, *r.stacked_relief, *r.excluded]
        for det in dets:
            assert det.citations, f"{fixture}: {det.outcome} has no citation"
            assert all(c.url.startswith("https://") for c in det.citations)


def test_fta_rerun_scenario_present_for_d():
    data = json.loads(Path("fixtures/case_d_ppia_cdp.json").read_text())
    r = ENGINE.evaluate(Facts.model_validate(data["facts"]), as_of=date(2026, 9, 6))
    assert r.scenario_if_penalties_abated.total_debt == 61300.0
    assert r.scenario_if_penalties_abated.changed is False
    assert r.primary.professional_referral_recommended is True and r.referrals
