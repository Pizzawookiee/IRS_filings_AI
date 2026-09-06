# IRS Resolve v1 — UI Design Specification

**Status:** Proposed UI companion specification  
**Version:** 1.0  
**Date:** September 2026  
**Applies to:** IRS Resolve v1  
**Companion documents:**

- `irs-resolution-eligibility-architecture.md` — rule logic, canonical data model, ingestion, screen flow, output contract
- `irs-resolve-use-cases.md` — user-facing use cases and flow; wins on user-facing behavior if documents disagree
- `irs-resolve-implementation-plan.md` — implementation structure and engine contract
- `irs-resolve-build-prompt.md` — v1 build constraints

---

## 1. Purpose

This document defines the **user interface and interaction model** for IRS Resolve v1. It does not redefine tax rules, thresholds, formulas, eligibility outcomes, or engine behavior. Its job is to make the existing architecture usable through a simple product shell while preserving the behavior required by the design documents.

The UI has exactly **three top-level pages**:

1. **Questions** — branching questions and taxpayer attestation
2. **Intake** — document upload, classification, extraction status, and source provenance
3. **Analysis** — eligibility results, reasoning, citations, exclusions, urgent actions, and professional-referral notices

These are persistent navigation surfaces, not a claim that the underlying decision tree has only three steps. The rule engine still follows the Layer 0 → Layer 1 → Layer 2 → Layer 3 order defined in the architecture.

---

## 2. Product principles

### 2.1 Three pages, one decision flow

The UI intentionally collapses the architecture's many logical screens into three stable pages. Internal sections on a page may appear or disappear based on engine state.

The three-page shell must never remove branching behavior required by the engine.

### 2.2 Recognition over recall

Use cards, toggles, selects, checkboxes, source badges, and prefilled values whenever possible. Free-text entry is reserved for values or explanations that cannot be represented reliably with structured controls.

This preserves the architecture's screen-flow principle: **recognition over recall wherever the IRS rule permits**.

### 2.3 Documents propose facts; the taxpayer attests facts

No parsed document value is authoritative by itself.

Every extracted or IRS-standard-prefilled value must show:

- the value,
- its source,
- an edit control,
- and an explicit confirmation state.

Only attested values may be sent to the rule engine.

### 2.4 Results explain, not merely score

Analysis must show:

- the recommended path,
- why it was recommended,
- the taxpayer inputs relied on,
- official IRS citations,
- alternatives,
- excluded programs and reasons,
- urgent deadlines before ordinary recommendations,
- and professional-referral recommendations when required.

### 2.5 v1 is taxpayer-facing

The architecture defines v1 as a **self-serve B2C product**. There is no preparer, attorney, CPA, or reviewer sitting between the taxpayer and the intake.

Therefore the v1 UI must **not** show an attorney identity such as “Jane Davis — Tax Attorney,” a reviewer queue, attorney approval controls, or internal case-management language.

A reviewer role may be added later, but it is not part of this UI specification.

---

## 3. Important compatibility changes from earlier mockups

Earlier UI concepts in this project conflict with the updated architecture in several places. This specification supersedes those mockups.

| Earlier concept | Updated v1 requirement |
|---|---|
| Form 433-A is mandatory | **Form 433-A is optional** if the taxpayer already has one. If absent, the Questions page collects the equivalent Layer 2 data. |
| W-2 and 1099 are always optional | **W-2 is required for payroll employment; 1099 is required for self-employment**, based on taxpayer type/income source. |
| Only W-2 / 1099 / 433-A uploads are accepted | Intake must also accept **IRS notices** and an **account transcript** when offered because they prefill debt, enforcement, and CSED-related fields. Form 433-B is accepted for business taxpayers. |
| Debt/year are always asked before upload | The updated use-case flow uploads documents immediately after taxpayer segmentation so notices/transcripts can prefill debt rows. |
| Document values feed analysis immediately | Extracted values remain **proposed** until the taxpayer confirms them. |
| Attorney-facing case workspace | v1 is **taxpayer-facing self-serve**. |
| Header always shows 1040, debt, and tax years | Header values must be state-driven. Do not display a tax form, period, or debt amount until known/attested. |
| Search bar | No global search is required in the three-page v1 shell. |

---

## 4. Global application shell

### 4.1 Navigation

The left sidebar contains exactly three items, in this order:

1. **Questions**
2. **Intake**
3. **Analysis**

The current page is highlighted.

Do not include Cases, Calendar, Tasks, Documents, Contacts, Reports, or other practice-management navigation in v1.

### 4.2 Header

The header should be lightweight and state-driven.

Recommended elements:

- taxpayer/case display name when available,
- simple account/profile control,
- current save/status indicator,
- no global search bar.

Optional case summary chips may appear only after their values are known:

- taxpayer type,
- relevant tax periods,
- total IRS debt,
- blocker/urgent status.

Do **not** pre-populate `1040`, tax years, or debt before the taxpayer has entered or confirmed them.

### 4.3 Page states

Each page supports:

- `not_started`
- `in_progress`
- `complete`
- `blocked`

Analysis additionally supports:

- `needs_financial_disclosure`
- `ready`
- `urgent`

State must be derived from the engine/session facts, not visual-only progress counters.

---

# 5. Page 1 — Questions

## 5.1 Role of the page

Questions is the **branching fact-collection and attestation surface**.

It combines the architecture's logical screens:

- Screen 1 — Who's the taxpayer?
- Screen 2 — What do you owe?
- Screen 3 — Are you caught up?
- Screen 4 — Quick check
- Screens 5–7 — Layer 2 financial disclosure when required
- Screen 7a — Confirm your data
- Screen 8 — Anything else going on?

Because document upload occurs between segmentation and later questions in the use-case flow, the Questions page has **stages** rather than behaving as one static form.

## 5.2 Stage A — Before document intake

On first entry, show only the minimum segmentation fields needed to know which documents to request.

### Taxpayer type

**Question:** Who owes the IRS?

Options:

- Individual
- Self-employed
- Business

Conditional fields:

- `business_entity_type` if business
- `has_employees` if business

### Primary action

After segmentation:

**Continue to Intake →**

This is intentional. The updated use-case flow uploads documents at this point so extracted notice/transcript/income data can prefill later questions.

Do not ask the complete debt/compliance questionnaire before the first Intake visit.

---

## 5.3 Stage B — Debt snapshot

After the Intake page has had a chance to parse uploaded notices/transcripts, returning to Questions shows the debt section.

### What do you owe?

Render repeatable tax-period rows containing:

- year,
- tax type,
- tax balance,
- penalty balance,
- interest balance.

Where available, prefill from IRS notices or account transcript.

Each prefilled value shows a source badge, for example:

- `IRS notice`
- `Account transcript`
- `You`

### Unknown amounts

Offer:

**I’ll estimate**

This creates a single estimated total and must visibly warn that:

- CSED confidence will be lower,
- First Time Abate analysis may be disabled,
- bankruptcy timing analysis may be disabled,
- the result will carry a data-incomplete flag.

### Liability dispute

Ask:

**Do you believe the IRS balance is wrong?**

If yes, reveal:

- dispute reason,
- whether the taxpayer previously appealed or went to court on the same liability.

This branch is important because Doubt as to Liability does not use the normal financial RCP analysis.

---

## 5.4 Stage C — Compliance gate

Section title:

**Are you caught up?**

Questions:

- Have all required tax returns been filed?
- If no, which years are unfiled?
- Are current-year estimated payments current? — self-employed/business only
- Are federal tax deposits current? — business with employees only
- What is the current bankruptcy status?

If the compliance gate produces a blocker, the UI should stop ordinary progression and show the blocker with its next action.

Urgent enforcement relief may still surface later even when a compliance blocker exists.

---

## 5.5 Stage D — Quick capacity check

Section title:

**Quick check**

Questions:

- Could you pay the full IRS balance within 180 days?
- What monthly payment could you realistically make?
- Have you been compliant for the prior 5 years?
- Have you had an IRS installment agreement in the prior 5 years?

This stage corresponds to Layer 1.

If the engine reaches a terminal Layer 1 outcome, the taxpayer should not be asked for unnecessary household, expense, or asset information.

The UI then proceeds to the final-flags section and Analysis.

---

## 5.6 Stage E — Financial disclosure, only when required

If the engine returns `NEEDS_FINANCIAL_DISCLOSURE`, Questions expands the Layer 2 sections.

Do not create another top-level page.

### Household

Fields:

- household size,
- ZIP code,
- state,
- monthly gross income,
- income sources,
- spouse income included where applicable.

Income should be prefilled from W-2/1099 when available.

### Monthly expenses

Fields map directly to `expenses.*` in the canonical model.

Prefill priority:

1. Form 433-A values, if uploaded
2. IRS standards based on ZIP/household size
3. user entry

Taxes may be prefilled from W-2 withholding.

When the taxpayer enters an expense above an IRS standard, show both numbers and explain that the excess may require justification. Keep the taxpayer-entered value.

### Assets

Render category cards for:

- cash/bank,
- investments,
- retirement,
- life-insurance cash value,
- real estate,
- vehicles,
- business assets,
- other assets.

For real estate/vehicles, show value + secured debt and calculate equity in the UI.

Use “None” to skip a category.

### 433-A behavior

If Form 433-A was uploaded, these sections should largely be **confirmation screens**, not re-entry screens.

If no 433-A was uploaded, these sections collect the equivalent data set and may later support generation of Form 433-A/433-F.

---

## 5.7 Stage F — Confirm extracted and prefilled data

Before analysis, every proposed/prefilled field used by the engine must be attested.

For each field show:

- field label,
- proposed value,
- source badge,
- edit control,
- confirmation state.

Source badges include:

- W-2
- 1099
- Form 433-A
- Form 433-B
- IRS notice
- Account transcript
- IRS standard
- You

Actions:

- **Accept**
- **Edit**

Edited values retain provenance to the original proposal but become `source=user_entered` for evaluation.

The page may not continue to Analysis while any required evaluated leaf is `attested=false`.

---

## 5.8 Stage G — Anything else going on?

This section always runs, even for taxpayers who terminate at Layer 1.

Use compact checkbox groups with at most one follow-up per checked item.

### Penalties

Capture:

- prior 3-year penalty history,
- reason for possible penalty relief,
- relevant disaster/combat-zone/IRS-advice facts when applicable.

### Hardship / special circumstances

Capture relevant circumstances such as:

- serious illness,
- disability,
- elderly taxpayer,
- dependent with special needs,
- natural disaster,
- death in family,
- unemployment.

### Spouse issues

Capture relevant spouse-relief facts, including:

- concealed income,
- separation/divorce,
- abuse,
- refund offset to spouse debt.

### Enforcement / notices

Capture or confirm:

- wage levy,
- bank levy,
- lien filed,
- seizure notice,
- passport certification,
- recent IRS notices and dates.

If `CDP_WINDOW_OPEN` or `URGENT_LEVY_RELEASE` is detected, Analysis must lead with that urgent item.

---

## 5.9 Questions page UX

Recommended layout:

- one primary content column,
- internal stepper/accordion for the current stage,
- concise “Why we ask” helper text,
- source badges inline with prefilled values,
- sticky bottom action bar.

Actions vary by stage:

- **Continue to Intake** after Stage A
- **Save and continue** within later stages
- **View Analysis** when all required facts are attested

Avoid rendering every question at once.

---

# 6. Page 2 — Intake

## 6.1 Role of the page

Intake is the **document acquisition and extraction-status surface**.

It should not duplicate the decision questionnaire. Its purpose is to obtain documents, classify them, parse them into proposed facts, and show the taxpayer what was found.

## 6.2 Required documents are conditional

The page determines required-document cards from taxpayer segmentation.

### Payroll employee

Require:

- W-2

### Self-employed / contractor

Require:

- relevant 1099 document(s)

### Business

Request documents appropriate to the business facts. Form 433-B must be accepted when supplied.

### Form 433-A

Form 433-A is **optional**, not mandatory.

Label:

**Form 433-A — Optional, saves time**

Helper text:

> If you already have a completed Form 433-A, upload it to prefill household, income, expense, and asset questions. If not, IRS Resolve will ask for the same information later if it is needed.

---

## 6.3 Optional but high-value uploads

Always allow:

- IRS notices such as CP14, CP504, LT11, Letter 1058, Letter 3172
- account transcript

Explain value:

- notices can prefill balances and identify urgent collection deadlines,
- transcripts provide the best assessment-date/CSED data.

These documents are optional and must not block progress.

---

## 6.4 Upload interaction

Primary surface:

**Drag and drop documents here**

Secondary:

**Browse files**

For each file show:

- filename,
- detected document type,
- extraction status,
- whether it is required/optional,
- extraction result count,
- error/reclassification action if needed.

Statuses:

- Uploading
- Classifying
- Extracting
- Ready to review
- Needs type confirmation
- Failed

If classification fails, ask the taxpayer to select a type.

---

## 6.5 Extracted-data preview

Intake may show a compact preview such as:

> **12 proposed values found**  
> Wages, federal withholding, employer, two tax periods, one notice date…

Do **not** mark these values as confirmed on Intake.

Primary action:

**Review questions →**

This returns the taxpayer to the Questions page, where the proposed values appear in context and can be attested.

---

## 6.6 Missing required document handling

If a conditionally required W-2/1099 is unavailable, support:

**I don’t have this yet**

The design docs allow affected fields to fall back to manual entry later.

The UI must distinguish:

- missing required document but manual fallback available,
- missing optional document,
- blocker caused by missing required facts.

Do not falsely imply that an optional 433-A blocks analysis.

---

## 6.7 Intake page UX

Recommended layout:

1. Required documents
2. Optional documents
3. Large drop zone
4. Upload/extraction status list
5. Extracted-value summary
6. **Review questions** primary CTA

No attorney notes, chat, case queue, or analysis cards on this page.

---

# 7. Page 3 — Analysis

## 7.1 Role of the page

Analysis renders the `EligibilityResult` produced by the engine. It does not independently calculate eligibility in the client.

The page is taxpayer-facing and should use plain language while retaining auditable rule reasoning.

---

## 7.2 Readiness gate

Analysis must not present a final recommendation if:

- required facts are missing,
- any fact used by the engine is unattested,
- the engine returned `NEEDS_FINANCIAL_DISCLOSURE`,
- a compliance blocker requires resolution first.

Instead show the appropriate state and a CTA back to Questions or Intake.

Examples:

- **More information needed** → Questions
- **Review extracted values** → Questions
- **Upload your W-2** → Intake
- **File missing returns first** → blocker guidance

---

## 7.3 Urgent issues render first

If `EligibilityResult.urgent` is non-empty, render a high-visibility banner before all other content.

Examples:

- CDP deadline
- active levy requiring urgent relief

The banner must show:

- what is happening,
- deadline/days remaining when known,
- recommended next action,
- reason,
- official IRS citation/link.

Urgent issues outrank the ordinary primary plan visually.

---

## 7.4 Primary resolution path

The primary recommendation card shows:

- outcome display name,
- plain-language recommendation status,
- reason using the taxpayer's own numbers,
- key inputs relied on,
- source/provenance labels where useful,
- forms/mechanism,
- what happens next,
- official IRS source links.

Do not display a bare “eligible” score without reasoning.

---

## 7.5 Alternatives and trade-offs

Render applicable alternatives below the primary path.

Each alternative shows:

- why it also fits,
- the relevant trade-off,
- any estimated payment/offer floor where supported,
- citation(s).

When both CNC and OIC-DATC apply, show both and explain the core trade-off rather than suppressing one.

---

## 7.6 Stacked relief

Separate from the primary collection path, render stacked/parallel relief such as:

- First Time Abate,
- reasonable-cause penalty relief,
- innocent spouse possibilities,
- lien relief,
- passport reversal implications.

If penalty relief changes the primary analysis, show the re-evaluated scenario distinctly:

**If eligible penalties are removed…**

---

## 7.7 Why not…

Every excluded program should be available in a collapsed section:

**Why not other options?**

For each excluded program show:

- program name,
- exclusion reason,
- relevant taxpayer input(s),
- citation.

This is part of the output contract and should not be omitted for UI simplicity.

---

## 7.8 Professional-referral recommendation

For outcomes identified by architecture §9, show a clear recommendation to consult a qualified professional, with the specific reason.

Do not render a “File now” CTA for outcomes that the architecture says are never ready-to-file.

v1 still has no reviewer queue; this is guidance to the taxpayer.

---

## 7.9 Financial breakdown

When applicable, show transparent calculations such as:

- disposable income,
- NRE,
- full-pay capacity,
- RCP lump-sum,
- RCP periodic,
- estimated payment or offer floor.

Use expandable detail rather than overwhelming the primary recommendation.

Every number should trace back to attested inputs and documented formulas.

---

## 7.10 Citations

Every determination must include at least one official IRS citation with URL and authority label.

Recommended display:

**Sources**

- IRS Form / Instructions
- IRS Tax Topic
- Internal Revenue Manual
- IRC / Treasury Regulation

Citations come from config; do not hard-code URLs in UI components.

---

## 7.11 Generated artifacts

If the taxpayer did not upload Form 433-A and completed the Layer 2 questions manually, Analysis may expose the generated Form 433-A/433-F artifact when that feature exists.

This is an output of the attested intake, not a required upload.

---

# 8. End-to-end navigation behavior

The three-page UI supports the architecture's documented order with the following route flow:

```text
Questions — Stage A: taxpayer segmentation
        ↓
Intake — upload/parse W-2 or 1099 + optional 433-A/notices/transcript
        ↓
Questions — Stages B–G: prefilled debt/compliance/capacity,
            conditional Layer 2 disclosure, confirmation, flags
        ↓
Analysis — result / blocker / needs-more-data / urgent state
```

This means the sidebar still contains only **Questions / Intake / Analysis**, but Questions may be visited twice during a normal case.

This is required to preserve the updated use-case flow in which documents prefill later questions.

### Shortcut behavior

- If a user opens Analysis too early, show readiness status and route back to the missing step.
- If a user edits a previously attested input after seeing Analysis, mark the result stale and require re-evaluation.
- If a new document is uploaded after Analysis, extracted proposals do not alter the result until confirmed on Questions.

---

# 9. Provenance and attestation UI contract

Every canonical field used by the engine should carry UI metadata equivalent to:

```yaml
value: ...
source: user_entered | w2 | 1099 | 433a | 433b | notice | transcript | irs_standard_prefill
source_ref: document/page/box or config reference
attested: true | false
```

### Visual rules

- `attested=false` → source badge + “Review” state
- accepted → checkmark + source badge
- edited → “You edited” + original source available in detail
- stale source (e.g. old W-2) → warning + required confirmation

The engine must never evaluate an unattested fact.

---

# 10. Error and edge-case UX

## 10.1 Unknown debt detail

Allow estimate fallback, but explain the consequences and mark analysis confidence accordingly.

## 10.2 Missing assessment date

Show:

> Assessment date not verified. CSED-related conclusions have lower confidence. Upload an IRS account transcript for the best estimate.

## 10.3 Self-employed income

A 1099 represents gross receipts, not net income. Never imply that 1099 income alone determines disposable income. Business expenses must come from Form 433-A/433-B or Questions.

## 10.4 Expenses above standards

Show taxpayer value and IRS-standard value side by side. Preserve the taxpayer value while explaining that justification may be needed.

## 10.5 Multiple tax types

Support multiple period/type rows. Do not collapse 1040 and 941 debt into one generic category in the UI.

## 10.6 New upload after attestation

Do not silently replace confirmed facts. New extracted values become proposals requiring review.

---

# 11. Visual design guidance

The product should feel like a calm financial decision tool rather than a tax form replica.

### Layout

- persistent dark or neutral sidebar,
- wide main content column,
- clear section hierarchy,
- desktop-first for v1 Streamlit demo,
- minimal top chrome.

### Status colors

- blue: informational / active step
- green: complete / recommended
- amber: needs review / uncertainty
- red: blocker / urgent deadline

Color must never be the only indicator; pair with text/iconography.

### Cards

Use cards for:

- document categories,
- outcome recommendations,
- asset categories,
- urgent items.

Avoid excessive card fragmentation for ordinary form fields.

### Copy

Prefer taxpayer-facing language:

- “What do you owe?” instead of `L0_debt_snapshot`
- “Are you caught up?” instead of `returns_filed_all`
- “Why this fits” instead of “rule fired” in the default view

Technical trace may appear in a “Why?” expander.

---

# 12. Page-level data ownership

| Data / behavior | Questions | Intake | Analysis |
|---|---:|---:|---:|
| Taxpayer segmentation | **Owns** | Reads | Reads |
| Debt periods / amounts | **Owns + attests** | May propose from docs | Reads |
| Compliance | **Owns** | — | Reads |
| Quick capacity | **Owns** | — | Reads |
| Household / expenses / assets | **Owns + attests when required** | May propose from 433-A/B | Reads |
| Special/enforcement flags | **Owns + attests** | May propose from notices | Reads |
| File upload/classification | — | **Owns** | — |
| Document extraction | — | **Owns proposal creation** | — |
| Confirmation/attestation | **Owns** | Shows preview only | Requires complete |
| Eligibility computation | Triggers | Does not compute | Displays result |
| Citations | — | — | **Owns display** |
| Urgent banner | — | — | **Owns display** |
| Excluded programs | — | — | **Owns display** |
| Referral recommendation | — | — | **Owns display** |

---

# 13. Implementation mapping

The UI must remain a thin layer over the existing model/engine.

### Questions

Writes/attests `Facts` fields and invokes engine evaluation at the documented checkpoints.

### Intake

Uses `ingest/` components to produce `ProposedFacts` with provenance. Intake itself does not merge proposals into engine-ready facts without taxpayer confirmation.

### Analysis

Renders `EligibilityResult` and its trace/citations. It does not recreate rule logic in Streamlit callbacks or UI conditionals.

### Important rule

No threshold, IRS URL, display name, or form number that belongs in config should be duplicated as business logic in UI code.

The UI may display configured values, but it must not become a second rule engine.

---

# 14. Acceptance criteria for the UI

The UI is compatible with the updated IRS Resolve design documents when all of the following are true:

1. Sidebar has exactly **Questions, Intake, Analysis**.
2. v1 is taxpayer-facing; no active attorney/reviewer workflow appears.
3. Questions first collects taxpayer segmentation, then routes to Intake.
4. Intake conditionally requires W-2 or 1099 based on taxpayer type/income source.
5. Form 433-A is shown as optional, not required.
6. Intake accepts IRS notices and account transcripts as optional high-value documents.
7. Parsed values are clearly labeled as proposed/unconfirmed.
8. Later Questions sections are prefilled from documents where possible.
9. The taxpayer must explicitly confirm or edit all proposed/prefilled values before evaluation.
10. `attested=false` facts never reach the rule engine.
11. Layer 1 terminal cases skip unnecessary Layer 2 household/expense/asset questions.
12. Layer 2 questions appear only when the engine returns `NEEDS_FINANCIAL_DISCLOSURE`.
13. When 433-A exists, Layer 2 is mostly confirmation; when absent, Questions collects equivalent data.
14. “Anything else going on?” always runs before final Analysis.
15. Analysis blocks final recommendations when required facts are missing or unattested.
16. Urgent CDP/levy items render above the primary recommendation.
17. Analysis displays the primary path, alternatives, stacked relief, exclusions, reasoning, and official IRS citations.
18. Referral-required outcomes show a professional-referral recommendation and no “File now” CTA.
19. New uploads after analysis do not silently change results; confirmation and re-evaluation are required.
20. The UI contains no duplicate tax-rule engine or hard-coded threshold logic.

---

# 15. Minimal v1 screen composition

## Questions

```text
┌ Questions ────────────────────────────────────────────────┐
│ Stage indicator                                           │
│                                                          │
│ [Current branching question section]                     │
│                                                          │
│ Prefilled values show:  Value   [Source]   [Edit/Accept] │
│                                                          │
│                                       [Continue →]        │
└──────────────────────────────────────────────────────────┘
```

## Intake

```text
┌ Intake ───────────────────────────────────────────────────┐
│ Required documents                                       │
│ [W-2 or 1099 depending on taxpayer]                      │
│                                                          │
│ Optional                                                  │
│ [433-A] [IRS notice] [Account transcript]                │
│                                                          │
│ ┌──────── Drag / drop or Browse ───────────────────────┐ │
│ └───────────────────────────────────────────────────────┘ │
│                                                          │
│ Uploaded files + classification/extraction status        │
│ Proposed values found: 12                                │
│                                       [Review questions] │
└──────────────────────────────────────────────────────────┘
```

## Analysis

```text
┌ Analysis ─────────────────────────────────────────────────┐
│ [URGENT ACTION — only when present]                      │
│                                                          │
│ Recommended path                                         │
│ Why this fits + taxpayer numbers + official IRS sources  │
│                                                          │
│ Alternatives / trade-offs                                │
│ Stacked relief                                           │
│ Why not other options?                                   │
│ Professional recommendation, when required               │
│                                                          │
│ [Why? trace]       [Generated form, when available]      │
└──────────────────────────────────────────────────────────┘
```

---

## 16. Summary

IRS Resolve v1 should feel like a **three-page product** even though the eligibility engine is a branching, multi-layer workflow.

- **Questions** owns facts, branching, and attestation.
- **Intake** owns documents and proposed facts.
- **Analysis** owns explanation of the engine result.

The key integration rule is simple:

> **Documents may propose facts. Only the taxpayer may attest facts. Only attested facts reach the engine. The Analysis page renders the engine result; it never invents one.**

This separation keeps the UI simple without weakening the auditability, branching behavior, or safety constraints defined by the updated architecture and use cases.
