#!/usr/bin/env python3
"""Make an explicitly comparable model prompt for a small evaluation task."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAVA_GUIDE = '''Language: Nava 0.1.
Output a complete Nava program with fn main() { ... }. No imports or global variables.
One statement per line; semicolons are optional. Comments start with # or //.
read_i64() reads one signed integer from standard input; print(value) prints one value and a newline.
Types: i64, bool, str. Integer arithmetic is checked. let x = expression declares a mutable local, with inferred type.
Functions: fn f(a: i64, b: i64) -> i64 { return a + b }. A function with no return type returns void.
Control flow: if condition { ... } else { ... } and while condition { ... }; conditions must be bool.
Operators: + - * / %; < <= > >= == !=; ! && ||. Division truncates toward zero.
Calls and operators evaluate left to right; && and || short-circuit. Variables are block-scoped.
Only these features are available: no arrays, for loops, string concatenation, external libraries, or casts.
'''
CPP_GUIDE = '''Language: C++17.
Output a complete program with int main(). Use standard input and standard output. Use signed 64-bit integers where necessary.
'''


def main():
    tasks = {task["id"]: task for task in json.loads((HERE / "tasks.json").read_text(encoding="utf-8"))}
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, choices=sorted(tasks))
    parser.add_argument("--language", required=True, choices=("nava", "cpp"))
    args = parser.parse_args()
    guide = NAVA_GUIDE if args.language == "nava" else CPP_GUIDE
    print(guide.strip())
    print("\nTask: " + tasks[args.task]["description"])
    print("Print only the requested result, with a newline. Output source code only, without Markdown or explanation.")


if __name__ == "__main__":
    main()
