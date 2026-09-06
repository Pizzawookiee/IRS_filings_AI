from decimal import Decimal

from irsresolve.core.config import load_config
from irsresolve.core.errors import ConfigError
import pytest

CFG = "irsresolve/config"


def test_loads_and_versions():
    c = load_config(CFG)
    assert c.version == "2026-09"


def test_threshold_resolution_is_decimal():
    c = load_config(CFG)
    assert c.cfg_path_exists("simple_plan.individual.max_balance")
    assert c.cfg_get("simple_plan.individual.max_balance") == Decimal(50000)
    # ratio kept exact (loaded via str, not binary float)
    assert c.cfg_get("oic.quick_sale_factor") == Decimal("0.80")
    assert not c.cfg_path_exists("simple_plan.nope")
    with pytest.raises(ConfigError):
        c.cfg_get("does.not.exist")


def test_added_gap_keys_present():
    c = load_config(CFG)
    assert c.cfg_get("ppia.equity_materiality_threshold") == Decimal(10000)
    assert c.cfg_get("oic.retirement_tax_penalty_estimate") == Decimal("0.30")


def test_citation_and_outcome():
    c = load_config(CFG)
    assert c.citation("simple_plan").url.startswith("https://")
    assert c.has_citation("cdp") and not c.has_citation("nope")
    assert c.outcome("PPIA_CANDIDATE")["display_name"] == "Partial Payment Installment Agreement"


def test_standards_lookups():
    c = load_config(CFG)
    amt, ref = c.housing_standard("MA", "worcester", 3)
    assert amt == Decimal(2200) and ref != "state_fallback"
    _, ref2 = c.housing_standard("MA", "nantucket", 3)  # unknown county
    assert ref2 == "state_fallback"
    assert c.food_misc_standard(3) == Decimal(1680)
    assert c.healthcare_standard(40, 3) == Decimal(255)
    assert c.healthcare_standard(70, 3) == Decimal(480)
    assert c.ownership_standard(1, 3) == Decimal(620)
    assert c.operating_standard("northeast") == Decimal(350)
    assert c.poverty_guideline(3) == Decimal(26650)
    assert c.zip_location("01608") == ("MA", "worcester", "northeast")
