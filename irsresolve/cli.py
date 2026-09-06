"""IRS Resolve CLI (impl-plan §CLI). Stdlib argparse only.

    python -m irsresolve.cli validate
    python -m irsresolve.cli fixtures
    python -m irsresolve.cli run <facts.json> [--json | --md] [--trace]
    python -m irsresolve.cli extract <document>

Engine/renderer are imported lazily so `validate` works before those modules land.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_CONFIG = "irsresolve/config"
DEFAULT_RULES = "irsresolve/rules"
FIXTURE_DIR = "fixtures"


def _load(config_dir, rules_dir):
    from .core.config import load_config
    from .core.expr import Validator
    from .core.rules import load_rules
    cfg = load_config(config_dir)
    rules = load_rules(rules_dir, Validator(cfg), cfg)
    return cfg, rules


def cmd_validate(args) -> int:
    from .core.errors import IRSResolveError
    try:
        cfg, rules = _load(args.config, args.rules)
    except IRSResolveError as e:
        print(f"validate: FAIL — {e}", file=sys.stderr)
        return 1
    print(f"validate: OK - config_version={cfg.version}, {len(rules)} rules loaded")
    return 0


def _evaluate_file(path, cfg, rules):
    from datetime import date
    from .core.engine import Engine
    from .core.facts import Facts
    data = json.loads(Path(path).read_text())
    facts = Facts.model_validate(data["facts"])
    as_of = date.fromisoformat(data["as_of"]) if data.get("as_of") else date.today()
    return Engine(cfg, rules).evaluate(facts, as_of=as_of), data


def cmd_run(args) -> int:
    cfg, rules = _load(args.config, args.rules)
    result, _data = _evaluate_file(args.facts, cfg, rules)
    if args.md:
        from .render.markdown import to_markdown
        print(to_markdown(result, show_trace=args.trace))
    else:
        out = result.model_dump(mode="json")
        if not args.trace:
            out.pop("trace", None)
        print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_fixtures(args) -> int:
    cfg, rules = _load(args.config, args.rules)
    failures = 0
    for path in sorted(Path(FIXTURE_DIR).glob("case_*.json")):
        result, data = _evaluate_file(path, cfg, rules)
        exp = data.get("_expected", {})
        problems = _check_expected(result, exp)
        status = "OK" if not problems else "FAIL"
        print(f"{path.name}: {status} -> primary={result.primary.outcome}")
        for p in problems:
            print(f"    - {p}")
        failures += bool(problems)
    return 1 if failures else 0


def cmd_extract(args) -> int:
    """Extract unattested proposed facts from one real document through OpenRouter."""
    from .core.errors import DocumentInferenceError
    from .ingest.openrouter import OpenRouterDocumentParser

    try:
        proposed = OpenRouterDocumentParser().parse(args.document)
    except (DocumentInferenceError, OSError) as e:
        print(f"extract: FAIL - {e}", file=sys.stderr)
        return 1
    print(json.dumps(proposed.model_dump(mode="json"), indent=2))
    return 0


def _check_expected(result, exp) -> list[str]:
    problems = []
    if exp.get("primary") and result.primary.outcome != exp["primary"]:
        problems.append(f"primary {result.primary.outcome} != expected {exp['primary']}")
    alts = {d.outcome for d in result.alternatives}
    for a in exp.get("alternatives", []):
        if a not in alts:
            problems.append(f"missing alternative {a}")
    flags = {d.outcome for d in result.stacked_relief} | {d.outcome for d in result.alternatives}
    for fl in exp.get("flags", []):
        if fl not in flags:
            problems.append(f"missing flag {fl}")
    urgent = {d.outcome for d in result.urgent}
    if urgent != set(exp.get("urgent", [])):
        problems.append(f"urgent {sorted(urgent)} != expected {sorted(exp.get('urgent', []))}")
    return problems


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="irsresolve")
    p.add_argument("--config", default=DEFAULT_CONFIG)
    p.add_argument("--rules", default=DEFAULT_RULES)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("validate")
    sub.add_parser("fixtures")
    x = sub.add_parser("extract")
    x.add_argument("document")
    r = sub.add_parser("run")
    r.add_argument("facts")
    r.add_argument("--json", dest="json_out", action="store_true")
    r.add_argument("--md", action="store_true")
    r.add_argument("--trace", action="store_true")

    args = p.parse_args(argv)
    return {
        "validate": cmd_validate,
        "fixtures": cmd_fixtures,
        "extract": cmd_extract,
        "run": cmd_run,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
