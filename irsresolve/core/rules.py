"""Rule model + loader (impl-plan §Rules). Rules are data; the engine is a generic evaluator.

Adding a program means adding a YAML rule, never editing Python. Load time validates every
`when`/reason/extra expression (paths, whitelist) and every citation key, and requires a
`reason_if_excluded` on each L1/L2_primary outcome rule (so exclusions can always explain why).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from .config import Config
from .errors import RuleLoadError
from .expr import Validator, compile_when, validate_template

Layer = Literal["L0_gate", "L0_dispute", "L1", "L2_primary", "L2_secondary", "L3"]
LAYER_ORDER = ["L0_gate", "L0_dispute", "L1", "L2_primary", "L2_secondary", "L3"]
FIRST_MATCH = {"L0_gate", "L1", "L2_primary"}
OUTCOME_LAYERS = {"L1", "L2_primary"}  # these emit `excluded` entries when not matched

# Deterministic file load order; within a file, list order is preserved (drives first-match).
RULE_FILES = ["l0_gate.yaml", "l1_tiers.yaml", "l2_capacity.yaml", "l3_flags.yaml"]


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    layer: Layer
    when: str
    outcome: str | None = None
    flags: list[str] = []
    variant: str | None = None
    referral: bool = False
    priority: Literal["P0", "P1", "P2"] = "P2"
    reason: str = ""
    reason_if_excluded: str | None = None
    citations: list[str] = []
    uses: list[str] = []
    rerun_with: str | None = None
    next: str | None = None            # "stop" on L1 terminal rules
    extras: dict[str, str] = {}        # extra_key -> expression, evaluated at fire time


@dataclass
class CompiledRule:
    rule: Rule
    when: ast.Expression
    extras: dict[str, ast.Expression] = field(default_factory=dict)


def load_rules(rules_dir: str | Path, validator: Validator, config: Config) -> list[CompiledRule]:
    rules_dir = Path(rules_dir)
    seen_ids: set[str] = set()
    compiled: list[CompiledRule] = []
    for fname in RULE_FILES:
        path = rules_dir / fname
        if not path.exists():
            continue
        for raw in yaml.safe_load(path.read_text()) or []:
            rule = Rule.model_validate(raw)
            if rule.id in seen_ids:
                raise RuleLoadError(f"duplicate rule id: {rule.id}")
            seen_ids.add(rule.id)
            when = compile_when(rule.when, rule.id, validator)
            extras = {k: compile_when(v, f"{rule.id}.extras.{k}", validator) for k, v in rule.extras.items()}
            validate_template(rule.reason, rule.id, validator)
            if rule.reason_if_excluded:
                validate_template(rule.reason_if_excluded, rule.id, validator)
            if not rule.citations:
                raise RuleLoadError(f"{rule.id}: every rule needs at least one citation")
            for c in rule.citations:
                if not config.has_citation(c):
                    raise RuleLoadError(f"{rule.id}: unknown citation key '{c}'")
            if rule.layer in OUTCOME_LAYERS and rule.outcome and not rule.reason_if_excluded:
                raise RuleLoadError(f"{rule.id}: outcome rule needs reason_if_excluded")
            compiled.append(CompiledRule(rule=rule, when=when, extras=extras))
    return compiled


def in_layer(rules: list[CompiledRule], layer: str) -> list[CompiledRule]:
    return [cr for cr in rules if cr.rule.layer == layer]
