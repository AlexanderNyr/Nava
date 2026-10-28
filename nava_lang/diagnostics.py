"""Stable diagnostic codes, severities and rendering.

Every message the front end can produce is registered here with a code, a
severity and a one-line title.  Codes are part of the interface: a test, an
editor integration or a model repair loop may match on them, so an existing
code must not change meaning and a new condition must get a new code.

Numbering:

======  ====================================================================
Range   Stage
======  ====================================================================
E01xx   Lexer and source decoding
E02xx   Parser
E03xx   Type checking and semantic analysis
E04xx   Resource limits (nesting, compiler recursion)
E05xx   Command line and host environment
E06xx   Sandbox and isolation
W00xx   Warnings; never fatal
======  ====================================================================

`tests/test_spec.py` checks that every code raised by the front end is
registered here and that every registered code has a test fixture, so the
table cannot drift away from the implementation.
"""
from __future__ import annotations

import json
from typing import Iterable, Optional

from .model import Diagnostic, Location, NavaError, Severity

#: code -> (severity, title).  The message text may vary; the code may not.
CODES: dict[str, tuple[str, str]] = {
    # --- lexer and decoding -------------------------------------------------
    "E0101": (Severity.ERROR, "unexpected character"),
    "E0102": (Severity.ERROR, "unterminated string"),
    "E0103": (Severity.ERROR, "literal newline in string"),
    "E0104": (Severity.ERROR, "invalid string escape"),
    "E0105": (Severity.ERROR, "NUL in string"),
    "E0106": (Severity.ERROR, "invalid Unicode surrogate in string"),
    "E0107": (Severity.ERROR, "invalid underscore in integer literal"),
    "E0108": (Severity.ERROR, "integer literal outside the i64 range"),
    "E0109": (Severity.ERROR, "unmatched ')'"),
    "E0110": (Severity.ERROR, "unclosed '('"),
    "E0111": (Severity.ERROR, "source is not valid UTF-8"),
    "E0112": (Severity.ERROR, "unmatched ']'"),
    "E0113": (Severity.ERROR, "unclosed '['"),
    # --- parser -------------------------------------------------------------
    "E0201": (Severity.ERROR, "unexpected token"),
    "E0202": (Severity.ERROR, "missing statement separator"),
    "E0206": (Severity.ERROR, "an array element type is not allowed"),
    "E0203": (Severity.ERROR, "unknown type"),
    "E0204": (Severity.ERROR, "unclosed block"),
    "E0205": (Severity.ERROR, "expected an expression"),
    # --- type checking and semantics ---------------------------------------
    "E0301": (Severity.ERROR, "reserved builtin name"),
    "E0302": (Severity.ERROR, "duplicate function"),
    "E0303": (Severity.ERROR, "missing fn main"),
    "E0304": (Severity.ERROR, "invalid main signature"),
    "E0305": (Severity.ERROR, "void parameter type"),
    "E0306": (Severity.ERROR, "missing return on some path"),
    "E0307": (Severity.ERROR, "duplicate variable in the same scope"),
    "E0308": (Severity.ERROR, "unknown variable"),
    "E0309": (Severity.ERROR, "void value in a variable"),
    "E0310": (Severity.ERROR, "void variable type"),
    "E0311": (Severity.ERROR, "annotation does not match the initializer"),
    "E0312": (Severity.ERROR, "assignment type mismatch"),
    "E0313": (Severity.ERROR, "expression statement must be a call"),
    "E0314": (Severity.ERROR, "void call used as a value"),
    "E0315": (Severity.ERROR, "return type mismatch"),
    "E0316": (Severity.ERROR, "if condition must be bool"),
    "E0317": (Severity.ERROR, "while condition must be bool"),
    "E0318": (Severity.ERROR, "unary operator type mismatch"),
    "E0319": (Severity.ERROR, "operator requires two i64 values"),
    "E0320": (Severity.ERROR, "equality requires two values of the same type"),
    "E0321": (Severity.ERROR, "logical operator requires two bool"),
    "E0322": (Severity.ERROR, "wrong number of arguments to print"),
    "E0323": (Severity.ERROR, "unsupported print argument type"),
    "E0324": (Severity.ERROR, "wrong number of arguments to read_i64"),
    "E0325": (Severity.ERROR, "unknown function"),
    "E0326": (Severity.ERROR, "wrong number of arguments"),
    "E0327": (Severity.ERROR, "argument type mismatch"),
    "E0328": (Severity.ERROR, "array index must be i64"),
    "E0329": (Severity.ERROR, "only an array can be indexed"),
    "E0330": (Severity.ERROR, "array returned or passed by value"),
    "E0331": (Severity.ERROR, "arrays cannot be copied"),
    "E0332": (Severity.ERROR, "array variable cannot be reassigned"),
    "E0333": (Severity.ERROR, "mutable array argument must be an owned variable"),
    "E0334": (Severity.ERROR, "array literal element type mismatch"),
    "E0335": (Severity.ERROR, "empty array literal needs a type annotation"),
    "E0336": (Severity.ERROR, "element assignment through a read-only borrow"),
    "E0337": (Severity.ERROR, "wrong number of arguments to len"),
    "E0338": (Severity.ERROR, "len expects an array"),
    "E0339": (Severity.ERROR, "wrong number of arguments to array_zeros"),
    "E0340": (Severity.ERROR, "array_zeros expects an i64 length"),
    "E0341": (Severity.ERROR, "mut on a non-array parameter"),
    "E0342": (Severity.ERROR, "the same array borrowed mutably twice"),
    "E0343": (Severity.ERROR, "'break' outside a loop"),
    "E0344": (Severity.ERROR, "'continue' outside a loop"),
    "E0345": (Severity.ERROR, "implicit conversion between i64 and f64"),
    "E0346": (Severity.ERROR, "invalid floating-point literal"),
    "E0347": (Severity.ERROR, "invalid numeric conversion argument"),
    "E0348": (Severity.ERROR, "wrong number of arguments to read_f64"),
    "E0349": (Severity.ERROR, "for-loop bound must be i64"),
    "E0350": (Severity.ERROR, "for loop cannot iterate over this value"),
    "E0351": (Severity.ERROR, "the loop variable cannot be assigned"),
    "E0352": (Severity.ERROR, "module not found"),
    "E0353": (Severity.ERROR, "modules import each other in a cycle"),
    "E0354": (Severity.ERROR, "import after a function definition"),
    "E0355": (Severity.ERROR, "module imported twice"),
    "E0356": (Severity.ERROR, "module escapes the project boundary"),
    "E0357": (Severity.ERROR, "module defines fn main"),
    "E0358": (Severity.ERROR, "function belongs to another module"),
    "E0359": (Severity.ERROR, "module is not imported"),
    "E0360": (Severity.ERROR, "a local module shadows a standard module"),
    "E0361": (Severity.ERROR, "standard module version mismatch"),

    "E0370": (Severity.ERROR, "edition declaration is not first"),
    "E0371": (Severity.ERROR, "unknown edition"),
    "E0372": (Severity.ERROR, "edition 2 construct in an edition 1 file"),
    "E0373": (Severity.ERROR, "unknown struct"),
    "E0374": (Severity.ERROR, "unknown field"),
    "E0375": (Severity.ERROR, "duplicate field"),
    "E0376": (Severity.ERROR, "missing field in a struct construction"),
    "E0377": (Severity.ERROR, "a struct contains itself"),
    "E0378": (Severity.ERROR, "unsupported struct field type"),
    "E0379": (Severity.ERROR, "member access on a non-struct value"),
    "E0380": (Severity.ERROR, "duplicate struct"),
    "E0381": (Severity.ERROR, "named fields used where they are not allowed"),

    "E0390": (Severity.ERROR, "duplicate enum"),
    "E0391": (Severity.ERROR, "duplicate enum variant"),
    "E0392": (Severity.ERROR, "a variant pattern needs the enum name"),
    "E0393": (Severity.ERROR, "unknown enum variant"),
    "E0394": (Severity.ERROR, "wrong number of variant fields"),
    "E0395": (Severity.ERROR, "match expects an enum value"),
    "E0396": (Severity.ERROR, "match is not exhaustive"),
    "E0397": (Severity.ERROR, "duplicate match arm"),
    "E0398": (Severity.ERROR, "unreachable match arm"),
    "E0399": (Severity.ERROR, "unsupported enum variant field type"),

    "E0410": (Severity.ERROR, "`?` expects an Option or a Result"),
    "E0411": (Severity.ERROR, "`?` cannot propagate into this function's return type"),
    "E0412": (Severity.ERROR, "wrong number of type arguments"),
    "E0413": (Severity.ERROR, "`?` is only allowed in a `let` initializer"),
    "E0414": (Severity.ERROR, "a user type cannot take type arguments yet"),
    "E0415": (Severity.ERROR, "a type argument is not a type"),
    # --- resource limits ----------------------------------------------------
    "E0401": (Severity.ERROR, "nesting too deep"),
    "E0402": (Severity.ERROR, "compiler recursion limit reached"),
    # --- warnings -----------------------------------------------------------
    "W0001": (Severity.WARNING, "unreachable statement"),
    "W0002": (Severity.WARNING, "unused import"),
    # --- command-line and host ---------------------------------------------
    "E0501": (Severity.ERROR, "invalid C compiler command"),
    "E0502": (Severity.ERROR, "empty C compiler command"),
    "E0503": (Severity.ERROR, "C compiler not found"),
    "E0504": (Severity.ERROR, "output path equals the source path"),
    "E0505": (Severity.ERROR, "output directory does not exist"),
    "E0506": (Severity.ERROR, "directory does not exist"),
    "E0507": (Severity.ERROR, "path is not a directory"),
    # --- isolation ----------------------------------------------------------
    "E0601": (Severity.ERROR, "requested isolation is unavailable"),
    "E0602": (Severity.ERROR, "sandboxed program was stopped"),
}


def is_registered(code: str) -> bool:
    return code in CODES


def severity_of(code: str) -> str:
    return CODES[code][0] if code in CODES else Severity.ERROR


def title_of(code: str) -> str:
    return CODES[code][1] if code in CODES else "unregistered diagnostic"


def make(code: str, message: str, loc: Optional[Location] = None,
         end: Optional[Location] = None, hint: Optional[str] = None) -> NavaError:
    """Build a `NavaError` carrying a registered code."""
    if code not in CODES:
        raise AssertionError(f"unregistered diagnostic code {code!r}")
    return NavaError(message, loc, code=code, end=end, hint=hint)


def format_human(path: str, diagnostic: Diagnostic) -> str:
    location = ""
    if diagnostic.loc is not None:
        if diagnostic.loc.path:
            path = diagnostic.loc.path
        location = f":{diagnostic.loc.line}:{diagnostic.loc.column}"
    text = f"{path}{location}: {diagnostic.severity}[{diagnostic.code}]: {diagnostic.message}"
    if diagnostic.hint:
        text += f"\n  hint: {diagnostic.hint}"
    return text


def diagnostic_json(path: str, diagnostic: Diagnostic) -> dict:
    if diagnostic.loc is not None and diagnostic.loc.path:
        path = diagnostic.loc.path
    document: dict = {
        "code": diagnostic.code,
        "severity": diagnostic.severity,
        "title": title_of(diagnostic.code),
        "message": diagnostic.message,
        "path": path,
    }
    if diagnostic.loc is None:
        document["range"] = None
    else:
        start = diagnostic.loc
        end = diagnostic.end or start
        document["range"] = {
            "start": {"line": start.line, "column": start.column},
            "end": {"line": end.line, "column": end.column},
        }
    if diagnostic.hint:
        document["hint"] = diagnostic.hint
    return document


def report_json(path: str, status: str, diagnostics: Iterable[Diagnostic],
                extra: Optional[dict] = None) -> str:
    """Machine-readable report: one JSON document on stdout."""
    document: dict = {
        "status": status,
        "path": path,
        "diagnostics": [diagnostic_json(path, diagnostic) for diagnostic in diagnostics],
    }
    if extra:
        document.update(extra)
    return json.dumps(document, ensure_ascii=False, indent=2)
