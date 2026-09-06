from decimal import Decimal

import pytest

from irsresolve.core.facts import check_attested, provenance_map
from irsresolve.core.intake import QuestionnaireDraft, TaxPeriodDraft, build_facts


def _draft(taxpayer_type="individual"):
    return QuestionnaireDraft(
        taxpayer_type=taxpayer_type,
        business_entity_type="llc" if taxpayer_type == "business" else None,
        has_employees=False if taxpayer_type == "business" else None,
        tax_periods=[TaxPeriodDraft(year=2025, tax=Decimal("12000"))],
    )


def _financial_values():
    return {
        "household.household_size": {"value": 2, "source": "433a", "ref": "section seven"},
        "household.zip_code": {"value": "10001", "source": "433a", "ref": "address"},
        "household.state": {"value": "NY", "source": "433a", "ref": "address"},
        "income.monthly_gross_income": {"value": 5000, "source": "433a", "ref": "income"},
        "expenses.housing_utilities": {"value": 1800, "source": "433a", "ref": "expenses"},
        "expenses.food_clothing_misc": {"value": 900, "source": "433a", "ref": "expenses"},
        "expenses.transportation_ownership": {"value": 400, "source": "433a", "ref": "expenses"},
        "expenses.transportation_operating": {"value": 300, "source": "433a", "ref": "expenses"},
        "expenses.healthcare_out_of_pocket": {"value": 200, "source": "433a", "ref": "expenses"},
        "expenses.taxes_withheld_or_estimated": {"value": 800, "source": "433a", "ref": "expenses"},
        "assets.cash_and_bank": {"value": 1000, "source": "433a", "ref": "assets"},
    }


@pytest.mark.parametrize(
    ("taxpayer_type", "required"),
    [("individual", "433a"), ("self_employed", "433a"), ("business", "433b")],
)
def test_required_financial_document_depends_on_taxpayer_type(taxpayer_type, required):
    assert _draft(taxpayer_type).required_financial_document == required


def test_build_facts_is_the_explicit_attestation_boundary():
    facts = build_facts(_draft(), _financial_values())
    check_attested(facts)
    sources = provenance_map(facts)
    assert sources["income.monthly_gross_income"]["source"] == "433a"
    assert sources["income.monthly_gross_income"]["attested"] is True
    assert sources["debt.tax_periods"]["source"] == "user"


def test_build_facts_rejects_incomplete_financial_confirmation():
    values = _financial_values()
    del values["assets.cash_and_bank"]
    with pytest.raises(ValueError, match="assets.cash_and_bank"):
        build_facts(_draft(), values)
