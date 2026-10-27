# Evaluating Generation with a Specific Model

These tools do not call a model API: send each prompt to the chosen model and
save its answer yourself. The scripts compile saved answers and check them
against tests. They provide measurement infrastructure, but **contain no
measured neural-model results yet**.

**Safety:** executing C++ and native binaries is unsafe. The evaluator is not
a sandbox; `--timeout` does not restrict files, network, memory or child
processes. Run untrusted answers only in an isolated environment with
operating-system resource limits.

## Example

1. Generate prompts for the same task:

```bash
python3 eval/prompt.py --task gcd --language nava > nava_prompt.txt
python3 eval/prompt.py --task gcd --language cpp  > cpp_prompt.txt
```

2. Send each complete prompt to the same model using the same settings. Save
   **only the source code produced by the model** as `answer.nv` and
   `answer.cpp`. Keep the full raw response, prompt, parameters and API usage
   statistics separately.
3. Evaluate the saved answers:

```bash
python3 eval/evaluate.py --task gcd --language nava --source answer.nv
python3 eval/evaluate.py --task gcd --language cpp --source answer.cpp
```

The evaluator emits JSON containing compilation status, the result of each
test, compile time, individual execution times and source size. Execution
includes process startup; it is not generation time. A compilation or test
failure produces exit status 1.

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
5. The `gcd`, `sum_to_n`, `factorial` and `fibonacci` tasks and their tests
   are public in `tasks.json`. They are not a hidden independent benchmark.
   Reliable conclusions need more varied tasks and separate hidden tests.

A shorter source file alone does not establish that an unfamiliar language is
easier for a model or yields a correct answer sooner.
