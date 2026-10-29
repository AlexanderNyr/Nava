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

## Doc blocks

`///` lines directly above a `fn` are that function's contract: a summary line,
then `pre:`, `post:`, `cost:`, one or more `example:` lines whose last statement
must be `true`, and `error:` lines shaped `statements => what the program prints
before it stops`. `nava doc` turns them into reference pages. A `///` block
anywhere else — over a `struct`, or inside a body — is `E0207`.

```nava
edition 2;

/// `double(x)` — twice `x`.
///
/// pre: none
/// post: the result is `2 * x`
/// cost: O(1)
/// example: double(2) == 4
pub fn double(x: i64) -> i64 {
    return x * 2
}
```

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
- `test` — `test::expect_true`, `test::expect_i64`, `test::expect_str`,
  `test::run`, `test::summary`, `test::passing`: assertions and a runner, so a
  program can test itself and return the failure count as its exit status.
- `fmt` — `fmt::repeat`, `fmt::pad_left`, `fmt::pad_right`, `fmt::pad_center`,
  `fmt::clip`, `fmt::i64_aligned`, `fmt::f64_fixed`: a column width in **bytes**,
  the side to align to and the number of decimal places, which is what printing a
  table needs. Every one of them answers with a `str`, so they compose with
  `str_concat`.

Two aligned columns, without a format language to learn:

```nava
import fmt;

fn main() {
    print(str_concat(fmt::pad_right("apples", 8), fmt::i64_aligned(1234, 6, true)))
    print(str_concat(fmt::pad_right("ratio", 8),
                     fmt::pad_left(fmt::f64_fixed(1.0 / 3.0, 3), 6)))
}
```

For a single-file answer, do not write imports; use the built-ins above.

A function of an imported module is a value with its module written on it, so a
test body defined here can be handed to `test::run`:

```nava
edition 2;

import math;
import test;

fn abs_tests() -> i64 {
    let failures = 0
    failures = failures + test::expect_i64(math::abs(-7), 7, "abs of -7")
    return failures
}

fn main() -> i64 {
    return test::summary("math", test::run("abs", abs_tests))
}
```

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

Fields are `i64`, `bool`, `f64`, `str`, a function type, an array, or another
struct or enum. A `str` field is fine: a string is immutable and lives for the
whole run, so copying the struct copies a pointer nobody writes or frees. An
**array field** is fine too, and it is copied: `let b = a` gives `b` its own
buffer, so `a.items[0] = 9` cannot be seen through `b`, and the block that owns
a value frees its storage when it ends. A struct may not contain itself, and a
struct cannot be printed or compared with `==`: print a field, not the struct.
Construction names every field exactly once, as in `Point(x: 1, y: 2)`; there is
no positional form.

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

## Edition 2: `Option`, `Result` and `?`

`Option(T)` (`Some(T)`, `None`) and `Result(T, E)` (`Ok(T)`, `Err(E)`) are part of
edition 2 without a declaration. Write the type arguments in parentheses, never
in angle brackets: `Option(i64)`, `Result(i64, str)`.

```nava
edition 2;

fn parse_positive(n: i64) -> Result(i64, str) {
    if n > 0 { return Result(i64, str)::Ok(n) }
    return Result(i64, str)::Err("not positive")
}

fn doubled(n: i64) -> Result(i64, str) {
    let value = parse_positive(n)?      # on Err the failure is returned now
    return Result(i64, str)::Ok(value * 2)
}

fn main() {
    match doubled(4) {
        Result(i64, str)::Ok(v) => { print(v) }
        Result(i64, str)::Err(e) => { print(e) }
    }
}
```

`?` is allowed in exactly one place: the initializer of a `let`, inside a
function that returns the same enum with the same failure type. The success type
may differ — `let flag = check()?` where `check()` returns `Result(bool, str)`
and the caller returns `Result(i64, str)` is fine. A failure is forwarded
unchanged, never converted, and the rest of the function does not run.

## Edition 2: arrays of structs and slices

An array element may be `i64`, `bool`, `str`, `String`, a **struct** or an
**enum** — anything that is a value, including a value that owns storage of its
own. An element that owns storage is copied when it enters the array and released
with the array. `for point in points` copies each element, and `points[i].x` reads
a field of an element. `array_zeros(n)` gives
every element its zero value, so it needs an element type whose zero bits are a
value: `i64`, `bool`, `f64`, a struct of those, or an enum whose first variant is
payload-free. `let names: [str] = array_zeros(2)` is `E0425`, because an
all-zero string is a null pointer, not an empty one; write `["", ""]`.

`a[i..j]` is a **slice**: a read-only view of `j - i` elements, not a copy. The
bounds are checked when the slice is made — `0 <= i <= j <= len`, otherwise
`nava runtime error: slice start …` or `slice end …` and exit status 1. A slice
has the type of the array it views, so it can be passed to a `[T]` parameter and
measured with `len`; it cannot be passed to a `mut [T]` parameter (`E0423`) and
its elements cannot be written (`E0422`). A view sees the owner's writes, and
`for p in points[1..3]` iterates a slice (the bounds are evaluated once).

```nava
edition 2;

struct Point { x: i64, y: i64 }

fn total(points: [Point]) -> i64 {
    let sum = 0
    for point in points {
        sum = sum + point.x + point.y
    }
    return sum
}

fn main() {
    let points: [Point] = [Point(x: 1, y: 2), Point(x: 3, y: 4)]
    print(total(points))
    print(points[1].y)
    let head = points[0..1]
    print(len(head))
    print(head[0].x)
}
```

An element is a value: `let p = points[0]` copies it, and `points[i] = p`
replaces the whole element. To change one field, copy the element, change the
copy and write it back with `points[i] = p`.

## Edition 2: `Vec(T)`

An array keeps the length it was created with; a `Vec(T)` grows. `T` is
`i64`, `bool`, `str`, `f64`, `String`, or a struct or enum — an element that owns
storage is copied in on `vec_push` and on `vec_set`, and a read hands back a copy.
A collection is not an element: `Vec(Vec(i64))` is `E0206`.

`let v: Vec(i64) = vec_new()` creates it, and the block that writes the `let`
releases it. A vector is never copied but it can be **moved** — into another
binding, into an assignment, and out of a function as its return value
(`fn make() -> Vec(i64)`) — and **stored** in a field, a payload or a type
argument, where the holder keeps a deep copy. `vec_new()` without the annotation
is `E0429`.

| Call | Type | Meaning |
|---|---|---|
| `vec_len(v)` | `i64` | elements it holds now |
| `vec_capacity(v)` | `i64` | elements before the next growth |
| `vec_push(v, x)` | `void` | append, growing if needed |
| `vec_pop(v)` | `Option(T)` | remove and return the last, or `None` |
| `vec_get(v, i)` | `T` | read element `i`, checked |
| `vec_set(v, i, x)` | `void` | write element `i`, checked |

A `Vec(T)` parameter reads the vector, a `mut Vec(T)` parameter may grow it and
the caller sees the new length. `vec_push`, `vec_pop` and `vec_set` change the
vector, so their first argument must be a local or a `mut Vec(T)` parameter
(`E0444`); a `mut Vec(T)` argument must be a vector created in the calling
function, not a borrow (`E0333`). Growth doubles the capacity from four, so pushes are amortised
constant time; an out-of-range `vec_get`/`vec_set` ends the program, and
`vec_pop` on an empty vector is `None`, not an error. `v[i]`, `v[0..1]` and
`print(v)` are refused (`E0329`, `E0421`, `E0323`): index with `vec_get`, slice
nothing, print the elements. `for x in v` reads the
length once, before the first turn, and binds the element type; the loop
variable is read-only. There is no insert, no remove and no shrink.

```nava
edition 2;

fn fill(xs: mut Vec(i64), n: i64) {
    let i = 0
    while i < n {
        vec_push(xs, i * i)
        i = i + 1
    }
}

fn main() {
    let xs: Vec(i64) = vec_new()
    fill(xs, 5)
    print(vec_len(xs))
    print(vec_capacity(xs))
    let sum = 0
    for x in xs { sum = sum + x }
    print(sum)
    match vec_pop(xs) {
        Option(i64)::Some(last) => { print(last) }
        Option(i64)::None => { print(-1) }
    }
}
```

## Edition 2: `HashMap(K, V)` and `HashSet(T)`

A map and a set answer "is this key here" without a search. `K` is a key — an
`i64`, a `bool`, a `str` or a `String`, and nothing else (`E0447`) — and `V` is
any value an element may be, a struct included, but not a collection (`E0206`).
`let m: HashMap(i64, str) = map_new()` and `let s: HashSet(str) = set_new()`,
because `map_new()`/`set_new()` have nowhere else to learn their types
(`E0448`). Both are **moved**, never copied, like a `Vec(T)`: into another
binding, into an assignment, out of a function as `-> HashMap(i64, str)`.

| Call | Type | Meaning |
|---|---|---|
| `map_len(m)` / `set_len(s)` | `i64` | how many entries |
| `map_contains(m, k)` / `set_contains(s, x)` | `bool` | is the key there |
| `map_get(m, k)` | `Option(V)` | the stored value, or `None` |
| `map_set(m, k, v)` | `void` | insert or overwrite |
| `map_remove(m, k)` / `set_remove(s, x)` | `bool` | delete, reporting whether it was there |
| `set_add(s, x)` | `void` | insert |
| `map_clear(m)` / `set_clear(s)` | `void` | drop every entry |

A mutating call needs a writable local or a `mut HashMap(K, V)` parameter
(`E0444`), exactly like `vec_push`. `map_get` is **total**: a missing key is
`None`, not a run-time error. The container keeps a **copy** of every key and
value it stores, so growing a `String` after `map_set` leaves the entry alone,
and `for k in m` / `for x in s` binds a copy in read-only form. The order of
iteration is the order of the table, not sorted: two runs of the same sequence
of operations agree, two different sequences need not.

```nava
edition 2;

fn main() {
    let counts: HashMap(str, i64) = map_new()
    map_set(counts, "a", 1)
    map_set(counts, "b", 2)
    map_set(counts, "a", 3)          # overwrite
    print(map_len(counts))           # 2
    match map_get(counts, "a") {
        Option(i64)::Some(value) => { print(value) }    # 3
        Option(i64)::None => { print(0) }
    }
    print(map_contains(counts, "z"))  # false
    let total = 0
    for key in counts { total = total + bytes_len(key) }
    print(total)                     # 2
    print(map_remove(counts, "b"))   # true
    let seen: HashSet(i64) = set_new()
    set_add(seen, 7)
    set_add(seen, 7)
    print(set_len(seen))             # 1
}
```

## Edition 2: `String` and the text functions

A `str` is immutable and lives for the run; `String` is the owner of bytes a
program assembles a piece at a time. `let s: String = string_new()` creates it
and the block releases it. A `String` is never copied but it can be **moved** —
into another binding, into an assignment, and out of a function as its return
value (`fn label(n: i64) -> String`) — and **stored** in a field, a payload, a
type argument or an **element**, where the holder keeps a deep copy. `mut String`
lets a function write into the caller's builder.

| Call | Type | Meaning |
|---|---|---|
| `string_push(s, x)` | `void` | append a `str` |
| `string_push_byte(s, b)` | `void` | append one byte, `0..255`, never `0` |
| `string_len(s)` | `i64` | bytes written so far |
| `string_capacity(s)` | `i64` | bytes before the next growth |
| `string_clear(s)` | `void` | forget the bytes, keep the capacity |
| `string_text(s)` | `str` | the finished text |
| `str_concat(a, b)` | `str` | joined |
| `str_from_i64(n)`, `str_from_f64(x)` | `str` | the text `print` would write |
| `str_slice(x, i, j)` | `str` | bytes `i .. j`, both checked |
| `str_index_of(x, n)` | `i64` | byte offset or `-1` |
| `str_starts_with(x, p)`, `str_ends_with(x, p)` | `bool` | prefix, suffix |
| `str_trim(x)` | `str` | ASCII whitespace removed |
| `str_replace(x, from, to)` | `str` | every occurrence |
| `str_split(x, sep, out)` | `void` | appends the fields to a `Vec(str)` |
| `str_join(parts, sep)` | `str` | the vector joined by `sep` |

`str_split` fills a vector rather than returning one, because the separator
work is done piece by piece: `let parts: Vec(str) = vec_new()` then
`str_split(text, ",", parts)`. Built `str` values live in an arena; a call
frees its own region when it returns, unless it returns `str` or writes it
through a `mut` parameter. Lengths and slices count **bytes**, not characters
(`chars_len` counts code points and validates UTF-8).

```nava
edition 2;

fn join(words: Vec(str), sep: str) -> str {
    let out: String = string_new()
    let first = true
    for word in words {
        if !first { string_push(out, sep) }
        string_push(out, word)
        first = false
    }
    return string_text(out)
}

fn main() {
    let parts: Vec(str) = vec_new()
    str_split("a,bb,ccc", ",", parts)
    print(vec_len(parts))
    print(str_join(parts, "-"))
    print(join(parts, "/"))
    print(str_concat("na", "va"))
    print(str_replace("a-b-c", "-", "+"))
}
```

## Edition 2: moves

A `Vec(T)` and a `String` own one buffer, so a program hands it on instead of
copying it. Three places accept a move — a `let`, an assignment, and `return` —
and the value comes from a constructor (`vec_new`, `string_new`), a call that
returns the same type, or a local of that type. After a move the source binding
is empty and every later use of it is `E0445`. A parameter is a borrow: returning
one or assigning over one is `E0446`.

```nava
edition 2;

fn counted(n: i64) -> Vec(i64) {
    let v: Vec(i64) = vec_new()
    let i = 0
    while i < n {
        vec_push(v, i)
        i = i + 1
    }
    return v
}

fn label(n: i64) -> String {
    let s: String = string_new()
    string_push(s, "n=")
    string_push(s, str_from_i64(n))
    return s
}

fn main() {
    let xs: Vec(i64) = counted(3)
    let ys: Vec(i64) = xs
    print(vec_len(ys))
    let text: String = label(4)
    print(string_text(text))
}
```

The example prints `3` and `n=4`. An owning value returned by a call must be
bound: `counted(3)` used as a statement is `E0428`, because the buffer would
have no owner.

## Edition 2: collections of owning elements

An array or a vector may hold elements that own storage. The container owns every
element: a value is copied in, a read hands back a copy, and the release frees every
element it still holds. `vec_pop` moves the element out instead of copying it.

```nava
edition 2;

struct Bag {
    items: Vec(i64)
}

fn make(n: i64) -> Bag {
    let xs: Vec(i64) = vec_new()
    vec_push(xs, n)
    return Bag(items: xs)
}

fn main() {
    let bags: [Bag] = [make(1), make(2)]
    let keep: Bag = bags[0]
    bags[0] = make(3)
    print(vec_len(keep.items))
    let words: Vec(String) = vec_new()
    let s: String = string_new()
    string_push(s, "x")
    vec_push(words, s)
    string_push(s, "!")
    print(string_text(vec_get(words, 0)))
    print(string_text(s))
}
```

The example prints `1`, `x` and `x!`: `keep` kept its own copy, the vector kept
the string it was given, and the source went on growing. A read costs a copy —
`vec_get(words, 0)` allocates — so read once into a `let` when the value is used
more than once. What is still refused is a collection inside a collection:
`[[i64]]`, `[Vec(i64)]` and `Vec(Vec(i64))` are `E0206`.

## Edition 2: storing a value that owns storage

A `Vec(T)` or a `String` may be a field, a payload or a type argument, and the
holder keeps a **deep copy**: building a struct copies the buffer, so the value it
was copied from and the copy go their own ways. Reading a field is a borrow, so
`let out: Vec(i64) = bag.items` is `E0428` and `vec_push(bag.items, 1)` is
`E0444`; write the whole field instead (`bag.items = other`). An **element** may
own storage too, and every read of one copies it — see
[the next section](#edition-2-collections-of-owning-elements).

```nava
edition 2;

struct Bag {
    items: Vec(i64),
    text: String
}

fn main() {
    let xs: Vec(i64) = vec_new()
    vec_push(xs, 1)
    let s: String = string_new()
    string_push(s, "note")
    let bag: Bag = Bag(items: xs, text: s)
    vec_push(xs, 2)
    print(vec_len(bag.items))
    print(string_len(bag.text))
    print(vec_len(xs))
}
```

The example prints `1`, `4` and `2`: the bag kept its own copy while the source
vector kept growing.

## Edition 2: `defer`, labelled loops and `pub`

`defer { ... }` runs when the enclosing block is left — on the normal path, on
`return`, and on a `break` or `continue` that leaves the block. Two `defer`s in
one block run last-first, a deferred block runs before the block releases its
arrays, it sees the variables as they are when it runs, and it cannot leave
itself: `return`, `break`, `continue` and `?` inside one are `E0430`.

A loop may carry a label and a jump may name it: `break outer` leaves every loop
between the jump and `outer`, and `continue outer` restarts it (running its
increment). A label on anything that is not a loop is `E0432`; a jump naming a
loop that is not enclosing is `E0431`.

`pub fn` makes a function of a module callable from another module. A module
that declares `edition 2;` exports only its `pub` functions (`E0433` otherwise);
a module that does not declare edition 2 still exports everything.

```nava
edition 2;

fn find(values: [i64], target: i64) -> i64 {
    let found = 0 - 1
    outer: for i in 0..3 {
        for j in 0..3 {
            if values[j] == target {
                found = i * 3 + j
                break outer
            }
        }
    }
    return found
}

fn main() {
    let values: [i64] = [4, 5, 6]
    defer { print(0 - 1) }
    print(find(values, 5))
    let i = 0
    while i < 2 {
        defer { print(100 + i) }
        print(i)
        i = i + 1
    }
}
```

Output: `1`, `0`, `101`, `1`, `102`, `-1`.

## Edition 2: generic declarations

A `struct`, an `enum` or a `fn` may take **type parameters** in angle brackets,
and every use instantiates them with concrete types in round brackets:

* declare `struct Pair<T> { first: T, second: T }`, `enum Box<T> { Full(T),
  Empty }`, `fn swap<T>(p: Pair(T)) -> Pair(T)`;
* write a type as `Pair(i64)`, `Box(bool)`, `Result(i64, str)` — always
  `Name(types)`, never `Name<types>`;
* build one with a second parenthesis: `Pair(i64)(first: 1, second: 2)`,
  `Box(i64)::Full(5)`;
* a call may omit the type arguments when the parameter types determine them:
  `swap(p)`. A call that writes them puts them first: `identity(i64)(7)`. A type
  parameter that nothing in the signature mentions cannot be inferred (`E0436`).

A declaration with type parameters is **not** a type: `let p: Pair` is `E0437`,
and so is `Pair(first: 1, second: 2)`. Two instantiations with different
arguments are different, unrelated types. There is no `T` at run time: the body
of a template is compiled once per instantiation, so `fn same<T>(a: T, b: T) ->
bool { return a == b }` compares `i64` in one copy and `bool` in another, and a
template that no call instantiates is reported as unchecked (`W0003`).

Not available yet: bounds on a type parameter (an operator used on `T` must
exist for every instantiation you actually call), arrays of `T`, and an
instantiation as an array element type (`[Pair(i64)]` is `E0206`).

```nava
edition 2;

struct Pair<T> {
    first: T,
    second: T
}

fn swap<T>(p: Pair(T)) -> Pair(T) {
    return Pair(T)(first: p.second, second: p.first)
}

fn largest<T>(a: T, b: T) -> T {
    if b > a {
        return b
    }
    return a
}

fn main() {
    let p: Pair(i64) = Pair(i64)(first: 1, second: 2)
    let q = swap(p)
    print(q.first)
    print(q.second)
    print(largest(3, 9))
    print(largest(2.5, 1.5))
}
```

Output: `2`, `1`, `9`, `2.5`.

## Edition 2: function values

A function is a value whose type is its signature, written `fn(i64) -> i64` and
`fn() -> void` for one that returns nothing:

* name a declared function where a value is expected — `let f = double` — and
  the type is inferred from its signature; `let f: fn(i64) -> i64 = double` says
  it out loud;
* call through a variable like any function: `f(21)`. The checker knows the
  arity and every argument type, so `f(true)` is `E0327` before the program runs;
* a signature fits everywhere a value fits: pass it, return it, keep it in a
  struct field or a variant payload, put it in `Option(fn(i64) -> i64)`, and use
  it as the type argument of a generic call.

The value is the address of a function; there are no closures and nothing is
captured, so a function defined inside another one does not exist. A call goes
through a **name only**: `ops.op(x)` is `E0441`, so bind the field first with
`let f: fn(i64) -> i64 = ops.op`. Function values cannot be compared
(`E0442`), printed (`E0323`) or stored in an array, and a generic function is
not a value at all (`E0437`) — write a small non-generic wrapper to pass one on.

```nava
edition 2;

fn double(x: i64) -> i64 {
    return x * 2
}

fn apply(f: fn(i64) -> i64, x: i64) -> i64 {
    return f(x)
}

fn main() {
    let f: fn(i64) -> i64 = double
    print(f(21))
    print(apply(double, 5))
    print(apply(f, 6))
}
```

Output: `42`, `10`, `12`.

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
11. `?` goes after a call inside a `let`, and only when the function returns the
    same `Result`/`Option`; type arguments are written `Option(i64)`, never
    `Option<i64>`.
12. A slice `a[i..j]` is read-only and borrowed: bind it with `let`, pass it to a
    `[T]` parameter, never write through it and never hand it to `mut [T]`.
13. `defer` takes a block, never a bare statement, and nothing inside it may
    leave the block (`return`, `break`, `continue`, `?` are all `E0430`).
14. Type parameters are declared with `<>` and instantiated with `()`:
    `fn swap<T>(p: Pair(T)) -> Pair(T)`, `Pair(i64)(first: 1, second: 2)`. A
    template name on its own is not a type (`E0437`).
15. A function type is written `fn(i64) -> i64`, with the result always spelled
    out. A call through a value goes through a name only: bind `ops.op` to a
    `let` before calling it (`E0441`).
16. A struct field or enum payload may be a `str`, an array, a `Vec(T)` or a
    `String`, and a value that owns storage is copied wherever it crosses a
    binding — `let`, assignment, field write, `match` binding, `return` — with a
    deep copy, so the two values never share a buffer. Such a value may also be an
    array or vector **element**: it is copied in, copied out on every read, and
    released with the container. `array_zeros(n)` needs an element type whose zero
    value is real, so `[str]`, `[String]` and a struct holding a `str` are `E0425`.
17. A `Vec(T)` and a `String` are moved, never copied: `let b: Vec(i64) = a`,
    `b = a` and `return a` hand the buffer on, and afterwards `a` is dead
    (`E0445`). A parameter is a borrow, so returning one or assigning over one is
    `E0446`, and an owning result that is not bound is `E0428`.
