"""Derived facts (architecture §4.2). Pure functions over the unwrapped fact view + config.

No inference: every value here is computed by a named formula from attested facts. Layer-2
derivations return None when their fact group is absent, so Layer-1-terminal cases never touch
household/expense/asset math. `as_of` is passed in (never `date.today()` inside) so results are
reproducible (plan decision #7).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from pydantic import BaseModel, ConfigDict

from .config import Config

ZERO = Decimal(0)


def _d(x) -> Decimal:
    """Coalesce an optional Decimal to 0."""
    return x if x is not None else ZERO


def _state_group(state: str | None) -> str:
    return {"AK": "alaska", "HI": "hawaii"}.get((state or "").upper(), "contiguous")


# ---- debt (Layer 0/1, always present) ----

def total_debt(f) -> Decimal:
    """§4.2: sum(tax + penalty + interest) over periods."""
    return sum((p.tax + p.penalty + p.interest for p in f.debt.tax_periods), ZERO)


def tax_only_balance(f) -> Decimal:
    return sum((p.tax for p in f.debt.tax_periods), ZERO)


def penalty_only_balance(f) -> Decimal:
    return sum((p.penalty for p in f.debt.tax_periods), ZERO)


def includes_trust_fund_tax(f) -> bool:
    """§4.2: trust-fund payroll tax present (941)."""
    return any(p.tax_type == "payroll_941" for p in f.debt.tax_periods)


# ---- CSED (§4.2 / §15) ----

def _months_between(start: date, end: date) -> int:
    """Whole months from start to end, floored (can be negative if end is past)."""
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return months


def _csed_date(p, years: int) -> date:
    """Assessment (best) or return-filed (§9 fallback) + 10 years."""
    base = p.assessment_date or p.return_filed_date or p.return_due_date
    return date(base.year + years, base.month, base.day)


def csed_months_remaining(f, cfg: Config, as_of: date) -> int:
    """§4.2: min over periods of months(today → csed_date) — conservative."""
    years = int(cfg.cfg.csed.years)
    return min(_months_between(as_of, _csed_date(p, years)) for p in f.debt.tax_periods)


def csed_confidence(f) -> str:
    """§9 data-quality: low when any period lacks an assessment date (CSED estimated from filing)."""
    return "low" if any(p.assessment_date is None for p in f.debt.tax_periods) else "high"


# ---- allowable expenses (Layer 2) ----

def _vehicle_count(f) -> int:
    a = f.assets
    return len(a.vehicles) if (a and a.vehicles) else 0


def allowable_expenses_total(f, cfg: Config) -> Decimal | None:
    """§4.2: standards-capped allowable living expenses. None without household+expenses."""
    if f.household is None or f.expenses is None:
        return None
    h, e = f.household, f.expenses
    size = h.household_size
    _state, county, region = cfg.zip_location(h.zip_code)
    housing_std, _ref = cfg.housing_standard(h.state, county, size)
    age = f.identity.age

    allowable_housing = min(e.housing_utilities, housing_std)
    allowable_food = cfg.food_misc_standard(size)  # full standard regardless of actual
    allowable_own = min(e.transportation_ownership, cfg.ownership_standard(_vehicle_count(f), size))
    allowable_op = min(e.transportation_operating, cfg.operating_standard(region))
    allowable_health = max(e.healthcare_out_of_pocket, cfg.healthcare_standard(age, size))
    allowable_other = (
        e.taxes_withheld_or_estimated + _d(e.childcare) + _d(e.court_ordered_payments)
        + _d(e.health_insurance) + _d(e.secured_debt_payments) + _d(e.other_necessary)
    )
    return allowable_housing + allowable_food + allowable_own + allowable_op + allowable_health + allowable_other


def disposable_income(f, allowable: Decimal | None) -> Decimal | None:
    """§4.2: monthly_gross_income − allowable_expenses_total."""
    if f.income is None or allowable is None:
        return None
    return f.income.monthly_gross_income - allowable


# ---- net realizable equity (Layer 2) ----

def nre_total(f, cfg: Config) -> Decimal | None:
    """§4.2: quick-sale equity. The 0.80 quick-sale factor applies ONLY to real estate and
    vehicles; retirement/cash/investments/business are counted at (near) full value."""
    a = f.assets
    if a is None:
        return None
    qsf = cfg.cfg.oic.quick_sale_factor
    exemption = cfg.cfg.vehicle_exemption
    re_equity = sum((max(r.fmv * qsf - r.mortgage_balance, ZERO) for r in (a.real_estate or [])), ZERO)
    veh_equity = sum((max(v.fmv * qsf - v.loan_balance - exemption, ZERO) for v in (a.vehicles or [])), ZERO)
    retirement = _d(a.retirement_accounts) * (1 - cfg.cfg.oic.retirement_tax_penalty_estimate)
    cash = max(a.cash_and_bank - cfg.cfg.oic.cash_exemption, ZERO)
    return (re_equity + veh_equity + retirement + cash
            + _d(a.investments) + _d(a.life_insurance_cash_value) + _d(a.business_assets) + _d(a.other_assets))


# ---- collection potential (Layer 2) ----

def full_pay_capacity(disp: Decimal | None, months: int, nre: Decimal | None) -> Decimal | None:
    """§4.2: (disposable_income × csed_months_remaining) + nre_total."""
    if disp is None or nre is None:
        return None
    return disp * months + nre


def rcp_lump_sum(disp: Decimal | None, nre: Decimal | None, cfg: Config) -> Decimal | None:
    if disp is None or nre is None:
        return None
    return nre + max(disp, ZERO) * cfg.cfg.oic.lump_sum_months


def rcp_periodic(disp: Decimal | None, nre: Decimal | None, cfg: Config) -> Decimal | None:
    if disp is None or nre is None:
        return None
    return nre + max(disp, ZERO) * cfg.cfg.oic.periodic_months


# ---- poverty test (Layer 2) ----

def low_income_certified(f, cfg: Config) -> bool | None:
    """§4.2: annual income ≤ poverty guideline × 2.5."""
    if f.income is None or f.household is None:
        return None
    annual = f.income.monthly_gross_income * 12
    guideline = cfg.poverty_guideline(f.household.household_size, _state_group(f.household.state))
    return annual <= guideline * cfg.cfg.oic.low_income_poverty_multiplier


# ---- CDP timing (§4.2) ----

_CDP_TYPES = {"LT11", "L1058", "L3172"}


def days_since_cdp_notice(f, as_of: date) -> int | None:
    """§4.2: days since the most recent levy/lien notice that starts the CDP clock."""
    notices = (f.enforcement.notices_received or [])
    dates = [n.date for n in notices if n.type in _CDP_TYPES]
    if not dates:
        return None
    return (as_of - max(dates)).days


def bankruptcy_dischargeable_possible(f, cfg: Config, as_of: date) -> bool:
    """§3.14 "3-2-240" timing test on any non-SFR income-tax period (informational only)."""
    three = int(cfg.cfg.bankruptcy.three_year_rule_days)
    two = int(cfg.cfg.bankruptcy.two_year_rule_days)
    assess = int(cfg.cfg.bankruptcy.assessment_rule_days)
    for p in f.debt.tax_periods:
        if p.assessed_via_sfr or not (p.return_due_date and p.return_filed_date and p.assessment_date):
            continue
        if ((as_of - p.return_due_date).days > three
                and (as_of - p.return_filed_date).days > two
                and (as_of - p.assessment_date).days > assess):
            return True
    return False


# ---- assembly ----

class DerivedFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)
    total_debt: Decimal
    tax_only_balance: Decimal
    penalty_only_balance: Decimal
    total_debt_if_penalties_abated: Decimal
    includes_trust_fund_tax: bool
    csed_months_remaining: int
    csed_confidence: str
    csed_imminent: bool
    allowable_expenses_total: Decimal | None = None
    disposable_income: Decimal | None = None
    nre_total: Decimal | None = None
    full_pay_capacity: Decimal | None = None
    rcp_lump_sum: Decimal | None = None
    rcp_periodic: Decimal | None = None
    rcp_min: Decimal | None = None
    low_income_certified: bool | None = None
    days_since_cdp_notice: int | None = None
    cdp_window_open: bool = False
    equivalent_hearing_open: bool = False
    bankruptcy_dischargeable_possible: bool = False
    primary_outcome: str | None = None  # engine-set before L3 (plan gap #5)


def derive_all(f: SimpleNamespace, cfg: Config, as_of: date) -> DerivedFacts:
    td = total_debt(f)
    pen = penalty_only_balance(f)
    months = csed_months_remaining(f, cfg, as_of)
    allowable = allowable_expenses_total(f, cfg)
    disp = disposable_income(f, allowable)
    nre = nre_total(f, cfg)
    rlump = rcp_lump_sum(disp, nre, cfg)
    rper = rcp_periodic(disp, nre, cfg)
    dscdp = days_since_cdp_notice(f, as_of)
    window = int(cfg.cfg.cdp.window_days)
    equiv = int(cfg.cfg.cdp.equivalent_hearing_days)
    return DerivedFacts(
        total_debt=td,
        tax_only_balance=tax_only_balance(f),
        penalty_only_balance=pen,
        total_debt_if_penalties_abated=td - pen,
        includes_trust_fund_tax=includes_trust_fund_tax(f),
        csed_months_remaining=months,
        csed_confidence=csed_confidence(f),
        csed_imminent=months < 24,
        allowable_expenses_total=allowable,
        disposable_income=disp,
        nre_total=nre,
        full_pay_capacity=full_pay_capacity(disp, months, nre),
        rcp_lump_sum=rlump,
        rcp_periodic=rper,
        rcp_min=(min(rlump, rper) if rlump is not None else None),
        low_income_certified=low_income_certified(f, cfg),
        days_since_cdp_notice=dscdp,
        cdp_window_open=(dscdp is not None and dscdp <= window),
        equivalent_hearing_open=(dscdp is not None and window < dscdp <= equiv),
        bankruptcy_dischargeable_possible=bankruptcy_dischargeable_possible(f, cfg, as_of),
    )
