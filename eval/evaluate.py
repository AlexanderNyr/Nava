#!/usr/bin/env python3
"""Compile and test one saved model response. This is NOT a sandbox."""
from __future__ import annotations
import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


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


def main():
    tasks = {task["id"]: task for task in json.loads((HERE / "tasks.json").read_text(encoding="utf-8"))}
    ap = argparse.ArgumentParser(description="Compile and test a saved response. NOT a security sandbox.")
    ap.add_argument("--task", choices=sorted(tasks), required=True)
    ap.add_argument("--language", choices=("nava", "cpp"), required=True)
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--timeout", type=positive_float, default=2.0, help="seconds per test")
    tokenizer = ap.add_mutually_exclusive_group()
    tokenizer.add_argument("--encoding", help="tiktoken encoding, e.g. cl100k_base")
    tokenizer.add_argument("--hf-tokenizer", help="Hugging Face tokenizer name or local path; requires transformers")
    ap.add_argument("--prompt-file", type=Path, help="actual prompt file, used with a tokenizer")
    ap.add_argument("--model", help="model name, recorded but not queried")
    ap.add_argument("--generation-ms", type=positive_float, help="measured generation wall time, supplied by you")
    ap.add_argument("--prompt-tokens", type=nonnegative_int, help="API-reported input tokens, supplied by you")
    ap.add_argument("--completion-tokens", type=nonnegative_int, help="API-reported output tokens, supplied by you")
    args = ap.parse_args()
    if args.prompt_file and not (args.encoding or args.hf_tokenizer):
        ap.error("--prompt-file requires --encoding or --hf-tokenizer")
    try:
        source = args.source.resolve()
        text = source.read_text(encoding="utf-8")
        result = {
            "task": args.task,
            "language": args.language,
            "source": {"path": str(source), "bytes_utf8": len(text.encode("utf-8")), "characters": len(text)},
            "generation": {
                "model": args.model,
                "wall_ms_reported": args.generation_ms,
                "prompt_tokens_reported": args.prompt_tokens,
                "completion_tokens_reported": args.completion_tokens,
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
                counts["prompt_file_tokens_without_special_tokens"] = count(args.prompt_file.read_text(encoding="utf-8"))
            result["offline_token_counts"] = counts
        tests = tasks[args.task]["tests"]
        with tempfile.TemporaryDirectory(prefix="nava-eval-") as temp:
            binary = Path(temp) / "candidate"
            if args.language == "nava":
                command = [sys.executable, str(ROOT / "nava"), "build", str(source), "-o", str(binary), "--opt", "2"]
            else:
                cxx = shlex.split(os.environ.get("CXX", "c++"))
                if not cxx:
                    ap.error("empty CXX")
                command = cxx + ["-std=c++17", "-O2", "-x", "c++", str(source), "-o", str(binary)]
            start = time.perf_counter_ns()
            try:
                compiled = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
                compile_ms = (time.perf_counter_ns() - start) / 1_000_000
                result["compile"] = {"ok": compiled.returncode == 0, "wall_ms": round(compile_ms, 3), "stderr": compiled.stderr[-4000:]}
            except subprocess.TimeoutExpired:
                result["compile"] = {"ok": False, "wall_ms": 60000, "stderr": "compilation timed out"}
            result["tests"] = []
            if result["compile"]["ok"]:
                for index, case in enumerate(tests, 1):
                    start = time.perf_counter_ns()
                    try:
                        executed = subprocess.run([str(binary)], input=case["stdin"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=args.timeout)
                        run_ms = (time.perf_counter_ns() - start) / 1_000_000
                        passed = executed.returncode == 0 and executed.stdout == case["stdout"]
                        entry = {"case": index, "passed": passed, "wall_ms": round(run_ms, 3), "exit_code": executed.returncode}
                        if not passed:
                            entry.update({"stdin": case["stdin"], "expected_stdout": case["stdout"], "actual_stdout": executed.stdout[:4000], "stderr": executed.stderr[:4000]})
                    except subprocess.TimeoutExpired:
                        entry = {"case": index, "passed": False, "timeout": True, "stdin": case["stdin"]}
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
