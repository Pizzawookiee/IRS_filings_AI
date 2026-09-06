"""EligibilityResult → Markdown (impl-plan §Render). Urgent first, then the primary path, its
reasoning and IRS citations, alternatives, stacked relief, the penalty-abated scenario, the
"Why not…" exclusions, professional-referral notices, and the disclaimer. Referral outcomes get a
recommendation to consult a pro and never a "file now" call to action.
"""

from __future__ import annotations

from ..core.result import Citation, Determination, EligibilityResult


def _money(x) -> str:
    return f"${float(x):,.0f}"


def _cites(cites: list[Citation]) -> str:
    if not cites:
        return ""
    return "  \nSources: " + " · ".join(f"[{c.source}]({c.url}) _{c.authority}_" for c in cites)


def _extras(det: Determination) -> list[str]:
    e = det.extras
    out = []
    if "days_remaining" in e:
        out.append(f"- **{int(float(e['days_remaining']))} days remaining**")
    if "monthly_payment" in e:
        out.append(f"- Estimated monthly payment: {_money(e['monthly_payment'])}")
    if "offer_floor_lump" in e:
        out.append(f"- Offer floor: {_money(e['offer_floor_lump'])} lump sum / "
                   f"{_money(e['offer_floor_periodic'])} periodic")
    return out


def _det_block(det: Determination, heading_level: int = 3) -> list[str]:
    h = "#" * heading_level
    lines = [f"{h} {det.display_name}", "", det.reason.strip()]
    lines += _extras(det)
    if det.professional_referral_recommended:
        lines.append(f"- 👤 **Consult a professional.** {det.referral_reason or ''}".rstrip())
    if det.forms:
        lines.append(f"- Forms: {', '.join(det.forms)}")
    if det.what_happens_next:
        lines.append(f"- What happens next: {det.what_happens_next}")
    if det.citations:
        lines.append(_cites(det.citations).strip())
    lines.append("")
    return lines


def to_markdown(result: EligibilityResult, show_trace: bool = False) -> str:
    out: list[str] = []

    if result.urgent:
        out.append("# ⚠️ Urgent — act now")
        out.append("")
        for u in result.urgent:
            out += _det_block(u, heading_level=2)

    out.append(f"# Recommended: {result.primary.display_name}")
    out.append("")
    out += _det_block(result.primary, heading_level=2)[1:]  # drop duplicate heading

    if result.alternatives:
        out.append("# Alternatives and trade-offs")
        out.append("")
        for a in result.alternatives:
            out += _det_block(a)

    if result.stacked_relief:
        out.append("# Additional relief you may stack")
        out.append("")
        for s in result.stacked_relief:
            out += _det_block(s)

    sc = result.scenario_if_penalties_abated
    if sc:
        change = (f"this changes your recommended path to **{sc.primary}**." if sc.changed
                  else f"your recommended path is unchanged (**{sc.primary}**).")
        out.append(f"**If eligible penalties are removed,** your balance drops to {_money(sc.total_debt)} — {change}")
        out.append("")

    if result.excluded:
        out.append("# Why not other options?")
        out.append("")
        for e in result.excluded:
            out.append(f"- **{e.display_name}** — {e.reason.strip()}{_cites(e.citations)}")
        out.append("")

    if result.referrals:
        out.append("# Professional help recommended")
        out.append("")
        for r in result.referrals:
            out.append(f"- {r.get('reason', '')}")
        out.append("")

    out.append("---")
    out.append(f"_{result.disclaimer}_")

    if show_trace and result.trace:
        out.append("")
        out.append("<details><summary>Why? (rule trace)</summary>")
        out.append("")
        for t in result.trace:
            mark = "✓" if t.matched else "✗"
            out.append(f"- `{mark}` **{t.rule_id}** ({t.layer}): {t.when_resolved}")
        out.append("")
        out.append("</details>")

    return "\n".join(out)
