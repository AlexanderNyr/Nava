from __future__ import annotations

import json
import re

from .diagnostics import make
from .limits import MAX_LITERAL_DIGITS
from .model import Location, Token


KEYWORDS = {
    "fn": "FN", "let": "LET", "if": "IF", "else": "ELSE",
    "while": "WHILE", "return": "RETURN", "true": "TRUE", "false": "FALSE",
}
TWO_CHAR = {"->", "<=", ">=", "==", "!=", "&&", "||"}
ONE_CHAR = set("(){}:,+-*/%=!<>;")

def lex(source: str) -> list[Token]:
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    tokens: list[Token] = []
    i, line, col = 0, 1, 1
    parens: list[Location] = []
    n = len(source)

    while i < n:
        ch = source[i]
        loc = Location(line, col)
        if ch in " \t\f\v":
            i += 1
            col += 1
        elif ch == "\n":
            if not parens:
                tokens.append(Token("NL", "\n", loc))
            i += 1
            line += 1
            col = 1
        elif ch == "#" or source.startswith("//", i):
            while i < n and source[i] != "\n":
                i += 1
                col += 1
        elif ch == '"':
            start = i
            j = i + 1
            while j < n:
                if source[j] == "\n":
                    raise make("E0103", "a string cannot contain a literal newline; use \\n", loc)
                if source[j] == "\\":
                    j += 2
                    continue
                if source[j] == '"':
                    break
                j += 1
            if j >= n:
                raise make("E0102", "unterminated string", loc)
            raw = source[start:j + 1]
            try:
                value = json.loads(raw)
            except (ValueError, json.JSONDecodeError) as exc:
                raise make("E0104", "invalid string escape", loc, loc.advance(raw)) from exc
            if "\x00" in value:
                raise make("E0105", "strings cannot contain a NUL character", loc, loc.advance(raw))
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise make("E0106", "string contains an invalid Unicode surrogate",
                           loc, loc.advance(raw)) from exc
            tokens.append(Token("STRING", value, loc))
            width = j + 1 - i
            i = j + 1
            col += width
        elif "0" <= ch <= "9":
            start = i
            while i < n and (("0" <= source[i] <= "9") or source[i] == "_"):
                i += 1
            raw = source[start:i]
            end = loc.advance(raw)
            if not re.fullmatch(r"[0-9]+(?:_[0-9]+)*", raw):
                raise make("E0107", "invalid underscore in integer literal", loc, end,
                           "digits may be separated by single underscores, as in 1_000_000")
            digits = raw.replace("_", "").lstrip("0") or "0"
            # Reject enormous literals before int() hits Python's digit limit.
            if len(digits) > MAX_LITERAL_DIGITS:
                raise make("E0108", "integer literal is outside the i64 range", loc, end,
                           "i64 holds -9223372036854775808 to 9223372036854775807")
            tokens.append(Token("NUMBER", int(digits), loc))
            col += i - start
        elif ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ch == "_":
            start = i
            while i < n and (source[i].isascii() and (source[i].isalnum() or source[i] == "_")):
                i += 1
            raw = source[start:i]
            tokens.append(Token(KEYWORDS.get(raw, "IDENT"), raw, loc))
            col += i - start
        elif source[i:i + 2] in TWO_CHAR:
            op = source[i:i + 2]
            tokens.append(Token(op, op, loc))
            i += 2
            col += 2
        elif ch in ONE_CHAR:
            if ch == "(":
                parens.append(loc)
            elif ch == ")":
                if not parens:
                    raise make("E0109", "unmatched ')'", loc, loc.advance(")"))
                parens.pop()
            tokens.append(Token(ch, ch, loc))
            i += 1
            col += 1
        else:
            raise make("E0101", f"unexpected character {ch!r}", loc, loc.advance(ch),
                       "identifiers use ASCII letters, digits and '_'")
    if parens:
        raise make("E0110", "unclosed '('", parens[-1], parens[-1].advance("("))
    tokens.append(Token("EOF", "", Location(line, col)))
    return tokens
