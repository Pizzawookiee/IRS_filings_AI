# Build prompt — IRS Resolve v1

Paste everything below the line into Claude Code (or another coding agent) from the root of an empty repository that contains the three documents in `docs/`.

---

You are building **IRS Resolve v1**, a self-serve eligibility screening engine for IRS tax-debt resolution programs. Three documents in `docs/` fully specify it. Read all three before writing any code, in this order:

1. `docs/irs-resolution-eligibility-architecture.md` — **what** the system does: the program catalog (§3), data model (§4), decision tree (§5), machine-readable rules (§6), screen flow (§7), output contract with citations (§8), referral rules (§9), worked examples (§11)
2. `docs/irs-resolve-implementation-plan.md` — **how** to build it: package layout, Pydantic models, derive functions, YAML rule format, expression language, engine steps, CLI, Streamlit demo, implementation order, acceptance checklist
3. `docs/irs-resolve-use-cases.md` — **who does what**: 16 use cases with main/alternate flows, and a traceability matrix tying each to architecture sections, implementation modules, fixtures, and acceptance items

Where the documents disagree, the implementation plan wins on code structure, the architecture doc wins on rule logic and thresholds, and the use case doc wins on user-facing flow. If a conflict is material, stop and report it rather than guessing.

## Decisions already made — do not revisit

- Python 3.11+, Pydantic v2, PyYAML, Streamlit. No other runtime dependencies. No CEL library; the expression evaluator is a Python `ast` whitelist as specified in the implementation plan.
- Rules are YAML in `irsresolve/rules/`. Thresholds, citations, outcomes, poverty guidelines, and standards tables are config in `irsresolve/config/`. **No threshold, URL, display name, or form number appears in Python.**
- v1 is self-serve. There is no reviewer queue. `professional_referral_recommended` renders as a notice to the taxpayer.
- Document parsing is stubbed: `ingest/fixtures.py` reads JSON shaped like real W-2 / 1099 / 433-A / notice extractions and emits `ProposedFacts` with provenance. Do not build OCR.
- The engine never makes network calls. Citations are static strings from config.
- Every fact the engine evaluates must be `attested=True`. Reject otherwise, before any rule runs.

## Build in this order and stop after each step to run its check

Follow the implementation plan's "Implementation order" (steps 1–11). After each step:

- Run `pytest` — everything so far passes
- Run `python -m irsresolve.cli validate` once rules exist — zero errors
- Run `python -m irsresolve.cli fixtures` once the engine exists — expected outcomes match
- Report in one paragraph what you built, what the check showed, and what's next. Do not proceed if a check fails.

Concretely:

1. **Models and config loader** — `core/facts.py`, `core/result.py`, `core/config.py`, `core/errors.py`. Write `config/thresholds.yaml`, `config/citations.yaml`, `config/outcomes.yaml`, `config/poverty.yaml`. Populate `citations.yaml` from architecture §8.2 (all 25 rows) and `thresholds.yaml` from §4.3. Ship standards CSVs for **one state (Massachusetts)** plus national tables, with a state-median fallback for unknown counties.
2. **Derive functions** — `core/derive.py`, one function per field in architecture §4.2, each with a docstring citing its source and a unit test whose expected value is computed by hand in the test. Use the §11 worked examples as test inputs.
3. **Expression evaluator** — parse-time path validation against the `Facts`/`DerivedFacts` schema and config keys; runtime evaluation with whitelisted AST nodes only; tests that prove function calls, imports, comprehensions, and out-of-namespace attribute access are rejected.
4. **Rules** — translate architecture §6 into `rules/l0_gate.yaml`, `rules/l1_tiers.yaml`, `rules/l2_capacity.yaml`, `rules/l3_flags.yaml` using the YAML shape in the implementation plan. Every outcome rule in L1 and L2_primary has `reason_if_excluded`. Every rule has ≥1 citation key and a `uses` list. Reasons are second person and reference the taxpayer's numbers and document sources.
5. **Engine** — `core/engine.py` implementing the ten steps, including `NEEDS_FINANCIAL_DISCLOSURE`, the `rerun_with` scenario, `excluded` assembly, and the per-rule `trace` with resolved values. Determinism test: evaluate a fixture twice, assert byte-identical JSON minus `generated_at`.
6. **Fixtures** — `fixtures/case_a_simple_plan.json`, `case_b_cnc_oic.json`, `case_c_business_blocked.json`, `case_d_ppia_cdp.json`, matching architecture §11 and the use case traceability matrix. Include a `_expected` block per fixture (`primary`, `alternatives`, `flags`, `urgent`) that `cli fixtures` asserts against.
7. **Ingestion** — `ingest/base.py` (`DocumentParser` protocol, `merge()`), `ingest/fixtures.py`. Include sample document JSONs for fixture D (W-2 + notice) and fixture B (1099 + 433-A). Test that 1099 parsing never populates expenses.
8. **Renderer and CLI** — `render/markdown.py`, `cli.py` with `run`, `validate`, `fixtures`, `--json | --md`, `--trace`. Test that every determination in every fixture's output has ≥1 citation with a URL.
9. **Demo** — `demo/app.py`: screens per architecture §7 (1 → 1a → 2 → 3 → 4 → [5 → 6 → 7 → 7a] → 8 → results), "Load example" buttons for A–D, source badges on the confirm screen, results rendered from the Markdown renderer, "Why?" expander showing the trace. In-memory session state only.

## Acceptance

You are done when every item in the implementation plan's **Acceptance checklist** passes and you have shown the output of:

```
pytest -q
python -m irsresolve.cli validate
python -m irsresolve.cli fixtures
python -m irsresolve.cli run fixtures/case_d_ppia_cdp.json --md --trace
```

and confirmed `streamlit run irsresolve/demo/app.py` loads fixture D and renders results with the CDP banner on top.

## Working rules

- Write the test before or alongside each module, not after.
- When the architecture doc gives a formula (RCP, NRE, disposable income, CSED), implement it exactly as written and cite the section in the docstring. Do not "improve" it.
- When a threshold or URL is marked "(verify current)" in §4.3, use the value as given and add a `# TODO verify` in the YAML comment; do not look it up.
- Keep functions small and pure. The engine is the only module with control flow over layers.
- No placeholder implementations, no `pass`, no `NotImplementedError` in shipped code paths. If something is out of scope, it is not in the code.
- If you find a rule in architecture §6 that cannot be expressed in the evaluator's whitelisted grammar, add the smallest possible helper (e.g., `any_period(...)`) to the four allowed helpers, document it in the implementation plan's expression-language section, and tell me.
- Do not add features, screens, outcomes, or dependencies not in the three documents.

Begin with step 1. Read the three documents first and confirm in two or three sentences that you understand the layer order, the attestation invariant, and the config-not-code rule before writing anything.
