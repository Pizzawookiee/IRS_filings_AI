# IRS Resolve v1

[![CI](https://github.com/ShaurjyaContributes/IRS_filings_AI/actions/workflows/ci.yml/badge.svg)](https://github.com/ShaurjyaContributes/IRS_filings_AI/actions/workflows/ci.yml)

A self-serve **eligibility screening engine** for IRS tax-debt resolution programs. A taxpayer
answers a short questionnaire, uploads the required financial statement, and may add supporting
documents; the engine runs their
attested facts through IRS collection rules and returns the programs they likely qualify for — and
the ones they don't — each with plain-language reasoning, the taxpayer's own numbers, and links to
the governing IRS source.

> **It screens, routes, and explains. It does not determine.** The IRS makes final eligibility
> decisions. Every result carries that disclaimer. This is not legal or tax advice.

Built end-to-end from five specification documents (in the repo root):
`irs-resolution-eligibility-architecture` (the *what*), `irs-resolve-implementation-plan` (the
*how*), `irs-resolve-use-cases` (the *who does what*), `irs-resolve-ui-design` (the UI shell), and
`irs-resolve-build-prompt` (the build contract).

---

## Table of contents

- [What it does](#what-it-does)
- [Programs it screens for](#programs-it-screens-for)
- [Design principles](#design-principles)
- [How it works — the four-layer engine](#how-it-works--the-four-layer-engine)
- [The three-page UI](#the-three-page-ui)
- [Project layout](#project-layout)
- [Quick start](#quick-start)
- [Using the CLI](#using-the-cli)
- [Running the demo app](#running-the-demo-app)
- [The worked-example fixtures](#the-worked-example-fixtures)
- [Running the tests](#running-the-tests)
- [Extending the engine](#extending-the-engine)
- [Disclaimers](#disclaimers)

---

## What it does

Given a taxpayer's situation, IRS Resolve:

1. **Collects** the minimum facts needed to understand the taxpayer, debt, compliance, payment
   capacity, disputes, enforcement activity, and relief circumstances in four short steps.
2. **Ingests** a required Form 433-A (individual or self-employed) or Form 433-B (business), plus
   optional W-2, 1099, IRS notice, and transcript files, as *proposed* values with
   provenance — the taxpayer must confirm each before it is used.
3. **Evaluates** the attested facts against every IRS collection alternative.
4. **Explains** the result: Sonnet organizes the confirmed facts and deterministic result into a
   personalized overview, followed by the recommended path, alternatives, stackable relief,
   urgent deadlines first, every excluded program with the reason it was excluded, and an official
   IRS citation for each determination.

The eligibility engine itself runs locally with **no network calls** — citations are static strings
from config. Document extraction and the optional personalized summary are separate OpenRouter
calls. Sonnet cannot select, add, or change an engine outcome.

## Programs it screens for

Short-Term Payment Plan · Guaranteed Installment Agreement · Simple Payment Plan (individual /
business / business-trust-fund) · Non-Streamlined IA · Partial Payment IA · Currently Not
Collectible · Offer in Compromise (Doubt as to Collectibility / Doubt as to Liability / Effective
Tax Administration) · First Time Abate · Reasonable-Cause & Statutory penalty relief · Innocent /
Injured Spouse · Levy release · Lien withdrawal · Collection Due Process / Equivalent Hearing ·
Passport reversal · Bankruptcy-discharge possibility · Taxpayer Advocate / Low Income Taxpayer
Clinic referrals — plus the two universal blockers (unfiled returns / open bankruptcy).

## Design principles

| Principle | What it means |
|---|---|
| **Config, not code** | No threshold, URL, display name, or form number lives in Python. Rules are YAML; thresholds, citations, outcomes, poverty guidelines, and IRS standards are YAML/CSV. Change a number in a file → behavior changes, no code edit. |
| **Attestation invariant** | Every fact the engine evaluates must be taxpayer-attested. Documents only *propose* values; the engine refuses to evaluate an unattested leaf and raises a `FactsError` naming its path. |
| **Determinism** | Same facts + same config version → byte-identical result (minus the timestamp). No network at evaluation time. |
| **Auditable reasoning** | Every determination records the rule that fired, the inputs it relied on, and a per-rule trace with resolved values (`68400 <= 50000 → False`). |
| **Safe by default** | Judgment-heavy outcomes (PPIA, OIC-DATL/ETA, innocent spouse, bankruptcy) are flagged `professional_referral_recommended` and never rendered with a "file now" call to action. |

## How it works — the four-layer engine

The decision tree is evaluated in a fixed order; each layer terminates, routes onward, or attaches
additive flags:

- **Layer 0 — Segment & gate.** Taxpayer type; compliance and bankruptcy blockers (first-match);
  liability-dispute flags (all-match). A blocker stops ordinary progression — though urgent levy
  relief still surfaces.
- **Layer 1 — Low-disclosure tiers** (first-match, no financials): short-term plan → guaranteed IA
  → simple payment plan. **Most cases terminate here** without ever entering income/expenses/assets.
- **Layer 2 — Full financial disclosure & capacity.** Only requested when Layer 1 can't terminate.
  Computes disposable income, net realizable equity, remaining CSED, full-pay capacity, and RCP
  *once*, then routes to CNC / Non-Streamlined IA / PPIA / OIC-DATC (first-match), plus all-match
  secondary flags (OIC alternative, fee waiver, CSED-imminent).
- **Layer 3 — Parallel flags** (all-match, additive): penalty relief, spouse relief, enforcement
  relief (levy/CDP/lien/passport), timing. First Time Abate triggers a **re-evaluation with
  penalties removed**, so the taxpayer sees both the as-is and penalty-abated outcomes.

The engine is a generic evaluator over data-defined rules — it is the only module with control flow
across layers. Rule `when` clauses are a tiny safe expression language (a hand-rolled Python `ast`
whitelist — comparisons, boolean/arithmetic ops, `in`, and six helpers). Every `f.` (fact), `d.`
(derived), and `cfg.` (config) path in a rule is validated at load time against the schema, so a
typo fails `validate` rather than at runtime.

## The three-page UI

The Streamlit app uses a stable three-page flow:

- **Questions** — four short, branching steps covering profile, debt/compliance, payment/dispute,
  and urgent issues/relief.
- **Documents** — requires Form 433-A for an individual or self-employed taxpayer and Form 433-B
  for a business. W-2s, 1099s, notices, and transcripts are optional. Every extracted value is
  previewed and must be explicitly confirmed before it becomes an attested fact.
- **Analysis** — runs the deterministic engine first. Sonnet then creates a schema-constrained
  explanation without changing the engine result. The page renders urgent items, then the primary
  path, alternatives, stacked relief, "why not other options," professional-referral notices,
  citations, and a "Why?" rule trace.

> **Documents may propose facts. Only the taxpayer may attest facts. Only attested facts reach the
> engine. The Analysis page renders the engine result; it never invents one.**

## Project layout

```
irsresolve/
  core/
    facts.py      # Pydantic models; Attested[T] wrapper; attestation check, unwrap, provenance
    derive.py     # pure derive functions (architecture §4.2): CSED, NRE, RCP, disposable income…
    expr.py       # ast-whitelist expression evaluator + reason-template renderer
    rules.py      # Rule model + YAML loader with load-time validation
    engine.py     # the 10-step evaluator
    result.py     # EligibilityResult / Determination / Citation / RuleTrace
    config.py     # config loader; dotted-key resolution; standards lookups
    errors.py
  config/         # thresholds.yaml, citations.yaml, outcomes.yaml, poverty.yaml, standards/*.csv
  rules/          # l0_gate.yaml, l1_tiers.yaml, l2_capacity.yaml, l3_flags.yaml (32 rules)
  analysis/       # constrained Sonnet synthesis over confirmed facts + engine result
  ingest/         # fixture parsers + OpenRouter W-2/1099/433-A/433-B/notice/transcript parser
  render/         # markdown.py renderer
  cli.py          # run / validate / fixtures
  demo/app.py     # three-page Streamlit demo
fixtures/         # case_a…case_d.json (+ docs/ sample extractions)
tests/            # 39 tests
```

## Quick start

Requires **Python 3.11+**. Runtime dependencies: HTTPX, Pydantic v2, PyYAML, and Streamlit (plus
pytest for tests).

```bash
# from the repo root
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e . pytest
```

Verify everything loads and the worked examples resolve correctly:

```bash
python -m irsresolve.cli validate    # config + rules load, all expressions/citations resolve
python -m irsresolve.cli fixtures     # all four example cases match their expected outcomes
```

Expected:

```
validate: OK — config_version=2026-09, 32 rules loaded
case_a_simple_plan.json: OK → primary=SIMPLE_PAYMENT_PLAN
case_b_cnc_oic.json: OK → primary=CNC
case_c_business_blocked.json: OK → primary=BLOCKER_COMPLIANCE
case_d_ppia_cdp.json: OK → primary=PPIA_CANDIDATE
```

## Using the CLI

```bash
# Evaluate a case and print the taxpayer-facing report
python -m irsresolve.cli run fixtures/case_d_ppia_cdp.json --md

# Add the rule trace ("why?") showing every rule evaluated with resolved values
python -m irsresolve.cli run fixtures/case_d_ppia_cdp.json --md --trace

# Machine-readable JSON instead of Markdown
python -m irsresolve.cli run fixtures/case_b_cnc_oic.json --json

# Extract proposed facts from a real tax document through OpenRouter
python -m irsresolve.cli extract path/to/tax-document.pdf

# Point at an alternate config/rules directory (e.g. to test a threshold change)
python -m irsresolve.cli --config irsresolve/config --rules irsresolve/rules fixtures
```

`run` reads a JSON case file (`{ "as_of", "facts", "_expected" }`), where `as_of` fixes the
evaluation date so CSED and CDP-window math is reproducible.

### OpenRouter document extraction

IRS Resolve can use Claude Sonnet 5 through OpenRouter to classify and extract proposed facts from
PDF, PNG, JPEG, and WebP tax documents (20 MB maximum per file). Configure it with environment
variables; never commit a key:

```powershell
$env:OPENROUTER_API_KEY = "your-rotated-key"
# Optional defaults shown below
$env:OPENROUTER_MODEL = "anthropic/claude-sonnet-5"
$env:OPENROUTER_TIMEOUT_SECONDS = "60"
$env:OPENROUTER_PDF_ENGINE = "mistral-ocr" # cloudflare-ai and native are also supported
```

Uploads are encoded in memory and transmitted to OpenRouter and its selected inference provider.
After confirmation, the confirmed facts and deterministic screening result are sent again for the
personalized explanation. The application does not persist the file. Model and PDF-processing
charges apply according to OpenRouter's current pricing. The default Mistral OCR parser adds a
small per-page charge and is used because completed tax forms are frequently scanned or
image-heavy. Extraction only proposes data, and Sonnet
cannot determine eligibility. Missing credentials, timeouts, and provider errors leave the
deterministic analysis and offline examples available.

If a required Form 433 upload cannot be read, the Documents page lets the taxpayer confirm the
document type and enter the required financial values manually. Those entries carry user
provenance and still require explicit confirmation.

Supported classifications are W-2, 1099, Form 433-A, Form 433-B, IRS notice, and account transcript.
Unknown fact paths and invalid values are rejected after inference against the canonical Pydantic
models. A 1099 is treated as gross receipts and can never propose expense values.

## Running the demo app

```bash
streamlit run irsresolve/demo/app.py
```

Then in the browser (opens at http://localhost:8501):

1. Complete the four **Questions** steps.
2. On **Documents**, upload the required Form 433-A or Form 433-B and optionally add W-2s, 1099s,
   notices, or transcripts. Review and confirm the proposed values.
3. The app moves to **Analysis**, where urgent items render on top,
   followed by the recommended Partial Payment IA, the OIC alternative with its offer floor, the
   First Time Abate stacked relief, the "why not other options" exclusions, and the professional
   referral — each with IRS citations. Open **Why? (rule trace)** to see the arithmetic.

For an offline tour, use any **Load an example** button in the sidebar. The deterministic result
still renders when OpenRouter is not configured.

## The worked-example fixtures

| Fixture | Taxpayer | Outcome |
|---|---|---|
| **A** `case_a_simple_plan` | Individual, $23k, small proposed payment | `SIMPLE_PAYMENT_PLAN` — terminates at Layer 1, no financial disclosure |
| **B** `case_b_cnc_oic` | Self-employed, $41k, negative disposable income | `CNC` primary, `OIC_DATC` alternative, low-income fee waiver + LITC referral |
| **C** `case_c_business_blocked` | S-corp with missed federal tax deposits | `BLOCKER_COMPLIANCE` — must get current before any program |
| **D** `case_d_ppia_cdp` | Individual, $68.4k, active CDP deadline | `PPIA_CANDIDATE` (referral) + `OIC_DATC` alt + `FTA` rerun + `CDP_WINDOW_OPEN` urgent |
| **E** `case_e_enforcement` | Individual, $18k, wage levy + lien + passport certified | `SIMPLE_PAYMENT_PLAN` + `URGENT_LEVY_RELEASE` + lien withdrawal + passport reversal |
| **F** `case_f_spouse_penalty` | Individual, joint return, penalties + spouse issues | `SIMPLE_PAYMENT_PLAN` + FTA / reasonable-cause / statutory relief + innocent & injured spouse |

A/B/C/D mirror the architecture document's worked examples (§8.3 / §11); E and F exercise the
Layer-3 enforcement and spouse/penalty rules.

## Running the tests

```bash
pytest -q
```

Tests cover: derive formulas (hand-computed expected values), the expression evaluator (accepts
safe expressions; rejects function calls, imports, comprehensions, lambdas, and out-of-namespace
access), the engine (Layer-1 termination, `NEEDS_FINANCIAL_DISCLOSURE`, the attestation invariant,
the FTA rerun), all six fixtures, ingestion (1099 never populates expenses; `merge()` attests only
accepted proposals), a citation-per-determination check, byte-identical determinism, and a Streamlit
questions-first fact assembly, constrained Sonnet synthesis, and a Streamlit demo smoke test.
Continuous integration runs `pytest`, `validate`,
and `fixtures` on every push (`.github/workflows/ci.yml`).

## Extending the engine

Adding an IRS program is a data change, not a code change:

1. Add citation key(s) to `irsresolve/config/citations.yaml`.
2. Add the outcome's display name, forms, and next-steps to `irsresolve/config/outcomes.yaml`.
3. Author the rule in the appropriate `irsresolve/rules/l*.yaml` with `when`, `reason`,
   `reason_if_excluded` (for outcome rules), `citations`, and `uses`.
4. If a new derived value is needed, add one pure function + unit test in `core/derive.py`.
5. Run `python -m irsresolve.cli validate` and `pytest -q`.

Any new threshold goes in `thresholds.yaml`; standards tables go in `config/standards/*.csv`
(the demo ships Massachusetts county data plus national tables, with a state-median fallback).

## Disclaimers

This engine screens; it does not determine. Thresholds and citations reflect publicly available IRS
guidance as of September 2026 and must be validated against irs.gov and the Internal Revenue Manual
before any production use. Values marked `# TODO verify` in the config were used as given per the
build contract and not independently confirmed. Bankruptcy and innocent-spouse analyses are legal
determinations; the engine surfaces possibilities only. Nothing here is legal or tax advice.
