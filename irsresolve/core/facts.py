"""Canonical taxpayer facts (architecture §4.1) with per-leaf provenance and attestation.

Every leaf the engine may evaluate is wrapped in `Attested[T]` = {value, provenance}.
The engine refuses to evaluate any leaf whose provenance.attested is False (§4.4 invariant).

Fixtures represent post-confirm taxpayer input, so a bare scalar/list in JSON auto-wraps
to `attested=True, source="user"` (see Attested._wrap). Document parsers instead set real
provenance with attested=False; those values only become attested via the confirm step.

Attestation is tracked at the leaf/collection level: a list field (tax_periods, vehicles,
income_sources...) carries one Attested wrapper for the whole collection, with plain inner
models. This matches the §8.3 provenance map, which keys off top-level leaves, not per-row.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Generic, Literal, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .errors import FactsError

T = TypeVar("T")

Source = Literal["user", "w2", "1099", "433a", "433b", "notice", "transcript", "irs_standard_prefill"]


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Source = "user"
    attested: bool = False
    ref: str | None = None  # e.g. "W-2 Box 1", "433-A §5", "state_fallback"


class Attested(BaseModel, Generic[T]):
    """A value plus where it came from and whether the taxpayer attested it."""

    model_config = ConfigDict(extra="forbid")
    value: T
    provenance: Provenance

    @model_validator(mode="before")
    @classmethod
    def _wrap(cls, data):
        # Already an {value, provenance} object → pass through. Otherwise treat a bare
        # scalar/list as user-attested input (the confirm-screen default for fixtures).
        if isinstance(data, dict) and "value" in data and "provenance" in data:
            return data
        return {"value": data, "provenance": {"source": "user", "attested": True}}


# ---- inner (plain) models for object collections ----

TaxType = Literal["income_1040", "income_1120", "payroll_941", "payroll_940", "excise", "other"]


class TaxPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid")
    year: int
    tax_type: TaxType
    tax: Decimal
    penalty: Decimal = Decimal(0)
    interest: Decimal = Decimal(0)
    assessment_date: date | None = None
    return_filed_date: date | None = None
    return_due_date: date | None = None
    filed_jointly: bool = False
    assessed_via_sfr: bool = False
    assessed_via_audit: bool = False


class IncomeSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["wages", "self_employment", "rental", "pension", "social_security", "unemployment", "other"]
    amount: Decimal
    source_name: str | None = None


class RealEstate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fmv: Decimal
    mortgage_balance: Decimal = Decimal(0)
    is_primary_residence: bool = False


class Vehicle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fmv: Decimal
    loan_balance: Decimal = Decimal(0)
    is_necessary: bool = True


class Notice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["CP14", "CP501", "CP503", "CP504", "LT11", "L1058", "L3172", "other"]
    date: date


# ---- fact groups (§4.1) ----


class Identity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    taxpayer_type: Attested[Literal["individual", "self_employed", "business"]]
    business_entity_type: Optional[Attested[Literal["sole_prop", "partnership", "s_corp", "c_corp", "llc"]]] = None
    has_employees: Optional[Attested[bool]] = None
    age: Optional[Attested[int]] = None  # §3.9 needs it; not listed in §4.1 — see plan gap #4


class Debt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tax_periods: Attested[list[TaxPeriod]]
    liability_disputed: Attested[bool]
    dispute_reason: Optional[Attested[str]] = None
    prior_appeal_or_court: Optional[Attested[bool]] = None


class Compliance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    returns_filed_all: Attested[bool]
    unfiled_years: Optional[Attested[list[int]]] = None
    current_year_estimated_paid: Optional[Attested[bool]] = None
    ftd_current: Optional[Attested[bool]] = None
    bankruptcy_status: Attested[Literal["none", "open_ch7", "open_ch13", "discharged", "dismissed"]]
    bankruptcy_petition_date: Optional[Attested[date]] = None


class QuickCapacity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    can_full_pay_180_days: Attested[bool]
    proposed_monthly_payment: Attested[Decimal]
    prior_5yr_compliance: Attested[bool]
    prior_ia_in_5yrs: Attested[bool]


class Household(BaseModel):
    model_config = ConfigDict(extra="forbid")
    household_size: Attested[int]
    zip_code: Attested[str]
    state: Attested[str]


class Income(BaseModel):
    model_config = ConfigDict(extra="forbid")
    monthly_gross_income: Attested[Decimal]
    income_sources: Optional[Attested[list[IncomeSource]]] = None
    spouse_income_included: Optional[Attested[bool]] = None


class Expenses(BaseModel):
    model_config = ConfigDict(extra="forbid")
    housing_utilities: Attested[Decimal]
    food_clothing_misc: Attested[Decimal]
    transportation_ownership: Attested[Decimal]
    transportation_operating: Attested[Decimal]
    healthcare_out_of_pocket: Attested[Decimal]
    taxes_withheld_or_estimated: Attested[Decimal]
    health_insurance: Optional[Attested[Decimal]] = None
    childcare: Optional[Attested[Decimal]] = None
    court_ordered_payments: Optional[Attested[Decimal]] = None
    secured_debt_payments: Optional[Attested[Decimal]] = None
    other_necessary: Optional[Attested[Decimal]] = None


class Assets(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cash_and_bank: Attested[Decimal]
    investments: Optional[Attested[Decimal]] = None
    retirement_accounts: Optional[Attested[Decimal]] = None
    life_insurance_cash_value: Optional[Attested[Decimal]] = None
    real_estate: Optional[Attested[list[RealEstate]]] = None
    vehicles: Optional[Attested[list[Vehicle]]] = None
    business_assets: Optional[Attested[Decimal]] = None
    other_assets: Optional[Attested[Decimal]] = None


class Enforcement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enforcement_flags: Attested[list[Literal["levy_wage", "levy_bank", "lien_filed", "seizure_notice", "passport_certified", "none"]]]
    notices_received: Optional[Attested[list[Notice]]] = None


class Circumstances(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prior_3yr_penalty_history: Attested[bool]
    penalty_reason_category: Attested[Literal["none", "illness", "disaster", "records_unavailable", "irs_advice", "professional_reliance", "other"]]
    combat_zone: Attested[bool]
    disaster_zone_resident: Attested[bool]
    hardship_circumstances: Attested[list[Literal["serious_illness", "disability", "elderly", "dependent_special_needs", "natural_disaster", "death_in_family", "unemployment", "none"]]]
    spouse_issues: Attested[list[Literal["spouse_concealed_income", "separated_or_divorced", "abuse", "refund_offset_to_spouse_debt", "none"]]]
    separation_date: Optional[Attested[date]] = None
    first_collection_activity_date: Optional[Attested[date]] = None


class Facts(BaseModel):
    """The full §4.1 input set. Layer-2 groups are Optional; absent → engine stops at Layer 1."""

    model_config = ConfigDict(extra="forbid")
    identity: Identity
    debt: Debt
    compliance: Compliance
    quick_capacity: QuickCapacity
    enforcement: Enforcement
    circumstances: Circumstances
    # Layer 2 (financial disclosure) — optional
    household: Optional[Household] = None
    income: Optional[Income] = None
    expenses: Optional[Expenses] = None
    assets: Optional[Assets] = None


# ---- proposed facts (parser output; never evaluated directly) ----


class ProposedFacts(BaseModel):
    """Parser output: every group optional, provenance carries the real source, attested=False.

    Merged into Facts only after the confirm step flips attested=True (ingest.base.merge).
    """

    model_config = ConfigDict(extra="forbid")
    document_type: Optional[Literal["w2", "1099", "433a", "433b", "notice", "transcript"]] = None
    values: dict = Field(default_factory=dict)  # path -> value/source/ref/attested proposal


# ---- helpers: attestation check, unwrap view, provenance map ----


def check_attested(facts: Facts) -> None:
    """Raise FactsError(path) on the first leaf whose provenance.attested is False."""

    def walk(obj, path: str) -> None:
        if isinstance(obj, Attested):
            if not obj.provenance.attested:
                raise FactsError(f"{path}: value is not attested (source={obj.provenance.source})")
            return
        if isinstance(obj, BaseModel):
            for name in type(obj).model_fields:
                child = getattr(obj, name)
                if child is not None:
                    walk(child, f"{path}.{name}" if path else name)

    walk(facts, "")


def unwrap(facts: Facts) -> SimpleNamespace:
    """Plain read-only view: Attested→value, groups→SimpleNamespace, None groups stay None.

    Derive functions and the expression evaluator read this via the `f.` namespace, so they
    never touch `.value`. Attested.value is returned as-is (list-of-plain-models keep attribute
    access), so `f.debt.tax_periods` is a list of TaxPeriod with `.tax`, `.assessment_date`, etc.
    """

    def conv(obj):
        if isinstance(obj, Attested):
            return obj.value
        if isinstance(obj, BaseModel):
            return SimpleNamespace(**{name: conv(getattr(obj, name)) for name in type(obj).model_fields})
        return obj

    return conv(facts)


def provenance_map(facts: Facts) -> dict[str, dict]:
    """{dotted.path: {value, source, attested}} for every Attested leaf — feeds inputs_summary."""
    out: dict[str, dict] = {}

    def walk(obj, path: str) -> None:
        if isinstance(obj, Attested):
            out[path] = {"value": obj.value, "source": obj.provenance.source, "attested": obj.provenance.attested}
            return
        if isinstance(obj, BaseModel):
            for name in type(obj).model_fields:
                child = getattr(obj, name)
                if child is not None:
                    walk(child, f"{path}.{name}" if path else name)

    walk(facts, "")
    return out
