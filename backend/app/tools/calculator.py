"""A safe arithmetic evaluator (AST whitelist — no eval, no names, no attribute access)."""

from __future__ import annotations

import ast
import math
import operator

from app.tools.search import ToolError

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
        ast.Pow: operator.pow}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {"round": round, "abs": abs, "min": min, "max": max, "sqrt": math.sqrt,
          "log": math.log, "log10": math.log10, "avg": lambda *a: sum(a) / len(a),
          "sum": lambda *a: sum(a)}
MAX_EXPR_LEN = 300


def evaluate(expression: str) -> float:
    if len(expression) > MAX_EXPR_LEN:
        raise ToolError("Expression too long.", code="calc_error")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ToolError("Invalid expression.", code="calc_error") from exc

    def _ev(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
                and not isinstance(node.value, bool):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            left, right = _ev(node.left), _ev(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 100:
                raise ToolError("Exponent too large.", code="calc_error")
            return _BIN[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return _UNARY[type(node.op)](_ev(node.operand))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in _FUNCS and not node.keywords and len(node.args) <= 50:
            return _FUNCS[node.func.id](*[_ev(a) for a in node.args])
        raise ToolError("Unsupported expression element.", code="calc_error")

    try:
        result = _ev(tree)
    except ZeroDivisionError as exc:
        raise ToolError("Division by zero.", code="calc_error") from exc
    except (ValueError, OverflowError, TypeError) as exc:
        raise ToolError(f"Math error: {exc}", code="calc_error") from exc
    return float(result)
