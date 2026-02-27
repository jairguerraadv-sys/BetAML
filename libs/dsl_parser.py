"""
DSL (Domain-Specific Language) Parser para o Rules Engine do BetAML.

Gramática suportada:
  expr   := or_expr
  or_expr  := and_expr ('or' and_expr)*
  and_expr := not_expr ('and' not_expr)*
  not_expr := 'not' not_expr | compare_expr
  compare_expr := call_expr (OP call_expr)?
  OP := '>' | '<' | '>=' | '<=' | '==' | '!=' | 'in' | 'contains'
  call_expr := FUNC '(' args ')' | atom
  FUNC := 'sum' | 'count' | 'zscore' | 'ratio' | 'abs'
  atom := NUMBER | STRING | BOOL | field_access | '(' expr ')'
  field_access := IDENT ('.' IDENT)*

Acesso a campos:
  transaction.amount         → event payload
  bet.stakeAmount
  features.deposit_sum_24h
  player.pepFlag
  params.threshold           → params do RuleDefinition

Funções:
  zscore(value, mean, stddev)       → (value - mean) / stddev
  ratio(a, b)                       → a / b (safe: retorna 0 se b=0)
  abs(x)                            → |x|
  sum(field, window)                → usa features pré-computadas
  count(field, window)              → usa features pré-computadas
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any


# ──────────────────────────────────────────────────
# Tokenizer
# ──────────────────────────────────────────────────

TOKEN_PATTERNS = [
    ("NUMBER",   r"-?\d+(?:\.\d+)?"),
    ("STRING",   r'"[^"]*"|\'[^\']*\''),
    ("GE",       r">="),
    ("LE",       r"<="),
    ("NE",       r"!="),
    ("EQ",       r"=="),
    ("GT",       r">"),
    ("LT",       r"<"),
    ("LPAREN",   r"\("),
    ("RPAREN",   r"\)"),
    ("COMMA",    r","),
    ("IDENT",    r"[A-Za-z_][A-Za-z0-9_.]*"),
    ("SKIP",     r"[ \t\n]+"),
]

_MASTER_RE = re.compile(
    "|".join(f"(?P<{name}>{pat})" for name, pat in TOKEN_PATTERNS)
)

KEYWORDS = {"and", "or", "not", "in", "contains", "true", "false", "True", "False"}
FUNCTIONS = {"sum", "count", "zscore", "ratio", "abs"}


class Token:
    __slots__ = ("type", "value")

    def __init__(self, type_: str, value: str):
        self.type = type_
        self.value = value

    def __repr__(self) -> str:
        return f"Token({self.type!r}, {self.value!r})"


def tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    for m in _MASTER_RE.finditer(text):
        kind = m.lastgroup
        value = m.group()
        if kind == "SKIP":
            continue
        if kind == "IDENT":
            if value in ("and", "or", "not", "in", "contains"):
                kind = value.upper()
            elif value in ("true", "True"):
                kind, value = "BOOL", "true"
            elif value in ("false", "False"):
                kind, value = "BOOL", "false"
        tokens.append(Token(kind, value))

    pos = 0
    for m in _MASTER_RE.finditer(text):
        pos = m.end()
    if pos < len(text):
        raise DSLSyntaxError(f"Caractere inválido próximo a: {text[pos:]!r}")
    return tokens


# ──────────────────────────────────────────────────
# AST nodes
# ──────────────────────────────────────────────────

class ASTNode:
    pass


class NumberNode(ASTNode):
    def __init__(self, value: Decimal):
        self.value = value


class StringNode(ASTNode):
    def __init__(self, value: str):
        self.value = value


class BoolNode(ASTNode):
    def __init__(self, value: bool):
        self.value = value


class FieldNode(ASTNode):
    def __init__(self, path: str):
        self.path = path  # ex.: "transaction.amount"


class BinOpNode(ASTNode):
    def __init__(self, op: str, left: ASTNode, right: ASTNode):
        self.op = op
        self.left = left
        self.right = right


class UnaryNotNode(ASTNode):
    def __init__(self, operand: ASTNode):
        self.operand = operand


class FuncCallNode(ASTNode):
    def __init__(self, name: str, args: list[ASTNode]):
        self.name = name
        self.args = args


# ──────────────────────────────────────────────────
# Parser (recursive descent)
# ──────────────────────────────────────────────────

class DSLSyntaxError(Exception):
    pass


class DSLParser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    @property
    def current(self) -> Token | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def consume(self, expected_type: str | None = None) -> Token:
        tok = self.current
        if tok is None:
            raise DSLSyntaxError("Fim inesperado da expressão")
        if expected_type and tok.type != expected_type:
            raise DSLSyntaxError(
                f"Esperado {expected_type!r}, encontrado {tok.type!r} ({tok.value!r})"
            )
        self.pos += 1
        return tok

    def peek_type(self) -> str | None:
        return self.current.type if self.current else None

    # or_expr := and_expr ('or' and_expr)*
    def parse_expr(self) -> ASTNode:
        node = self.parse_and_expr()
        while self.peek_type() == "OR":
            self.consume("OR")
            right = self.parse_and_expr()
            node = BinOpNode("or", node, right)
        return node

    # and_expr := not_expr ('and' not_expr)*
    def parse_and_expr(self) -> ASTNode:
        node = self.parse_not_expr()
        while self.peek_type() == "AND":
            self.consume("AND")
            right = self.parse_not_expr()
            node = BinOpNode("and", node, right)
        return node

    # not_expr := 'not' not_expr | compare_expr
    def parse_not_expr(self) -> ASTNode:
        if self.peek_type() == "NOT":
            self.consume("NOT")
            return UnaryNotNode(self.parse_not_expr())
        return self.parse_compare_expr()

    # compare_expr := call_expr (OP call_expr)?
    def parse_compare_expr(self) -> ASTNode:
        left = self.parse_call_expr()
        op_map = {
            "GT": ">", "LT": "<", "GE": ">=", "LE": "<=",
            "EQ": "==", "NE": "!=", "IN": "in", "CONTAINS": "contains",
        }
        if self.peek_type() in op_map:
            op_tok = self.consume()
            right = self.parse_call_expr()
            return BinOpNode(op_map[op_tok.type], left, right)
        return left

    # call_expr := FUNC '(' args ')' | atom
    def parse_call_expr(self) -> ASTNode:
        tok = self.current
        if tok and tok.type == "IDENT" and tok.value in FUNCTIONS:
            self.consume("IDENT")
            self.consume("LPAREN")
            args: list[ASTNode] = []
            if self.peek_type() != "RPAREN":
                args.append(self.parse_expr())
                while self.peek_type() == "COMMA":
                    self.consume("COMMA")
                    args.append(self.parse_expr())
            self.consume("RPAREN")
            return FuncCallNode(tok.value, args)
        return self.parse_atom()

    # atom := NUMBER | STRING | BOOL | field_access | '(' expr ')'
    def parse_atom(self) -> ASTNode:
        tok = self.current
        if tok is None:
            raise DSLSyntaxError("Expressão incompleta")
        if tok.type == "NUMBER":
            self.consume()
            return NumberNode(Decimal(tok.value))
        if tok.type == "STRING":
            self.consume()
            return StringNode(tok.value.strip('"\''))
        if tok.type == "BOOL":
            self.consume()
            return BoolNode(tok.value == "true")
        if tok.type == "IDENT":
            self.consume()
            return FieldNode(tok.value)
        if tok.type == "LPAREN":
            self.consume("LPAREN")
            node = self.parse_expr()
            self.consume("RPAREN")
            return node
        raise DSLSyntaxError(f"Token inesperado: {tok!r}")


def parse_dsl(expression: str) -> ASTNode:
    """Parsa uma expressão DSL e retorna a AST raiz."""
    tokens = tokenize(expression)
    parser = DSLParser(tokens)
    ast = parser.parse_expr()
    if parser.current is not None:
        raise DSLSyntaxError(f"Tokens extras após fim da expressão: {parser.current!r}")
    return ast


# ──────────────────────────────────────────────────
# Evaluator
# ──────────────────────────────────────────────────

class DSLEvaluationError(Exception):
    pass


def _resolve_field(path: str, context: dict[str, Any]) -> Any:
    """
    Resolve um campo dotted como 'transaction.amount' no contexto.
    O contexto pode ter chaves de 1º nível como 'transaction', 'features',
    'player', 'params', etc., cada uma sendo um dict ou objeto Pydantic.
    """
    parts = path.split(".")
    root = parts[0]
    obj = context.get(root)
    if obj is None:
        return None
    for part in parts[1:]:
        if obj is None:
            return None
        if isinstance(obj, dict):
            obj = obj.get(part)
        else:
            obj = getattr(obj, part, None)
    return obj


def _to_decimal(v: Any) -> Decimal:
    try:
        return Decimal(str(v))
    except InvalidOperation:
        raise DSLEvaluationError(f"Não é possível converter {v!r} para Decimal")


def evaluate(node: ASTNode, context: dict[str, Any]) -> Any:
    if isinstance(node, NumberNode):
        return node.value

    if isinstance(node, StringNode):
        return node.value

    if isinstance(node, BoolNode):
        return node.value

    if isinstance(node, FieldNode):
        return _resolve_field(node.path, context)

    if isinstance(node, UnaryNotNode):
        return not evaluate(node.operand, context)

    if isinstance(node, FuncCallNode):
        return _eval_func(node, context)

    if isinstance(node, BinOpNode):
        return _eval_binop(node, context)

    raise DSLEvaluationError(f"Nó AST desconhecido: {type(node)}")


def _eval_func(node: FuncCallNode, context: dict[str, Any]) -> Any:
    args = [evaluate(a, context) for a in node.args]

    if node.name == "zscore":
        if len(args) != 3:
            raise DSLEvaluationError("zscore(value, mean, stddev) requer 3 args")
        v, mean, std = (_to_decimal(a) for a in args)
        if std == 0:
            return Decimal("0")
        return (v - mean) / std

    if node.name == "ratio":
        if len(args) != 2:
            raise DSLEvaluationError("ratio(a, b) requer 2 args")
        a, b = _to_decimal(args[0]), _to_decimal(args[1])
        return a / b if b != 0 else Decimal("0")

    if node.name == "abs":
        if len(args) != 1:
            raise DSLEvaluationError("abs(x) requer 1 arg")
        return abs(_to_decimal(args[0]))

    if node.name in ("sum", "count"):
        # sum/count são resolvidos via features pré-computadas
        # Retorna o valor do contexto de features diretamente
        if len(args) >= 1:
            raw = args[0]
            if raw is not None:
                return _to_decimal(raw)
        return Decimal("0")

    raise DSLEvaluationError(f"Função desconhecida: {node.name!r}")


def _eval_binop(node: BinOpNode, context: dict[str, Any]) -> Any:
    left = evaluate(node.left, context)
    right = evaluate(node.right, context)

    if node.op in (">", "<", ">=", "<=", "==", "!="):
        # Tenta comparação numérica primeiro
        try:
            l_d = _to_decimal(left) if left is not None else None
            r_d = _to_decimal(right) if right is not None else None
            if l_d is not None and r_d is not None:
                left, right = l_d, r_d
        except DSLEvaluationError:
            pass

        if node.op == ">":  return left > right        # type: ignore[operator]
        if node.op == "<":  return left < right        # type: ignore[operator]
        if node.op == ">=": return left >= right       # type: ignore[operator]
        if node.op == "<=": return left <= right       # type: ignore[operator]
        if node.op == "==": return left == right
        if node.op == "!=": return left != right

    if node.op == "in":
        if isinstance(right, str):
            return str(left) in right
        return left in right  # type: ignore[operator]

    if node.op == "contains":
        if isinstance(left, str):
            return str(right) in left
        return right in left   # type: ignore[operator]

    if node.op == "and":
        return bool(left) and bool(right)

    if node.op == "or":
        return bool(left) or bool(right)

    raise DSLEvaluationError(f"Operador desconhecido: {node.op!r}")


# ──────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────

def eval_dsl(expression: str, context: dict[str, Any]) -> bool:
    """
    Avalia uma expressão DSL e retorna True/False.
    context exemplo:
      {
        "transaction": {"amount": 10000, "type": "DEPOSIT", ...},
        "features": {"deposit_sum_24h": 9000, ...},
        "player": {"pepFlag": False, ...},
        "params": {"threshold": 5000},
      }
    """
    ast = parse_dsl(expression)
    result = evaluate(ast, context)
    return bool(result)


def validate_dsl(expression: str) -> tuple[bool, str]:
    """Valida sintaxe da expressão DSL sem avaliar. Retorna (ok, mensagem)."""
    try:
        parse_dsl(expression)
        return True, "OK"
    except DSLSyntaxError as e:
        return False, str(e)
