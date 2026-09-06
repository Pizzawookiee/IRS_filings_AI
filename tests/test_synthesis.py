import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from irsresolve.analysis.openrouter import OpenRouterAnalysisSynthesizer
from irsresolve.core.config import load_config
from irsresolve.core.engine import Engine
from irsresolve.core.errors import DocumentInferenceError, MissingCredentialsError
from irsresolve.core.expr import Validator
from irsresolve.core.facts import Facts
from irsresolve.core.rules import load_rules


ROOT = Path(__file__).parents[1]


def _case_and_result():
    case = json.loads((ROOT / "fixtures" / "case_d_ppia_cdp.json").read_text())
    facts = Facts.model_validate(case["facts"])
    config = load_config(str(ROOT / "irsresolve" / "config"))
    rules = load_rules(str(ROOT / "irsresolve" / "rules"), Validator(config), config)
    result = Engine(config, rules).evaluate(facts, as_of=date.fromisoformat(case["as_of"]))
    return facts, result


def _body(primary, alternatives=None):
    content = {
        "situation_summary": "Your confirmed information points to a payment option with review needs.",
        "confidence": "medium",
        "primary": {
            "outcome": primary,
            "summary": "The rule based screening selected this as the leading path.",
            "why": "Your ability to pay and available assets support further review of this path.",
        },
        "alternatives": alternatives or [],
        "urgent_summary": ["A collection notice may require prompt attention."],
        "uncertainties": ["A professional should verify the financial statement."],
        "next_steps": ["Review every confirmed fact before contacting the tax agency."],
        "referral_guidance": ["Consider help from a qualified tax professional."],
    }
    return {"choices": [{"message": {"content": json.dumps(content)}}]}


def _synth(handler):
    return OpenRouterAnalysisSynthesizer(
        api_key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_synthesis_is_constrained_to_engine_outcomes():
    facts, eligibility = _case_and_result()
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=_body(eligibility.primary.outcome))

    result = _synth(handler).synthesize(facts, eligibility)
    assert result.primary.outcome == eligibility.primary.outcome
    schema = seen["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["primary"]["properties"]["outcome"]["enum"]
    assert seen["provider"] == {"require_parameters": True}
    assert seen["temperature"] == 0


def test_synthesis_rejects_changed_primary_and_unsupported_alternative():
    facts, eligibility = _case_and_result()
    changed_primary = eligibility.alternatives[0].outcome
    with pytest.raises(DocumentInferenceError, match="changed"):
        _synth(lambda request: httpx.Response(200, json=_body(changed_primary))).synthesize(facts, eligibility)

    alternative = [{"outcome": eligibility.primary.outcome, "summary": "Possible path.", "why": "Needs review."}]
    with pytest.raises(DocumentInferenceError, match="unsupported alternative"):
        _synth(lambda request: httpx.Response(
            200, json=_body(eligibility.primary.outcome, alternative)
        )).synthesize(facts, eligibility)


def test_synthesis_rejects_model_invented_numbers_and_refusal():
    facts, eligibility = _case_and_result()
    numbered = _body(eligibility.primary.outcome)
    content = json.loads(numbered["choices"][0]["message"]["content"])
    content["situation_summary"] = "Pay $1000."
    numbered["choices"][0]["message"]["content"] = json.dumps(content)
    with pytest.raises(DocumentInferenceError, match="invalid analysis"):
        _synth(lambda request: httpx.Response(200, json=numbered)).synthesize(facts, eligibility)

    refused = {"choices": [{"message": {"refusal": "cannot comply", "content": ""}}]}
    with pytest.raises(DocumentInferenceError, match="refused"):
        _synth(lambda request: httpx.Response(200, json=refused)).synthesize(facts, eligibility)


def test_synthesis_missing_configuration_is_recoverable(monkeypatch):
    facts, eligibility = _case_and_result()
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(MissingCredentialsError):
        OpenRouterAnalysisSynthesizer().synthesize(facts, eligibility)
