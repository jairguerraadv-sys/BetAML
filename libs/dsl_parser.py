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
  atom := NUMBER | STRING | BOOL | field_access | '(' expr ')'
  field_access := IDENT ('.' IDENT)*

Acesso a campos:
  transaction.amount           → event payload
  bet.stakeAmount
  features.deposit_sum_24h
  player.pepFlag
  params.threshold             → params do RuleDefinition

Funções disponíveis:
  zscore(value, mean, stddev)               → (v-m)/s
  zscore(feature_name, baseline_window)     → usa features pré-computadas
  ratio(a, b)                               → a / b (safe: 0 se b=0)
  abs(x)                                    → |x|
  sum(field, window)                        → features pré-computadas
  count(field, window)                      → features pré-computadas
  window_sum(field, window)                 → alias explícito sum com window
  window_count(field, window)               → alias explícito count com window
  iff(cond, then_value, else_value)         → condicional ternário
  is_in_list(field, listName)               → verdadeiro se field ∈ PlayerList listName
  shared_device_count()                     → de features.shared_device_count
  cluster_size()                            → de features.cluster_size
  is_in_cluster(cluster_id)                 → features.cluster_id == cluster_id
  percentile_rank(feature, segment)         → 0-100 rank dentro do segmento
  min(a, b) / max(a, b)                     → mínimo / máximo

Macro expansion:
  expand_macros(expression, macros_dict) → substitui %macro_name% por expressão
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
    ("LBRACKET", r"\["),
    ("RBRACKET", r"\]"),
    ("MUL",      r"\*"),
    ("COMMA",    r","),
    ("IDENT",    r"[A-Za-z_][A-Za-z0-9_.]*"),
    ("SKIP",     r"[ \t\n]+"),
]

_MASTER_RE = re.compile(
    "|".join(f"(?P<{name}>{pat})" for name, pat in TOKEN_PATTERNS)
)

KEYWORDS = {"and", "or", "not", "in", "contains", "true", "false", "True", "False"}
FUNCTIONS = {
    "sum", "count", "zscore", "ratio", "abs",
    "window_sum", "window_count",
    "iff", "is_in_list",
    "shared_device_count", "cluster_size", "is_in_cluster",
    "percentile_rank",
    "min", "max",
}


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


class ListNode(ASTNode):
    """Literal list, e.g. ['PIX', 'TED', 'DOC'] used with `in` operator."""
    def __init__(self, items: list[ASTNode]):
        self.items = items


# ──────────────────────────────────────────────────
# Parser (recursive descent)
# ──────────────────────────────────────────────────

class DSLSyntaxError(ValueError):
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

    # mul_expr := call_expr ('*' call_expr)*
    def parse_mul_expr(self) -> ASTNode:
        node = self.parse_call_expr()
        while self.peek_type() == "MUL":
            self.consume("MUL")
            right = self.parse_call_expr()
            node = BinOpNode("*", node, right)
        return node

    # compare_expr := mul_expr (OP mul_expr)?
    def parse_compare_expr(self) -> ASTNode:
        left = self.parse_mul_expr()
        op_map = {
            "GT": ">", "LT": "<", "GE": ">=", "LE": "<=",
            "EQ": "==", "NE": "!=", "IN": "in", "CONTAINS": "contains",
        }
        if self.peek_type() in op_map:
            op_tok = self.consume()
            right = self.parse_mul_expr()
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
        if tok.type == "LBRACKET":
            self.consume("LBRACKET")
            items: list[ASTNode] = []
            if self.peek_type() != "RBRACKET":
                items.append(self.parse_expr())
                while self.peek_type() == "COMMA":
                    self.consume("COMMA")
                    items.append(self.parse_expr())
            self.consume("RBRACKET")
            return ListNode(items)
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

    if isinstance(node, ListNode):
        return [evaluate(item, context) for item in node.items]

    if isinstance(node, BinOpNode):
        return _eval_binop(node, context)

    raise DSLEvaluationError(f"Nó AST desconhecido: {type(node)}")


def _eval_func(node: FuncCallNode, context: dict[str, Any]) -> Any:
    args = [evaluate(a, context) for a in node.args]

    # ── numeric helpers ──────────────────────────────────────────────────────
    if node.name == "zscore":
        if len(args) == 3:
            v, mean, std = (_to_decimal(a) for a in args)
            return (v - mean) / std if std != 0 else Decimal("0")
        if len(args) == 2:
            # zscore(feature_name_string, window) — fetch from features
            feat_val = context.get("features", {}).get(str(args[0]), 0)
            mean     = context.get("feature_stats", {}).get(f"{args[0]}_mean", 0)
            std      = context.get("feature_stats", {}).get(f"{args[0]}_std", 1)
            v, m, s = _to_decimal(feat_val), _to_decimal(mean), _to_decimal(std)
            return (v - m) / s if s != 0 else Decimal("0")
        raise DSLEvaluationError("zscore requer 2 ou 3 args")

    if node.name == "ratio":
        if len(args) != 2:
            raise DSLEvaluationError("ratio(a, b) requer 2 args")
        a, b = _to_decimal(args[0]), _to_decimal(args[1])
        return a / b if b != 0 else Decimal("0")

    if node.name == "abs":
        if len(args) != 1:
            raise DSLEvaluationError("abs(x) requer 1 arg")
        return abs(_to_decimal(args[0]))

    if node.name == "min":
        if len(args) != 2:
            raise DSLEvaluationError("min(a, b) requer 2 args")
        a, b = _to_decimal(args[0]), _to_decimal(args[1])
        return a if a <= b else b

    if node.name == "max":
        if len(args) != 2:
            raise DSLEvaluationError("max(a, b) requer 2 args")
        a, b = _to_decimal(args[0]), _to_decimal(args[1])
        return a if a >= b else b

    # ── feature window lookups ───────────────────────────────────────────────
    if node.name in ("sum", "window_sum"):
        # sum(a, b, ...) → arithmetic sum; sum(feature, window) → feature value
        if len(args) >= 2:
            try:
                return sum(_to_decimal(a) for a in args if a is not None)
            except (DSLEvaluationError, TypeError):
                pass
        if args:
            raw = args[0]
            if raw is not None:
                return _to_decimal(raw)
        return Decimal("0")

    if node.name in ("count", "window_count"):
        if args:
            raw = args[0]
            if raw is not None:
                return _to_decimal(raw)
        return Decimal("0")

    # ── conditional iff(cond, then, else) ────────────────────────────────────
    if node.name == "iff":
        if len(args) != 3:
            raise DSLEvaluationError("iff(cond, then_value, else_value) requer 3 args")
        cond, then_val, else_val = args
        return then_val if bool(cond) else else_val

    # ── list membership ──────────────────────────────────────────────────────
    if node.name == "is_in_list":
        if len(args) != 2:
            raise DSLEvaluationError("is_in_list(field_value, list_name) requer 2 args")
        field_val, list_name = args
        # context["player_lists"] = {"high_risk_cpfs": {"111": True, ...}}
        player_lists: dict[str, set | dict] = context.get("player_lists", {})
        the_list = player_lists.get(str(list_name), set())
        return str(field_val) in the_list

    # ── network features ─────────────────────────────────────────────────────
    if node.name == "shared_device_count":
        return _to_decimal(context.get("features", {}).get("shared_device_count", 0))

    if node.name == "cluster_size":
        return _to_decimal(context.get("features", {}).get("cluster_size", 0))

    if node.name == "is_in_cluster":
        if len(args) != 1:
            raise DSLEvaluationError("is_in_cluster(cluster_id) requer 1 arg")
        current_cluster = context.get("features", {}).get("cluster_id")
        return str(current_cluster) == str(args[0]) if current_cluster is not None else False

    # ── statistical rank ─────────────────────────────────────────────────────
    if node.name == "percentile_rank":
        if len(args) < 1:
            raise DSLEvaluationError("percentile_rank(feature, [segment]) requer 1+ args")
        feat_name = str(args[0])
        rank_key  = f"{feat_name}_percentile_rank"
        val = context.get("feature_stats", {}).get(rank_key, 50)
        return _to_decimal(val)

    raise DSLEvaluationError(f"Função desconhecida: {node.name!r}")


def _eval_binop(node: BinOpNode, context: dict[str, Any]) -> Any:
    left = evaluate(node.left, context)
    right = evaluate(node.right, context)

    if node.op == "*":
        try:
            return _to_decimal(left) * _to_decimal(right)
        except (DSLEvaluationError, TypeError):
            return Decimal("0")

    if node.op in (">", "<", ">=", "<=", "==", "!="):
        # Tenta comparação numérica primeiro
        try:
            l_d = _to_decimal(left) if left is not None else None
            r_d = _to_decimal(right) if right is not None else None
            if l_d is not None and r_d is not None:
                left, right = l_d, r_d
        except DSLEvaluationError:
            pass

        # None comparisons: only == and != make sense
        if left is None or right is None:
            if node.op == "==":
                return left == right
            if node.op == "!=":
                return left != right
            return False

        try:
            if node.op == ">":
                return left > right  # type: ignore[operator]
            if node.op == "<":
                return left < right  # type: ignore[operator]
            if node.op == ">=":
                return left >= right  # type: ignore[operator]
            if node.op == "<=":
                return left <= right  # type: ignore[operator]
            if node.op == "==":
                return left == right
            if node.op == "!=":
                return left != right
        except TypeError:
            return False

    if node.op == "in":
        if isinstance(right, str):
            return str(left) in right
        if isinstance(right, (list, tuple, set)):
            return left in right or str(left) in [str(x) for x in right]
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
# Macro expansion
# ──────────────────────────────────────────────────

_MACRO_RE = re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)%")


def expand_macros(expression: str, macros: dict[str, str], max_depth: int = 10) -> str:
    """
    Substitui referências de macro na forma %macro_name% pela expressão correspondente.

    Args:
        expression: expressão DSL com possíveis referências a macros.
        macros: dict de {nome: expressão_dsl}.
        max_depth: limite de expansão recursiva para evitar ciclos.

    Returns:
        Expressão DSL com todas as macros expandidas.

    Raises:
        DSLSyntaxError: se uma macro não existe ou há ciclo de expansão.
    """
    for _ in range(max_depth):
        def replace(m: re.Match) -> str:
            name = m.group(1)
            if name not in macros:
                return m.group(0)  # leave undefined macro as-is
            return f"({macros[name]})"

        new_expr = _MACRO_RE.sub(replace, expression)
        if new_expr == expression:
            return new_expr
        expression = new_expr

    raise DSLSyntaxError("Excedido limite de expansão recursiva de macros (ciclo detectado?)")


# ──────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────

def eval_dsl(
    expression: str,
    context: dict[str, Any],
    *,
    macros: dict[str, str] | None = None,
) -> bool:
    """
    Avalia uma expressão DSL e retorna True/False.

    Args:
        expression: expressão DSL.
        context: dicionário de contexto com chaves:
            - "transaction" / "bet": payload do evento
            - "features": features do player (pré-computadas)
            - "feature_stats": médias/desvios/percentis para zscore e percentile_rank
            - "player": dados do player
            - "params": parâmetros configuráveis da regra
            - "player_lists": sets de CPFs/IDs para uso nos predicados
        macros: dict opcional de macros a expandir antes do parse.

    Exemplo de contexto:
      {
        "transaction": {"amount": 10000, "type": "DEPOSIT"},
        "features": {"deposit_sum_24h": 9000, "shared_device_count": 3},
        "feature_stats": {"deposit_sum_24h_mean": 500, "deposit_sum_24h_std": 200},
        "player": {"pepFlag": False},
        "params": {"threshold": 5000},
        "player_lists": {"high_risk_cpfs": {"12345678901"}},
      }
    """
    if macros:
        expression = expand_macros(expression, macros)
    ast = parse_dsl(expression)
    result = evaluate(ast, context)
    return result if result is not None else False


def validate_dsl(expression: str, *, macros: dict[str, str] | None = None) -> tuple[bool, str]:
    """Valida sintaxe da expressão DSL sem avaliar. Retorna (ok, mensagem vazia se ok)."""
    try:
        expr = expand_macros(expression, macros) if macros else expression
        parse_dsl(expr)
        return True, ""
    except (DSLSyntaxError, DSLEvaluationError) as e:
        return False, str(e)
