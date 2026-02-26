# DSL Parser for Rules Engine
# Simple rule language parser supporting: operators, functions, features, baselines

import re
from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass
from enum import Enum


class OperatorEnum(str, Enum):
    GT = ">"
    LT = "<"
    GTE = ">="
    LTE = "<="
    EQ = "=="
    NEQ = "!="
    IN = "in"
    CONTAINS = "contains"
    AND = "and"
    OR = "or"
    NOT = "not"


@dataclass
class Token:
    type: str  # NUMBER, IDENTIFIER, OPERATOR, STRING, LPAREN, RPAREN, COMMA
    value: Any
    position: int


class Tokenizer:
    """Simple tokenizer for DSL."""
    
    def __init__(self, expression: str):
        self.expression = expression
        self.position = 0
        self.tokens: List[Token] = []
    
    def tokenize(self) -> List[Token]:
        while self.position < len(self.expression):
            self._skip_whitespace()
            if self.position >= len(self.expression):
                break
            
            # Try to match token types
            if self._try_string():
                continue
            elif self._try_number():
                continue
            elif self._try_operator():
                continue
            elif self._try_identifier():
                continue
            elif self._try_punctuation():
                continue
            else:
                raise SyntaxError(f"Unknown character at position {self.position}: {self.expression[self.position]}")
        
        return self.tokens
    
    def _skip_whitespace(self):
        while self.position < len(self.expression) and self.expression[self.position].isspace():
            self.position += 1
    
    def _try_string(self) -> bool:
        if self.expression[self.position] in ('"', "'"):
            quote = self.expression[self.position]
            start = self.position
            self.position += 1
            string_value = ""
            while self.position < len(self.expression) and self.expression[self.position] != quote:
                string_value += self.expression[self.position]
                self.position += 1
            if self.position >= len(self.expression):
                raise SyntaxError(f"Unterminated string at position {start}")
            self.position += 1
            self.tokens.append(Token("STRING", string_value, start))
            return True
        return False
    
    def _try_number(self) -> bool:
        start = self.position
        if self.expression[self.position].isdigit() or (
            self.expression[self.position] == '.' and 
            self.position + 1 < len(self.expression) and 
            self.expression[self.position + 1].isdigit()
        ):
            num_str = ""
            while self.position < len(self.expression) and (self.expression[self.position].isdigit() or self.expression[self.position] == '.'):
                num_str += self.expression[self.position]
                self.position += 1
            value = float(num_str) if '.' in num_str else int(num_str)
            self.tokens.append(Token("NUMBER", value, start))
            return True
        return False
    
    def _try_operator(self) -> bool:
        start = self.position
        # Try multi-char operators first
        for op in [">=", "<=", "==", "!="]:
            if self.expression[start:start+2] == op:
                self.tokens.append(Token("OPERATOR", op, start))
                self.position += 2
                return True
        # Try single-char operators
        if self.expression[self.position] in "><":
            op = self.expression[self.position]
            self.tokens.append(Token("OPERATOR", op, start))
            self.position += 1
            return True
        return False
    
    def _try_identifier(self) -> bool:
        start = self.position
        if self.expression[self.position].isalpha() or self.expression[self.position] == '_':
            ident = ""
            while (self.position < len(self.expression) and 
                   (self.expression[self.position].isalnum() or self.expression[self.position] in ('_', '.'))):
                ident += self.expression[self.position]
                self.position += 1
            
            # Check if it's a keyword
            if ident in ("and", "or", "not", "in", "contains", "true", "false"):
                self.tokens.append(Token("KEYWORD", ident, start))
            else:
                self.tokens.append(Token("IDENTIFIER", ident, start))
            return True
        return False
    
    def _try_punctuation(self) -> bool:
        if self.expression[self.position] == '(':
            self.tokens.append(Token("LPAREN", "(", self.position))
            self.position += 1
            return True
        elif self.expression[self.position] == ')':
            self.tokens.append(Token("RPAREN", ")", self.position))
            self.position += 1
            return True
        elif self.expression[self.position] == ',':
            self.tokens.append(Token("COMMA", ",", self.position))
            self.position += 1
            return True
        return False


@dataclass
class ASTNode:
    """Abstract Syntax Tree Node."""
    type: str
    value: Any = None
    left: Optional['ASTNode'] = None
    right: Optional['ASTNode'] = None
    children: Optional[List['ASTNode']] = None


class Parser:
    """Simple recursive descent parser for DSL."""
    
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.position = 0
    
    def parse(self) -> ASTNode:
        return self._parse_or()
    
    def _current_token(self) -> Optional[Token]:
        return self.tokens[self.position] if self.position < len(self.tokens) else None
    
    def _consume(self, expected_type: Optional[str] = None) -> Token:
        token = self._current_token()
        if not token:
            raise SyntaxError("Unexpected end of input")
        if expected_type and token.type != expected_type:
            raise SyntaxError(f"Expected {expected_type}, got {token.type}")
        self.position += 1
        return token
    
    def _parse_or(self) -> ASTNode:
        left = self._parse_and()
        while self._current_token() and self._current_token().type == "KEYWORD" and self._current_token().value == "or":
            self._consume()
            right = self._parse_and()
            left = ASTNode("OR", left=left, right=right)
        return left
    
    def _parse_and(self) -> ASTNode:
        left = self._parse_not()
        while self._current_token() and self._current_token().type == "KEYWORD" and self._current_token().value == "and":
            self._consume()
            right = self._parse_not()
            left = ASTNode("AND", left=left, right=right)
        return left
    
    def _parse_not(self) -> ASTNode:
        if self._current_token() and self._current_token().type == "KEYWORD" and self._current_token().value == "not":
            self._consume()
            operand = self._parse_comparison()
            return ASTNode("NOT", left=operand)
        return self._parse_comparison()
    
    def _parse_comparison(self) -> ASTNode:
        left = self._parse_function_or_primary()
        
        token = self._current_token()
        if token and token.type == "OPERATOR":
            op = self._consume().value
            right = self._parse_function_or_primary()
            return ASTNode("COMPARISON", value=op, left=left, right=right)
        elif token and token.type == "KEYWORD" and token.value in ("in", "contains"):
            op = self._consume().value
            right = self._parse_function_or_primary()
            return ASTNode("COMPARISON", value=op, left=left, right=right)
        
        return left
    
    def _parse_function_or_primary(self) -> ASTNode:
        token = self._current_token()
        if not token:
            raise SyntaxError("Expected expression")
        
        # Check if it's a function call
        if token.type == "IDENTIFIER" and self.position + 1 < len(self.tokens) and self.tokens[self.position + 1].type == "LPAREN":
            func_name = self._consume().value
            self._consume("LPAREN")
            args = []
            while self._current_token() and self._current_token().type != "RPAREN":
                args.append(self._parse_or())
                if self._current_token() and self._current_token().type == "COMMA":
                    self._consume()
            self._consume("RPAREN")
            return ASTNode("FUNCTION", value=func_name, children=args)
        
        return self._parse_primary()
    
    def _parse_primary(self) -> ASTNode:
        token = self._current_token()
        if not token:
            raise SyntaxError("Expected primary expression")
        
        if token.type == "NUMBER":
            return ASTNode("LITERAL", value=self._consume().value)
        elif token.type == "STRING":
            return ASTNode("LITERAL", value=self._consume().value)
        elif token.type == "KEYWORD" and token.value in ("true", "false"):
            value = self._consume().value == "true"
            return ASTNode("LITERAL", value=value)
        elif token.type == "IDENTIFIER":
            return ASTNode("IDENTIFIER", value=self._consume().value)
        elif token.type == "LPAREN":
            self._consume()
            expr = self._parse_or()
            self._consume("RPAREN")
            return expr
        else:
            raise SyntaxError(f"Unexpected token: {token.type}")


class RuleEvaluator:
    """Evaluates parsed DSL rules against event context."""
    
    def __init__(self, context: Dict[str, Any]):
        """
        context should include:
        - transaction: current transaction object
        - bet: current bet object
        - features: computed features dict
        - player: player profile
        """
        self.context = context
    
    def evaluate(self, ast: ASTNode) -> Any:
        if ast.type == "LITERAL":
            return ast.value
        elif ast.type == "IDENTIFIER":
            return self._resolve_identifier(ast.value)
        elif ast.type == "COMPARISON":
            return self._evaluate_comparison(ast)
        elif ast.type == "AND":
            return self.evaluate(ast.left) and self.evaluate(ast.right)
        elif ast.type == "OR":
            return self.evaluate(ast.left) or self.evaluate(ast.right)
        elif ast.type == "NOT":
            return not self.evaluate(ast.left)
        elif ast.type == "FUNCTION":
            return self._evaluate_function(ast)
        else:
            raise ValueError(f"Unknown AST node type: {ast.type}")
    
    def _resolve_identifier(self, identifier: str) -> Any:
        """Resolve identifier to value from context."""
        parts = identifier.split(".")
        current = self.context
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                current = getattr(current, part, None)
            if current is None:
                return None
        return current
    
    def _evaluate_comparison(self, node: ASTNode) -> bool:
        left = self.evaluate(node.left)
        right = self.evaluate(node.right)
        op = node.value
        
        if op == ">":
            return left > right
        elif op == "<":
            return left < right
        elif op == ">=":
            return left >= right
        elif op == "<=":
            return left <= right
        elif op == "==":
            return left == right
        elif op == "!=":
            return left != right
        elif op == "in":
            return left in right
        elif op == "contains":
            return right in left
        else:
            raise ValueError(f"Unknown operator: {op}")
    
    def _evaluate_function(self, node: ASTNode) -> Any:
        func_name = node.value
        args = [self.evaluate(arg) for arg in node.children or []]
        
        if func_name == "sum":
            return sum(args)
        elif func_name == "count":
            return len(args)
        elif func_name == "avg":
            return sum(args) / len(args) if args else 0
        elif func_name == "max":
            return max(args)
        elif func_name == "min":
            return min(args)
        elif func_name == "zscore":
            if len(args) < 2:
                raise ValueError("zscore requires at least 2 arguments")
            value, baseline = args[0], args[1]
            stddev = args[2] if len(args) > 2 else 1
            return (value - baseline) / stddev if stddev != 0 else 0
        else:
            raise ValueError(f"Unknown function: {func_name}")


def parse_rule_dsl(expression: str) -> ASTNode:
    """Parse DSL expression into AST."""
    tokenizer = Tokenizer(expression)
    tokens = tokenizer.tokenize()
    parser = Parser(tokens)
    return parser.parse()


def evaluate_rule(expression: str, context: Dict[str, Any]) -> bool:
    """Parse and evaluate rule expression in given context."""
    ast = parse_rule_dsl(expression)
    evaluator = RuleEvaluator(context)
    result = evaluator.evaluate(ast)
    return bool(result)
