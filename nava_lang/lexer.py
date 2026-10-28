from __future__ import annotations

import json
import re

from .diagnostics import make
from .limits import MAX_FLOAT_DIGITS, MAX_LITERAL_DIGITS
from .model import Location, Token


KEYWORDS = {
    "fn": "FN", "let": "LET", "if": "IF", "else": "ELSE",
    "while": "WHILE", "return": "RETURN", "true": "TRUE", "false": "FALSE",
    "mut": "MUT", "for": "FOR", "in": "IN", "break": "BREAK",
    "continue": "CONTINUE", "import": "IMPORT",
    # Edition 2 keywords. They are keywords in every edition so that an
    # edition-1 file cannot use them as identifiers and then change meaning.
    "struct": "STRUCT", "edition": "EDITION", "enum": "ENUM", "match": "MATCH",
    "defer": "DEFER", "pub": "PUB",
}
TWO_CHAR = {"->", "<=", ">=", "==", "!=", "&&", "||", "..", "::", "=>"}
ONE_CHAR = set("(){}[],:+-*/%=!<>;.?")
#: Decimal floating-point literal: the integer part may not use underscores, so
#: `1_0.5` is a malformed token rather than a silently different number.
FLOAT_RE = re.compile(r"[0-9][0-9]*(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")

def lex(source: str, path: str | None = None) -> list[Token]:
    """Tokens of `source`. `path` is stamped on every location for multi-file use."""
    tokens = _lex(source)
    if path is None:
        return tokens
    return [Token(token.kind, token.value, Location(token.loc.line, token.loc.column, path))
            for token in tokens]


def _lex(source: str) -> list[Token]:
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    tokens: list[Token] = []
    i, line, col = 0, 1, 1
    # Open `(` and `[` delimiters, innermost last. A newline inside either is
    # not a statement separator, so an argument list or an array literal may
    # be split across lines; outside them it is.
    open_delimiters: list[tuple[str, Location]] = []
    n = len(source)

    while i < n:
        ch = source[i]
        loc = Location(line, col)
        if ch in " \t\f\v":
            i += 1
            col += 1
        elif ch == "\n":
            if not open_delimiters:
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
            float_match = FLOAT_RE.match(source, i)
            if float_match and ("." in float_match.group() or "e" in float_match.group()
                               or "E" in float_match.group()):
                raw = float_match.group()
                end = loc.advance(raw)
                following = source[float_match.end():float_match.end() + 1]
                if following in ("e", "E"):
                    raise make("E0346", "malformed floating-point exponent", loc, end,
                               "write an exponent as e12 or E-3, with digits after the sign")
                digits = sum(1 for character in raw if character.isdigit())
                if digits > MAX_FLOAT_DIGITS:
                    raise make("E0346", "floating-point literal is too long", loc, end,
                               f"at most {MAX_FLOAT_DIGITS} digits are accepted")
                value = float(raw)
                if value in (float("inf"), float("-inf")):
                    raise make("E0346", "floating-point literal is outside the f64 range",
                               loc, end, "f64 is IEEE-754 binary64: about 1.8e308 at most")
                tokens.append(Token("FLOAT", value, loc))
                col += len(raw)
                i = float_match.end()
                continue
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
        elif ch == "." and tokens and tokens[-1].kind in ("NUMBER", "FLOAT"):
            # `1.` and `1.5.5` are not literals and not a field access: keep the
            # error at the token that caused it.
            raise make("E0101", "unexpected character '.'", loc, loc.advance(ch),
                       hint="a field access needs a value on the left, as in p.x")
        elif ch in ONE_CHAR:
            if ch in "([":
                open_delimiters.append((ch, loc))
            elif ch in ")]":
                closing = ")" if ch == ")" else "]"
                expected = "(" if ch == ")" else "["
                if not open_delimiters or open_delimiters[-1][0] != expected:
                    code = "E0109" if ch == ")" else "E0112"
                    hint = None
                    if open_delimiters and open_delimiters[-1][0] != expected:
                        hint = (f"the '{open_delimiters[-1][0]}' opened at line "
                                f"{open_delimiters[-1][1].line} is still open")
                    raise make(code, f"unmatched {closing!r}", loc, loc.advance(closing), hint)
                open_delimiters.pop()
            tokens.append(Token(ch, ch, loc))
            i += 1
            col += 1
        else:
            raise make("E0101", f"unexpected character {ch!r}", loc, loc.advance(ch),
                       "identifiers use ASCII letters, digits and '_'")
    if open_delimiters:
        kind, where = open_delimiters[-1]
        if kind == "(":
            raise make("E0110", "unclosed '('", where, where.advance("("))
        raise make("E0113", "unclosed '['", where, where.advance("["),
                   "close the array literal or type with ']'")
    tokens.append(Token("EOF", "", Location(line, col)))
    return tokens
