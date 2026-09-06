"""Config loader (impl-plan §Config). Loads thresholds/citations/outcomes/poverty YAML and
the standards CSVs, exposes a `cfg` namespace for the evaluator and reason templates, and
resolves dotted keys for parse-time validation. Money values load as Decimal (plan decision
#8) so Decimal facts never meet a float in arithmetic.

Config-not-code: every threshold, URL, display name, form number, and standard lives here.
"""

from __future__ import annotations

import csv
import statistics
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import yaml

from .errors import ConfigError
from .result import Citation

AUTHORITY_ORDER = ["irc", "treas_reg", "irm", "irs_gov", "form_instructions"]


def _to_decimal(x):
    if isinstance(x, bool):
        return x
    if isinstance(x, int):
        return Decimal(x)
    if isinstance(x, float):
        return Decimal(str(x))
    if isinstance(x, dict):
        return {k: _to_decimal(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_to_decimal(v) for v in x]
    return x


def _ns(d):
    if isinstance(d, dict):
        return SimpleNamespace(**{k: _ns(v) for k, v in d.items()})
    return d


class Config:
    def __init__(self, root: Path):
        self.root = root
        raw = yaml.safe_load((root / "thresholds.yaml").read_text())
        self.version = raw.pop("config_version", "unknown")
        self.effective_date = raw.pop("effective_date", None)
        self.disclaimer = (raw.pop("disclaimer", "") or "").strip()
        self._thresholds = _to_decimal(raw)
        self.cfg = _ns(self._thresholds)  # evaluator `cfg.` root

        cites = yaml.safe_load((root / "citations.yaml").read_text()) or {}
        self._citations = {k: Citation(key=k, **v) for k, v in cites.items()}
        self._outcomes = yaml.safe_load((root / "outcomes.yaml").read_text()) or {}
        self._poverty = _to_decimal(yaml.safe_load((root / "poverty.yaml").read_text()) or {})
        self._load_standards(root / "standards")

    # ---- threshold key resolution ----

    def cfg_path_exists(self, dotted: str) -> bool:
        node = self._thresholds
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return False
            node = node[part]
        return True

    def cfg_get(self, dotted: str):
        node = self._thresholds
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                raise ConfigError(f"unknown config key: cfg.{dotted}")
            node = node[part]
        return node

    # ---- citations / outcomes ----

    def citation(self, key: str) -> Citation:
        if key not in self._citations:
            raise ConfigError(f"unknown citation key: {key}")
        return self._citations[key]

    def has_citation(self, key: str) -> bool:
        return key in self._citations

    def outcome(self, code: str) -> dict:
        return self._outcomes.get(code, {})

    # ---- standards ----

    def _load_standards(self, sdir: Path):
        self._national = {}
        for row in csv.DictReader((sdir / "national.csv").read_text().splitlines()):
            self._national[int(row["household_size"])] = {
                "food_clothing_misc": Decimal(row["food_clothing_misc"]),
                "healthcare_under65": Decimal(row["healthcare_under65"]),
                "healthcare_65plus": Decimal(row["healthcare_65plus"]),
                "vehicle_ownership": Decimal(row["vehicle_ownership"]),
            }
        self._housing: dict[tuple[str, str, int], Decimal] = {}
        for row in csv.DictReader((sdir / "local_housing.csv").read_text().splitlines()):
            self._housing[(row["state"].upper(), row["county"].lower(), int(row["household_size"]))] = Decimal(row["amount"])
        self._transport = {}
        for row in csv.DictReader((sdir / "local_transport.csv").read_text().splitlines()):
            self._transport[row["region"].lower()] = Decimal(row["operating"])
        self._zip_map = {}
        for row in csv.DictReader((sdir / "zip_map.csv").read_text().splitlines()):
            self._zip_map[row["zip"]] = (row["state"].upper(), row["county"].lower(), row["region"].lower())

    def zip_location(self, zip_code: str) -> tuple[str | None, str | None, str | None]:
        return self._zip_map.get(zip_code, (None, None, None))

    def housing_standard(self, state: str, county: str | None, size: int) -> tuple[Decimal, str]:
        """(amount, ref). Unknown county → median of that state+size (ref='state_fallback')."""
        state = (state or "").upper()
        if county and (state, county.lower(), size) in self._housing:
            return self._housing[(state, county.lower(), size)], f"{state} {county} housing standard"
        same = [v for (s, _c, sz), v in self._housing.items() if s == state and sz == size]
        if same:
            return Decimal(str(statistics.median(same))), "state_fallback"
        raise ConfigError(f"no housing standard for state={state} size={size}")

    def food_misc_standard(self, size: int) -> Decimal:
        return self._national[self._clamp_size(size)]["food_clothing_misc"]

    def healthcare_standard(self, age: int | None, size: int) -> Decimal:
        row = self._national[self._clamp_size(size)]
        return row["healthcare_65plus"] if (age or 0) >= 65 else row["healthcare_under65"]

    def ownership_standard(self, vehicle_count: int, size: int) -> Decimal:
        return self._national[self._clamp_size(size)]["vehicle_ownership"] * vehicle_count

    def operating_standard(self, region: str | None) -> Decimal:
        if region and region.lower() in self._transport:
            return self._transport[region.lower()]
        # ponytail: unknown region → median regional operating cost; refine if more regions ship
        return Decimal(str(statistics.median(self._transport.values())))

    def poverty_guideline(self, size: int, state_group: str = "contiguous") -> Decimal:
        table = self._poverty.get(state_group) or self._poverty["contiguous"]
        return Decimal(table[self._clamp_size(size)])

    def _clamp_size(self, size: int) -> int:
        sizes = self._national.keys()
        return min(max(size, min(sizes)), max(sizes))


def load_config(path: str | Path) -> Config:
    return Config(Path(path))
