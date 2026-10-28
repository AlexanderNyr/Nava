"""Capability-limited file access (PLAN.md phase N6).

The gate has four parts:

1. **Deny by default** — a program built without `--allow-read`/`--allow-write`
   cannot touch the filesystem; the refusal is a runtime error, not a silent
   empty result.
2. **Grants are directory-scoped and symbolic-link safe** — a grant covers one
   directory subtree, `..` and a symlink pointing outside it are denied, and the
   check uses the resolved path (`realpath`).
3. **Limits** — one file, one run and one call count are bounded, so a program
   cannot turn a grant into unbounded memory use.
4. **Isolation interaction** — inside the namespace runner the program sees
   only its working directory, so a grant outside it is ineffective; both the
   allowed and the denied path are demonstrated under `--isolation required`.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import grants  # noqa: E402
from nava_lang import sandbox  # noqa: E402

#: Whether this host can run the namespace jail. This is a *probe*, not a
#: selection: `select_backend("required")` raises by design when namespaces are
#: unavailable, and a module-level constant must never be able to fail the
#: import of a whole test module (it did: the registry guard died on the GitHub
#: runner before any test ran).
NAMESPACES = sandbox.namespaces_available()
READ = 'fn main() {\n    print(read_file("data.txt"))\n}\n'


def nava(*arguments, stdin: str = "", timeout: int = 180):
    return subprocess.run([sys.executable, str(ROOT / "nava"), *arguments],
                          input=stdin, capture_output=True, text=True, timeout=timeout)


@unittest.skipUnless(grants.CC, "a C compiler is required for the file tests")
class FileCapabilityTests(unittest.TestCase):
    def test_a_program_without_a_grant_is_denied(self):
        result = grants.run(READ, files={"data.txt": "secret\n"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("read_file denied: no permitted directory was configured",
                      result.stderr)
        self.assertEqual(result.stdout, "")

    def test_a_granted_directory_is_readable(self):
        result = grants.run(READ, files={"data.txt": "hello file"},
                            capabilities={"read": ["."]})
        self.assertEqual(result.returncode, 0, result.stderr)
        # print adds one newline; the file itself ends with none.
        self.assertEqual(result.stdout, "hello file\n")

    def test_a_path_outside_the_grant_is_denied(self):
        result = grants.run('fn main() {\n    print(read_file("../outside.txt"))\n}\n',
                            files={"data.txt": "", "../outside.txt": "nope\n"},
                            capabilities={"read": ["."]})
        self.assertEqual(result.returncode, 1)
        self.assertIn("'../outside.txt' is outside every permitted directory",
                      result.stderr)

    def test_a_symlink_outside_the_grant_is_denied(self):
        result = grants.run(READ, files={
            "data.txt": "inside\n",
            "link.txt": ("link", "../outside.txt"),
            "../outside.txt": "outside\n",
        }, capabilities={"read": ["."]})
        # data.txt is granted, so this run succeeds; the symlink is checked next.
        self.assertEqual(result.returncode, 0, result.stderr)
        escaped = grants.run(
            'fn main() {\n    print(read_file("link.txt"))\n}\n',
            files={"data.txt": "inside\n", "link.txt": ("link", "../outside.txt"),
                   "../outside.txt": "outside\n"},
            capabilities={"read": ["."]})
        self.assertEqual(escaped.returncode, 1)
        self.assertIn("read_file denied: 'link.txt' is outside every permitted directory",
                      escaped.stderr)

    def test_a_missing_file_says_why(self):
        result = grants.run(READ, files={"other.txt": ""},
                            capabilities={"read": ["."]})
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot open 'data.txt'", result.stderr)
        self.assertIn("No such file", result.stderr)

    def test_a_directory_is_not_a_file(self):
        result = grants.run('fn main() {\n    print(read_file("."))\n}\n',
                            capabilities={"read": ["."]})
        self.assertEqual(result.returncode, 1)
        self.assertIn("is not a regular file", result.stderr)

    def test_an_empty_file_is_an_empty_string(self):
        result = grants.run(
            'fn main() {\n    let text = read_file("empty.txt")\n'
            '    print(bytes_len(text))\n    print(chars_len(text))\n}\n',
            files={"empty.txt": ""}, capabilities={"read": ["."]})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "0\n0\n")

    def test_write_needs_its_own_grant(self):
        program = ('fn main() {\n    write_file("out.txt", "written")\n'
                   '    print(read_file("out.txt"))\n}\n')
        denied = grants.run(program, files={"data.txt": ""},
                            capabilities={"read": ["."]})
        self.assertEqual(denied.returncode, 1)
        self.assertIn("write_file denied: no permitted directory was configured",
                      denied.stderr)
        allowed = grants.run(program, files={"data.txt": ""},
                             capabilities={"read": ["."], "write": ["."]})
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertEqual(allowed.stdout, "written\n")

    def test_write_through_a_symlinked_directory_is_denied(self):
        # `link` points at the parent of the working directory, which is a real
        # directory but outside the grant.
        result = grants.run('fn main() {\n    write_file("link/out.txt", "x")\n}\n',
                            files={"data.txt": "", "link": ("link", "..")},
                            capabilities={"write": ["."]})
        self.assertEqual(result.returncode, 1)
        self.assertIn("write_file denied: 'link/out.txt' is outside every permitted "
                      "directory", result.stderr)

    def test_the_per_file_limit_is_enforced(self):
        result = grants.run(READ, files={"data.txt": "x" * (1024 * 1024 + 1)},
                            capabilities={"read": ["."]})
        self.assertEqual(result.returncode, 1)
        self.assertIn("above the limit of 1048576", result.stderr)

    def test_the_total_read_limit_is_enforced(self):
        program = ('fn main() {\n    let index = 0\n    while index < 5 {\n'
                   '        let text = read_file("big.bin")\n'
                   '        print(bytes_len(text))\n        index = index + 1\n    }\n}\n')
        result = grants.run(program, files={"big.bin": "x" * 1024000},
                            capabilities={"read": ["."]})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "1024000\n1024000\n1024000\n1024000\n")
        self.assertIn("read_file: more than 4194304 bytes read in one run", result.stderr)

    def test_the_read_call_limit_is_enforced(self):
        program = ('fn main() {\n    let index = 0\n    while index < 65 {\n'
                   '        let text = read_file("data.txt")\n        index = index + 1\n'
                   '    }\n    print("done")\n}\n')
        result = grants.run(program, files={"data.txt": "x"},
                            capabilities={"read": ["."]})
        self.assertEqual(result.returncode, 1)
        self.assertIn("read_file: more than 64 calls in one run", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_the_grants_are_compiled_in(self):
        granted = grants.emit('fn main() {\n    print(read_file("data.txt"))\n}\n',
                              capabilities={"read": ["data"], "write": ["out"]})
        self.assertIn("#define NV_READ_ROOT_COUNT 1", granted)
        self.assertIn("#define NV_WRITE_ROOT_COUNT 1", granted)
        self.assertIn(grants_octal("data"), granted)
        plain = grants.emit('fn main() { print(1) }\n')
        self.assertIn("#define NV_READ_ROOT_COUNT 0", plain)
        self.assertIn("#define NV_WRITE_ROOT_COUNT 0", plain)

    def test_the_program_cannot_grant_itself_access(self):
        # The grant list is a compile-time constant: nothing in the language can
        # name it, and the runtime rejects a path outside it.
        code = grants.emit(READ)
        self.assertNotIn("setenv", code)
        self.assertIn("static const char *const nv_read_roots[] = {NULL};", code)


def grants_octal(text: str) -> str:
    """The C literal `c_string` emits for `text`: fixed-width octal escapes."""
    body = "".join(f"\\{byte:03o}" for byte in text.encode("utf-8"))
    return f'"{body}"'


@unittest.skipUnless(grants.CC, "a C compiler is required for the file tests")
class FileCommandLineTests(unittest.TestCase):
    def test_a_missing_grant_directory_is_reported(self):
        with grants.workspace({"main.nv": READ, "data.txt": "x"}) as root:
            result = nava("check", str(root / "main.nv"), "--allow-read", "nosuchdir")
        self.assertEqual(result.returncode, 2)
        self.assertIn("E0506", result.stderr)
        self.assertIn("--allow-read directory does not exist", result.stderr)

    def test_a_grant_that_is_not_a_directory_is_reported(self):
        with grants.workspace({"main.nv": READ, "data.txt": "x"}) as root:
            result = nava("check", str(root / "main.nv"), "--allow-write",
                          str(root / "data.txt"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("E0507", result.stderr)
        self.assertIn("--allow-write is not a directory", result.stderr)


@unittest.skipUnless(grants.CC, "a C compiler is required for the file tests")
class IsolatedFileTests(unittest.TestCase):
    """The PLAN gate: both an allowed and a denied path under isolation."""

    def test_the_isolated_runner_allows_a_granted_file_and_denies_another(self):
        program = 'fn main() {\n    print(read_file("outside/data.txt"))\n}\n'
        with grants.workspace({
            "main.nv": program,
            "inside/data.txt": "visible",
            "outside/data.txt": "hidden",
        }) as root:
            # Capability decisions do not depend on the sandbox: the granted
            # subtree is read and the file beside it is denied in every mode.
            allowed = nava("run", "--isolation", "off", "--workdir", str(root),
                           "--allow-read", ".", str(root / "main.nv"))
            denied = nava("run", "--isolation", "off", "--workdir", str(root),
                          "--allow-read", "inside", str(root / "main.nv"))
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertEqual(allowed.stdout, "hidden\n")
        self.assertEqual(denied.returncode, 1)
        self.assertIn("read_file denied: 'outside/data.txt' is outside every permitted "
                      "directory", denied.stderr)
        self.assertEqual(denied.stdout, "")

    def test_the_boundary_holds_inside_the_namespace_jail(self):
        if not NAMESPACES:
            # No namespaces on this host: `--isolation required` must refuse to
            # run rather than quietly fall back, and that is what is asserted.
            program = 'fn main() {\n    print(read_file("data.txt"))\n}\n'
            with grants.workspace({"main.nv": program, "data.txt": "x"}) as root:
                refused = nava("run", "--isolation", "required", "--workdir", str(root),
                               "--allow-read", ".", str(root / "main.nv"))
            self.assertEqual(refused.returncode, 2)
            self.assertIn("E0601", refused.stderr)
            return
        program = 'fn main() {\n    print(read_file("outside/data.txt"))\n}\n'
        with grants.workspace({
            "main.nv": program,
            "inside/data.txt": "visible",
            "outside/data.txt": "hidden",
        }) as root:
            # The whole working directory is the sandbox: the file exists and is
            # readable inside the jail, and only the capability grant decides.
            allowed = nava("run", "--isolation", "required", "--workdir", str(root),
                           "--allow-read", ".", str(root / "main.nv"))
            denied = nava("run", "--isolation", "required", "--workdir", str(root),
                          "--allow-read", "inside", str(root / "main.nv"))
            # A grant for a directory the jail does not expose cannot help: the
            # path is not there, so resolution fails before the grant is used.
            unreachable = nava("run", "--isolation", "required", "--workdir", str(root),
                               "--allow-read", str(root / "inside"),
                               str(root / "main.nv"))
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertEqual(allowed.stdout, "hidden\n")
        self.assertEqual(denied.returncode, 1)
        self.assertIn("read_file denied: 'outside/data.txt' is outside every permitted "
                      "directory", denied.stderr)
        self.assertEqual(denied.stdout, "")
        self.assertEqual(unreachable.returncode, 1)
        self.assertIn("read_file denied", unreachable.stderr)

    def test_a_workdir_is_required_to_exist(self):
        with grants.workspace({"main.nv": READ}) as root:
            result = nava("run", "--isolation", "off", "--workdir",
                          str(root / "missing"), str(root / "main.nv"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("E0506", result.stderr)


if __name__ == "__main__":
    unittest.main()
