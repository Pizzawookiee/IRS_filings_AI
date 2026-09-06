"""Safe expression evaluator for rule `when` clauses (impl-plan §Expression language).

A hand-rolled Python `ast` whitelist — no CEL dependency, no eval() of raw strings. Only the
listed node types are allowed; the only callables are the six helpers below. At load time every
`f.`/`d.`/`cfg.` path is validated against the Facts/DerivedFacts schema and the config keys, so
a typo fails `cli validate` rather than at runtime. `resolved_repr` renders an evaluated
expression with values substituted for the trace ("68400 <= 50000 → False").
"""

from __future__ import annotations

import ast
import re
from types import UnionType
from typing import Union, get_args, get_origin

from pydantic import BaseModel

from .config import Config
from .derive import DerivedFacts
from .errors import RuleLoadError, RuleRuntimeError
from .facts import Attested, Facts

# Whitelisted AST node types. Anything else → RuleLoadError.
_ALLOWED = (
    ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub,
    ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Compare,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
    ast.Attribute, ast.Subscript, ast.Name, ast.Load, ast.Constant,
    ast.List, ast.Tuple, ast.Call,
)


def _any_period(items, attr):
    return any(getattr(x, attr) for x in (items or []))


def _intersects(a, b):
    return bool(set(a or []) & set(b or []))


HELPERS = {"any": any, "len": len, "min": min, "max": max,
           "intersects": _intersects, "any_period": _any_period}


# ---- schema path sets (built once) ----

def _strip_optional(ann):
    if get_origin(ann) in (Union, UnionType):
        args = [a for a in get_args(ann) if a is not type(None)]
        return args[0] if len(args) == 1 else ann
    return ann


def _fact_paths(model: type[BaseModel], prefix: str, out: set[str]) -> None:
    for name, field in model.model_fields.items():
        path = f"{prefix}.{name}"
        ann = _strip_optional(field.annotation)
        origin = get_origin(ann) or ann
        out.add(path)  # leaf, list, or group prefix are all referenceable
        if isinstance(origin, type) and issubclass(origin, Attested):
            continue  # Attested → terminal (unwrapped value)
        if isinstance(origin, type) and issubclass(origin, BaseModel):
            _fact_paths(origin, path, out)  # nested group


def build_fact_paths() -> set[str]:
    out: set[str] = set()
    _fact_paths(Facts, "f", out)
    return out


def build_derived_paths() -> set[str]:
    return {f"d.{name}" for name in DerivedFacts.model_fields}


# ---- validation ----

def _dotted(node: ast.AST) -> str | None:
    """Reconstruct a dotted path from an Attribute/Name chain, else None."""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return None


class Validator:
    def __init__(self, config: Config):
        self.config = config
        self.fact_paths = build_fact_paths()
        self.derived_paths = build_derived_paths()

    def check(self, tree: ast.AST, rule_id: str) -> None:
        for node in ast.walk(tree):
            if not isinstance(node, _ALLOWED):
                raise RuleLoadError(f"{rule_id}: disallowed expression element {type(node).__name__}")
            if isinstance(node, ast.Call):
                if not (isinstance(node.func, ast.Name) and node.func.id in HELPERS):
                    raise RuleLoadError(f"{rule_id}: only {sorted(HELPERS)} may be called")
            if isinstance(node, ast.Name) and node.id not in ({"f", "d", "cfg"} | set(HELPERS)):
                # True/False/None are Constant nodes, not Name — a bare Name here is a typo
                # (e.g. YAML `false` instead of Python `False`).
                raise RuleLoadError(f"{rule_id}: unknown name '{node.id}' (use True/False/None, not yaml booleans)")
        # path resolution: every f./d./cfg. attribute chain must exist
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                dotted = _dotted(node)
                if dotted is None:
                    continue
                root = dotted.split(".", 1)[0]
                if root == "f" and dotted not in self.fact_paths:
                    raise RuleLoadError(f"{rule_id}: unknown fact path {dotted}")
                if root == "d" and dotted not in self.derived_paths:
                    raise RuleLoadError(f"{rule_id}: unknown derived path {dotted}")
                if root == "cfg" and not self.config.cfg_path_exists(dotted.split(".", 1)[1]):
                    raise RuleLoadError(f"{rule_id}: unknown config key {dotted}")


def compile_when(expr: str, rule_id: str, validator: Validator) -> ast.Expression:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise RuleLoadError(f"{rule_id}: cannot parse `when`: {e}") from e
    validator.check(tree, rule_id)
    return tree


# ---- runtime ----

def _context(f, d: DerivedFacts, config: Config) -> dict:
    return {"f": f, "d": d, "cfg": config.cfg, **HELPERS}


def eval_when(tree: ast.Expression, rule_id: str, f, d: DerivedFacts, config: Config) -> bool:
    """Evaluate a compiled expression. A None (Layer-2-absent) operand → rule does not match."""
    try:
        return bool(_eval(tree.body, _context(f, d, config)))
    except TypeError:
        return False  # e.g. comparison against a None Layer-2 derived → not matched
    except RuleRuntimeError:
        raise
    except Exception as e:  # noqa: BLE001
        raise RuleRuntimeError(f"{rule_id}: {e}") from e


def eval_expr(tree: ast.Expression, f, d: DerivedFacts, config: Config):
    """Evaluate a compiled expression to its raw value (used for rule `extras`)."""
    return _eval(tree.body, _context(f, d, config))


_CMP = {ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b,
        ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b,
        ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b,
        ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b}


def _eval(node: ast.AST, ctx: dict):
    if isinstance(node, ast.BoolOp):
        vals = [_eval(v, ctx) for v in node.values]
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, ctx)
        return (not v) if isinstance(node.op, ast.Not) else -v
    if isinstance(node, ast.BinOp):
        a, b = _eval(node.left, ctx), _eval(node.right, ctx)
        return {ast.Add: a.__add__, ast.Sub: a.__sub__, ast.Mult: a.__mul__, ast.Div: a.__truediv__}[type(node.op)](b)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, ctx)
        for op, comp in zip(node.ops, node.comparators):
            right = _eval(comp, ctx)
            if not _CMP[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Call):
        return ctx[node.func.id](*[_eval(a, ctx) for a in node.args])
    if isinstance(node, ast.Attribute):
        return getattr(_eval(node.value, ctx), node.attr)
    if isinstance(node, ast.Subscript):
        return _eval(node.value, ctx)[_eval(node.slice, ctx)]
    if isinstance(node, ast.Name):
        return ctx[node.id]
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(e, ctx) for e in node.elts]
    raise RuleRuntimeError(f"unhandled node {type(node).__name__}")


# ---- trace rendering ----

def _fmt(v) -> str:
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, str):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_fmt(x) for x in v) + "]"
    if v is None:
        return "None"
    try:
        n = float(v)
        return str(int(n)) if n == int(n) else str(v)
    except (TypeError, ValueError):
        return str(v)


def resolved_repr(tree: ast.Expression, f, d: DerivedFacts, config: Config) -> str:
    """The expression with f./d./cfg. leaves replaced by their values, plus '→ result'."""
    ctx = _context(f, d, config)

    class Sub(ast.NodeTransformer):
        def visit_Attribute(self, node):
            dotted = _dotted(node)
            if dotted and dotted.split(".", 1)[0] in ("f", "d", "cfg"):
                try:
                    return ast.Name(id=_fmt(_eval(node, ctx)))
                except Exception:  # noqa: BLE001
                    return ast.Name(id=dotted)
            return self.generic_visit(node)

    substituted = Sub().visit(ast.parse(ast.unparse(tree), mode="eval"))
    try:
        result = _fmt(_eval(tree.body, ctx))
    except Exception:  # noqa: BLE001
        result = "error"
    return f"{ast.unparse(substituted.body)} → {result}"


# ---- reason templates: {expr:fmt} placeholders over the same f./d./cfg. grammar ----

_PLACEHOLDER = re.compile(r"\{([^}]+)\}")


def _split_placeholder(ph: str) -> tuple[str, str]:
    expr, _sep, fmt = ph.partition(":")  # expressions never contain ':'; fmt is a format spec
    return expr.strip(), fmt


def validate_template(template: str, rule_id: str, validator: Validator) -> None:
    for ph in _PLACEHOLDER.findall(template):
        expr, _fmt = _split_placeholder(ph)
        compile_when(expr, rule_id, validator)  # reuse path/whitelist validation


def render_template(template: str, f, d: DerivedFacts, config: Config) -> str:
    ctx = _context(f, d, config)

    def repl(m: re.Match) -> str:
        expr, fmt = _split_placeholder(m.group(1))
        val = _eval(ast.parse(expr, mode="eval").body, ctx)
        return format(val, fmt) if fmt else str(val)

    return _PLACEHOLDER.sub(repl, template)
