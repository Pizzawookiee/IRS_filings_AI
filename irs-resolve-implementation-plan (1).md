**IRS Resolve — MVP Implementation Plan**

Companion to `irs-resolution-eligibility-architecture.md` (v1.1). That document is the *what*; this is the *how* for a demo that runs end-to-end on a laptop in under a second.

**Core principles**

- Rules are data, not code paths. Every rule declares `when`, `outcome`, `reason`, and `citations`. The engine is a generic evaluator; adding a program means adding a rule, never editing the engine.
- No inference. Every fact the rules consume is either user-attested or derived by a named, unit-tested function from attested facts. Document parsers only *propose* values; the user confirms.
- Every threshold and every URL lives in config (`YAML`), never in Python. Rules reference config by key (`cfg.simple_plan.individual.max_balance`), and the citation for a rule is looked up by that same key.
- Determinism: same `facts` + same `config_version` → byte-identical `EligibilityResult`. No network calls at evaluation time.
- Strong typing: all inputs, derived facts, rules, and outputs are Pydantic v2 models. Invalid facts fail before evaluation with a field path.
- Layered evaluation, first-match within a layer, all-match for flag layers. The layer order is fixed and mirrors the architecture doc (§5).

**Scope of MVP**

- Core: `Facts` model, `derive()` functions, `Rule` model, `Engine.evaluate()`
- Config: `thresholds.yaml`, `citations.yaml`, `standards/` (National + Local Standards as CSV, one state's counties is enough for demo), `poverty.yaml`
- Rules: all Layer 0–3 rules from architecture §6 (≈35 rules), authored in `rules/*.yaml`
- Ingestion: JSON-in only. Parsers for W-2 / 1099 / 433-A are **stubbed** as fixture loaders that emit `ProposedFacts` with provenance. Real OCR is post-MVP.
- Output: `EligibilityResult` (JSON) + a Markdown renderer for the demo
- Demo: CLI (`irsresolve run fixtures/case_b.json`) and a single-page Streamlit app with the intake screens, confirm step, and results

**Out of MVP**

- OCR / document parsing, IRS transcript retrieval, e-file or OPA submission, 433-A PDF generation, user accounts, persistence, reviewer queue, i18n

---

**Package layout**

```
irsresolve/
  core/
    facts.py        # Facts, ProposedFacts, Provenance (Pydantic)
    derive.py       # pure functions: facts → DerivedFacts
    rules.py        # Rule, Layer, Outcome models + loader
    engine.py       # Engine.evaluate(facts, config) → EligibilityResult
    result.py       # EligibilityResult, Determination, Citation
    config.py       # Config loader, version stamping, key resolution
    errors.py
  config/
    thresholds.yaml
    citations.yaml
    poverty.yaml
    standards/national.csv
    standards/local_housing.csv
    standards/local_transport.csv
  rules/
    l0_gate.yaml
    l1_tiers.yaml
    l2_capacity.yaml
    l3_flags.yaml
  ingest/
    base.py         # DocumentParser protocol → ProposedFacts
    fixtures.py     # W2Fixture, F1099Fixture, F433AFixture (read JSON, tag provenance)
  render/
    markdown.py     # EligibilityResult → Markdown
  cli.py
  demo/app.py       # Streamlit
fixtures/
  case_a_simple_plan.json
  case_b_cnc_oic.json
  case_c_business_blocked.json
  case_d_ppia_cdp.json
tests/
```

---

**Facts (irsresolve/core/facts.py)**

- `Provenance`: `source: Literal["user", "w2", "1099", "433a", "notice", "irs_standard_prefill"]`, `attested: bool`, `ref: str | None` (e.g. `"W-2 Box 1"`)
- `Attested[T]`: generic wrapper `{value: T, provenance: Provenance}`. Every leaf field in `Facts` is `Attested[...]`.
- `TaxPeriod`: `year: int`, `tax_type: Literal[...]`, `tax: Decimal`, `penalty: Decimal`, `interest: Decimal`, `assessment_date: date | None`, `return_filed_date: date | None`, `return_due_date: date`, `filed_jointly: bool`, `assessed_via_sfr: bool`, `assessed_via_audit: bool`
- `Facts`: the full §4.1 input field set, grouped as nested models: `identity`, `debt`, `compliance`, `quick_capacity`, `household`, `income`, `expenses`, `assets`, `enforcement`, `circumstances`
  - Layer 2 groups (`household`, `income`, `expenses`, `assets`) are `Optional`; the engine stops at Layer 1 when they are absent and the tier rules terminate
  - `model_config = ConfigDict(extra="forbid")`
- `ProposedFacts`: same shape as `Facts` but every field optional and `attested=False`. Produced by parsers; merged into `Facts` only after the confirm step sets `attested=True`
- Validation: `Facts.model_validate()` raises `FactsError` with the JSON path on the first invalid field; `Engine.evaluate()` additionally rejects any leaf with `attested=False`

**Derived facts (irsresolve/core/derive.py)**

- One pure function per derived field, signature `fn(facts: Facts, cfg: Config) -> T`, registered in an ordered dict so dependencies resolve top-down
- MVP set (from architecture §4.2): `total_debt`, `tax_only_balance`, `penalty_only_balance`, `total_debt_if_penalties_abated`, `includes_trust_fund_tax`, `csed_months_remaining`, `csed_confidence`, `csed_imminent`, `allowable_expenses_total` (applies standards caps), `disposable_income`, `nre_total` (quick-sale factor, exemptions), `full_pay_capacity`, `rcp_lump_sum`, `rcp_periodic`, `rcp_min`, `low_income_certified`, `days_since_cdp_notice`, `cdp_window_open`, `equivalent_hearing_open`
- Layer 2 derivations return `None` when Layer 2 facts are absent; rules that reference them are skipped, not failed
- `DerivedFacts` is a Pydantic model; `derive_all(facts, cfg) -> DerivedFacts`
- Each function has a docstring citing the IRM/IRC source of the formula, and a unit test with a hand-computed expected value

---

**Rules (irsresolve/core/rules.py + rules/*.yaml)**

- `Rule` (Pydantic)
  - `id: str` — unique, e.g. `L1.simple_plan.individual`
  - `layer: Literal["L0", "L1", "L2_primary", "L2_secondary", "L3"]`
  - `when: str` — boolean expression (see expression language below)
  - `outcome: str | None` — outcome code for terminal/primary rules (e.g. `SIMPLE_PAYMENT_PLAN`)
  - `flags: list[str] = []` — for additive rules
  - `variant: str | None`
  - `referral: bool = False` — sets `professional_referral_recommended`
  - `priority: Literal["P0", "P1", "P2"] = "P2"` — P0 renders in the `urgent` block
  - `reason: str` — template with `{placeholders}` resolved against facts, derived, and cfg; written in second person
  - `citations: list[str]` — keys into `citations.yaml`
  - `uses: list[str]` — fact/derived paths relied on (populates `inputs_relied_on`); validated at load time against the `Facts`/`DerivedFacts` schema
  - `rerun_with: str | None` — derived field name to substitute for `total_debt` on re-evaluation (FTA scenario)
  - `next: str | None` — for L0/L1 rules only: `"stop"` (terminal) or `"L2"`
- `Layer` semantics
  - `L0`, `L1`, `L2_primary`: first match wins; matched rule's `outcome` becomes `primary` (or a `BLOCKER_*`). If `next == "stop"`, evaluation jumps to `L3`
  - `L2_secondary`, `L3`: all matching rules fire; results append to `alternatives`, `stacked_relief`, or `urgent` based on outcome/flag kind and priority
  - Exclusions: every rule with an `outcome` in `L1` and `L2_primary` that did *not* match is emitted in `excluded` with its `reason_if_excluded` template (each such rule must declare one)
- Expression language
  - MVP: a tiny safe evaluator over a whitelisted AST — comparison, boolean ops, `in`, `not in`, arithmetic, attribute/index access, and four helpers: `any(list_expr)`, `len()`, `intersects(a, b)`, `min()/max()`
  - Namespaces: `f.` (facts, unwrapped values), `d.` (derived), `cfg.` (config)
  - Implemented with Python `ast` + a node whitelist; no `eval` of raw strings, no function calls outside the four helpers
  - Compile-time: parse and whitelist check on load; every `f.`/`d.`/`cfg.` path must exist in the schema/config. Runtime: type errors raise `RuleRuntimeError` with `rule_id`
  - Rationale vs CEL: keeps the demo dependency-free; the evaluator is ~120 lines. Swap to CEL post-MVP if rules need to be authored outside Python

- Example rule (YAML)

```yaml
- id: L1.simple_plan.individual
  layer: L1
  when: >
    f.identity.taxpayer_type != "business"
    and d.total_debt <= cfg.simple_plan.individual.max_balance
    and (d.total_debt / f.quick_capacity.proposed_monthly_payment)
        <= min(cfg.simple_plan.max_term_months, d.csed_months_remaining)
  outcome: SIMPLE_PAYMENT_PLAN
  variant: individual
  next: stop
  uses: [identity.taxpayer_type, total_debt, quick_capacity.proposed_monthly_payment, csed_months_remaining]
  reason: >
    Your balance of ${d.total_debt:,.0f} is at or under the ${cfg.simple_plan.individual.max_balance:,.0f}
    threshold, and ${f.quick_capacity.proposed_monthly_payment:,.0f}/month pays it off in
    {d.total_debt / f.quick_capacity.proposed_monthly_payment:.0f} months, within the collection statute.
    No financial statement is required.
  reason_if_excluded: >
    Your balance of ${d.total_debt:,.0f} exceeds the ${cfg.simple_plan.individual.max_balance:,.0f}
    threshold, or your proposed payment would not clear it before the collection statute expires.
  citations: [simple_plan.individual.max_balance, simple_plan.definition]
```

---

**Config (irsresolve/core/config.py + config/*)**

- `thresholds.yaml`: nested keys exactly as referenced by rules (`simple_plan.individual.max_balance: 50000`). Top-level `config_version: "2026-09"` and `effective_date`
- `citations.yaml`: map of citation key → `{source, url, authority, excerpt_paraphrase, retrieved}`. Every `cfg.*` key used by a rule must have a citation with the same key *or* the rule must list an explicit citation key; the loader enforces this
- `standards/*.csv`: `national.csv` (household_size → food_misc, healthcare_under65, healthcare_65plus), `local_housing.csv` (state, county, household_size → amount), `local_transport.csv` (region → operating cost, plus national ownership). Ship one full state for the demo; unknown county falls back to state median with `provenance.ref = "state_fallback"`
- `poverty.yaml`: household_size → annual guideline, by `contiguous | alaska | hawaii`
- `Config.load(path) -> Config`: parses, validates that every key referenced by any loaded rule resolves, stamps `config_version`
- Authority enum: `irc > treas_reg > irm > irs_gov > form_instructions`

---

**Engine (irsresolve/core/engine.py)**

- `Engine(config: Config, rules: list[Rule])`
- `evaluate(facts: Facts) -> EligibilityResult`
  1. Reject any unattested leaf → `FactsError`
  2. `derived = derive_all(facts, cfg)`
  3. Run `L0` first-match. If `BLOCKER_*` → skip to step 7 with `primary = blocker`
  4. Run `L1` first-match. If matched rule `next == "stop"` → skip to step 7
  5. Require Layer 2 facts present, else return `NEEDS_FINANCIAL_DISCLOSURE` with the list of missing field groups (the UI uses this to show screens 5–7)
  6. Run `L2_primary` first-match, then `L2_secondary` all-match
  7. Run `L3` all-match
  8. If any fired rule has `rerun_with`, re-run steps 3–6 with the substituted `total_debt`; store as `scenario_if_penalties_abated` with `changed: bool`
  9. Assemble `excluded` from non-matching outcome rules in L1/L2_primary
  10. Resolve reason templates and citations; sort `urgent` by priority; stamp versions; return
- Trace: `evaluate()` also returns `trace: list[RuleTrace]` — `{rule_id, layer, matched: bool, when_resolved: str}` with the expression rendered against actual values (e.g. `68400 <= 50000 → False`). The demo shows this under an "Why?" expander. This is the single most useful debugging and trust feature; do not cut it.

**Result (irsresolve/core/result.py)**

- `Citation`: `key`, `source`, `url`, `authority`, `excerpt_paraphrase`, `retrieved`
- `Determination`: `outcome`, `display_name`, `variant`, `reason`, `rule_id`, `inputs_relied_on: list[str]`, `citations: list[Citation]`, `professional_referral_recommended: bool`, `referral_reason`, `forms: list[str]`, `extras: dict` (monthly payment, offer floors, days remaining)
- `EligibilityResult`: `generated_at`, `engine_version`, `config_version`, `inputs_summary` (derived facts + provenance map), `urgent: list[Determination]`, `primary: Determination`, `alternatives`, `stacked_relief`, `excluded`, `scenario_if_penalties_abated`, `referrals`, `disclaimer`, `trace`
- Display names, forms, and `what_happens_next` per outcome code live in `config/outcomes.yaml`, not in rules

---

**Ingestion (irsresolve/ingest/)**

- `DocumentParser` protocol: `parse(path) -> ProposedFacts`
- MVP implementations read JSON fixtures shaped like the real extraction targets (W-2 boxes, 1099 totals, 433-A sections) and map them per architecture §4.4, tagging provenance (`source="w2", ref="Box 1"`)
- `merge(proposed: ProposedFacts, confirmed: dict) -> Facts` — the confirm step. Fields the user accepted or edited become `attested=True`; anything else is dropped
- 1099 mapping sets income only; it never touches expenses (gross-not-net rule)

**Render (irsresolve/render/markdown.py)**

- `to_markdown(result) -> str`: urgent banner → primary card (reason, forms, next steps, citations as links) → alternatives → stacked relief → "Why not…" excluded list → referral notice → disclaimer
- Every citation renders as `[Source](url)` with an authority badge

**CLI (irsresolve/cli.py)**

- `irsresolve run <facts.json> [--config config/] [--rules rules/] [--json | --md] [--trace]`
- `irsresolve validate` — loads config + rules, reports unresolved keys, missing citations, unparseable expressions, exit code 1 on any
- `irsresolve fixtures` — runs all fixtures and asserts expected primary outcomes (used as the smoke test)

**Demo (irsresolve/demo/app.py)**

- Streamlit, single file, in-memory session state
- Pages: Segment → Debt → Compliance → Quick check → *(conditional)* Household / Expenses (pre-filled from standards by zip + size) / Assets → Upload (fixture picker standing in for real upload) → Confirm (table with source badge + edit) → Results (Markdown render + trace expander)
- "Load example" buttons for the four fixtures so the demo runs in one click
- No backend; `Engine` runs in-process

---

**Implementation order**

1. `facts.py`, `result.py` models; `config.py` loader with key resolution; `errors.py`
2. `thresholds.yaml`, `citations.yaml`, `outcomes.yaml`, `poverty.yaml`, one state of standards CSVs
3. `derive.py` with unit tests against hand-computed values (use architecture §11 examples as test cases)
4. Expression evaluator (AST whitelist) with parse-time path validation
5. `rules.py` loader; author `l0_gate.yaml`, `l1_tiers.yaml`
6. `engine.py` steps 1–4, 7, 9–10; fixture A (Simple Plan) passes
7. `l2_capacity.yaml`, engine steps 5–6; fixtures B (CNC + OIC) and D (PPIA) pass
8. `l3_flags.yaml`, engine step 8 (rerun); fixture D shows CDP urgent + FTA scenario; fixture C shows blocker
9. `render/markdown.py`, `cli.py`
10. `ingest/fixtures.py`, `merge()`
11. `demo/app.py`

**Acceptance checklist**

- `irsresolve validate` passes: every `cfg.*` and `f.`/`d.` path in every rule resolves; every rule has ≥1 citation; every outcome rule has `reason_if_excluded`
- `irsresolve fixtures` passes: A → `SIMPLE_PAYMENT_PLAN`; B → `CNC` primary, `OIC_DATC_CANDIDATE` alternative, `LITC_REFERRAL`; C → `BLOCKER_COMPLIANCE`; D → `PPIA_CANDIDATE` with referral, `CDP_WINDOW_OPEN` in `urgent`, `FTA_ELIGIBLE` with rerun scenario
- Same fixture evaluated twice produces byte-identical JSON (excluding `generated_at`)
- Layer 1 termination: fixture A produces no Layer 2 derived fields and `trace` shows no L2 rules evaluated
- Missing Layer 2 facts with no L1 terminal → `NEEDS_FINANCIAL_DISCLOSURE` listing missing groups, not an exception
- Any `attested=False` leaf → `FactsError` with JSON path before any rule runs
- Every `Determination` (including each `excluded` entry) has ≥1 citation with a URL
- Reason strings resolve all placeholders; an unresolved `{placeholder}` fails `validate`
- Changing `simple_plan.individual.max_balance` in YAML changes fixture outcomes with no Python edit
- Expression evaluator rejects function calls, imports, attribute access outside `f./d./cfg.`, and comprehensions
- Trace shows resolved values for every evaluated rule
- Streamlit demo loads each fixture and renders results in < 1 s
- Unit tests for every `derive.py` function; integration test per fixture

**Non functional requirements**

- Performance: full evaluation < 50 ms; config + rules load < 200 ms (cache in process)
- No network at evaluation time; citations are static strings
- Observability: `trace` on every result; CLI `--trace` prints it; log rule load counts and config version at startup
- Extensibility: new program = new YAML rule + `outcomes.yaml` entry + `citations.yaml` entry; new derived fact = one function + test; real OCR = implement `DocumentParser`
- Safety: results carry the disclaimer string from config; referral outcomes never render a "file now" call to action
