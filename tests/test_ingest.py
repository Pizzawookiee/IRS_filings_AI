import json
from decimal import Decimal
from pathlib import Path

from irsresolve.core.facts import ProposedFacts
from irsresolve.ingest.base import merge
from irsresolve.ingest.fixtures import F433AFixture, F1099Fixture, NoticeFixture, W2Fixture


def test_1099_never_populates_expenses():
    p = F1099Fixture().parse("fixtures/docs/case_b_1099.json")
    assert not any(k.startswith("expenses.") for k in p.values)  # gross, not net
    assert p.values["income.monthly_gross_income"]["value"] == Decimal(37200) / 12
    assert p.values["identity.taxpayer_type"]["value"] == "self_employed"
    assert all(pv.get("source") in ("1099",) for pv in p.values.values())


def test_w2_maps_wages_and_withholding_unattested():
    p = W2Fixture().parse("fixtures/docs/case_d_w2.json")
    assert p.values["income.monthly_gross_income"]["value"] == Decimal(58800) / 12
    assert "expenses.taxes_withheld_or_estimated" in p.values
    # proposals carry source but are not yet attested (that happens at confirm/merge)
    assert p.values["income.monthly_gross_income"]["source"] == "w2"


def test_433a_populates_layer2_and_notice_parses():
    p = F433AFixture().parse("fixtures/docs/case_b_433a.json")
    assert p.values["household.household_size"]["value"] == 3
    assert "assets.cash_and_bank" in p.values and "expenses.housing_utilities" in p.values
    n = NoticeFixture().parse("fixtures/docs/case_d_notice.json")
    assert n.values["enforcement.notices_received"]["value"][0]["type"] == "L1058"


def test_merge_attests_only_accepted_proposals():
    base = json.loads(Path("fixtures/case_a_simple_plan.json").read_text())["facts"]
    proposed = ProposedFacts(values={
        "identity.age": {"value": 51, "source": "433a", "ref": "§1"},
        "circumstances.combat_zone": {"value": True, "source": "433a", "ref": "§1"},  # will be dropped
    })
    facts = merge(base, proposed, accepted_paths={"identity.age"})
    assert facts.identity.age.value == 51
    assert facts.identity.age.provenance.attested is True
    assert facts.identity.age.provenance.source == "433a"
    # unaccepted proposal dropped → base value retained (attested user answer)
    assert facts.circumstances.combat_zone.value is False
