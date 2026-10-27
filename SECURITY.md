# Running code you do not trust

Nava executes two kinds of code that the project does not control: the C or C++
source produced by a model, and the native binary compiled from it. This
document states what `nava_lang/sandbox.py` does about that, what it does not
do, and how the claim is tested.

The short version: **there is no sandbox on this project's behalf unless the
Linux namespace backend is active, and every report says which backend ran.**

## Backends

| Backend | Selected when | Filesystem | Network | Process tree | CPU / memory / output |
|---|---|---|---|---|---|
| `namespaces` | Linux with working unprivileged user namespaces and `unshare` | minimal read-only root, one writable directory | denied (own network namespace) | own PID namespace, whole group killed | rlimits + wall clock + byte caps |
| `rlimits` | everywhere else, or `--isolation off` | **not isolated** | **not isolated** | killed by process group only | rlimits + wall clock + byte caps |

`--isolation` (`nava run`, `eval/evaluate.py`):

- `auto` (default) — use `namespaces` when available, otherwise `rlimits` and a
  warning on stderr. The report records the gap.
- `required` — use `namespaces` or refuse to run. Exit status 2 with `E0601`;
  the evaluator prints `"isolation": {"available": false}` and never a passing
  summary.
- `off` — explicitly opt in to `rlimits` only. Every report carries
  "this is not a sandbox" and the list of missing protections.

## What the namespace backend does

Before the program starts, `unshare` creates user, mount, PID, network, IPC and
UTS namespaces, and a setup step inside them builds the only filesystem the
program will see:

- `/usr` bind-mounted **read-only** (this is where compilers and libraries live);
- `/lib`, `/lib64`, `/bin`, `/sbin` as symlinks into `/usr`, plus
  `/etc/alternatives` for distribution symlink chains;
- a few `/etc` files (`ld.so.cache`, `passwd`, `group`, `nsswitch.conf`,
  `localtime`, `hosts`) bind-mounted read-only;
- `/dev/null`, `/dev/zero`, `/dev/random`, `/dev/urandom` only;
- a private 16 MiB `tmpfs` at `/tmp`;
- **one** writable directory, bind-mounted at `/work`, also reported to the
  program as `NAVA_SANDBOX_WORKDIR`;
- a fresh `/proc` for the private PID namespace.

The root is a private `tmpfs` that is remounted read-only at the end, so nothing
written by the program reaches the host. After `chroot` the program is exec'd
with a minimal environment (`PATH`, `HOME=/tmp`, `TMPDIR=/tmp`, `LANG`), so
credentials in the parent environment are not visible.

Limits applied by the parent process through `RLIMIT_*`: CPU seconds,
address space, output file size, open files, core dumps and process count.
The parent also enforces a wall-clock deadline and a per-stream byte cap; when
either is exceeded the **whole process group** is killed and, if anything is
still alive, killed again and reported as `survivors: true`.

## What it does not do

- It is not protection against a **kernel exploit**, a container escape or a
  vulnerability in `unshare`. It confines a misbehaving program, not a hostile
  kernel.
- `/usr` is shared read-only with the host. Anything world-writable inside
  `/usr` on the host stays writable inside the jail.
- There is no seccomp filter, no cgroup memory quota (RLIMIT_AS is per-process)
  and no filesystem quota: a program can still fill the 16 MiB `/tmp` and its
  working directory up to `RLIMIT_FSIZE`.
- The `rlimits` backend isolates nothing but resource use. On macOS, on kernels
  without unprivileged user namespaces, and in containers that forbid them,
  `--isolation required` refuses to run — but `auto` will run with `rlimits`,
  and that is only as safe as the account running it.
- The Nava front end (parsing, type checking, C emission) runs in the evaluator
  process. It is a pure-Python parser over a text file with its own nesting and
  literal limits; it never executes candidate code. Only native compilation and
  native execution are sandboxed.
- `nava run` streams the program's standard streams to your terminal, so the
  output cap does not apply there; the evaluator captures output and does cap it.

## Platform support

| Platform | `namespaces` | Notes |
|---|---|---|
| Linux with unprivileged user namespaces (tested: 6.1) | yes | the configuration the gate is written against |
| Linux with `kernel.unprivileged_userns_clone=0`, or AppArmor-restricted distributions | no | detected by a probe; `required` refuses |
| macOS, BSD, Windows | no | `rlimits` only; label says so |

Detection is a real probe — `unshare --user --map-root-user --mount --pid
--fork --net -- true` — not a version check, so a host that lies about the
feature is caught.

## How this is tested

`tests/test_sandbox.py` runs deliberately hostile C programs through both
backends:

| Probe | Expected |
|---|---|
| busy loop | stopped by CPU rlimit or the wall clock, promptly |
| sleeping program (no CPU) | stopped by the wall clock, not by the CPU rlimit |
| flood of output | killed, output truncated at the byte cap |
| 64 MiB allocations in a loop | allocation fails inside the program at the address-space limit |
| `fork` + `sleep` child | no process matching the probe survives on the host |
| write to the allowed directory, to the host `/tmp`, to `/usr`, to `/etc` | allowed write succeeds; the rest denied (`denied=3`) under `namespaces`; the host write **succeeds** under `rlimits`, and the test asserts that gap rather than hiding it |
| connect to a local listener | `DENIED` under `namespaces`; `CONNECTED` under `rlimits`, asserted as a documented gap |
| `--isolation required` with `unshare` removed from `PATH` | refuse, exit 2, `E0601`, `"available": false` |

Disabling any one mechanism makes its test fail; this was verified by removing
`--net`, the timeout kill, the fail-closed branch, the read-only minimal root and
the output cap one at a time.

## Reporting a problem

An isolation bypass is a security issue: describe the probe, the kernel, the
distribution and whether `namespaces` was reported as active, and include the
`isolation` block from an evaluator report. Do not attach real credentials,
private prompts or model responses.
