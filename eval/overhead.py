#!/usr/bin/env python3
"""How much does the guidance cost? (PLAN.md phase N7, third task.)

An A/B run that compares Nava with C or C++ has to charge Nava for its guide.
This tool renders the *whole* prompt for every task in every language through
`eval/prompt.py` — the same function the study and A/B harnesses call — and
records, per part, what each language costs:

* `guide` — the language guide alone;
* `task` — the task description, which is identical in all three languages;
* `instruction` + `total` — the complete prompt that is sent.

Two kinds of number appear here, and the difference matters:

* **bytes, characters and lines** are exact and recomputable offline by anyone
  with the repository; `tests/test_guides.py` recomputes them.
* **tokens** exist only when a real tokenizer was constructed
  (`eval/tokens.py`). They are `null` otherwise, and no estimate is
  substituted.

A token count under a general-purpose encoding such as `cl100k_base` is a
measurement of *that encoding on this text*. It is **not** a measurement of the
study model's prompt: a different tokenizer gives a different number, and the
number is not evidence that anything is faster or more accurate. Every record
says so in `tokenizer` and `note`, and `--require measured` is the only way to
demand a token count.
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

from eval import constrained  # noqa: E402
from eval import grammar as grammar_module  # noqa: E402
from eval import prompt as prompt_module  # noqa: E402
from eval import tokens as tokens_module  # noqa: E402

SCHEMA = "nava-overhead/1"
MEASUREMENTS = HERE / "measurements"

NOTE = ("Bytes, characters and lines are exact. Tokens exist only for the tokenizer named in "
        "this record; they measure that encoding, not a model's prompt length, and they say "
        "nothing about speed or accuracy. Pass-rate claims need a measured A/B run "
        "(eval/ab.py).")


def sized(text: str, tokenizer: tokens_module.Tokenizer) -> dict:
    """Exact sizes always; a digest so a recorded part can be identified; tokens or null."""
    return {**tokens_module.guide_module.size_of(text),
            "sha256": tokens_module.guide_module.sha256(text),
            "tokens": tokenizer.count(text)}


def delta(left: dict, right: dict) -> dict:
    """`left - right` for every counter, `null` when either side is missing."""
    out = {}
    for key in ("bytes", "chars", "lines", "tokens"):
        if key not in left:
            continue
        a, b = left.get(key), right.get(key)
        out[key] = None if a is None or b is None else a - b
    return out


def measure(task_ids: list[str] | None = None,
            tokenizer: tokens_module.Tokenizer | None = None) -> dict:
    tokenizer = tokenizer or tokens_module.make_tokenizer()
    tasks = prompt_module.load_tasks()
    if task_ids:
        unknown = set(task_ids) - set(tasks)
        if unknown:
            raise ValueError(f"unknown task ids: {', '.join(sorted(unknown))}")
        tasks = {task_id: tasks[task_id] for task_id in task_ids}

    pairs = []
    per_language: dict[str, dict] = {
        language: {"guide": {"bytes": 0, "chars": 0, "lines": 0, "tokens": 0},
                   "prompt": {"bytes": 0, "chars": 0, "lines": 0, "tokens": 0}}
        for language in prompt_module.LANGUAGES}
    unavailable = False
    for task_id in sorted(tasks):
        entry = {"task": task_id, "languages": {}}
        for language in prompt_module.LANGUAGES:
            parts = prompt_module.build_parts(task_id, language, tasks)
            prompt = prompt_module.build_prompt(task_id, language, tasks)
            entry["languages"][language] = {
                "guide": sized(parts["guide"], tokenizer),
                "task": sized(parts["task"], tokenizer),
                "instruction": sized(parts["instruction"], tokenizer),
                "prompt": sized(prompt, tokenizer),
            }
            totals = per_language[language]
            if any(entry["languages"][language][key]["tokens"] is None
                   for key in ("guide", "prompt")):
                unavailable = True
            for key, source in (("guide", "guide"), ("prompt", "prompt")):
                for counter in ("bytes", "chars", "lines"):
                    totals[key][counter] += entry["languages"][language][source][counter]
                if not unavailable and entry["languages"][language][source]["tokens"] is not None:
                    totals[key]["tokens"] += entry["languages"][language][source]["tokens"]
        nava = entry["languages"]["nava"]
        entry["guidance_overhead"] = {}
        for other in ("c", "cpp"):
            entry["guidance_overhead"][other] = {
                "guide": delta(nava["guide"], entry["languages"][other]["guide"]),
                "prompt": delta(nava["prompt"], entry["languages"][other]["prompt"]),
            }
        pairs.append(entry)

    for language, totals in per_language.items():
        if unavailable:
            totals["guide"]["tokens"] = None
            totals["prompt"]["tokens"] = None

    return {
        "schema": SCHEMA,
        "status": "measured" if tokenizer.available else "not-measured",
        "reason": tokenizer.reason,
        "tokenizer": tokenizer.describe(),
        "guide": prompt_module.GUIDE_INFO,
        "guide_digests": prompt_module.GUIDE_DIGESTS,
        "grammar": constrained.grammar_metadata(grammar_module.emit_gbnf()),
        "tasks": [case["task"] for case in pairs],
        "languages": per_language,
        "pairs": pairs,
        "note": NOTE,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure the guidance overhead of a prompt: bytes, characters, tokens.",
        epilog="Tokens need a real tokenizer (--encoding or --hf-tokenizer); without one the "
               "record says not-measured and no estimate is substituted.")
    parser.add_argument("--task", action="append", dest="task_ids",
                        help="restrict to these task ids (repeatable)")
    parser.add_argument("--encoding", help="tiktoken encoding name, e.g. cl100k_base")
    parser.add_argument("--hf-tokenizer", help="Hugging Face tokenizer path or model id")
    parser.add_argument("--require", choices=("measured", "not-measured"),
                        help="exit 1 unless the record has this status")
    parser.add_argument("--out", type=Path, help="write the record here instead of stdout")
    args = parser.parse_args(argv)

    try:
        tokenizer = tokens_module.make_tokenizer(args.encoding, args.hf_tokenizer)
        record = measure(args.task_ids, tokenizer)
    except ValueError as error:
        parser.error(str(error))

    rendered = json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(rendered, end="")

    totals = record["languages"]
    print(f"nava prompt: {totals['nava']['prompt']['chars']} chars, "
          f"{totals['nava']['prompt']['tokens']} tokens; "
          f"c prompt: {totals['c']['prompt']['chars']} chars, "
          f"{totals['c']['prompt']['tokens']} tokens "
          f"(summed over {len(record['tasks'])} task(s))", file=sys.stderr)
    print(f"status: {record['status']}"
          + (f" ({record['reason']})" if record["reason"] else ""), file=sys.stderr)
    if args.require and record["status"] != args.require:
        print(f"overhead: status is {record['status']!r}, --require {args.require} was requested",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
