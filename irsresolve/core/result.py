"""EligibilityResult and its parts (architecture §8.3, impl-plan §Result).

Display names, forms, and citation URLs live in config, never here — these models only
carry resolved values. `extras` holds program-specific numbers (monthly payment, offer
floors, days_remaining) so we don't sprout a typed field per outcome.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    source: str
    url: str
    authority: str  # irc | treas_reg | irm | irs_gov | form_instructions
    excerpt_paraphrase: str | None = None
    retrieved: str | None = None


class Determination(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: str
    display_name: str
    variant: str | None = None
    reason: str
    rule_id: str | None = None
    inputs_relied_on: list[str] = []
    citations: list[Citation] = []
    professional_referral_recommended: bool = False
    referral_reason: str | None = None
    forms: list[str] = []
    what_happens_next: str | None = None
    priority: str = "P2"
    extras: dict[str, Any] = {}


class RuleTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_id: str
    layer: str
    matched: bool
    when_resolved: str  # expression with values substituted, e.g. "68400 <= 50000 → False"


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total_debt: float
    primary: str
    changed: bool


class EligibilityResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generated_at: str
    engine_version: str
    config_version: str
    audience: str = "taxpayer"
    inputs_summary: dict[str, Any] = {}
    urgent: list[Determination] = []
    primary: Determination
    alternatives: list[Determination] = []
    stacked_relief: list[Determination] = []
    excluded: list[Determination] = []
    scenario_if_penalties_abated: Optional[Scenario] = None
    referrals: list[dict[str, Any]] = []
    reviewer_queue: None = None  # reserved (§9); never populated in v1
    disclaimer: str = ""
    trace: list[RuleTrace] = []
