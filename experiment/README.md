# Experiment: Writing GCD in Nava and C

Task: read two non-negative integers from 0 to 1,000,000,000 and print their
greatest common divisor; `0 0` returns `0`. The sources are
[`gcd.nv`](gcd.nv) and [`gcd.c`](gcd.c).

## One-attempt result

| Language | Time to write after task selection and preliminary planning | Source size |
|---|---:|---:|
| Nava | 5.944 s | 148 bytes / characters |
| C | 5.762 s | 291 bytes / characters |

Nava was written first and C second. In this measurement C finished 0.182 s
sooner, while the Nava source was about 49% shorter in characters.

**Limitations:** this is not pure model-generation time. The stopwatch was
started after preliminary planning and included sending the source to a
file-writing tool and tool-call latency. Each program was written only once;
the order was not alternated. A 0.182 s difference under these conditions is
not enough to establish a speed advantage. Characters and bytes are not model
tokens; the actual model tokenizer and usage counters were not measured in
this session. These times are not program-execution times either.

`timing.txt` contains four successive monotonic timestamps in nanoseconds:
the start and end of the Nava write, followed by the start and end of the C
write.

## Correctness check

Both programs were built at `-O2` and checked on 107 identical inputs: seven
edge cases and 100 pseudorandom cases. Each result was compared to Python's
`math.gcd`. All 107 cases passed.

To repeat the check (Python 3.10+ and a C compiler are required):

```bash
cd nava
python3 experiment/verify.py
```

A reliable generation-speed comparison requires separate independent requests
to the same model, API timing and token counters, many repetitions, alternating
order and correctness checks. The comparison must include all input prompt
tokens.
