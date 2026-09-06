**IRS Resolve — Use Case Document**

Companion to `irs-resolution-eligibility-architecture.md` (v1.1, "ARCH") and `irs-resolve-implementation-plan.md` ("IMPL"). Every use case below cites the ARCH section that defines the behavior and the IMPL module that implements it, so the three documents can be checked against each other.

---

**1. System overview**

- **System:** IRS Resolve, a self-serve eligibility screening engine for IRS tax-debt resolution programs
- **Boundary:** intake UI, document ingestion, rule engine, result rendering. The IRS itself, the taxpayer's documents, and any tax professional are outside the boundary
- **Primary goal:** given a taxpayer's attested facts, return the resolution programs they likely qualify for, the ones they don't, the reasoning, and links to the governing IRS sources
- **Non-goal:** determining eligibility. The IRS does that. The system screens, routes, and explains (ARCH §1)

**2. Actors**

| Actor | Type | Description | v1 status |
|---|---|---|---|
| **Taxpayer** | Primary, human | Individual, self-employed person, or business owner who owes the IRS. Provides all input, consumes all output. Assumed no tax expertise | Active |
| **IRS guidance source** | Secondary, external | irs.gov Tax Topics, forms and instructions, Internal Revenue Manual, IRC. Referenced by URL; never called at runtime (IMPL: no network at evaluation) | Active (static) |
| **Maintainer** | Secondary, human | Engineer or tax-domain owner who updates thresholds, standards tables, citations, and rules | Active |
| **Tax reviewer** | Secondary, human | Enrolled agent, CPA, or attorney who reviews flagged cases | **Reserved** — not in v1 (ARCH §1.1, §9) |
| **Document parser** | System component | Converts uploaded W-2 / 1099 / 433-A into proposed facts. Stubbed as fixture loaders in MVP (IMPL `ingest/`) | Active (stub) |
| **Rule engine** | System component | Evaluates rules over attested + derived facts (IMPL `core/engine.py`) | Active |

**3. Use case index**

| ID | Name | Primary actor | ARCH | IMPL |
|---|---|---|---|---|
| UC-01 | Answer intake questions | Taxpayer | §5, §7 screens 1–4 | `demo/app.py`, `core/facts.py` |
| UC-02 | Upload documents | Taxpayer | §4.4, §7 screen 1a | `ingest/` |
| UC-03 | Confirm extracted data | Taxpayer | §4.4 confirm step, §7 screen 7a | `ingest/base.py merge()` |
| UC-04 | Complete financial disclosure | Taxpayer | §5 Layer 2, §7 screens 5–7 | `core/engine.py` step 5 |
| UC-05 | View resolution path | Taxpayer | §8 | `render/markdown.py` |
| UC-06 | Understand why (trace) | Taxpayer | §8.3 `rule_fired`, `inputs_relied_on` | `core/engine.py` trace |
| UC-07 | Resolve a blocker | Taxpayer | §3.0, §5 Layer 0 | `rules/l0_gate.yaml` |
| UC-08 | Act on an urgent deadline | Taxpayer | §3.16, §6 L3 P0 flags | `rules/l3_flags.yaml` |
| UC-09 | Parse documents | Document parser | §4.4 | `ingest/fixtures.py` |
| UC-10 | Evaluate eligibility | Rule engine | §5, §6 | `core/engine.py` |
| UC-11 | Cite IRS guidelines | Rule engine | §8.1, §8.2 | `core/config.py`, `config/citations.yaml` |
| UC-12 | Re-evaluate with penalty relief | Rule engine | §5.3 | `core/engine.py` step 8 |
| UC-13 | Maintain thresholds and citations | Maintainer | §4.3, §10 | `cli.py validate`, `config/` |
| UC-14 | Add or change a program rule | Maintainer | §3, §6 | `rules/*.yaml`, `config/outcomes.yaml` |
| UC-15 | Run the demo | Maintainer / stakeholder | §11 | `cli.py fixtures`, `demo/app.py` |
| UC-16 | Review flagged case | Tax reviewer | §9 | — (reserved) |

**Relationships**

- UC-01 «includes» UC-10
- UC-02 «includes» UC-09; UC-09 «precedes» UC-03
- UC-05 «includes» UC-11 and UC-06
- UC-04 «extends» UC-01 (only when UC-10 returns `NEEDS_FINANCIAL_DISCLOSURE`)
- UC-07 and UC-08 «extend» UC-05 (only when a blocker or P0 flag is present)
- UC-12 «extends» UC-10 (only when a penalty-relief flag fires)
- UC-16 «extends» UC-05 when a reviewer tier is enabled (post-v1)

---

**4. Use case specifications**

---

**UC-01 — Answer intake questions**

- **Actor:** Taxpayer
- **Goal:** provide the minimum facts needed for the engine to reach a determination
- **Preconditions:** none
- **Trigger:** taxpayer starts a new case
- **Main flow**
  1. System shows screen 1 (Who's the taxpayer). Taxpayer selects individual / self-employed / business; business adds entity type and employee flag
  2. System shows screen 1a (Upload documents) → UC-02. Taxpayer may skip optional items
  3. System shows screen 2 (What do you owe). Rows pre-filled from any parsed notice/transcript; taxpayer enters or edits tax periods and amounts; answers dispute questions
  4. System shows screen 3 (Are you current). Taxpayer answers filing, estimated-tax/deposit, and bankruptcy questions
  5. System shows screen 4 (Quick check). Taxpayer answers 180-day, proposed monthly payment, 5-year compliance, prior IA
  6. System marks all entered values `attested=true`, `source="user"` and invokes UC-10
  7. If UC-10 returns a terminal outcome → UC-05. If it returns `NEEDS_FINANCIAL_DISCLOSURE` → UC-04. If it returns a blocker → UC-07
- **Alternate flows**
  - 3a. Taxpayer doesn't know per-period amounts: selects "I'll estimate" and enters one total. System creates a single period, sets `csed_confidence=low`, and later disables FTA and bankruptcy rules (ARCH §9 data-quality table)
  - 5a. Taxpayer can pay in full within 180 days: UC-10 terminates at `SHORT_TERM_PLAN`; screens 5–7 never shown
- **Postconditions:** a `Facts` object exists with Layer 0–1 groups populated and attested
- **Rules:** recognition over recall — selects and toggles wherever the IRS rule permits (ARCH §7)

---

**UC-02 — Upload documents**

- **Actor:** Taxpayer
- **Goal:** supply W-2 or 1099 (required by employment type) and optionally 433-A, IRS notices, account transcript
- **Preconditions:** UC-01 step 1 complete (employment type known)
- **Main flow**
  1. System requests W-2 (if wages) or 1099 (if self-employed) and lists optional documents
  2. Taxpayer uploads; system classifies document type
  3. System invokes UC-09 for each document
  4. System stores resulting `ProposedFacts` with provenance for use in UC-03
- **Alternate flows**
  - 1a. Taxpayer has no document yet: selects "I don't have this"; later screens require manual entry for the affected fields
  - 2a. Classification fails: system asks taxpayer to pick the type from a list
- **Postconditions:** zero or more `ProposedFacts` sets exist, all `attested=false`
- **MVP note:** upload is a fixture picker; the flow and data shapes are identical (IMPL `ingest/fixtures.py`)

---

**UC-03 — Confirm extracted data**

- **Actor:** Taxpayer
- **Goal:** attest every pre-filled value before it reaches the rule engine
- **Preconditions:** at least one `ProposedFacts` set or one IRS-standard prefill exists
- **Trigger:** taxpayer reaches screen 7a, or screen 2 when notice data was parsed
- **Main flow**
  1. System displays each pre-filled field with a source badge (W-2 Box 1 · 1099 · 433-A §5 · IRS standard · you) and an edit control
  2. Taxpayer edits or accepts each value
  3. Taxpayer confirms the screen
  4. System calls `merge()`: accepted/edited values → `attested=true` with original provenance retained (edited values get `source="user"`, `ref` notes the original); unconfirmed proposals are dropped
- **Alternate flows**
  - 2a. Expense above IRS standard: system shows the standard alongside and notes the IRS will require justification (ARCH §9); taxpayer's value is kept
- **Postconditions:** no `attested=false` leaf remains in `Facts`
- **Invariant:** the engine refuses to evaluate any unattested fact (IMPL engine step 1)

---

**UC-04 — Complete financial disclosure**

- **Actor:** Taxpayer
- **Goal:** supply the Layer 2 facts (household, income, expenses, assets) when Layer 1 cannot terminate
- **Preconditions:** UC-10 returned `NEEDS_FINANCIAL_DISCLOSURE` with a list of missing groups
- **Main flow**
  1. System shows screen 5 (Household): size, ZIP, income sources pre-filled from W-2/1099 and 433-A §1–2
  2. System shows screen 6 (Expenses): pre-filled from 433-A §5 if uploaded, else from National/Local Standards for ZIP + household size; taxes pre-filled from W-2 withholding; childcare and court-ordered payments blank
  3. System shows screen 7 (Assets): category cards pre-filled from 433-A §3; each card is value + loan → equity
  4. System shows screen 7a → UC-03
  5. System invokes UC-10 with the full `Facts`
- **Alternate flows**
  - 1a. ZIP maps to an unknown county in the standards table: system uses state median and tags `ref="state_fallback"`
  - 3a. Taxpayer selects "none" for a category: card skipped, value 0
- **Postconditions:** Layer 2 groups populated and attested; UC-10 proceeds past step 5
- **Note:** this use case is the 433-A. When the taxpayer had no 433-A to upload, the attested data from these screens is what would be generated into one (post-MVP artifact, ARCH §4.4)

---

**UC-05 — View resolution path**

- **Actor:** Taxpayer
- **Goal:** see which programs they qualify for, why, what to do next, and where the rules come from
- **Preconditions:** UC-10 produced an `EligibilityResult`
- **Main flow**
  1. If `urgent` non-empty → UC-08 banner renders first
  2. System renders the primary determination: display name, second-person reason referencing the taxpayer's own numbers and document sources, forms, what happens next, citations as links with authority badges (UC-11)
  3. System renders alternatives with trade-off text
  4. System renders stacked relief (penalty, spouse, lien) and the "if penalties abated" scenario when present
  5. System renders "Why not…" — every excluded program with its reason and citation
  6. If `professional_referral_recommended` on any determination → referral notice with the specific reason; no "file now" call to action for that outcome
  7. System renders the disclaimer
  8. Taxpayer may open the "Why?" expander → UC-06
- **Alternate flows**
  - 1a. Primary is a `BLOCKER_*` → UC-07 replaces steps 2–5
- **Postconditions:** none (read-only)
- **Output contract:** ARCH §8.3; renderer IMPL `render/markdown.py`

---

**UC-06 — Understand why (trace)**

- **Actor:** Taxpayer
- **Goal:** see exactly which rules were evaluated and how their values resolved
- **Preconditions:** UC-05 displayed
- **Main flow**
  1. Taxpayer opens the "Why?" expander
  2. System lists every evaluated rule in layer order with `matched: true/false` and the expression rendered against actual values (e.g. `68,400 <= 50,000 → false`)
  3. Each rule links to its citations
- **Postconditions:** none
- **Rationale:** trust in a rule-based system comes from showing the arithmetic. IMPL marks this as the one feature not to cut

---

**UC-07 — Resolve a blocker**

- **Actor:** Taxpayer
- **Goal:** understand why no program is available yet and what to do first
- **Preconditions:** UC-10 returned `BLOCKER_COMPLIANCE` or `BLOCKER_BANKRUPTCY`
- **Main flow**
  1. System explains the gate in plain language with citation (Topic 202: all returns must be filed; open bankruptcy excludes payment plans)
  2. For `BLOCKER_COMPLIANCE`: lists unfiled years / missing deposits and the next action ("file these, then re-run")
  3. For `BLOCKER_BANKRUPTCY`: shows `BANKRUPTCY_DISCHARGE_POSSIBLE` informational flag if timing tests pass, with attorney referral
  4. Any `URGENT_LEVY_RELEASE` flag still renders — hardship levy release does not require full compliance (ARCH §9 rule conflicts)
  5. System offers "I've fixed this, re-run" which returns to UC-01 step 4
- **Postconditions:** taxpayer knows the prerequisite; case can be re-evaluated

---

**UC-08 — Act on an urgent deadline**

- **Actor:** Taxpayer
- **Goal:** not miss a time-boxed right (30-day CDP window) or stop active enforcement (levy)
- **Preconditions:** a P0 flag fired in L3 (`CDP_WINDOW_OPEN`, `URGENT_LEVY_RELEASE`)
- **Main flow**
  1. System renders the urgent banner above everything else: flag, days remaining, the action (e.g. "File Form 12153 within 11 days"), the reason, and the citation
  2. For `CDP_WINDOW_OPEN`: explains that the hearing preserves Tax Court review and suspends levy; recommends representation
  3. For `URGENT_LEVY_RELEASE`: explains hardship release and that entering an IA also stops the levy
- **Postconditions:** none
- **Data dependency:** notice type and date from UC-01 screen 8 / parsed notice in UC-09

---

**UC-09 — Parse documents**

- **Actor:** Document parser (system)
- **Goal:** turn an uploaded document into `ProposedFacts` with field-level provenance
- **Preconditions:** document classified
- **Main flow**
  1. Parser reads the document (MVP: JSON fixture shaped like the extraction target)
  2. Maps fields per ARCH §4.4: W-2 Box 1 ÷ 12 → wages; W-2 Boxes 2/4/6/17/19 ÷ 12 → taxes withheld; 1099 total ÷ 12 → self-employment income (income only, never expenses); 433-A sections → household, income, assets, expenses; notice → `notices_received[]`, balances, enforcement flags; transcript → assessment dates, SFR indicators
  3. Tags every value `source=<doc>`, `ref=<box or section>`, `attested=false`
  4. Returns `ProposedFacts`
- **Alternate flows**
  - 2a. Document tax year is not current: parser sets `income_data_stale=true`; UC-03 asks "is this still your income?"
  - 2b. Multiple W-2s or 1099s: parser sums and lists all `ref`s
- **Postconditions:** `ProposedFacts` ready for UC-03
- **Invariant:** parsers never write to `Facts` directly

---

**UC-10 — Evaluate eligibility**

- **Actor:** Rule engine (system)
- **Goal:** produce a deterministic `EligibilityResult` from attested facts
- **Preconditions:** `Facts` validates; all leaves attested; config and rules loaded and validated (UC-13)
- **Main flow** (IMPL engine steps)
  1. Reject unattested leaves
  2. Derive all computable fields (Layer 2 derivations return null when Layer 2 facts absent)
  3. L0 first-match → blocker or continue
  4. L1 first-match → terminal (`next: stop`) or continue
  5. If Layer 2 facts missing → return `NEEDS_FINANCIAL_DISCLOSURE` with missing groups
  6. L2_primary first-match; L2_secondary all-match
  7. L3 all-match
  8. If any rule has `rerun_with` → UC-12
  9. Build `excluded` from non-matching outcome rules in L1 / L2_primary
  10. Resolve reason templates, attach citations (UC-11), sort urgent, stamp versions, attach trace
- **Alternate flows**
  - 6a. No L2_primary rule matches: `else` rule assigns `CNC` with `referral=true` and reason "unclassified capacity profile"
- **Postconditions:** `EligibilityResult` with `engine_version`, `config_version`, and `trace`
- **Guarantees:** no network; same input + config → identical output; < 50 ms

---

**UC-11 — Cite IRS guidelines**

- **Actor:** Rule engine (system)
- **Goal:** attach a sourced, linked citation to every determination and exclusion
- **Preconditions:** `citations.yaml` loaded; every rule's citation keys resolved at load
- **Main flow**
  1. For each fired or excluded rule, look up each key in `rule.citations` → `Citation{source, url, authority, excerpt_paraphrase, retrieved}`
  2. Attach to the `Determination`
  3. Renderer shows `[Source](url)` with authority badge (IRC > Treas. Reg. > IRM > irs.gov > form instructions)
- **Alternate flows**
  - 1a. Two citations conflict (e.g., outdated 9465 instructions vs. current Simple Payment Plan page): the higher-authority or more recent irs.gov source is listed first; the paraphrase notes the discrepancy (ARCH §8.1)
- **Postconditions:** every `Determination` has ≥ 1 citation with a URL (acceptance check)
- **Source of truth:** ARCH §8.2 canonical citation map → `config/citations.yaml`

---

**UC-12 — Re-evaluate with penalty relief**

- **Actor:** Rule engine (system)
- **Goal:** show whether removing penalties changes the primary outcome
- **Preconditions:** `FTA_ELIGIBLE` or `REASONABLE_CAUSE_CANDIDATE` fired with `rerun_with: total_debt_if_penalties_abated`
- **Main flow**
  1. Engine substitutes `total_debt` with the abated value
  2. Re-runs L0–L2 (not L3)
  3. Stores `scenario_if_penalties_abated: {total_debt, primary, changed}`
  4. UC-05 renders the scenario under stacked relief ("Balance drops to $61,300; still points to a PPIA" or "…which qualifies you for a Simple Payment Plan")
- **Postconditions:** both scenarios visible to the taxpayer

---

**UC-13 — Maintain thresholds and citations**

- **Actor:** Maintainer
- **Goal:** keep config current with IRS changes without touching code
- **Preconditions:** repository access
- **Trigger:** IRS updates a threshold, standards table (April), poverty guideline (January), or a URL moves
- **Main flow**
  1. Maintainer edits `thresholds.yaml` / `standards/*.csv` / `poverty.yaml` / `citations.yaml`
  2. Bumps `config_version`
  3. Runs `irsresolve validate` — fails on any rule referencing a missing key, any config key lacking a citation, any unparseable expression, any unresolved reason placeholder
  4. Runs `irsresolve fixtures` — confirms expected outcomes (or intentionally updates a fixture's expected outcome with a note)
  5. Commits
- **Alternate flows**
  - 3a. Weekly link check reports a 404/redirect → maintainer updates URL, bumps `config_version`
- **Postconditions:** engine behavior changes with no Python edit (acceptance check)

---

**UC-14 — Add or change a program rule**

- **Actor:** Maintainer
- **Goal:** add a new IRS program or adjust an existing rule's logic
- **Main flow**
  1. Add a program entry to ARCH §3 (what, requirements, reasoning, forms)
  2. Add citation key(s) to `citations.yaml`
  3. Add outcome code to `outcomes.yaml` (display name, forms, what happens next)
  4. Author the rule in the appropriate `rules/l*.yaml` with `when`, `reason`, `reason_if_excluded` (if outcome rule), `citations`, `uses`
  5. If a new derived fact is needed: add one function + unit test in `derive.py`
  6. Add or update a fixture; run `validate` and `fixtures`
- **Postconditions:** the three documents and the code agree; the new program appears in results and in `excluded` when not matched

---

**UC-15 — Run the demo**

- **Actor:** Maintainer / stakeholder
- **Goal:** see the end-to-end flow in one click
- **Main flow**
  1. Launch `streamlit run irsresolve/demo/app.py`
  2. Click "Load example" for one of four fixtures (A Simple Plan; B CNC + OIC; C business blocker; D PPIA + CDP + FTA)
  3. Step through pre-filled screens or jump to Results
  4. Open the "Why?" trace
- **Alternate:** `irsresolve run fixtures/case_d_ppia_cdp.json --md --trace` for a terminal-only walkthrough
- **Postconditions:** results render in < 1 s; fixtures correspond to ARCH §11 worked examples

---

**UC-16 — Review flagged case (reserved)**

- **Actor:** Tax reviewer
- **Status:** not implemented in v1. The data model reserves `reviewer_queue` (null in v1) and every referral-flagged outcome already carries the reason a reviewer would need (ARCH §9). Enabling this use case means: populate `reviewer_queue` when `professional_referral_recommended` is true, add a reviewer view over `EligibilityResult` + `trace`, and add a reviewer decision field. No rule changes.

---

**5. Traceability matrix**

| Use case | ARCH sections | IMPL modules | Fixture(s) | Acceptance item(s) |
|---|---|---|---|---|
| UC-01 | §5, §7 | `core/facts.py`, `demo/app.py` | A–D | Layer 1 termination; attested check |
| UC-02 | §4.4, §7 | `ingest/` | D (notice) | — |
| UC-03 | §4.4 | `ingest/base.py` | all | `attested=False` → `FactsError` |
| UC-04 | §5 L2, §7 | `core/engine.py` step 5 | B, D | `NEEDS_FINANCIAL_DISCLOSURE` |
| UC-05 | §8 | `render/markdown.py` | all | every determination has a citation |
| UC-06 | §8.3 | `core/engine.py` trace | all | trace shows resolved values |
| UC-07 | §3.0, §5 L0 | `rules/l0_gate.yaml` | C | C → `BLOCKER_COMPLIANCE` |
| UC-08 | §3.16, §6 L3 | `rules/l3_flags.yaml` | D | D → `CDP_WINDOW_OPEN` urgent |
| UC-09 | §4.4 | `ingest/fixtures.py` | all | provenance tagged |
| UC-10 | §5, §6 | `core/engine.py` | all | determinism; < 50 ms |
| UC-11 | §8.1, §8.2 | `config/citations.yaml` | all | ≥ 1 citation per determination |
| UC-12 | §5.3 | `core/engine.py` step 8 | D | FTA rerun scenario |
| UC-13 | §4.3, §10 | `cli.py validate` | all | YAML change alters outcome |
| UC-14 | §3, §6 | `rules/`, `derive.py` | new | validate + fixtures pass |
| UC-15 | §11 | `cli.py`, `demo/app.py` | A–D | < 1 s render |
| UC-16 | §9 | — | — | reserved |

**6. Glossary**

- **Attested** — a fact the taxpayer has explicitly confirmed; the only kind the engine will evaluate
- **Provenance** — where a value came from (user, W-2, 1099, 433-A, notice, IRS standard prefill) and the specific box/section
- **Derived fact** — a value computed from attested facts by a named function (e.g., `disposable_income`)
- **Determination** — one program outcome (primary, alternative, stacked, or excluded) with reason and citations
- **Blocker** — a Layer 0 result that prevents all programs until resolved
- **P0 flag** — a Layer 3 result with a deadline or active enforcement; rendered first
- **Referral** — `professional_referral_recommended=true`; the taxpayer is advised to consult a professional; the UI shows no "file now" action
- **Trace** — the per-rule record of evaluation with resolved values
