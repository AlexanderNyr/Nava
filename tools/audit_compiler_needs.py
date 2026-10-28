#!/usr/bin/env python3
"""What Nava's own compiler asks of a language (INDEPENDENCE.md §2).

The audit table in `INDEPENDENCE.md` is evidence, not a wish list, so its numbers
have to be reproducible by anyone with the repository. This script is the method:
it parses every module under `nava_lang/` and counts the Python constructs the
table lists. The definitions are deliberately literal — a row counts *syntax
nodes*, not opinions about style:

* a **dict literal** is an `ast.Dict`; a **set literal** is an `ast.Set`;
* a **method** is counted by the attribute name of a call, so `.get` counts
  `mapping.get(...)` and nothing else — reading `d[k]` is not counted;
* a **comprehension** is a `GeneratorExp`, `ListComp` or `SetComp`;
* a **slice** is an `ast.Slice` node (`a[i:j]`), not a string method;
* `f-strings` are `ast.JoinedStr` nodes plus `.format(...)` calls;
* a **sorted call** is a call whose function is the name `sorted`, and the
  `key=` column counts how many of those pass a `key`.

Usage::

    python3 tools/audit_compiler_needs.py            # the table, as markdown
    python3 tools/audit_compiler_needs.py --json     # the same counts as JSON
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DICT_METHODS = ("get", "items", "values", "setdefault", "keys")
SEQUENCE_METHODS = ("append", "extend", "pop", "sort", "insert", "remove")
SET_METHODS = ("add", "discard", "update")
STRING_METHODS = ("split", "join", "strip", "startswith", "endswith", "replace",
                  "encode", "decode")
#: Constructs Nava is *not* asked to acquire: the count is a claim too, and the
#: audit states it rather than leaving the row out.
NOT_NEEDED = ("Yield", "YieldFrom", "Await", "AsyncFunctionDef")


def count() -> dict[str, int]:
    totals = {
        "lines": 0,
        "modules": 0,
        "functions": 0,
        "classes": 0,
        "raise": 0,
        "try": 0,
        "dict_literal": 0,
        "dict_methods": 0,
        "sequence_methods": 0,
        "set_literal": 0,
        "set_methods": 0,
        "f_strings": 0,
        "string_methods": 0,
        "slices": 0,
        "sorted_calls": 0,
        "sorted_with_key": 0,
        "generator_exp": 0,
        "list_comp": 0,
        "set_comp": 0,
        "lambda": 0,
    }
    for name in NOT_NEEDED:
        totals[f"not_needed_{name}"] = 0
    for path in sorted((ROOT / "nava_lang").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        totals["modules"] += 1
        totals["lines"] += len(source.splitlines())
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                totals["functions"] += 1
            elif isinstance(node, ast.ClassDef):
                totals["classes"] += 1
            elif isinstance(node, ast.Raise):
                totals["raise"] += 1
            elif isinstance(node, ast.Try):
                totals["try"] += 1
            elif isinstance(node, ast.Dict):
                totals["dict_literal"] += 1
            elif isinstance(node, ast.Set):
                totals["set_literal"] += 1
            elif isinstance(node, ast.JoinedStr):
                totals["f_strings"] += 1
            elif isinstance(node, ast.Slice):
                totals["slices"] += 1
            elif isinstance(node, ast.GeneratorExp):
                totals["generator_exp"] += 1
            elif isinstance(node, ast.ListComp):
                totals["list_comp"] += 1
            elif isinstance(node, ast.SetComp):
                totals["set_comp"] += 1
            elif isinstance(node, ast.Lambda):
                totals["lambda"] += 1
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id == "sorted":
                totals["sorted_calls"] += 1
                if any(keyword.arg == "key" for keyword in node.keywords):
                    totals["sorted_with_key"] += 1
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                method = node.func.attr
                if method in DICT_METHODS:
                    totals["dict_methods"] += 1
                elif method in SEQUENCE_METHODS:
                    totals["sequence_methods"] += 1
                elif method in SET_METHODS:
                    totals["set_methods"] += 1
                elif method in STRING_METHODS:
                    totals["string_methods"] += 1
                elif method == "format":
                    totals["f_strings"] += 1
            for name in NOT_NEEDED:
                if type(node).__name__ == name:
                    totals[f"not_needed_{name}"] += 1
    return totals


ROWS = (
    ("`class` (AST nodes, diagnostics, module records, type descriptors)", "classes"),
    ("`raise` / `try`", ("raise", "try")),
    ("dict literal, `.get`, `.items`, `.values`, `.setdefault`, `.keys`",
     ("dict_literal", "dict_methods")),
    ("`.append`, `.extend`, `.pop`, `.sort`, `.insert`, `.remove`", "sequence_methods"),
    ("set literal, `.add`, `.discard`, `.update`", ("set_literal", "set_methods")),
    ("f-strings and `str.format`", "f_strings"),
    ("`split`, `join`, `strip`, `startswith`, `endswith`, `replace`, `encode`, `decode`",
     "string_methods"),
    ("slice syntax `a[i:j]`", "slices"),
    ("`sorted(...)` (and how many pass `key=`)", ("sorted_calls", "sorted_with_key")),
    ("generator expressions, list/set comprehensions",
     ("generator_exp", "list_comp", "set_comp")),
    ("`lambda`", "lambda"),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print the counts as JSON")
    args = parser.parse_args(argv)
    totals = count()
    if args.json:
        print(json.dumps(totals, indent=2, sort_keys=True))
        return 0
    print(f"{totals['lines']} lines, {totals['modules']} modules, "
          f"{totals['functions']} functions under nava_lang/")
    print()
    print("| Python construct in the compiler | Count |")
    print("|---|---|")
    for label, key in ROWS:
        if isinstance(key, tuple):
            value = " / ".join(str(totals[item]) for item in key)
        else:
            value = str(totals[key])
        print(f"| {label} | {value} |")
    print()
    not_needed = ", ".join(f"{name} {totals[f'not_needed_{name}']}" for name in NOT_NEEDED)
    print(f"| generators, `await`, async functions | {not_needed} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
