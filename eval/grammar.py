#!/usr/bin/env python3
"""Nava's grammar in the two forms an A/B run needs (PLAN.md phase N7).

One description, two back ends:

* **tokens** — the rules are matched against the compiler's own token stream,
  so the conformance corpus is checked against the real lexer and parser.
* **characters** — the same rules are emitted as GBNF text, which is what an
  inference engine consumes for grammar-constrained decoding. The emitted text
  is parsed back and recognised (`eval/gbnf.py`), so the artefact is executed in
  the tests rather than trusted.

The grammar is a *syntax* filter. It cannot prove types, ownership or runtime
behaviour: `program ::= ...` accepts `1 + true` (type error) and `1 / 0`
(runtime error). `tests/test_grammar.py` measures exactly that boundary.

Everything that only exists to model layout — whitespace, comments, separators —
is defined once per back end in `LAYOUT_CHARS` / `TOKEN_TERMINALS`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import gbnf  # noqa: E402
from nava_lang.lexer import lex  # noqa: E402
from nava_lang.model import NavaError  # noqa: E402

SCHEMA = "nava-grammar/1"

# ---------------------------------------------------------------------------
# Terminals
# ---------------------------------------------------------------------------

QUOTE = r'"\""'            # GBNF literal for one double quote: "\""
BACKSLASH = r'"\\"'          # GBNF literal for one backslash: "\\"
NOT_QUOTE = r'[^"\\\n]'     # any character except a quote, a backslash or a newline
STRING_PATTERN = f"{QUOTE} ({NOT_QUOTE} | {BACKSLASH} .)* {QUOTE}"
TYPE_PATTERN = '"i64" | "bool" | "str" | "f64" | "void"'
ELEMENT_PATTERN = '"i64" | "bool" | "str"'


def _terminal(kinds: set[str], pattern: str, values: set[str] | None = None) -> dict:
    return {"kinds": frozenset(kinds), "values": None if values is None
            else frozenset(values), "pattern": pattern}


#: name -> how to match it as a token, and how to spell it in GBNF.
TERMINALS: dict[str, dict] = {
    "IDENT": _terminal({"IDENT"}, "[a-zA-Z_] [a-zA-Z0-9_]*"),
    "NUMBER": _terminal({"NUMBER"}, "[0-9] [0-9_]*"),
    #: Mirrors `FLOAT_RE` in `nava_lang/lexer.py`: the fraction and the exponent
    #: are each optional, and at least one of them must be present for the lexer
    #: to call the token a float. The grammar keeps both terminals permissive and
    #: lets the compiler reject a literal that is out of range.
    "FLOAT": _terminal({"FLOAT"}, '[0-9]+ ("." [0-9]+)? ([eE] [-+]? [0-9]+)?'),
    "STRING": _terminal({"STRING"}, STRING_PATTERN),
    "TYPE": _terminal({"IDENT"}, TYPE_PATTERN, {"i64", "bool", "str", "f64", "void"}),
    "ELEM": _terminal({"IDENT"}, ELEMENT_PATTERN, {"i64", "bool", "str"}),
    "FN": _terminal({"FN"}, '"fn"'),
    "LET": _terminal({"LET"}, '"let"'),
    "IF": _terminal({"IF"}, '"if"'),
    "ELSE": _terminal({"ELSE"}, '"else"'),
    "WHILE": _terminal({"WHILE"}, '"while"'),
    "FOR": _terminal({"FOR"}, '"for"'),
    "IN": _terminal({"IN"}, '"in"'),
    "BREAK": _terminal({"BREAK"}, '"break"'),
    "CONTINUE": _terminal({"CONTINUE"}, '"continue"'),
    "RETURN": _terminal({"RETURN"}, '"return"'),
    "IMPORT": _terminal({"IMPORT"}, '"import"'),
    "MUT": _terminal({"MUT"}, '"mut"'),
    "TRUE": _terminal({"TRUE"}, '"true"'),
    "FALSE": _terminal({"FALSE"}, '"false"'),
    "ARROW": _terminal({"->"}, '"->"'),
    "EQ": _terminal({"="}, '"="'),
    "EQEQ": _terminal({"=="}, '"=="'),
    "NEQ": _terminal({"!="}, '"!="'),
    "LE": _terminal({"<="}, '"<="'),
    "GE": _terminal({">="}, '">="'),
    "LT": _terminal({"<"}, '"<"'),
    "GT": _terminal({">"}, '">"'),
    "PLUS": _terminal({"+"}, '"+"'),
    "MINUS": _terminal({"-"}, '"-"'),
    "STAR": _terminal({"*"}, '"*"'),
    "SLASH": _terminal({"/"}, '"/"'),
    "PERCENT": _terminal({"%"}, '"%"'),
    "ANDAND": _terminal({"&&"}, '"&&"'),
    "OROR": _terminal({"||"}, '"||"'),
    "BANG": _terminal({"!"}, '"!"'),
    "COLON": _terminal({":"}, '":"'),
    "COLONCOLON": _terminal({"::"}, '"::"'),
    "COMMA": _terminal({","}, '","'),
    "RANGE": _terminal({".."}, '".."'),
    "LPAREN": _terminal({"("}, '"("'),
    "RPAREN": _terminal({")"}, '")"'),
    "LBRACKET": _terminal({"["}, '"["'),
    "RBRACKET": _terminal({"]"}, '"]"'),
    "LBRACE": _terminal({"{"}, '"{"'),
    "RBRACE": _terminal({"}"}, '"}"'),
}

#: The statement separator, matched as one token in token mode.
SEPTOK = _terminal({"NL", ";"}, "")

# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

N = lambda name: ("n", name)          # noqa: E731 - a rule reference
T = lambda name: ("t", name)          # noqa: E731 - a terminal

CORE: dict[str, list[list[tuple]]] = {
    # program ::= SEP* item (SEP+ item)* SEP*
    "program": [[N("pad"), N("sep_star"), N("item"), N("items"), N("sep_star"), N("end_pad")]],
    "items": [[N("sep_plus"), N("item"), N("items")], []],
    "sep_star": [[N("SEP"), N("sep_star")], []],
    "sep_plus": [[N("SEP"), N("sep_plus")], [N("SEP")]],

    "item": [[T("IMPORT"), T("IDENT")], [N("function")]],
    "function": [[T("FN"), T("IDENT"), T("LPAREN"), N("params_opt"), T("RPAREN"),
                  N("returns"), N("block")]],
    "returns": [[T("ARROW"), N("type")], []],
    "params_opt": [[N("params")], []],
    "params": [[N("param"), N("params_tail")]],
    "params_tail": [[T("COMMA"), N("param"), N("params_tail")], []],
    "param": [[T("IDENT"), T("COLON"), N("mut_opt"), N("type")]],
    "mut_opt": [[T("MUT")], []],
    "type": [[T("TYPE")], [T("LBRACKET"), T("ELEM"), T("RBRACKET")]],

    "block": [[T("LBRACE"), N("sep_star"), N("block_body"), N("sep_star"), T("RBRACE")]],
    "block_body": [[N("stmt"), N("stmts")], []],
    "stmts": [[N("sep_plus"), N("stmt"), N("stmts")], []],

    "stmt": [
        [T("LET"), T("IDENT"), N("annotation"), T("EQ"), N("expr")],
        [T("IDENT"), T("EQ"), N("expr")],
        [T("IDENT"), T("LBRACKET"), N("expr"), T("RBRACKET"), T("EQ"), N("expr")],
        [N("if_statement")],
        [T("WHILE"), N("expr"), N("block")],
        [N("for_statement")],
        [T("BREAK")],
        [T("CONTINUE")],
        [N("return_statement")],
        [N("expr")],
    ],
    "annotation": [[T("COLON"), N("type")], []],
    "if_statement": [[T("IF"), N("expr"), N("block"), N("else_part")]],
    "else_part": [[T("ELSE"), N("if_statement")], [T("ELSE"), N("block")], []],
    "for_statement": [[T("FOR"), T("IDENT"), T("IN"), N("expr"), N("for_range"), N("block")]],
    "for_range": [[T("RANGE"), N("expr")], []],
    "return_statement": [[T("RETURN"), N("expr")], [T("RETURN")]],

    "expr": [[N("or")]],
    "or": [[N("and"), N("or_tail")]],
    "or_tail": [[T("OROR"), N("and"), N("or_tail")], []],
    "and": [[N("equality"), N("and_tail")]],
    "and_tail": [[T("ANDAND"), N("equality"), N("and_tail")], []],
    "equality": [[N("ordering"), N("equality_tail")]],
    "equality_tail": [[N("equality_op"), N("ordering"), N("equality_tail")], []],
    "equality_op": [[T("EQEQ")], [T("NEQ")]],
    "ordering": [[N("additive"), N("ordering_tail")]],
    "ordering_tail": [[N("ordering_op"), N("additive"), N("ordering_tail")], []],
    "ordering_op": [[T("LT")], [T("LE")], [T("GT")], [T("GE")]],
    "additive": [[N("multiplicative"), N("additive_tail")]],
    "additive_tail": [[N("additive_op"), N("multiplicative"), N("additive_tail")], []],
    "additive_op": [[T("PLUS")], [T("MINUS")]],
    "multiplicative": [[N("unary"), N("multiplicative_tail")]],
    "multiplicative_tail": [[N("multiplicative_op"), N("unary"),
                             N("multiplicative_tail")], []],
    "multiplicative_op": [[T("STAR")], [T("SLASH")], [T("PERCENT")]],
    "unary": [[T("MINUS"), N("unary")], [T("BANG"), N("unary")], [N("primary")]],
    "primary": [
        [T("NUMBER")], [T("FLOAT")], [T("STRING")], [T("TRUE")], [T("FALSE")],
        [T("LPAREN"), N("expr"), T("RPAREN")],
        [N("qualified_call")], [N("call")], [N("element")], [N("array_literal")],
        [T("IDENT")],
    ],
    "qualified_call": [[T("IDENT"), T("COLONCOLON"), T("IDENT"), T("LPAREN"),
                        N("args_opt"), T("RPAREN")]],
    "call": [[T("IDENT"), T("LPAREN"), N("args_opt"), T("RPAREN")]],
    "element": [[T("IDENT"), T("LBRACKET"), N("expr"), T("RBRACKET")]],
    "array_literal": [[T("LBRACKET"), N("args_opt"), T("RBRACKET")]],
    "args_opt": [[N("args")], []],
    "args": [[N("expr"), N("args_tail")]],
    "args_tail": [[T("COMMA"), N("expr"), N("args_tail")], []],
}

#: Layout rules used only by the character backend. `pad`/`end_pad` absorb
#: whitespace at the start and end of a file; the token backend has no such
#: characters, so there they are empty rules.
LAYOUT_CHARS = """\
sp ::= [ \\t]*
comment ::= ("#" | "//") [^\\n]*
nl ::= sp comment? "\\n" sp
sp_one ::= [ \\t]+
g_item ::= sp_one | nl
g ::= g_item g | ""
one_sep ::= sp ";" sp | nl
more_sep ::= one_sep more_sep | ""
SEP ::= one_sep more_sep
pad ::= sp
end_pad ::= sp
"""


# ---------------------------------------------------------------------------
# Token back end
# ---------------------------------------------------------------------------

def token_terminal(name: str) -> dict:
    return SEPTOK if name == "SEP" else TERMINALS[name]


def token_productions() -> tuple[list[tuple[str, tuple]], str]:
    """Productions over terminals that match one lexer token."""
    #: `pad` and `end_pad` absorb whitespace in the character backend; a token
    #: stream has no whitespace, so there they match nothing at all.
    productions: list[tuple[str, tuple]] = [("pad", ()), ("end_pad", ())]
    for lhs, alternatives in CORE.items():
        for alternative in alternatives:
            symbols = tuple(("t", symbol[1]) if symbol[0] == "n" and symbol[1] == "SEP"
                            else symbol for symbol in alternative)
            productions.append((lhs, symbols))
    return productions, "program"


class TokenMatcher:
    """Matches a terminal against one token of `nava_lang.lexer`."""

    def __init__(self, tokens):
        self.tokens = tokens

    def advance(self, position: int, symbol: tuple) -> int:
        if position >= len(self.tokens):
            return 0
        token = self.tokens[position]
        spec = token_terminal(symbol[1])
        if token.kind not in spec["kinds"]:
            return 0
        if spec["values"] is not None and str(token.value) not in spec["values"]:
            return 0
        return position + 1


def lex_tokens(source: str):
    """The compiler's tokens for `source`; raises `NavaError` when it cannot lex."""
    return lex(source)


def accepts_tokens(tokens) -> bool:
    """Recognise a token stream. The end-of-file marker is not a grammar symbol."""
    tokens = [token for token in tokens if token.kind != "EOF"]
    productions, start = token_productions()
    return gbnf.earley_accepts(productions, start, len(tokens), TokenMatcher(tokens).advance)


def accepts_source(source: str, path: str | None = None) -> tuple[bool, str | None]:
    """Syntax-level acceptance of one file. `(False, reason)` when it cannot lex."""
    try:
        tokens = lex(source, path)
    except NavaError as error:
        return False, f"{error.code}: {error.message}"
    return accepts_tokens(tokens), None


# ---------------------------------------------------------------------------
# Character back end (GBNF)
# ---------------------------------------------------------------------------

def emit_gbnf() -> str:
    """The GBNF artefact. `program` is the first rule, so it is the entry rule."""
    lines = ["# Nava 0.1 grammar in GBNF, generated by eval/grammar.py.",
             "# Syntax only: types, ownership and runtime behaviour are checked by the compiler.",
             "# GBNF has no lookahead, so a keyword literal also matches the identifier",
             "# pattern: `let = 1` is accepted here and rejected by the lexer. The grammar",
             "# is a pre-filter for generation, not a replacement for the compiler.",
             ""]
    for lhs, alternatives in CORE.items():
        rendered = []
        for alternative in alternatives:
            pieces = [f"T_{symbol[1]}" if symbol[0] == "t" else symbol[1]
                      for symbol in alternative]
            rendered.append(" ".join(pieces) if pieces else '""')
        lines.append(f"{lhs} ::= " + " | ".join(rendered))
    lines.append("")
    lines.extend(LAYOUT_CHARS.rstrip("\n").splitlines())
    lines.append("")
    pad = {"LPAREN": '"(" g', "RPAREN": 'g ")" sp', "LBRACKET": '"[" g',
           "RBRACKET": 'g "]" sp', "LBRACE": '"{" sp', "RBRACE": 'sp "}" sp'}
    for name in sorted(TERMINALS):
        pattern = TERMINALS[name]["pattern"]
        body = pad.get(name, f"({pattern}) sp")
        lines.append(f"T_{name} ::= {body}")
    return "\n".join(lines) + "\n"


def compiled_gbnf() -> gbnf.Grammar:
    return gbnf.parse(emit_gbnf())


def accepts_chars(text: str) -> bool:
    return gbnf.accepts_text(compiled_gbnf(), text)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Emit the Nava grammar as GBNF, or check files against it.",
        epilog="The grammar is a syntax filter; the compiler remains the authority on "
               "types, ownership and runtime behaviour.")
    parser.add_argument("--emit", nargs="?", const="-", metavar="PATH",
                        help="write the GBNF artefact (default: stdout)")
    parser.add_argument("--check", action="append", type=Path, default=[],
                        help="report whether these files are syntactically well formed")
    parser.add_argument("--validate", action="store_true",
                        help="check the emitted GBNF for undefined, unreachable and "
                             "left-recursive rules")
    parser.add_argument("--chars", action="store_true",
                        help="recognise --check files against the emitted GBNF text "
                             "instead of the token stream")
    args = parser.parse_args(argv)

    if args.emit is not None:
        text = emit_gbnf()
        if args.emit == "-":
            print(text, end="")
        else:
            Path(args.emit).write_text(text, encoding="utf-8")
            print(f"wrote {args.emit} ({len(text.encode('utf-8'))} bytes)")
        return 0

    if args.validate:
        problems = gbnf.validate(compiled_gbnf())
        for problem in problems:
            print(f"grammar: {problem}", file=sys.stderr)
        print("OK: the emitted GBNF has no undefined, unreachable or left-recursive rules"
              if not problems else f"FAILED: {len(problems)} problem(s)", file=sys.stderr)
        return 1 if problems else 0

    if not args.check:
        parser.error("nothing to do: pass --emit, --check FILE or --validate")
    failures = 0
    for path in args.check:
        text = path.read_text(encoding="utf-8")
        if args.chars:
            accepted = accepts_chars(text)
            reason = None
        else:
            accepted, reason = accepts_source(text, str(path))
        print(json.dumps({"schema": SCHEMA, "file": str(path), "accepted": accepted,
                          "reason": reason}, ensure_ascii=False))
        failures += 0 if accepted else 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
