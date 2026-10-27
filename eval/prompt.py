#!/usr/bin/env python3
"""Build an explicitly comparable model prompt for a small evaluation task.

The prompt is assembled from three recorded parts so a run can be described
exactly: the optional system message, the language guide, and the task with its
output instruction. `eval/study.py` uses the same functions, so the prompt a
model saw is always the prompt that was counted.
"""
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

C_GUIDE = '''Language: C11.
Output a complete program with int main(void). Use standard input and standard output. Use int64_t where necessary and include the headers you need.
'''

CPP_GUIDE = '''Language: C++17.
Output a complete program with int main(). Use standard input and standard output. Use signed 64-bit integers where necessary.
'''

GUIDES = {"nava": NAVA_GUIDE, "c": C_GUIDE, "cpp": CPP_GUIDE}
LANGUAGES = tuple(GUIDES)

OUTPUT_INSTRUCTION = ("Print only the requested result, with a newline. "
                      "Output source code only, without Markdown or explanation.")


def load_tasks(path: Path | None = None) -> dict[str, dict]:
    tasks = json.loads((path or HERE / "tasks.json").read_text(encoding="utf-8"))
    return {task["id"]: task for task in tasks}


def build_parts(task_id: str, language: str, tasks: dict[str, dict] | None = None,
                *, include_guide: bool = True) -> dict[str, str]:
    """The three parts of a prompt, kept separate so each can be counted."""
    tasks = tasks or load_tasks()
    if language not in GUIDES:
        raise ValueError(f"unknown language {language!r}; use one of {', '.join(LANGUAGES)}")
    if task_id not in tasks:
        raise ValueError(f"unknown task {task_id!r}")
    guide = GUIDES[language].strip() if include_guide else ""
    body = "Task: " + tasks[task_id]["description"]
    return {"guide": guide, "task": body, "instruction": OUTPUT_INSTRUCTION}


def build_prompt(task_id: str, language: str, tasks: dict[str, dict] | None = None, *,
                 include_guide: bool = True) -> str:
    parts = build_parts(task_id, language, tasks, include_guide=include_guide)
    pieces = [piece for piece in (parts["guide"], parts["task"], parts["instruction"]) if piece]
    return "\n\n".join(pieces) + "\n"


def main() -> int:
    tasks = load_tasks()
    parser = argparse.ArgumentParser(description="Build the exact prompt sent to a model.")
    parser.add_argument("--task", required=True, choices=sorted(tasks))
    parser.add_argument("--language", required=True, choices=LANGUAGES)
    parser.add_argument("--no-guide", action="store_true",
                        help="omit the language guide (records the guide overhead as zero)")
    parser.add_argument("--json", action="store_true", help="print the prompt parts as JSON")
    args = parser.parse_args()
    if args.json:
        parts = build_parts(args.task, args.language, tasks, include_guide=not args.no_guide)
        parts["prompt"] = build_prompt(args.task, args.language, tasks, include_guide=not args.no_guide)
        print(json.dumps(parts, ensure_ascii=False, indent=2))
        return 0
    print(build_prompt(args.task, args.language, tasks, include_guide=not args.no_guide), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
