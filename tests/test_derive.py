"""Derive unit tests. Expected values are hand-computed here (not read from the code path)."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from irsresolve.core.config import load_config
from irsresolve.core.derive import derive_all
from irsresolve.core.errors import FactsError
from irsresolve.core.facts import Facts, unwrap

CFG = load_config("irsresolve/config")
AS_OF = date(2026, 9, 6)


def _derive(fixture):
    data = json.loads(Path(f"fixtures/{fixture}").read_text())
    f = unwrap(Facts.model_validate(data["facts"]))
    return derive_all(f, CFG, AS_OF)


def test_case_a_debt_and_csed_layer1_only():
    d = _derive("case_a_simple_plan.json")
    assert d.total_debt == Decimal(23000)
    assert d.tax_only_balance == Decimal(19000)
    assert d.penalty_only_balance == Decimal(2500)
    assert d.total_debt_if_penalties_abated == Decimal(20500)
    # assessment 2023-01-01 + 10y = 2033-01-01; from 2026-09-06 → 75 whole months
    assert d.csed_months_remaining == 75
    assert d.csed_confidence == "high"
    # Layer 2 absent → all None
    assert d.disposable_income is None and d.nre_total is None and d.rcp_min is None


def test_missing_csed_reference_date_is_a_clear_facts_error():
    data = json.loads(Path("fixtures/case_a_simple_plan.json").read_text())
    period = data["facts"]["debt"]["tax_periods"][0]
    period["assessment_date"] = None
    period["return_filed_date"] = None
    period["return_due_date"] = None

    with pytest.raises(FactsError, match="Tax period 2022 needs an assessment"):
        derive_all(unwrap(Facts.model_validate(data["facts"])), CFG, AS_OF)


def test_case_b_disposable_and_zero_nre():
    d = _derive("case_b_cnc_oic.json")
    assert d.total_debt == Decimal(41000)
    # allowable: housing 900 + food 1680 + own 200 + op 165 + healthcare 255 + taxes 200 = 3400
    assert d.allowable_expenses_total == Decimal(3400)
    assert d.disposable_income == Decimal(-300)
    # cash 800 → max(800-1000,0)=0; vehicle 6000*0.8-7000 <0 → 0
    assert d.nre_total == Decimal(0)
    assert d.rcp_lump_sum == Decimal(0) and d.rcp_periodic == Decimal(0) and d.rcp_min == Decimal(0)
    # 37,200 <= 26,650 * 2.5 = 66,625
    assert d.low_income_certified is True


def test_case_d_full_capacity_and_rcp():
    d = _derive("case_d_ppia_cdp.json")
    assert d.total_debt == Decimal(68400)
    assert d.total_debt_if_penalties_abated == Decimal(61300)
    assert d.csed_months_remaining == 74
    assert d.csed_confidence == "low"  # no assessment_date → estimated from filing
    # allowable 1850+1450(food size2)+400+300+170(hc size2 under65)+320 = 4490
    assert d.allowable_expenses_total == Decimal(4490)
    assert d.disposable_income == Decimal(410)
    # retirement 4000*0.70=2800 + cash max(1400-1000,0)=400 + vehicle 0 = 3200
    assert d.nre_total == Decimal(3200)
    assert d.full_pay_capacity == Decimal(33540)   # 410*74 + 3200
    assert d.rcp_lump_sum == Decimal(8120)         # 3200 + 410*12
    assert d.rcp_periodic == Decimal(13040)        # 3200 + 410*24
    assert d.rcp_min == Decimal(8120)
    assert d.low_income_certified is False          # 58,800 > 21,150*2.5
    # Letter 1058 dated 2026-08-18, as_of 2026-09-06 → 19 days
    assert d.days_since_cdp_notice == 19
    assert d.cdp_window_open is True


def test_quick_sale_factor_only_on_re_and_vehicles():
    """Retirement/cash/investments are NOT reduced by the 0.80 quick-sale factor."""
    facts = {
        "identity": {"taxpayer_type": "individual", "age": 45},
        "debt": {"tax_periods": [{"year": 2022, "tax_type": "income_1040", "tax": 50000,
                 "return_filed_date": "2023-01-01", "return_due_date": "2023-04-15"}],
                 "liability_disputed": False},
        "compliance": {"returns_filed_all": True, "bankruptcy_status": "none"},
        "quick_capacity": {"can_full_pay_180_days": False, "proposed_monthly_payment": 500,
                           "prior_5yr_compliance": False, "prior_ia_in_5yrs": False},
        "household": {"household_size": 1, "zip_code": "02118", "state": "MA"},
        "income": {"monthly_gross_income": 2000},
        "expenses": {"housing_utilities": 1000, "food_clothing_misc": 0, "transportation_ownership": 0,
                     "transportation_operating": 0, "healthcare_out_of_pocket": 0,
                     "taxes_withheld_or_estimated": 0},
        "assets": {"cash_and_bank": 1000, "investments": 5000, "retirement_accounts": 10000,
                   "real_estate": [{"fmv": 100000, "mortgage_balance": 60000, "is_primary_residence": True}]},
        "enforcement": {"enforcement_flags": ["none"]},
        "circumstances": {"prior_3yr_penalty_history": True, "penalty_reason_category": "none",
                          "combat_zone": False, "disaster_zone_resident": False,
                          "hardship_circumstances": ["none"], "spouse_issues": ["none"]},
    }
    d = derive_all(unwrap(Facts.model_validate(facts)), CFG, AS_OF)
    # RE: 100000*0.8 - 60000 = 20000 (quick-sale applied)
    # cash: max(1000-1000,0)=0; investments 5000 full; retirement 10000*0.7=7000 (no quick-sale)
    assert d.nre_total == Decimal(20000) + Decimal(0) + Decimal(7000) + Decimal(5000)
