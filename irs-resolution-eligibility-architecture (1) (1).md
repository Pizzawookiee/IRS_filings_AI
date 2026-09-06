# IRS Tax Debt Resolution Eligibility Engine
## End-to-End Architecture: Intake → Decision Tree → Eligible Programs

**Version:** 1.1 · **Date:** September 2026 · **Audience:** Product, engineering, and tax-domain reviewers

---

## Table of contents

1. Purpose and scope
2. How the IRS thinks about collection (the reasoning model behind every program)
3. Program catalog — every resolution path, its requirements, and why it exists
4. Canonical data model — inputs, derived fields, configuration, and document ingestion
5. The decision tree — gates, tiers, capacity, outcomes, and flags
6. Rule specification (machine-readable)
7. Screen-by-screen intake flow
8. Output contract — eligibility result with reasoning and IRS citations
9. Professional-referral triggers and edge cases
10. Configuration and maintenance
11. Worked examples
12. Disclaimers and validation requirements

---

## 1. Purpose and scope

This document specifies a single system that:

- **Collects** a taxpayer's financial situation through a branching intake (asking only what is needed to reach a determination)
- **Evaluates** that data against the eligibility rules of every IRS collection alternative
- **Outputs** a ranked list of programs the taxpayer likely qualifies for, with the reasoning for each, plus the programs they are excluded from and why

The system is a **screening and routing engine**, not a determination authority. The IRS makes final determinations. The engine's job is to get the taxpayer into the right application with the right supporting data, and to flag cases that need a professional.

### 1.1 Actors, inputs, and outputs

| Question | Answer |
|---|---|
| **Who provides the input?** | The taxpayer who owes the IRS. This is a self-serve B2C product; no preparer or agent sits between the taxpayer and the intake. |
| **What input do they provide?** | (a) Answers to the branching intake questions (Sections 5–7); (b) two required documents — a **W-2** if employed on payroll, or a **1099** if self-employed — and (c) an optional **Form 433-A** if the taxpayer already has one. When no 433-A exists, the intake screens generate the equivalent data set. |
| **Who consumes the output?** | The taxpayer. v1 has **no human reviewer in the loop**. Outcomes that the engine cannot safely determine on its own are surfaced to the taxpayer as a *professional referral recommendation* with the reason, not routed to an internal queue (see Section 9). An optional `reviewer` role is reserved in the data model for a later B2B2C or assisted tier. |
| **What must the output show?** | (1) The recommended resolution path; (2) the reasoning — which inputs drove the result and which rule fired; (3) **citations with links to the official IRS source** for every threshold or requirement relied on; (4) the programs excluded and why; (5) urgent deadlines first. See Section 8. |

**Use cases (from the product definition):**

- Taxpayer: *Answer intake*, *Upload documents*, *Confirm extracted data*, *View resolution path*
- System: *Parse documents* (included by Upload), *Evaluate eligibility* (included by Answer intake), *Cite IRS guidelines* (included by View resolution path)
- External: *IRS guidance source* — irs.gov Tax Topics, forms/instructions, and Internal Revenue Manual, referenced by URL in every result
- Reserved: *Tax reviewer* — *Review flagged case* (not active in v1)

### In scope
Federal (IRS) individual and business tax debt resolution. All programs in Section 3.

### Out of scope (v1)
State tax agencies; payroll trust-fund recovery penalty (TFRP) assessments against responsible persons; criminal tax matters; estate/gift tax.

---

## 2. How the IRS thinks about collection

Every program below is derived from one question the IRS asks: **"What is the most we can reasonably collect from this taxpayer before the statute expires, and does collecting it undermine tax administration?"**

Understanding this makes the program list coherent rather than arbitrary:

| Concept | Definition | Why it matters |
|---|---|---|
| **CSED** (Collection Statute Expiration Date) | 10 years from assessment date, per tax period; extendable by bankruptcy, OIC pendency, CDP hearings, out-of-country time, and installment agreement requests | The clock the IRS is collecting against. Every payment plan is compared to "can this be paid before the CSED?" |
| **Collection Financial Standards** | IRS-published allowable living expenses: National Standards (food, clothing, misc, out-of-pocket healthcare) and Local Standards (housing/utilities by county, transportation by region) | The IRS does not accept your actual expenses — it caps them at these standards. Disposable income is computed against allowable, not actual, expenses. |
| **Disposable income** | Monthly gross income − allowable expenses (capped at standards, with documented exceptions) | Determines the monthly payment the IRS expects |
| **Net Realizable Equity (NRE)** | Quick-sale value of assets (typically 80% of fair market value) − secured debt − exemptions | Determines what the IRS could collect by liquidation |
| **Reasonable Collection Potential (RCP)** | NRE + (disposable income × 12 or 24 months) | The IRS's estimate of total collectable amount; the floor for any OIC |
| **Compliance** | All required returns filed; current-year estimated taxes and federal tax deposits paid | No program is available to a non-compliant taxpayer. This is the universal gate. |

**The program ladder** falls out of these definitions:

1. Can you pay in full soon? → **Full pay / short-term plan**
2. Can you pay in full over time with no financial review? → **Guaranteed IA / Simple Payment Plan**
3. Can you pay in full over time, but the debt is large enough that the IRS wants to verify? → **Non-streamlined IA**
4. Can you pay *something* monthly, but not the full balance before the CSED? → **Partial Payment IA**
5. Can you pay nothing without hardship? → **Currently Not Collectible**
6. Is the RCP less than the debt, and would you rather settle than wait out the CSED? → **Offer in Compromise (DATC)**
7. Is the liability itself wrong? → **OIC-DATL, audit reconsideration, amended return**
8. Is the liability correct and collectable, but collection would be inequitable? → **OIC-ETA**
9. Is part of the balance penalties that can be removed? → **Penalty abatement** (stacks with any of the above)
10. Should a spouse be relieved of joint liability? → **Innocent spouse relief** (parallel track)
11. Is the IRS taking enforcement action that needs to stop? → **Levy release, lien relief, CDP/CAP appeals** (parallel track)

---

## 3. Program catalog

Each entry follows the same structure so it maps directly to a rule in Section 6.

### 3.0 Compliance gate (prerequisite, not a program)

| | |
|---|---|
| **What it is** | Universal precondition for every program below |
| **Requirements** | All legally required returns filed (generally the last 6 years per IRS policy, though the IRS can require more); current-year estimated tax payments made if required; current-quarter federal tax deposits made if a business with employees; not in an open bankruptcy proceeding |
| **Data needed** | `returns_filed_all`, `unfiled_years[]`, `current_year_estimated_paid`, `ftd_current` (business), `bankruptcy_status` |
| **Reasoning** | The IRS will not negotiate resolution of old liabilities while new ones are accruing. Bankruptcy triggers an automatic stay that removes the IRS's ability to enter agreements. |
| **Outcome if failed** | `BLOCKER_COMPLIANCE` — route to "file missing returns" workflow; re-enter tree after compliance |

---

### 3.1 Full payment / short-term payment plan

| | |
|---|---|
| **What it is** | Pay the balance within 180 days (individuals). No setup fee. |
| **Requirements** | Compliance gate; ability to pay full balance within 180 days |
| **Data needed** | `total_debt`, `liquid_assets`, `disposable_income` (rough) |
| **Forms** | None (Online Payment Agreement or phone) |
| **Reasoning** | Fastest resolution; interest and failure-to-pay penalty continue until paid. Lowest cost to taxpayer. |
| **Outcome** | `SHORT_TERM_PLAN` |

---

### 3.2 Guaranteed Installment Agreement

| | |
|---|---|
| **What it is** | Statutory right to an installment plan (IRC §6159(c)); IRS *must* accept |
| **Requirements** | Individual (not business); total tax owed ≤ $10,000 (excluding penalties and interest); has filed and paid on time for the prior 5 years with no IA in that period; agrees to pay in full within 3 years; agrees to stay compliant |
| **Data needed** | `taxpayer_type`, `tax_only_balance`, `prior_5yr_compliance`, `prior_ia_in_5yrs` |
| **Forms** | 9465 or Online Payment Agreement |
| **Reasoning** | Congress guaranteed small, first-time debtors a plan without IRS discretion. |
| **Outcome** | `GUARANTEED_IA` |

---

### 3.3 Simple Payment Plan (formerly Streamlined Installment Agreement)

| | |
|---|---|
| **What it is** | Low-disclosure installment plan. As of 2026, the streamlined installment agreement has been phased out; the IRS replaced it with the Simple Payment Plan for individuals in 2025 and extended it to businesses in 2026. |
| **Requirements — individuals** | Compliance gate; assessed balance (tax + penalties + interest) ≤ $50,000; can pay in full by the CSED (most taxpayers may have up to 10 years to pay, subject to the collection statute); no financial disclosure required |
| **Requirements — businesses** | Compliance gate; balance ≤ $50,000 (≤ $25,000 if the debt includes trust fund taxes); can pay in full by the CSED |
| **Data needed** | `taxpayer_type`, `total_debt`, `includes_trust_fund_tax`, `csed_months_remaining`, `proposed_monthly_payment` |
| **Forms** | Online Payment Agreement (individuals); 9465 by mail; businesses call the IRS (business accounts cannot apply online) |
| **Reasoning** | Below these thresholds the IRS has decided verification costs more than it recovers. Direct debit is typically required above $25,000 and avoids a lien filing. |
| **Legacy note** | Older Form 9465 instructions still reference a 72-month term; the applicable term should be confirmed when the agreement is established. Store the term as config, not a constant. |
| **Outcome** | `SIMPLE_PAYMENT_PLAN` (with `variant: individual | business | business_trust_fund`) |

---

### 3.4 Non-Streamlined Installment Agreement (full-pay, verified)

| | |
|---|---|
| **What it is** | Installment plan for balances above Simple Payment Plan thresholds that can still be paid in full before the CSED |
| **Requirements** | Compliance gate; balance > Simple Plan threshold; `disposable_income × csed_months_remaining + NRE ≥ total_debt`; financial disclosure may be required (433-F for ACS, 433-A/B for Revenue Officer); Notice of Federal Tax Lien determination |
| **Data needed** | Full 433 data set (Section 4); `csed_months_remaining` |
| **Forms** | 9465 + 433-F/433-A/433-B |
| **Reasoning** | The IRS wants proof the proposed payment is the most it can get. A lien is likely filed to protect the government's interest. Payments are set at disposable income, not at what the taxpayer proposes. |
| **Outcome** | `NON_STREAMLINED_IA` |

---

### 3.5 Partial Payment Installment Agreement (PPIA)

| | |
|---|---|
| **What it is** | Monthly payments that will *not* pay the balance before the CSED; remaining balance expires at CSED. Authorized by the American Jobs Creation Act of 2004 amending IRC §6159. |
| **Requirements** | Compliance gate; `disposable_income > 0`; `disposable_income × csed_months_remaining + NRE < total_debt`; little to no accessible asset equity — must generally prove no marketable assets or inability to access equity in home or property; full financial disclosure (433-A/433-B); lien determination; agreement is subject to reviews every two years to determine if the financial situation has changed |
| **Data needed** | Full 433 data set; per-period `assessment_date` to derive CSED; asset liquidity/accessibility indicators |
| **Forms** | 9465 + 433-A/433-B |
| **Reasoning** | Between "can pay in full" and "can pay nothing." The IRS takes what it can get monthly rather than forcing OIC or CNC. It is judgment-heavy: the IRS weighs PPIA against liquidating equity or extending the CSED. |
| **Outcome** | `PPIA_CANDIDATE` — **professional referral recommended** |

---

### 3.6 Currently Not Collectible (CNC) / Hardship status

| | |
|---|---|
| **What it is** | IRS suspends active collection (no levies, no payment demands). Debt remains, interest and penalties accrue, CSED keeps running, lien may be filed. |
| **Requirements** | Compliance gate (IRS sometimes grants CNC-hardship without full compliance but expects it); `disposable_income ≤ 0` after allowable expenses; NRE too low to satisfy debt or liquidation would cause hardship; financial disclosure (433-F/433-A) |
| **Data needed** | Full 433 data set |
| **Forms** | 433-F or 433-A (no application form; requested by phone or with a Revenue Officer) |
| **Reasoning** | Collecting would deprive the taxpayer of basic living expenses (economic hardship, Treas. Reg. §301.6343-1). The IRS "parks" the account and rechecks income annually via return filings. If income rises above a set threshold, the account reactivates. |
| **Outcome** | `CNC` |

---

### 3.7 Offer in Compromise — Doubt as to Collectibility (OIC-DATC)

| | |
|---|---|
| **What it is** | Settle for less than owed. Most OICs are Doubt as to Collectibility cases decided by comparing RCP to the balance owed. |
| **Requirements** | Compliance gate (returns filed, estimated payments made, FTDs current for employers); not in open bankruptcy; `RCP < total_debt`; offer amount ≥ RCP; application fee + initial payment (20% for lump sum; first periodic payment for periodic) unless low-income certified (household income ≤ 250% of federal poverty guidelines); 5-year post-acceptance compliance covenant |
| **RCP formula** | `RCP = NRE + (disposable_income × 12)` for lump-sum (pay within 5 months of acceptance); `RCP = NRE + (disposable_income × 24)` for periodic (6–24 months) |
| **Data needed** | Full 433-A (OIC) / 433-B (OIC) data set; household income vs poverty guideline |
| **Forms** | 656 + 433-A (OIC) and/or 433-B (OIC) |
| **Reasoning** | The IRS accepts a certain sum now over an uncertain larger sum later. Acceptance rates are low (roughly 14% in FY2025 per industry analysis), primarily because taxpayers offer below RCP. Offers at or above RCP are generally required to be accepted; offers below RCP are rejected and the deposit kept. |
| **Trade-off vs PPIA/CNC** | OIC pays RCP and closes the case. PPIA/CNC pays less (or nothing) but the debt lingers to the CSED with lien and compliance exposure. Engine should present both when both qualify. |
| **Outcome** | `OIC_DATC_CANDIDATE` |

---

### 3.8 Offer in Compromise — Doubt as to Liability (OIC-DATL)

| | |
|---|---|
| **What it is** | Dispute that the assessed tax is correct. Financials are irrelevant. |
| **Requirements** | Genuine dispute as to the existence or amount of the correct tax debt under the law; taxpayer has not already had (and lost) an opportunity to dispute it (e.g., Tax Court, Appeals); offer ≥ $1; no application fee |
| **Data needed** | `liability_disputed`, `dispute_reason`, `prior_appeal_or_court_on_same_years` |
| **Forms** | 656-L (not 656, no 433) |
| **Reasoning** | It costs the IRS less to compromise a defensible dispute than to litigate. |
| **Related alternatives** | Audit reconsideration (if audit-assessed), amended return (1040-X), Collection Due Process hearing challenging liability if no prior opportunity |
| **Outcome** | `OIC_DATL_CANDIDATE` — **professional referral recommended** |

---

### 3.9 Offer in Compromise — Effective Tax Administration (OIC-ETA)

| | |
|---|---|
| **What it is** | Liability is correct *and* fully collectable, but collection would create economic hardship or would be unfair and inequitable because of exceptional circumstances (Treas. Reg. §301.7122-1(b)(3)) |
| **Requirements** | Compliance gate; `RCP ≥ total_debt` (otherwise it's DATC); documented hardship (e.g., long-term illness, disability, assets are sole means of care for dependents) or compelling public policy/equity considerations; must not undermine compliance; individuals only (not available to businesses) |
| **Data needed** | `hardship_circumstances[]`, `dependents_with_special_needs`, `age`, `medical_conditions`, `asset_is_sole_support` |
| **Forms** | 656 + 433-A (OIC) with narrative explanation |
| **Reasoning** | Rarely accepted; primarily used by elderly, disabled, or taxpayers with special extenuating circumstances. |
| **Outcome** | `OIC_ETA_CANDIDATE` — **professional referral recommended** |

---

### 3.10 Penalty relief — First Time Abate (FTA)

| | |
|---|---|
| **What it is** | Administrative waiver removing Failure-to-File, Failure-to-Pay, and Failure-to-Deposit penalties for one tax period |
| **Requirements** | Penalties eligible: FTF (IRC §6651(a)(1), §6698, §6699), FTP (§6651(a)(2), (a)(3)), FTD (§6656); clean compliance history — no penalties (other than estimated tax penalty) in the prior 3 tax years; all returns filed; paid or arranged to pay any tax due. Considered regardless of penalty amount. |
| **Data needed** | `penalty_breakdown_by_year[]`, `prior_3yr_penalty_history`, `returns_filed_all` |
| **Forms** | Phone request or Form 843; often granted on the spot |
| **Reasoning** | Rewards historically compliant taxpayers for one slip. Note: FTP penalty continues to accrue until tax is paid; requesting FTA after full payment maximizes relief. |
| **Stacking** | Additive to any primary plan. Reduces `total_debt` before other rules run — engine should compute eligibility with and without abatement. |
| **Outcome** | `FTA_ELIGIBLE` (flag) |

---

### 3.11 Penalty relief — Reasonable Cause

| | |
|---|---|
| **What it is** | Penalty removal where the taxpayer exercised ordinary business care but could not comply |
| **Requirements** | Documented cause: death/serious illness of taxpayer or immediate family; natural disaster; inability to obtain records; erroneous IRS advice (written); reliance on a tax professional (limited); other circumstances beyond control. Does **not** include lack of funds alone (for FTP, inability to pay must be despite ordinary care). |
| **Data needed** | `penalty_reason_category`, `supporting_docs_available`, `dates_of_event` |
| **Forms** | Form 843 with written statement |
| **Reasoning** | Penalties exist to encourage compliance; they serve no purpose when noncompliance was unavoidable. |
| **Outcome** | `REASONABLE_CAUSE_CANDIDATE` (flag) |

---

### 3.12 Penalty relief — Statutory exception / other administrative waivers

| | |
|---|---|
| **What it is** | Relief mandated by statute (e.g., disaster declarations, combat zone, erroneous written IRS advice) or announced by IRS notice |
| **Data needed** | `disaster_zone_resident`, `combat_zone`, `irs_written_advice_relied_on` |
| **Outcome** | `STATUTORY_PENALTY_RELIEF` (flag) |

---

### 3.13 Innocent Spouse Relief (Form 8857) — three types

| Type | IRC | Requirements | Effect |
|---|---|---|---|
| **Innocent spouse relief** | §6015(b) | Joint return; understatement due to spouse's erroneous items; requesting spouse did not know and had no reason to know; inequitable to hold liable; filed within 2 years of first collection activity | Relief from understated tax attributable to spouse |
| **Separation of liability** | §6015(c) | Joint return; now divorced, legally separated, widowed, or not living together for 12 months; no actual knowledge of item; filed within 2 years of first collection activity | Allocates understatement between spouses |
| **Equitable relief** | §6015(f) | Does not qualify under (b) or (c); includes *underpayment* (not just understatement); factors: marital status, economic hardship, knowledge, legal obligation, benefit received, compliance, abuse, health; generally must file within CSED (for balance due) or refund statute | Case-by-case relief |

| | |
|---|---|
| **Data needed** | `filed_jointly[]` by year, `marital_status`, `separation_date`, `knowledge_of_item`, `abuse_present`, `first_collection_activity_date` |
| **Forms** | 8857 |
| **Reasoning** | Joint and several liability is harsh when one spouse concealed income or the couple has separated. Note: the IRS **must** contact the other spouse. |
| **Related** | Injured Spouse (Form 8379) — different: recovers the requesting spouse's share of a refund offset to the other spouse's separate debt. Trigger: `refund_offset_to_spouse_debt`. |
| **Outcome** | `INNOCENT_SPOUSE_CANDIDATE` (parallel track, **professional referral recommended**) |

---

### 3.14 Bankruptcy discharge of tax debt (informational routing only)

| | |
|---|---|
| **What it is** | Income tax debt can be discharged in Chapter 7 (or paid over the plan / partially discharged in Chapter 13) if it meets timing tests |
| **Requirements (the "3-2-240 rule")** | Return due date (including extensions) > 3 years before petition; return actually filed > 2 years before petition; tax assessed > 240 days before petition (extended by OIC pendency + 30 days and prior bankruptcy); no fraud or willful evasion; not a trust fund tax; not from a Substitute for Return (in many circuits) |
| **Data needed** | Per-year: `return_due_date`, `return_filed_date`, `assessment_date`, `sfr_flag`, `fraud_flag`; `tax_type` |
| **Reasoning** | Bankruptcy is a court process, not an IRS program. The engine should only surface "some or all of this debt may be dischargeable — consult a bankruptcy attorney," never advise filing. |
| **Outcome** | `BANKRUPTCY_DISCHARGE_POSSIBLE` (informational flag, **always professional referral**) |

---

### 3.15 Collection Statute Expiration (running out the clock)

| | |
|---|---|
| **What it is** | Not a program — a fact. Debt becomes legally uncollectible at the CSED. |
| **Requirements** | Accurate CSED per period, accounting for tolling events |
| **Data needed** | `assessment_date[]`, `tolling_events[]` (bankruptcy dates, prior OIC dates, CDP requests, IA requests, out-of-country > 6 months) |
| **Reasoning** | Informs PPIA and CNC value: if the CSED is close, CNC/PPIA effectively discharges the debt. If far, OIC may be better. |
| **Outcome** | `csed_months_remaining` (derived field feeding other rules); `CSED_IMMINENT` flag if < 24 months |

---

### 3.16 Enforcement relief (parallel track)

| Action | Trigger | Requirements | Form / mechanism |
|---|---|---|---|
| **Levy release — economic hardship** | Active wage/bank levy | Levy prevents meeting basic living expenses (IRC §6343(a)(1)(D)); or IA entered; or CSED passed; or release facilitates collection | Call ACS / Revenue Officer; 433-F to prove hardship |
| **Lien withdrawal** | NFTL filed | Direct-debit IA ≤ $25,000 paying within 60 months or by CSED; or filing was premature/erroneous; or withdrawal facilitates collection | Form 12277 |
| **Lien discharge** (specific property) | Selling property with lien attached | IRS interest protected or property has no equity for IRS | Form 14135 |
| **Lien subordination** | Refinancing | Refinance produces payment to IRS or improves collection | Form 14134 |
| **Collection Due Process (CDP) hearing** | Received Letter 1058 / LT11 (levy) or Letter 3172 (lien) | Request within 30 days of notice; preserves Tax Court review; suspends levy; tolls CSED | Form 12153 |
| **Equivalent hearing** | Missed 30-day CDP window | Request within 1 year; no Tax Court review; does not suspend levy | Form 12153 |
| **Collection Appeals Program (CAP)** | IA rejected/terminated, levy, lien, seizure | Faster than CDP; no court review; available before or after action | Form 9423 |
| **Passport certification reversal** | Debt certified as "seriously delinquent" (threshold indexed; ~$66,000 in 2026) | Enter an IA, OIC, CNC-hardship, innocent spouse request, or CDP hearing; certification reversed within 30 days | Automatic upon qualifying resolution |

**Data needed:** `enforcement_flags[]` (levy, garnishment, lien, seizure notice, passport certification), `notice_type`, `notice_date`, `days_since_notice`

**Outcome:** `URGENT_LEVY_RELEASE`, `LIEN_WITHDRAWAL_ELIGIBLE`, `CDP_WINDOW_OPEN` (with days remaining), `CAP_AVAILABLE`, `PASSPORT_REVERSAL_ON_RESOLUTION`

---

### 3.17 Other routing destinations

| Destination | Trigger | Notes |
|---|---|---|
| **Taxpayer Advocate Service (Form 911)** | Economic hardship + IRS delay/unresponsiveness; systemic issue | Free; independent within IRS |
| **Low Income Taxpayer Clinic** | Income ≤ 250% poverty guideline; dispute or collection issue | Free representation |
| **Audit reconsideration** | Assessment from audit taxpayer disagrees with; new information available | No form; written request with docs |
| **Amended return (1040-X / 1120-X)** | Original return contained errors inflating liability | Must be within refund statute for refund; can reduce balance anytime |
| **Substitute for Return replacement** | IRS filed an SFR (no deductions/credits) | File actual return to replace SFR and reduce assessment |

---

## 4. Canonical data model

### 4.1 Input fields (collected from user)

```yaml
# ---- Identity & segmentation ----
taxpayer_type:            enum [individual, self_employed, business]
business_entity_type:     enum [sole_prop, partnership, s_corp, c_corp, llc] # if business
has_employees:            boolean                                            # if business
filing_status_by_year:    map<year, enum [single, mfj, mfs, hoh, qw]>

# ---- Debt ----
tax_periods:              list of:
    year:                 int
    tax_type:             enum [income_1040, income_1120, payroll_941, payroll_940, excise, other]
    tax_balance:          decimal
    penalty_balance:      decimal
    interest_balance:     decimal
    assessment_date:      date        # drives CSED
    return_filed_date:    date | null
    return_due_date:      date
    assessed_via_sfr:     boolean
    assessed_via_audit:   boolean
    filed_jointly:        boolean
includes_trust_fund_tax:  boolean     # derived from tax_type in [941] but confirm
liability_disputed:       boolean
dispute_reason:           text        # if disputed
prior_appeal_or_court:    boolean     # if disputed

# ---- Compliance ----
returns_filed_all:        boolean
unfiled_years:            list<int>
current_year_estimated_paid: boolean  # self_employed / business
ftd_current:              boolean     # business with employees
prior_5yr_compliance:     boolean     # for guaranteed IA
prior_ia_in_5yrs:         boolean
prior_3yr_penalty_history: boolean    # for FTA
bankruptcy_status:        enum [none, open_ch7, open_ch13, discharged, dismissed]
bankruptcy_petition_date: date | null

# ---- Household & income ----
household_size:           int
zip_code:                 string      # → county → Local Standards
state:                    string
monthly_gross_income:     decimal
income_sources:           list of {type: enum [wages, self_employment, rental, pension, social_security, unemployment, other], amount: decimal}
spouse_income_included:   boolean

# ---- Expenses (monthly) ----
expenses:
    housing_utilities:    decimal
    food_clothing_misc:   decimal
    transportation_ownership: decimal
    transportation_operating: decimal
    healthcare_out_of_pocket: decimal
    health_insurance:     decimal
    taxes_withheld_or_estimated: decimal
    childcare:            decimal
    court_ordered_payments: decimal    # child support, alimony
    secured_debt_payments: decimal
    other_necessary:      decimal
    other_justification:  text

# ---- Assets ----
assets:
    cash_and_bank:        decimal
    investments:          decimal
    retirement_accounts:  decimal
    life_insurance_cash_value: decimal
    real_estate:          list of {fmv: decimal, mortgage_balance: decimal, is_primary_residence: boolean}
    vehicles:             list of {fmv: decimal, loan_balance: decimal, is_necessary: boolean}
    business_assets:      decimal     # equipment, inventory, receivables (business)
    other_assets:         decimal
    asset_liquidity_barrier: text     # "cannot refinance", "spouse co-owns", etc.

# ---- Enforcement ----
enforcement_flags:        list<enum [levy_wage, levy_bank, lien_filed, seizure_notice, passport_certified, none]>
notices_received:         list of {type: enum [CP14, CP501, CP503, CP504, LT11, L1058, L3172, other], date: date}

# ---- Special circumstances ----
hardship_circumstances:   list<enum [serious_illness, disability, elderly, dependent_special_needs, natural_disaster, death_in_family, unemployment, none]>
penalty_reason_category:  enum [none, illness, disaster, records_unavailable, irs_advice, professional_reliance, other]
combat_zone:              boolean
disaster_zone_resident:   boolean
spouse_issues:            list<enum [spouse_concealed_income, separated_or_divorced, abuse, refund_offset_to_spouse_debt, none]>
separation_date:          date | null
first_collection_activity_date: date | null
```

### 4.2 Derived fields (computed, never asked)

```yaml
total_debt:               sum(tax_balance + penalty_balance + interest_balance)
tax_only_balance:         sum(tax_balance)
penalty_only_balance:     sum(penalty_balance)
total_debt_if_penalties_abated: total_debt - penalty_only_balance   # for FTA scenario

# CSED per period, then aggregate
csed_date[period]:        assessment_date + 10 years + tolling_days
csed_months_remaining:    min over periods of months(today → csed_date)   # conservative
csed_imminent:            csed_months_remaining < 24

# Allowable expenses (IRS standards applied)
allowable_housing:        min(expenses.housing_utilities, local_standard_housing[county][household_size])
allowable_food_misc:      national_standard_food_misc[household_size]   # IRS allows full standard regardless of actual
allowable_transport_own:  min(expenses.transportation_ownership, national_standard_ownership[vehicle_count])
allowable_transport_op:   min(expenses.transportation_operating, local_standard_operating[region][vehicle_count])
allowable_healthcare:     max(expenses.healthcare_out_of_pocket, national_standard_healthcare[age_bracket])
allowable_other:          expenses.taxes + expenses.childcare + expenses.court_ordered + expenses.health_insurance + expenses.secured_debt (if necessary) + justified other
allowable_expenses_total: sum of above

disposable_income:        monthly_gross_income - allowable_expenses_total

# Net realizable equity
nre_real_estate:          sum((fmv × 0.80) - mortgage_balance), floor 0 per property
nre_vehicles:             sum((fmv × 0.80) - loan_balance - vehicle_exemption), floor 0
nre_retirement:           retirement_accounts × (1 - early_withdrawal_tax_estimate)   # IRS counts it
nre_cash:                 max(cash_and_bank - 1000, 0)                                # $1,000 exemption in OIC
nre_total:                sum of all NRE components + investments + life_insurance_cash_value + business_assets

# Collection potential
full_pay_capacity:        (disposable_income × csed_months_remaining) + nre_total
rcp_lump_sum:             nre_total + (max(disposable_income, 0) × 12)
rcp_periodic:             nre_total + (max(disposable_income, 0) × 24)
rcp_min:                  min(rcp_lump_sum, rcp_periodic)

# Poverty test
poverty_guideline:        federal_poverty_guideline[household_size][state_group]
low_income_certified:     (monthly_gross_income × 12) <= poverty_guideline × 2.5

# Time windows
days_since_cdp_notice:    today - notice_date where type in [LT11, L1058, L3172]
cdp_window_open:          days_since_cdp_notice <= 30
equivalent_hearing_open:  days_since_cdp_notice <= 365
```

### 4.3 Configuration table (externalized, versioned)

| Key | Value (Sept 2026) | Source | Review cadence |
|---|---|---|---|
| `guaranteed_ia.max_tax_balance` | 10,000 | IRC §6159(c) | Statutory |
| `guaranteed_ia.max_term_months` | 36 | IRC §6159(c) | Statutory |
| `simple_plan.individual.max_balance` | 50,000 | irs.gov Topic 202 | Annual |
| `simple_plan.business.max_balance` | 50,000 | irs.gov | Annual |
| `simple_plan.business_trust_fund.max_balance` | 25,000 | irs.gov | Annual |
| `simple_plan.max_term_months` | to CSED (≤120) | irs.gov | Annual — verify vs 72 in older 9465 |
| `short_term_plan.max_days` | 180 | irs.gov | Annual |
| `direct_debit_required_above` | 25,000 | IRM 5.14 | Annual |
| `oic.quick_sale_factor` | 0.80 | IRM 5.8.5 | Annual |
| `oic.lump_sum_months` | 12 | IRM 5.8.5 | Annual |
| `oic.periodic_months` | 24 | IRM 5.8.5 | Annual |
| `oic.cash_exemption` | 1,000 | IRM 5.8.5 | Annual |
| `oic.low_income_poverty_multiplier` | 2.5 | Form 656 | Annual |
| `oic.application_fee` | (verify current) | Form 656 | Annual |
| `oic.initial_payment_pct_lump` | 0.20 | Form 656 | Statutory |
| `fta.clean_history_years` | 3 | IRM 20.1.1 | Annual |
| `ppia.review_interval_months` | 24 | IRM 5.14.2 | Annual |
| `csed.years` | 10 | IRC §6502 | Statutory |
| `passport.seriously_delinquent_threshold` | ~66,000 | IRC §7345 (indexed) | Annual |
| `bankruptcy.three_year_rule_days` | 1,095 | 11 USC §507(a)(8) | Statutory |
| `bankruptcy.two_year_rule_days` | 730 | 11 USC §523(a)(1)(B) | Statutory |
| `bankruptcy.assessment_rule_days` | 240 | 11 USC §507(a)(8) | Statutory |
| `standards.national.*` | (table) | irs.gov Collection Financial Standards | Annual (April) |
| `standards.local.housing[county][size]` | (table) | irs.gov | Annual (April) |
| `standards.local.transport[region]` | (table) | irs.gov | Annual (April) |
| `poverty_guideline[size][state_group]` | (table) | HHS | Annual (January) |

### 4.4 Document ingestion

The taxpayer uploads two required documents (W-2 or 1099, depending on employment type) and optionally a completed Form 433-A. Parsed values **never feed the rules directly** — they populate the intake fields as pre-filled values, and the taxpayer confirms or corrects them on the *Confirm data* screen before evaluation. This keeps the engine auditable (every input is taxpayer-attested) and tolerant of OCR error.

| Document | Who has it | Fields populated | Transform / caveat |
|---|---|---|---|
| **W-2** (Wage and Tax Statement) | Employed on payroll | `income_sources[type=wages].amount` = Box 1 ÷ 12; `expenses.taxes_withheld_or_estimated` = (Box 2 + Box 4 + Box 6 + Box 17 + Box 19) ÷ 12; `filing_status_by_year` hint from Box 13/marital indicators; employer for `income_sources.source_name` | Annual → monthly conversion. If multiple W-2s, sum. If tax year ≠ current, flag `income_data_stale` and ask "is this still your income?" |
| **1099-NEC / 1099-K / 1099-MISC** | Self-employed / contractor | `income_sources[type=self_employment].amount` = total ÷ 12; sets `taxpayer_type = self_employed` if not already; `current_year_estimated_paid` prompt triggered | **1099 is gross, not net.** Business expenses must come from 433-A Section 7 or the intake's business-expense screen; do not compute `disposable_income` from 1099 alone. Multiple payers → sum. |
| **Form 433-A** (Collection Information Statement) | Taxpayer who has already prepared one | Section 1 → `household_size`, `zip_code`, `state`; Section 2 → employment/`income_sources[wages]`; Section 3 → `assets.cash_and_bank`, `investments`, `retirement_accounts`, `life_insurance_cash_value`, `real_estate[]`, `vehicles[]`; Section 4 → `income_sources[other]`; Section 5 → all `expenses.*`; Sections 6–7 → business income/expenses and `assets.business_assets` | Populates the **entire Layer 2 field set**, so screens 5–7 become confirmation screens rather than entry screens. Check form revision date; older revisions have different section numbering. 433-A (OIC) variant also includes the offer calculation worksheet — ignore worksheet, use raw values. |
| **Form 433-B** (business) | Business taxpayer | Business equivalents of the above | Same confirm-before-use rule |
| **IRS notices** (CP14, CP504, LT11, Letter 1058, Letter 3172) | Any taxpayer who has received them | `notices_received[]` (type, date); `tax_periods[].tax_balance/penalty_balance/interest_balance` from the notice breakdown; `enforcement_flags` inferred from notice type | Notice date drives the CDP 30-day window — highest-value extraction in the set. Not required by the product definition but should be accepted if offered. |
| **Account transcript** (if the taxpayer can pull it from their IRS online account) | Optional | `tax_periods[].assessment_date`, exact balances, penalty codes, `assessed_via_sfr` (TC 150 with SFR indicator), filing dates | Only reliable source of `assessment_date` → CSED. When absent, CSED confidence is `low`. |

**Ingestion pipeline:**

```
upload → classify document type → extract fields (OCR/form parser)
       → map to canonical fields with provenance {source_doc, page, box}
       → present on Confirm-data screen with source shown per field
       → taxpayer edits/accepts → fields marked attested=true → evaluation
```

**Provenance rule:** every canonical field stores `{value, source: enum[user_entered, w2, 1099, 433a, notice, transcript, irs_standard_prefill], attested: boolean}`. The output's reasoning section can then say "based on the wages on your W-2" or "based on the IRS housing standard for your county, which you accepted," which is what makes the citations in Section 8 meaningful.

**When the 433-A is absent:** screens 5–7 collect the same fields, and the system can **generate a completed Form 433-A** (or 433-F) from the attested data as a downloadable artifact for the taxpayer to submit with their application. This turns the intake into the form rather than duplicating it.

---

## 5. The decision tree

### 5.1 Structural overview

The tree has **four layers**. Each layer either terminates, routes to the next, or attaches a flag.

```
LAYER 0 — SEGMENT & GATE
  └─ taxpayer_type
  └─ compliance gate ─────────── fail → BLOCKER_COMPLIANCE (terminal, loop back)
  └─ bankruptcy gate ─────────── open → BLOCKER_BANKRUPTCY (terminal + BANKRUPTCY_DISCHARGE_POSSIBLE flag)
  └─ liability dispute gate ──── disputed → OIC_DATL / AUDIT_RECON / AMENDED_RETURN (parallel track) → continue

LAYER 1 — LOW-DISCLOSURE TIERS (no financial data needed)
  └─ can full-pay ≤180 days? ── yes → SHORT_TERM_PLAN
  └─ guaranteed IA test ──────── pass → GUARANTEED_IA
  └─ simple plan test ────────── pass → SIMPLE_PAYMENT_PLAN
  └─ else → LAYER 2

LAYER 2 — FULL FINANCIAL DISCLOSURE & CAPACITY
  └─ collect 433 data set
  └─ compute: disposable_income, nre_total, csed_months_remaining, full_pay_capacity, rcp_min
  └─ route:
       disposable_income ≤ 0  AND  nre_total < debt ──────────────── → CNC
       disposable_income > 0  AND  full_pay_capacity ≥ debt ───────── → NON_STREAMLINED_IA
       disposable_income > 0  AND  full_pay_capacity < debt  AND  nre_total low → PPIA_CANDIDATE
       rcp_min < debt (any of the above) ──────────────────────────── + OIC_DATC_CANDIDATE
       rcp_min ≥ debt  AND  hardship_circumstances present ────────── + OIC_ETA_CANDIDATE

LAYER 3 — PARALLEL FLAGS (always evaluated, additive)
  └─ penalty relief:      FTA_ELIGIBLE | REASONABLE_CAUSE_CANDIDATE | STATUTORY_PENALTY_RELIEF
  └─ spouse relief:       INNOCENT_SPOUSE_CANDIDATE | INJURED_SPOUSE
  └─ enforcement:         URGENT_LEVY_RELEASE | LIEN_WITHDRAWAL_ELIGIBLE | CDP_WINDOW_OPEN | CAP_AVAILABLE | PASSPORT_REVERSAL_ON_RESOLUTION
  └─ timing:              CSED_IMMINENT | BANKRUPTCY_DISCHARGE_POSSIBLE
  └─ referral:            TAS_REFERRAL | LITC_REFERRAL
```

### 5.2 Why the layers are ordered this way

- **Layer 0 first** because a non-compliant taxpayer cannot use any program — collecting financials would be wasted effort. Liability disputes are checked here because if the debt is wrong, the "right" answer is to fix the debt, not pay it.
- **Layer 1 before Layer 2** because most B2C users (balances under $50k) never need to enter expenses or assets. This is the single biggest UX win: roughly the majority of intake paths terminate here in under two minutes.
- **Layer 2 computes everything once, then routes** rather than asking sequential yes/no questions, because CNC, IA, PPIA, and OIC are all functions of the same three numbers (disposable income, NRE, CSED). Compute once, evaluate all.
- **Layer 3 is additive, not branching**, because penalty relief, spouse relief, and enforcement relief stack on top of any primary plan. They are never mutually exclusive with the primary outcome.

### 5.3 Re-evaluation with penalty abatement

If `FTA_ELIGIBLE` or `REASONABLE_CAUSE_CANDIDATE` fires, the engine **re-runs Layers 1–2 with `total_debt_if_penalties_abated`**. A taxpayer at $54,000 with $6,000 in penalties may drop into Simple Payment Plan territory after abatement. Output both scenarios: "As-is" and "If penalty relief granted."

---

## 6. Rule specification (machine-readable)

Node types: `question` (collect fields), `computed` (derive fields), `rule` (evaluate `evaluate_in_order`, first match wins unless `mode: all_matching`), `terminal`. A `"referral": true` on any branch sets `professional_referral_recommended` on the resulting outcome (§8–9). Every `reason` string has a matching citation in §8.2 keyed by the rule's config reference.

```json
{
  "engine_version": "1.0",
  "config_version": "2026-09",

  "nodes": {

    "L0_segment": {
      "type": "question",
      "fields": ["taxpayer_type", "business_entity_type?", "has_employees?"],
      "next": "L0_debt_snapshot"
    },

    "L0_debt_snapshot": {
      "type": "question",
      "fields": ["tax_periods[]", "liability_disputed", "dispute_reason?", "prior_appeal_or_court?"],
      "next": "L0_compliance"
    },

    "L0_compliance": {
      "type": "question",
      "fields": ["returns_filed_all", "unfiled_years[]?", "current_year_estimated_paid?", "ftd_current?", "bankruptcy_status", "bankruptcy_petition_date?"],
      "next": "L0_gate_rule"
    },

    "L0_gate_rule": {
      "type": "rule",
      "mode": "first_match",
      "evaluate_in_order": [
        { "if": "bankruptcy_status in ['open_ch7','open_ch13']",
          "outcome": "BLOCKER_BANKRUPTCY",
          "flags": ["BANKRUPTCY_DISCHARGE_POSSIBLE"],
          "reason": "Automatic stay prevents IRS from entering agreements; discharge analysis requires attorney" },
        { "if": "returns_filed_all == false",
          "outcome": "BLOCKER_COMPLIANCE",
          "reason": "All required returns must be filed before any resolution program is available",
          "next_action": "file_returns_workflow" },
        { "if": "taxpayer_type != 'individual' AND (current_year_estimated_paid == false OR ftd_current == false)",
          "outcome": "BLOCKER_COMPLIANCE",
          "reason": "Current-period estimated taxes / federal tax deposits must be current" },
        { "else": "L0_dispute_rule" }
      ]
    },

    "L0_dispute_rule": {
      "type": "rule",
      "mode": "all_matching",
      "evaluate_in_order": [
        { "if": "liability_disputed AND prior_appeal_or_court == false",
          "flags": ["OIC_DATL_CANDIDATE"], "referral": true,
          "reason": "Genuine dispute as to amount owed with no prior opportunity to contest" },
        { "if": "liability_disputed AND any(tax_periods.assessed_via_audit)",
          "flags": ["AUDIT_RECONSIDERATION_CANDIDATE"], "referral": true },
        { "if": "any(tax_periods.assessed_via_sfr)",
          "flags": ["SFR_REPLACEMENT_RECOMMENDED"],
          "reason": "Filing actual return replaces IRS substitute and typically lowers assessment" }
      ],
      "next": "L1_compute_basics"
    },

    "L1_compute_basics": {
      "type": "computed",
      "derive": ["total_debt", "tax_only_balance", "penalty_only_balance", "includes_trust_fund_tax", "csed_months_remaining", "csed_imminent"],
      "next": "L1_quick_capacity"
    },

    "L1_quick_capacity": {
      "type": "question",
      "fields": ["can_full_pay_180_days", "proposed_monthly_payment", "prior_5yr_compliance", "prior_ia_in_5yrs"],
      "next": "L1_tier_rule"
    },

    "L1_tier_rule": {
      "type": "rule",
      "mode": "first_match",
      "evaluate_in_order": [
        { "if": "can_full_pay_180_days == true",
          "outcome": "SHORT_TERM_PLAN",
          "reason": "Full payment within 180 days; no setup fee; lowest total cost",
          "next": "L3_flags" },
        { "if": "taxpayer_type == 'individual' AND tax_only_balance <= cfg.guaranteed_ia.max_tax_balance AND prior_5yr_compliance AND NOT prior_ia_in_5yrs AND (total_debt / proposed_monthly_payment) <= cfg.guaranteed_ia.max_term_months",
          "outcome": "GUARANTEED_IA",
          "reason": "Statutory right under IRC 6159(c); IRS must accept",
          "next": "L3_flags" },
        { "if": "taxpayer_type != 'business' AND total_debt <= cfg.simple_plan.individual.max_balance AND (total_debt / proposed_monthly_payment) <= min(cfg.simple_plan.max_term_months, csed_months_remaining)",
          "outcome": "SIMPLE_PAYMENT_PLAN", "variant": "individual",
          "reason": "Below disclosure threshold; no 433 required",
          "next": "L3_flags" },
        { "if": "taxpayer_type == 'business' AND includes_trust_fund_tax AND total_debt <= cfg.simple_plan.business_trust_fund.max_balance AND (total_debt / proposed_monthly_payment) <= min(cfg.simple_plan.max_term_months, csed_months_remaining)",
          "outcome": "SIMPLE_PAYMENT_PLAN", "variant": "business_trust_fund",
          "next": "L3_flags" },
        { "if": "taxpayer_type == 'business' AND NOT includes_trust_fund_tax AND total_debt <= cfg.simple_plan.business.max_balance AND (total_debt / proposed_monthly_payment) <= min(cfg.simple_plan.max_term_months, csed_months_remaining)",
          "outcome": "SIMPLE_PAYMENT_PLAN", "variant": "business",
          "next": "L3_flags" },
        { "else": "L2_full_disclosure",
          "reason": "Balance or term exceeds low-disclosure thresholds; financial verification required" }
      ]
    },

    "L2_full_disclosure": {
      "type": "question",
      "fields": [
        "household_size", "zip_code", "state", "monthly_gross_income", "income_sources[]", "spouse_income_included",
        "expenses.*", "assets.*", "hardship_circumstances[]"
      ],
      "prefill": {
        "expenses.housing_utilities": "cfg.standards.local.housing[county(zip_code)][household_size]",
        "expenses.food_clothing_misc": "cfg.standards.national.food_misc[household_size]",
        "expenses.transportation_operating": "cfg.standards.local.transport[region(zip_code)]",
        "expenses.healthcare_out_of_pocket": "cfg.standards.national.healthcare[age_bracket]"
      },
      "next": "L2_compute_capacity"
    },

    "L2_compute_capacity": {
      "type": "computed",
      "derive": ["allowable_expenses_total", "disposable_income", "nre_total", "full_pay_capacity", "rcp_lump_sum", "rcp_periodic", "rcp_min", "low_income_certified"],
      "next": "L2_primary_rule"
    },

    "L2_primary_rule": {
      "type": "rule",
      "mode": "first_match",
      "evaluate_in_order": [
        { "if": "disposable_income <= 0 AND nre_total < total_debt",
          "outcome": "CNC",
          "reason": "No disposable income after IRS allowable expenses; liquidation would not satisfy debt",
          "next": "L2_secondary_rule" },
        { "if": "disposable_income > 0 AND full_pay_capacity >= total_debt",
          "outcome": "NON_STREAMLINED_IA",
          "reason": "Monthly disposable income over remaining CSED (plus equity) covers full balance",
          "monthly_payment": "disposable_income",
          "next": "L2_secondary_rule" },
        { "if": "disposable_income > 0 AND full_pay_capacity < total_debt AND nre_total < cfg.ppia.equity_materiality_threshold",
          "outcome": "PPIA_CANDIDATE", "referral": true,
          "reason": "Can pay monthly but not full balance before CSED; no accessible equity to bridge gap",
          "monthly_payment": "disposable_income",
          "next": "L2_secondary_rule" },
        { "if": "disposable_income > 0 AND full_pay_capacity < total_debt AND nre_total >= cfg.ppia.equity_materiality_threshold",
          "outcome": "OIC_DATC_CANDIDATE",
          "reason": "Cannot full-pay by CSED and equity exists; IRS will expect equity applied — OIC is the structured path",
          "next": "L2_secondary_rule" },
        { "else": "CNC", "referral": true, "reason": "Unclassified capacity profile — professional referral" }
      ]
    },

    "L2_secondary_rule": {
      "type": "rule",
      "mode": "all_matching",
      "evaluate_in_order": [
        { "if": "rcp_min < total_debt AND bankruptcy_status not in ['open_ch7','open_ch13']",
          "flags": ["OIC_DATC_CANDIDATE"],
          "offer_floor_lump": "rcp_lump_sum", "offer_floor_periodic": "rcp_periodic",
          "reason": "Reasonable collection potential below balance; settlement viable at or above RCP" },
        { "if": "rcp_min >= total_debt AND taxpayer_type != 'business' AND len(hardship_circumstances) > 0",
          "flags": ["OIC_ETA_CANDIDATE"], "referral": true,
          "reason": "Collectible in full but documented hardship may support effective tax administration offer" },
        { "if": "low_income_certified",
          "flags": ["OIC_FEE_WAIVER", "LITC_REFERRAL"] },
        { "if": "csed_imminent",
          "flags": ["CSED_IMMINENT"],
          "reason": "Under 24 months to statute expiration — CNC/PPIA may effectively resolve debt without OIC" }
      ],
      "next": "L3_flags"
    },

    "L3_flags": {
      "type": "question",
      "fields": ["prior_3yr_penalty_history", "penalty_reason_category", "combat_zone", "disaster_zone_resident",
                 "spouse_issues[]", "separation_date?", "first_collection_activity_date?",
                 "enforcement_flags[]", "notices_received[]"],
      "next": "L3_flag_rule"
    },

    "L3_flag_rule": {
      "type": "rule",
      "mode": "all_matching",
      "evaluate_in_order": [
        { "if": "penalty_only_balance > 0 AND prior_3yr_penalty_history == false AND returns_filed_all",
          "flags": ["FTA_ELIGIBLE"], "rerun_with": "total_debt_if_penalties_abated",
          "reason": "Clean 3-year history; FTF/FTP/FTD penalties eligible for administrative waiver" },
        { "if": "penalty_only_balance > 0 AND penalty_reason_category not in ['none']",
          "flags": ["REASONABLE_CAUSE_CANDIDATE"], "rerun_with": "total_debt_if_penalties_abated" },
        { "if": "combat_zone OR disaster_zone_resident",
          "flags": ["STATUTORY_PENALTY_RELIEF"] },
        { "if": "any(tax_periods.filed_jointly) AND intersects(spouse_issues, ['spouse_concealed_income','separated_or_divorced','abuse'])",
          "flags": ["INNOCENT_SPOUSE_CANDIDATE"], "referral": true,
          "sub_type": "derive: 6015(b) if concealed+no knowledge; 6015(c) if separated≥12mo; else 6015(f)",
          "deadline": "first_collection_activity_date + 2 years (for b/c)" },
        { "if": "'refund_offset_to_spouse_debt' in spouse_issues",
          "flags": ["INJURED_SPOUSE_8379"] },
        { "if": "intersects(enforcement_flags, ['levy_wage','levy_bank'])",
          "flags": ["URGENT_LEVY_RELEASE"], "priority": "P0",
          "reason": "Active levy; hardship release or IA entry stops it" },
        { "if": "any(notices_received.type in ['LT11','L1058','L3172']) AND days_since_cdp_notice <= 30",
          "flags": ["CDP_WINDOW_OPEN"], "priority": "P0",
          "days_remaining": "30 - days_since_cdp_notice",
          "reason": "Collection Due Process rights preserve Tax Court review and suspend levy" },
        { "if": "any(notices_received.type in ['LT11','L1058','L3172']) AND days_since_cdp_notice > 30 AND days_since_cdp_notice <= 365",
          "flags": ["EQUIVALENT_HEARING_OPEN"] },
        { "if": "'lien_filed' in enforcement_flags AND primary_outcome in ['GUARANTEED_IA','SIMPLE_PAYMENT_PLAN'] AND total_debt <= cfg.direct_debit_required_above",
          "flags": ["LIEN_WITHDRAWAL_ELIGIBLE"],
          "reason": "Direct-debit IA under $25k qualifies for Form 12277 withdrawal" },
        { "if": "'passport_certified' in enforcement_flags",
          "flags": ["PASSPORT_REVERSAL_ON_RESOLUTION"],
          "reason": "Certification reverses within 30 days of entering IA/OIC/CNC-hardship" },
        { "if": "bankruptcy_status == 'none' AND any(tax_periods where return_due_date + 3y < today AND return_filed_date + 2y < today AND assessment_date + 240d < today AND NOT assessed_via_sfr)",
          "flags": ["BANKRUPTCY_DISCHARGE_POSSIBLE"], "referral": true,
          "reason": "Some periods meet 3-2-240 timing tests; informational only — attorney referral" },
        { "if": "intersects(enforcement_flags, ['levy_wage','levy_bank']) AND disposable_income <= 0",
          "flags": ["TAS_REFERRAL"],
          "reason": "Economic hardship with active enforcement — Taxpayer Advocate Form 911" }
      ],
      "next": "TERMINAL"
    },

    "TERMINAL": {
      "type": "terminal",
      "emit": "EligibilityResult"
    }
  }
}
```

---

## 7. Screen-by-screen intake flow

Each node maps to one screen. Screens use recognition (select/checkbox) over recall (typing) wherever the IRS rule permits.

| # | Screen | Node | Fields | Input pattern | Exit condition |
|---|---|---|---|---|---|
| 1 | Who's the taxpayer? | `L0_segment` | taxpayer_type (+entity, employees if business) | 3 cards → conditional sub-select | always continue |
| 1a | Upload your documents | ingestion (§4.4) | W-2 or 1099 (required, by employment type); 433-A, notices, transcript (optional) | Drag/drop or camera; document type auto-classified; "I don't have this yet" allowed for optional items | always continue; extracted fields pre-fill later screens |
| 2 | What do you owe? | `L0_debt_snapshot` | tax_periods[] (year, type, tax/penalty/interest), liability_disputed | Repeatable year rows pre-filled from notices/transcript if uploaded; "I'll estimate" fallback with single total | always continue |
| 3 | Are you caught up? | `L0_compliance` | returns_filed_all, unfiled_years, estimated/FTD current, bankruptcy | Toggle → conditional year picker; bankruptcy select | **BLOCKER** → exit to file-returns or attorney flow |
| 4 | Quick check | `L1_quick_capacity` | can_full_pay_180, proposed_monthly, 5yr compliance, prior IA | Slider for monthly amount; two toggles | **Terminal for ~majority of users** (SHORT_TERM / GUARANTEED / SIMPLE) → skip to screen 8 |
| 5 | Your household | `L2_full_disclosure` (part 1) | household_size, zip, income sources | Stepper + zip + repeatable income rows; **income pre-filled from W-2/1099**, household from 433-A §1 if present | continue |
| 6 | Monthly expenses | `L2_full_disclosure` (part 2) | expenses.* | **Pre-filled** from 433-A §5 if uploaded, else IRS standards from zip+size; editable; taxes pre-filled from W-2 withholding | continue |
| 7 | What you own | `L2_full_disclosure` (part 3) | assets.* | Category cards pre-filled from 433-A §3; each = value + loan → auto equity; "none" skips | continue |
| 7a | Confirm your data | confirm step (§4.4) | all pre-filled fields with source shown | Per-field: source badge (W-2 / 1099 / 433-A / IRS standard / you) + edit control; must accept to proceed | sets `attested=true` on every field |
| 8 | Anything else going on? | `L3_flags` | penalty history, hardship, spouse issues, enforcement, notices | Three checkbox groups; each checked item reveals ≤1 follow-up | → Results |
| 9 | Results | `TERMINAL` | — | Urgent-deadline banner (if any) → primary plan card with reasoning and **IRS source links** → alternatives → stacked relief → excluded programs with reasons → professional-referral notice (if any) → downloadable generated 433-A/433-F | — |

**Branch coverage:** Screens 5–7a are skipped for anyone terminating at screen 4 (screen 1a still runs because W-2/1099 income confirms the proposed monthly payment is plausible and pre-fills the generated 9465). Screen 8 always runs — penalty and enforcement relief apply to every taxpayer.

**Urgency override:** If screen 8 detects `CDP_WINDOW_OPEN` or `URGENT_LEVY_RELEASE`, the results screen leads with that, not the primary plan.

---

## 8. Output contract

The output is consumed directly by the taxpayer. Every determination — primary, alternative, stacked relief, and exclusion — carries three things: the **reason** (which inputs and which rule), the **inputs it relied on** with their provenance, and **citations** to the official IRS source with a URL. Citations are not free text: each maps to a config key in §4.3, so the source column of the config table is the single place URLs are maintained.

### 8.1 Citation object

```json
{
  "Citation": {
    "source": "IRS Topic No. 202, Tax Payment Options",
    "url": "https://www.irs.gov/taxtopics/tc202",
    "rule_ref": "cfg.simple_plan.individual.max_balance",
    "excerpt_paraphrase": "Simple Payment Plan available when assessed balance is $50,000 or less",
    "retrieved": "2026-09-01",
    "authority": "irs_gov | irm | irc | treas_reg | form_instructions"
  }
}
```

Authority levels let the UI badge sources: statute (IRC) > regulation > Internal Revenue Manual > irs.gov guidance page > form instructions. When two sources conflict (e.g., outdated Form 9465 instructions vs. current irs.gov Simple Payment Plan page), cite the higher authority or the more recent irs.gov page and note the discrepancy in `excerpt_paraphrase`.

### 8.2 Canonical citation map (rule → source)

| Rule / threshold | Primary citation | URL |
|---|---|---|
| Compliance gate, bankruptcy exclusion | Topic 202 | https://www.irs.gov/taxtopics/tc202 |
| Short-term plan (180 days) | Payment plans / OPA page | https://www.irs.gov/payments/online-payment-agreement-application |
| Guaranteed IA | IRC §6159(c); IRM 5.14.5 | https://www.irs.gov/irm/part5/irm_05-014-005 |
| Simple Payment Plan thresholds | Topic 202; Payment plans page | https://www.irs.gov/taxtopics/tc202 |
| Non-streamlined IA, lien determination | IRM 5.14.1 | https://www.irs.gov/irm/part5/irm_05-014-001 |
| PPIA, 2-year review | Topic 202; IRM 5.14.2 | https://www.irs.gov/irm/part5/irm_05-014-002r |
| CNC / hardship | IRM 5.16.1; Treas. Reg. §301.6343-1 | https://www.irs.gov/irm/part5/irm_05-016-001 |
| OIC grounds (DATC/DATL/ETA), forms | Topic 204 | https://www.irs.gov/taxtopics/tc204 |
| OIC RCP calculation, quick-sale, exemptions | IRM 5.8.5 | https://www.irs.gov/irm/part5/irm_05-008-005 |
| OIC ETA standard | IRM 5.8.11; IRM 33.3.2 | https://www.irs.gov/irm/part33/irm_33-003-002 |
| OIC pre-qualifier / low-income | Form 656 Booklet | https://www.irs.gov/forms-pubs/about-form-656 |
| First Time Abate | IRS penalty relief page; IRM 20.1.1.3.3.2.1 | https://www.irs.gov/businesses/small-businesses-self-employed/penalty-relief-due-to-first-time-penalty-abatement-or-other-administrative-waiver |
| Reasonable cause | IRM 20.1.1.3.2 | https://www.irs.gov/irm/part20/irm_20-001-001r |
| Innocent spouse (b/c/f) | IRC §6015; Pub 971; Form 8857 | https://www.irs.gov/forms-pubs/about-form-8857 |
| Injured spouse | Form 8379 | https://www.irs.gov/forms-pubs/about-form-8379 |
| Collection Financial Standards | Standards page | https://www.irs.gov/businesses/small-businesses-self-employed/collection-financial-standards |
| CSED | IRC §6502; IRM 5.1.19 | https://www.irs.gov/irm/part5/irm_05-001-019 |
| CDP / equivalent hearing | Pub 1660; Form 12153 | https://www.irs.gov/forms-pubs/about-form-12153 |
| CAP | Pub 1660; Form 9423 | https://www.irs.gov/forms-pubs/about-form-9423 |
| Lien withdrawal / discharge / subordination | Pub 783, 784; Forms 12277, 14135, 14134 | https://www.irs.gov/businesses/small-businesses-self-employed/understanding-a-federal-tax-lien |
| Levy release | IRC §6343; Pub 594 | https://www.irs.gov/businesses/small-businesses-self-employed/levy |
| Passport certification | IRC §7345 | https://www.irs.gov/businesses/small-businesses-self-employed/revocation-or-denial-of-passport-in-case-of-certain-unpaid-taxes |
| Bankruptcy timing tests | Pub 908 | https://www.irs.gov/forms-pubs/about-publication-908 |
| Taxpayer Advocate | Form 911 | https://www.irs.gov/forms-pubs/about-form-911 |
| Low Income Taxpayer Clinics | Pub 4134 | https://www.irs.gov/forms-pubs/about-publication-4134 |

URLs must be verified at build time and re-verified on the §10 cadence; irs.gov restructures paths periodically. Store URLs in config, not in rule code.

### 8.3 EligibilityResult

```json
{
  "EligibilityResult": {
    "generated_at": "ISO-8601",
    "engine_version": "1.1",
    "config_version": "2026-09",
    "audience": "taxpayer",

    "inputs_summary": {
      "taxpayer_type": "individual",
      "total_debt": 68400.00,
      "penalty_only_balance": 7100.00,
      "csed_months_remaining": 74,
      "csed_confidence": "low",
      "disposable_income": 410.00,
      "nre_total": 3200.00,
      "full_pay_capacity": 33540.00,
      "rcp_lump_sum": 8120.00,
      "rcp_periodic": 13040.00,
      "provenance": {
        "monthly_gross_income": { "value": 4900.00, "source": "w2", "attested": true },
        "expenses.housing_utilities": { "value": 1850.00, "source": "irs_standard_prefill", "attested": true },
        "assets.retirement_accounts": { "value": 4000.00, "source": "433a", "attested": true }
      }
    },

    "urgent": [
      { "flag": "CDP_WINDOW_OPEN", "days_remaining": 11,
        "action": "File Form 12153 before the deadline to preserve appeal rights and stop the levy",
        "reason": "Letter 1058 dated 19 days ago; Collection Due Process requests must be made within 30 days",
        "citations": [ { "source": "Form 12153 instructions", "url": "https://www.irs.gov/forms-pubs/about-form-12153", "rule_ref": "cfg.cdp.window_days", "authority": "form_instructions" } ] }
    ],

    "primary": {
      "outcome": "PPIA_CANDIDATE",
      "display_name": "Partial Payment Installment Agreement",
      "professional_referral_recommended": true,
      "referral_reason": "The IRS weighs a partial-payment plan against selling assets or extending the collection deadline. This is a judgment call an enrolled agent or tax attorney should review before you apply.",
      "reason": "Your disposable income of $410/mo (wages from your W-2 minus IRS-allowed expenses) over the 74 months remaining on the collection statute, plus $3,200 in asset equity, totals $33,540 — below your $68,400 balance. You have no accessible equity to bridge the gap.",
      "rule_fired": "L2_primary_rule[2]",
      "inputs_relied_on": ["monthly_gross_income", "allowable_expenses_total", "csed_months_remaining", "nre_total", "total_debt"],
      "estimated_monthly_payment": 410.00,
      "forms": ["9465", "433-A"],
      "generated_artifacts": ["433-A (pre-filled from your attested data)"],
      "what_happens_next": "The IRS reviews your financials, will likely file a federal tax lien, and re-evaluates your ability to pay every two years.",
      "citations": [
        { "source": "IRS Topic No. 202", "url": "https://www.irs.gov/taxtopics/tc202", "rule_ref": "ppia.definition", "excerpt_paraphrase": "If you cannot full pay by the Collection Statute Expiration Date, a Partial Payment Installment Agreement may be an option; a Collection Information Statement is required", "authority": "irs_gov" },
        { "source": "IRM 5.14.2", "url": "https://www.irs.gov/irm/part5/irm_05-014-002r", "rule_ref": "cfg.ppia.review_interval_months", "excerpt_paraphrase": "PPIAs are reviewed every two years", "authority": "irm" },
        { "source": "Collection Financial Standards", "url": "https://www.irs.gov/businesses/small-businesses-self-employed/collection-financial-standards", "rule_ref": "cfg.standards.*", "excerpt_paraphrase": "Allowable living expenses used to compute disposable income", "authority": "irs_gov" }
      ]
    },

    "alternatives": [
      { "outcome": "OIC_DATC_CANDIDATE",
        "display_name": "Offer in Compromise (Doubt as to Collectibility)",
        "professional_referral_recommended": false,
        "reason": "Your reasonable collection potential ($8,120 lump sum / $13,040 periodic) is well below your balance. An offer at or above that amount meets the IRS's acceptance standard.",
        "offer_floor_lump": 8120.00, "offer_floor_periodic": 13040.00,
        "forms": ["656", "433-A (OIC)"],
        "trade_off": "Pays roughly $8–13k now and closes the case, versus a PPIA paying about $30k over six years with the debt remaining until the statute expires.",
        "fee_waiver": false,
        "citations": [
          { "source": "IRS Topic No. 204", "url": "https://www.irs.gov/taxtopics/tc204", "rule_ref": "oic.datc.definition", "excerpt_paraphrase": "Doubt as to collectibility exists when assets and income are less than the full liability; RCP includes asset value plus future income less allowable expenses", "authority": "irs_gov" },
          { "source": "IRM 5.8.5", "url": "https://www.irs.gov/irm/part5/irm_05-008-005", "rule_ref": "cfg.oic.lump_sum_months, cfg.oic.periodic_months, cfg.oic.quick_sale_factor", "authority": "irm" }
        ] }
    ],

    "stacked_relief": [
      { "flag": "FTA_ELIGIBLE",
        "display_name": "First Time Penalty Abatement",
        "reason": "You have no penalties in the prior three tax years, so $7,100 in failure-to-file and failure-to-pay penalties is eligible for administrative waiver.",
        "impact": "Balance drops to $61,300; re-running the rules with that balance still points to a PPIA.",
        "forms": ["843 or phone request"],
        "citations": [ { "source": "Penalty relief due to First Time Abate", "url": "https://www.irs.gov/businesses/small-businesses-self-employed/penalty-relief-due-to-first-time-penalty-abatement-or-other-administrative-waiver", "rule_ref": "cfg.fta.clean_history_years", "authority": "irs_gov" } ] }
    ],

    "scenario_if_penalties_abated": {
      "total_debt": 61300.00,
      "primary": "PPIA_CANDIDATE",
      "changed": false
    },

    "excluded": [
      { "outcome": "GUARANTEED_IA", "reason": "Your tax-only balance of $61,300 exceeds the $10,000 statutory limit.", "citations": [ { "source": "IRC §6159(c) via IRM 5.14.5", "url": "https://www.irs.gov/irm/part5/irm_05-014-005", "rule_ref": "cfg.guaranteed_ia.max_tax_balance", "authority": "irm" } ] },
      { "outcome": "SIMPLE_PAYMENT_PLAN", "reason": "Your $68,400 balance exceeds the $50,000 threshold.", "citations": [ { "source": "IRS Topic No. 202", "url": "https://www.irs.gov/taxtopics/tc202", "rule_ref": "cfg.simple_plan.individual.max_balance", "authority": "irs_gov" } ] },
      { "outcome": "NON_STREAMLINED_IA", "reason": "Your full-pay capacity of $33,540 before the statute expires is below your balance.", "citations": [ { "source": "IRM 5.14.1", "url": "https://www.irs.gov/irm/part5/irm_05-014-001", "rule_ref": "non_streamlined.full_pay_test", "authority": "irm" } ] },
      { "outcome": "CNC", "reason": "You have positive disposable income ($410/mo) after IRS-allowed expenses.", "citations": [ { "source": "IRM 5.16.1", "url": "https://www.irs.gov/irm/part5/irm_05-016-001", "rule_ref": "cnc.hardship_test", "authority": "irm" } ] },
      { "outcome": "OIC_ETA_CANDIDATE", "reason": "Your collection potential is below your balance, so Doubt as to Collectibility is the correct ground, not Effective Tax Administration.", "citations": [ { "source": "IRS Topic No. 204", "url": "https://www.irs.gov/taxtopics/tc204", "rule_ref": "oic.eta.definition", "authority": "irs_gov" } ] },
      { "outcome": "INNOCENT_SPOUSE_CANDIDATE", "reason": "No joint-return years with spouse-related issues were indicated.", "citations": [ { "source": "Form 8857", "url": "https://www.irs.gov/forms-pubs/about-form-8857", "rule_ref": "innocent_spouse.trigger", "authority": "form_instructions" } ] }
    ],

    "referrals": [
      { "type": "professional", "reason": "PPIA determinations are judgment-based", "suggested": ["enrolled agent", "tax attorney", "CPA"] },
      { "type": "professional", "reason": "CDP deadline within 30 days — consider representation before the hearing" }
    ],

    "reviewer_queue": null,

    "disclaimer": "This is a screening result based on the information you provided and publicly available IRS guidance as of the cited dates. The IRS makes the final eligibility determination. This is not legal or tax advice."
  }
}
```

**Field notes**

- `professional_referral_recommended` replaces the earlier `requires_review` flag. In the self-serve v1, it renders as a notice to the taxpayer; if a reviewer tier is enabled, the same flag also populates `reviewer_queue` with a case ID.
- `rule_fired` and `inputs_relied_on` make every determination reproducible: given the same config version and inputs, the result is deterministic and auditable.
- `citations` on **excluded** programs matter as much as on the primary — "why can't I just do a Simple Payment Plan?" is the most common follow-up question.
- Reasoning text is written to the taxpayer in second person and references their document sources ("wages from your W-2") so the citation chain runs from IRS rule → config → input → document.

---

## 9. Professional-referral triggers and edge cases

v1 has no internal reviewer. The following outcomes are still surfaced to the taxpayer, but with `professional_referral_recommended: true` and a plain-language reason, because they are judgment or legal determinations the engine cannot make on its own. The same list drives `reviewer_queue` if an assisted tier is later enabled.

### Always recommend a professional (never present as ready-to-file)
- `PPIA_CANDIDATE` — IRS weighs against equity liquidation and CSED extension
- `OIC_DATL_CANDIDATE`, `OIC_ETA_CANDIDATE` — narrative-driven, low acceptance
- `INNOCENT_SPOUSE_CANDIDATE` — legal standard, IRS contacts other spouse
- `BANKRUPTCY_DISCHARGE_POSSIBLE` — court process, attorney required
- Any `taxpayer_type == 'business'` with `includes_trust_fund_tax` — TFRP exposure for owners
- `total_debt > cfg.passport.seriously_delinquent_threshold`
- `csed_months_remaining < 12` — timing-sensitive; IA request itself tolls CSED
- `CDP_WINDOW_OPEN` — deadline-driven; representation before the hearing is valuable

### Safe to present as self-serve, ready-to-apply
- `SHORT_TERM_PLAN`, `GUARANTEED_IA`, `SIMPLE_PAYMENT_PLAN` — threshold tests with no IRS discretion; link directly to the Online Payment Agreement
- `FTA_ELIGIBLE` — phone request; script the call
- `CNC` and `NON_STREAMLINED_IA` — present with generated 433-A/433-F and a note that the IRS will verify
- `OIC_DATC_CANDIDATE` — present with the offer floor and the IRS Pre-Qualifier link; recommend (not require) a professional above a configurable balance

### Data-quality edge cases
| Case | Handling |
|---|---|
| User doesn't know assessment dates | Default CSED to `return_filed_date + 10y` with `csed_confidence: low`; recommend transcript pull |
| User estimates single total instead of per-period | Treat as one period; disable FTA (needs per-period penalty), disable bankruptcy test, flag `data_incomplete` |
| Expenses above IRS standards | Accept user value, compute with standard, show delta: "IRS allows $X for housing in your county; you entered $Y. You'll need to justify the difference." |
| Retirement accounts | IRS counts them in NRE (net of tax/penalty). Many users don't expect this — surface explicitly. |
| Spouse income when filing separately | Ask `spouse_income_included`; IRS may still consider household income for expense sharing |
| Self-employed income volatility | Ask for 12-month average; flag if variance > 30% |
| Multiple tax types (1040 + 941) | Compute per type; trust fund portion is never dischargeable, never eligible for individual Simple Plan thresholds |

### Rule conflicts
- If `GUARANTEED_IA` and `SIMPLE_PAYMENT_PLAN` both pass → return `GUARANTEED_IA` (stronger right, IRS cannot refuse)
- If `CNC` and `OIC_DATC` both fire → present both; CNC costs nothing but leaves debt; OIC closes it for RCP
- If `BLOCKER_COMPLIANCE` fires but `URGENT_LEVY_RELEASE` also detected → still show levy release (hardship release doesn't require full compliance) with compliance as next step

---

## 10. Configuration and maintenance

- **All thresholds live in the config table (§4.3), not code.** Statutory values change rarely; administrative values (Simple Plan thresholds, standards tables) change annually.
- **Collection Financial Standards** update each April. Ingest from irs.gov as versioned tables keyed by `effective_date`; the engine uses the version in effect on the evaluation date.
- **Poverty guidelines** update each January (HHS).
- **Program renames**: the engine uses stable internal outcome codes (`SIMPLE_PAYMENT_PLAN`); display names ("Simple Payment Plan", formerly "Streamlined IA") live in a localization layer so IRS rebrands don't touch rules.
- **Rule versioning**: every `EligibilityResult` records `engine_version` + `config_version` so historical determinations are reproducible.
- **Citation URLs** (§8.2) live in config alongside thresholds. Run an automated link check weekly; on a 404 or redirect, update the URL and bump `config_version`.
- **Document parsers** (§4.4) are versioned separately; W-2/1099 box layouts change rarely, but Form 433-A revisions renumber sections. Pin parser to form revision date.
- **Validation cadence**: quarterly review of each §3 entry against irs.gov and the IRM (5.14 for IAs, 5.8 for OIC, 5.16 for CNC, 20.1 for penalties, 25.15 for innocent spouse).

---

## 11. Worked examples

### Example A — Simple Plan, terminates at Layer 1
- Individual, $23,000 total (tax $19,000, pen $2,500, int $1,500), all returns filed, no bankruptcy
- Can't pay in 180 days; proposes $350/mo; had a penalty 2 years ago (fails guaranteed 5-yr test)
- **Path:** L0 pass → L1: not short-term; guaranteed fails (prior penalty); simple: $23k ≤ $50k, $23k/$350 = 66 months ≤ 120 ✓
- **Documents:** W-2 uploaded; wages $5,200/mo confirms $350/mo proposal is plausible
- **Output:** `SIMPLE_PAYMENT_PLAN` with citation to Topic 202 and the OPA link; excluded FTA (penalty in 3-yr window) with citation to the FTA page; no flags. Screens 5–7a skipped.

### Example B — CNC with OIC alternative
- Self-employed, $41,000 total, all filed, no bankruptcy. Proposes $100/mo → $41k/$100 = 410 months > CSED → fails simple plan → Layer 2
- Household 3, income $3,100/mo; allowable expenses (standards) $3,400 → disposable −$300; assets: $800 cash, car $6k FMV / $7k loan → NRE ≈ 0
- **Path:** L2: disposable ≤ 0, NRE < debt → `CNC`; secondary: RCP = 0 + 0 = $0 < $41k → `OIC_DATC_CANDIDATE` (offer floor effectively minimum offer); low-income certified → fee waiver
- **Output:** Primary `CNC`; alternative `OIC_DATC` with fee waiver; `LITC_REFERRAL`. Reasoning explains CNC leaves debt on books to CSED vs OIC closes it.

### Example C — Business with trust fund, blocked then routed
- S-corp, $31,000 owed on 941s (trust fund), Q2 deposits missed
- **Path:** L0 gate: `ftd_current == false` → `BLOCKER_COMPLIANCE`
- **Output:** Blocker with next action "bring current-quarter deposits current, then re-run"; note that on re-run $31k > $25k trust-fund threshold → Layer 2 required; `professional_referral_recommended` for TFRP exposure.

---

## 12. Disclaimers and validation requirements

- This engine screens; it does not determine. Every result must carry language that the IRS makes final eligibility decisions.
- Citations link to official IRS sources but paraphrase rather than reproduce them; the taxpayer is directed to the source for authoritative text.
- v1 is self-serve. Outcomes marked for professional referral are recommendations to the taxpayer, not a substitute for representation.
- Thresholds cited reflect publicly available IRS guidance as of September 2026 and must be validated against irs.gov and the Internal Revenue Manual before production use, and on the cadence in §10.
- Bankruptcy and innocent spouse analyses are legal determinations; the engine surfaces possibilities only.
- Nothing in this document constitutes legal or tax advice. The product should be reviewed by an enrolled agent, CPA, or tax attorney before launch, and the rule set should be re-reviewed whenever the IRS revises Publication 594, Form 656 instructions, Form 9465 instructions, or the Collection Financial Standards.
