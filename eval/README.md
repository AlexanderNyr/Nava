# Evaluating Generation with a Specific Model

Two workflows share one task set, one prompt builder and one isolation boundary:

| Tool | Use |
|---|---|
| `eval/evaluate.py` | check **one saved** answer for one task and language |
| `eval/study.py` | run a **paired study**: many tasks × languages × attempts, with repairs, tokens, timing and aggregates |
| `eval/check_study_record.py` | validate a saved record before anyone quotes it |
| `eval/guide.py` | print or verify the versioned model guide (`guides/nava-1.md`) |
| `eval/tokens.py` | count bytes, characters and — only with a real tokenizer — tokens |
| `eval/overhead.py` | measure the complete guidance overhead of a prompt, per language |
| `eval/grammar.py` | the language grammar, as a conformance checker and as GBNF text |
| `eval/constrained.py` | the optional constrained-decoding adapter (`experimental`) |
| `eval/ab.py` | free versus grammar-constrained generation, same tasks and settings |
| `eval/check_ab_record.py` | validate an A/B record before anyone quotes it |
| `eval/feedback.py` | structured compiler feedback for repair rounds |
| `tools/local_model_server.py` | serve a local model over the same chat-completions schema |

`eval/study.py` can call a model through an OpenAI-compatible endpoint (a hosted
one, or a local server — see the last section), replay answers saved earlier, or
run a deterministic mock. **No measured neural-model
result is included in this repository**: the mock and replay modes exist so the
machinery can be tested and audited without a model, and every record says
whether a model was actually called.

**Safety:** the evaluator runs untrusted code through
[`nava_lang/sandbox.py`](../SECURITY.md). On Linux that means user, mount, PID
and network namespaces, a read-only minimal root, one writable directory, and
limits on CPU, memory, output size and process count. Every report contains an
`isolation` block naming the backend and listing what it did **not** isolate.

```json
"isolation": {
  "mode": "auto", "backend": "namespaces", "available": true,
  "network_denied": true, "host_files_hidden": true,
  "gaps": ["protects against a misbehaving program, not against a kernel exploit",
           "the read-only /usr is shared with the host"]
}
```

Use `--isolation required` for a result you can defend: it refuses to run
instead of falling back to rlimits (exit status 2, `"available": false`).
`--isolation off` is an explicit opt-in to the unsafe local mode, where the
filesystem and the network are **not** isolated. Output is always collected
with a byte limit, never with unbounded `capture_output`.

## Paired study (`study.py`)

```bash
# 1. Predeclare the experiment. This prints the plan and calls nothing.
python3 eval/study.py --plan-only --provider openai --model <model> --attempts 30

# 2. Run it. The API key is read from an environment variable and never recorded.
OPENAI_API_KEY=... python3 eval/study.py \
  --provider openai --base-url https://api.example.com/v1 --model <model> \
  --languages nava,c,cpp --attempts 30 --repair-rounds 1 \
  --temperature 0 --max-tokens 1024 --isolation required \
  --held-out /path/outside/the/repo/held_out_tasks.json \
  --out study-<model>.json

# 3. Dry run without a model, and replay of earlier answers.
python3 eval/study.py --provider mock --task gcd --languages nava,c,cpp --attempts 2
python3 eval/study.py --provider replay --replay-file answers.jsonl --languages nava
```

What one attempt records: the prompt hash (and the text with `--store-raw`), the
extraction provenance, whether it compiled, which tests failed, whether it
passed on the **first** attempt, every repair round and its diagnostics, the
time to a correct answer, the wall time, the time to first token, the
provider-reported token counts and the isolation backend that ran the program.

Aggregates per task/language and per language: `pass_at_1`, `final_pass`,
`compile_rate`, `repair_rate`, provider error count, median wall time, median
time to a correct answer, median prompt and completion tokens, with a percentile
bootstrap 95% interval (fixed seed, so a re-run reports the same interval).

### Scale, and why the label matters

`study.py` classifies itself using the thresholds in `PLAN.md` N3: at least
**10 tasks** and **30 independent attempts per task and language**. Below either
threshold the record is `exploratory`, the reasons are printed, and the summary
line says so. The public task set has 11 tasks and 76 cases, so the task
threshold is already met; the attempt threshold is not, and reaching `empirical`
needs 30 independent attempts per task and language. A hidden set goes in a file
**outside this repository** — `--held-out` refuses a path inside the checkout,
and both files are identified in the record by SHA-256 (`plan.task_files`).

A task from a held-out file is evaluated with `--task-file`, so the evaluator
reads the same contract the prompt was built from instead of rejecting an id it
has never seen:

```bash
python3 eval/evaluate.py --task my_hidden_task --task-file /path/outside/held-out.json \
  --language nava --source answer.nv
```

### Rate limits, gateways and retries

A rate limit is not a model failure. When the server answers 429 (or a
transient 5xx) the call is retried after the delay the server itself asks for —
`retry-after` or `x-ratelimit-reset-tokens`, both as `1.5`, `690ms`, `12s` or
`1m26.4s` — with exponential backoff otherwise, up to `--retries` (default 5).
Every round records `http_attempts`, and a run that needed retries says so in
`notes`. A permanent error (401, 400) is **not** retried.

The client sends `User-Agent: nava-study/1.0`. That is not cosmetic: a gateway
may answer the default `Python-urllib/3.13` with HTTP 403 and Cloudflare's
`error code: 1010`, and a study that silently turned that into "the model
failed" would be worthless. Extra headers go through `--header 'Name: value'`
(repeatable); only the header **names** are recorded, never the values, and
`Authorization` may not be set that way — the key comes from `--api-key-env` and
nowhere else.

### Missing values

A provider that does not report token usage yields `null`, never `0` and never
an estimate; `tokens_reported` counts how many of the attempts actually had a
number. An attempt whose provider call failed is recorded with its error class,
is excluded from repairs, and makes the run exit with status 1. If every call
failed, or nothing was compiled and tested, `model_measurements.status` stays
`not-measured` with the reason spelled out.

### Records

`--out PATH` writes `nava-study/1` JSON:

```text
schema, created_at_utc, plan, scale, provider, request, prompt_policy,
environment, attempts[], aggregates{}, model_measurements{}, notes[]
```

Before quoting a record, run the validator:

```bash
python3 eval/check_study_record.py study-<model>.json --require measured
python3 eval/check_study_record.py study-mock.json --require not-measured
```

It re-derives the claim from the attempts instead of trusting the labels: the
schema and required keys, `empirical` only above the 10-task / 30-attempt line,
`measured` only for a non-mock run with evaluated attempts, no provider errors,
and reported token usage; `not-measured` must carry a reason. Aggregates must
match the attempt list. Exit status 1 on any inconsistency. CI runs it on a mock
record and on a deliberately broken endpoint.

`plan` is what was predeclared, `prompt_policy` records whether the language
guide was included and how large it is, `environment` records Nava, Python,
platform and the sandbox probe. Prompts and answers are stored only with
`--store-raw`; by default only their SHA-256 and byte length are kept.

## Running a study against a local model (N3)

`eval/providers.py` speaks the OpenAI chat-completions schema, so a model that
lives on the same machine only needs something that speaks the same schema.
`tools/local_model_server.py` is that something: `transformers` plus `torch`,
no API key, no egress, and `usage` counts that are the lengths of the token
sequences actually processed and generated.

```bash
# 1. Serve the weights. The revision is pinned so the record can cite it.
python3 tools/local_model_server.py \
  --model HuggingFaceTB/SmolLM2-135M-Instruct --revision <commit> \
  --host 127.0.0.1 --port 8765 --threads 2 --max-tokens 1024 &

# 2. Run the ordinary study against it. Nothing in eval/ changes.
NAVA_LOCAL_KEY=local python3 eval/study.py --provider openai \
  --base-url http://127.0.0.1:8765/v1 --api-key-env NAVA_LOCAL_KEY \
  --model HuggingFaceTB/SmolLM2-135M-Instruct \
  --held-out /path/outside/the/repo/held-out.json \
  --languages nava,c,cpp --attempts 2 --repair-rounds 1 \
  --temperature 0.2 --max-tokens 320 --isolation auto \
  --out bench/results/<date>-n3-study-<model>.json
```

The server refuses streaming requests with HTTP 400 instead of pretending to
stream, records the revision in every response (`model@revision`), and caps the
completion length whatever the client asks for; the cap is why
`finish_reason: "length"` dominates a small-model record. It imports
`transformers` and `torch` inside `load()`, so importing it, and testing it,
needs no ML stack at all (`tests/test_local_model_server.py`).

The key is ignored by the server, and the harness still wants the environment
variable to exist: the provider layer treats a missing key as an error, and that
behaviour should not change just because the endpoint is local.

`bench/results/2026-10-27-n3-report.md` is the report of the run that was
actually executed on this machine, and
`bench/results/2026-10-27-n3-study-smollm2-135m.json` is its record. It is
**exploratory**, not empirical: the scale block says why, in numbers.

### A hosted provider

The same command works against a hosted endpoint; nothing in `eval/` changes.
For Groq, note the two client behaviours described above — the User-Agent and the
retry policy — and the tier limits measured on a free key: 1,000 requests/day,
8,000 tokens/minute and **200,000 tokens/day per model**, which is why
`bench/results/2026-10-28-n3-groq-report.md` reports 6 attempts per task and
language rather than the 30 an empirical run needs.

## Model-facing guide, grammar and constrained decoding (N7)

Four separate things live here, and none of them is a model result.

**The guide.** `guides/nava-1.md` is the compact guide the prompt carries. It is
versioned and pinned by `guides/manifest.json` — version, specification version,
SHA-256, bytes, characters, lines — because a study record cites that digest: a
later edit must not silently change what "the model saw" means.
`guides/examples/` holds eight canonical programs whose stdout is recorded in
`guides/examples.json` and re-produced on every test run, so the guide cannot
describe a language the compiler does not implement.

```bash
python3 eval/guide.py --verify          # digests and sizes still match
python3 eval/guide.py --print           # the exact text a prompt carries
python3 eval/guide.py --examples        # canonical examples and their output
```

**The overhead.** An A/B run has to charge Nava for its guide, so the cost is
measured, not guessed:

```bash
python3 eval/tokens.py --guide --encoding cl100k_base
python3 eval/overhead.py --encoding cl100k_base --out eval/measurements/overhead-cl100k_base.json
```

`eval/tokens.py` reports `tokens: null` with a reason when no tokenizer can be
constructed, and `--require measured` makes that an error instead of a silent
zero. `eval/overhead.py` renders the whole prompt through `eval/prompt.py` for
every task and language and records the guide, the task, the instruction, the
total and the difference between languages. The measured records in
`eval/measurements/` say which encoding produced their numbers:

| Prompt (`collatz_steps`) | `cl100k_base` | `o200k_base` |
|---|---|---|
| Nava (guide + task) | 1484 tokens / 4975 chars | 1490 tokens / 4975 chars |
| C (guide + task) | 110 tokens / 447 chars | 110 tokens / 447 chars |
| C++ (guide + task) | 106 tokens / 427 chars | 106 tokens / 427 chars |
| Nava − C | +1374 tokens / +4528 chars | +1380 tokens / +4528 chars |

Those are measurements of two public encodings on this text. They are **not**
measurements of any particular model's prompt: a different tokenizer gives a
different number, and neither number is evidence about accuracy or latency.

**The grammar.** `eval/grammar.py` describes Nava's grammar once and drives two
back ends:

* the compiler's own token stream — the conformance corpus, which is what the
  tests check against the real lexer and parser;
* GBNF text, which is what an inference engine consumes for constrained
  decoding. `eval/gbnf.py` parses that text back and recognises input with it,
  so the artefact is executed rather than trusted.

```bash
python3 eval/grammar.py --check answer.nv     # would the grammar accept it?
python3 eval/grammar.py --emit grammar.gbnf   # the artefact for an engine
python3 eval/grammar.py --validate grammar.gbnf
```

The grammar is a **syntax** filter. It accepts `let x: bool = 1` (a type error)
and `100 / 0` (a runtime error): a context-free grammar cannot prove types,
ownership or termination. `tests/test_grammar.py` measures exactly that
boundary, and the repair accounting in `eval/study.py` keeps the three stages
(`parse`, `compile`, `tests`) apart so a constrained run that removes parse
failures cannot hide the failures it leaves behind.

**The A/B run.** Two arms, one task set, one model, one set of settings:

```bash
python3 eval/ab.py --plan-only --provider openai --model <model> --attempts 30
python3 eval/ab.py --provider openai --base-url https://api.example.com/v1 \
  --model <model> --attempts 30 --interface llamacpp \
  --encoding cl100k_base --out ab-<model>.json
python3 eval/check_ab_record.py ab-<model>.json --require measured
```

The prompts are identical in both arms; only the request payload differs (the
grammar field named by `eval/constrained.py`). Each arm records per-task
aggregates, syntax acceptance per round, latency, provider-reported tokens
(`null` when the provider reports none), first-attempt and final correctness,
and the guidance overhead. `comparison.claim` is `null` and
`comparison.claim_status` is `open`: the harness collects evidence, it does not
assert an improvement. `eval/check_ab_record.py` re-derives the record's status
from the attempts and refuses a claim attached to a `not-measured` record.

The constrained adapter is **experimental**: `eval/constrained.py` knows the
request field for `llamacpp` (`grammar`) and `vllm` (`guided_grammar`), but no
server was running when it was written, so it records `verified_against: null`
and a run must add the server version before anyone cites it.

**Status: no model was called.** Every A/B record produced in this repository is
`not-measured`, the accuracy comparison is open, and nothing in `README.md`,
`PLAN.md` or this file claims that constrained decoding or the shorter guide
improves speed or accuracy.

## Example

1. Generate prompts for the same task:

```bash
python3 eval/prompt.py --task gcd --language nava > nava_prompt.txt
python3 eval/prompt.py --task gcd --language c    > c_prompt.txt
python3 eval/prompt.py --task gcd --language cpp  > cpp_prompt.txt
```

2. Send each complete prompt to the same model using the same settings. Save
   **only the source code produced by the model** as `answer.nv`, `answer.c`
   and `answer.cpp`. Keep the full raw response, prompt, parameters and API
   usage statistics separately.
3. Evaluate the saved answers:

```bash
python3 eval/evaluate.py --task gcd --language nava --source answer.nv
python3 eval/evaluate.py --task gcd --language c    --source answer.c
python3 eval/evaluate.py --task gcd --language cpp  --source answer.cpp
```

Every report contains `candidate.language`, `candidate.compile_command` and
`candidate.arithmetic`. The arithmetic label is not cosmetic: a Nava candidate
is checked (`checked-i64`), while C11 and C++17 signed overflow is undefined
behaviour (`unchecked-signed`). Results across languages must be compared with
that difference in mind; the evaluator never hides it.

The evaluator emits JSON containing compilation status, the result of each
test, compile time, individual execution times, source size and the isolation
block. Every test entry also records `timed_out`, `exit_code`, `signal`,
`output_limit_exceeded` and truncation flags, so a killed program is
distinguishable from a wrong answer. Execution includes sandbox setup and
process startup; it is not generation time. A compilation or test failure
produces exit status 1; being unable to run at all (including unavailable
isolation) produces exit status 2.

Two **hand-written** solutions are included to check the evaluator itself,
not a neural model:

```bash
python3 eval/evaluate.py --task gcd --language nava --source eval/solutions/gcd.nv
python3 eval/evaluate.py --task gcd --language cpp --source eval/solutions/gcd.cpp
```

## Model tokens and latency

If an API reports usage and you measured the complete response time, pass the
values manually. The evaluator records the figures; it does not verify them.

```bash
python3 eval/evaluate.py --task gcd --language nava --source answer.nv \
  --model 'model-name' --generation-ms 1200 \
  --prompt-tokens 300 --completion-tokens 90
```

The numbers above are illustrative, not measured results. If the model uses a
compatible `tiktoken` encoding, source text can also be counted locally:

```bash
python3 -m pip install tiktoken
python3 eval/evaluate.py --task gcd --language nava --source answer.nv \
  --encoding cl100k_base --prompt-file nava_prompt.txt
```

For a Hugging Face tokenizer, install `transformers` and pass
`--hf-tokenizer /path/to/tokenizer` or a model identifier; the latter may
require a network download. Local text counts omit special tokens, system
messages, hidden tokens and Markdown if they are not present in the supplied
files. Prefer API or inference-engine counters for actual usage; do not use
`cl100k_base` as a substitute for an incompatible model's tokenizer.

## Fair comparison

1. Fix the model, inference engine, hardware, sampling settings, system
   message and limits. Run many independent attempts per task and language;
   alternate the order to reduce systematic bias. `study.py` rotates the
   language order per attempt index and records the rotation policy in `plan`.
2. Use identical tasks. Count **all input tokens**, including the Nava guide,
   as well as output tokens. Report prompt caching or fine-tuning separately.
3. Compare first-attempt correctness, compilation rate, full generation time,
   time to first token and token counts; account for repairs and retries
   separately. Tokens per second alone is not time to a correct answer.
4. Measure compiled execution separately under equivalent conditions. Check
   boundary cases and errors; the short tasks in this set are largely
   dominated by process startup.
5. The `gcd`, `sum_to_n`, `factorial`, `fibonacci`, `is_prime`, `digit_sum`,
   `max_of_three` and `collatz_steps` tasks and their tests are public in
   `tasks.json`. They are not a hidden independent benchmark. Reliable
   conclusions need a larger task set and separate held-out tests that are
   never written into this repository.

A shorter source file alone does not establish that an unfamiliar language is
easier for a model or yields a correct answer sooner.
