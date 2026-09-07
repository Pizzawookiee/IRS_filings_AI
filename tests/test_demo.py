"""Smoke test for the Streamlit demo: loading an example renders Analysis without error."""

import json
from datetime import date
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "irsresolve" / "demo" / "app.py")


def test_load_example_d_renders_analysis():
    at = AppTest.from_file(APP, default_timeout=30).run()
    [b for b in at.button if b.label.startswith("D —")][0].click().run()
    assert not at.exception
    texts = " ".join(str(getattr(m, "value", "")) for m in at.markdown)
    assert "Partial Payment Installment Agreement" in texts  # recommended path
    assert "Collection Due Process" in texts                 # urgent banner
    assert len(at.metric) == 4                                # header chips


def test_analysis_prompts_for_missing_csed_date_instead_of_crashing():
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "case_a_simple_plan.json"
    facts = json.loads(fixture.read_text())["facts"]
    period = facts["debt"]["tax_periods"][0]
    period["assessment_date"] = None
    period["return_filed_date"] = None
    period["return_due_date"] = None

    at = AppTest.from_file(APP, default_timeout=30).run()
    at.session_state["facts"] = facts
    at.session_state["page"] = "Analysis"
    at.run()

    assert not at.exception
    warnings = " ".join(str(message.value) for message in at.warning)
    assert "date is needed for each tax period" in warnings
    assert "NoneType" not in warnings
    at.date_input[0].set_value(date(2023, 1, 1))
    [button for button in at.button if button.label == "Save dates and analyze"][0].click().run()

    assert not at.exception
    assert len(at.metric) == 4
    saved_period = at.session_state["facts"]["debt"]["tax_periods"]["value"][0]
    assert saved_period["assessment_date"] == "2023-01-01"
