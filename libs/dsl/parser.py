"""DSL parser and evaluator for AML rule conditions.

Grammar (informal):
    expr     := or_expr
    or_expr  := and_expr ('or' and_expr)*
    and_expr := not_expr ('and' not_expr)*
    not_expr := 'not' not_expr | comparison
    comparison := term (op term | 'in' '[' list ']' | 'contains' term)?
    term     := func_call | field_access | literal
    func_call := IDENT '(' args ')'
    field_access := IDENT ('.' IDENT)*
    literal  := NUMBER | STRING | BOOL
    op       := '>' | '<' | '>=' | '<=' | '==' | '!='
"""

from __future__ import annotations

import math
import re
from decimal import Decimal
from typing import Any


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DSLParseError(Exception):
    """Raised when the DSL string cannot be parsed."""


class DSLEvalError(Exception):
    """Raised when a parsed AST cannot be evaluated against a context."""


# ---------------------------------------------------------------------------
# Tokeniser
# ---------------------------------------------------------------------------

_TOKEN_SPEC: list[tuple[str, str]] = [
    ("NUMBER", r"-?\d+(?:\.\d+)?"),
    ("STRING", r"'[^']*'|\"[^\"]*\""),
    ("BOOL", r"\b(?:true|false|True|False)\b"),
    ("OP", r">=|<=|!=|==|>|<"),
    ("LBRACKET", r"\["),
    ("RBRACKET", r"\]"),
    ("LPAREN", r"\("),
    ("RPAREN", r"\)"),
    ("COMMA", r","),
    ("DOT", r"\."),
    ("IDENT", r"[A-Za-z_][A-Za-z0-9_]*"),
    ("SKIP", r"[ \t\n\r]+"),
    ("MISMATCH", r"."),
]

_TOKEN_RE = re.compile(
    "|".join(f"(?P<{name}>{pattern})" for name, pattern in _TOKEN_SPEC)
)


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    for mo in _TOKEN_RE.finditer(text):
        kind = mo.lastgroup
        value = mo.group()
        if kind == "SKIP":
            continue
        if kind == "MISMATCH":
            raise DSLParseError(f"Unexpected character: {value!r}")
        tokens.append((kind, value))
    return tokens


# ---------------------------------------------------------------------------
# Recursive-descent parser
# ---------------------------------------------------------------------------


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self._tokens = tokens
        self._pos = 0

    # -- helpers -------------------------------------------------------------

    def _peek(self) -> tuple[str, str] | None:
        if self._pos < len(self._tokens):
            return self._tokens[self._pos]
        return None

    def _consume(self) -> tuple[str, str]:
        tok = self._tokens[self._pos]
        self._pos += 1
        return tok

    def _expect(self, kind: str, value: str | None = None) -> tuple[str, str]:
        tok = self._peek()
        if tok is None:
            raise DSLParseError(f"Expected {kind!r} but reached end of input")
        if tok[0] != kind:
            raise DSLParseError(f"Expected token type {kind!r}, got {tok[0]!r} ({tok[1]!r})")
        if value is not None and tok[1] != value:
            raise DSLParseError(f"Expected {value!r}, got {tok[1]!r}")
        return self._consume()

    def _match_ident(self, *names: str) -> bool:
        tok = self._peek()
        return tok is not None and tok[0] == "IDENT" and tok[1] in names

    # -- grammar rules -------------------------------------------------------

    def parse(self) -> dict:
        node = self._or_expr()
        if self._peek() is not None:
            raise DSLParseError(f"Unexpected token at position {self._pos}: {self._peek()}")
        return node

    def _or_expr(self) -> dict:
        left = self._and_expr()
        while self._match_ident("or"):
            self._consume()
            right = self._and_expr()
            left = {"op": "or", "left": left, "right": right}
        return left

    def _and_expr(self) -> dict:
        left = self._not_expr()
        while self._match_ident("and"):
            self._consume()
            right = self._not_expr()
            left = {"op": "and", "left": left, "right": right}
        return left

    def _not_expr(self) -> dict:
        if self._match_ident("not"):
            self._consume()
            return {"op": "not", "operand": self._not_expr()}
        return self._comparison()

    def _comparison(self) -> dict:
        left = self._term()
        tok = self._peek()
        if tok is None:
            return left

        if tok[0] == "OP":
            op = self._consume()[1]
            right = self._term()
            return {"op": op, "left": left, "right": right}

        if tok[0] == "IDENT" and tok[1] == "in":
            self._consume()
            items = self._list_literal()
            return {"op": "in", "left": left, "items": items}

        if tok[0] == "IDENT" and tok[1] == "contains":
            self._consume()
            right = self._term()
            return {"op": "contains", "left": left, "right": right}

        return left

    def _list_literal(self) -> list:
        self._expect("LBRACKET")
        items: list = []
        while True:
            tok = self._peek()
            if tok is None:
                raise DSLParseError("Unterminated list literal")
            if tok[0] == "RBRACKET":
                self._consume()
                break
            items.append(self._literal_value())
            tok = self._peek()
            if tok and tok[0] == "COMMA":
                self._consume()
        return items

    def _term(self) -> dict:
        tok = self._peek()
        if tok is None:
            raise DSLParseError("Unexpected end of expression")

        # Parenthesised sub-expression
        if tok[0] == "LPAREN":
            self._consume()
            node = self._or_expr()
            self._expect("RPAREN")
            return node

        # Literals
        if tok[0] in ("NUMBER", "STRING", "BOOL"):
            self._consume()
            return {"type": "literal", "value": self._coerce_literal(tok[0], tok[1])}

        # Identifier – could be a function call or field access
        if tok[0] == "IDENT":
            name = self._consume()[1]
            # Function call
            if self._peek() and self._peek()[0] == "LPAREN":  # type: ignore[index]
                self._consume()  # consume '('
                args: list[dict] = []
                while True:
                    if self._peek() and self._peek()[0] == "RPAREN":  # type: ignore[index]
                        self._consume()
                        break
                    args.append(self._term())
                    if self._peek() and self._peek()[0] == "COMMA":  # type: ignore[index]
                        self._consume()
                return {"type": "func", "name": name, "args": args}

            # Field access  (a.b.c)
            parts = [name]
            while self._peek() and self._peek()[0] == "DOT":  # type: ignore[index]
                self._consume()  # consume '.'
                field = self._expect("IDENT")[1]
                parts.append(field)
            return {"type": "field", "path": parts}

        raise DSLParseError(f"Unexpected token: {tok}")

    def _literal_value(self) -> Any:
        tok = self._peek()
        if tok is None:
            raise DSLParseError("Expected literal value")
        if tok[0] not in ("NUMBER", "STRING", "BOOL", "IDENT"):
            raise DSLParseError(f"Expected literal, got {tok!r}")
        self._consume()
        return self._coerce_literal(tok[0], tok[1])

    @staticmethod
    def _coerce_literal(kind: str, raw: str) -> Any:
        if kind == "NUMBER":
            return Decimal(raw)
        if kind == "STRING":
            return raw[1:-1]  # strip quotes
        if kind == "BOOL":
            return raw.lower() == "true"
        return raw  # IDENT used as bare string in lists


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


def _get_field(path: list[str], context: dict) -> Any:
    """Resolve a dotted field path against the context dict."""
    obj: Any = context
    for part in path:
        if isinstance(obj, dict):
            if part not in obj:
                raise DSLEvalError(f"Field {part!r} not found in context path {path}")
            obj = obj[part]
        else:
            try:
                obj = getattr(obj, part)
            except AttributeError:
                raise DSLEvalError(f"Field {part!r} not found on object of type {type(obj).__name__}")
    return obj


def _call_func(name: str, args: list, context: dict) -> Any:
    """Evaluate a built-in DSL function."""
    if name == "sum":
        if len(args) != 1:
            raise DSLEvalError("sum() requires exactly 1 argument (a window/list)")
        values = args[0]
        if not isinstance(values, (list, tuple)):
            raise DSLEvalError(f"sum() argument must be a list, got {type(values).__name__}")
        return sum(Decimal(str(v)) for v in values)

    if name == "count":
        if len(args) != 1:
            raise DSLEvalError("count() requires exactly 1 argument")
        values = args[0]
        if not isinstance(values, (list, tuple)):
            raise DSLEvalError(f"count() argument must be a list, got {type(values).__name__}")
        return Decimal(len(values))

    if name == "zscore":
        if len(args) != 3:
            raise DSLEvalError("zscore() requires 3 arguments: value, baseline_mean, baseline_stddev")
        value, mean, stddev = (Decimal(str(a)) for a in args)
        if stddev == 0:
            raise DSLEvalError("zscore() baseline_stddev must not be zero")
        return (value - mean) / stddev

    if name == "ratio":
        if len(args) != 2:
            raise DSLEvalError("ratio() requires 2 arguments: a, b")
        a, b = Decimal(str(args[0])), Decimal(str(args[1]))
        if b == 0:
            raise DSLEvalError("ratio() denominator must not be zero")
        return a / b

    if name == "abs":
        if len(args) != 1:
            raise DSLEvalError("abs() requires exactly 1 argument")
        return abs(Decimal(str(args[0])))

    if name == "sqrt":
        if len(args) != 1:
            raise DSLEvalError("sqrt() requires exactly 1 argument")
        return Decimal(str(math.sqrt(float(args[0]))))

    raise DSLEvalError(f"Unknown function: {name!r}")


def _to_comparable(v: Any) -> Any:
    """Coerce numeric-ish values to Decimal for consistent comparison.

    Booleans are intentionally excluded – they compare as booleans.
    """
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    return v


class DSLEvaluator:
    """Evaluates a parsed DSL AST against a context dictionary."""

    def evaluate(self, ast: dict, context: dict) -> bool:
        result = self._eval(ast, context)
        if not isinstance(result, bool):
            raise DSLEvalError(
                f"Top-level expression must evaluate to bool, got {type(result).__name__}"
            )
        return result

    def _eval(self, node: dict, context: dict) -> Any:
        op = node.get("op")
        ntype = node.get("type")

        # ---- leaf nodes ----
        if ntype == "literal":
            return node["value"]

        if ntype == "field":
            return _get_field(node["path"], context)

        if ntype == "func":
            evaluated_args = [self._eval(a, context) for a in node["args"]]
            return _call_func(node["name"], evaluated_args, context)

        # ---- logical ----
        if op == "and":
            return bool(self._eval(node["left"], context)) and bool(self._eval(node["right"], context))

        if op == "or":
            return bool(self._eval(node["left"], context)) or bool(self._eval(node["right"], context))

        if op == "not":
            return not bool(self._eval(node["operand"], context))

        # ---- comparison ----
        if op in (">", "<", ">=", "<=", "==", "!="):
            left = _to_comparable(self._eval(node["left"], context))
            right = _to_comparable(self._eval(node["right"], context))
            try:
                if op == ">":
                    return left > right
                if op == "<":
                    return left < right
                if op == ">=":
                    return left >= right
                if op == "<=":
                    return left <= right
                if op == "==":
                    return left == right
                if op == "!=":
                    return left != right
            except TypeError as exc:
                raise DSLEvalError(f"Cannot compare {type(left).__name__} with {type(right).__name__}: {exc}") from exc

        if op == "in":
            left = self._eval(node["left"], context)
            return left in node["items"]

        if op == "contains":
            left = self._eval(node["left"], context)
            right = self._eval(node["right"], context)
            if not isinstance(left, str):
                raise DSLEvalError(f"'contains' requires a string on the left, got {type(left).__name__}")
            return str(right) in left

        raise DSLEvalError(f"Unknown node: {node!r}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class DSLParser:
    """Parses DSL condition strings into an AST dict."""

    def parse(self, dsl_string: str) -> dict:
        """Parse *dsl_string* and return an AST dictionary.

        Raises :class:`DSLParseError` on syntax errors.
        """
        if not dsl_string or not dsl_string.strip():
            raise DSLParseError("DSL string must not be empty")
        try:
            tokens = _tokenize(dsl_string.strip())
            parser = _Parser(tokens)
            return parser.parse()
        except DSLParseError:
            raise
        except Exception as exc:
            raise DSLParseError(f"Failed to parse DSL: {exc}") from exc
