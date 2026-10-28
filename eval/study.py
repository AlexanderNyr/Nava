#!/usr/bin/env python3
"""Paired model study: time to a correct answer, per task and language.

This is the harness that PLAN.md phase N3 asks for. It does **not** ship a
model result: with `--provider mock` or `--provider replay` it exercises the
whole pipeline without a model, and the record says so. An empirical claim needs
`--provider openai` (or another adapter), a named model and enough attempts; the
record classifies itself as `empirical` or `exploratory` using the thresholds
from the plan and refuses to blur the two.

What is recorded per attempt: the exact prompt (hash, and the text itself only
with `--store-raw`), the extracted code, whether it compiled, whether it passed
on the first attempt, every repair round, the provider-reported token counts
exactly as reported (`null` when absent), wall time, time to first token and the
isolation backend that ran the program.

What is deliberately not recorded: API keys, and — unless asked — prompts or
answers that may contain private data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import random
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import feedback as feedback_module  # noqa: E402
from eval import guide as guide_module  # noqa: E402
from eval import prompt as prompt_module  # noqa: E402
from eval.extraction import extract_code  # noqa: E402
from eval.providers import Request, Response, build_provider  # noqa: E402
from nava_lang import __version__ as nava_version  # noqa: E402
from nava_lang import sandbox  # noqa: E402

SCHEMA = "nava-study/1"
SUFFIXES = {"nava": ".nv", "c": ".c", "cpp": ".cpp"}

#: PLAN.md thresholds for an empirical rather than exploratory report.
MIN_TASKS_FOR_EMPIRICAL = 10
MIN_ATTEMPTS_FOR_EMPIRICAL = 30
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20261027


def _parse_headers(items: list[str]) -> tuple[dict, str | None]:
    """`NAME: value` or `NAME=value` pairs, validated before anything is called."""
    headers: dict[str, str] = {}
    for item in items:
        name, separator, value = item.partition(":")
        if not separator:
            name, separator, value = item.partition("=")
        if not separator or not name.strip() or not value.strip():
            return {}, f"--header expects 'Name: value', got {item!r}"
        if name.strip().lower() in {"authorization", "content-type", "user-agent"} and \
                name.strip().lower() != "user-agent":
            return {}, (f"--header may not set {name.strip()!r}: the key comes from "
                        "--api-key-env and the content type is set by the client")
        headers[name.strip()] = value.strip()
    return headers, None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Scale classification
# --------------------------------------------------------------------------

def classify_scale(task_count: int, attempts: int) -> dict:
    """Label a study as empirical or exploratory, with the reason spelled out."""
    reasons = []
    if task_count < MIN_TASKS_FOR_EMPIRICAL:
        reasons.append(f"{task_count} tasks < {MIN_TASKS_FOR_EMPIRICAL} required")
    if attempts < MIN_ATTEMPTS_FOR_EMPIRICAL:
        reasons.append(f"{attempts} attempts per task and language < {MIN_ATTEMPTS_FOR_EMPIRICAL} required")
    return {
        "label": "exploratory" if reasons else "empirical",
        "reasons": reasons,
        "thresholds": {"tasks": MIN_TASKS_FOR_EMPIRICAL, "attempts_per_task_language": MIN_ATTEMPTS_FOR_EMPIRICAL},
    }


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------

def bootstrap_ci(values: list[bool], resamples: int = BOOTSTRAP_RESAMPLES,
                 seed: int = BOOTSTRAP_SEED) -> dict:
    """Percentile bootstrap 95% interval for a proportion. Deterministic."""
    if not values:
        return {"point": None, "low": None, "high": None, "resamples": 0}
    rng = random.Random(seed)
    point = sum(values) / len(values)
    means = []
    for _ in range(resamples):
        sample = [values[rng.randrange(len(values))] for _ in values]
        means.append(sum(sample) / len(sample))
    means.sort()
    low = means[int(0.025 * len(means))]
    high = means[min(len(means) - 1, int(0.975 * len(means)))]
    return {"point": round(point, 4), "low": round(low, 4), "high": round(high, 4),
            "resamples": resamples}


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 3) if values else None


def aggregate(attempts: list[dict]) -> dict:
    """Summarize attempts; every field that has no data is null, never zero."""
    if not attempts:
        return {"attempts": 0}
    first = [bool(attempt["first_attempt_passed"]) for attempt in attempts]
    final = [bool(attempt["final_passed"]) for attempt in attempts]
    compiled = [bool(attempt["rounds"] and attempt["rounds"][0]["compile_ok"]) for attempt in attempts]
    repaired = [bool(attempt.get("repaired")) for attempt in attempts]
    provider_errors = [attempt for attempt in attempts if attempt.get("provider_error")]
    wall = [attempt["total_wall_ms"] for attempt in attempts if attempt.get("total_wall_ms") is not None]
    ttft = [attempt["rounds"][0]["ttft_ms"] for attempt in attempts
            if attempt.get("rounds") and attempt["rounds"][0].get("ttft_ms") is not None]
    prompt_tokens = [attempt["prompt_tokens"] for attempt in attempts if attempt.get("prompt_tokens") is not None]
    completion_tokens = [attempt["completion_tokens"] for attempt in attempts
                         if attempt.get("completion_tokens") is not None]
    time_to_correct = [attempt["time_to_correct_ms"] for attempt in attempts
                       if attempt.get("time_to_correct_ms") is not None]
    rounds_used = [attempt["rounds_used"] for attempt in attempts if attempt.get("rounds_used") is not None]
    return {
        "attempts": len(attempts),
        "pass_at_1": bootstrap_ci(first),
        "final_pass": bootstrap_ci(final),
        "compile_rate": bootstrap_ci(compiled),
        "repair_rate": (round(sum(repaired) / len(repaired), 4) if repaired else None),
        "provider_errors": len(provider_errors),
        "median_wall_ms": _median(wall),
        "median_time_to_correct_ms": _median(time_to_correct),
        "median_first_token_ms": _median(ttft),
        "median_prompt_tokens": _median([float(value) for value in prompt_tokens]),
        "median_completion_tokens": _median([float(value) for value in completion_tokens]),
        "tokens_reported": {
            "prompt": len(prompt_tokens),
            "completion": len(completion_tokens),
            "of_attempts": len(attempts),
        },
        "median_rounds_used": _median([float(value) for value in rounds_used]),
        "successes": sum(final),
    }


# --------------------------------------------------------------------------
# One attempt
# --------------------------------------------------------------------------

def run_evaluator(source: Path, task_id: str, language: str, *, isolation: str,
                  timeout: float, memory: int, task_file: Path | None = None) -> dict:
    """Run `eval/evaluate.py` and return its parsed report, or an error record."""
    command = [
        sys.executable, str(HERE / "evaluate.py"),
        "--task", task_id, "--language", language, "--source", str(source),
        "--timeout", str(timeout), "--memory", str(memory), "--isolation", isolation,
    ]
    if task_file is not None:
        # A held-out task is not in `eval/tasks.json`; the evaluator has to be
        # told where the contract came from or it would reject the task id.
        command += ["--task-file", str(task_file)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"internal_error": f"{type(error).__name__}: {error}"}
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"internal_error": "evaluator produced no JSON",
                "stderr": result.stderr[-2000:], "exit_code": result.returncode}
    report["_exit_code"] = result.returncode
    return report


def build_repair_prompt(parts: dict[str, str], code: str, report: dict, *,
                        feedback_mode: str = "structured") -> tuple[str, dict]:
    """The prompt for one repair round, and what was fed back.

    `structured` sends one capped JSON object with an explicit stage (`parse`,
    `compile` or `tests`), so a run can separate syntax failures from semantic
    and runtime ones. `text` reproduces the free-form stderr behaviour of the
    first implementation, kept for comparisons against old runs.
    """
    if feedback_mode == "structured":
        document, summary, blob = feedback_module.build(code, report)
        detail = f"Your previous answer {summary}. Feedback ({document['schema']}, stage {document['stage']}):\n{blob}"
        metadata = feedback_module.describe(document, blob)
    else:
        if report.get("compile", {}).get("ok") is False:
            diagnostics = report["compile"].get("stderr", "").strip()[:1200] or "(no compiler output)"
            summary = "did not compile"
        else:
            failed = [case for case in report.get("tests", []) if not case.get("passed")]
            diagnostics = "\n".join(
                f"test {case['case']}: stdin={case.get('stdin', '')!r} "
                f"expected={case.get('expected_stdout', '')!r} actual={case.get('actual_stdout', '')!r}"
                f" exit={case.get('exit_code')} reason={case.get('reason')}"
                for case in failed[:3]
            )
            summary = f"failed {len(failed)} of {len(report.get('tests', []))} tests"
        detail = f"Your previous answer {summary}. Diagnostics:\n{diagnostics}"
        metadata = {"schema": "text", "stage": None, "codes": [], "diagnostics": 0,
                    "failures": 0, "elided": 0,
                    "bytes": len(diagnostics.encode("utf-8")),
                    "sha256": hashlib.sha256(diagnostics.encode("utf-8")).hexdigest()}
    prompt = (
        f"{parts['guide']}\n\n{parts['task']}\n\n"
        f"{detail}\n\n"
        f"Previous answer:\n{code}\n\n"
        f"Return a corrected program. {parts['instruction']}\n"
    )
    return prompt, metadata


def attempt_once(*, task_id: str, language: str, attempt_index: int, provider,
                 request_template: dict, parts: dict[str, str], system: str | None,
                 repair_rounds: int, isolation: str, timeout: float, memory: int,
                 store_raw: bool, workdir: Path, feedback_mode: str = "structured",
                 syntax_check=None, request_extra: dict | None = None,
                 task_file: Path | None = None) -> dict:
    record = {
        "task": task_id,
        "language": language,
        "attempt": attempt_index,
        "rounds": [],
        "first_attempt_passed": False,
        "final_passed": False,
        "repaired": False,
        "rounds_used": 0,
        "time_to_correct_ms": None,
        "total_wall_ms": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "provider_error": None,
        "code": None,
        "extraction": None,
    }
    current_prompt = request_template["prompt"]
    wall_sum = 0.0
    prompt_tokens_sum = 0
    prompt_tokens_seen = 0
    completion_tokens_sum = 0
    completion_tokens_seen = 0
    time_to_correct = None

    for round_index in range(repair_rounds + 1):
        request = Request(
            prompt=current_prompt,
            system=system,
            model=request_template.get("model"),
            temperature=request_template.get("temperature"),
            max_tokens=request_template.get("max_tokens"),
            stream=request_template.get("stream", False),
            extra=request_extra if request_extra is not None else request_template.get("extra"),
        )
        response: Response = provider.complete(request)
        round_record = {
            "round": round_index,
            "prompt_sha256": request.prompt_sha256,
            "prompt_bytes": len(request.prompt.encode("utf-8")),
            "prompt": request.prompt if store_raw else None,
            "response": response.report() if store_raw else None,
            "response_sha256": hashlib.sha256(response.text.encode("utf-8")).hexdigest(),
            "response_bytes": len(response.text.encode("utf-8")),
            "model": response.model,
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
            "ttft_ms": response.ttft_ms,
            "wall_ms": response.wall_ms,
            "finish_reason": response.finish_reason,
            "http_attempts": response.attempts,
            "error": response.error,
            "error_class": response.error_class,
            "compile_ok": False,
            "passed": False,
            "tests_passed": 0,
            "tests_total": 0,
            "isolation_backend": None,
            "syntax_accepted": None,
            "feedback": None,
        }
        if response.prompt_tokens is not None:
            prompt_tokens_sum += response.prompt_tokens
            prompt_tokens_seen += 1
        if response.completion_tokens is not None:
            completion_tokens_sum += response.completion_tokens
            completion_tokens_seen += 1
        if response.wall_ms is not None:
            wall_sum += response.wall_ms
        record["rounds"].append(round_record)
        if not response.ok:
            record["provider_error"] = {"message": response.error, "class": response.error_class,
                                        "round": round_index}
            break
        code, provenance = extract_code(response.text, language)
        round_record["extraction"] = provenance
        if syntax_check is not None:
            # Syntactic validity of the candidate as written, independent of the
            # compiler: a grammar-constrained arm can be compared with a free one.
            round_record["syntax_accepted"] = bool(syntax_check(code))
        if store_raw:
            round_record["code"] = code
        candidate = workdir / f"{task_id}-{language}-{attempt_index}-{round_index}{SUFFIXES[language]}"
        candidate.write_text(code, encoding="utf-8")
        report = run_evaluator(candidate, task_id, language, isolation=isolation,
                               timeout=timeout, memory=memory, task_file=task_file)
        if "internal_error" in report:
            round_record["error"] = report["internal_error"]
            round_record["error_class"] = "EvaluatorError"
            record["provider_error"] = {"message": report["internal_error"],
                                        "class": "EvaluatorError", "round": round_index}
            break
        summary = report.get("summary", {})
        round_record.update({
            "compile_ok": bool(report.get("compile", {}).get("ok")),
            "passed": bool(summary.get("all_passed")),
            "tests_passed": summary.get("passed"),
            "tests_total": summary.get("total"),
            "isolation_backend": (report.get("isolation") or {}).get("backend"),
            "evaluator_exit_code": report.get("_exit_code"),
        })
        if round_index == 0:
            record["first_attempt_passed"] = bool(summary.get("all_passed"))
        if summary.get("all_passed"):
            record["final_passed"] = True
            record["rounds_used"] = round_index + 1
            # A provider that reports no wall time leaves this missing; 0 would
            # be a fabricated measurement (PLAN.md N3).
            time_to_correct = round(wall_sum, 3) if wall_sum else None
            break
        if round_index < repair_rounds:
            current_prompt, round_record["feedback"] = build_repair_prompt(
                parts, code, report, feedback_mode=feedback_mode)
    if not record["final_passed"]:
        record["rounds_used"] = len(record["rounds"])
    record["repaired"] = record["final_passed"] and not record["first_attempt_passed"]
    record["time_to_correct_ms"] = time_to_correct
    record["total_wall_ms"] = round(wall_sum, 3) if wall_sum else None
    record["prompt_tokens"] = prompt_tokens_sum if prompt_tokens_seen == len(record["rounds"]) else None
    record["completion_tokens"] = (
        completion_tokens_sum if completion_tokens_seen == len(record["rounds"]) else None)
    record["codes"] = None
    return record


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def load_task_file(path: Path, held_out: bool) -> list[dict]:
    tasks = json.loads(path.read_text(encoding="utf-8"))
    for task in tasks:
        task["held_out"] = held_out
        task["source"] = str(path)
    return tasks


def language_order(languages: list[str], attempt_index: int) -> list[str]:
    """Rotate the language order per attempt so no language is always first."""
    if not languages:
        return []
    offset = attempt_index % len(languages)
    return languages[offset:] + languages[:offset]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Paired model study: pass@1, repairs, tokens and time to a correct answer.",
        epilog="Refuses to call a result 'empirical' below the thresholds in PLAN.md N3.",
    )
    parser.add_argument("--provider", choices=("mock", "replay", "openai"), default="mock")
    parser.add_argument("--model", help="model name passed to the provider and recorded")
    parser.add_argument("--base-url", help="OpenAI-compatible base URL, e.g. http://localhost:8000/v1")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY",
                        help="environment variable holding the API key (never recorded)")
    parser.add_argument("--replay-file", type=Path, help="JSONL of saved answers for --provider replay")
    parser.add_argument("--header", action="append", default=[], dest="headers", metavar="NAME: VALUE",
                        help="extra request header for the openai provider; only the header "
                             "names are recorded, never the values")
    parser.add_argument("--retries", type=int, default=5,
                        help="retries for a rate-limited or transiently failing call (default 5)")
    parser.add_argument("--tasks", type=Path, default=HERE / "tasks.json")
    parser.add_argument("--held-out", type=Path,
                        help="additional task file kept outside the repository")
    parser.add_argument("--task", action="append", dest="task_ids", help="restrict to these task ids")
    parser.add_argument("--languages", default="nava,c,cpp")
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--repair-rounds", type=int, default=0, choices=(0, 1, 2))
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--stream", action="store_true", help="request a streaming response")
    parser.add_argument("--system-file", type=Path, help="system message; default: none")
    parser.add_argument("--no-guide", action="store_true", help="omit the language guide")
    parser.add_argument("--feedback", choices=("structured", "text"), default="structured",
                        help="repair feedback: one capped JSON document with an explicit "
                             "stage (default), or the free-form compiler output")
    parser.add_argument("--isolation", choices=sandbox.MODES, default="required")
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--memory", type=int, default=256)
    parser.add_argument("--out", type=Path, help="write the study record here")
    parser.add_argument("--store-raw", action="store_true",
                        help="store full prompts and answers (may contain private data)")
    parser.add_argument("--plan-only", action="store_true",
                        help="print the predeclared plan and exit without calling anything")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    languages = [item.strip() for item in args.languages.split(",") if item.strip()]
    unknown = [item for item in languages if item not in prompt_module.LANGUAGES]
    if unknown:
        parser.error(f"unknown languages: {', '.join(unknown)}")
    if args.attempts < 1:
        parser.error("--attempts must be at least 1")
    system = args.system_file.read_text(encoding="utf-8") if args.system_file else None
    headers, header_problem = _parse_headers(args.headers)
    if header_problem:
        parser.error(header_problem)
    if args.retries < 0:
        parser.error("--retries must not be negative")

    public = load_task_file(args.tasks, held_out=False)
    if args.held_out:
        # "Held out" has to mean something: a file inside the repository is a
        # public task set with a different name, so refuse it rather than
        # record a hidden set that was never hidden.
        resolved = args.held_out.resolve()
        if resolved == ROOT or ROOT in resolved.parents:
            parser.error(f"--held-out {args.held_out} is inside the repository "
                         f"({ROOT}); keep the hidden set outside it")
    held_out_tasks = load_task_file(args.held_out, held_out=True) if args.held_out else []
    tasks = public + held_out_tasks
    if args.task_ids:
        wanted = set(args.task_ids)
        tasks = [task for task in tasks if task["id"] in wanted]
        missing = wanted - {task["id"] for task in tasks}
        if missing:
            parser.error(f"unknown task ids: {', '.join(sorted(missing))}")
    if not tasks:
        parser.error("no tasks selected")
    task_index = {task["id"]: task for task in tasks}

    task_files = []
    for path, held_out in ((args.tasks, False), (args.held_out, True)):
        if path is None:
            continue
        material = path.read_bytes()
        task_files.append({"held_out": held_out,
                           "sha256": hashlib.sha256(material).hexdigest(),
                           "tasks": sum(1 for task in tasks if task["source"] == str(path)),
                           "name": "held-out set" if held_out else path.name})
    plan = {
        "tasks": [{"id": task["id"], "held_out": task["held_out"], "cases": len(task["tests"])}
                  for task in tasks],
        "task_files": task_files,
        "languages": languages,
        "attempts_per_task_language": args.attempts,
        "repair_rounds": args.repair_rounds,
        "order_policy": "languages rotated per attempt index; attempts are independent",
        "request": {"model": args.model, "temperature": args.temperature,
                    "max_tokens": args.max_tokens, "stream": args.stream,
                    "system_message": bool(system), "guide_included": not args.no_guide,
                    "feedback": args.feedback,
                    # Names only: a value may be a credential.
                    "extra_header_names": sorted(headers)},
        "isolation": args.isolation,
        "store_raw": args.store_raw,
    }
    scale = classify_scale(len(tasks), args.attempts)
    plan["expected_scale"] = scale
    if args.plan_only:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    provider = build_provider(args.provider, model=args.model, base_url=args.base_url,
                              api_key_env=args.api_key_env, replay_path=args.replay_file,
                              script=_mock_script(args, task_index, languages),
                              headers=headers, max_retries=args.retries)
    record = {
        "schema": SCHEMA,
        "created_at_utc": utc_now(),
        "plan": plan,
        "scale": scale,
        "provider": provider.describe(),
        "request": plan["request"],
        "prompt_policy": {
            "guide_included": not args.no_guide,
            "guide_bytes": {language: len(prompt_module.GUIDES[language].encode("utf-8"))
                            for language in languages},
            # The versioned Nava guide: name, version and digest, so the text a
            # model saw can be identified even when it is not stored.
            "guide": prompt_module.GUIDE_INFO if "nava" in languages else None,
            "guide_sha256": {language: prompt_module.GUIDE_DIGESTS[language]
                             for language in languages},
            "feedback": args.feedback,
            "instruction": prompt_module.OUTPUT_INSTRUCTION,
            "system_message": system if args.store_raw else (hashlib.sha256(
                system.encode("utf-8")).hexdigest() if system else None),
        },
        "environment": {
            "nava_version": nava_version,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "isolation": sandbox.describe_host(),
        },
        "attempts": [],
        "aggregates": {},
        "notes": [],
    }
    if args.provider != "openai":
        record["notes"].append(
            f"provider {args.provider!r} does not call a model: generation time and token usage "
            "are not model measurements")
    if not args.store_raw:
        record["notes"].append("raw prompts and answers were not stored (--store-raw is off)")
    if scale["label"] == "exploratory":
        record["notes"].append("exploratory study: " + "; ".join(scale["reasons"]))

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="nava-study-") as temp:
        workdir = Path(temp)
        for attempt_index in range(args.attempts):
            for task in tasks:
                for language in language_order(languages, attempt_index):
                    parts = prompt_module.build_parts(task["id"], language, task_index,
                                                      include_guide=not args.no_guide)
                    request_template = {
                        "prompt": prompt_module.build_prompt(task["id"], language, task_index,
                                                             include_guide=not args.no_guide),
                        "model": args.model,
                        "temperature": args.temperature,
                        "max_tokens": args.max_tokens,
                        "stream": args.stream,
                    }
                    attempt = attempt_once(
                        task_id=task["id"], language=language, attempt_index=attempt_index,
                        provider=provider,
                        request_template=request_template, parts=parts, system=system,
                        repair_rounds=args.repair_rounds, isolation=args.isolation,
                        timeout=args.timeout, memory=args.memory, store_raw=args.store_raw,
                        workdir=workdir, feedback_mode=args.feedback,
                        task_file=Path(task["source"]),
                    )
                    attempt["held_out"] = task["held_out"]
                    record["attempts"].append(attempt)
    record["duration_s"] = round(time.perf_counter() - started, 3)

    for language in languages:
        for task in tasks:
            key = f"{task['id']}/{language}"
            record["aggregates"][key] = aggregate(
                [attempt for attempt in record["attempts"]
                 if attempt["task"] == task["id"] and attempt["language"] == language])
    for language in languages:
        record["aggregates"][f"all/{language}"] = aggregate(
            [attempt for attempt in record["attempts"] if attempt["language"] == language])
    record["aggregates"]["all/all"] = aggregate(record["attempts"])

    retried = sum(1 for attempt in record["attempts"]
                  if any((round_record.get("http_attempts") or 1) > 1
                         for round_record in attempt["rounds"]))
    if retried:
        record["notes"].append(
            f"{retried} attempt(s) needed more than one HTTP request because the server "
            "rate-limited or failed transiently; each is recorded with its real token "
            "counts, not as a provider error")
    errors = sum(1 for attempt in record["attempts"] if attempt.get("provider_error"))
    overall = record["aggregates"]["all/all"]
    evaluated = sum(1 for attempt in record["attempts"]
                    if any(round_record.get("tests_total") for round_record in attempt["rounds"]))
    if args.provider != "openai":
        status, reason = "not-measured", f"provider {args.provider!r} does not call a model"
    elif errors:
        status = "not-measured"
        reason = (f"{errors} attempt(s) failed at the provider or evaluator level; "
                  "the aggregates are incomplete")
    elif evaluated == 0:
        status, reason = "not-measured", "no answer was compiled and tested"
    else:
        status, reason = "measured", "every attempt produced a compile and test result"
    record["model_measurements"] = {
        "status": status,
        "reason": reason,
        "evaluated_attempts": evaluated,
        "provider": args.provider,
        "model": args.model,
        "pass_at_1": overall.get("pass_at_1", {}).get("point"),
        "final_pass": overall.get("final_pass", {}).get("point"),
        "median_wall_ms": overall.get("median_wall_ms"),
        "median_completion_tokens": overall.get("median_completion_tokens"),
    }

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not args.quiet:
        _print_summary(record, languages, args)
    if errors:
        print(f"error: {errors} attempt(s) failed at the provider or evaluator level; "
              "the study record is incomplete and its aggregates must not be reported "
              "as a model result", file=sys.stderr)
    return 0 if errors == 0 else 1


def _print_summary(record: dict, languages: list[str], args) -> None:
    print(f"study: provider={args.provider} model={args.model or '-'} "
          f"tasks={len(record['plan']['tasks'])} attempts={args.attempts} "
          f"repairs={args.repair_rounds} isolation={args.isolation}")
    print(f"scale: {record['scale']['label']}"
          + ("" if record["scale"]["label"] == "empirical"
             else " (" + "; ".join(record["scale"]["reasons"]) + ")"))
    print(f"{'language':10s} {'pass@1':>16s} {'final':>16s} {'compile':>8s} "
          f"{'wall ms':>9s} {'prompt tok':>10s} {'compl tok':>10s}")
    for language in languages + ["all"]:
        entry = record["aggregates"][f"all/{language}"]
        if not entry.get("attempts"):
            continue
        pass_at_1 = entry["pass_at_1"]
        final = entry["final_pass"]
        def interval(bucket):
            return f"{bucket['point']:.2f} [{bucket['low']:.2f},{bucket['high']:.2f}]"
        wall = "-" if entry["median_wall_ms"] is None else f"{entry['median_wall_ms']:.0f}"
        prompt_tokens = "-" if entry["median_prompt_tokens"] is None else f"{entry['median_prompt_tokens']:.0f}"
        completion = ("-" if entry["median_completion_tokens"] is None
                      else f"{entry['median_completion_tokens']:.0f}")
        print(f"{language:10s} {interval(pass_at_1):>16s} {interval(final):>16s} "
              f"{entry['compile_rate']['point']:>8.2f} {wall:>9s} {prompt_tokens:>10s} {completion:>10s}")
    for note in record["notes"]:
        print(f"note: {note}")
    measurements = record["model_measurements"]
    if measurements["status"] != "measured":
        if args.provider in ("mock", "replay"):
            print("result: no model was called; this run is a pipeline check, "
                  "not a model measurement")
        else:
            print(f"result: NOT MEASURED — {measurements['reason']}")


def _mock_script(args, task_index: dict, languages: list[str]):
    """Deterministic dry-run answers, chosen from the prompt itself.

    Keying on the prompt rather than on call order keeps the mock honest when
    attempts are interleaved: the same prompt always gets the same answer, and
    a repair prompt (which repeats the guide and the task) gets the same answer
    again. `gcd` is answered correctly in every language so the pipeline can be
    checked end to end; every other task gets a program that compiles but
    prints `0`, which exercises the correctness gate.
    """
    if args.provider != "mock":
        return None
    correct = {
        "nava": ("```nava\nfn gcd(a: i64, b: i64) -> i64 {\n"
                 "    while b != 0 {\n        let r = a % b\n        a = b\n        b = r\n    }\n"
                 "    return a\n}\n\nfn main() {\n    let a = read_i64()\n    let b = read_i64()\n"
                 "    print(gcd(a, b))\n}\n```\n"),
        "c": ("```c\n#include <inttypes.h>\n#include <stdint.h>\n#include <stdio.h>\n\n"
              "int main(void) {\n    int64_t a, b;\n"
              "    if (scanf(\"%\" SCNd64 \" %\" SCNd64, &a, &b) != 2) return 1;\n"
              "    while (b != 0) {\n        int64_t r = a % b;\n        a = b;\n        b = r;\n    }\n"
              "    printf(\"%\" PRId64 \"\\n\", a);\n    return 0;\n}\n```\n"),
        "cpp": ("```cpp\n#include <cstdint>\n#include <iostream>\n\nint main() {\n"
                "    std::int64_t a, b;\n    if (!(std::cin >> a >> b)) return 1;\n"
                "    while (b != 0) {\n        const std::int64_t r = a % b;\n        a = b;\n"
                "        b = r;\n    }\n    std::cout << a << '\\n';\n}\n```\n"),
    }
    wrong = {
        "nava": "```nava\nfn main() { print(0) }\n```\n",
        "c": "```c\n#include <stdio.h>\nint main(void) { printf(\"0\\n\"); return 0; }\n```\n",
        "cpp": "```cpp\n#include <cstdio>\nint main() { std::printf(\"0\\n\"); }\n```\n",
    }

    def responder(request):
        text = request.prompt
        language = next((name for name in languages
                         if prompt_module.GUIDES[name].strip() and prompt_module.GUIDES[name].strip() in text),
                        languages[0])
        for task_id, task in task_index.items():
            if task["description"] in text and task_id == "gcd":
                return correct[language]
        return wrong[language]

    return [responder]


if __name__ == "__main__":
    raise SystemExit(main())
