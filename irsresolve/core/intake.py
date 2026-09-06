"""Incomplete questionnaire state and final attested-facts assembly.

The questionnaire deliberately allows partial progress. Only ``build_facts`` crosses the boundary
into the strict ``Facts`` model used by the deterministic eligibility engine.
"""

from __future__ import annotations

import copy
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .facts import Facts


REQUIRED_FINANCIAL_PATHS = {
    "household.household_size",
    "household.zip_code",
    "household.state",
    "income.monthly_gross_income",
    "expenses.housing_utilities",
    "expenses.food_clothing_misc",
    "expenses.transportation_ownership",
    "expenses.transportation_operating",
    "expenses.healthcare_out_of_pocket",
    "expenses.taxes_withheld_or_estimated",
    "assets.cash_and_bank",
}


class TaxPeriodDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    year: int = Field(default_factory=lambda: date.today().year - 1)
    tax_type: Literal["income_1040", "income_1120", "payroll_941", "payroll_940", "excise", "other"] = "income_1040"
    tax: Decimal = Decimal(0)
    penalty: Decimal = Decimal(0)
    interest: Decimal = Decimal(0)
    assessment_date: date | None = None
    return_filed_date: date | None = None
    return_due_date: date | None = None
    filed_jointly: bool = False
    assessed_via_sfr: bool = False
    assessed_via_audit: bool = False


class QuestionnaireDraft(BaseModel):
    """Flat, serializable state for the short questions-first wizard."""

    model_config = ConfigDict(extra="forbid")
    taxpayer_type: Literal["individual", "self_employed", "business"] = "individual"
    business_entity_type: Literal["sole_prop", "partnership", "s_corp", "c_corp", "llc"] | None = None
    has_employees: bool | None = None
    age: int = 45
    tax_periods: list[TaxPeriodDraft] = Field(default_factory=lambda: [TaxPeriodDraft()])
    liability_disputed: bool = False
    dispute_reason: str | None = None
    prior_appeal_or_court: bool | None = None
    returns_filed_all: bool = True
    unfiled_years: list[int] | None = None
    current_year_estimated_paid: bool | None = None
    ftd_current: bool | None = None
    bankruptcy_status: Literal["none", "open_ch7", "open_ch13", "discharged", "dismissed"] = "none"
    can_full_pay_180_days: bool = False
    proposed_monthly_payment: Decimal = Decimal(0)
    prior_5yr_compliance: bool = False
    prior_ia_in_5yrs: bool = False
    enforcement_flags: list[Literal["levy_wage", "levy_bank", "lien_filed", "seizure_notice", "passport_certified", "none"]] = Field(default_factory=lambda: ["none"])
    notices_received: list[dict[str, Any]] | None = None
    prior_3yr_penalty_history: bool = False
    penalty_reason_category: Literal["none", "illness", "disaster", "records_unavailable", "irs_advice", "professional_reliance", "other"] = "none"
    combat_zone: bool = False
    disaster_zone_resident: bool = False
    hardship_circumstances: list[Literal["serious_illness", "disability", "elderly", "dependent_special_needs", "natural_disaster", "death_in_family", "unemployment", "none"]] = Field(default_factory=lambda: ["none"])
    spouse_issues: list[Literal["spouse_concealed_income", "separated_or_divorced", "abuse", "refund_offset_to_spouse_debt", "none"]] = Field(default_factory=lambda: ["none"])

    @property
    def required_financial_document(self) -> Literal["433a", "433b"]:
        return "433b" if self.taxpayer_type == "business" else "433a"


def _set_path(tree: dict[str, Any], dotted: str, value: Any) -> None:
    node = tree
    parts = dotted.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _attested(value: Any, *, source: str = "user", ref: str | None = None) -> dict[str, Any]:
    return {
        "value": value,
        "provenance": {"source": source, "ref": ref, "attested": True},
    }


def questionnaire_base(draft: QuestionnaireDraft) -> dict[str, Any]:
    """Convert completed questionnaire answers into engine-shaped, user-attested groups."""
    enforcement = [x for x in draft.enforcement_flags if x != "none"] or ["none"]
    hardship = [x for x in draft.hardship_circumstances if x != "none"] or ["none"]
    spouse = [x for x in draft.spouse_issues if x != "none"] or ["none"]
    identity: dict[str, Any] = {"taxpayer_type": draft.taxpayer_type, "age": draft.age}
    if draft.taxpayer_type == "business":
        identity.update(
            business_entity_type=draft.business_entity_type or "llc",
            has_employees=bool(draft.has_employees),
        )
    compliance: dict[str, Any] = {
        "returns_filed_all": draft.returns_filed_all,
        "bankruptcy_status": draft.bankruptcy_status,
    }
    if not draft.returns_filed_all and draft.unfiled_years:
        compliance["unfiled_years"] = draft.unfiled_years
    if draft.taxpayer_type == "self_employed":
        compliance["current_year_estimated_paid"] = bool(draft.current_year_estimated_paid)
    if draft.taxpayer_type == "business":
        compliance["ftd_current"] = bool(draft.ftd_current)
    debt: dict[str, Any] = {
        "tax_periods": [period.model_dump(mode="json") for period in draft.tax_periods],
        "liability_disputed": draft.liability_disputed,
    }
    if draft.liability_disputed:
        debt["dispute_reason"] = draft.dispute_reason or "other"
        debt["prior_appeal_or_court"] = bool(draft.prior_appeal_or_court)
    return {
        "identity": identity,
        "debt": debt,
        "compliance": compliance,
        "quick_capacity": {
            "can_full_pay_180_days": draft.can_full_pay_180_days,
            "proposed_monthly_payment": draft.proposed_monthly_payment,
            "prior_5yr_compliance": draft.prior_5yr_compliance,
            "prior_ia_in_5yrs": draft.prior_ia_in_5yrs,
        },
        "enforcement": {
            "enforcement_flags": enforcement,
            **({"notices_received": draft.notices_received} if draft.notices_received else {}),
        },
        "circumstances": {
            "prior_3yr_penalty_history": draft.prior_3yr_penalty_history,
            "penalty_reason_category": draft.penalty_reason_category,
            "combat_zone": draft.combat_zone,
            "disaster_zone_resident": draft.disaster_zone_resident,
            "hardship_circumstances": hardship,
            "spouse_issues": spouse,
        },
    }


def build_facts(draft: QuestionnaireDraft, confirmed_values: dict[str, dict[str, Any]]) -> Facts:
    """Build strict Facts from the questionnaire plus explicitly confirmed document/manual values."""
    missing = sorted(REQUIRED_FINANCIAL_PATHS - confirmed_values.keys())
    if missing:
        raise ValueError(f"missing confirmed financial facts: {', '.join(missing)}")
    for path in ("household.zip_code", "household.state"):
        if not str(confirmed_values[path]["value"]).strip():
            raise ValueError(f"{path} cannot be blank")
    tree = copy.deepcopy(questionnaire_base(draft))
    for path, proposal in confirmed_values.items():
        _set_path(
            tree,
            path,
            _attested(
                proposal["value"],
                source=proposal.get("source", "user"),
                ref=proposal.get("ref"),
            ),
        )
    return Facts.model_validate(tree)
