# Nava 0.1 — model-facing guide (`nava-1`, `nava-guide/1`)

Compact guide to the implemented language. It is tied to specification version 1
(`LANGUAGE.md`); every example in `guides/examples/` is compiled and run by
`tests/test_guides.py`, and the digest of this file is what an evaluation record
cites.

Write **one complete program**. It must define `fn main()` with no parameters.
One statement per line; a newline or `;` separates statements. Comments start
with `#` or `//`.

## Types

`i64`, `bool`, `str` (a byte string, printed as-is), `f64`, and the fixed-length
arrays `[i64]`, `[bool]`, `[str]`. There are no implicit conversions:
`to_i64(x: f64)` and `to_f64(x: i64)` convert explicitly. Integer arithmetic is
checked and a runtime overflow is an error, not a wrapped value.

## Values and variables

```nava
let count = 0              # i64, inferred
let ratio: f64 = 1.5       # annotation optional but checked
let name = "nava"          # str
let empty: [i64] = array_zeros(4)
```

`true` and `false` are the only `bool` literals. `let` declares a mutable
local. Assignment is `count = count + 1`; the type may
not change. Variables are block-scoped. Array elements are assigned with
`a[i] = value` and read with `a[i]` (bounds are checked at run time).

## Statements

```nava
if n < 0 {
    print("negative")
} else if n == 0 {
    print("zero")
} else {
    print("positive")
}

while n > 0 {
    n = n / 10
}

for i in 0..n {          # i64 range, half-open: 0, 1, ..., n-1
    if i == 3 { continue }
    if i > 8 { break }
}

for item in a {          # over an array, read-only
    print(item)
}
```

Conditions must be `bool`; `while` and `if` take a bare expression, no
parentheses. `break` and `continue` act on the innermost loop.

## Functions

```nava
fn gcd(a: i64, b: i64) -> i64 {
    while b != 0 {
        let r = a % b
        a = b
        b = r
    }
    return a
}

fn greet() {             # no return type means void
    print("hi")
}

fn main() {
    print(gcd(48, 18))
}
```

Functions may be called before their definition and may be recursive. Every
path of a non-`void` function must return a value. A parameter declared
`fn fill(a: mut [i64])` may write elements; a plain `[i64]` parameter is a
read-only view. An array is created by a `let` binding, has exactly one owner,
is released when its block ends, and is never copied (`E0331`) or returned
(`E0330`); a borrow cannot outlive the call, so there are no lifetimes.

## Built-in functions

- `print(value)` — one `i64`, `bool`, `f64` or `str`, plus a newline.
- `read_i64()`, `read_f64()` — one whitespace-separated value from stdin.
- `len(a: [T]) -> i64`, `array_zeros(n: i64) -> [i64]`.
- `to_i64(x: f64) -> i64`, `to_f64(x: i64) -> f64`.
- `bytes_len(s)`, `chars_len(s)`, `byte_at(s, i)` — byte length, Unicode code
  point count (an error if the bytes are not valid UTF-8) and one byte.
- `read_file(path)`, `write_file(path, text)` — only if the caller was granted
  a directory, otherwise a runtime error.

## Operators

`+ - * / %` (arithmetic, `/` truncates toward zero, `%` keeps the sign of the
left operand), `< <= > >= == !=`, `! && ||`. Precedence from tightest:
unary, `* / %`, `+ -`, comparisons, `== !=`, `&&`, `||`. `&&` and `||`
short-circuit; operands are evaluated left to right. Strings compare with
`==`/`!=` only and cannot be concatenated.

## Modules and the standard library

A project may hold several files: `import name` before the first `fn` makes
`name::function(...)` available, resolved as `name.nv` next to the entry file.

```nava
import math

fn main() {
    print(math::abs(-7))
    print(math::gcd(48, 18))
}
```

- `math` — `math::abs`, `math::min`, `math::max`, `math::sign`, `math::gcd`,
  `math::sum`.
- `sort` — `sort::is_sorted`, `sort::insertion_sort`, `sort::binary_search`.
- `text` — `text::is_ascii`, `text::is_valid_utf8`, `text::code_points`,
  `text::is_continuation`, `text::sequence_width`.

For a single-file answer, do not write imports; use the built-ins above.

## Edition 2: structs

A file that starts with `edition 2;` may declare and use structs and enums. A file without
that line is edition 1 and is unchanged: do not mix the two.

```nava
edition 2;

struct Point {
    x: i64,
    y: i64
}

fn manhattan(p: Point) -> i64 {
    let ax = p.x
    if ax < 0 { ax = 0 - ax }
    let ay = p.y
    if ay < 0 { ay = 0 - ay }
    return ax + ay
}

fn main() {
    let a = Point(x: 3, y: 0 - 4)
    print(manhattan(a))     # 7
    a.x = 10                # fields are assigned in place
    let b = a               # a struct is copied: b is an independent value
    b.y = 1
    print(a.y)              # -4
    print(b.y)              # 1
}
```

Fields are `i64`, `bool`, `f64` or another struct. `str` and array fields do not
exist yet, a struct may not contain itself, and a struct cannot be printed or
compared with `==`: print a field, not the struct. Construction names every
field exactly once, as in `Point(x: 1, y: 2)`; there is no positional form.

## Edition 2: enums and `match`

An enum is a type whose values are exactly its variants. A variant carries a
fixed, positional list of fields; `match` is a **statement** that must cover
every variant.

```nava
edition 2;

enum Op {
    Add(i64, i64),
    Mul(i64, i64),
    Halt
}

fn apply(acc: i64, op: Op) -> i64 {
    match op {
        Op::Add(a, b) => { return acc + a + b }
        Op::Mul(a, b) => { return acc * a * b }
        Op::Halt => { return acc }
    }
}

fn main() {
    let acc = 1
    acc = apply(acc, Op::Add(2, 3))    # 6
    acc = apply(acc, Op::Mul(2, 3))    # 36
    print(acc)
}
```

Rules that matter: a value is always built through its enum, as in
`Op::Add(2, 3)` or `Op::Halt`; a pattern is always qualified too, as in
`Op::Add(a, b)`, and `_ => { ... }` is the wildcard arm and must come last; a
binding is a copy of the field. A `match` that misses a variant does not
compile. There is no `match` expression, no `Option`/`Result`, no guards and no
matching on `i64` values.

## Rules that most often cost a round trip

1. `main` takes no parameters and returns `void` or `i64`.
2. A function that returns a value must `return` on every path.
3. Types never convert implicitly; annotate with `to_i64(1.9)`, not `1.9` as an
   `i64`.
4. `a.length` does not exist; use `len(a)`.
5. Only a call may stand alone as a statement.
6. Integer literals fit in `i64`; `-9223372036854775808` is the one allowed
   negative literal of that magnitude.
7. `f64` prints with the shortest exact round-trip form, so `print(1.0)` writes
   `1.0`.
8. Division or overflow errors stop the program with a non-zero status; do not
   "fix" them with wrapping arithmetic.
9. Structs need `edition 2;` on the first line, and only a field can be printed,
   not the struct itself.
10. A `match` covers every variant of the enum or adds a final `_` arm; a
    variant is written `Enum::Variant`, never bare.
