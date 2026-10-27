"""Isolation gate (PLAN.md phase N2).

Each test runs a small C program that misbehaves in a specific way and asserts
what the sandbox does about it. Two rules keep these tests honest:

* when a property is **not** provided by a backend, the test asserts the gap
  instead of pretending. The rlimits backend does not isolate the filesystem or
  the network, and the tests say so;
* `required` mode must refuse to run when namespaces are unavailable, so a
  missing backend can never be reported as protection.

The probes are compiled once per class; a missing C compiler skips the class,
and `tests/registry.py` fails the gate on such a host.
"""
from __future__ import annotations

import json
import os
import shutil
import uuid
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nava_lang import sandbox  # noqa: E402

CC = shutil.which("cc")

PROBES = {
    "allowed": r'''
#include <stdio.h>
int main(void) { printf("allowed program ran\n"); return 0; }
''',
    "loop": r'''
int main(void) { volatile long x = 0; for (;;) x++; return 0; }
''',
    "sleepy": r'''
#include <unistd.h>
int main(void) { for (;;) sleep(60); }
''',
    "output": r'''
#include <stdio.h>
int main(void) {
    for (long i = 0; i < 1000000; i++) printf("line %ld ................................\n", i);
    return 0;
}
''',
    "memory": r'''
#include <stdlib.h>
#include <string.h>
int main(void) {
    for (;;) {
        char *p = malloc(64 * 1024 * 1024);
        if (!p) return 3;
        memset(p, 1, 64 * 1024 * 1024);
    }
}
''',
    "child": r'''
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
int main(void) {
    pid_t child = fork();
    if (child == 0) { sleep(120); return 0; }
    const char *dir = getenv("NAVA_SANDBOX_WORKDIR");
    char path[512];
    snprintf(path, sizeof path, "%s/child.pid", dir ? dir : "/work");
    FILE *f = fopen(path, "w");
    if (f) { fprintf(f, "%d\n", (int)child); fclose(f); }
    for (;;) pause();
}
''',
    "write": r'''
#include <stdio.h>
#include <stdlib.h>
int main(int argc, char **argv) {
    const char *dir = getenv("NAVA_SANDBOX_WORKDIR");
    char allowed[512];
    snprintf(allowed, sizeof allowed, "%s/allowed.txt", dir ? dir : "/work");
    FILE *a = fopen(allowed, "w");
    if (!a) return 4;
    fputs("ok\n", a);
    fclose(a);
    int denied = 0;
    FILE *host = fopen(argv[1], "w");          /* outside the allowed directory */
    if (host) { fputs("escaped\n", host); fclose(host); } else denied++;
    FILE *usr = fopen("/usr/escape.txt", "w");
    if (usr) { fputs("escaped\n", usr); fclose(usr); } else denied++;
    FILE *etc = fopen("/etc/escape.txt", "w");
    if (etc) { fputs("escaped\n", etc); fclose(etc); } else denied++;
    printf("denied=%d\n", denied);
    return 0;
}
''',
    "network": r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
int main(int argc, char **argv) {
    int port = atoi(argv[1]);
    int s = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in address;
    memset(&address, 0, sizeof address);
    address.sin_family = AF_INET;
    address.sin_port = htons((unsigned short)port);
    address.sin_addr.s_addr = inet_addr("127.0.0.1");
    if (connect(s, (struct sockaddr *)&address, sizeof address) == 0) { puts("CONNECTED"); return 0; }
    puts("DENIED");
    return 7;
}
''',
}


def host_processes_matching(needle: str) -> list[int]:
    """Host PIDs whose command line contains `needle`.

    Inside the PID namespace the probe records a namespace-local PID, which is
    meaningless on the host, so survival is checked by command line instead.
    """
    found = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            command = Path(f"/proc/{entry}/cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", "replace")
        except OSError:
            continue
        if needle in command:
            found.append(int(entry))
    return found


@unittest.skipUnless(CC, "a C compiler is required for the isolation gate")
class SandboxTests(unittest.TestCase):
    backends = ("namespaces", "rlimits") if sandbox.namespaces_available() else ("rlimits",)

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="nava-probes-")
        cls.work = Path(cls.directory.name)
        for name, source in PROBES.items():
            (cls.work / f"{name}.c").write_text(source, encoding="utf-8")
            built = subprocess.run([CC, "-O1", "-w", str(cls.work / f"{name}.c"),
                                    "-o", str(cls.work / name)], capture_output=True, text=True)
            if built.returncode != 0:
                raise unittest.SkipTest(f"cannot build probe {name}: {built.stderr[:200]}")
        cls.port = None
        cls.listener = None
        cls.accepted = []

    @classmethod
    def tearDownClass(cls):
        if cls.listener is not None:
            cls.listener.close()
        cls.directory.cleanup()

    def run_probe(self, name, *, mode, limits=None, arguments=(), workdir=None, tag=None):
        """Copy the probe into the work directory and run it from there.

        The namespace backend only exposes the work directory, so the program
        must live inside it; that is also how the evaluator runs candidates.
        `tag` makes the copy uniquely nameable so survivors can be found on the
        host by command line.
        """
        directory = Path(workdir or self.work)
        program = directory / f"probe-{name}{tag or ''}"
        if not program.exists():
            shutil.copy2(self.work / name, program)
        self.last_program = program
        return sandbox.run([str(program), *arguments],
                           limits=limits or sandbox.Limits(wall_seconds=5), mode=mode,
                           workdir=directory)

    # -- positive control ---------------------------------------------------
    def test_allowed_program_completes(self):
        for mode in ("required", "off"):
            with self.subTest(mode=mode):
                outcome = self.run_probe("allowed", mode=mode)
                self.assertTrue(outcome.ok, outcome.failure_reason())
                self.assertEqual(outcome.stdout, "allowed program ran\n")
                self.assertFalse(outcome.timed_out)

    # -- runaway CPU --------------------------------------------------------
    def test_infinite_loop_is_stopped(self):
        for mode in ("required", "off"):
            with self.subTest(mode=mode):
                outcome = self.run_probe("loop", mode=mode,
                                         limits=sandbox.Limits(wall_seconds=1, cpu_seconds=1))
                self.assertFalse(outcome.ok)
                self.assertTrue(outcome.timed_out or outcome.cpu_limit_hit,
                                outcome.failure_reason())
                self.assertLess(outcome.duration_ms, 5000)

    def test_wall_clock_limit_stops_an_idle_program(self):
        """A sleeping program burns no CPU, so only the wall clock can stop it."""
        for mode in ("required", "off"):
            with self.subTest(mode=mode):
                outcome = self.run_probe("sleepy", mode=mode,
                                         limits=sandbox.Limits(wall_seconds=1, cpu_seconds=60))
                self.assertFalse(outcome.ok)
                self.assertTrue(outcome.timed_out)
                self.assertFalse(outcome.cpu_limit_hit)
                self.assertLess(outcome.duration_ms, 5000, "the idle program was not killed promptly")

    # -- unbounded output ---------------------------------------------------
    def test_excess_output_is_stopped(self):
        for mode in ("required", "off"):
            with self.subTest(mode=mode):
                outcome = self.run_probe("output", mode=mode,
                                         limits=sandbox.Limits(wall_seconds=10, output_bytes=8192))
                self.assertFalse(outcome.ok)
                self.assertTrue(outcome.output_limit_exceeded)
                self.assertLessEqual(len(outcome.stdout), 8192)
                self.assertLess(outcome.duration_ms, 5000)

    # -- memory -------------------------------------------------------------
    def test_memory_ceiling_stops_allocation(self):
        for mode in ("required", "off"):
            with self.subTest(mode=mode):
                outcome = self.run_probe("memory", mode=mode,
                                         limits=sandbox.Limits(wall_seconds=10, cpu_seconds=10,
                                                               memory_bytes=128 * 1024 * 1024))
                self.assertEqual(outcome.exit_code, 3, "the probe must see a failed allocation")
                self.assertLess(outcome.duration_ms, 8000)

    # -- process tree -------------------------------------------------------
    def test_child_processes_do_not_survive(self):
        for mode in ("required", "off"):
            with self.subTest(mode=mode):
                work = Path(tempfile.mkdtemp(dir=self.work))
                tag = "-" + uuid.uuid4().hex[:12]
                outcome = self.run_probe("child", mode=mode, workdir=work, tag=tag,
                                         limits=sandbox.Limits(wall_seconds=1.5, cpu_seconds=5))
                self.assertFalse(outcome.ok)
                self.assertFalse(outcome.survivors, "a child outlived the sandboxed program")
                pid_file = work / "child.pid"
                self.assertTrue(pid_file.exists(), "the probe did not record its child pid")
                namespaced_pid = int(pid_file.read_text(encoding="utf-8").strip())
                self.assertGreater(namespaced_pid, 0)
                survivors = host_processes_matching(f"probe-child{tag}")
                self.assertEqual(survivors, [],
                                 f"forked child still running on the host: {survivors}")

    # -- filesystem ---------------------------------------------------------
    def test_filesystem_boundary(self):
        for mode in ("required", "off"):
            with self.subTest(mode=mode):
                work = Path(tempfile.mkdtemp(dir=self.work))
                # A path outside the work directory: the only place the
                # namespace backend must not reach.
                host_path = Path(tempfile.mkdtemp(prefix="nava-outside-")) / "host-escape.txt"
                outcome = self.run_probe("write", mode=mode, workdir=work, arguments=(str(host_path),))
                self.assertTrue(outcome.ok, outcome.failure_reason())
                self.assertEqual((work / "allowed.txt").read_text(encoding="utf-8"), "ok\n")
                self.assertFalse(Path("/usr/escape.txt").exists())
                self.assertFalse(Path("/etc/escape.txt").exists())
                backend = sandbox.select_backend(mode)
                if backend == "namespaces":
                    # The one writable directory is /work; nothing else is reachable.
                    self.assertEqual(outcome.stdout.strip(), "denied=3")
                    self.assertFalse(host_path.exists(), "a write escaped the sandbox")
                else:
                    # Documented gap: rlimits gives no filesystem isolation at all.
                    self.assertIn("denied=", outcome.stdout)
                    self.assertTrue(host_path.exists(),
                                    "rlimits backend unexpectedly denied a host write; "
                                    "update the documented gap if this changes")

    # -- network ------------------------------------------------------------
    def test_network_boundary(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(5)
        listener.settimeout(0.2)
        accepted = []
        stop = threading.Event()

        def accept_loop():
            while not stop.is_set():
                try:
                    accepted.append(listener.accept())
                except (socket.timeout, OSError):
                    continue

        thread = threading.Thread(target=accept_loop, daemon=True)
        thread.start()
        port = listener.getsockname()[1]
        try:
            for mode in ("required", "off"):
                with self.subTest(mode=mode):
                    outcome = self.run_probe("network", mode=mode, arguments=(str(port),),
                                             limits=sandbox.Limits(wall_seconds=5))
                    backend = sandbox.select_backend(mode)
                    if backend == "namespaces":
                        self.assertEqual(outcome.stdout.strip(), "DENIED")
                        self.assertEqual(outcome.exit_code, 7)
                        self.assertTrue(outcome.network_denied)
                    else:
                        self.assertEqual(outcome.stdout.strip(), "CONNECTED")
                        self.assertFalse(outcome.network_denied,
                                         "rlimits must never claim network isolation")
        finally:
            stop.set()
            thread.join(timeout=2)
            listener.close()
        if sandbox.namespaces_available():
            ns_connections = [connection for connection in accepted]
            # One connection is expected: from the rlimits run, which documents the gap.
            self.assertLessEqual(len(ns_connections), 1,
                                 "the namespace backend allowed a network connection")

    # -- fail-closed --------------------------------------------------------
    def test_required_mode_refuses_without_namespaces(self):
        script = (
            "import sys; sys.path.insert(0, %r)\n"
            "from nava_lang import sandbox\n"
            "print('available', sandbox.namespaces_available())\n"
            "try:\n"
            "    sandbox.select_backend('required')\n"
            "except sandbox.SandboxUnavailable as error:\n"
            "    print('refused', error)\n"
            "    raise SystemExit(3)\n"
            "raise SystemExit(0)\n" % str(ROOT)
        )
        # /bin is a symlink to /usr/bin on usrmerge systems, so use a real
        # directory that contains no `unshare` at all.
        empty = tempfile.mkdtemp(prefix="nava-empty-path-")
        environment = {"PATH": f"{empty}:/usr/local/bin", "LANG": "C.UTF-8"}
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                                env=environment, timeout=120)
        # Without unshare in PATH the probe fails and `required` must refuse.
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("available False", result.stdout)
        self.assertIn("needs Linux namespaces", result.stdout)

    def test_evaluator_refuses_required_without_namespaces(self):
        with tempfile.TemporaryDirectory() as temp:
            empty = tempfile.mkdtemp(prefix="nava-empty-path-")
            environment = {"PATH": f"{empty}:/usr/local/bin", "LANG": "C.UTF-8", "HOME": temp}
            result = subprocess.run(
                [sys.executable, str(ROOT / "eval/evaluate.py"), "--task", "gcd", "--language", "nava",
                 "--source", str(ROOT / "eval/solutions/gcd.nv"), "--isolation", "required"],
                capture_output=True, text=True, env=environment, timeout=180,
            )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["isolation"], {"mode": "required", "backend": None, "available": False})
        self.assertIn("needs Linux namespaces", report["error"])

    # -- honest labels ------------------------------------------------------
    def test_backend_labels_state_what_is_missing(self):
        for backend in sandbox.BACKENDS:
            with self.subTest(backend=backend):
                self.assertIn(backend, sandbox.ISOLATION_LABELS)
                self.assertTrue(sandbox.GAP_NOTES[backend])
        self.assertIn("NOT isolated", sandbox.ISOLATION_LABELS["rlimits"])
        self.assertIn("not a sandbox", sandbox.ISOLATION_LABELS["rlimits"])
        self.assertTrue(any("no network isolation" in note
                            for note in sandbox.GAP_NOTES["rlimits"]))
        outcome = sandbox.Outcome(command=["x"], backend="rlimits", mode="off", isolation="rlimits",
                                  network_denied=False, host_files_hidden=False)
        report = outcome.report()
        self.assertIn("NOT isolated", report["isolation_label"])
        self.assertTrue(report["gaps"])
        self.assertFalse(report["network_denied"])

    def test_report_records_limits_and_truncation(self):
        outcome = self.run_probe("output", mode="off",
                                 limits=sandbox.Limits(wall_seconds=10, output_bytes=4096))
        report = outcome.report()
        self.assertTrue(report["output_limit_exceeded"])
        self.assertTrue(report["stdout_truncated"])
        self.assertEqual(report["backend"], "rlimits")
        self.assertGreaterEqual(report["stdout_bytes"], 4096)
        self.assertLessEqual(report["stdout_bytes"], 4096 + 8192)

    # -- CLI integration ----------------------------------------------------
    def test_cli_run_reports_isolation_and_timeout(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "program.nv"
            source.write_text("fn main() { print(read_i64() * 2) }\n", encoding="utf-8")
            result = subprocess.run([sys.executable, str(ROOT / "nava"), "run", str(source),
                                     "--isolation", "required"], input="21\n",
                                    capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "42\n")
            source.write_text("fn main() { while true {} }\n", encoding="utf-8")
            result = subprocess.run([sys.executable, str(ROOT / "nava"), "run", str(source),
                                     "--timeout", "0.5", "--isolation", "required"],
                                    capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 124)
            self.assertIn("exceeded", result.stderr)

    def test_surviving_child_is_reported_by_the_cli(self):
        """If a child ever outlives the kill, `nava run` must report E0602, not pass."""
        from unittest import mock
        from nava_lang import cli

        outcome = sandbox.Outcome(command=["x"], backend="rlimits", mode="off",
                                  isolation="rlimits", network_denied=False,
                                  host_files_hidden=False, survivors=True)
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "program.nv"
            source.write_text("fn main() { print(1) }\n", encoding="utf-8")
            with mock.patch.object(cli.sandbox, "run", return_value=outcome):
                code = cli.main(["run", str(source), "--isolation", "off"])
        self.assertEqual(code, 2)

    def test_cli_runtime_error_keeps_its_status(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "program.nv"
            source.write_text("fn main() { print(1 / 0) }\n", encoding="utf-8")
            result = subprocess.run([sys.executable, str(ROOT / "nava"), "run", str(source),
                                     "--isolation", "required"], capture_output=True, text=True,
                                    timeout=180)
        self.assertEqual(result.returncode, 1)
        self.assertIn("division by zero", result.stderr)


if __name__ == "__main__":
    unittest.main()
