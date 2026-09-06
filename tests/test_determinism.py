"""Same facts + same config → byte-identical result JSON (excluding generated_at)."""

import json
from datetime import date
from pathlib import Path

from irsresolve.core.config import load_config
from irsresolve.core.engine import Engine
from irsresolve.core.expr import Validator
from irsresolve.core.facts import Facts
from irsresolve.core.rules import load_rules

CFG = load_config("irsresolve/config")
ENGINE = Engine(CFG, load_rules("irsresolve/rules", Validator(CFG), CFG))


def _dump(fixture):
    data = json.loads(Path(f"fixtures/{fixture}").read_text())
    r = ENGINE.evaluate(Facts.model_validate(data["facts"]), as_of=date.fromisoformat(data["as_of"]))
    out = r.model_dump(mode="json")
    out.pop("generated_at")
    return json.dumps(out, sort_keys=True, default=str)


def test_two_runs_are_byte_identical():
    assert _dump("case_d_ppia_cdp.json") == _dump("case_d_ppia_cdp.json")
