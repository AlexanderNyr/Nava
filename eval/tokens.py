#!/usr/bin/env python3
"""Count bytes, characters and — only with a real tokenizer — tokens.

PLAN.md N7 asks for the guide and the source to be measured with the *target
model's* tokenizer "where available". This tool therefore has three states and
never a fourth:

* `measured` — a tokenizer that was actually constructed produced the number;
* `not-measured` with a reason — the library is missing, the encoding failed to
  load, or no tokenizer was named;
* an error (exit 2) — the arguments are wrong.

There is no character-per-token estimate and no `tokens / 4` shorthand anywhere
in the output. Bytes, characters and lines are always reported: they need no
tokenizer and cannot be mistaken for token counts.
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

from eval import guide as guide_module  # noqa: E402

SCHEMA = "nava-tokens/1"


def probe() -> dict:
    """Which tokenizers can be constructed on this host, without loading data."""
    state = {"tiktoken": None, "transformers": None}
    try:
        import tiktoken  # noqa: F401
        state["tiktoken"] = getattr(__import__("tiktoken"), "__version__", "unknown")
    except Exception as error:  # noqa: BLE001 - any failure means "not available"
        state["tiktoken"] = None
        state["tiktoken_error"] = f"{type(error).__name__}: {error}"
    try:
        import transformers  # noqa: F401
        state["transformers"] = getattr(transformers, "__version__", "unknown")
    except Exception as error:  # noqa: BLE001
        state["transformers"] = None
        state["transformers_error"] = f"{type(error).__name__}: {error}"
    return state


class Tokenizer:
    """A constructed tokenizer, or an explanation of why there is none."""

    def __init__(self, kind: str, name: str, counter=None, reason: str | None = None):
        self.kind = kind
        self.name = name
        self._counter = counter
        self.reason = reason

    @property
    def available(self) -> bool:
        return self._counter is not None

    def describe(self) -> dict:
        return {"kind": self.kind, "name": self.name, "available": self.available,
                "reason": self.reason}

    def count(self, text: str) -> int | None:
        return None if self._counter is None else int(self._counter(text))


def make_tokenizer(encoding: str | None = None, hf_tokenizer: str | None = None) -> Tokenizer:
    """Build a real tokenizer, or return an object that explains the absence."""
    if encoding and hf_tokenizer:
        raise ValueError("choose one tokenizer: --encoding or --hf-tokenizer")
    if encoding:
        try:
            import tiktoken
        except Exception as error:  # noqa: BLE001
            return Tokenizer("tiktoken", encoding,
                             reason=f"the tiktoken package is not installed "
                                    f"({type(error).__name__}); python3 -m pip install tiktoken")
        try:
            encoder = tiktoken.get_encoding(encoding)
        except Exception as error:  # noqa: BLE001 - may need a download
            return Tokenizer("tiktoken", encoding,
                             reason=f"tiktoken could not load {encoding!r}: {type(error).__name__}: {error}")
        return Tokenizer("tiktoken", encoding, counter=lambda text: len(encoder.encode(text)))
    if hf_tokenizer:
        try:
            from transformers import AutoTokenizer
        except Exception as error:  # noqa: BLE001
            return Tokenizer("transformers", hf_tokenizer,
                             reason=f"the transformers package is not installed "
                                    f"({type(error).__name__}); python3 -m pip install transformers")
        try:
            tokenizer = AutoTokenizer.from_pretrained(hf_tokenizer)
        except Exception as error:  # noqa: BLE001 - may need a network download
            return Tokenizer("transformers", hf_tokenizer,
                             reason=f"the tokenizer {hf_tokenizer!r} could not be loaded: "
                                    f"{type(error).__name__}: {error}")
        return Tokenizer("transformers", hf_tokenizer,
                         counter=lambda text: len(tokenizer.encode(text, add_special_tokens=False)))
    return Tokenizer("none", "none",
                     reason="no tokenizer was named; pass --encoding NAME (tiktoken) or "
                            "--hf-tokenizer PATH_OR_ID")


def measure(parts: dict[str, str], tokenizer: Tokenizer) -> dict:
    """One record for a set of named texts. Sizes are facts; tokens may be null."""
    measured = []
    for name, text in parts.items():
        entry = {"part": name, **guide_module.size_of(text), "sha256": guide_module.sha256(text)}
        entry["tokens"] = tokenizer.count(text)
        measured.append(entry)
    total = None if not tokenizer.available else sum(entry["tokens"] for entry in measured)
    return {
        "schema": SCHEMA,
        "tokenizer": tokenizer.describe(),
        "status": "measured" if tokenizer.available else "not-measured",
        "reason": tokenizer.reason,
        "tokens": total,
        "parts": measured,
        "note": "bytes, characters and lines are always exact; tokens are null unless a real "
                "tokenizer was constructed, and no estimate is substituted",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure a guide and/or a source file; tokens only with a real tokenizer.")
    parser.add_argument("--guide", nargs="?", const="", metavar="NAME",
                        help="include the versioned guide (default name: the current one)")
    parser.add_argument("--source", action="append", type=Path, default=[],
                        help="include this file (repeatable)")
    parser.add_argument("--encoding", help="tiktoken encoding name, e.g. cl100k_base")
    parser.add_argument("--hf-tokenizer", help="Hugging Face tokenizer path or model id")
    parser.add_argument("--probe", action="store_true", help="report which tokenizers are installed")
    parser.add_argument("--require", choices=("measured", "not-measured"),
                        help="exit 1 unless the record has this status")
    parser.add_argument("--json", action="store_true", help="print the record (default)")
    args = parser.parse_args(argv)

    if args.probe:
        state = probe()
        print(json.dumps({"schema": SCHEMA, "probe": state}, indent=2))
        return 0

    parts: dict[str, str] = {}
    if args.guide is not None:
        chosen = guide_module.load(args.guide or None)
        parts[f"guide:{chosen.name}"] = chosen.text
    for path in args.source:
        parts[path.name] = path.read_text(encoding="utf-8")
    if not parts:
        parser.error("nothing to measure: pass --guide and/or --source")
    try:
        tokenizer = make_tokenizer(args.encoding, args.hf_tokenizer)
    except ValueError as error:
        parser.error(str(error))

    record = measure(parts, tokenizer)
    print(json.dumps(record, indent=2, ensure_ascii=False))
    if args.require and record["status"] != args.require:
        print(f"tokens: status is {record['status']!r}, --require {args.require} was requested",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
