"""IRS Resolve demo (impl-plan §Demo, UI design doc). Three-page shell — Questions / Intake /
Analysis — over the real engine. In-memory session state only; no backend.

The UI doc's shell is three pages; the load-example buttons are the one-click path through a full
case. Manual entry here is intentionally minimal (segmentation + confirm-with-source-badges) — the
fixtures stand in for a fully typed questionnaire, exactly as the impl-plan describes. No threshold,
URL, display name, or form number is written here; everything shown comes from config via the engine.

Run:  streamlit run irsresolve/demo/app.py
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import streamlit as st

from irsresolve.core.config import load_config
from irsresolve.core.engine import Engine
from irsresolve.core.expr import Validator
from irsresolve.core.facts import Facts, provenance_map
from irsresolve.core.rules import load_rules
from irsresolve.ingest.fixtures import F433AFixture, F1099Fixture, NoticeFixture, W2Fixture
from irsresolve.ingest.openrouter import OpenRouterDocumentParser

CSS = """
<style>
  .block-container {padding-top: 2.5rem; max-width: 1100px;}
  section[data-testid="stSidebar"] {background: #0f172a;}
  section[data-testid="stSidebar"] * {color: #e2e8f0;}
  section[data-testid="stSidebar"] .stButton button {
    background:#1e293b; border:1px solid #334155; color:#e2e8f0; text-align:left;}
  section[data-testid="stSidebar"] .stButton button:hover {border-color:#2563eb;}
  div[data-testid="stMetricValue"] {font-size: 1.15rem;}
</style>
"""

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
EXAMPLES = {
    "A — Simple Payment Plan": "case_a_simple_plan.json",
    "B — CNC + OIC": "case_b_cnc_oic.json",
    "C — Business blocked": "case_c_business_blocked.json",
    "D — PPIA + CDP + FTA": "case_d_ppia_cdp.json",
}
PARSERS = {"w2": W2Fixture, "1099": F1099Fixture, "433a": F433AFixture, "notice": NoticeFixture}


@st.cache_resource
def get_engine():
    cfg = load_config(str(ROOT / "irsresolve" / "config"))
    rules = load_rules(str(ROOT / "irsresolve" / "rules"), Validator(cfg), cfg)
    return cfg, Engine(cfg, rules)


def _ss():
    st.session_state.setdefault("facts", None)   # dict
    st.session_state.setdefault("as_of", date(2026, 9, 6).isoformat())
    st.session_state.setdefault("proposed", {})   # path -> {value, source, ref}
    st.session_state.setdefault("page", "Questions")


def _load_example(fname: str):
    data = json.loads((FIXTURES / fname).read_text())
    st.session_state.facts = data["facts"]
    st.session_state.as_of = data.get("as_of", date.today().isoformat())
    st.session_state.proposed = {}
    st.session_state.page = "Analysis"


def extract_uploaded_document(data: bytes, filename: str, media_type: str, parser=None):
    """Small testable boundary between Streamlit uploads and the OpenRouter parser."""
    return (parser or OpenRouterDocumentParser()).parse_bytes(data, filename, media_type)


# ---- pages ----

def page_questions():
    st.header("Questions")
    st.caption("Branching fact-collection and attestation. Documents propose facts; only you attest them.")
    facts = st.session_state.facts

    st.subheader("Who owes the IRS?")
    current = facts["identity"]["taxpayer_type"] if facts else "individual"
    if isinstance(current, dict):
        current = current["value"]
    choice = st.radio("Taxpayer type", ["individual", "self_employed", "business"],
                      index=["individual", "self_employed", "business"].index(current), horizontal=True)
    if facts:
        facts["identity"]["taxpayer_type"] = choice

    if not facts:
        st.info("Load an example from the sidebar to populate the rest of the case, then confirm below.")
        return

    st.subheader("Confirm your data")
    st.caption("Each value shows its source. Only attested values reach the engine.")
    try:
        pm = provenance_map(Facts.model_validate(facts))
    except Exception as e:  # noqa: BLE001
        st.error(f"Facts invalid: {e}")
        return
    for path, meta in list(pm.items())[:40]:
        c1, c2, c3 = st.columns([3, 2, 2])
        c1.write(f"`{path}`")
        c2.write(str(meta["value"]))
        badge = {"user": "You", "w2": "W-2", "1099": "1099", "433a": "Form 433-A",
                 "notice": "IRS notice", "transcript": "Transcript",
                 "irs_standard_prefill": "IRS standard"}.get(meta["source"], meta["source"])
        c3.markdown(f"**{badge}** {'✅' if meta['attested'] else '⚠️'}")

    if st.button("View Analysis →", type="primary"):
        st.session_state.page = "Analysis"
        st.rerun()


def page_intake():
    st.header("Intake")
    st.caption("Upload documents. We classify and extract proposed values — you confirm them on Questions.")
    facts = st.session_state.facts
    ttype = "individual"
    if facts and isinstance(facts["identity"]["taxpayer_type"], (str, dict)):
        ttype = facts["identity"]["taxpayer_type"]
        ttype = ttype["value"] if isinstance(ttype, dict) else ttype

    st.subheader("Required documents")
    st.write("**1099 (self-employment income)**" if ttype in ("self_employed", "business")
             else "**W-2 (payroll wages)**")
    st.subheader("Optional, high-value")
    st.write("Form 433-A — *optional, saves time* · IRS notice · Account transcript")

    st.subheader("Upload a document")
    st.warning(
        "Tax documents contain sensitive personal and financial data. Files uploaded here are "
        "sent to OpenRouter and its selected inference provider for extraction. Extracted values "
        "remain unconfirmed until you review them."
    )
    uploads = st.file_uploader(
        "PDF, PNG, JPEG, or WebP (20 MB maximum per file)",
        type=["pdf", "png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
    )
    if st.button("Extract proposed values", disabled=not uploads, type="primary"):
        for upload in uploads or []:
            try:
                with st.spinner(f"Classifying and extracting {upload.name}…"):
                    proposed = extract_uploaded_document(
                        upload.getvalue(), upload.name, upload.type or "application/octet-stream"
                    )
                st.session_state.proposed.update(proposed.values)
                st.success(
                    f"{upload.name}: {proposed.document_type} — "
                    f"{len(proposed.values)} proposed values ready to review"
                )
                st.json({k: str(v["value"]) for k, v in proposed.values.items()})
            except Exception as e:  # noqa: BLE001 — UI boundary must keep manual workflow available
                st.error(f"{upload.name}: extraction failed — {e}")

    st.subheader("Offline demo extraction")
    docs = sorted(p.name for p in (FIXTURES / "docs").glob("*.json"))
    pick = st.selectbox("Sample document", ["—"] + docs)
    if pick != "—":
        kind = next((k for k in PARSERS if pick.endswith(f"_{k}.json")), None)
        if kind:
            proposed = PARSERS[kind]().parse(FIXTURES / "docs" / pick)
            st.session_state.proposed.update(proposed.values)
            st.success(f"{len(proposed.values)} proposed values found (not yet confirmed)")
            st.json({k: str(v["value"]) for k, v in proposed.values.items()})
    if st.session_state.proposed:
        st.info(
            f"{len(st.session_state.proposed)} proposed values pending review. They have not "
            "changed the eligibility analysis."
        )
    if st.button("Review questions →"):
        st.session_state.page = "Questions"
        st.rerun()


def _money(x) -> str:
    return f"${float(x):,.0f}"


def _cites(cites) -> str:
    return "Sources: " + " · ".join(f"[{c.source}]({c.url})" for c in cites) if cites else ""


def _chips(result):
    s = result.inputs_summary
    prov = s.get("provenance", {})
    ttype = prov.get("identity.taxpayer_type", {}).get("value", "—")
    status = ("🔴 Urgent" if result.urgent
              else "🟠 Blocked" if result.primary.outcome.startswith("BLOCKER")
              else "🟢 Ready")
    c = st.columns(4)
    c[0].metric("Taxpayer", str(ttype).replace("_", " ").title())
    c[1].metric("Total debt", _money(s.get("total_debt", 0)))
    c[2].metric("Statute left", f"{s.get('csed_months_remaining', '—')} mo")
    c[3].metric("Status", status)


def _card(det):
    with st.container(border=True):
        st.markdown(f"#### {det.display_name}")
        if det.reason:
            st.write(det.reason.strip())
        e = det.extras
        if "monthly_payment" in e:
            st.markdown(f"**Estimated monthly payment:** {_money(e['monthly_payment'])}")
        if "offer_floor_lump" in e:
            st.markdown(f"**Offer floor:** {_money(e['offer_floor_lump'])} lump / "
                        f"{_money(e['offer_floor_periodic'])} periodic")
        if "days_remaining" in e:
            st.markdown(f"**{int(float(e['days_remaining']))} days remaining**")
        if det.professional_referral_recommended:
            st.warning(f"👤 **Consult a professional.** {det.referral_reason or ''}".rstrip())
        meta = " · ".join(filter(None, [
            ("Forms: " + ", ".join(det.forms)) if det.forms else "",
            det.what_happens_next or "",
        ]))
        if meta:
            st.caption(meta)
        if det.citations:
            st.caption(_cites(det.citations))


def page_analysis():
    st.header("Analysis")
    facts = st.session_state.facts
    if not facts:
        st.info("No case yet. Load an example from the sidebar, or complete Questions and Intake.")
        return
    try:
        model = Facts.model_validate(facts)
    except Exception as e:  # noqa: BLE001
        st.error(f"More information needed before analysis: {e}")
        return
    _cfg, engine = get_engine()
    try:
        result = engine.evaluate(model, as_of=date.fromisoformat(st.session_state.as_of))
    except Exception as e:  # noqa: BLE001
        st.warning(f"Cannot analyze yet: {e}")
        return

    _chips(result)
    st.divider()

    if result.primary.outcome == "NEEDS_FINANCIAL_DISCLOSURE":
        st.warning("**More information needed** — answer the household, income, expense, and asset "
                   f"questions. Missing: {', '.join(result.primary.extras.get('missing_groups', []))}")
        return

    # Urgent first — outrank the ordinary recommendation.
    for u in result.urgent:
        st.error(f"⚠️ **{u.display_name}** — {u.reason.strip()}")
        _card(u)

    if result.primary.outcome.startswith("BLOCKER"):
        st.error(f"🚧 **{result.primary.display_name}**")
        _card(result.primary)
    else:
        st.success(f"✅ **Recommended: {result.primary.display_name}**")
        _card(result.primary)

    if result.alternatives:
        st.subheader("Alternatives & trade-offs")
        for a in result.alternatives:
            _card(a)

    if result.stacked_relief:
        st.subheader("Additional relief you may stack")
        for s in result.stacked_relief:
            _card(s)

    sc = result.scenario_if_penalties_abated
    if sc:
        change = (f"changes your path to **{sc.primary}**." if sc.changed
                  else f"leaves your path unchanged (**{sc.primary}**).")
        st.info(f"**If eligible penalties are removed,** your balance drops to {_money(sc.total_debt)} — this {change}")

    if result.excluded:
        with st.expander("Why not other options?"):
            for x in result.excluded:
                st.markdown(f"- **{x.display_name}** — {x.reason.strip()}  \n  {_cites(x.citations)}")

    for r in result.referrals:
        st.warning(f"👤 {r.get('reason', '')}")

    st.caption(result.disclaimer)
    with st.expander("Why? (rule trace)"):
        for t in result.trace:
            st.markdown(f"`{'✓' if t.matched else '✗'}` **{t.rule_id}** ({t.layer}) — {t.when_resolved}")


def main():
    st.set_page_config(page_title="IRS Resolve", page_icon="🧾", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    _ss()
    with st.sidebar:
        st.title("IRS Resolve")
        st.session_state.page = st.radio(
            "Navigation",
            ["Questions", "Intake", "Analysis"],
            index=["Questions", "Intake", "Analysis"].index(st.session_state.page),
            label_visibility="collapsed",
        )
        st.divider()
        st.caption("Load an example")
        for label, fname in EXAMPLES.items():
            if st.button(label, use_container_width=True):
                _load_example(fname)
                st.rerun()

    {"Questions": page_questions, "Intake": page_intake, "Analysis": page_analysis}[st.session_state.page]()


if __name__ == "__main__":
    main()
