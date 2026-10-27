# Evaluating Generation with a Specific Model

These tools do not call a model API: send each prompt to the chosen model and
save its answer yourself. The scripts compile saved answers and check them
against tests. They provide measurement infrastructure, but **contain no
measured neural-model results yet**.

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
   alternate the order to reduce systematic bias.
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
