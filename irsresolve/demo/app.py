"""Questions-first IRS Resolve screening flow.

The questionnaire and document confirmation build attested facts for the deterministic engine.
Sonnet may explain that engine result, but it cannot choose or change an eligibility outcome.
All session data and uploaded document bytes remain in memory only.

Run:  streamlit run irsresolve/demo/app.py
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import streamlit as st

from irsresolve.core.config import load_config
from irsresolve.core.engine import Engine
from irsresolve.core.expr import Validator
from irsresolve.core.facts import Facts
from irsresolve.core.intake import QuestionnaireDraft, TaxPeriodDraft, build_facts, questionnaire_base
from irsresolve.core.rules import load_rules
from irsresolve.analysis.openrouter import OpenRouterAnalysisSynthesizer, SynthesisResult
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
    st.session_state.setdefault("draft", QuestionnaireDraft().model_dump(mode="json"))
    st.session_state.setdefault("question_step", 0)
    st.session_state.setdefault("questions_complete", False)
    st.session_state.setdefault("documents", [])
    st.session_state.setdefault("documents_complete", False)
    st.session_state.setdefault("synthesis", None)
    st.session_state.setdefault("synthesis_signature", None)
    st.session_state.setdefault("page", "Questions")


def _load_example(fname: str):
    data = json.loads((FIXTURES / fname).read_text())
    st.session_state.facts = data["facts"]
    st.session_state.as_of = data.get("as_of", date.today().isoformat())
    st.session_state.proposed = {}
    st.session_state.questions_complete = True
    st.session_state.documents_complete = True
    st.session_state.synthesis = None
    st.session_state.synthesis_signature = None
    st.session_state.page = "Analysis"


def _start_over() -> None:
    for key in (
        "facts", "proposed", "draft", "question_step", "questions_complete", "documents",
        "documents_complete", "synthesis", "synthesis_signature",
    ):
        st.session_state.pop(key, None)
    _ss()
    st.session_state.page = "Questions"


def extract_uploaded_document(data: bytes, filename: str, media_type: str, parser=None):
    """Small testable boundary between Streamlit uploads and the OpenRouter parser."""
    return (parser or OpenRouterDocumentParser()).parse_bytes(data, filename, media_type)


def _draft() -> QuestionnaireDraft:
    return QuestionnaireDraft.model_validate(st.session_state.draft)


def _save_draft(draft: QuestionnaireDraft) -> None:
    st.session_state.draft = draft.model_dump(mode="json")
    st.session_state.facts = None
    st.session_state.documents_complete = False
    st.session_state.synthesis = None
    st.session_state.synthesis_signature = None


def _advance_questions(draft: QuestionnaireDraft) -> None:
    _save_draft(draft)
    if st.session_state.question_step < 3:
        st.session_state.question_step += 1
    else:
        st.session_state.questions_complete = True
        st.session_state.page = "Documents"
    st.rerun()


def _back_question() -> None:
    st.session_state.question_step = max(0, st.session_state.question_step - 1)
    st.rerun()


# ---- pages ----

def page_questions():
    st.header("Questions")
    st.caption("A short, four-step intake. You can return and change any answer before analysis.")
    draft = _draft()
    step = st.session_state.question_step
    labels = ["Profile", "Debt & compliance", "Payment & dispute", "Urgent issues & relief"]
    st.progress((step + 1) / len(labels), text=f"Step {step + 1} of {len(labels)} — {labels[step]}")

    if step == 0:
        with st.form("question_profile"):
            taxpayer_type = st.radio(
                "Who owes the IRS?",
                ["individual", "self_employed", "business"],
                index=["individual", "self_employed", "business"].index(draft.taxpayer_type),
                format_func=lambda x: x.replace("_", " ").title(),
                horizontal=True,
            )
            age = st.number_input("Your age", min_value=18, max_value=120, value=draft.age)
            business_entity_type = draft.business_entity_type
            has_employees = draft.has_employees
            if taxpayer_type == "business":
                entities = ["sole_prop", "partnership", "s_corp", "c_corp", "llc"]
                business_entity_type = st.selectbox(
                    "Business type", entities,
                    index=entities.index(draft.business_entity_type or "llc"),
                    format_func=lambda x: x.replace("_", " ").upper(),
                )
                has_employees = st.checkbox("The business has employees", value=bool(draft.has_employees))
            submitted = st.form_submit_button("Continue →", type="primary")
        if submitted:
            _advance_questions(draft.model_copy(update={
                "taxpayer_type": taxpayer_type,
                "age": age,
                "business_entity_type": business_entity_type if taxpayer_type == "business" else None,
                "has_employees": has_employees if taxpayer_type == "business" else None,
            }))

    elif step == 1:
        with st.form("question_debt"):
            st.subheader("What do you owe?")
            count = st.number_input("Number of tax periods", 1, 6, len(draft.tax_periods))
            periods: list[TaxPeriodDraft] = []
            tax_types = ["income_1040", "income_1120", "payroll_941", "payroll_940", "excise", "other"]
            for i in range(int(count)):
                old = draft.tax_periods[i] if i < len(draft.tax_periods) else TaxPeriodDraft()
                st.markdown(f"**Tax period {i + 1}**")
                c1, c2 = st.columns(2)
                year = c1.number_input("Year", 1990, date.today().year, old.year, key=f"period_year_{i}")
                tax_type = c2.selectbox(
                    "Tax type", tax_types, index=tax_types.index(old.tax_type), key=f"period_type_{i}"
                )
                c3, c4, c5 = st.columns(3)
                tax = c3.number_input("Tax owed", 0.0, value=float(old.tax), step=100.0, key=f"period_tax_{i}")
                penalty = c4.number_input("Penalties", 0.0, value=float(old.penalty), step=100.0, key=f"period_penalty_{i}")
                interest = c5.number_input("Interest", 0.0, value=float(old.interest), step=100.0, key=f"period_interest_{i}")
                filed_jointly = st.checkbox("Filed jointly", old.filed_jointly, key=f"period_joint_{i}")
                periods.append(TaxPeriodDraft(
                    year=year, tax_type=tax_type, tax=tax, penalty=penalty,
                    interest=interest, filed_jointly=filed_jointly,
                ))
            returns_filed_all = st.checkbox("All required tax returns are filed", draft.returns_filed_all)
            unfiled_years_text = ""
            if not returns_filed_all:
                unfiled_years_text = st.text_input(
                    "Unfiled tax years (comma-separated)",
                    ", ".join(str(year) for year in (draft.unfiled_years or [])),
                )
            current_paid = draft.current_year_estimated_paid
            ftd_current = draft.ftd_current
            if draft.taxpayer_type == "self_employed":
                current_paid = st.checkbox("Current-year estimated payments are up to date", bool(current_paid))
            if draft.taxpayer_type == "business":
                ftd_current = st.checkbox("Federal tax deposits are current", bool(ftd_current))
            bankruptcy_options = ["none", "open_ch7", "open_ch13", "discharged", "dismissed"]
            bankruptcy = st.selectbox(
                "Bankruptcy status", bankruptcy_options,
                index=bankruptcy_options.index(draft.bankruptcy_status),
                format_func=lambda x: x.replace("_", " ").title(),
            )
            submitted = st.form_submit_button("Continue →", type="primary")
        if st.button("← Back", key="back_debt"):
            _back_question()
        if submitted:
            try:
                unfiled_years = (
                    [int(value.strip()) for value in unfiled_years_text.split(",") if value.strip()]
                    if not returns_filed_all else None
                )
            except ValueError:
                st.error("Enter unfiled years as four-digit years separated by commas.")
                return
            if not any(period.tax + period.penalty + period.interest > 0 for period in periods):
                st.error("Enter an amount for at least one tax period.")
            elif not returns_filed_all and not unfiled_years:
                st.error("List at least one unfiled tax year.")
            else:
                _advance_questions(draft.model_copy(update={
                    "tax_periods": periods,
                    "returns_filed_all": returns_filed_all,
                    "unfiled_years": unfiled_years,
                    "current_year_estimated_paid": current_paid,
                    "ftd_current": ftd_current,
                    "bankruptcy_status": bankruptcy,
                }))

    elif step == 2:
        with st.form("question_capacity"):
            liability_disputed = st.checkbox("I disagree with some or all of the tax owed", draft.liability_disputed)
            dispute_reason = draft.dispute_reason
            prior_appeal = draft.prior_appeal_or_court
            if liability_disputed:
                dispute_reason = st.text_input("Brief reason for the dispute", draft.dispute_reason or "")
                prior_appeal = st.checkbox("I already had an appeal or court opportunity", bool(prior_appeal))
            can_pay = st.checkbox("I could pay the full balance within about six months", draft.can_full_pay_180_days)
            monthly = st.number_input(
                "Realistic monthly payment", min_value=0.0,
                value=float(draft.proposed_monthly_payment), step=50.0,
            )
            prior_compliance = st.checkbox("I met filing and payment obligations during the prior five years", draft.prior_5yr_compliance)
            prior_ia = st.checkbox("I had an IRS installment agreement during the prior five years", draft.prior_ia_in_5yrs)
            submitted = st.form_submit_button("Continue →", type="primary")
        if st.button("← Back", key="back_capacity"):
            _back_question()
        if submitted:
            _advance_questions(draft.model_copy(update={
                "liability_disputed": liability_disputed,
                "dispute_reason": dispute_reason if liability_disputed else None,
                "prior_appeal_or_court": prior_appeal if liability_disputed else None,
                "can_full_pay_180_days": can_pay,
                "proposed_monthly_payment": monthly,
                "prior_5yr_compliance": prior_compliance,
                "prior_ia_in_5yrs": prior_ia,
            }))

    else:
        with st.form("question_relief"):
            enforcement_options = ["levy_wage", "levy_bank", "lien_filed", "seizure_notice", "passport_certified"]
            enforcement = st.multiselect(
                "Active IRS collection actions", enforcement_options,
                default=[x for x in draft.enforcement_flags if x != "none"],
                format_func=lambda x: x.replace("_", " ").title(),
            )
            notice_types = ["none", "CP14", "CP501", "CP503", "CP504", "LT11", "L1058", "L3172", "other"]
            old_notice = draft.notices_received[0] if draft.notices_received else None
            notice_type = st.selectbox(
                "Most recent IRS notice", notice_types,
                index=notice_types.index(old_notice["type"] if old_notice else "none"),
            )
            notice_date = st.date_input(
                "Notice date", value=date.fromisoformat(old_notice["date"]) if old_notice else date.today(),
                disabled=notice_type == "none",
            )
            penalty_history = st.checkbox("Penalties were assessed in the prior three years", draft.prior_3yr_penalty_history)
            penalty_reasons = ["none", "illness", "disaster", "records_unavailable", "irs_advice", "professional_reliance", "other"]
            penalty_reason = st.selectbox(
                "Reason penalties may deserve relief", penalty_reasons,
                index=penalty_reasons.index(draft.penalty_reason_category),
                format_func=lambda x: x.replace("_", " ").title(),
            )
            hardships = ["serious_illness", "disability", "elderly", "dependent_special_needs", "natural_disaster", "death_in_family", "unemployment"]
            hardship = st.multiselect(
                "Current hardship circumstances", hardships,
                default=[x for x in draft.hardship_circumstances if x != "none"],
                format_func=lambda x: x.replace("_", " ").title(),
            )
            spouse_options = ["spouse_concealed_income", "separated_or_divorced", "abuse", "refund_offset_to_spouse_debt"]
            spouse = st.multiselect(
                "Spouse-related concerns", spouse_options,
                default=[x for x in draft.spouse_issues if x != "none"],
                format_func=lambda x: x.replace("_", " ").title(),
            )
            combat = st.checkbox("I served in a qualifying combat zone", draft.combat_zone)
            disaster = st.checkbox("I live in a federally declared disaster area", draft.disaster_zone_resident)
            submitted = st.form_submit_button("Continue to Documents →", type="primary")
        if st.button("← Back", key="back_relief"):
            _back_question()
        if submitted:
            notices = [{"type": notice_type, "date": notice_date.isoformat()}] if notice_type != "none" else None
            _advance_questions(draft.model_copy(update={
                "enforcement_flags": enforcement or ["none"],
                "notices_received": notices,
                "prior_3yr_penalty_history": penalty_history,
                "penalty_reason_category": penalty_reason,
                "hardship_circumstances": hardship or ["none"],
                "spouse_issues": spouse or ["none"],
                "combat_zone": combat,
                "disaster_zone_resident": disaster,
            }))


def _json_editor_value(value) -> str:
    return json.dumps(value, default=str)


def _parse_editor_value(raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _values_equal(left, right) -> bool:
    if not isinstance(left, bool) and not isinstance(right, bool):
        try:
            return Decimal(str(left)) == Decimal(str(right))
        except (InvalidOperation, ValueError):
            pass
    return _json_editor_value(left) == _json_editor_value(right)


def _proposal_default(path: str, default):
    proposal = st.session_state.proposed.get(path)
    return proposal["value"] if proposal else default


def _path_value(tree: dict, path: str):
    current = tree
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _rebuild_proposals() -> None:
    rebuilt = {}
    for record in st.session_state.documents:
        if record.get("status") == "ready":
            rebuilt.update(record.get("proposals", {}))
    st.session_state.proposed = rebuilt


def _render_document_review(draft: QuestionnaireDraft) -> None:
    st.subheader("Review and confirm")
    st.caption("Nothing extracted by the model is used until you confirm it here.")
    proposals = st.session_state.proposed
    questionnaire = questionnaire_base(draft)
    with st.form("document_review"):
        edited: list[tuple[str, dict, bool, str]] = []
        for index, (path, proposal) in enumerate(sorted(proposals.items())):
            c1, c2 = st.columns([1, 3])
            accept = c1.checkbox("Use", value=True, key=f"accept_{index}_{path}")
            raw = c2.text_input(
                path,
                value=_json_editor_value(proposal["value"]),
                help=f"Source: {proposal.get('source')} · {proposal.get('ref') or 'reference unavailable'}",
                key=f"proposal_{index}_{path}",
            )
            asked_value = _path_value(questionnaire, path)
            if asked_value is not None and not _values_equal(asked_value, proposal["value"]):
                c2.warning(f"Your questionnaire answer was {_json_editor_value(asked_value)}. Confirm which value is correct.")
            edited.append((path, proposal, accept, raw))

        st.markdown("#### Required financial facts")
        st.caption("Complete any values the financial statement did not contain clearly.")
        c1, c2, c3 = st.columns(3)
        household_size = c1.number_input("Household size", 1, 20, int(_proposal_default("household.household_size", 1)))
        zip_code = c2.text_input("ZIP code", str(_proposal_default("household.zip_code", "")))
        state = c3.text_input("State abbreviation", str(_proposal_default("household.state", ""))).upper()
        monthly_income = st.number_input(
            "Monthly gross household income", 0.0,
            value=float(_proposal_default("income.monthly_gross_income", 0)), step=100.0,
        )
        required_money = [
            ("expenses.housing_utilities", "Housing and utilities"),
            ("expenses.food_clothing_misc", "Food, clothing, and household supplies"),
            ("expenses.transportation_ownership", "Vehicle ownership or lease"),
            ("expenses.transportation_operating", "Transportation operating costs"),
            ("expenses.healthcare_out_of_pocket", "Out-of-pocket healthcare"),
            ("expenses.taxes_withheld_or_estimated", "Taxes withheld or estimated payments"),
            ("assets.cash_and_bank", "Cash and bank accounts"),
        ]
        money_values = {}
        for path, label in required_money:
            money_values[path] = st.number_input(
                label, 0.0, value=float(_proposal_default(path, 0)), step=50.0, key=f"required_{path}"
            )
        confirmed = st.form_submit_button("Confirm and analyze →", type="primary")

    if confirmed:
        if not zip_code.strip() or not state.strip():
            st.error("ZIP code and state are required before analysis.")
            return
        accepted: dict[str, dict] = {}
        for path, proposal, use, raw in edited:
            if not use:
                continue
            value = _parse_editor_value(raw)
            edited_by_user = not _values_equal(value, proposal["value"])
            accepted[path] = {
                "value": value,
                "source": "user" if edited_by_user else proposal.get("source", "user"),
                "ref": "edited during review" if edited_by_user else proposal.get("ref"),
            }
        manual = {
            "household.household_size": household_size,
            "household.zip_code": zip_code,
            "household.state": state,
            "income.monthly_gross_income": monthly_income,
            **money_values,
        }
        for path, value in manual.items():
            proposal = proposals.get(path)
            edited_by_user = proposal is None or not _values_equal(value, proposal["value"])
            accepted[path] = {
                "value": value,
                "source": "user" if edited_by_user else proposal.get("source", "user"),
                "ref": "entered during review" if edited_by_user else proposal.get("ref"),
            }
        try:
            facts = build_facts(draft, accepted)
        except Exception as exc:  # noqa: BLE001
            st.error(f"More information is needed before analysis: {exc}")
            return
        st.session_state.facts = facts.model_dump(mode="json")
        st.session_state.documents_complete = True
        st.session_state.synthesis = None
        st.session_state.synthesis_signature = None
        st.session_state.page = "Analysis"
        st.rerun()


def page_documents():
    st.header("Documents")
    if not st.session_state.questions_complete:
        st.warning("Complete the short questionnaire before uploading documents.")
        if st.button("Return to Questions"):
            st.session_state.page = "Questions"
            st.rerun()
        return
    draft = _draft()
    required_type = draft.required_financial_document
    required_label = "Form 433-B" if required_type == "433b" else "Form 433-A"
    st.caption("Your financial statement is required. Other documents are optional and can improve accuracy.")
    st.subheader("Required")
    st.write(f"**{required_label}** — upload a completed financial statement.")
    st.subheader("Optional")
    st.write("W-2 · 1099 · IRS notices · Account transcript")

    st.subheader("Upload documents")
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
                st.session_state.documents = [
                    record for record in st.session_state.documents if record["filename"] != upload.name
                ] + [{
                    "filename": upload.name,
                    "status": "ready",
                    "detected_type": proposed.document_type,
                    "confirmed_type": proposed.document_type,
                    "count": len(proposed.values),
                    "proposals": proposed.model_dump(mode="json")["values"],
                }]
                _rebuild_proposals()
                st.success(
                    f"{upload.name}: {proposed.document_type} — "
                    f"{len(proposed.values)} proposed values ready to review"
                )
                st.json({k: str(v["value"]) for k, v in proposed.values.items()})
            except Exception as e:  # noqa: BLE001 — UI boundary must keep manual workflow available
                st.error(f"{upload.name}: extraction failed — {e}")
                st.session_state.documents = [
                    record for record in st.session_state.documents if record["filename"] != upload.name
                ] + [{"filename": upload.name, "status": "failed", "error": str(e)}]

    if st.session_state.documents:
        st.subheader("Uploaded files")
        type_options = ["w2", "1099", "433a", "433b", "notice", "transcript"]
        proposals_changed = False
        for index, record in enumerate(st.session_state.documents):
            with st.container(border=True):
                st.write(f"**{record['filename']}** — {record['status'].replace('_', ' ').title()}")
                if record["status"] == "ready":
                    selected = st.selectbox(
                        "Document type", type_options,
                        index=type_options.index(record["confirmed_type"]),
                        key=f"document_type_{index}",
                    )
                    if selected != record["confirmed_type"]:
                        record["confirmed_type"] = selected
                        for proposal in record.get("proposals", {}).values():
                            proposal["source"] = selected
                        proposals_changed = True
                    st.caption(f"{record['count']} proposed values")
                elif record.get("error"):
                    st.caption(record["error"])
        if proposals_changed:
            _rebuild_proposals()

    required_ready = any(
        record.get("status") == "ready" and record.get("confirmed_type") == required_type
        for record in st.session_state.documents
    )
    if not required_ready:
        st.info(f"Upload and successfully extract {required_label} to continue.")
    else:
        _render_document_review(draft)


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


def _analysis_signature(facts: Facts, result) -> str:
    payload = {
        "facts": facts.model_dump(mode="json"),
        "result": result.model_dump(mode="json", exclude={"generated_at"}),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _get_synthesis(facts: Facts, result) -> SynthesisResult | None:
    signature = _analysis_signature(facts, result)
    cached = st.session_state.synthesis
    if st.session_state.synthesis_signature != signature:
        st.session_state.synthesis = None
        st.session_state.synthesis_signature = signature
        try:
            with st.spinner("Sonnet is organizing your confirmed facts and screening result…"):
                synthesis = OpenRouterAnalysisSynthesizer().synthesize(facts, result)
            st.session_state.synthesis = {"data": synthesis.model_dump(mode="json")}
        except Exception as exc:  # noqa: BLE001 — deterministic result remains available
            st.session_state.synthesis = {"error": str(exc)}
        cached = st.session_state.synthesis
    if cached and cached.get("data"):
        return SynthesisResult.model_validate(cached["data"])
    if cached and cached.get("error"):
        st.info(
            "Personalized Sonnet summary is unavailable, so the verified deterministic "
            f"screening is shown below. {cached['error']}"
        )
    return None


def _render_synthesis(synthesis: SynthesisResult) -> None:
    st.subheader("Personalized overview")
    st.write(synthesis.situation_summary)
    st.caption(f"Summary confidence: {synthesis.confidence.title()}")
    with st.container(border=True):
        st.markdown(f"**{synthesis.primary.summary}**")
        st.write(synthesis.primary.why)
    if synthesis.uncertainties:
        st.markdown("**What still needs judgment or verification**")
        for item in synthesis.uncertainties:
            st.write(f"• {item}")
    if synthesis.next_steps:
        st.markdown("**Suggested next steps**")
        for item in synthesis.next_steps:
            st.write(f"• {item}")
    for item in synthesis.referral_guidance:
        st.warning(item)


def page_analysis():
    st.header("Analysis")
    facts = st.session_state.facts
    if not facts:
        st.info("No case yet. Load an example from the sidebar, or complete Questions and Documents.")
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

    synthesis = _get_synthesis(model, result)
    if synthesis:
        _render_synthesis(synthesis)
        st.divider()

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
            ["Questions", "Documents", "Analysis"],
            index=["Questions", "Documents", "Analysis"].index(st.session_state.page),
            label_visibility="collapsed",
        )
        st.divider()
        st.caption("Load an example")
        for label, fname in EXAMPLES.items():
            if st.button(label, use_container_width=True):
                _load_example(fname)
                st.rerun()
        st.divider()
        if st.button("Start over", use_container_width=True):
            _start_over()
            st.rerun()

    {"Questions": page_questions, "Documents": page_documents, "Analysis": page_analysis}[st.session_state.page]()


if __name__ == "__main__":
    main()
