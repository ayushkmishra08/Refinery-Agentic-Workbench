"""calculate: deterministic arithmetic that shows its working.

A safe AST evaluator — numbers, the arithmetic operators, parentheses, named variables and a
short list of maths functions. No attribute access, no calls to anything else, no names that
were not supplied. Every sub-expression is evaluated bottom-up and recorded as a step
(``482 - 219 = 263``), then the whole thing is restated with the result, so the answer can be
checked line by line rather than trusted. Unit conversion goes through the workbench's own unit
tables (``convert(value, from_unit, to_unit)``) and is recorded as a step too.
"""
from __future__ import annotations

import ast
import math
import operator

from workbench.tools.base import Tool, ToolContext, ToolError, ToolResult

_BIN = {ast.Add: (operator.add, "+"), ast.Sub: (operator.sub, "-"), ast.Mult: (operator.mul, "*"),
        ast.Div: (operator.truediv, "/"), ast.Pow: (operator.pow, "**"), ast.Mod: (operator.mod, "%"),
        ast.FloorDiv: (operator.floordiv, "//")}
_UNARY = {ast.USub: (operator.neg, "-"), ast.UAdd: (operator.pos, "+")}
_FUNCS = {"sqrt": math.sqrt, "log": math.log, "log10": math.log10, "exp": math.exp, "sin": math.sin, "cos": math.cos,
          "tan": math.tan, "abs": abs, "min": min, "max": max, "round": round, "ln": math.log, "floor": math.floor,
          "ceil": math.ceil}
_CONSTS = {"pi": math.pi, "e": math.e}


def _fmt(x: float, precision: int) -> str:
    if isinstance(x, bool):
        return str(x)
    if isinstance(x, int):
        return str(x)
    if x == int(x) and abs(x) < 1e15:
        return str(int(x))
    return f"{x:.{precision}f}".rstrip("0").rstrip(".")


class _Evaluator:
    def __init__(self, variables: dict[str, float], precision: int) -> None:
        self.vars = variables
        self.precision = precision
        self.steps: list[str] = []
        self.count = 0

    def _guard(self) -> None:
        self.count += 1
        if self.count > 500:
            raise ToolError("expression too large")

    def eval(self, node: ast.AST) -> float:
        self._guard()
        if isinstance(node, ast.Expression):
            return self.eval(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ToolError(f"only numbers are allowed, not {node.value!r}")
            return node.value
        if isinstance(node, ast.Name):
            if node.id in self.vars:
                return float(self.vars[node.id])
            if node.id in _CONSTS:
                return _CONSTS[node.id]
            raise ToolError(f"unknown name {node.id!r}; pass it in 'variables'")
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            fn, sym = _UNARY[type(node.op)]
            val = self.eval(node.operand)
            out = fn(val)
            if sym == "-" and not isinstance(node.operand, ast.Constant):
                self.steps.append(f"-({_fmt(val, self.precision)}) = {_fmt(out, self.precision)}")
            return out
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            fn, sym = _BIN[type(node.op)]
            left = self.eval(node.left)
            right = self.eval(node.right)
            if sym in ("/", "//", "%") and right == 0:
                raise ToolError("division by zero")
            if sym == "**" and (abs(right) > 1000 or abs(left) > 1e150):
                raise ToolError("exponent out of range")
            out = fn(left, right)
            self.steps.append(f"{_fmt(left, self.precision)} {sym} {_fmt(right, self.precision)} = {_fmt(out, self.precision)}")
            return out
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
            if node.keywords:
                raise ToolError("keyword arguments are not supported")
            args = [self.eval(a) for a in node.args]
            fn = _FUNCS[node.func.id]
            try:
                out = fn(*args)
            except (ValueError, TypeError) as exc:
                raise ToolError(f"{node.func.id}: {exc}") from exc
            self.steps.append(f"{node.func.id}({', '.join(_fmt(a, self.precision) for a in args)}) = {_fmt(out, self.precision)}")
            return out
        raise ToolError(f"unsupported syntax: {type(node).__name__}")


def evaluate(expression: str, variables: dict | None = None, precision: int = 4) -> tuple[float, list[str]]:
    """(result, steps) for an arithmetic expression. Raises ToolError on anything unsafe."""
    if not isinstance(expression, str) or not expression.strip():
        raise ToolError("expression is empty")
    if len(expression) > 2000:
        raise ToolError("expression too long")
    variables = {str(k): float(v) for k, v in (variables or {}).items()}
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise ToolError(f"cannot parse expression: {exc.msg}") from exc
    for node in ast.walk(tree):
        if isinstance(node, (ast.Attribute, ast.Subscript, ast.Lambda, ast.ListComp, ast.DictComp, ast.SetComp,
                             ast.GeneratorExp, ast.IfExp, ast.Compare, ast.BoolOp, ast.JoinedStr, ast.Starred)):
            raise ToolError(f"{type(node).__name__} is not allowed in a calculation")
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in _FUNCS):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None) or "?"
            raise ToolError(f"call to {name!r} is not allowed; functions: {', '.join(sorted(_FUNCS))}")
    ev = _Evaluator(variables, precision)
    steps: list[str] = []
    if variables:
        substituted = expression.strip()
        for k, v in variables.items():
            substituted = _substitute(substituted, k, _fmt(v, precision))
        steps.append(f"substitute: {substituted}")
    result = ev.eval(tree)
    steps.extend(ev.steps)
    steps.append(f"result: {expression.strip()} = {_fmt(result, precision)}")
    return result, steps


def _substitute(text: str, name: str, value: str) -> str:
    import re

    return re.sub(rf"\b{re.escape(name)}\b", value, text)


class CalculateTool(Tool):
    name = "calculate"
    description = ("Evaluate an arithmetic expression deterministically and show every step. Supports + - * / ** % //, "
                   "parentheses, named variables, sqrt/log/log10/exp/sin/cos/tan/abs/min/max/round, pi and e. "
                   "Optionally convert the result between units (from_unit -> to_unit) using the plant unit tables.")
    parameters = {"type": "object",
                  "properties": {"expression": {"type": "string"}, "variables": {"type": "object"},
                                 "precision": {"type": "integer"}, "from_unit": {"type": "string"}, "to_unit": {"type": "string"},
                                 "label": {"type": "string"}},
                  "required": ["expression"]}

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        precision = int(args.get("precision") or 4)
        precision = max(0, min(precision, 12))
        result, steps = evaluate(args["expression"], args.get("variables") or {}, precision)
        data = {"expression": args["expression"], "variables": args.get("variables") or {}, "result": result,
                "precision": precision}
        if args.get("from_unit") and args.get("to_unit"):
            from workbench.services.calculators.units import Quantity, convert

            try:
                q = convert(Quantity(result, args["from_unit"]), args["to_unit"])
            except ValueError as exc:
                raise ToolError(str(exc)) from exc
            steps.append(f"convert: {_fmt(result, precision)} {args['from_unit']} = {_fmt(q.value, precision)} {q.unit}"
                         + (f" ({q.basis})" if q.basis else ""))
            data.update({"converted": q.value, "unit": q.unit, "basis": q.basis})
        label = args.get("label")
        head = f"{label}: " if label else ""
        return ToolResult(tool=self.name, output=head + "\n".join(steps), steps=steps, data=data)
