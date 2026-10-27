from __future__ import annotations

import json
import re

from .model import Location, NavaError, Token


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
                    raise NavaError("a string cannot contain a literal newline; use \\n", loc)
                if source[j] == "\\":
                    j += 2
                    continue
                if source[j] == '"':
                    break
                j += 1
            if j >= n:
                raise NavaError("unterminated string", loc)
            raw = source[start:j + 1]
            try:
                value = json.loads(raw)
            except (ValueError, json.JSONDecodeError) as exc:
                raise NavaError("invalid string escape", loc) from exc
            if "\x00" in value:
                raise NavaError("strings cannot contain a NUL character", loc)
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise NavaError("string contains an invalid Unicode surrogate", loc) from exc
            tokens.append(Token("STRING", value, loc))
            width = j + 1 - i
            i = j + 1
            col += width
        elif "0" <= ch <= "9":
            start = i
            while i < n and (("0" <= source[i] <= "9") or source[i] == "_"):
                i += 1
            raw = source[start:i]
            if not re.fullmatch(r"[0-9]+(?:_[0-9]+)*", raw):
                raise NavaError("invalid underscore in integer literal", loc)
            digits = raw.replace("_", "").lstrip("0") or "0"
            # Reject enormous literals before int() hits Python's digit limit.
            if len(digits) > 19:
                raise NavaError("integer literal is outside the i64 range", loc)
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
                    raise NavaError("unmatched ')'", loc)
                parens.pop()
            tokens.append(Token(ch, ch, loc))
            i += 1
            col += 1
        else:
            raise NavaError(f"unexpected character {ch!r}", loc)
    if parens:
        raise NavaError("unclosed '('", parens[-1])
    tokens.append(Token("EOF", "", Location(line, col)))
    return tokens
