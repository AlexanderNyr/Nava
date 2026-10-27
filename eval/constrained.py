#!/usr/bin/env python3
"""Optional grammar-constrained decoding adapter (PLAN.md phase N7).

This module builds the *extra request fields* a server needs to constrain
generation to the Nava grammar. Nothing here talks to a server, installs an
engine or claims that a constrained run happened.

Status: **experimental**. The payload shapes below follow the documented
request fields of each server; no server was running when this was written, so
`verified_against` is `null` and every record built here says so. A run that
uses these fields must record the adapter, the grammar digest and the server
version next to the result — otherwise "constrained decoding" is an intention,
not a measurement.

Two facts the adapter cannot fix, and does not pretend to:

* constraining to a grammar removes *syntax* errors only. Type errors, ownership
  errors and runtime failures remain, which is why `eval/feedback.py` keeps
  `parse`, `compile` and `tests` apart;
* a constrained decoder still produces untrusted text. The candidate is compiled
  and executed through the same sandbox as any other answer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import gbnf, grammar, prompt as prompt_module  # noqa: E402

SCHEMA = "nava-constrained/1"

#: interface name -> how it is described and which request field carries the
#: grammar. Only fields documented by the server itself belong here.
INTERFACES: dict[str, dict] = {
    "llamacpp": {
        "description": "llama.cpp HTTP server (GGUF models)",
        "field": "grammar",
        "status": "experimental",
        "verified_against": None,
        "note": "the `grammar` field of the request body takes GBNF text",
    },
    "vllm": {
        "description": "vLLM OpenAI-compatible server",
        "field": "guided_grammar",
        "status": "experimental",
        "verified_against": None,
        "note": "vLLM reads `guided_grammar` as an extra body parameter; it must be "
                "enabled on the server side",
    },
}


def interfaces() -> list[str]:
    return sorted(INTERFACES)


def describe(interface: str) -> dict:
    if interface not in INTERFACES:
        raise ValueError(f"unknown interface {interface!r}; use one of "
                         f"{', '.join(interfaces())}")
    entry = INTERFACES[interface]
    return {"schema": SCHEMA, "interface": interface, **entry,
            "grammar_schema": grammar.SCHEMA,
            "syntax_only": "a grammar constrains syntax; types and runtime behaviour "
                           "are still checked by the compiler"}


def grammar_text(path: Path | None = None) -> str:
    return path.read_text(encoding="utf-8") if path else grammar.emit_gbnf()


def grammar_metadata(text: str) -> dict:
    return {"schema": grammar.SCHEMA, "bytes": len(text.encode("utf-8")),
            "lines": text.count("\n"),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def extra_fields(interface: str, text: str) -> dict:
    """The request-body fields that turn on constrained decoding."""
    if interface not in INTERFACES:
        raise ValueError(f"unknown interface {interface!r}; use one of "
                         f"{', '.join(interfaces())}")
    return {INTERFACES[interface]["field"]: text}


def check(text: str) -> list[str]:
    """Problems with a grammar artefact, before it is sent anywhere."""
    try:
        parsed = gbnf.parse(text)
    except gbnf.GbnfError as error:
        return [f"the grammar is not valid GBNF: {error}"]
    return gbnf.validate(parsed)


def preview(interface: str, *, model: str | None, text: str,
            task: str | None = None, language: str = "nava",
            temperature: float | None = 0.0, max_tokens: int | None = 1024,
            full_grammar: bool = True) -> dict:
    """What would be sent, as JSON. `full_grammar=False` keeps the digest only."""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "<the prompt from eval/prompt.py>"}],
        "stream": False,
    }
    if temperature is not None:
        body["temperature"] = temperature
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    body.update(extra_fields(interface, text if full_grammar else "<omitted>"))
    document = {
        "schema": SCHEMA,
        "adapter": describe(interface),
        "request": body,
        "grammar": grammar_metadata(text) if not full_grammar else
                   {**grammar_metadata(text), "text": text},
        "task": task,
        "language": language,
    }
    if task:
        document["prompt"] = prompt_module.build_prompt(task, language)
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Show the request fields a grammar-constrained run would use.",
        epilog="No server is contacted. The adapter is experimental and unverified; see "
               "eval/README.md.")
    parser.add_argument("--interface", required=True, choices=interfaces())
    parser.add_argument("--model", help="model name recorded in the preview")
    parser.add_argument("--grammar", type=Path,
                        help="GBNF file (default: the grammar emitted from eval/grammar.py)")
    parser.add_argument("--task", help="build the prompt for this task id too")
    parser.add_argument("--language", default="nava", choices=prompt_module.LANGUAGES)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--summary", action="store_true",
                        help="record the grammar digest instead of its full text")
    parser.add_argument("--check", action="store_true",
                        help="validate the grammar artefact first; exit 1 when it is unusable")
    parser.add_argument("--out", type=Path, help="write the preview here instead of stdout")
    args = parser.parse_args(argv)

    text = grammar_text(args.grammar)
    if args.check:
        problems = check(text)
        for problem in problems:
            print(f"constrained: {problem}", file=sys.stderr)
        if problems:
            return 1
    document = preview(args.interface, model=args.model, text=text, task=args.task,
                       language=args.language, temperature=args.temperature,
                       max_tokens=args.max_tokens, full_grammar=not args.summary)
    rendered = json.dumps(document, indent=2, ensure_ascii=False)
    if args.out:
        args.out.write_text(rendered + "\n", encoding="utf-8")
        print(f"wrote {args.out} ({len(rendered.encode('utf-8'))} bytes)")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
