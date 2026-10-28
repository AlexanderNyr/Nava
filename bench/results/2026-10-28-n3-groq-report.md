# Paired study on a hosted model (Groq), 2026-10-28

Second model-specific report for PLAN.md N3. The first
(`2026-10-27-n3-report.md`) served a 135M model locally and found a floor: no
language was winnable. This one calls a hosted 27B model through an
OpenAI-compatible endpoint, and for the first time the three languages can be
compared on a task set where all of them mostly succeed.

**It is still exploratory, and for a different reason than before.** Not because
the model is too weak, but because the provider's free tier caps the daily token
budget at 200,000 per model. Six attempts per task and language is what fits;
the plan asks for thirty.

| | |
|---|---|
| Model | `qwen/qwen3.8-27b` on Groq (`api.groq.com/openai/v1`) |
| Tasks | 10 — 6 public + 4 held out (73 test cases), each file pinned by SHA-256 in `plan.task_files` |
| Languages | `nava`, `c`, `cpp`, guide included, same instruction |
| Settings | temperature 0.2, `max_tokens` 500, no system message, **no repair rounds** (budget), `--isolation auto` → namespaces |
| Attempts | 6 per task and language — 180 attempts, 180 model calls |
| Cost | 138,078 tokens (110,802 prompt + 27,276 completion), 26.2 min wall clock |
| Measured? | `model_measurements.status: measured`, 180 evaluated attempts, 0 provider errors |
| Record | `bench/results/2026-10-28-n3-groq-qwen3-27b.json` — `check_study_record.py --require measured` → OK |
| Samples | `bench/results/2026-10-28-n3-groq-samples-qwen3-27b.json` (same settings, 3 tasks, raw text) |

## Result

| Language | pass@1 (95% CI) | compiled first try | median wall, all calls | median wall, unthrottled calls | median prompt tokens | median completion tokens |
|---|---|---|---|---|---|---|
| Nava | **0.90** [0.82, 0.97] | 1.00 | 5,484 ms | 474 ms (19 calls) | 1,602 | 119 |
| C | **0.90** [0.82, 0.97] | 1.00 | 5,581 ms | 438 ms (17 calls) | 118 | 178 |
| C++ | **0.87** [0.77, 0.95] | 0.97 | 10,391 ms | 327 ms (5 calls) | 114 | 124 |
| all | 0.89 [0.84, 0.93] | 0.99 | 6,524 ms | 440 ms (41 calls) | 132 | 133 |

**The intervals overlap almost completely: at this scale the three languages are
indistinguishable.** 54 of 60 Nava attempts passed, 54 of 60 C, 52 of 60 C++.

Two caveats that belong next to those numbers:

* **Wall time here is not model latency.** 139 of 180 attempts were rate-limited
  and retried; the recorded `wall_ms` includes every `retry-after` wait. The
  "unthrottled" column is the median over the 41 calls that were answered on the
  first request — that is the comparable figure, and it still contains network
  and queueing time that the local-model run did not have.
* **The prompt cost is lopsided and measured, not estimated.** Nava carries a
  1,602-token prompt against 114–118 for C/C++ — the guide. It does not show up
  as slower end-to-end here because the answer is short and the API is fast, but
  it is 14× the input tokens per attempt.

### Per task

| Task | Held out | Cases | Nava | C | C++ |
|---|---|---|---|---|---|
| gcd | no | 7 | 1.00 | 1.00 | 0.67 |
| factorial | no | 6 | 1.00 | 1.00 | 1.00 |
| collatz_steps | no | 6 | 1.00 | 1.00 | 1.00 |
| sort_numbers | no | 8 | **0.50** | 1.00 | 1.00 |
| sieve_count | no | 8 | **0.50** | 1.00 | 1.00 |
| mean_abs | no | 11 | **1.00** | **0.00** | **0.00** |
| lcm_two | yes | 6 | 1.00 | 1.00 | 1.00 |
| modexp | yes | 8 | 1.00 | 1.00 | 1.00 |
| count_odd_digits | yes | 6 | 1.00 | 1.00 | 1.00 |
| triangle_kind | yes | 7 | 1.00 | 1.00 | 1.00 |

The four held-out tasks were solved perfectly in all three languages. The
difference between the languages lives in exactly three tasks, and the failure
modes are different in kind:

* **`mean_abs` — Nava 1.00, C 0.00, C++ 0.00.** Not an algorithmic failure: the
  C and C++ answers compute the right mean and then print it with `printf("%.1f")`
  (7 of 11 cases pass) or with `std::cout`'s default six significant digits (3 of
  11). The task's expected output uses shortest round-trip formatting, including
  denormal inputs like `1e-320` whose mean is `5e-321`; Nava's `print(f64)`
  produces that by definition, while in C and C++ the model has to know a
  formatting convention the task never spells out. Saved samples:
  `…-samples-qwen3-27b.json`.
* **`sort_numbers` and `sieve_count` — Nava 0.50, C/C++ 1.00.** These are the
  array tasks. Half of the Nava attempts failed, and the failures are in the
  array machinery (bounds and `array_zeros(n)` sizing) rather than in the
  algorithm; the same model wrote correct sieve and insertion-sort logic in C and
  C++ in every attempt.
* **`gcd` — C++ 0.67.** Two of six C++ answers did not compile.

That split is the honest summary: **Nava's advantage, where it exists, is that
the language defines how a value is printed; its disadvantage, where it exists,
is the array API.** Neither shows up as a difference in aggregate pass rate,
because the tasks are few and the model is good.

## The limit that made this exploratory

Groq's on-demand tier, measured on this key (headers and 429 bodies, quoted):

| Limit | Value | What it means here |
|---|---|---|
| Requests per day | 1,000 | 900 attempts would fit with 100 to spare |
| Tokens per minute | 8,000 (input+output); 1,000 output tokens/minute | 139 of 180 attempts needed a retry |
| **Tokens per day, per model** | **200,000** | **the binding constraint** |

An empirical N3 run — 10 tasks × 3 languages × 30 attempts — costs roughly
1.0 M tokens, of which about 480 k is Nava's guide alone. That is **five days of
this key's daily budget per model**, or one day on a paid tier. Two runs on
2026-10-28 demonstrate the wall rather than describe it:

| Run | Outcome |
|---|---|
| `openai/gpt-oss-120b`, 900 attempts planned | stopped at 240 attempts / 198,689 of 200,000 tokens: `429 … on tokens per day (TPD): Limit 200000, Used 198689` |
| `openai/gpt-oss-20b`, 180 attempts | 163 attempts evaluated, 16 hit the daily cap and 1 was rejected with `400 … Tool choice is none, but model called a tool`; the harness therefore marked the run **`not-measured`**, printed `result: NOT MEASURED`, and exited 1 — its aggregates are **not** quoted here, by its own rule |
| `qwen/qwen3.8-27b`, 180 attempts | completed inside the budget, `measured`, quoted above |

The second row is a live negative control: the harness refused to turn an
incomplete run into a headline number, which is exactly what PLAN.md N3 asks of
it.

## What this run establishes, and what it does not

**It establishes** that the pipeline works against a hosted provider as well as a
local one: 180 calls, real token accounting from the API, compile and test
results from the isolation boundary, bootstrap intervals, and a record a separate
validator re-derives and accepts. It also gives the first comparison in which all
three languages are winnable — and there the aggregate difference is nil.

**It does not establish** that Nava is faster or slower than C or C++ for a
model. 6 attempts per task and language is a fifth of the plan's threshold, the
task set is small, and the differences that do exist are concentrated in two
distinct mechanisms (printing, arrays) that a larger task set would need to
separate from noise.

**It is not empirical.** To reach 30 attempts with this provider: run the same
command on five consecutive days with `--attempts 6` and a different `--out`, or
use a paid tier and run `--attempts 30` once. The tier arithmetic above is the
reason, and it is measured, not assumed.

## Reproducing it

```bash
export GROQ_API_KEY=...            # never written to a record, a log or the tree

python3 eval/study.py --provider openai --model qwen/qwen3.8-27b \
  --base-url https://api.groq.com/openai/v1 --api-key-env GROQ_API_KEY \
  --held-out /path/outside/the/repo/nava-N3-heldout.json \
  --task gcd --task factorial --task collatz_steps --task sort_numbers \
  --task sieve_count --task mean_abs --task lcm_two --task modexp \
  --task count_odd_digits --task triangle_kind \
  --languages nava,c,cpp --attempts 6 --repair-rounds 0 \
  --temperature 0.2 --max-tokens 500 --isolation auto \
  --out bench/results/<date>-n3-groq-qwen3-27b.json

python3 eval/check_study_record.py bench/results/<date>-n3-groq-qwen3-27b.json --require measured
```

Two client behaviours matter for a rate-limited gateway, and both are recorded
rather than trusted:

* the client sends `User-Agent: nava-study/1.0` — Groq answers the default
  `Python-urllib/3.13` with HTTP 403 and Cloudflare's `error code: 1010`;
* a 429 is retried after the delay the server asks for, and every round records
  `http_attempts`; a daily cap (`DailyQuotaExceeded`) and a request larger than a
  whole per-minute budget (`RequestTooLarge`) are reported at once instead of
  being retried for an hour.
