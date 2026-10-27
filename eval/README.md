# Evaluating Generation with a Specific Model

Two workflows share one task set, one prompt builder and one isolation boundary:

| Tool | Use |
|---|---|
| `eval/evaluate.py` | check **one saved** answer for one task and language |
| `eval/study.py` | run a **paired study**: many tasks × languages × attempts, with repairs, tokens, timing and aggregates |
| `eval/check_study_record.py` | validate a saved record before anyone quotes it |

`eval/study.py` can call a model through an OpenAI-compatible endpoint, replay
answers saved earlier, or run a deterministic mock. **No measured neural-model
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
line says so. The public task set has 8 tasks, so reaching `empirical` requires
a held-out file with at least two more; keep that file **outside this
repository** so it never appears in prompts or fixtures.

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
