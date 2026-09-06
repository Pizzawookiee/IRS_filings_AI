import copy

import pytest

from irsresolve.core.errors import FactsError
from irsresolve.core.facts import Facts, check_attested, provenance_map, unwrap

MINIMAL = {
    "identity": {"taxpayer_type": "individual", "age": 45},
    "debt": {
        "tax_periods": [
            {"year": 2022, "tax_type": "income_1040", "tax": 19000, "penalty": 2500, "interest": 1500,
             "assessment_date": "2023-01-01"}
        ],
        "liability_disputed": False,
    },
    "compliance": {"returns_filed_all": True, "bankruptcy_status": "none"},
    "quick_capacity": {"can_full_pay_180_days": False, "proposed_monthly_payment": 350,
                        "prior_5yr_compliance": False, "prior_ia_in_5yrs": False},
    "enforcement": {"enforcement_flags": ["none"]},
    "circumstances": {"prior_3yr_penalty_history": True, "penalty_reason_category": "none",
                      "combat_zone": False, "disaster_zone_resident": False,
                      "hardship_circumstances": ["none"], "spouse_issues": ["none"]},
}


def test_bare_scalars_autowrap_and_attest():
    f = Facts.model_validate(MINIMAL)
    check_attested(f)  # no raise
    v = unwrap(f)
    assert v.identity.taxpayer_type == "individual"
    assert v.debt.tax_periods[0].tax == 19000  # Decimal-comparable
    assert v.household is None  # Layer 2 absent


def test_provenance_map_keys_leaves():
    f = Facts.model_validate(MINIMAL)
    pm = provenance_map(f)
    assert pm["identity.taxpayer_type"]["source"] == "user"
    assert pm["identity.taxpayer_type"]["attested"] is True


def test_unattested_leaf_rejected_with_path():
    bad = copy.deepcopy(MINIMAL)
    bad["identity"]["taxpayer_type"] = {"value": "individual", "provenance": {"source": "user", "attested": False}}
    f = Facts.model_validate(bad)
    with pytest.raises(FactsError) as e:
        check_attested(f)
    assert "identity.taxpayer_type" in str(e.value)
