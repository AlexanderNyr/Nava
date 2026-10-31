# Changelog

## Unreleased

N14p: **`read_f64`** — a decimal read into a double, with no `strtod` to ask.

- The digits become a base-10^9 bignum and the power of ten the exponent asks for is
  met with a shift and a whole number of limbs instead of a division, which is exact
  where a division by ten would not be. What is still too wide is divided down by
  powers of two until fifty-six bits are left, and those — with the bit that says
  whether anything fell away below them — decide the rounding, a tie going to the even
  neighbour.
- The six refusals are the C runtime's, in its order and with its words, including the
  one it only reaches after `strtod` has answered: a value above `DBL_MAX` is
  `f64 input is outside the f64 range` and not `inf`. A token of 512 bytes or more is
  `f64 input token is too long`, because that is the token the C runtime has.
- Four bignum routines (`nv_bn_div_small`, `nv_bn_drop_limbs`, `nv_bn_shl`,
  `nv_bn_bitlen_low`) are the reader's alone and are emitted only for a program that
  calls `read_f64`.

N14o: **`chars_len` and `str_from_f64`** — characters, and the text of a float.

- `chars_len(s)` counts **characters**, not bytes: it decodes UTF-8 and refuses the
  six ways a byte sequence is not one — a continuation byte with nothing to
  continue, an overlong form, half of a surrogate pair, a value above `U+10FFFF`,
  a lead byte no form begins with, and a form that runs off the end — with
  `chars_len of a string that is not valid UTF-8`, the C runtime's words.
- `str_from_f64(x)` answers the text `print(x)` writes, because it goes through the
  same decimal conversion: one routine, two callers, so the two cannot drift.
- `str_from_i64` now shares one copier with `string_text`, `str_split`, `arg_at` and
  `str_from_f64` instead of writing its own copy loop.


N14n: **`str_split`, the argument functions and the clock** — and `self_test`, the last
fixture printed by the AST walk, now printed from the IR.

- `str_split(text, sep, out)` appends each field to the vector it is given. It is a
  **copy**, not a window: the separator is searched for one position at a time and each
  field goes into the arena before it is pushed, so the pieces outlive the text they
  came from. An empty separator is the C runtime's trap instead of a loop.
- `arg_count()` counts the words after the program's own name and `arg_at(i)` answers
  one of them as a `str`. `argc` and `argv` are on the stack when `_start` runs and
  nowhere afterwards, so they are copied into the data segment there — and only for a
  program that asks: a binary that reads neither carries neither symbol.
- `clock_seconds()`, `clock_millis()` and `clock_monotonic_millis()` go to the kernel
  directly (`clock_gettime`, one `struct timespec`) and share one helper. A reading the
  kernel refuses stops the program rather than answering `0`.
- `self_test` is built three ways — C from the IR, C from the AST walk, and native — and
  the three agree byte for byte. Every fixture in the corpus is now printed from the IR;
  the guard asks each fixture rather than trusting a list.

N14m: **`String` through one IR** — text a piece at a time, with the copy that makes
it a `str`.

- A `String` is three words behind a handle — the bytes, how many are text, how many
  the buffer holds — grown by doubling from 32 like a vector's, so a run of pushes
  costs amortised constant time per byte. `string_len` and `string_capacity` read the
  handle and cost no call, and `string_clear` empties the text without giving the
  buffer back.
- **`string_text` copies.** The builder's buffer moves when it grows, so a `str` must
  never point into it: the bytes go into the arena and the answer is a `str` the next
  push cannot change. A test takes a `str`, pushes two hundred more pieces and
  asserts the first answer is still four hundred bytes.
- Two traps a byte can trip, with the C runtime's words: a byte outside `0..255`
  names the byte it was given, and a NUL is refused on its own, because a `String` is
  NUL-terminated and a NUL inside it would make its own length a lie.
- The C backend prints the same builtins from the IR — one runtime helper each, and
  the `nv_string` typedef in front of a body the IR produced.
- Still refused by name: a `String` in a struct field, an enum payload or a container
  element (a copy this backend does not make), and `str_split`, `str_from_f64`,
  `read_f64`, `chars_len`, the filesystem, the argument functions and the clock.

N14l: **a function is a value** — one word, the address of its code, with no closure.

- A declared function can now be passed to a function, kept in a local, returned from
  one and handed to a function in another module: two instructions say it — `funcref`
  answers the address, and a `call` whose callee is a value runs it. Both name the same
  link-time symbol a direct call does, so a function reached through a value and the
  same function called by name are one address.
- Native: `funcref` is one `lea rax, [rip + symbol]` with a `pc32` relocation like any
  call's, and an indirect call loads the address into `r11` and emits `call *%r11` —
  the one branch in the backend the linker has nothing to say about. C: a function
  pointer needs a typedef, so the printer takes the program's function-type table and
  emits the typedef block in front of a body printed from the IR.
- A `funcref` is a *use* of a function, and the reachability walk now follows a value
  as well as a name: without it, the only caller of `apply(twice, 21)` names nobody,
  and the build stopped with an undefined symbol for a function the program had asked
  for by name.
- `self_test` moved: the lowering refuses no fixture any more, so `OUTSIDE_THE_SUBSET`
  in `tests/test_cgen_ir.py` is empty and `self_test` is in `LOWERED_NOT_PRINTED`,
  declined by the C printer for `String` and `str_split` and named as a reason by the
  new `cgen_ir.unprintable`. **31 of 32** fixtures still build and run identically.
- Still refused by name: `String`, `str_from_f64`, `read_f64`, `chars_len`, the
  filesystem, the argument functions and the clock.

N14j: **`print f64` through one IR** — the shortest decimal that reads back, converted
with integers only.

- Printing an `f64` is no longer refused: `nv_format_f64` on the native side answers the
  same question the C runtime asks `snprintf`/`strtod`, and answers it with a base-10^9
  bignum, one digit at a time. No `strtod`, no `snprintf`, no floating point in the
  conversion at all — a test reads the linked binary's symbols and asserts both are
  absent while `nv_bn_mul_small` is present.
- The rule being reproduced is `nv_format_f64`'s, not "shortest round-tripping decimal",
  because those differ: for `2**-1017` the nearest sixteen-digit text is the exact
  midpoint and reads back as the neighbour, so the C backend prints seventeen digits and
  so does this backend. `tools/f64_corpus.py` is the corpus that says so — 12 330 values
  (every power of two with both neighbours, the curated edges, random bit patterns and
  random subnormals) print identically through both backends.
- What is decided per digit is *where the digit run stops*: the nearest candidate at the
  current count is the truncation or the truncation plus one, the tie going to an even
  last digit, and it is printed only if it lies inside the value's rounding interval — a
  midpoint belongs to the even significand, which is one bit taken before the hidden bit
  is added. Seventeen digits are always enough, so the loop has a floor as well as a stop.
- `floats` left the AST walk: **29 of 32** fixtures build and run identically through both
  backends, and `LOWERED_NOT_PRINTED` in `tests/test_cgen_ir.py` is empty — every program
  the IR expresses is now printed from the IR as well. Printing an `f64` moved into the C
  backend's `PRINT_HELPERS` for the same reason: the body comes from the IR.
- Still refused by name: `Vec`, `String`, `HashMap`, `HashSet`, a function used as a
  value, and the builtins the runtime does not have (`chars_len`, `str_from_f64`,
  `read_f64`, the filesystem and the argument functions).

N14k: **`Vec(T)`, `HashMap(K, V)` and `HashSet(T)`** — the two containers that grow.

- A vector is a handle to a buffer with a length and a capacity, and `push` doubles
  the capacity and copies the elements across. A table is a power-of-two array of
  slots probed by a hash of the key, growing at three quarters full; a removal marks
  its slot instead of emptying it, because an empty slot would end the probe chain
  of a key that had collided with the one taken out.
- Both are *per type* rather than one runtime that switches on a type at run time:
  the slot layout, the hash and the equality are facts about the key, so `nv_map_
  str_i64_set` and `nv_set_i64_add` are generated from the program's types. The
  module states which tables a program uses, the way it already stated its structs.
- `for x in v` and `for key in m` are the same walk as `for x in a` for the vector,
  and three new instructions for a table — `slots`, `live` and `key` — because a
  table is walked over its *storage*, skipping the empty and tombstoned positions.
  The order is the slots', which is why the two backends agreeing on it is evidence
  and not a coincidence.
- `map_basics` and `set_basics` left the AST walk: **31 of 32** fixtures build and
  run identically through both backends, and the only one left is `self_test`.
- Still refused by name: `String`, a function used as a value, `str_from_f64`,
  `read_f64`, `chars_len`, the filesystem and the argument functions. (N14l took the
  function value out of this list.)

N14i: **slices and arrays of structs through one IR** — a view that owns nothing, and an
element that is one word.

- `a[i..j]` is one instruction (`slice`) on both sides of the differential: the native
  backend answers two words pointing into the parent's elements after `nv_check_slice`'s
  two questions, and the C backend prints the `nv_arr_T_slice` it always printed. No
  elements are copied, so a view of a thousand is two words and a write through the
  parent is visible through the view.
- An array of structs is the same six instructions as any other array: an element on
  this backend is the address of its record, so the handle stays one word per element. A
  struct that enters the array — by a literal or by `a[i] = p` — is copied as it enters,
  and `array_zeros` of a struct element fills every slot with a zeroed record, because a
  null there would read a field out of nothing.
- The C backend prints the family of per-array-type helpers from the IR path too
  (`array_helpers`), because the IR calls them by name: the body comes from the IR, the
  helpers are still generated from the program's array types.
- Two fixtures left the AST walk — `slices_basic` and `struct_arrays` — so **28 of 32**
  now build and run identically through both backends, and the set the C backend prints
  from the IR is the same 28.
- Two shapes stay refused by name: `Vec`, `String`, `HashMap`, `HashSet`, a function used
  as a value, and an `f64` that is printed.

N14h: **cleanup and early return through one IR** — `defer` and `?`, and the two
fixtures that needed them.

- A `defer` body is *lowered at every exit*, not where it is written: at the end of the
  block that registered it, on `return`, and on a `break`/`continue` that leaves it — and
  before the block gives its arrays back, so a deferred block may still use them. That is
  why `defer { print(100 + i) }` in a loop prints the value `i` has when the iteration
  ends, on both backends, and it is what makes the fixture's order `-1 1 1 0 101 1 102`.
- `let x = f()?` is a tag test, an early `return` of the failure value, and one payload
  read on the success path: `Option` and `Result` both, with the failure variant the
  checker numbered last. The early return leaves its scopes first, so the `defer` bodies
  it passes run and the arrays it owned are given back.
- Two refusals closed, two narrowed and named: `defer` and `?` compile natively now; a
  `defer` whose body *assigns* is refused by name (its assignment would land after the
  scope was carried out), and so is a `?` that would forward `Result(bool, str)` into a
  function returning `Result(i64, str)` (the two instantiations are different records).
- Three fixtures left the AST walk — `defer_labels`, `results_basic`, `results_chain` —
  so **26 of 32** now build and run identically through both backends, and the set the C
  backend prints from the IR is the same 26.

N14g: **enums and `match` through one IR** — a tag in a record, and a pattern that
compares it with a constant.

- A variant value, its tag and a payload field are instructions (`variant`, `tag`,
  `payload`), and the module states every enum layout it lays out: the position of a
  variant in its declaration *is* its tag, so a `match` compares numbers the checker
  assigned and no backend looks a name up at run time.
- `match` lowers to a chain of tag comparisons, one arm per variant, with one join that
  carries the locals the arms assign — the same block-parameter join an `if` uses. A `_`
  arm is the default and tests nothing; the checker has already proved the arms cover
  every variant.
- A function whose every path returns now lowers. The block an `if` or a `match` would
  have met at is *dead*, and the IR says so with `unreachable`, which the native backend
  turns into a trap and the C backend into `nv_unreachable()`. Before this, an `if` whose
  two halves both returned was refused as "a path that does not return".
- Two fixtures left the AST walk: `enums_basic` and `enums_state` — 23 of 32 now build and
  run identically through both backends, and the set the C backend prints from the IR is
  the same 23.
- A payload of struct type is refused by name: a struct value inside a record is the
  copy a struct already needs, and it arrives with that copy.

N14f: **text and numbers through one IR** — a string comparison is a comparison of text,
and the runtime's text routines exist in both backends.

- `==`/`!=` on two `str` are `TextCompare` in the representation, not `Cmp`: two strings
  are addresses, and comparing addresses is not comparing text. C prints it as `strcmp`,
  the native backend as a byte loop, and a pointer comparison is now impossible in
  either printer.
- Eight builtins are implemented by the native runtime — `bytes_len`, `byte_at`,
  `str_concat`, `str_slice`, `str_from_i64`, `to_i64`, `to_f64`, `read_i64` — with the C
  runtime's four ways of refusing a number it cannot read, word for word. A builtin
  neither backend has (`chars_len`, `str_from_f64`, the filesystem and argument
  functions) is refused by name.
- The native `str` is a pointer to `[length][bytes]`, which is not C's `char *`; the
  representation says only what the language says, so the two never have to agree about
  a representation — the differential is what holds them together.
- Traps gained two shapes: a byte index and a string slice (`byte index 5 is out of
  bounds for a string of 2 bytes`, `string slice 3..9 is out of bounds for a string of 5
  bytes`), both the C runtime's text and status.
- Six fixtures left the AST walk: `strings`, `numerical`, `fmt_table`, `read_i64`,
  `runtime_bad_input` and `arrays_sieve` now compile natively and print from the IR, so
  the C printer's set and the native backend's set are the same 21 of 32.

N14e: **`f64` and struct values in the IR** — the representation learns the rest of the
value model, and both readers print it.

- `Bin`, `Neg` and `Cmp` carry the type they operate on, so `add f64` and `add i64` are
  different instructions rather than one instruction a backend has to guess about.
- A struct is a value of the IR: `struct` builds one, `field` reads one by name, and a
  **cell** (`slot`, `load`, `store`) is the storage a field write needs, because a value
  is immutable and storage has an identity. The module states the layout, so a field
  name is a position in the program's declaration rather than in a backend's opinion.
- A builtin is an instruction too (`builtin`) when the language defines it and no module
  declares it: what it means is the language's business, which runtime implements it is
  the backend's, and a backend that has no such routine refuses it by name.
- The native backend gains SSE arithmetic (`addsd`, `subsd`, `mulsd`, `divsd`) and
  comparisons (`ucomisd`, with `nan` answered separately: unordered is false for
  `<`, `<=`, `==`, `>` and `>=`, and true for `!=` — the language is IEEE), struct
  records from the arena with fields one word apart, and a copier per struct type so
  that a copy is as deep as the type is nested.
- The C backend prints cells, structs and `f64` comparisons from the same IR, and names
  every function with the checker's table, so the artefact's vocabulary does not depend
  on which path printed it.
- Still refused by name: printing an `f64` (its shortest round-tripping decimal form is
  a conversion the native backend does not have), the builtins, slices, enums, `match`,
  `defer`, `?`, `String`/`Vec`/`HashMap`/`HashSet`, arrays of structs and struct fields
  that own storage.

N14d: **one IR, two backends** — the C backend reads the shared IR.

- For everything the IR can express — arrays of `i64`/`bool`/`str`, checked arithmetic
  and comparisons, `if`/`while`/`for`, calls, `print` — `cgen.py` no longer walks the
  AST. A block becomes a label and a `goto`, a value a local, and an instruction the
  runtime call the language says it is, so the two backends read one lowering instead
  of writing two. The rest of the language is still printed by the AST walk, and the
  emitted file says which path produced it.
- The gate is three-way: C from the IR, C from the AST (`NAVA_C_FROM_IR=0`) and the
  native backend agree on stdout, stderr and exit status.
- The index of a store is checked **before** the value is evaluated — `a[1] = mark()`
  must not call `mark` — and the IR states that as a `check` instruction rather than
  leaving it to the printer.
- Two refusals that would otherwise have been silent wrongness: `==` on two `str`s is
  never printed from the IR (C would compare pointers, the language compares text),
  and a generic function with more than one instantiation is refused by name (an
  instantiation carries no type argument in its name, so two would share one symbol).

N14c: **arrays through one IR** — an array is a value of the intermediate
representation, with its own instructions and its own ownership.

- `array`, `zeros`, `index`, `index store`, `len` and `release` are IR
  instructions, so both backends read the same six; `index` is checked and `len`
  answers the length the array was built with.
- Ownership is the lowering's decision and is written down: the block that declared
  an array gives it back when it is left — on `return`, on `break`/`continue` that
  leave the block — last declared first, and an array nobody binds belongs to the
  statement that built it. A `[T]` parameter is a borrow and is never released.
- The native backend gains an `mmap`-backed arena and one array helper for all three
  element types (`i64`, `bool`, `str` share one word and one stride). `release`
  encodes as nothing there: the arena never returns memory, and that is stated.
- The three array traps are the C runtime's, word for word and status for status:
  index out of bounds, a negative length, and a length above `MAX_ARRAY_ELEMENTS`.
- The differential covers arrays: `arrays_basic`, `arrays_borrow`, `arrays_sort`,
  `for_loops` and a new `arrays_and_loops` program are identical on stdout, stderr
  and exit status through both backends.
- Fixed, found by that differential: `while a && b` looped forever on the native
  backend — the back edge went into the `&&` join block instead of the loop header.

N14b: **whole programs** — the native backend links, names what it links, and can
say where a program died.

- A module is a **unit**: its code, its constant data, its writable data, the symbols
  it defines and the references it cannot resolve. A function's link-time name is
  `nava.<module>.<name>`, so two modules may each define `abs`; every other label is
  private and named after its unit.
- A **static linker** puts the units in one file and resolves their references. A
  reference nothing defines stops the link with the name of the symbol — never a
  zeroed field, which is a jump into nothing that runs.
- The executable carries `.text`, `.rodata`, `.nava.lines`, `.data`, `.symtab`,
  `.strtab` and section headers, so `nm` and `readelf` read it: a program several
  files long, one executable, and a reader of our own (`nava_lang/native/reader.py`)
  that parses it back and is compared with `readelf` and `nm` rather than believed.
- **Line tables, and a trace**: with `NAVA_TRACE` set, a trap prints
  `  at nava.entry.scale (prog.nv:2)` for every frame, innermost first. Without it,
  the output is exactly the message, so the two backends stay comparable byte for
  byte.
- `nava run --backend native` builds and runs through the same sandbox as any other
  run, with the same isolation, limits and timeout; `--cc` and `--opt` are `E0903`
  there too.
- A `str`-returning call and a call into another module are now compilable; only the
  functions `main` can reach are compiled, so importing a module and calling one
  function of it does not drag in the rest.
- Two builds of one file in two directories are still the same bytes: the line table
  names the file and never the path.

N14a: **a second code generator** — Nava to x86-64 ELF, with no C compiler.

- `nava build --backend native` writes a static x86-64 Linux executable itself: no
  C compiler, no assembler, no linker. The file is `ET_EXEC` with two `PT_LOAD`
  segments (code read-execute, data read-write), and its runtime is `write` and
  `exit` — printing an `i64` is a decimal conversion loop in machine code.
- The subset is named rather than approximated: `i64`, `bool`, string literals,
  checked `+ - * / %`, comparisons, `!`, `&&`/`||` with short-circuit evaluation,
  `if`/`while`/`for … in a..b`, `break`/`continue`, labelled jumps, calls (up to six
  arguments, recursion included) and `print`. Everything else is `E0901`, naming the
  construct and the function; the same program still compiles through the C backend.
- `nava emit-ir` prints the IR: blocks with parameters, one value per instruction,
  `jump`/`branch`/`ret` terminators — a join is a block parameter, not a phi.
- `--cc`, `--opt` and `--record` with `--backend native` are `E0903`: a record with
  no toolchain in it would be a provenance claim the build never made.
- Traps print the same messages as the C runtime (`i64 overflow in +`, `division by
  zero`, `remainder by zero`, …) and exit with status 1, and the differential test
  compares stdout, stderr and status of the two backends over a corpus of programs.
- Two builds of one source produce the same bytes.

N13d: **reproducible builds** — records, and proof that two builds are one build.

- `nava build --record PATH` writes a build record: the digest of every `.nv` file
  compiled (`entry`, then each module by name), the digest of all of them, the
  digest of the generated C, the digest of the binary, the packages that
  contributed, the toolchain (the C compiler's command, path and version, the
  linker's version, platform and machine) and `SOURCE_DATE_EPOCH`. JSON with sorted
  keys and no build time, so the same tree writes the same bytes on any machine.
- `nava reproduce SOURCE [--runs N] [--record PATH]` builds `N` times in fresh
  temporary directories, compares every build with the first and optionally with a
  record written earlier, and prints `reproducible over 2 builds: sha256:…`. A
  difference stops it with `E0814` and is **named**: `toolchain.cc_command: … != …`
  first, then the source, C and binary digests it explains.
- `SOURCE_DATE_EPOCH` is read, recorded and passed to the compiler, and validated by
  every command that produces a binary (`build`, `reproduce`, `run`): anything that
  is not a whole non-negative number is `E0813`.
- A record that is not a record is `E0815`, not a guess.

N13c: **building against vendored packages**.

- `import name;` searches the project's own files, then `vendor/<package>/name.nv`
  for every package `nava.lock` names, then the standard library. A package's
  modules are ordinary modules and may import each other.
- The lockfile is the boundary: a module provided by a `vendor/` directory the
  lockfile does not name is `E0810`, naming the package and saying to add the
  constraint and lock again — *no transitive dependency can appear that the lockfile
  does not name*, enforced where a program can break it.
- A name means one thing: a local file wins over a package's file (documented
  order), a package file may not shadow a standard module (`E0360`), and two locked
  packages may not provide the same module (`E0811`).
- The digest check now runs **before** the build: a vendored package whose bytes
  moved stops `check`, `build`, `emit-c` and `run` with `E0804`. That is where *a
  tampered package fails the build* becomes true.
- `nava build --project` prints the packages that contributed a module, and
  `--format json` carries the same list plus `"network": "offline"`.
- `--network` is refused with `E0812` (there is no registry to reach); `--offline`
  is accepted and recorded, because offline is the only mode there is.

N13b: **`nava.lock`** — the resolved graph, by digest.

- Packages are vendored: `vendor/<name>/` holds a `nava.toml` and its `.nv` files.
  There is no registry and no network; a build reads the tree it was given.
- A package is identified by a **content digest**: SHA-256 over `"F\0" + path +
  "\0" + length + "\0" + bytes` for each regular file in byte order of the
  relative path, then `"E\0"`. No timestamps, no modes, no absolute paths — so two
  checkouts agree and one flipped byte does not. A symbolic link inside a package is
  refused.
- `nava lock` resolves the graph (transitively, every constraint on a name, cycles
  refused), writes `nava.lock` sorted by name, and prints the graph with its
  digests. `nava lock --check` recomputes it and stops at the first disagreement.
- The tamper list has one code per accident: an edited package or a hand-edited
  digest (`E0804`, with both digests in the message), a version or dependency list
  that moved, an entry nothing needs and an entry that went missing (`E0803`), a
  missing lockfile (`E0801`), a malformed one (`E0802`), a package that is not
  vendored (`E0805`), a version outside a constraint placed by the project or by a
  package (`E0806`), a cycle (`E0807`), a directory that is not a package (`E0808`).
- `^0.2.0` no longer accepts `0.3.0`: the caret rule is semantic versioning's, which
  may not move the left-most non-zero digit.
- One reader serves both files: the manifest's parser became a TOML-subset reader
  with repeated tables, used by `nava.toml` and `nava.lock`.

N13a: **`nava.toml`** — a project has a manifest.

- `[package]` (`name`, `version`, `edition`), `[build]` (`entry`, default
  `main.nv`) and `[dependencies]` (`name = "constraint"`), read as a strict subset
  of TOML: three tables, strings, integers, booleans, comments. An unknown table,
  an unknown field, a repeated key or a value of the wrong shape is refused with a
  code and a line, because a key that is skipped silently is a setting its author
  believes is in force.
- Version constraints: `1.2.3`/`=1.2.3` exact, `^1.2.3` to the next major,
  `~1.2.3` to the next minor, `*` any — with the matching written down and tested
  rather than assumed.
- A project name and a dependency name may not be the name of a standard-library
  module (`E0708`), and a name is a lower-case identifier of at most 64 characters
  (`E0709`).
- The manifest's `edition` must be the edition the entry file declares (`E0706`):
  only one of the two is read by the compiler.
- `nava project` describes the manifest (`--format json` for a document), and
  `nava check|build|emit-c|run --project` takes the entry file and the project
  root from it. Naming a source as well is `E0509`, not a guess.
- Ten new diagnostic codes: `E0509` and `E0701`–`E0709`.

N12i: **`time`** — the clock, and what `pure:` means when the answer moves.

- Three readings and no way to set one: `time::now_seconds()`,
  `time::now_millis()` (the wall clock, which can be corrected backwards) and
  `time::monotonic_millis()` (which only moves forward), plus
  `time::elapsed_millis(start)` for a duration. All four are `pure: no`: `pure:
  yes` promises the same answer for the same call, and a clock cannot.
- Three conversions that are arithmetic, and `pure: yes`:
  `time::seconds_to_millis` (checked `*`), `time::millis_to_seconds` (truncating
  towards zero) and `time::millis_in_second` (the remainder, so the two rebuild
  the value). These are the half of the module the determinism gate covers.
- The claim is enforced, not trusted: the clock builtins are not on the purity
  list, so a `pure: yes` function that reads a clock is refused by the guard.
- The builtins are `clock_seconds()`, `clock_millis()` and
  `clock_monotonic_millis()`: a builtin's name is reserved in every module
  (`E0301`), so a builtin and the function wrapping it cannot share a word.
- `elapsed_millis` is not clamped: a mark that lies in the future gives a
  negative number rather than a measurement of zero.

N12h: **`collections`** — the layer above one element at a time.

- Four questions that only read, and are `pure: yes`: `sum_of` (checked `+`, so a
  sum that does not fit stops the program), `count_of`, `contains_all` and
  `has_any`. An empty wanted list is vacuously contained and never "any", which is
  the pair a reader is most likely to get backwards.
- Three that fill a container the caller passes in, and are `pure: no` because the
  effect is on storage the caller keeps: `count_distinct(values, seen)`,
  `keys_of(table, out)` and `elements_of(items, out)`. Filling **appends**; the
  vector keeps what it held. `count_distinct` answers `set_len(seen)` after the
  call, so calling it twice answers the same number instead of doubling it.
- The iteration order is the container's own: stable for the same insertions in
  the same build, and not claimed to be sorted.
- Every public name of this module runs in the standard library's self-test, so it
  adds no entry to `TESTED_ELSEWHERE`; the run is 41 `ok` lines and `standard
  library: ok`, 863 bytes recorded as a positive case.

N12g: **`args`**, and a program's command line. A program can see the words it
was started with, and nothing about them is interpreted.

- Two builtins: `arg_count() -> i64` and `arg_at(i64) -> str`, over
  `int main(int argc, char **argv)`. The program's own name is not an argument;
  an out-of-range position stops the program with
  `argument index 2 is out of bounds for 2 arguments` (and `1 argument`, singular,
  when there is one).
- `nava run prog.nv -- a "two words" -x` passes everything after `--` to the
  program word for word: no splitting, no expansion, and an argument that looks
  like an option of `nava` stays an argument because it sits behind the fence.
- Module `args` in two layers, which is what makes it testable: `flag_in` and
  `value_in` take an array of words and are `pure: yes` (the self-test runs them);
  `count`, `get`, `flag` and `value` read the real command line and are `pure: no`.
  A flag with nothing after it has **no** value, so the fallback answers and a
  missing argument cannot look like an empty one.
- Contract examples now start with a fixed command line, `--count 3 name`, since a
  module about command lines cannot be demonstrated without one.
- A regression this increment caused and fixed: a `nargs=REMAINDER` parser handed
  `nava run f.nv --timeout 0.1`'s `--timeout 0.1` to the *program*, which hung the
  timeout test. The positional is `nargs="*"`, and a regression test runs a
  bounded loop and asserts exit `124`.

N12f: **`fs`**, and three builtins behind it. Paths are text; the filesystem is
asked questions, and the questions are gated.

- `fs::basename`, `fs::dirname`, `fs::path_join`, `fs::extension`, `fs::stem`,
  `fs::is_absolute` are arithmetic on a `str` and documented `pure: yes`.
  `path_join` never doubles a separator and nothing is normalised:
  `path_join("a", "..")` is `"a/.."`, because a normaliser that guesses produces
  a path the kernel will not resolve.
- `fs::exists`, `fs::size`, `fs::entries` wrap three new builtins —
  `path_exists(str) -> bool`, `file_size(str) -> i64`,
  `list_dir(str, mut Vec(str))` — and are `pure: no`: asking twice may see two
  filesystems.
- All three are **read-grant** operations. A path outside every grant stops the
  program (`path_exists denied: … is outside every permitted directory`) rather
  than answering `false`, so a question is not a way to probe the host. A path
  that simply is not there is an answer: `false` for `path_exists`, a stopped
  program for `file_size` and `list_dir`, which have nothing to report.
- `list_dir` appends the names in **byte order** and skips `.` and `..`; the order
  is part of the result, so two runs and two hosts agree. `MAX_DIR_ENTRIES` (4096)
  makes a huge directory a runtime error rather than a short-looking listing, and
  it is documented and compared with the compiled value like the other limits.
- Writing the contracts found a documented failure that could not happen:
  `fs::exists` promised the "no grant configured" message while the example lane
  runs *with* grants. The contract now names the refusal the grant case produces.
- `tests/test_fs.py` (19 tests) covers the text edges, the three questions, the
  ordering, the refusals on the real command line; `TESTED_ELSEWHERE` grows the
  three file-touching names, and `self_test.nv` runs the six pure ones.

N12e: **`io`**. Lines of text and the files they come from, on the capability
model N6 established — and the first module whose public surface is tested in two
places, with the second place named in a list rather than left implicit.

- `io::count_lines`, `io::first_line`, `io::join_lines` are `pure: yes`;
  `io::lines_of` (writes its `mut` parameter) and `io::read_lines`,
  `io::write_lines`, `io::append_line` (touch a file) are `pure: no` with the
  reason written down.
- A line is the bytes between two `\n` bytes, so `count_lines("")` is 1 and
  `count_lines("a\nb\n")` is 3. `write_lines` and `read_lines` round-trip: the
  terminator a final `\n` leaves is dropped, a file that really ends with an
  empty line keeps it, and an empty file reads back as one empty line.
- The capability model is inherited, not widened: no grant is `read_file denied`,
  a read grant cannot write, a write grant cannot read, and a missing file stops
  the program with `cannot open '<path>': No such file or directory`.
- The contract guard now runs examples with grants (`--allow-read .`,
  `--allow-write .`) inside its own temporary directory, so a file example is
  executed instead of merely compiled.
- `tests/test_io.py` carries the twelve file-and-capability tests;
  `tests/test_self_test.py` grows a `TESTED_ELSEWHERE` list with two meta-tests,
  so a public name tested outside the self-test is a pointer a guard can follow.
- One defect the lanes caught: `read_lines` dropped its last empty field
  unconditionally, so an empty file read back as zero lines while
  `count_lines("")` said one. The rule now looks at the last byte.

N12d: **`fmt`**. Printing a table needs a column width, a side to align to and a
number of decimal places; the audit's f-string row (519) is closed with a module
instead of a format language.

- `fmt::pad_left`, `fmt::pad_right`, `fmt::pad_center`, `fmt::clip`,
  `fmt::i64_aligned`, `fmt::f64_fixed` and `fmt::repeat`, each with a full contract
  and executed examples, and all of them `pure: yes` because every one answers
  with a `str` built by `str_concat` rather than a `String` builder.
- A width counts **bytes**: a `str` is a byte sequence and the module does not
  guess how wide a character is on a terminal, so `clip` may cut inside a
  multi-byte character. `f64_fixed` rounds the binary value with ties away from
  zero (`-2.5` → `"-3"`, `1.005` → `"1.00"`), keeps no sign on a value that rounds
  to zero (`-0.4` → `"0"`), and documents the overflow it can hit
  (`to_i64 of a value outside the i64 range`).
- Two holes in the guards, found by writing the module. A `pure: yes` function
  that called a **local helper** was reported as calling something with "an
  effect", which names the wrong repair; there are now three cases — documented
  `pure: no`, no claim at all (with the doc block to write) and a builtin with an
  effect — and a helper that states `pure: yes` gives `[]`. And a module whose
  every function forgot `pub` used to pass every lane by having nothing to check;
  `check_program` now reports `exports no function`.
- The guide guard reads a module's public surface by edition (`pub fn` in edition
  2, every `fn` in edition 1), so a private helper is not the guide's business
  while an exported name still cannot be omitted.
- `self_test.nv` names 21 public functions now and prints 21 `ok` lines;
  `tests/fixtures/positive/fmt_table.nv` is a two-column table recorded byte for
  byte; `docs/stdlib/fmt.md` is generated and compared.

N12c: **purity and self-testing**. A contract now says whether the function has
effects, the guard checks that claim against the body, and the standard library
tests itself in Nava and returns its own failure count.

- `pure: yes` / `pure: no` is a required field of every doc block. A function that
  claims `yes` is checked against what it does: printing, calling a builtin with an
  effect, calling a function documented `pure: no`, taking a `mut` parameter,
  writing through a parameter and calling into another module are each reported —
  the last because purity across files is not decidable from one, not because it is
  assumed. Ten of the fourteen packaged functions are documented pure, and each of
  their examples is built at `-O0`, `-O1`, `-O2` and `-O3`, twice, with identical
  output. A second backend does not exist until N14, so "across backends" is not
  claimed.
- A function is a value with its module on it: `test::passing` names a
  `fn() -> i64` across an `import`, kept in the tree as `Variable.module`. The
  failures are the ones a call has — module not imported, no such function, not
  `pub` — plus `E0372` in edition 1, which has no function type.
- The `test` module: `expect_true`, `expect_i64` and `expect_str` return the number
  of failures, `run(name, body)` prints `ok   <name>` or `  expected 8, got 7: …`
  and `FAIL <name>: 1 failed`, and `summary(suite, failures)` prints the verdict and
  returns the count. The process exit status is that count, so a program tests
  itself and says how badly it failed.
- `tests/fixtures/positive/self_test.nv` runs one body per public name of `math`,
  `sort` and `text` — fourteen `ok` lines and `standard library: ok`, 305 bytes,
  recorded as a positive case — and the same report comes out at three
  optimisation levels and on a second run. `tests/test_self_test.py` also makes
  sure the harness can fail: one wrong expectation exits `1` and names the test,
  an inverted condition fails, a body with two bad checks exits `2`, a clean body
  stays quiet.
- `nava doc` documents `test` like the rest of the library; `docs/stdlib/test.md`
  is generated and compared byte for byte.

N12b: **contracts**. A `///` block above a `fn` is that function's contract, and
the standard library writes one for every public name; `nava doc` renders the
reference from them, and the test suite compiles and runs every example and every
documented failure in CI.

- A doc comment is **syntax**, not trivia: `///` lines in a run belong to the `fn`
  below and are kept in the syntax tree (`Function.doc`). A block anywhere else —
  over a `struct`, or inside a body — is `E0207`. The grammar models the same rule,
  so a doc block is part of the shape the conformance checker and the GBNF agree
  on, and the packaged modules are programs both accept.
- The contract fields are read literally: `pre:`, `post:`, `cost:`, one or more
  `example:` lines and zero or more `error: statements_must_stop => message`
  lines; every other line is prose. A typo such as `pres:` shows up as a *missing*
  `pre:` rather than being silently accepted, and a repeated single field is
  reported.
- `nava doc [MODULE…] [--out DIR]` generates Markdown from the blocks; with no
  module named it documents the whole standard library, in file order, so the
  output is reproducible. An unknown module is `E0508`.
- `docs/stdlib/{math,sort,text}.md` are generated and committed; the guard
  regenerates them and compares byte for byte, the same discipline as the pinned
  guide digest.
- `tests/test_contracts.py` is the gate: every public function has a summary,
  `pre:`, `post:`, `cost:` and at least one `example:`; every example is compiled
  into a program and must be `true`; every `error:` line is run, and the program
  must stop with the documented text. The file also carries the negative controls
  — an undocumented public function, an example that does not compile, an example
  that is false and a documented failure that does not happen — because a guard
  whose negative control passes is not a guard.
- `nava_lang/contracts.py` is the module behind both: parse, `problems`,
  `check_program`, `module_version`, `signature` and `render_module`.
- `E0207` and `E0508` are new, so the code table has 147 entries.

N12a: `HashMap(K, V)` and `HashSet(T)` — the first increment of the standard
library, and the two collections N11 moved to N12 on purpose.

- `let m: HashMap(str, i64) = map_new()`, `let s: HashSet(String) = set_new()`.
  The annotation is the only place the types are written, so `let m = map_new()`
  is `E0448` rather than a guess.
- Interface: `map_len`, `map_contains`, `map_get -> Option(V)`, `map_set`,
  `map_remove -> bool`, `map_clear`; `set_len`, `set_contains`, `set_add`,
  `set_remove -> bool`, `set_clear`. `map_get` is **total**: a missing key is
  `None`, not a run-time error.
- A key is `i64`, `bool`, `str` or `String`; anything else is `E0447`. Both hash
  functions are fixed (`murmur3` finaliser over an integer, FNV-1a over bytes),
  so iteration order is reproducible across runs and platforms — it is observable
  in `for k in m`, so it cannot be free to change.
- The table is open-addressed with linear probing over power-of-two slots,
  tombstones for removals and doubling at three-quarters occupancy, so a
  set/remove-heavy loop rehashes instead of degrading.
- Ownership follows the element rule: a key and a value are copied in, released
  on overwrite, `map_remove` and `map_clear`, and `map_get` hands back a copy
  inside its `Option`. A map or a set is **moved**, never copied (`E0445`), a
  parameter is a borrow (`E0446`), and a mutating call needs a writable target
  (`E0444`).
- A map or a set is not a value and not an element: `Vec(HashMap(i64, i64))`,
  `[HashMap(i64, i64)]` and `HashMap(str, HashMap(i64, i64))` are `E0206`, the
  nested-collection rule that already covered `[[i64]]`.
- A map or a set **field** is fine, and returning one keeps the arena text of its
  keys alive (`holds_text` now walks keys and values), which is what
  `test_a_returned_map_keeps_its_keys` checks.
- `E0447` and `E0448` are new, so the code table has 145 entries; `E0444`'s text
  now names a map and a set as well as a vector.

N11g: an **element** of an array or a vector may be a value that owns storage —
`[String]`, `Vec(String)`, `[Bag]`, `Vec(Bag)`, `Vec(Slot)` — with a deep copy on
the way in, a deep copy on every read, and a release that frees every element.

- Copy in: `vec_push`, `vec_set`, `xs[i] = x` and an array literal copy the value
  into the storage (`nv_string_copy` for a `String`, the type's own helper for a
  struct or an enum), and a write releases the slot it overwrites first.
- Copy out: `xs[0]`, `vec_get(v, 0)` and the variable of `for x in v` are copies
  that own their bytes and are released at the end of the statement, the block, or
  the turn of the loop — `continue` and `break` included. `vec_pop` moves the
  element out instead.
- Release: freeing an array or a vector releases each element it holds, then the
  buffer; growth moves handles between buffers without copying them.
- `array_zeros(n)` of an owning element is `E0425`: an all-zero `String` is a null
  buffer, not an empty string, so an owning element has no zero value.
- `E0426` and `E0427` are **retired**: no element type owns "too much" any more.
  What they used to carry is now a shape rule — a collection is not an element —
  and `[[i64]]`, `[Vec(i64)]`, `Vec(Vec(i64))` and `Vec([i64])` are `E0206`.
- Three fixtures were retired with them (`array_of_owning_values.nv`,
  `vector_of_owning_values.nv`, `array_of_strings.nv`): each asserted a refusal
  this increment turns into a feature, and `tests/test_elements.py` is the positive
  counterpart that replaces them.

N11f: a `Vec` or a `String` can be **stored** — as a struct field, a variant
payload or a type argument — and the holder keeps a deep copy of its own buffer.

- `struct Bag { items: Vec(i64), text: String }`, `enum Slot { Empty, Full(Bag) }`,
  `Pair(String)` and `Option(Vec(i64))` all work. The generated copy helper of
  the holding type calls the helper of the field's type, so `nv_vec_i64_copy`
  allocates a buffer and copies the elements and `nv_string_copy` copies the
  bytes: releasing one holder never touches the other's storage.
- Everything that crosses a binding — a `let`, an assignment, a field write, a
  `match` binding, a `return` — copies deeply, and the block that declared the
  holder releases what it holds.
- A field read stays a borrow: `let out: Vec(i64) = bag.items` is `E0428` and
  `vec_push(bag.items, 1)` is `E0444`. Write the field as a whole instead.
- An **element** still may not own storage, and the refusal now follows the
  fields: `[Bag]` and `Vec(Bag)` are `E0426` / `E0427` because a `Bag` holds a
  `Vec` and a `String`. Owning elements are the element work of N11g.
- `string_field.nv` is retired — the refusal it asserted is now a feature — and
  `E0428` is covered by `discarded_owned_result.nv` instead.

N11e: a value that owns storage crosses a frame boundary by moving, and the arena
gets the regions that moves make sound.

- `fn make() -> Vec(i64)` and `fn label(n: i64) -> String` are allowed: `return`
  **moves** the value out of the frame, the caller's binding owns it, and the
  callee releases nothing. An early `return` from inside a loop moves the same
  way.
- `let b: Vec(i64) = a`, `b = a` and reassignment of an owning local are moves,
  not copies: the buffer is transferred and the source binding is emptied. A
  moved-from binding is dead — reading it, passing it, returning it, moving it
  again or assigning to it is **`E0445`**.
- Only a value the function owns can be moved out: returning a parameter or
  assigning over one is **`E0446`**, because the caller still owns it. A call
  that returns an owning value and is thrown away is `E0428`.
- The arena now has **regions**. A call is given one — a mark taken on entry and
  released on return — when nothing it allocates can be reached afterwards: the
  return type holds no text and no `mut` parameter holds text. 2 000 calls that
  each build text internally therefore report no live arena bytes; a function
  that returns `str` or writes through `mut Vec(str)` keeps its chunks, which is
  the price of soundness and is asserted too.
- Fixed while building it: the arena registered its exit report every time the
  chunk count returned to one, so a program that used regions printed the same
  report twice; the registration is a one-shot flag and a test counts the lines.
- Still not storable: an owning value in a field, a payload, a type argument or
  an element — that is the storage work of N11f.

N11d: text a program builds — `String`, the `str_*` functions and the arena.

- `str_concat`, `str_from_i64`, `str_from_f64`, `str_slice`, `str_index_of`,
  `str_starts_with`, `str_ends_with`, `str_trim`, `str_replace`, `str_split` and
  `str_join` build a `str` from parts. Slices, lengths and offsets count bytes;
  a bad slice is a runtime error and a missing needle is `-1`.
- `String` is the builtin owner of bytes assembled a piece at a time:
  `string_new`, `string_push`, `string_push_byte`, `string_len`,
  `string_capacity`, `string_clear` and `string_text`. Capacity doubles from 32,
  a `mut String` parameter writes into the caller's builder, and the block that
  wrote the `let` frees the buffer. A `String` cannot be copied, reassigned,
  returned, stored in a field or a payload, or used as a type argument
  (`E0428`); it cannot be an array or vector element (`E0426` / `E0427`).
- `str_split` fills a `Vec(str)` rather than returning one, because a function
  cannot return a vector: `let parts: Vec(str) = vec_new()` then
  `str_split(text, ",", parts)`.
- Built text lives in an **arena**: 64 KiB chunks, bump-allocated, released
  together at exit. A `str` may be stored wherever a value may go, so a region
  cannot be reclaimed while something that crossed a frame boundary points into
  it; that needs the move work of N11e. Under `-DNAVA_TRACK_ALLOCS` the arena
  prints `nava: arena bytes: N, chunks: M` and frees every chunk, which is what
  the AddressSanitizer gate checks.
- A defect in N11c is closed: the vector builtins evaluated their arguments
  twice, so `vec_push(v, bump(1))` repeated a side effect. Every builtin now
  materialises each argument once, and two tests pin it.
- `HashMap`/`HashSet`, the last item of N11, moved to N12 with the standard
  library rather than the memory model; `INDEPENDENCE.md` says so next to the
  increment table instead of leaving it implied.

N11c: `Vec(T)`, the growing vector.

- `Vec(T)` is a builtin type, not a generic declaration: `let xs: Vec(i64) =
  vec_new()` creates an empty vector and the block that writes the `let` releases
  it, including on an early `return`. `T` is `i64`, `bool`, `str`, `f64`, or a
  struct or enum that owns nothing; an owning element is `E0427`, for the reason
  an owning array element is `E0426`.
- The whole interface is seven builtins: `vec_new`, `vec_len`, `vec_capacity`,
  `vec_push`, `vec_pop` (which returns `Option(T)::None` on an empty vector),
  `vec_get` and `vec_set`, the last two with checked indices that stop the
  program like an array index does.
- Capacity doubles from four, so `N` pushes copy fewer than `2N` elements in
  total and `vec_capacity` reports the slack; a vector cannot be copied,
  reassigned, returned or stored in a field or a payload (`E0428`), and
  `vec_new()` without an annotation is `E0429`.
- Growth replaces the buffer inside the handle, never the handle itself, so a
  `mut Vec(T)` parameter grows the caller's vector and the caller sees the new
  length. Every mutating call therefore needs a writable target — a local or a
  `mut Vec(T)` parameter (`E0444`).
- `for x in v` reads the length once, before the first turn, and binds the
  element type; the loop variable is read-only like every other loop variable.
- `tests/test_vec.py` covers the interface, the growth arithmetic, the drain, the
  element kinds, the four refusals and the out-of-range stop, with the
  live-array counter at zero on every run and one program clean under
  AddressSanitizer and UndefinedBehaviorSanitizer.

N11b: fields and payloads that own array storage.

- A struct field and a variant payload may now be an array. The value owns that
  storage: copying the value copies the array, so `let b = a` gives `b` its own
  buffer and a write through one value is never seen through the other. The
  compiler emits one copy and one release helper per type that needs them and
  calls them at every ownership boundary — a `let`, an assignment, a field
  write, a `match` binding and a `return` — and the block that owns a value
  releases it when it ends. No copy is elided yet.
- `b.items[1]`, `len(b.items)`, `b.items[1..3]`, `for x in b.items` and
  `b.items = [...]` all work: an array field is an array in every position.
- A value that owns an array may not be an **array element** (`E0426`). An
  element is copied on every read and every write, so an owning element needs
  deep copies inside the storage itself — the move work of N11e. The retired
  `E0378` and `E0399` go with the walls they carried.
- `tests/test_ownership.py` checks the copy rule through observable values and
  the release rule through the runtime's live-array counter: seven runtime
  programs, every one reporting zero arrays at exit, including a `return` out of
  a loop and a `defer` that reads an owning value.

N11a: strings and enums as values, and arrays of every value.

- A `str` is now allowed as a struct field and as a variant payload. A string
  value in this compiler is immutable and lives for the whole run — nothing
  frees it and nothing writes it — so copying a struct that holds one aliases a
  pointer that cannot matter, and the old `E0378`/`E0399` walls on `str` are
  gone. An array field and an array payload stay refused, because an array owns
  storage with exactly one owner (N11b).
- An enum is an array element: `[Shape]`, with literals, indexing, element
  writes, `for`, slices, `mut [Shape]` parameters and `array_zeros`. The retired
  `E0424` ("an array element may not be an enum") goes with the restriction.
- `array_zeros(n)` is allowed only when all-zero bits are a *value* of the
  element type (`E0425`): `0`, `false`, `0.0`, a struct of those, or an enum
  whose first variant is. This closes a hole that predates the increment —
  `let names: [str] = array_zeros(2)` used to type-check and then segfault on
  `print(names[0])`; the case is now a negative fixture.
- Guide, grammar artefact and measurements moved with the language where the
  text changed; `INDEPENDENCE.md` records N11a and splits N11 into four
  increments.

N10g: function values — a function as a value with a checked signature.

- A function type is written `fn(i64) -> i64`, or `fn() -> void` when it returns
  nothing. The result is always spelled out, so two signatures are the same type
  exactly when they are written the same way.
- A declared function named where a value is expected is a value:
  `let f: fn(i64) -> i64 = double` (or `let f = double`). The value is the
  address of the function — there is no closure and nothing is captured — and it
  can be passed, returned, stored in a struct field or a variant payload, put in
  `Option(fn(i64) -> i64)` and used as the type argument of a generic call.
- A call through a variable is checked statically: `f(21)` knows the arity, every
  argument type and the result, so `f(true)` is `E0327` and `f(1, 2)` is `E0326`
  before the program runs. `E0311` catches a value of another signature.
- The generated C keeps the type: one `typedef` per distinct signature and one
  `&nv_fn_N` per function value, so nothing is passed as `void *` and the C
  compiler checks the signature a second time.
- Refusals, each with a code: calling something that is not a function (`E0439`),
  a call through a field (`E0441`, bind it to a `let` first), comparing two
  function values (`E0442`), `mut` inside a signature (`E0443`), and a generic
  function used as a value (`E0437`, it has no single signature).
- Guide, grammar artefact and measurements moved with the language: new guide
  section and rule, canonical `fnvalues.nv` example, re-pinned digest, overhead
  re-measured rather than estimated.
- This closes the last N10 row of the `INDEPENDENCE.md` §2 audit: a call-back no
  longer needs a closure.

N10f: monomorphised generics — type parameters on `struct`, `enum` and `fn`.

- `struct Pair<T>`, `enum Box<T>` and `fn swap<T>(p: Pair(T)) -> Pair(T)` are
  *templates*. A use instantiates one: `Pair(i64)`, `Box(bool)`, `swap(p)`. There
  is no `T` at run time — each instantiation is an ordinary struct, enum or
  function with the parameters replaced, generated once per combination of
  arguments, so the code generator never sees a type variable.
- A type is written `Name(types)` wherever a type may appear, the spelling
  `Option(i64)` already had; a value is built with a second parenthesis
  (`Pair(i64)(first: 1, second: 2)`, `identity(i64)(7)`). A call may omit the
  type arguments when the parameter types determine them. A template name on its
  own is not a type (`E0437`), a call that cannot be inferred is `E0436`, a
  second argument list on a non-generic function is `E0438` and a repeated type
  parameter is `E0435`.
- A template body is checked once per instantiation. A template that no call
  instantiates has never been checked, and the compiler says so (`W0003`) instead
  of implying that dead generic code was verified.
- Restrictions, all stated in `LANGUAGE.md`: no bounds on a type parameter, no
  generic arrays, no instantiation as an array element type, and the existing
  ownership rules still apply inside an instance (`Pair(str)` is `E0378`).
- Guide, grammar artefact and measurements moved with the language: new guide
  section and rule, canonical `generics.nv` example, re-pinned digest, overhead
  re-measured rather than estimated.
- The increment is narrower than the plan's N10f: function pointers move to
  N10g. `INDEPENDENCE.md` records the split.
- New `tools/audit_compiler_needs.py`: `INDEPENDENCE.md` §2 counts its own
  method, so the audit table is regenerated rather than remembered.

N10e: `defer`, labelled loops and `pub` — three statements about where control
leaves a scope.

- `defer { ... }` registers a block that runs when its block is left: on the
  normal path, on `return`, and on a `break`/`continue` that leaves it. Defers
  run last-first, before the block releases its arrays, once per iteration when
  written in a loop body, and they read their variables when they run. A
  deferred block cannot leave itself: `return`, `break`, `continue` and `?`
  inside one are `E0430`, and the braces are required (`E0201`).
- A loop may carry a label (`outer: for ...`) and a jump may name it. `break
  outer` leaves every loop between the jump and the target, releasing the arrays
  of each block it leaves; `continue outer` restarts the target and runs its
  increment. An unknown label is `E0431`, a label on something that is not a
  loop is `E0432`, and labels need edition 2.
- `pub fn` makes a function callable from another module, and a module that
  declares `edition 2;` exports **only** its `pub` functions (`E0433`). An
  edition-1 module keeps exporting every function, which is the released
  contract and does not change. `pub` on anything but a function is `E0434`.
- Guide, grammar artefact and measurements moved with the language: new guide
  section, canonical `control2.nv` example, re-pinned digest, overhead
  re-measured rather than estimated.
- The increment is narrower than the plan's original N10e: monomorphised
  generics and function pointers move to N10f, because a patch that changes
  types, code generation, the grammar and the guide for five features at once
  cannot be reviewed. The split is recorded in `INDEPENDENCE.md`.
- Not in this increment: generic parameters of any kind, function pointers,
  `defer` on a bare statement, a `defer` that can fail, and struct or enum
  visibility.

N10d: arrays of structs and slices — the first construct that hands out a view
of storage somebody else owns, so the ownership rules are the point.

- An array element may be a struct: `let points: [Point] = [Point(x: 1, y: 2)]`,
  `array_zeros(n)` zeroes every field, `for point in points` and `points[i]`
  copy the element, and `points[i] = point` replaces it whole. A field of an
  element is readable (`points[i].x`); a field write into an element is not part
  of edition 2 — copy, change, write back.
- `a[i..j]` is a **slice**: a read-only view of `j - i` elements. The bounds are
  checked when the slice is created — `0 <= i <= j <= len` — with two distinct
  runtime messages for a bad start and a bad end, and exit status 1.
- A slice has the type of the array it views, so `len`, `[i]` and passing it to
  a `[T]` parameter work; a slice is never passed to `mut [T]` (`E0423`), never
  written through (`E0422`) and never released by the block that binds it. The
  syntax names a variable after `[`, so a view of a temporary cannot be written.
- An array element may not be an enum yet (`E0424`), and `f64`, `void` or a
  nested array stay shape errors (`E0206`); an unknown element name is `E0203`.
  The array-literal and element-type messages now name structs, because they are
  element types.
- Guide, grammar artefact and measurements moved with the language: new guide
  section, canonical `slices.nv` example, re-pinned digest, overhead re-measured
  rather than estimated.
- Not in this increment: arrays of enums, slices of `str` text, field writes
  through an index, generic containers, function pointers, `defer`, labelled
  loops and `pub`.

N10c: `Option`, `Result` and `?` — the first construct in the language whose
meaning is a control-flow promise, so the promise is the thing that is tested.

- `Option(T)` (`Some(T)`, `None`) and `Result(T, E)` (`Ok(T)`, `Err(E)`) exist in
  edition 2 without a declaration. Type arguments are written in parentheses —
  `Option(i64)`, `Result(i64, str)` — never in angle brackets, because `<` is a
  comparison operator and `Option<i64>` would make the two readings depend on
  lookahead. Wrong arity is `E0412`, a value where a type belongs is `E0415`, and
  a user type with type arguments is `E0414`.
- An instantiation is an ordinary enum: values are built as
  `Result(i64, str)::Ok(3)`, patterns name the same instantiation, and
  exhaustiveness, duplicate arms and `_` work as in N10b.
- `?` is allowed in exactly one place: the initializer of a `let`, inside a
  function that returns the same enum. A failure is forwarded unchanged — the
  failure type must be identical (`E0411`), the success type may differ and is
  what the binding holds. `?` on a non-`Option`/`Result` is `E0410`; `?` outside
  a `let` initializer is `E0413`.
- Not in this increment: `match` as an expression, guards, generic declarations
  of the user's own, `Vec`/`HashMap`, owned payloads (`str` and array payloads
  stay refused until N11), and any conversion between failure types.
- Guide, grammar artefact and measurements moved with the language: new guide
  section, canonical `results.nv` example, re-pinned digest, overhead re-measured
  rather than estimated.

N10b: enums and `match` — the part of edition 2 that makes a value's shape
checkable instead of conventional.

- `enum Name { Variant, Variant(type, ...) }` with positional payloads of `i64`,
  `bool`, `f64`, a struct or another enum. `str` and array payloads are refused
  with `E0399` until the memory work (N11) gives them an ownership story.
- A value is always built through its enum — `Shape::Circle(2)`, `Shape::Dot` —
  and so is a pattern; the compiler tells a variant apart from a module call
  because it knows which names are enums.
- `match` is a **statement**: every arm names a variant (or `_`), bindings are
  typed copies of the fields, the scrutinee is evaluated once, arms run in source
  order, and the checker requires the arms to cover every variant (`E0396` names
  the missing ones). Duplicate arm `E0397`, unreachable arm after `_` `E0398`,
  unqualified pattern `E0392`, wrong binding count `E0394`, `match` on a non-enum
  `E0395`.
- An exhaustive `match` whose arms all return counts as a return, so
  `fn area(s: Shape) -> i64 { match s { ... } }` needs no trailing `return` and
  still passes the `E0306` analysis.
- The grammar artefact (token grammar and GBNF), its conformance classification
  and the model-facing guide moved with the language: new guide section, a
  canonical `enums.nv` example and a re-pinned digest, with the guidance overhead
  re-measured rather than estimated.
- Not in this increment: `match` as an expression, guards, literal or range
  patterns, matching on `i64` or `str`, `Option`/`Result`, `?` and generic enums.

N10a: edition 2 and structs — the first language feature since N7, delivered as
an additive edition rather than a break.

- `edition 2;` on the first line of a file opts into the new constructs; a file
  without it is edition 1 and compiles exactly as before (`E0372` names the
  declaration when an edition-2 construct appears in an edition-1 file).
- `struct Name { field: type }` with `i64`, `bool`, `f64` or another struct
  field. Named construction `Name(field: value, ...)` — named rather than
  `Name { ... }` because a brace after an expression would be ambiguous with the
  block of an `if` or a `for`. Field read `p.x`, field write `p.x = value`, copy
  on assignment, as a parameter and as a return value.
- Twelve new diagnostic codes, `E0370`–`E0381`, each with a fixture and a
  documented meaning; the array-shape rule moved to its own syntactic code
  `E0206` so that an unknown *name* in a type position (`E0203`) can be
  semantic while `[void]`, `[f64]` and `[[i64]]` stay syntactic. The grammar
  artefact and its conformance classification follow the same split.
- The model-facing guide gains an edition-2 section, a canonical example and a
  re-pinned digest; the guidance overhead is re-measured with a real tokenizer
  (`cl100k_base` 19 864 prompt tokens, `o200k_base` 19 941 over the task set),
  not estimated.
- Not in this increment: `enum`/`match`, `Option`/`Result`, slices, generics,
  `defer`, function pointers, `pub`, `str` and array fields, struct equality and
  printing a struct. A struct is a value with named fields and nothing else.

The independence plan is its own document: `INDEPENDENCE.md` specifies phases
N10–N18 for making Nava a language in its own right and a toolchain that needs
neither Python nor a C compiler, and reverses the two non-goals that excluded
self-hosting and a package manager. `PLAN.md` keeps the dependency order and
points at it.

- Six criteria (C1–C6) define what "a language" and "independent" mean, each
  with a probe: a normative specification, the bootstrap criterion, a static
  binary on a host without `python3`/`cc`/`ld`/libc, an ecosystem, proof on real
  programs, and the existing evidence discipline.
- The language feature list is an audit of the current compiler rather than a
  wish list: 4 394 lines, 13 modules and 141 functions in `nava_lang/`, with each
  Python construct it relies on mapped to the Nava feature and the phase that
  adds it. It shows Nava needs structs, sum types, generic containers, strings
  and slicing — and needs no closures, laziness, exceptions or garbage
  collector — to express its own compiler.
- Decisions D7–D12: editions instead of breaking changes, the bootstrap criterion
  decides feature scope, arena memory before any collector, the C backend as a
  trampoline that is removed at N16, independence demonstrated on a bare host,
  and the conformance set as the document of record.
- `tests/test_plan.py` guards the plan itself: every phase keeps its objective,
  tasks, gate and deliverable, the independence phases keep a negative control,
  a phase cannot be called delivered without a delivery record, the audit numbers
  match the tree, and the non-goal reversal stays stated.

No phase of N10–N18 is started; the plan says so in its own status line.

CI follow-up: the eight `required` jobs are red on GitHub-hosted runners, and the
study lane was green while measuring nothing.

- The red jobs: `tests/test_files.py` called `sandbox.select_backend("required")`
  at module level — a *selection*, which raises by design — where it needed a
  *probe*. On a runner without unprivileged user namespaces it aborted
  `tests/registry.py` at import time, before a single test ran.
- `nava_lang/sandbox.py` gains `namespaces_probe()`, which returns
  `{available, reason, detail}`; `namespaces_available()` is the boolean view, and
  the refusal message for `--isolation required` now carries the reason `unshare`
  gave. Nothing falls back silently: `required` still refuses (`exit 2`, `E0601`).
- The study and A/B harnesses refuse *before* spending a provider call when
  `--isolation required` cannot be satisfied (`exit 2`, stderr names
  `--isolation auto`), and `--plan-only` records `isolation_available`,
  `isolation_reason` and `isolation_backend`. An evaluator refusal is now a
  `provider_error` (`EvaluatorError`), never a wrong answer — a harness that
  compiled nothing can no longer report `pass@1 = 0.00` as a measurement.
- `tests/registry.py` prints the isolation backend in its OK line and accepts
  `--require-namespaces`, which fails instead of skipping when the jail cannot
  run (the message names the AppArmor sysctl).
- The workflow attempts to enable unprivileged user namespaces, reports which
  backend the runner has, and the isolation gate now gates: it demands the jail
  where namespaces exist, and where they do not it asserts the documented
  refusal and requires `--require-namespaces` to fail. The study and A/B lanes
  run `--isolation auto` and assert `evaluated_attempts` and `pass@1`, so a lane
  cannot be green while compiling nothing.
- `tests/test_release.py` is registered with the registry guard (it was not
  discovered at all) and pins these properties, including the lane assertions.

Phase N3 of `PLAN.md` follow-up: the study now runs against a hosted,
rate-limited provider as well as a local model, and the results are recorded.

- A rate limit is no longer recorded as a model failure. The client retries a
  429 or a transient 5xx after the delay the server asks for (`retry-after`,
  `x-ratelimit-reset-tokens`, parsed from `1.5`, `690ms`, `12s`, `1m26.4s`), with
  exponential backoff otherwise, up to `--retries`; every round records
  `http_attempts`. A daily cap (`DailyQuotaExceeded`) and a request larger than a
  whole per-minute output budget (`RequestTooLarge`) are reported immediately
  instead of being retried for an hour.
- Requests carry `User-Agent: nava-study/1.0`: Groq rejects the default
  `Python-urllib/3.13` with HTTP 403 and Cloudflare's `error code: 1010`. Extra
  headers go through `--header 'Name: value'`; only the names are recorded, and
  `Authorization` cannot be set that way.
- The summary line no longer says "no model was called" when a real run ends
  `not-measured` for another reason; it prints the reason.
- Hosted run: `qwen/qwen3.8-27b` on Groq, 10 tasks (6 public + 4 held out) ×
  `nava`/`c`/`cpp` × 6 attempts — 180 attempts, 138,078 tokens, 26 min,
  `pass@1` Nava 0.90 [0.82, 0.97], C 0.90 [0.82, 0.97], C++ 0.87 [0.77, 0.95].
  A second run (`openai/gpt-oss-20b`) hit the provider's 200,000 token/day cap
  and was correctly marked `not-measured` with exit 1; its aggregates are not
  quoted. Report: `bench/results/2026-10-28-n3-groq-report.md`.
- Tests: `RateLimitTests` (10) and `HeaderOptionTests` (3) pin the retry policy,
  the error classification, the user agent and the header handling; registry
  307 → 322.

Phase N3 of `PLAN.md` (real model evaluation). The result is marked
exploratory by the harness itself, and nothing in the record is estimated.

- `tools/local_model_server.py`: an OpenAI-compatible chat-completions server
  for a model on the same machine — no key, no egress, `usage` counts taken from
  the tokenizer that produced them, the model revision echoed in every answer,
  streaming refused with HTTP 400 rather than faked, and `transformers`/`torch`
  imported lazily so importing or testing the shim needs no ML stack.
- Held-out task sets work end to end: `eval/evaluate.py --task-file` evaluates a
  task from a file other than `eval/tasks.json` (before this, a held-out id was
  rejected before it could run), `eval/study.py` passes each task's own file to
  the evaluator, refuses a `--held-out` path inside the repository, and pins
  every task file by SHA-256 in `plan.task_files`.
- A real run: `HuggingFaceTB/SmolLM2-135M-Instruct` at revision
  `12fd25f7…`, 15 tasks (11 public + 4 held out) × `nava`/`c`/`cpp` × 2 attempts
  with 1 repair round — 90 attempts, 180 calls, 187,367 tokens, 33 minutes,
  `pass@1 0.00 [0.00, 0.00]` in all three languages, compile rate 0.00 Nava /
  0.53 C / 0.70 C++, and all 37 compiling answers failed every test case.
- `bench/results/2026-10-27-n3-report.md` is the model-specific report, including
  what the run does **not** establish: at 2 attempts per task and language the
  result is exploratory, a uniform 0.00 cannot order the languages, and reaching
  the plan's 30-attempt threshold on this machine would take about 10.4 hours.
  The records are `…-n3-study-smollm2-135m.json` and `…-n3-samples-…json`.
- Tests: `tests/test_local_model_server.py` (17) and four held-out-set tests in
  `tests/test_study.py`; registry 286 → 307, suite 381 → 402.

Phase N9 of `PLAN.md` (packaging, CI and release readiness).

- The project is now licensed under **Apache-2.0** (`LICENSE`, `NOTICE`,
  `license = "Apache-2.0"` plus `license-files` in `pyproject.toml`); the wheel
  ships the license text. The copyright line names "the Nava project
  contributors" because the repository records no legal entity.
- `pyproject.toml` declares the `nava` console script, no runtime dependencies
  and the Nava standard library as package data, so `pip install` yields a
  compiler that can `import math` from any directory.
- `tools/verify_release.py`: version agreement across `pyproject.toml`,
  `nava_lang/__init__.py` and `CHANGELOG.md`, the declared Python 3.10 syntax
  floor, wheel and sdist contents (standard library, console script,
  `License-Expression`, license file, no tests or benchmark records in the
  wheel), a credential scan of the tree and of both archives, and the SHA-256 of
  every artifact.
- `RELEASE.md`: versioning policy, requirements, a support matrix that
  distinguishes *exercised* (Linux x86_64, Python 3.10–3.13, gcc and clang)
  from *expected, not exercised* (macOS, Windows), a seven-step release
  checklist and an explicit list of what a release does not claim.
- CI: the required lane now covers Python 3.11 as well, runs the release gate
  and its tests, and a new `release` lane builds the artifacts, rejects a wheel
  whose standard library was stripped, then installs the wheel into a fresh
  environment and uses it from outside the checkout.
- Tests: `tests/test_release.py` (25). Suite 356 → 381 tests.

Phase N8 of `PLAN.md` (native and compiler performance). Nothing here weakens a
checked operation, and the costs are published next to the gains.

- `bench/profile.py` (`nava-profile/1`) measures a build by phase —
  `python_startup`, `import_and_cli`, `lex`/`parse`/`check`/`emit`,
  `frontend_total`, `cli_emit_c`, `cc`, `binary_startup`, `run` — on five
  workloads. `bench/compare.py` (`nava-comparison/1`) classifies two records
  with a stated rule: a phase is `improved` or `regressed` only when the median
  difference exceeds twice the larger standard deviation and 5% of the
  baseline; everything else is `inconclusive`, sizes are compared exactly and a
  missing phase is `missing`, never zero.
- `nava_lang/runtime.py`: the runtime is split into units and only the
  reachable closure is emitted, so the loop workload's translation unit drops
  from 23442 to 4909 bytes (−79%). `NAVA_FULL_RUNTIME=1` restores the old
  output for comparison. The whole selection is a pure function of the emitted
  body, and `tests/test_runtime.py` checks that the closure is closed.
- The command-line start-up path no longer imports what it does not use:
  `nava_lang/records.py` replaces `dataclasses` for the AST nodes and
  `subprocess`, `tempfile`, `shutil` and `nava_lang.sandbox` are imported
  inside the commands that need them. `nava --version` went from 74.4 to 49.0 ms
  and `nava emit-c` from 78.0 to 50.0 ms on the measured records.
- `tests/test_differential.py`: randomised differential gate — generated
  programs are compared, value by value and error by error, against a model of
  the documented semantics (overflow, division and remainder by zero,
  `INT64_MIN / -1`, truncation, evaluation order, short-circuiting, bounds,
  loop-scoped array release), each arithmetic case compiled twice (builtin and
  `-DNAVA_FORCE_PORTABLE`).
- `bench/negative_controls.py`: twelve deliberately reintroduced defects, each
  required to make its gate fail; 12/12 caught, every file restored and verified
  by SHA-256.
- Tests: `tests/test_runtime.py` (16), `tests/test_records.py` (13),
  `tests/test_bench_compare.py` (22), `tests/test_differential.py` (19).
  Suite 286 → 356 tests.

Phase N7 of `PLAN.md` (model-facing grammar and constrained generation). No
model was called: the accuracy comparison is `not-measured` and the claim
stays open.

- `guides/nava-1.md`: a compact, versioned model guide (`nava-guide/1`) pinned
  by `guides/manifest.json` (version, specification version, SHA-256, bytes,
  characters, lines). Eight canonical programs live in `guides/examples/` with
  their output recorded in `guides/examples.json`; `tests/test_guides.py`
  compiles and runs each one, and checks that the guide names every keyword,
  builtin and standard-library function.
- `eval/tokens.py` counts tokens only with a real tokenizer and reports
  `not-measured` with a reason otherwise — no estimate is ever substituted.
  `eval/overhead.py` measures the complete prompt per language (guide, task,
  instruction, total) and the difference between languages; two measured
  records are in `eval/measurements/` (`cl100k_base`, `o200k_base`): the Nava
  prompt carries 1374 more tokens than the C prompt for the same task.
- `eval/grammar.py` describes the language once and drives two back ends: the
  compiler's own token stream (conformance corpus) and GBNF text for an
  inference engine. `eval/gbnf.py` parses the emitted GBNF back and recognises
  text with it, so the artefact is executed by the tests instead of trusted.
- `eval/constrained.py`: an optional adapter that builds the grammar fields for
  `llamacpp` and `vllm`, marked `experimental` with `verified_against: null`.
  `eval/ab.py` runs the same tasks in two arms whose prompts are identical and
  whose request payloads differ only in that field, and
  `eval/check_ab_record.py` refuses a claim attached to an unmeasured record.
- `eval/feedback.py`: structured repair feedback (`nava-feedback/1`) with the
  stage kept apart (`parse`, `compile`, `tests`), capped at 2048 bytes, dropped
  items counted. `eval/study.py` gains `--feedback structured|text`,
  `syntax_accepted` per round and repair accounting that cannot turn a repaired
  attempt into a first-attempt success.
- Tests: `tests/test_guides.py` (25), `tests/test_grammar.py` (24),
  `tests/test_ab.py` (17). Suite 219 → 286 tests.

Phase N6 of `PLAN.md` (modules and standard-library foundations).

- Modules: `import name` loads `name.nv`, and its functions are called as
  `name::function`. Resolution has exactly two locations — the project root
  (the entry file's directory, or `--root DIR`) and the packaged standard
  library — with no search-path list. A local module may not shadow a standard
  one (`E0360`), a module may not define `main` (`E0357`), an import may not be
  repeated (`E0355`), the graph must be acyclic (`E0353`) and the resolved path
  must stay inside the root after `realpath`, so a symbolic link cannot escape
  (`E0356`). Calling a function of another module without the prefix is `E0358`,
  prefixing a module that was not imported is `E0359`, and an unused import is
  `W0002`. Every module is merged into one `Program` and one C translation unit.
- Versioned standard library in Nava source, installed as package data:
  `math` (`abs`, `min`, `max`, `sign`, `gcd`, `sum`), `sort` (`is_sorted`,
  `insertion_sort`, `binary_search`) and `text` (`is_ascii`, `is_valid_utf8`,
  `code_points`, `sequence_width`, `is_continuation`). Each declares
  `module_version`, and the compiler refuses a copy it was not built against
  (`E0361`).
- Text: a `str` is a NUL-terminated byte sequence. `bytes_len` and `byte_at`
  count and read bytes, `chars_len` counts Unicode code points and stops on
  bytes that are not valid UTF-8, and `read_file` returns bytes unchanged
  instead of replacing them. Source literals are still validated as UTF-8
  (`E0111`, `E0104`, `E0106`, `E0105`).
- Capability-limited file API: `read_file(path)` and `write_file(path, text)`
  are compiled with the grants given at build time
  (`--allow-read DIR`, `--allow-write DIR`) and have none by default. Both
  resolve the argument with `realpath` and accept it only inside a granted
  subtree, so `..`, absolute paths and symbolic links are all covered. Reads
  are bounded at 1048576 bytes per file, 4194304 bytes per run and 64 calls;
  a file containing a NUL byte is refused. `run --workdir DIR` makes the
  sandbox's single writable directory explicit, and the boundary is tested
  under `--isolation required`.
- Command line: `--root`, `--allow-read`, `--allow-write` on `check`,
  `emit-c`, `build` and `run`; `--workdir` on `run`; `E0506`/`E0507` for a
  capability or working directory that is missing or not a directory.
- Diagnostics: `E0352`–`E0361`, `E0506`, `E0507` and `W0002` (13 new codes,
  92 in total).
- Tests: `tests/test_modules.py` (22), `tests/test_stdlib.py` (11),
  `tests/test_files.py` (18) and `tests/fixtures/modules.json` (8 multi-file
  cases: 6 error, 1 warning, 1 run). Suite 168 → 219 tests; fixtures 103 → 112.
- `LANGUAGE.md`: "Modules and the standard library", "Text, bytes and UTF-8",
  "Files and capabilities", the grammar (`import`, `::`), the diagnostic tables
  and the three new limits.


Phase N5 of `PLAN.md` (control flow and numerical coverage).

- `for i in a..b` iterates a half-open `i64` range; `for x in values` iterates
  an array (variable, parameter or literal) by value. Both bounds are evaluated
  once. `break` and `continue` work in `for` and `while`, and release the arrays
  owned by the iteration they leave. The loop variable is a fresh read-only
  local (`E0351`), and a statement after a jump is reported as `W0001`.
- `f64` as IEEE-754 binary64: literals (`1.5`, `1e300`), `+ - * /`, unary `-`,
  the full comparison set with the NaN rules, `to_f64`/`to_i64` with checked
  conversions and no undefined behaviour, `read_f64`, and `print` support.
  There is no implicit `i64`/`f64` conversion (`E0345`, `E0311`), `%` is not
  defined for floats, and `f64` arrays do not exist.
- Documented float printing: the shortest form that round-trips, fixed notation
  for decimal exponents in [-4, 15] and exponent notation outside it, `.0` for
  integral values, and `nan`/`inf`/`-inf`. It matches Python's `repr`, which is
  how the tests check 113 values, including subnormals and `DBL_MAX`.
- Locale: the program never calls `setlocale`, so `.` is always the decimal
  separator; `read_f64` validates tokens itself and rejects `inf`, `nan`,
  hexadecimal floats, commas and trailing garbage.
- Diagnostics: `E0343`–`E0351` (9 new codes, 79 in total).
- `LANGUAGE.md`: sections for `for`/`break`/`continue` and for floating-point
  numbers, grammar, limits (`MAX_FLOAT_DIGITS`), the new builtins and updated
  diagnostic tables. 8 new executable documentation examples.
- Tests: `tests/test_control.py` (19) and `tests/test_float.py` (17), as well
  as `tests/spec_example.py`, which checks that a documented example really
  prints what `LANGUAGE.md` claims. Fixtures: 16 negative, 3 positive and 1
  warning added. Public task `mean_abs`. Tests: 132 -> 168.

Phase N4 of `PLAN.md` (arrays and ownership).

- Arrays: `[i64]`, `[bool]` and `[str]`, created by an array literal or
  `array_zeros(n)`, read with `len(a)`, `a[i]` and written with `a[i] = value`.
  The length is fixed at creation; there is no resizing, copying or nested
  array.
- Ownership: one owner per array, no copies (`E0331`, `E0332`), no array
  results (`E0330`), read-only `[T]` and mutable `mut [T]` parameters that
  cannot outlive the call (`E0333`, `E0336`, `E0342`), and statement-scoped
  temporaries for literals used as arguments.
- Checked runtime: allocation length (negative, above `MAX_ARRAY_ELEMENTS`,
  or unallocatable) and every index are checked, with deterministic
  `nava runtime error:` messages and exit status 1. Release is emitted by the
  compiler: exactly one `free` per binding on the normal path and before every
  `return`. A runtime error releases by process exit, which `SECURITY.md`
  states and a test asserts.
- Diagnostics: `E0112`, `E0113` and `E0328`–`E0342` (17 new codes, 70 in
  total), each with a fixture, a `LANGUAGE.md` table row and a title.
- `LANGUAGE.md`: specification sections for arrays, ownership, storage release
  and the new builtins; grammar, limits (`MAX_ARRAY_ELEMENTS`) and the
  "not implemented" list updated. 13 new executable documentation examples.
- Tests: `tests/test_arrays.py` (27 tests: type rules, runtime behaviour
  against independent references, release accounting with
  `-DNAVA_TRACK_ALLOCS`, sanitizer runs, a deliberately disabled bounds check),
  plus array fixtures (19 negative, 4 positive) and the `sort_numbers` and
  `sieve_count` public tasks with reference answers. Tests: 104 -> 132.
- `tests/registry.py` also fails when the address/undefined-behaviour
  sanitizers are unavailable, so the memory-safety gate cannot pass by being
  skipped.

Phase N3 of `PLAN.md` (real model evaluation). **No model was run**: this
release adds the harness and the honesty rules, not a result.

- `eval/study.py`: paired study over tasks × languages × attempts. Per attempt
  it records the prompt hash (text only with `--store-raw`), extraction
  provenance, compilation, failing tests, first-attempt versus final success,
  every repair round with its diagnostics, time to a correct answer, wall time,
  time to first token, provider-reported tokens and the isolation backend.
  Aggregates carry percentile bootstrap intervals with a fixed seed.
- `eval/providers.py`: `openai-compatible` (key from an environment variable,
  redacted from errors, never recorded), `replay` (JSONL keyed by prompt
  SHA-256) and `mock` (deterministic, measures nothing).
- Self-classification: a study is `exploratory` below 10 tasks or 30 attempts
  per task and language, with the reasons printed; `empirical` is refused
  otherwise. `model_measurements.status` stays `not-measured` unless a model
  was actually called and every attempt produced a compile-and-test result.
- `--plan-only` prints the predeclared plan (tasks, order policy, request
  parameters, expected scale) without calling anything.
- `eval/prompt.py` refactored into `build_parts`/`build_prompt`: the guide, the
  task and the output instruction are counted separately, so guide overhead is
  recorded rather than assumed.
- `eval/extraction.py`: fenced-block extraction with recorded provenance
  (`tagged-fence`, `untagged-fence`, `whole-text`, `whole-text-fallback`); a
  foreign fence is never silently rewritten.
- `eval/check_study_record.py`: re-derives the claim from a saved record -- the
  schema, the thresholds behind `empirical`, `measured` only for a non-mock run
  with evaluated attempts and no provider errors, a reason for `not-measured`,
  and aggregates that match the attempt list. CI runs it on a mock record and on
  a deliberately unreachable endpoint, so "it ran" can never stand in for
  "it measured".
- `tests/test_study.py` (27 tests): classification thresholds, aggregates with
  missing values, bootstrap determinism, provider error handling, credential
  redaction, replay hits and misses, extraction cases, end-to-end mock study,
  repair accounting, raw storage, and five record-validation rejections.
- `tests/registry.py` tracks the new classes. Tests: 77 -> 104.

## N2 (phase N2)

Phase N2 of `PLAN.md` (isolated build and execution).

- `nava_lang/sandbox.py`: two backends and three policies. `namespaces` uses
  `unshare` for user, mount, PID, network, IPC and UTS namespaces with a
  read-only minimal root (read-only `/usr`, a few `/etc` files, four `/dev`
  nodes, a private 16 MiB `/tmp`, one writable directory at `/work`) and a
  fresh `/proc`. `rlimits` provides process-group cleanup, `RLIMIT_CPU`,
  `RLIMIT_AS`, `RLIMIT_FSIZE`, `RLIMIT_NOFILE`, `RLIMIT_NPROC`, `RLIMIT_CORE`,
  a wall-clock deadline and bounded output capture, and states in every report
  that the filesystem, the network and the process tree are **not** isolated.
- Fail-closed policy: `--isolation {auto,required,off}` on `nava run` and
  `eval/evaluate.py`. `required` refuses to run when namespaces are unavailable
  (exit 2, new diagnostic `E0601`); `auto` warns and labels the gap; `off` is an
  explicit opt-in to the unsafe mode. Backend detection is a real `unshare`
  probe, not a version check.
- Bounded output collection replaces unbounded `capture_output` for untrusted
  candidates: each stream is capped, a program that exceeds the cap is killed,
  and truncation is recorded per test case.
- No surviving children: the whole process group is killed on timeout or
  overflow, then re-checked; a survivor is reported and `nava run` fails with
  `E0602`.
- `nava run` gained `--isolation` and `--memory`; its default is `auto`, so the
  best available isolation is used, with the program's own exit status, `124`
  on timeout and 1 for runtime errors preserved.
- `eval/evaluate.py`: compilation and execution both go through the sandbox,
  compiler paths are resolved to real absolute paths (inside the jail
  `PATH`/`/etc/alternatives` chains do not resolve), and each report records
  `isolation.backend`, `network_denied`, `host_files_hidden`, `gaps` and the
  limits in force.
- `SECURITY.md`: threat model, per-platform support table, what is deliberately
  not protected, and the probe-to-expectation map used by the gate.
- `tests/test_sandbox.py` (15 tests): busy loop, sleeping program, output flood,
  memory exhaustion, `fork`+`sleep` child, writes outside the working directory,
  connection to a local listener, fail-closed `required` in the library and in
  the evaluator, honest labels and CLI integration. Tests: 62 -> 77.
- `tests/registry.py` tracks the new test class; `E0601`/`E0602` are registered
  and documented with the tests that cover them.

## N1 (phase N1)

Phase N1 of `PLAN.md` (language specification and diagnostics).

- Stable diagnostic codes: 51 registered codes (`E01xx` lexer, `E02xx` parser,
  `E03xx` semantics, `E04xx` limits, `E05xx` command line, `W00xx` warnings) in
  `nava_lang/diagnostics.py`. An existing code never changes meaning; building
  an unregistered code raises instead of silently printing it.
- Source ranges: `NavaError` and `Diagnostic` carry a start location and an
  exclusive end. Identifier diagnostics point at the identifier, not at the
  statement keywords (`let`, `fn`).
- `--format json`: one JSON document on stdout with `status`, `path` and
  `diagnostics[]` entries containing `code`, `severity`, `title`, `message`,
  `path`, `range` and `hint`. Human output is unchanged apart from the
  `severity[CODE]` prefix and an optional `hint:` line.
- New `W0001` warning for unreachable statements, reported on stderr and in the
  JSON document; the program still compiles and runs.
- `nava_lang/limits.py`: `MAX_NESTING = 200`, `MAX_LITERAL_DIGITS = 19`,
  `I64_MIN`/`I64_MAX` as the single source of truth, enforced by the parser and
  the checker. Pathologically nested input, unary chains and long
  left-associative expression trees now raise `E0401` instead of a Python
  `RecursionError`; the CLI converts any escaped `RecursionError` into `E0402`.
- `LANGUAGE.md` rewritten as specification version 1: EBNF grammar, lexical
  rules, evaluation order, checked arithmetic, exit statuses, warnings,
  documented limits and the complete code table. Its `nava` examples are
  compiled by the test suite.
- `tests/test_spec.py` (23 tests) and `tests/fixtures/`: 47 negative fixtures
  asserting code, line, column and message; 12 positive fixtures asserting
  stdout and exit status; one warning fixture; fuzzing over random token soup
  and mutations of valid programs; documentation/limit/exit-status conformance.
- `tests/registry.py` extended with fixture counts, diagnostic-code coverage
  (every code needs a fixture, a documented test and a `LANGUAGE.md` entry) and
  the new test classes. Tests: 37 -> 62.

## N0 (phase N0)

Phase N0 of `PLAN.md` (measurement rig and test integrity), plus the packaging
parts of N9 that the N0 gate depends on.

- `pyproject.toml`: installable package `nava-lang` with the `nava` console
  script; no third-party runtime dependencies. `nava` is now executable.
- `tests/registry.py`: registry guard for test methods, task fixtures and
  required compilers. A missing test, a changed case count or a host without
  `cc`/`c++` fails the guard; skipped runtime tests are no longer a pass.
- `eval/reference.py`: independent reference implementations for all eight
  public tasks, with `--check` validating every fixture in `tasks.json`.
- `eval/evaluate.py`: C candidate support alongside Nava and C++; reports now
  record `candidate.language`, `candidate.compile_command` and a labelled
  `candidate.arithmetic` (checked-i64 versus unchecked-signed).
- `eval/tasks.json`: four additional public tasks (`is_prime`, `digit_sum`,
  `max_of_three`, `collatz_steps`), each with six cases checked against the
  reference; `eval/solutions/gcd.c` added.
- `bench/run.py`: separated phases (Nava front end, native compilation,
  zero-iteration startup, main runs), warm-up policy, rotated run order,
  standard deviation and a `--json PATH` machine-readable record
  (`nava-bench/1`) with environment, compiler versions and flags. Missing
  model measurements are recorded as `null` with status `not-measured`.
- `bench/results/`: first recorded benchmark run (Linux x86-64, GCC 14.2.0).
- `.github/workflows/ci.yml`: required lane (Python 3.10/3.12/3.13 x GCC/Clang)
  running the registry guard, the full suite, the cross-language check and the
  fixture reference; a lane proving the guard fails without compilers; a
  packaging lane; a benchmark-record lane.
- Tests: 31 -> 37, including the C candidate, the four new tasks, the
  benchmark record schema and four registry-guard tests.
