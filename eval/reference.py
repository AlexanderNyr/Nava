#!/usr/bin/env python3
"""Independent reference answers for the public evaluation tasks.

Every expected `stdout` in `eval/tasks.json` must be reproducible by a function
in this module.  The functions are written directly from each task description
and share no code with the Nava compiler or with the candidate solutions, so a
hand-edited or accidentally corrupted fixture is visible.

Usage:
    python3 eval/reference.py --check          # verify every fixture
    python3 eval/reference.py --task gcd --stdin "48 18\n"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent


def _ints(stdin: str) -> list[int]:
    return [int(token) for token in stdin.split()]


def _ints_allow_empty(stdin: str) -> list[int]:
    """For tasks whose input may legitimately be empty after the count."""
    tokens = stdin.split()
    return [int(token) for token in tokens]


def gcd(stdin: str) -> str:
    a, b = _ints(stdin)
    while b != 0:
        a, b = b, a % b
    return f"{a}\n"


def sum_to_n(stdin: str) -> str:
    (n,) = _ints(stdin)
    return f"{(n * (n + 1)) // 2}\n"


def factorial(stdin: str) -> str:
    (n,) = _ints(stdin)
    result = 1
    for value in range(2, n + 1):
        result *= value
    return f"{result}\n"


def fibonacci(stdin: str) -> str:
    (n,) = _ints(stdin)
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return f"{a}\n"


def is_prime(stdin: str) -> str:
    (n,) = _ints(stdin)
    if n < 2:
        return "false\n"
    if n % 2 == 0:
        return "true\n" if n == 2 else "false\n"
    divisor = 3
    while divisor * divisor <= n:
        if n % divisor == 0:
            return "false\n"
        divisor += 2
    return "true\n"


def digit_sum(stdin: str) -> str:
    (n,) = _ints(stdin)
    total = 0
    while n > 0:
        total += n % 10
        n //= 10
    return f"{total}\n"


def max_of_three(stdin: str) -> str:
    a, b, c = _ints(stdin)
    return f"{max(a, b, c)}\n"


def collatz_steps(stdin: str) -> str:
    (n,) = _ints(stdin)
    steps = 0
    while n != 1:
        n = n // 2 if n % 2 == 0 else 3 * n + 1
        steps += 1
    return f"{steps}\n"


def sort_numbers(stdin: str) -> str:
    values = _ints_allow_empty(stdin)
    count, numbers = values[0], values[1:]
    if len(numbers) != count:
        raise ValueError(f"expected {count} numbers, got {len(numbers)}")
    return "".join(f"{value}\n" for value in sorted(numbers))


def sieve_count(stdin: str) -> str:
    (limit,) = _ints(stdin)
    if limit < 0:
        raise ValueError("the limit is negative")
    flags = [True] * (limit + 1)
    count = 0
    for value in range(2, limit + 1):
        if flags[value]:
            count += 1
            for multiple in range(value * value, limit + 1, value):
                flags[multiple] = False
    return f"{count}\n"


def mean_abs(stdin: str) -> str:
    tokens = stdin.split()
    count = int(tokens[0])
    values = [float(token) for token in tokens[1:]]
    if len(values) != count:
        raise ValueError(f"expected {count} floats, got {len(values)}")
    if count == 0:
        return "0.0\n"
    total = 0.0
    for value in values:
        total += abs(value)
    # The printed form is the documented `print(f64)` rule, which is exactly
    # Python's repr for a float; see LANGUAGE.md.
    return f"{total / float(count)!r}\n"


REFERENCE: dict[str, Callable[[str], str]] = {
    "gcd": gcd,
    "sum_to_n": sum_to_n,
    "factorial": factorial,
    "fibonacci": fibonacci,
    "is_prime": is_prime,
    "digit_sum": digit_sum,
    "max_of_three": max_of_three,
    "collatz_steps": collatz_steps,
    "sort_numbers": sort_numbers,
    "sieve_count": sieve_count,
    "mean_abs": mean_abs,
}


def load_tasks(path: Path | None = None) -> list[dict]:
    return json.loads((path or HERE / "tasks.json").read_text(encoding="utf-8"))


def check(path: Path | None = None) -> list[str]:
    """Return human-readable problems; an empty list means all fixtures agree."""
    problems: list[str] = []
    tasks = load_tasks(path)
    seen: set[str] = set()
    for task in tasks:
        task_id = task["id"]
        if task_id in seen:
            problems.append(f"{task_id}: duplicate task id")
        seen.add(task_id)
        reference = REFERENCE.get(task_id)
        if reference is None:
            problems.append(f"{task_id}: no independent reference implementation")
            continue
        if not task.get("tests"):
            problems.append(f"{task_id}: task has no test cases")
            continue
        for index, case in enumerate(task["tests"], 1):
            try:
                actual = reference(case["stdin"])
            except (TypeError, ValueError) as exc:
                problems.append(f"{task_id} case {index}: reference rejected the input: {exc}")
                continue
            if actual != case["stdout"]:
                problems.append(
                    f"{task_id} case {index}: expected {case['stdout']!r}, reference gives {actual!r}"
                )
    for missing in sorted(set(REFERENCE) - seen):
        problems.append(f"{missing}: reference implementation without a task in tasks.json")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Independent reference answers for eval/tasks.json")
    parser.add_argument("--check", action="store_true", help="verify every fixture against the reference")
    parser.add_argument("--task", choices=sorted(REFERENCE), help="compute one answer")
    parser.add_argument("--stdin", default="", help="standard input for --task")
    args = parser.parse_args()
    if args.task:
        sys.stdout.write(REFERENCE[args.task](args.stdin))
        return 0
    problems = check()
    for problem in problems:
        print(f"reference: {problem}", file=sys.stderr)
    if problems:
        return 1
    tasks = load_tasks()
    cases = sum(len(task["tests"]) for task in tasks)
    print(f"OK: {len(tasks)} tasks and {cases} cases agree with the independent reference")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
