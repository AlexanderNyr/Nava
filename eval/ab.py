#!/usr/bin/env python3
"""A/B run: free generation versus grammar-constrained generation (N7).

Two arms, one task set, one model, one set of settings:

``free``
    the prompt from `eval/prompt.py` and nothing else;
``constrained``
    the same prompt plus the request fields from `eval/constrained.py` that ask
    the server to constrain decoding to the Nava grammar.

What the record holds for each arm: per-task aggregates (reusing
`eval/study.py`), the guide overhead (bytes, characters and — only when a real
tokenizer is installed — tokens), the grammar digest and, per round, whether the
candidate was syntactically acceptable at all. That last field is what makes the
comparison meaningful: a constrained run is expected to reduce *parse* failures
and to leave type errors and wrong answers alone.

The harness never claims an improvement by itself. `comparison.claim` is `null`
and `comparison.claim_status` is `"open"`; with no model to call, the record says
`not-measured` and `eval/check_ab_record.py` refuses to accept a claim attached
to such a record.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import random
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import constrained, feedback as feedback_module  # noqa: E402
from eval import grammar, guide as guide_module  # noqa: E402
from eval import prompt as prompt_module  # noqa: E402
from eval import study  # noqa: E402
from eval import tokens as tokens_module  # noqa: E402
from eval.providers import build_provider  # noqa: E402
from nava_lang import __version__ as nava_version  # noqa: E402
from nava_lang import sandbox  # noqa: E402

SCHEMA = "nava-ab/1"
ARMS = ("free", "constrained")
LANGUAGE = "nava"


def paired_bootstrap(a: list[bool], b: list[bool], resamples: int = study.BOOTSTRAP_RESAMPLES,
                     seed: int = study.BOOTSTRAP_SEED) -> dict:
    """Difference in means with a percentile bootstrap over paired attempts."""
    if len(a) != len(b) or not a:
        return {"point": None, "low": None, "high": None, "resamples": 0, "pairs": len(a)}
    point = sum(b) / len(b) - sum(a) / len(a)
    generator = random.Random(seed)
    samples = []
    for _ in range(resamples):
        picks = [generator.randrange(len(a)) for _ in range(len(a))]
        samples.append(sum(b[index] for index in picks) / len(picks)
                       - sum(a[index] for index in picks) / len(picks))
    samples.sort()
    low = samples[int(0.025 * (len(samples) - 1))]
    high = samples[int(0.975 * (len(samples) - 1))]
    return {"point": round(point, 6), "low": round(low, 6), "high": round(high, 6),
            "resamples": resamples, "pairs": len(a)}


def syntax_accept_rate(attempts: list[dict]) -> dict:
    flags = [round_record.get("syntax_accepted")
             for attempt in attempts for round_record in attempt.get("rounds", [])]
    known = [flag for flag in flags if flag is not None]
    return {"accepted": sum(1 for flag in known if flag), "of_rounds": len(known),
            "rate": round(sum(1 for flag in known if flag) / len(known), 6) if known else None}


def run_arm(arm: str, *, tasks: list[dict], task_index: dict, args, grammar_text: str,
            workdir: Path) -> dict:
    """Run one arm; the only difference between arms is the request payload."""
    extra = (constrained.extra_fields(args.interface, grammar_text)
             if arm == "constrained" else None)
    provider = build_provider(args.provider, model=args.model, base_url=args.base_url,
                              api_key_env=args.api_key_env, replay_path=args.replay_file,
                              script=study._mock_script(args, task_index, [LANGUAGE]))
    attempts: list[dict] = []
    for attempt_index in range(args.attempts):
        for task in tasks:
            template = {
                "prompt": prompt_module.build_prompt(task["id"], LANGUAGE, task_index,
                                                     include_guide=not args.no_guide),
                "model": args.model,
                "temperature": args.temperature,
                "max_tokens": args.max_tokens,
                "stream": False,
            }
            attempt = study.attempt_once(
                task_id=task["id"], language=LANGUAGE, attempt_index=attempt_index,
                provider=provider,
                request_template=template,
                parts=prompt_module.build_parts(task["id"], LANGUAGE, task_index,
                                                include_guide=not args.no_guide),
                system=None, repair_rounds=args.repair_rounds, isolation=args.isolation,
                timeout=args.timeout, memory=args.memory, store_raw=args.store_raw,
                workdir=workdir, feedback_mode=args.feedback,
                syntax_check=lambda code: grammar.accepts_source(code)[0],
                request_extra=extra,
            )
            attempts.append(attempt)
    aggregates = {}
    for task in tasks:
        mine = [attempt for attempt in attempts if attempt["task"] == task["id"]]
        aggregates[f"{task['id']}/{LANGUAGE}"] = study.aggregate(mine)
    return {
        "arm": arm,
        "provider": provider.describe(),
        "request_fields": sorted(extra) if extra else [],
        "attempts": attempts,
        "aggregates": aggregates,
        "syntax": syntax_accept_rate(attempts),
        "evaluated_attempts": sum(1 for attempt in attempts if not attempt.get("provider_error")),
        "provider_errors": sum(1 for attempt in attempts if attempt.get("provider_error")),
    }


def compare(arms: dict[str, dict], tasks: list[dict]) -> dict:
    free = arms["free"]["attempts"]
    constrained_arm = arms["constrained"]["attempts"]
    # Attempts are emitted in the same order for both arms, so index pairing
    # compares the same task and attempt index.
    first_free = [bool(attempt.get("first_attempt_passed")) for attempt in free]
    first_constrained = [bool(attempt.get("first_attempt_passed")) for attempt in constrained_arm]
    final_free = [bool(attempt.get("final_passed")) for attempt in free]
    final_constrained = [bool(attempt.get("final_passed")) for attempt in constrained_arm]
    return {
        "pass_at_1_delta": {**paired_bootstrap(first_free, first_constrained),
                            "direction": "constrained minus free"},
        "final_pass_delta": {**paired_bootstrap(final_free, final_constrained),
                             "direction": "constrained minus free"},
        "syntax_accepted": {"free": arms["free"]["syntax"],
                            "constrained": arms["constrained"]["syntax"]},
        "tasks": [task["id"] for task in tasks],
        "claim": None,
        "claim_status": "open",
        "claim_note": "this record compares two arms; it asserts nothing by itself. A claim "
                      "needs a measured run (a real provider), the same tasks and settings, "
                      "and the intervals above not overlapping zero.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Free versus grammar-constrained generation, same tasks and settings.",
        epilog="With no model the record is labelled not-measured; eval/check_ab_record.py "
               "re-derives that label from the attempts.")
    parser.add_argument("--provider", choices=("mock", "replay", "openai"), default="mock")
    parser.add_argument("--model", help="model name passed to the provider and recorded")
    parser.add_argument("--base-url", help="OpenAI-compatible base URL")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--replay-file", type=Path)
    parser.add_argument("--tasks", type=Path, default=HERE / "tasks.json")
    parser.add_argument("--task", action="append", dest="task_ids")
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--repair-rounds", type=int, default=0, choices=(0, 1, 2))
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--interface", choices=constrained.interfaces(), default="llamacpp")
    parser.add_argument("--feedback", choices=("structured", "text"), default="structured")
    parser.add_argument("--isolation", choices=sandbox.MODES, default="required")
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--memory", type=int, default=256)
    parser.add_argument("--encoding", help="tiktoken encoding for the guide token count")
    parser.add_argument("--hf-tokenizer", help="Hugging Face tokenizer for the token count")
    parser.add_argument("--no-guide", action="store_true", help="omit the guide from both arms")
    parser.add_argument("--store-raw", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    if args.attempts < 1:
        parser.error("--attempts must be at least 1")
    tasks = prompt_module.load_tasks(args.tasks)
    selected = list(tasks.values())
    if args.task_ids:
        unknown = set(args.task_ids) - set(tasks)
        if unknown:
            parser.error(f"unknown task ids: {', '.join(sorted(unknown))}")
        selected = [tasks[task_id] for task_id in args.task_ids]
    task_index = {task["id"]: task for task in selected}

    # Same rule as `eval/study.py`: refuse before spending a call when the
    # requested isolation cannot run here. Otherwise a two-arm comparison ends up
    # as two tables of zeros and looks like a model result.
    try:
        isolation_backend = sandbox.select_backend(args.isolation)
    except sandbox.SandboxUnavailable as error:
        if args.plan_only:
            print(f"warning: this plan cannot run on this host: {error}", file=sys.stderr)
            isolation_backend = None
        else:
            print(f"ab: {error}", file=sys.stderr)
            print(f"ab: use --isolation auto to fall back to {sandbox.select_backend('auto')}, "
                  "or run where user namespaces are permitted", file=sys.stderr)
            return 2

    grammar_text = grammar.emit_gbnf()
    problems = constrained.check(grammar_text)
    if problems:
        for problem in problems:
            print(f"ab: {problem}", file=sys.stderr)
        return 2
    guide = guide_module.load()
    tokenizer = tokens_module.make_tokenizer(args.encoding, args.hf_tokenizer)
    guide_tokens = tokenizer.count(guide.text)

    plan = {
        "arms": list(ARMS),
        "difference_between_arms": f"arm 'constrained' adds {constrained.INTERFACES[args.interface]['field']!r} "
                                   "to the request body; prompts are identical",
        "tasks": [{"id": task["id"], "cases": len(task["tests"])} for task in selected],
        "language": LANGUAGE,
        "attempts_per_task": args.attempts,
        "repair_rounds": args.repair_rounds,
        "interface": args.interface,
        "feedback": args.feedback,
        "request": {"model": args.model, "temperature": args.temperature,
                    "max_tokens": args.max_tokens, "guide_included": not args.no_guide},
        "scale": study.classify_scale(len(selected), args.attempts),
        "isolation": args.isolation,
        "isolation_backend": isolation_backend,
    }
    if args.plan_only:
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0

    record = {
        "schema": SCHEMA,
        "created_at_utc": study.utc_now(),
        "plan": plan,
        "provider": args.provider,
        "arms": {},
        "guidance": {
            "guide": {**guide.describe(), "tokens": guide_tokens,
                      "tokenizer": tokenizer.describe()},
            "grammar": {**constrained.grammar_metadata(grammar_text),
                        "field": constrained.INTERFACES[args.interface]["field"],
                        "interface": args.interface},
            "feedback": {"schema": feedback_module.SCHEMA,
                         "max_bytes": feedback_module.MAX_FEEDBACK_BYTES,
                         "mode": args.feedback},
            "tokens_status": "measured" if tokenizer.available else "not-measured",
            "tokens_reason": tokenizer.reason,
        },
        "environment": {"nava_version": nava_version, "python": sys.version.split()[0],
                        "platform": platform.platform(),
                        "isolation": sandbox.describe_host()},
        "notes": [],
    }
    if args.provider != "openai":
        record["notes"].append("provider does not call a model: latency and token counts are "
                               "not model measurements")
    if not tokenizer.available:
        record["notes"].append(f"guide token count not measured: {tokenizer.reason}")

    with tempfile.TemporaryDirectory(prefix="nava-ab-") as temp:
        workdir = Path(temp)
        for arm in ARMS:
            record["arms"][arm] = run_arm(arm, tasks=selected, task_index=task_index, args=args,
                                          grammar_text=grammar_text, workdir=workdir)
    record["comparison"] = compare(record["arms"], selected)

    evaluated = min(arm["evaluated_attempts"] for arm in record["arms"].values())
    errors = sum(arm["provider_errors"] for arm in record["arms"].values())
    if args.provider == "openai" and evaluated and not errors:
        record["status"] = "measured"
        record["status_reason"] = f"{evaluated} evaluated attempts per arm with a real provider"
    else:
        record["status"] = "not-measured"
        if args.provider != "openai":
            record["status_reason"] = f"provider {args.provider!r} does not call a model"
        elif errors:
            record["status_reason"] = f"{errors} provider calls failed"
        else:
            record["status_reason"] = "no attempt was compiled and tested"
    record["claim_open"] = True

    rendered = json.dumps(record, indent=2, ensure_ascii=False)
    if args.out:
        args.out.write_text(rendered + "\n", encoding="utf-8")
    if not args.quiet:
        if not args.out:
            print(rendered)
        else:
            print(f"wrote {args.out}")
    if not args.quiet:
        for arm in ARMS:
            points = [bucket["pass_at_1"]["point"] for bucket in
                      record["arms"][arm]["aggregates"].values()
                      if bucket.get("pass_at_1", {}).get("point") is not None]
            mean = sum(points) / len(points) if points else None
            print(f"{arm:12s} pass@1 mean {mean} over {len(points)} task(s), "
                  f"syntax accepted {record['arms'][arm]['syntax']['accepted']}/"
                  f"{record['arms'][arm]['syntax']['of_rounds']}")
        delta = record["comparison"]["pass_at_1_delta"]
        print(f"pass@1 delta (constrained - free): {delta['point']} "
              f"[{delta['low']}, {delta['high']}] over {delta['pairs']} pairs")
        print(f"status: {record['status']} ({record['status_reason']}); claim: open")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
