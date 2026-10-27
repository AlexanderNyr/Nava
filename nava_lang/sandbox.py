"""Isolated compilation and execution of untrusted programs (PLAN.md phase N2).

Two backends are implemented and one is deliberately absent:

``namespaces``
    Linux user, mount, PID, network, IPC and UTS namespaces via ``unshare``.
    The program sees a minimal filesystem (a read-only ``/usr``, a handful of
    ``/etc`` files, fresh ``/dev`` nodes, a private ``/tmp`` and one writable
    working directory), no host home directories, only its own processes, and
    no network at all. This is the only backend that isolates the filesystem
    and the network, so it is the only one that can satisfy
    ``mode="required"``.

``rlimits``
    Process group, ``RLIMIT_CPU``, ``RLIMIT_AS``, ``RLIMIT_FSIZE``,
    ``RLIMIT_NOFILE``, ``RLIMIT_CORE`` and bounded output capture, with the
    whole group killed on timeout. **Filesystem, network and process-tree
    isolation are not provided by this backend**, and every report says so in
    plain words. It is what remains on macOS, on a kernel without unprivileged
    user namespaces, or in a container that forbids them.

``mode`` selects the policy:

``off``
    Use ``rlimits`` even if namespaces are available: an explicit opt-in to the
    unsafe local mode. Nothing about isolation is claimed, and every report
    carries the gaps in plain words.
``auto`` (default)
    Use ``namespaces`` when available, otherwise ``rlimits`` with a warning on
    stderr. Never silently claims isolation.
``required``
    Use ``namespaces`` or refuse to run: `SandboxUnavailable` is raised and the
    CLI exits with status 2. A missing backend is never reported as protection.

Limitations of the namespace backend, stated rather than hidden: it protects
against a program that misbehaves, not against a kernel exploit; ``unshare``
itself must be present; the host kernel must allow unprivileged user
namespaces; the read-only ``/usr`` is shared with the host, so anything
world-writable inside it is shared too; and CPU, memory and output limits are
cooperative rlimits enforced by the kernel, not a scheduler quota.
"""
from __future__ import annotations

import errno
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

from . import limits
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional, Sequence

MODES = limits.ISOLATION_MODES
BACKENDS = ("namespaces", "rlimits")

#: Isolation label reported for each backend, in plain words.
ISOLATION_LABELS = {
    "namespaces": "user, mount, PID, network, IPC and UTS namespaces with a read-only minimal root",
    "rlimits": "rlimits and a killed process group only: the filesystem, the network and "
               "the process tree are NOT isolated (this is not a sandbox)",
}

GAP_NOTES = {
    "namespaces": [
        "protects against a misbehaving program, not against a kernel exploit",
        "the read-only /usr is shared with the host",
    ],
    "rlimits": [
        "no filesystem isolation: the program can read and write anything this user can",
        "no network isolation: outbound connections are allowed",
        "no PID namespace: forked children are killed by process group, not by the kernel",
    ],
}


class SandboxUnavailable(RuntimeError):
    """Raised when ``mode="required"`` cannot be satisfied."""


@dataclass(frozen=True)
class Limits:
    """Resource limits applied to every sandboxed process."""

    wall_seconds: Optional[float] = 5.0
    cpu_seconds: int = 5
    memory_bytes: int = 256 * 1024 * 1024
    output_bytes: int = 64 * 1024
    processes: int = 16
    file_bytes: int = 1024 * 1024
    open_files: int = 256

    def validate(self) -> "Limits":
        if self.wall_seconds is not None and not (0 < self.wall_seconds < float("inf")):
            raise ValueError("wall_seconds must be positive and finite, or None for no limit")
        for name in ("cpu_seconds", "memory_bytes", "output_bytes", "processes",
                     "file_bytes", "open_files"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        return self


@dataclass
class Outcome:
    """Result of one sandboxed run, including what the backend did *not* do."""

    command: list[str]
    backend: Optional[str]
    mode: str
    isolation: str
    network_denied: bool
    host_files_hidden: bool
    exit_code: Optional[int] = None
    signal: Optional[int] = None
    timed_out: bool = False
    cpu_limit_hit: bool = False
    output_limit_exceeded: bool = False
    survivors: bool = False
    duration_ms: float = 0.0
    stdout: str = ""
    stderr: str = ""
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (self.exit_code == 0 and not self.timed_out
                and not self.output_limit_exceeded and not self.survivors)

    def report(self) -> dict:
        document = asdict(self)
        document["ok"] = self.ok
        document["isolation_label"] = ISOLATION_LABELS.get(self.backend or "", "none")
        document["gaps"] = GAP_NOTES.get(self.backend or "", ["no isolation at all"])
        return document

    def failure_reason(self) -> Optional[str]:
        if self.timed_out:
            return f"wall-clock limit exceeded after {self.duration_ms / 1000:.1f}s"
        if self.output_limit_exceeded:
            return "output limit exceeded"
        if self.cpu_limit_hit:
            return f"CPU limit reached (signal {self.signal})"
        if self.survivors:
            return "child processes survived termination"
        if self.signal:
            return f"terminated by signal {self.signal}"
        if self.exit_code not in (0, None):
            return f"exit status {self.exit_code}"
        return None


# --------------------------------------------------------------------------
# Backend detection
# --------------------------------------------------------------------------

_probe_cache: dict[str, bool] = {}


def namespaces_available() -> bool:
    """True when a real namespace backend can be used on this host."""
    if "namespaces" in _probe_cache:
        return _probe_cache["namespaces"]
    available = False
    if sys.platform.startswith("linux") and shutil.which("unshare"):
        try:
            probe = subprocess.run(
                ["unshare", "--user", "--map-root-user", "--net", "--pid", "--fork",
                 "--mount", "--", "true"],
                capture_output=True, timeout=30,
            )
            available = probe.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            available = False
    _probe_cache["namespaces"] = available
    return available


def select_backend(mode: str) -> str:
    """Map a mode to a backend name, or raise `SandboxUnavailable`."""
    if mode not in MODES:
        raise ValueError(f"unknown isolation mode {mode!r}; use one of {', '.join(MODES)}")
    if mode == "off":
        return "rlimits"
    if namespaces_available():
        return "namespaces"
    if mode == "required":
        raise SandboxUnavailable(
            "isolation mode 'required' needs Linux namespaces via `unshare` "
            "(user, mount, PID and network namespaces); this host does not provide them. "
            "Refusing to run instead of falling back to an unsandboxed mode."
        )
    return "rlimits"


def describe_host() -> dict:
    return {
        "platform": sys.platform,
        "unshare": shutil.which("unshare"),
        "namespaces_available": namespaces_available(),
        "user_namespaces_max": _read_sysctl("/proc/sys/user/max_user_namespaces"),
        "backends": {"namespaces": namespaces_available(), "rlimits": True},
    }


def _read_sysctl(path: str) -> Optional[str]:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None


# --------------------------------------------------------------------------
# Child-side setup
# --------------------------------------------------------------------------

#: Runs *inside* the new namespaces. Builds the minimal root, chroots and execs
#: the target. It is written to the sandbox directory so it is part of the one
#: writable mount the program can see; it must stay dependency-free.
_JAIL_SETUP = r'''"""Nava sandbox setup: build a minimal root inside the namespaces, then exec."""
import os
import sys


def mount(*args):
    result = os.spawnvp(os.P_WAIT, "mount", ["mount", *args])
    if result != 0:
        sys.stderr.write("nava sandbox: mount failed: %s\n" % " ".join(args))
        sys.exit(90)


ROOT_TMPFS = "size=8m,nosuid,nodev,mode=755"


def main():
    root, work, target = sys.argv[1], sys.argv[2], sys.argv[3:]
    os.makedirs(root, exist_ok=True)
    # The root lives on a private tmpfs: nothing the program writes can reach
    # the host filesystem, and the tmpfs is frozen read-only at the end.
    mount("-t", "tmpfs", "-o", ROOT_TMPFS, "tmpfs", root)
    for name in ("usr", "etc", "dev", "proc", "tmp", "work"):
        os.makedirs(os.path.join(root, name), exist_ok=True)
    # The only system tree the program may see, mounted read-only.
    mount("--bind", "/usr", root + "/usr")
    mount("-o", "remount,bind,ro,nodev,nosuid", "/usr", root + "/usr")
    for link, destination in (("lib", "usr/lib"), ("lib64", "usr/lib64"),
                              ("bin", "usr/bin"), ("sbin", "usr/sbin")):
        path = root + "/" + link
        if not os.path.lexists(path) and os.path.exists("/" + destination):
            os.symlink(destination, path)
    for name in ("ld.so.cache", "ld.so.conf", "nsswitch.conf", "passwd", "group",
                 "localtime", "hosts", "resolv.conf"):
        source = "/etc/" + name
        if not os.path.exists(source):
            continue
        destination = root + "/etc/" + name
        open(destination, "w").close()
        mount("--bind", source, destination)
        mount("-o", "remount,bind,ro", source, destination)
    # Debian-style alternatives: /usr/bin/cc is a symlink chain through /etc.
    if os.path.isdir("/etc/alternatives"):
        alternatives = root + "/etc/alternatives"
        os.makedirs(alternatives, exist_ok=True)
        mount("--bind", "/etc/alternatives", alternatives)
        mount("-o", "remount,bind,ro", "/etc/alternatives", alternatives)
    for device in ("null", "zero", "random", "urandom"):
        destination = root + "/dev/" + device
        open(destination, "w").close()
        mount("--bind", "/dev/" + device, destination)
    mount("-t", "tmpfs", "-o", "size=16m,nosuid,nodev", "tmpfs", root + "/tmp")
    mount("--bind", work, root + "/work")
    mount("-t", "proc", "proc", root + "/proc")
    # Freeze the root in place. A bind mount here would stack a new mount and
    # hide the submounts above, so the tmpfs is remounted instead; the size and
    # mode must be repeated or the kernel rejects the remount.
    mount("-o", "remount,ro," + ROOT_TMPFS, "tmpfs", root)
    os.chroot(root)
    os.chdir("/work")
    environment = {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "TMPDIR": "/tmp",
                   "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                   "NAVA_SANDBOX_WORKDIR": "/work", "NAVA_SANDBOX_BACKEND": "namespaces"}
    try:
        os.execvpe(target[0], target, environment)
    except OSError as error:
        sys.stderr.write("nava sandbox: cannot execute %s: %s\n" % (target[0], error))
        sys.exit(91)


if __name__ == "__main__":
    main()
'''


def _process_count() -> int:
    """Processes owned by this user, used to give RLIMIT_NPROC some headroom."""
    uid = os.getuid()
    count = 0
    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/status", encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        if line.startswith("Uid:"):
                            if str(uid) in line.split()[1:]:
                                count += 1
                            break
            except OSError:
                continue
    except OSError:
        return 0
    return count


def _apply_limits(limits: Limits) -> None:
    """preexec_fn: apply rlimits in the child before exec."""
    try:
        import resource
    except ImportError:  # pragma: no cover - non-POSIX
        return
    limits = limits.validate()
    resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds))
    resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
    resource.setrlimit(resource.RLIMIT_FSIZE, (limits.file_bytes, limits.file_bytes))
    resource.setrlimit(resource.RLIMIT_NOFILE, (limits.open_files, limits.open_files))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # RLIMIT_NPROC counts processes of the real user, so add the current count:
    # otherwise a low limit would break unrelated processes of the same user.
    nproc = max(limits.processes, _process_count() + limits.processes)
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (nproc, nproc))
    except (ValueError, OSError):
        pass


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------

class _BoundedReader(threading.Thread):
    """Reads a pipe, keeps at most `limit` bytes and reports overflow once."""

    def __init__(self, stream, limit: int):
        super().__init__(daemon=True)
        self.stream = stream
        self.limit = limit
        self.buffer = bytearray()
        self.total = 0
        self.overflowed = False

    def run(self) -> None:
        while True:
            # Read at most one byte past the limit so the total is meaningful.
            want = max(1, min(8192, self.limit + 1 - self.total))
            try:
                chunk = self.stream.read(want)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            self.total += len(chunk)
            room = self.limit - len(self.buffer)
            if room > 0:
                self.buffer.extend(chunk[:room])
            if self.total > self.limit and not self.overflowed:
                self.overflowed = True
                break

    def text(self) -> str:
        return bytes(self.buffer).decode("utf-8", "replace")


def _kill_group(process: subprocess.Popen) -> None:
    for sig in (signal.SIGKILL,):
        try:
            os.killpg(os.getpgid(process.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                process.kill()
            except OSError:
                pass


def _group_alive(process: subprocess.Popen) -> bool:
    try:
        os.killpg(os.getpgid(process.pid), 0)
    except (ProcessLookupError, PermissionError):
        return False
    except OSError as error:
        return error.errno != errno.ESRCH
    return True


def run(argv: Sequence[str], *, limits: Optional[Limits] = None, mode: str = "auto",
        stdin: bytes = b"", workdir: Optional[Path] = None,
        capture: bool = True) -> Outcome:
    """Run `argv` under the selected backend and return an `Outcome`.

    `workdir` is the only writable directory the program sees under the
    namespace backend. When it is omitted a temporary directory is created and
    removed afterwards; the caller is responsible for placing the program there
    when the backend hides the rest of the filesystem.

    With `capture=False` the program inherits this process's standard streams,
    so interactive use keeps working; the output limit then does not apply.
    """
    limits = (limits or Limits()).validate()
    backend = select_backend(mode)
    outcome = Outcome(
        command=list(argv),
        backend=backend,
        mode=mode,
        isolation=backend,
        network_denied=backend == "namespaces",
        host_files_hidden=backend == "namespaces",
    )
    if backend == "rlimits" and mode == "auto":
        outcome.notes.append(
            "namespace isolation is unavailable on this host: running with rlimits only "
            "(filesystem, network and process tree are NOT isolated)"
        )

    temporary = None
    if workdir is None:
        temporary = tempfile.TemporaryDirectory(prefix="nava-sandbox-")
        workdir = Path(temporary.name)
    workdir = Path(workdir)
    command = list(argv)

    if backend == "namespaces":
        program = Path(command[0])
        if program.is_absolute() and not _visible_inside_jail(program, workdir):
            raise ValueError(
                f"the program {program} is neither inside the sandbox work directory "
                f"{workdir} nor part of the read-only system tree that the jail exposes; "
                "copy it into the work directory first"
            )
        setup = workdir / ".nava-sandbox-setup.py"
        setup.write_text(_JAIL_SETUP, encoding="utf-8")
        root = workdir / ".nava-jail"
        # The program must live inside the single writable mount.
        command = _move_into_workdir(command, workdir)
        command = [
            "unshare", "--user", "--map-root-user", "--mount", "--pid", "--fork",
            "--net", "--ipc", "--uts", "--kill-child",
            sys.executable, str(setup), str(root), str(workdir), *command,
        ]

    start = time.perf_counter()
    if capture:
        streams = {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
    else:
        streams = {"stdin": None, "stdout": None, "stderr": None}
    try:
        process = subprocess.Popen(
            command,
            **streams,
            start_new_session=True,
            preexec_fn=lambda: _apply_limits(limits),
            cwd=str(workdir) if backend == "rlimits" else None,
            env=_clean_environment(workdir, backend),
        )
    except FileNotFoundError as error:
        if temporary is not None:
            temporary.cleanup()
        raise SandboxUnavailable(f"cannot start the sandbox: {error}") from error

    readers = {}
    if capture:
        readers = {
            "stdout": _BoundedReader(process.stdout, limits.output_bytes),
            "stderr": _BoundedReader(process.stderr, limits.output_bytes),
        }
        for reader in readers.values():
            reader.start()
        try:
            process.stdin.write(stdin)
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass

    deadline = start + limits.wall_seconds if limits.wall_seconds is not None else float("inf")
    while True:
        if process.poll() is not None:
            break
        if any(reader.overflowed for reader in readers.values()):
            outcome.output_limit_exceeded = True
            _kill_group(process)
            break
        if time.perf_counter() > deadline:
            outcome.timed_out = True
            _kill_group(process)
            break
        time.sleep(0.01)

    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        _kill_group(process)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    if _group_alive(process) or _group_has_members(process.pid):
        _kill_group(process)
        time.sleep(0.05)
        outcome.survivors = _group_has_members(process.pid)

    for reader in readers.values():
        reader.join(timeout=5)

    outcome.duration_ms = round((time.perf_counter() - start) * 1000, 3)
    if capture:
        outcome.stdout = readers["stdout"].text()
        outcome.stderr = readers["stderr"].text()
        outcome.stdout_bytes = readers["stdout"].total
        outcome.stderr_bytes = readers["stderr"].total
        outcome.stdout_truncated = readers["stdout"].total > limits.output_bytes
        outcome.stderr_truncated = readers["stderr"].total > limits.output_bytes
    if process.returncode is not None:
        if process.returncode < 0:
            outcome.signal = -process.returncode
            # RLIMIT_CPU shows up as SIGKILL (or SIGXCPU) without a wall timeout.
            outcome.cpu_limit_hit = (outcome.signal in (signal.SIGKILL, signal.SIGXCPU)
                                     and not outcome.timed_out)
        else:
            outcome.exit_code = process.returncode
    if temporary is not None:
        temporary.cleanup()
    return outcome


#: Host trees that the namespace backend exposes read-only inside the jail.
_JAIL_SYSTEM_ROOTS = ("/usr/", "/bin/", "/sbin/", "/lib/", "/lib64/", "/etc/")


def _visible_inside_jail(program: Path, workdir: Path) -> bool:
    """True when the program is reachable inside the jail, or lives in the workdir."""
    try:
        resolved = program.resolve()
    except OSError:
        return False
    if str(resolved).startswith(_JAIL_SYSTEM_ROOTS):
        return True
    try:
        resolved.relative_to(workdir.resolve())
    except (OSError, ValueError):
        return False
    return True


def _clean_environment(workdir: Path, backend: str) -> dict:
    """A minimal environment: no model credentials, no host secrets.

    ``NAVA_SANDBOX_WORKDIR`` tells the program where it may write, whichever
    backend is active: ``/work`` inside the jail, the real path under rlimits.
    """
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "NAVA_SANDBOX_WORKDIR": "/work" if backend == "namespaces" else str(workdir),
        "NAVA_SANDBOX_BACKEND": backend,
    }


def _group_has_members(pgid: int) -> bool:
    """Scan /proc for processes whose process group equals `pgid`."""
    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/stat", "rb") as handle:
                    fields = handle.read().rsplit(b")", 1)[-1].split()
                if len(fields) > 2 and int(fields[2]) == pgid:
                    return True
            except (OSError, ValueError, IndexError):
                continue
    except OSError:
        return False
    return False


def _move_into_workdir(command: list[str], workdir: Path) -> list[str]:
    """Rewrite paths of files that must be visible inside the jail.

    Only the first argument (the program) and any argument that is an existing
    file inside `workdir` are rewritten; everything else is passed through.
    """
    rewritten = []
    for index, argument in enumerate(command):
        if index == 0 and not argument.startswith("/"):
            rewritten.append(argument)
            continue
        path = Path(argument)
        try:
            absolute = path.resolve()
            relative = absolute.relative_to(workdir.resolve())
        except (OSError, ValueError):
            rewritten.append(argument)
            continue
        rewritten.append("/work/" + relative.as_posix() if relative.as_posix() != "." else "/work")
    return rewritten


def summarize(outcome: Outcome) -> str:
    """One-line human summary used by the CLI and the evaluator."""
    parts = [f"backend={outcome.backend or 'none'}",
             f"duration={outcome.duration_ms:.0f}ms"]
    reason = outcome.failure_reason()
    parts.append(f"result={reason}" if reason else "result=ok")
    if outcome.stdout_truncated or outcome.stderr_truncated:
        parts.append("output=truncated")
    if not outcome.network_denied:
        parts.append("network=allowed")
    return " ".join(parts)
