#!/usr/bin/env python3
"""Compile and test one saved model response inside the isolation boundary.

Nava, C and C++ candidates are supported. Unlike the previous version this
script does **not** run untrusted code unsandboxed by default:

* compilation and execution both go through `nava_lang.sandbox`;
* stdout and stderr are collected with a byte limit instead of unbounded
  `capture_output`, and a program that exceeds it is killed;
* every report records which backend ran, what it did **not** isolate, and the
  resource limits in force;
* `--isolation required` refuses to run when Linux namespaces are unavailable
  instead of silently falling back to an unsandboxed mode.

The Nava front end itself (parsing and C emission) runs in this process: it is
a pure-Python parser over a text file with its own input limits, and it never
executes candidate code. Only native compilation and native execution are
sandboxed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nava_lang import sandbox  # noqa: E402

LANGUAGES = ("nava", "c", "cpp")
ARITHMETIC = {
    "nava": "checked-i64 (overflow and division errors are diagnosed at run time)",
    "c": "unchecked-signed (C11 signed overflow is undefined behaviour)",
    "cpp": "unchecked-signed (C++17 signed overflow is undefined behaviour)",
}
SUFFIXES = {"nava": ".nv", "c": ".c", "cpp": ".cpp"}


def nonnegative_int(value):
    result = int(value)
    if result < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return result


def positive_float(value):
    result = float(value)
    if not (0 < result < float("inf")):
        raise argparse.ArgumentTypeError("must be positive and finite")
    return result


def positive_int(value):
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def resolve_tool(command: list[str]) -> list[str]:
    """Replace a bare program name with its real absolute path.

    Inside the jail only /usr and a minimal /etc exist, so a name that resolves
    through PATH or a symlink chain (cc -> /etc/alternatives/cc -> gcc-14) must
    be resolved against the host first.
    """
    if not command:
        return command
    path = shutil.which(command[0])
    if path is None:
        return command
    return [os.path.realpath(path), *command[1:]]


def emit_c(source: Path, output: Path) -> tuple[bool, str]:
    """Run `nava emit-c`. The front end is pure Python and executes nothing."""
    import subprocess

    result = subprocess.run(
        [sys.executable, str(ROOT / "nava"), "emit-c", str(source), "-o", str(output)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    return result.returncode == 0, result.stderr[-4000:]


def load_tasks(path: Path) -> dict[str, dict]:
    """Task definitions by id, from the public set or a held-out file."""
    return {task["id"]: task for task in json.loads(path.read_text(encoding="utf-8"))}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Compile and test a saved response inside an isolation boundary.",
        epilog="Exit status: 0 all tests passed, 1 a test or the compilation failed, "
               "2 the evaluation could not run (including unavailable isolation).",
    )
    ap.add_argument("--task", required=True,
                    help="task id, from --task-file (default: eval/tasks.json)")
    ap.add_argument("--task-file", type=Path, default=HERE / "tasks.json",
                    help="task definitions; a held-out set lives outside the repository")
    ap.add_argument("--language", choices=LANGUAGES, required=True)
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--timeout", type=positive_float, default=2.0, help="wall-clock seconds per test")
    ap.add_argument("--compile-timeout", type=positive_float, default=60.0,
                    help="wall-clock seconds for the compilation")
    ap.add_argument("--isolation", choices=sandbox.MODES, default="auto",
                    help="isolation policy: auto (default), required, or off (explicitly unsafe)")
    ap.add_argument("--memory", type=positive_int, default=256, help="address-space limit in MiB")
    tokenizer = ap.add_mutually_exclusive_group()
    tokenizer.add_argument("--encoding", help="tiktoken encoding, e.g. cl100k_base")
    tokenizer.add_argument("--hf-tokenizer", help="Hugging Face tokenizer name or local path; requires transformers")
    ap.add_argument("--prompt-file", type=Path, help="actual prompt file, used with a tokenizer")
    ap.add_argument("--model", help="model name, recorded but not queried")
    ap.add_argument("--generation-ms", type=positive_float, help="measured generation wall time, supplied by you")
    ap.add_argument("--prompt-tokens", type=nonnegative_int, help="API-reported input tokens, supplied by you")
    ap.add_argument("--completion-tokens", type=nonnegative_int, help="API-reported output tokens, supplied by you")
    args = ap.parse_args()
    try:
        tasks = load_tasks(args.task_file)
    except (OSError, ValueError, KeyError) as error:
        ap.error(f"cannot read the task file {args.task_file}: {error}")
    if args.task not in tasks:
        ap.error(f"unknown task {args.task!r}; --task-file has "
                 f"{', '.join(sorted(tasks)) or 'no tasks'}")
    if args.prompt_file and not (args.encoding or args.hf_tokenizer):
        ap.error("--prompt-file requires --encoding or --hf-tokenizer")
    try:
        source = args.source.resolve()
        text = source.read_text(encoding="utf-8")
        result = {
            "task": args.task,
            "language": args.language,
            "task_file": {"path": str(args.task_file), "sha256": hashlib.sha256(
                args.task_file.read_bytes()).hexdigest(), "tasks": len(tasks)},
            "source": {"path": str(source), "bytes_utf8": len(text.encode("utf-8")), "characters": len(text)},
            "generation": {
                "model": args.model,
                "wall_ms_reported": args.generation_ms,
                "prompt_tokens_reported": args.prompt_tokens,
                "completion_tokens_reported": args.completion_tokens,
            },
            "candidate": {
                "language": args.language,
                "arithmetic": ARITHMETIC[args.language],
                "compile_command": None,
            },
        }
        if args.encoding or args.hf_tokenizer:
            try:
                if args.encoding:
                    import tiktoken
                    encoder = tiktoken.get_encoding(args.encoding)
                    count = lambda value: len(encoder.encode(value, disallowed_special=()))
                    label = "tiktoken:" + args.encoding
                else:
                    from transformers import AutoTokenizer
                    encoder = AutoTokenizer.from_pretrained(args.hf_tokenizer, trust_remote_code=False)
                    count = lambda value: len(encoder.encode(value, add_special_tokens=False))
                    label = "huggingface:" + args.hf_tokenizer
            except (ImportError, ValueError, OSError) as exc:
                ap.error(f"could not load tokenizer: {exc}")
            counts = {"tokenizer": label, "source_tokens_without_special_tokens": count(text)}
            if args.prompt_file:
                counts["prompt_file_tokens_without_special_tokens"] = count(
                    args.prompt_file.read_text(encoding="utf-8"))
            result["offline_token_counts"] = counts

        tests = tasks[args.task]["tests"]
        memory_bytes = args.memory * 1024 * 1024
        with tempfile.TemporaryDirectory(prefix="nava-eval-") as temp:
            work = Path(temp)
            candidate = work / ("candidate" + SUFFIXES[args.language])
            candidate.write_text(text, encoding="utf-8")

            if args.language == "nava":
                emitted = work / "candidate.c"
                ok, stderr = emit_c(candidate, emitted)
                if not ok:
                    result["compile"] = {"ok": False, "wall_ms": 0.0, "stderr": stderr}
                    result["tests"] = []
                    result["summary"] = {"passed": 0, "total": len(tests), "all_passed": False}
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                    return 1
                compile_command = resolve_tool(["cc", "-std=c11", "-O2", "candidate.c", "-o", "candidate"])
            elif args.language == "c":
                cc = shlex.split(os.environ.get("CC", "cc"))
                if not cc:
                    ap.error("empty CC")
                compile_command = resolve_tool(cc + ["-std=c11", "-O2", "candidate.c", "-o", "candidate"])
            else:
                cxx = shlex.split(os.environ.get("CXX", "c++"))
                if not cxx:
                    ap.error("empty CXX")
                compile_command = resolve_tool(cxx + ["-std=c++17", "-O2", "candidate.cpp", "-o", "candidate"])
            result["candidate"]["compile_command"] = compile_command

            compile_limits = sandbox.Limits(
                wall_seconds=args.compile_timeout,
                cpu_seconds=max(1, int(args.compile_timeout)),
                memory_bytes=max(memory_bytes, 1024 * 1024 * 1024),
                output_bytes=64 * 1024,
            )
            start = time.perf_counter_ns()
            try:
                compiled = sandbox.run(compile_command, limits=compile_limits,
                                       mode=args.isolation, workdir=work)
            except sandbox.SandboxUnavailable as exc:
                result["isolation"] = {"mode": args.isolation, "backend": None, "available": False}
                result["error"] = str(exc)
                result["summary"] = {"passed": 0, "total": len(tests), "all_passed": False}
                print(json.dumps(result, ensure_ascii=False, indent=2))
                print(f"evaluation refused: {exc}", file=sys.stderr)
                return 2
            compile_ms = (time.perf_counter_ns() - start) / 1_000_000
            result["isolation"] = {
                "mode": args.isolation,
                "backend": compiled.backend,
                "available": True,
                "network_denied": compiled.network_denied,
                "host_files_hidden": compiled.host_files_hidden,
                "isolation_label": sandbox.ISOLATION_LABELS.get(compiled.backend or "", "none"),
                "gaps": sandbox.GAP_NOTES.get(compiled.backend or "", []),
                "limits": {
                    "wall_seconds_per_test": args.timeout,
                    "compile_wall_seconds": args.compile_timeout,
                    "memory_bytes": memory_bytes,
                    "output_bytes": compile_limits.output_bytes,
                },
            }
            result["compile"] = {
                "ok": compiled.exit_code == 0 and not compiled.output_limit_exceeded,
                "wall_ms": round(compile_ms, 3),
                "exit_code": compiled.exit_code,
                "stderr": compiled.stderr[-4000:],
                "timed_out": compiled.timed_out,
                "output_limit_exceeded": compiled.output_limit_exceeded,
                "isolation": compiled.backend,
            }
            result["tests"] = []
            if result["compile"]["ok"]:
                test_limits = sandbox.Limits(
                    wall_seconds=args.timeout,
                    cpu_seconds=max(1, int(-(-args.timeout // 1))),
                    memory_bytes=memory_bytes,
                    output_bytes=64 * 1024,
                )
                for index, case in enumerate(tests, 1):
                    executed = sandbox.run(["./candidate"], limits=test_limits, mode=args.isolation,
                                           stdin=case["stdin"].encode("utf-8"), workdir=work)
                    passed = (executed.exit_code == 0 and executed.stdout == case["stdout"]
                              and not executed.output_limit_exceeded)
                    entry = {
                        "case": index,
                        "passed": passed,
                        "wall_ms": executed.duration_ms,
                        "exit_code": executed.exit_code,
                        "signal": executed.signal,
                        "timed_out": executed.timed_out,
                        "output_limit_exceeded": executed.output_limit_exceeded,
                        "stdout_truncated": executed.stdout_truncated,
                        "stderr_truncated": executed.stderr_truncated,
                    }
                    if not passed:
                        entry.update({
                            "stdin": case["stdin"],
                            "expected_stdout": case["stdout"],
                            "actual_stdout": executed.stdout[:4000],
                            "stderr": executed.stderr[:4000],
                            "reason": executed.failure_reason(),
                        })
                    result["tests"].append(entry)
        passed = sum(case["passed"] for case in result["tests"])
        result["summary"] = {"passed": passed, "total": len(tests), "all_passed": passed == len(tests)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["summary"]["all_passed"] else 1
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"evaluation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
