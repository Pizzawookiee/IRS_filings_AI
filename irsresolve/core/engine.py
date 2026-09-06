"""The engine (impl-plan §Engine). A generic layered evaluator over data-defined rules — it is
the only module with control flow across layers. Ten steps: attestation check, derive, L0 gate +
dispute, L1 tiers, Layer-2 disclosure gate, L2 capacity, L3 flags, penalty-abatement rerun,
excluded assembly, finalize with trace. Deterministic: same facts + config → same result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .config import Config
from .derive import DerivedFacts, derive_all
from .expr import eval_expr, eval_when, render_template, resolved_repr
from .facts import Facts, check_attested, provenance_map, unwrap
from .result import Citation, Determination, EligibilityResult, RuleTrace, Scenario
from .rules import CompiledRule, in_layer

ENGINE_VERSION = "1.1"
L2_GROUPS = ("household", "income", "expenses", "assets")


@dataclass
class _Selection:
    blocker: CompiledRule | None = None
    primary: CompiledRule | None = None
    l1_terminal: bool = False
    reached_l2: bool = False
    nfd: bool = False
    missing: list[str] = field(default_factory=list)
    secondary: list[CompiledRule] = field(default_factory=list)
    dispute: list[CompiledRule] = field(default_factory=list)


class Engine:
    def __init__(self, config: Config, rules: list[CompiledRule]):
        self.cfg = config
        self.rules = rules

    # ---- public ----

    def evaluate(self, facts: Facts, as_of=None) -> EligibilityResult:
        from datetime import date
        as_of = as_of or date.today()
        check_attested(facts)                 # step 1
        f = unwrap(facts)
        d = derive_all(f, self.cfg, as_of)    # step 2
        trace: list[RuleTrace] = []

        sel = self._run_layers(f, d, trace)   # steps 3–6

        if sel.nfd:
            primary = self._nfd_determination(sel.missing)
        else:
            primary = self._det(sel.primary, sel.primary.rule.outcome, f, d, is_primary=True)
        d.primary_outcome = primary.outcome

        l3 = [cr for cr in in_layer(self.rules, "L3") if self._match(cr, f, d, trace)]  # step 7

        urgent: list[Determination] = []
        alternatives: list[Determination] = []
        stacked: list[Determination] = []
        referrals: list[dict[str, Any]] = []

        emit_from = list(sel.dispute) + list(sel.secondary) + list(l3)
        if sel.blocker:  # blocker flags (e.g. BANKRUPTCY_DISCHARGE_POSSIBLE) still surface
            emit_from.append(sel.blocker)
        for cr in emit_from:
            for det in self._emit(cr, f, d, skip_outcome=(cr is sel.blocker)):
                if cr.rule.priority == "P0":
                    urgent.append(det)
                elif cr.rule.outcome and cr.rule.layer == "L2_secondary" and det.outcome == cr.rule.outcome:
                    alternatives.append(det)
                else:
                    stacked.append(det)
            if cr.rule.referral:
                referrals.append({"type": "professional", "reason": self._referral_reason(cr, f, d)})
        if primary.professional_referral_recommended:  # the primary path itself may need a pro
            referrals.insert(0, {"type": "professional", "reason": primary.referral_reason or primary.reason})

        scenario = self._rerun(f, d, as_of, primary) if (not sel.nfd and any(cr.rule.rerun_with for cr in l3)) else None
        presented = ({primary.outcome} | {x.outcome for x in alternatives}
                     | {x.outcome for x in stacked} | {x.outcome for x in urgent})
        excluded = [] if (sel.blocker or sel.nfd) else self._excluded(f, d, sel, presented)

        urgent.sort(key=lambda det: det.priority)  # P0 first
        return EligibilityResult(
            generated_at=datetime.now(timezone.utc).isoformat(),
            engine_version=ENGINE_VERSION,
            config_version=self.cfg.version,
            inputs_summary=self._inputs_summary(facts, d),
            urgent=urgent,
            primary=primary,
            alternatives=alternatives,
            stacked_relief=stacked,
            excluded=excluded,
            scenario_if_penalties_abated=scenario,
            referrals=referrals,
            disclaimer=self.cfg.disclaimer,
            trace=trace,
        )

    # ---- layer selection (steps 3–6); trace is appended for every evaluated rule ----

    def _run_layers(self, f, d: DerivedFacts, trace: list[RuleTrace]) -> _Selection:
        sel = _Selection()
        for cr in in_layer(self.rules, "L0_gate"):
            if self._match(cr, f, d, trace) and (cr.rule.outcome or "").startswith("BLOCKER"):
                sel.blocker = cr
                break
        sel.dispute = [cr for cr in in_layer(self.rules, "L0_dispute") if self._match(cr, f, d, trace)]
        if sel.blocker:
            sel.primary = sel.blocker
            return sel
        for cr in in_layer(self.rules, "L1"):
            if self._match(cr, f, d, trace):
                sel.primary = cr
                sel.l1_terminal = cr.rule.next == "stop"
                return sel
        sel.missing = [g for g in L2_GROUPS if getattr(f, g) is None]
        if sel.missing:
            sel.nfd = True
            return sel
        sel.reached_l2 = True
        for cr in in_layer(self.rules, "L2_primary"):
            if self._match(cr, f, d, trace):
                sel.primary = cr
                break
        sel.secondary = [cr for cr in in_layer(self.rules, "L2_secondary") if self._match(cr, f, d, trace)]
        return sel

    def _match(self, cr: CompiledRule, f, d: DerivedFacts, trace: list[RuleTrace]) -> bool:
        matched = eval_when(cr.when, cr.rule.id, f, d, self.cfg)
        try:
            when_resolved = resolved_repr(cr.when, f, d, self.cfg)
        except Exception:  # noqa: BLE001
            when_resolved = cr.rule.when
        trace.append(RuleTrace(rule_id=cr.rule.id, layer=cr.rule.layer, matched=matched, when_resolved=when_resolved))
        return matched

    # ---- determination builders ----

    def _det(self, cr: CompiledRule, outcome: str, f, d: DerivedFacts, is_primary=False) -> Determination:
        meta = self.cfg.outcome(outcome)
        reason = render_template(cr.rule.reason, f, d, self.cfg) if cr.rule.reason else (meta.get("what_happens_next") or "")
        extras = {k: _jsonify(eval_expr(tree, f, d, self.cfg)) for k, tree in cr.extras.items()}
        referral = cr.rule.referral
        return Determination(
            outcome=outcome,
            display_name=meta.get("display_name", outcome),
            variant=cr.rule.variant if is_primary else None,
            reason=reason,
            rule_id=cr.rule.id,
            inputs_relied_on=cr.rule.uses,
            citations=self._citations(cr.rule.citations),
            professional_referral_recommended=referral,
            referral_reason=(self._referral_reason(cr, f, d) if referral else None),
            forms=meta.get("forms", []),
            what_happens_next=meta.get("what_happens_next"),
            priority=cr.rule.priority,
            extras=extras,
        )

    def _emit(self, cr: CompiledRule, f, d: DerivedFacts, skip_outcome=False) -> list[Determination]:
        dets = []
        if cr.rule.outcome and not skip_outcome:
            dets.append(self._det(cr, cr.rule.outcome, f, d))
        for flag in cr.rule.flags:
            dets.append(self._det(cr, flag, f, d))
        return dets

    def _nfd_determination(self, missing: list[str]) -> Determination:
        meta = self.cfg.outcome("NEEDS_FINANCIAL_DISCLOSURE")
        return Determination(
            outcome="NEEDS_FINANCIAL_DISCLOSURE",
            display_name=meta.get("display_name", "More information needed"),
            reason=meta.get("what_happens_next", ""),
            citations=self._citations(["collection_standards"]),
            forms=meta.get("forms", []),
            what_happens_next=meta.get("what_happens_next"),
            extras={"missing_groups": missing},
        )

    def _referral_reason(self, cr: CompiledRule, f, d: DerivedFacts) -> str:
        for code in ([cr.rule.outcome] + cr.rule.flags):
            rr = self.cfg.outcome(code or "").get("referral_reason")
            if rr:
                return rr
        return render_template(cr.rule.reason, f, d, self.cfg) if cr.rule.reason else ""

    def _citations(self, keys: list[str]) -> list[Citation]:
        return [self.cfg.citation(k) for k in keys]

    # ---- excluded (step 9) ----

    def _excluded(self, f, d: DerivedFacts, sel: _Selection, presented: set[str]) -> list[Determination]:
        layers = ["L1"] + (["L2_primary"] if sel.reached_l2 else [])
        out: list[Determination] = []
        seen: set[str] = set(presented)
        for layer in layers:
            for cr in in_layer(self.rules, layer):
                oc = cr.rule.outcome
                if not oc or oc in seen or cr is sel.primary:
                    continue
                seen.add(oc)
                meta = self.cfg.outcome(oc)
                reason = render_template(cr.rule.reason_if_excluded, f, d, self.cfg) if cr.rule.reason_if_excluded else ""
                out.append(Determination(
                    outcome=oc, display_name=meta.get("display_name", oc), reason=reason,
                    rule_id=cr.rule.id, citations=self._citations(cr.rule.citations),
                ))
        return out

    # ---- penalty-abatement rerun (step 8) ----

    def _rerun(self, f, d: DerivedFacts, as_of, primary: Determination) -> Scenario:
        d2 = d.model_copy(update={"total_debt": d.total_debt_if_penalties_abated})
        sel2 = self._run_layers(f, d2, [])
        new_outcome = "NEEDS_FINANCIAL_DISCLOSURE" if sel2.nfd else sel2.primary.rule.outcome
        return Scenario(
            total_debt=float(d.total_debt_if_penalties_abated),
            primary=new_outcome,
            changed=(new_outcome != primary.outcome),
        )

    # ---- inputs summary (step 10) ----

    def _inputs_summary(self, facts: Facts, d: DerivedFacts) -> dict[str, Any]:
        summary = {k: _jsonify(v) for k, v in d.model_dump().items() if v is not None}
        summary["provenance"] = {k: {**v, "value": _jsonify(v["value"])} for k, v in provenance_map(facts).items()}
        return summary


def _jsonify(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, list):
        return [_jsonify(x) for x in v]
    return v
