"""Stub parsers (impl-plan: JSON-in only, no OCR). Each reads a JSON extraction shaped like the
real document and maps it to canonical paths with provenance per architecture §4.4. Real OCR is a
post-MVP `DocumentParser` implementation — the data shapes here are identical.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from ..core.facts import ProposedFacts


def _read(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def _p(value, source, ref) -> dict:
    return {"value": value, "source": source, "ref": ref}


class W2Fixture:
    """W-2 → wages (Box 1 ÷ 12) and withheld taxes (Boxes 2+4+6+17+19 ÷ 12). Annual → monthly."""

    def parse(self, path: str | Path) -> ProposedFacts:
        d = _read(path)
        wages = Decimal(str(d["box1"])) / 12
        taxes = sum(Decimal(str(d.get(b, 0))) for b in ("box2", "box4", "box6", "box17", "box19")) / 12
        return ProposedFacts(values={
            "income.monthly_gross_income": _p(wages, "w2", "Box 1 ÷ 12"),
            "expenses.taxes_withheld_or_estimated": _p(taxes, "w2", "Boxes 2/4/6/17/19 ÷ 12"),
        })


class F1099Fixture:
    """1099 → self-employment income (total ÷ 12). Gross, not net — NEVER touches expenses."""

    def parse(self, path: str | Path) -> ProposedFacts:
        d = _read(path)
        monthly = Decimal(str(d["total"])) / 12
        return ProposedFacts(values={
            "identity.taxpayer_type": _p("self_employed", "1099", d.get("payer", "1099 payer")),
            "income.monthly_gross_income": _p(monthly, "1099", "Total ÷ 12"),
        })


class F433AFixture:
    """Form 433-A → the full Layer 2 field set (household, income, assets, expenses)."""

    def parse(self, path: str | Path) -> ProposedFacts:
        d = _read(path)
        vals: dict[str, dict] = {}
        s1 = d.get("section1", {})
        for k in ("household_size", "zip_code", "state"):
            if k in s1:
                vals[f"household.{k}"] = _p(s1[k], "433a", "§1")
        for k, v in d.get("section3", {}).items():  # assets
            vals[f"assets.{k}"] = _p(v, "433a", "§3")
        for k, v in d.get("section5", {}).items():  # expenses
            vals[f"expenses.{k}"] = _p(Decimal(str(v)), "433a", "§5")
        return ProposedFacts(values=vals)


class NoticeFixture:
    """IRS notice → notices_received[] (type + date). The notice date drives the CDP window."""

    def parse(self, path: str | Path) -> ProposedFacts:
        d = _read(path)
        return ProposedFacts(values={
            "enforcement.notices_received": _p([{"type": d["type"], "date": d["date"]}], "notice", d["type"]),
        })
