# Nava 0.1: Language Reference

**Specification version 1**, matching Nava 0.1.0. This document describes the
implemented behaviour; it is not a promise of future compatibility. Where this
document and the compiler disagree, that is a bug in one of them:
`tests/test_spec.py` fails if a documented example does not compile, if a
registered diagnostic code is missing here, or if a documented limit differs
from `nava_lang/limits.py`.

Every example here is executable. Full programs live in
[`tests/fixtures/positive/`](tests/fixtures/positive/) and invalid ones in
[`tests/fixtures/negative/`](tests/fixtures/negative/); both sets are run by
`python3 -m unittest tests.test_spec -v`.

## Program structure

A program is a sequence of functions. It must define `fn main()` with no
parameters and either `void` (the default) or `i64` as its return type.
Functions may be called before their definition and may be recursive.

```nava
fn add(a: i64, b: i64) -> i64 {
    return a + b
}

fn main() {
    let x = add(2, 3)
    print(x)
}
```

A `main` function without a return value exits with status 0. The result of
`main -> i64` is converted to an exit status modulo 256. A function without
`-> type` returns `void`. Every path through a non-`void` function must return
a value. The return analysis is conservative: even `while true` is not treated
as a guaranteed return.

## Lexical structure

- **Whitespace** — spaces, tabs, form feeds and vertical tabs separate tokens
  and are otherwise ignored.
- **Newlines** — a newline separates statements. Inside `(` `)` and `[` `]` a
  newline is ignored, so an expression, an argument list or an array literal
  may continue on the next line only while it is inside one of those
  delimiters. Outside them an expression cannot be split across lines.
- **Comments** — `#` or `//` to the end of the line.
- **Keywords** — `fn`, `let`, `if`, `else`, `while`, `for`, `in`, `break`,
  `continue`, `return`, `import`, `true`, `false`, `mut` (only in a parameter
  type, see [Arrays and ownership](#arrays-and-ownership)).
- **Identifiers** — ASCII letters, digits and `_`, not starting with a digit.
- **Types** — `i64`, `bool`, `str`, `f64`, `void`, and the array types `[i64]`,
  `[bool]`, `[str]`. Any other name in a type position is `E0203`; so are
  `[void]`, `[f64]` and a nested array such as `[[i64]]`.
- **Integer literals** — decimal digits, optionally separated by single
  underscores (`1_000_000`). A literal must fit in `i64`
  (-9223372036854775808 to 9223372036854775807). The single negation
  `-9223372036854775808` is accepted because its positive counterpart does not
  fit; `-` applied to any larger magnitude is `E0108` or an overflow at run
  time.
- **Floating-point literals** — digits, a point and more digits, with an
  optional decimal exponent: `1.5`, `0.25`, `1e300`, `1.5e-7`. At least one
  digit must follow the point, so `1.` is not a literal. Underscores are not
  used in floating-point literals. A literal that is too long (`40` digits) or
  that is outside the `f64` range is `E0346`; `1e999` is rejected rather than
  silently becoming infinity. A range `0..3` is two integers and `..`, not a
  float.
- **Strings** — double-quoted, byte for byte as written in the file, with JSON escapes (`\"`, `\\`, `\n`, `\t`,
  `\uXXXX`, and the other JSON escapes). A literal newline inside a string is
  `E0103`; an invalid escape is `E0104`; NUL is `E0105`; an unpaired surrogate
  is `E0106`.
- **Operators** — `( ) [ ] { } : , + - * / % = ! < > ;` and the two-character
  `->`, `..`, `::`, `<=`, `>=`, `==`, `!=`, `&&`, `||`. Delimiters must match: an unmatched
  `)` is `E0109`, an unmatched `]` is `E0112`, an unclosed `(` is `E0110` and
  an unclosed `[` is `E0113`.

## Grammar

The grammar below is the whole language. Statements are separated by a newline
or `;`; a separator is not required before `}`.

```text
program        = [ edition ] { import } { function } end_of_file ;
import         = "import" IDENT ;
function       = "fn" IDENT "(" [ parameters ] ")" [ "->" type ] block ;
parameters     = parameter { "," parameter } ;
parameter      = IDENT ":" [ "mut" ] type ;
type           = "i64" | "bool" | "str" | "void"
               | "[" ( "i64" | "bool" | "str" ) "]" ;
block          = "{" { separator } { statement { separator } } "}" ;
statement      = let | assignment | element_assignment | member_assignment
               | if_statement | while_statement | for_statement | break_statement
               | continue_statement | return_statement | call ;

let            = "let" IDENT [ ":" type ] "=" expression ;
assignment     = IDENT "=" expression ;
element_assignment = IDENT "[" expression "]" "=" expression ;
if_statement   = "if" expression block [ "else" ( if_statement | block ) ] ;
while_statement= "while" expression block ;
for_statement  = "for" IDENT "in" iterable block ;
iterable       = expression ".." expression | expression ;
break_statement= "break" ;
continue_statement = "continue" ;
return_statement = "return" [ expression ] ;
separator      = NEWLINE | ";" ;

expression     = logical_or ;
logical_or     = logical_and { "||" logical_and } ;
logical_and    = equality { "&&" equality } ;
equality       = ordering { ( "==" | "!=" ) ordering } ;
ordering       = additive { ( "<" | "<=" | ">" | ">=" ) additive } ;
additive       = multiplicative { ( "+" | "-" ) multiplicative } ;
multiplicative = unary { ( "*" | "/" | "%" ) unary } ;
unary          = ( "-" | "!" ) unary | postfix ;
primary        = NUMBER | STRING | "true" | "false" | "(" expression ")"
               | IDENT | call | element | array_literal | construction ;
postfix        = primary { "." IDENT } ;
element        = IDENT "[" expression "]" ;
array_literal  = "[" [ expression { "," expression } ] "]" ;
call           = [ IDENT "::" ] IDENT "(" [ arguments ] ")" ;
arguments      = expression { "," expression } ;
```

All binary operators are left-associative. Only a call may be used as a
statement (`E0313` otherwise). Every `import` must appear before the first
`fn` (`E0354`).

## Types and values

| Type | Values |
|---|---|
| `i64` | Integers from -9223372036854775808 to 9223372036854775807. |
| `bool` | `true` and `false`. Conditions require `bool`; integers are not converted. |
| `str` | A NUL-terminated byte sequence; source literals are valid UTF-8. Bytes and code points are different units — see [Text, bytes and UTF-8](#text-bytes-and-utf-8). |
| `f64` | IEEE-754 binary64, the same values as C `double`. |
| `void` | No function result; not a variable or parameter type. |
| `[i64]`, `[bool]`, `[str]` | A fixed-length sequence of one element type. The length is set when the array is created and never changes. |

Strings can be passed to functions, returned, printed, written to a file and
compared for equality. Equality compares bytes without Unicode normalization.
`bytes_len` and `chars_len` give the size, `byte_at` reads one byte.
Concatenation, string indexing and string input are not implemented: `len`
accepts an array, not a `str`. There are no implicit conversions between
`i64`, `bool` and `str`, and no conversions between element types.

## Variables and scope

```nava
fn main() {
    let count = 0
    let total: i64 = 10
    count = count + 1
    total = total + count
    print(total)
}
```

- `let` introduces a mutable local; the type is inferred from the initializer
  or given by an annotation. A mismatch is `E0311`.
- Function parameters are mutable locals of their declared type.
- Variables are block-scoped. Redeclaring a name in the same block is `E0307`;
  shadowing in a nested block is permitted.
- The initializer is evaluated before the new variable is declared: the
  right-hand `x` in `let x = x + 1` inside a nested block refers to the outer
  variable.
- There are no global variables and no forward declarations of variables;
  reading an unknown name is `E0308`.

## Expressions and evaluation order

From highest to lowest precedence: unary `-`, `!`; `*`, `/`, `%`; `+`, `-`;
`<`, `<=`, `>`, `>=`; `==`, `!=`; `&&`; `||`.

- Arithmetic and ordering comparisons accept only `i64` (`E0319`).
- `==` and `!=` compare values of the same type: `i64`, `bool` or `str`
  (`E0320`).
- `!` accepts `bool`; `&&` and `||` accept `bool` and short-circuit: the
  right-hand side is evaluated only when needed.
- Arguments and subexpressions are evaluated strictly left to right, including
  calls with side effects. The generated C materialises each subexpression in a
  temporary, so no C evaluation-order rule can change this.

```nava
fn probe(value: bool) -> bool {
    print("evaluated")
    return value
}

fn main() {
    print(false && probe(true))
    print(true || probe(false))
    print(true && probe(true))
}
```

Output: `false`, `true`, `evaluated`, `true`. The first two lines never call
`probe`; see `tests/fixtures/positive/short_circuit.nv`.

## Arrays and ownership

An array is a fixed-length sequence of one element type. It is created with an
array literal or with `array_zeros(n)`, it belongs to exactly one binding, and
its elements are read and written with a checked index.

```nava
fn main() {
    let scores: [i64] = [10, 20, 30]
    print(len(scores))
    print(scores[0] + scores[2])
    scores[1] = scores[0] * 2
    print(scores[1])
    let empty: [i64] = []
    let words: [str] = ["alpha", "beta"]
    let flags: [bool] = [true, false]
    print(len(empty))
    print(words[1])
    print(flags[0])
}
```

Output: `3`, `40`, `20`, `0`, `beta`, `true`. See
`tests/fixtures/positive/arrays_basic.nv`.

### Creating an array

- An array literal `[a, b, c]` creates a new array. Every item must have the
  same element type (`E0334`); the length is the number of items.
- An empty literal needs an annotation, because its element type cannot be
  inferred: `let a: [i64] = []` is valid, `let a = []` is `E0335`.
- `array_zeros(n)` allocates `n` elements of type `i64`, all zero. The length is
  checked at run time: a negative length, a length above the documented limit
  or an allocation failure is a runtime error, never a truncated array. There is
  no `[bool]` or `[str]` allocation: use an `[i64]` array of `0` and `1` flags,
  or a literal.
- The length never changes. Resizing, appending, slicing and concatenation are
  not part of Nava 0.1.

```nava
fn main() {
    let n = read_i64()
    let counts: [i64] = array_zeros(n)
    let i = 0
    while i < n {
        counts[i] = i * i
        i = i + 1
    }
    print(len(counts))
}
```

### Reading and writing elements

- `a[i]` reads one element; the result has the element type. Only a variable
  can be indexed (`E0329`), and the index must be `i64` (`E0328`).
- `a[i] = value` writes one element; the value must have the element type
  (`E0312`).
- Every access is bounds checked at run time. A negative index or an index at
  or beyond the length is a runtime error:
  `nava runtime error: array index 5 is out of bounds for length 3`, exit
  status 1. There is no way to disable the check, and there are no raw
  pointers in the language.
- Element writes are allowed only on an array this function owns. Writing
  through a read-only parameter is `E0336`.

### Passing an array: borrows, not copies

A parameter of type `[T]` is a **read-only borrow**; a parameter of type
`mut [T]` is a **mutable borrow**, so a function can fill or sort the caller's
array. `mut` on a non-array parameter is `E0341`: scalar parameters are already
mutable locals inside the function, so `mut` would only suggest a copy-back that
does not happen.

```nava
fn fill(a: mut [i64], value: i64) {
    let i = 0
    while i < len(a) {
        a[i] = value
        i = i + 1
    }
}

fn total(a: [i64]) -> i64 {
    let sum = 0
    let i = 0
    while i < len(a) {
        sum = sum + a[i]
        i = i + 1
    }
    return sum
}

fn main() {
    let a: [i64] = [0, 0, 0]
    fill(a, 7)
    print(total(a))
}
```

Output: `21`. `fill` writes through the borrow, so the caller sees the result;
nothing is copied in or out.

The rules that make this safe are small enough to state completely:

1. **One owner.** An array is created by a `let` binding (from a literal or
   `array_zeros`) and is released when that binding's block ends.
2. **No copies.** `let b = a` is `E0331` and `a = b` is `E0332`: an array name
   always refers to the same storage for its whole life. Copying would either
   duplicate the buffer or create two owners, and Nava has neither.
3. **No array results.** A function cannot return an array (`E0330`): the
   result would outlive the storage that produced it. Write into a `mut [T]`
   parameter instead.
4. **A borrow cannot be extended.** A parameter is a borrow, so it cannot be
   returned, stored in another array, or handed on as a `mut` borrow (`E0333`).
   Nothing in the language can keep a borrow alive after the call, which is why
   there are no lifetime annotations.
5. **A mutable borrow is exclusive.** The same variable cannot be passed to two
   `mut` parameters of one call (`E0342`).
6. **A temporary lives until the end of its statement.** An array literal or
   `array_zeros(n)` used as an argument, as in `total([1, 2, 3])`, is released
   at the end of the statement that created it.

### Storage release

Release is deterministic and emitted by the compiler, not left to a garbage
collector:

- On the normal path, and before every `return` (including a `return` from
  inside a loop or a nested block), each owned array is released exactly once.
  An array declared inside a loop body is released on every iteration.
- A runtime error such as an out-of-bounds index stops the program with exit
  status 1. The storage is then reclaimed by the operating system when the
  process exits; it is not released by a `free` call. This is a deliberate
  trade-off recorded in `SECURITY.md`, not an oversight.
- Compiling the generated C with `-DNAVA_TRACK_ALLOCS` makes the runtime report
  `nava: live arrays at exit: N` on `stderr`. The test suite uses it to show
  that every normal and early-return path releases everything.

```nava
fn first_positive(a: [i64]) -> i64 {
    let i = 0
    while i < len(a) {
        if a[i] > 0 {
            let pair: [i64] = [a[i], a[i] * 10]
            return pair[1]
        }
        i = i + 1
    }
    return 0
}

fn main() {
    print(first_positive([0 - 1, 3]))
}
```

Output: `30`. `pair` is owned by the loop body and released before the early
`return` leaves the function. See
`tests/fixtures/positive/arrays_borrow.nv`.

Sorting and a sieve of Eratosthenes are in
`tests/fixtures/positive/arrays_sort.nv` and
`tests/fixtures/positive/arrays_sieve.nv`.

## Statements and control flow

```nava
fn sign(x: i64) -> str {
    if x < 0 {
        return "negative"
    } else if x == 0 {
        return "zero"
    } else {
        return "positive"
    }
}

fn main() {
    let x = 0
    while x < 3 {
        x = x + 1
    }
    print(sign(-5))
    print(sign(0))
    print(sign(4))
    print(x)
}
```

Braces are required. `return value` is used in a function with a result; a
`void` function may use a bare `return` or reach the end of its body. `while`
re-evaluates its condition on every iteration, including any calls it contains.

### `for`, `break` and `continue`

`for` iterates over a half-open range or over an array. The loop variable is a
fresh, **read-only** local in the loop's own scope: assigning to it is `E0351`,
because the loop rebinds it and a write would silently desynchronise the
iteration.

```nava
fn total(a: [i64]) -> i64 {
    let sum = 0
    for value in a {
        sum = sum + value
    }
    return sum
}

fn main() {
    for i in 0..3 {
        print(i)
    }
    for i in 3..3 {
        print(99)
    }
    print(total([4, 5, 6]))
    for word in ["alpha", "beta"] {
        print(word)
    }
    let kept = 0
    for i in 0..10 {
        if i % 3 != 0 {
            continue
        }
        if i > 6 {
            break
        }
        kept = kept + i
    }
    print(kept)
}
```

Output: `0`, `1`, `2`, `15`, `alpha`, `beta`, `9`. See
`tests/fixtures/positive/for_loops.nv`.

- `for i in a..b` counts `i` from `a` up to but never including `b`. Both
  bounds must be `i64` (`E0349`) and are evaluated **once**, before the first
  iteration, even when they are function calls. If `b <= a` the body never
  runs; there is no reversed or inclusive range, and to include `n` write
  `0..n + 1`.
- `for x in values` visits the elements of an array in index order. `values`
  is any array expression — a variable, a read-only or mutable parameter, or a
  literal such as `for x in [1, 2, 3]`, whose array lives until the loop ends.
  `x` is a **copy** of the element of its element type; writing to the array
  still needs an index loop, `for i in 0..len(a) { a[i] = ... }`.
- `break` leaves the innermost `for` or `while`; `continue` starts its next
  iteration. Outside a loop they are `E0343` and `E0344`. In a `while` loop
  `continue` re-evaluates the condition; in a `for` loop it advances to the next
  range value or element.
- A statement after `return`, `break` or `continue` in the same block is
  unreachable and is reported as the warning `W0001`; the program still
  compiles and runs.
- Arrays owned by the loop body are released when `break` or `continue` leaves
  the iteration, so a loop that jumps out early leaks nothing.
- The return analysis is conservative and unchanged: `break`, `continue` and
  even `while true` are not counted as guarantees that a function returns, so a
  non-`void` function still needs a `return` on every path.

Nava 0.1 has no `for ... in` over anything but ranges and arrays, no labelled
break or continue, and no exceptions or first-class functions.

## Floating-point numbers

`f64` is IEEE-754 binary64, the type C calls `double`. It is a separate type
from `i64`, and **no conversion between them is implicit**: adding an `i64` to
an `f64` is `E0345`, storing an `i64` in an `f64` is `E0311`, and the fix is
always an explicit call to `to_f64` or `to_i64`.

```nava
fn mean(a: f64, b: f64) -> f64 {
    return (a + b) / 2.0
}

fn main() {
    let x = 1.5
    let y = 0.25
    print(x + y)
    print(x * y)
    print(-x)
    print(mean(1.0, 2.0))
    print(0.1 + 0.2)
    print(to_f64(7))
    print(to_i64(2.9))
    print(to_i64(-2.9))
}
```

Output: `1.75`, `0.375`, `-1.5`, `1.5`, `0.30000000000000004`, `7.0`, `2`,
`-2`. See `tests/fixtures/positive/floats.nv`.

### Operators and rounding

`+`, `-`, `*`, `/` and unary `-` are defined for two `f64` values (or one, for
unary `-`) and follow IEEE-754 exactly:

- rounding is **round to nearest, ties to even**, which is the default and is
  never changed by Nava — the program cannot alter the floating-point
  environment;
- overflow gives `inf` or `-inf`, division by zero gives `inf`, `-inf` or `nan`
  (for `0.0 / 0.0`), and none of these is an error;
- `0.1 + 0.2` is `0.30000000000000004`, and `1.0e300 * 1.0e300` is `inf`;
- `%` is **not** defined for `f64` (`E0319`): `to_i64` and integer remainder, or
  an explicit formula, express the intent without ambiguity.

Comparisons `<`, `<=`, `>`, `>=`, `==`, `!=` follow IEEE-754 as well, which
means every comparison with `nan` is false except `!=`, which is true:

```nava
fn main() {
    let zero = 0.0
    let none = zero / zero
    print(none)
    print(none == none)
    print(none != none)
    print(none < 0.0)
    print(1.0 / zero)
}
```

Output: `nan`, `false`, `true`, `false`, `inf`.

### Conversions

- `to_f64(x: i64) -> f64` is exact for every integer with at most 53
  significant bits. Above that it rounds to nearest-even, so
  `to_f64(9007199254740993)` is `9007199254740992.0`. Both are documented
  behaviour, not a bug.
- `to_i64(x: f64) -> i64` truncates toward zero: `2.9` is `2`, `-2.9` is `-2`,
  `0.5` is `0`. A `nan` and any value outside `[-2^63, 2^63)` are runtime
  errors (`to_i64 of NaN`, `to_i64 of a value outside the i64 range`), exit
  status 1. The conversion never has undefined behaviour, unlike a C cast.

### Printing and reading

`print(value)` accepts an `f64` and writes the shortest decimal form that reads
back as exactly the same number, using fixed notation when the decimal
exponent is between -4 and 15 inclusive and exponent notation outside that
range, with `.0` appended so a float never looks like an integer. The
exceptional values print as `nan`, `inf` and `-inf`. This is the same rule
Python uses for `repr` of a float, which is how the tests check it:

```nava
fn main() {
    print(1.0)
    print(100.0)
    print(0.1)
    print(-0.0)
    print(1.0e-7)
    print(1e300)
}
```

Output: `1.0`, `100.0`, `0.1`, `-0.0`, `1e-07`, `1e+300`.

`read_f64() -> f64` reads one whitespace-delimited decimal number: an optional
sign, digits, an optional point with digits, and an optional decimal exponent.
`inf`, `nan`, hexadecimal floats, a decimal comma and trailing garbage are
errors, not accepted inputs. A value too large for `f64` is an error; a value
too small rounds to zero. `read_f64` never consumes more than one token.

**Locale:** Nava never calls `setlocale`, so the C locale stays in effect for
the whole program. Input and output always use `.` as the decimal separator and
never group digits, whatever `LC_ALL`, `LC_NUMERIC` or `LANG` are set to. The
test suite checks this by running a program with a comma-decimal locale
requested.

There are no `f64` arrays, no `f64` literals for `inf`/`nan` (produce them
arithmetically), no fused multiply-add, no `sqrt` or other mathematical
functions, and no decimal or arbitrary-precision type.

## Modules and the standard library

A program may be split across files. A **module** is one UTF-8 source file that
contains only functions; `import name` loads `name.nv` and makes its functions
callable as `name::function`. There are no namespaces beyond that, no
re-exports and no visibility keyword: everything a module defines is callable
through the module name, and nothing of another module is visible without it.

Resolution is deterministic and has no configurable search list:

1. `<project root>/<name>.nv`, where the project root is the directory of the
   file being compiled, or the directory given with `--root`;
2. the standard library that ships with the compiler,
   `nava_lang/stdlib/<name>.nv`.

A local file may not shadow a standard module (`E0360`); that is an error rather
than a precedence rule, so `import math` always means the same library. A module
additionally may not define `main` (`E0357`) and may not be imported twice
(`E0355`). The import graph must be acyclic (`E0353`, which names the cycle). The
resolved file is checked with `realpath`, so a symbolic link that leaves the
project root is `E0356`.

A set of three files, where `stats.nv` is local and the other three modules come
from the standard library:

```text
examples/modules/main.nv      import math, sort, text, stats
examples/modules/stats.nv     a local module; it imports math itself
nava_lang/stdlib/math.nv      abs, min, max, sign, gcd, sum
nava_lang/stdlib/sort.nv      is_sorted, insertion_sort, binary_search
nava_lang/stdlib/text.nv      is_ascii, is_valid_utf8, code_points, ...
```

`python3 nava run examples/modules/main.nv` prints the sorted values, the sum,
a search result, the mean and two text answers; `tests/test_modules.py` checks
the exact output. Inside a module, its own functions are called without a
prefix; a call into any other module must be prefixed. Calling a function of
another module without the prefix is `E0358` (the message names the module),
prefixing a module that was not imported is `E0359`, and an import that no
qualified call uses is the warning `W0002`.

The standard library is versioned. Every module in it declares the version it
implements, and the compiler refuses a module that does not match
(`E0361`):

```nava module:geometry
fn module_version() -> i64 {
    return 1
}

fn twice(x: i64) -> i64 {
    return x * 2
}
```

### `math`

| Function | Result |
|---|---|
| `math::module_version() -> i64` | the standard library version |
| `math::abs(x: i64) -> i64` | the absolute value; `abs(-9223372036854775808)` is a runtime error, because the result does not fit |
| `math::min(a: i64, b: i64) -> i64` | the smaller value |
| `math::max(a: i64, b: i64) -> i64` | the larger value |
| `math::sign(x: i64) -> i64` | -1, 0 or 1 |
| `math::gcd(a: i64, b: i64) -> i64` | Euclid on the absolute values; `gcd(0, 0)` is 0 |
| `math::sum(values: [i64]) -> i64` | the sum of an array, 0 for an empty one, with checked overflow |

### `sort`

| Function | Result |
|---|---|
| `sort::module_version() -> i64` | the standard library version |
| `sort::is_sorted(values: [i64]) -> bool` | true when every element is at least the one before it |
| `sort::insertion_sort(values: mut [i64])` | insertion sort in place through a mutable borrow |
| `sort::binary_search(values: [i64], wanted: i64) -> i64` | an index of `wanted`, or -1; the caller must pass a sorted array |

### `text`

`chars_len` and `bytes_len` are builtins; the module adds classification and
validation that never allocates.

| Function | Result |
|---|---|
| `text::module_version() -> i64` | the standard library version |
| `text::is_continuation(byte: i64) -> bool` | true for a UTF-8 continuation byte (0x80..0xBF) |
| `text::sequence_width(value: str, index: i64) -> i64` | length in bytes of the well-formed UTF-8 sequence at `index`, or 0 |
| `text::is_valid_utf8(value: str) -> bool` | strict validation: no overlong forms, no surrogates, nothing above U+10FFFF |
| `text::code_points(value: str) -> i64` | the number of code points, or -1 when the string is not valid UTF-8 |
| `text::is_ascii(value: str) -> bool` | true when every byte is below 128 |

## Text, bytes and UTF-8

A `str` is a NUL-terminated byte sequence, and the two units are never mixed up
silently:

- A **source literal** is always valid UTF-8: source that is not is `E0111`, an
  escape that is not valid is `E0104`, an unpaired surrogate is `E0106` and a
  NUL is `E0105`.
- `read_file` can return **any** bytes except NUL, so a `str` may be invalid
  UTF-8 at run time. Nothing is replaced or truncated: the bytes arrive exactly
  as stored.
- `bytes_len` counts bytes, `byte_at` reads one byte, and `chars_len` counts
  code points. `chars_len` is where validity is enforced: on bytes that are not
  valid UTF-8 it stops the program with
  `nava runtime error: chars_len of a string that is not valid UTF-8`.
- `print` writes the bytes unchanged, and `==` compares bytes exactly, so two
  strings that look alike before Unicode normalization are not equal.
- A **code point** is one Unicode scalar value: `é` is one code point and two
  bytes in UTF-8, `😀` is one code point and four bytes, and `"héllo"` is five
  code points and six bytes. `tests/test_stdlib.py` checks every one of those
  numbers against Python's encoder and its strict decoder, including overlong
  forms, surrogate halves, truncated sequences and values above U+10FFFF.

```nava
fn main() {
    let text = "héllo"
    print(bytes_len(text))
    print(chars_len(text))
    print(byte_at(text, 1))
    print(text == "hello")
}
```

Output: `6`, `5`, `169`, `false`.

## Files and capabilities

File access is opt-in. A program compiled without a grant cannot read or write
anything: `read_file` and `write_file` exist in the language, but the grants are
decided when the program is **built** and compiled into it, and nothing in the
language can add one at run time.

```text
nava build prog.nv -o prog --allow-read data --allow-write out
nava run   prog.nv --workdir sandbox --allow-read . --allow-write .
```

- `--allow-read DIR` and `--allow-write DIR` may be repeated; each one grants
  one directory subtree. Read and write are separate grants.
- The argument of `read_file`/`write_file` is resolved with `realpath`, so `..`,
  a symbolic link and a symbolic-linked directory cannot be used to leave a
  granted subtree: the resolved path must be inside one of the granted
  directories, otherwise the program stops with
  `nava runtime error: read_file denied: '<path>' is outside every permitted directory`.
  With no grant at all the message says that no directory was configured.
- A **relative** grant is relative to the program's working directory, and it is
  resolved again inside the sandbox, so `--workdir DIR --allow-read .` means the
  same thing with and without namespace isolation. An **absolute** grant only
  matches when the program's view of the filesystem is the host's, which is the
  `rlimits` backend; inside the namespace jail the host paths do not exist, so
  use relative grants there.
- Reads are bounded: one file may be at most 1048576 bytes, one run may read at
  most 4194304 bytes in total and may call `read_file` at most 64 times. The
  returned text lives until the process exits, which is why the total, not the
  call count, is the memory bound.
- `write_file` refuses text larger than 1048576 bytes and refuses a path whose
  last component is empty.
- File access never overrides isolation: inside the namespace jail the program
  sees only its working directory, so a grant for a directory that is not
  mounted cannot help, and a file outside the working directory cannot be
  reached even when a grant names it.

## Checked arithmetic

`i64` arithmetic is checked. The following are runtime errors, written to
`stderr` as `nava runtime error: ...`, with exit status 1:

- overflow in `+`, `-`, `*` and unary `-`;
- division or remainder by zero;
- `-9223372036854775808 / -1` and `-9223372036854775808 % -1`;
- an array index that is negative or at least the length;
- an array length that is negative, above `MAX_ARRAY_ELEMENTS`, or that cannot
  be allocated.

Division truncates toward zero and the remainder takes the sign of the
dividend, so `-7 / 3` is `-2` and `-7 % 3` is `-1`. A runtime error stops the
program immediately: no later side effect happens. Recursion and loops are not
limited automatically; use `nava run --timeout` when running untrusted code.

## Built-in functions

```nava
fn main() {
    let a = read_i64()
    let b = read_i64()
    print(a + b)
    print(true)
    print("ready")
}
```

- `read_i64() -> i64` reads one whitespace-delimited decimal integer, with an
  optional `+` or `-` sign. Invalid input, an out-of-range value and end of
  input are runtime errors.
- `print(value)` accepts exactly one `i64`, `bool`, `f64` or `str` and prints it
  followed by a newline. Booleans print as `true` or `false`. `print` returns
  `void` and cannot be used as a value (`E0309`, `E0314`).
- `read_f64() -> f64` reads one decimal floating-point number (`E0348`).
- `to_f64(x: i64) -> f64` and `to_i64(x: f64) -> i64` convert explicitly
  (`E0347`); there is no implicit conversion.
- `len(a: [T]) -> i64` returns the number of elements (`E0337`, `E0338`).
- `array_zeros(n: i64) -> [i64]` allocates `n` zeroed elements and checks the
  length (`E0339`, `E0340`).
- `bytes_len(s: str) -> i64` is the number of bytes, `chars_len(s: str) -> i64`
  the number of Unicode code points (a runtime error if the bytes are not valid
  UTF-8), and `byte_at(s: str, i: i64) -> i64` one byte as a number in 0..255
  with a checked index.
- `read_file(path: str) -> str` and `write_file(path: str, text: str)` are the
  capability-limited file API; see [Files and capabilities](#files-and-capabilities).
- `print(value)` also accepts `f64`.
- All twelve names are reserved: they cannot be redefined (`E0301`).

## Edition 2: structs

A file opts into edition 2 with `edition 2;` as its first statement. An edition-1
file is unchanged: every program that compiled before still compiles, with the
same diagnostics. A file without the declaration is edition 1, and the
edition-2 constructs below are then refused with `E0372` and a hint that names
the declaration.

```text
edition        = "edition" NUMBER ";" ;
struct         = "struct" IDENT "{" { separator } [ field { separator } ] "}" ;
field          = IDENT ":" type { "," IDENT ":" type } ;
construction   = IDENT "(" IDENT ":" expression { "," IDENT ":" expression } ")" ;
member         = primary "." IDENT ;
member_assign  = IDENT "." IDENT "=" expression ;
```

### Declaring a struct

```
edition 2;

struct Point {
    x: i64,
    y: i64
}
```

A struct is a value with named fields. A field type is `i64`, `bool`, `f64`,
`str`, a function type, an array, or another struct or enum. A `str` field is
allowed because a string value is immutable and lives for the whole run:
copying the struct copies the pointer to a string nobody writes and nobody
frees. An **array field owns storage**, so the struct is copied by copying that
array: `let b = a` gives `b` its own buffer, writing through one value can never
be seen through the other, and the block that owns a value releases its storage
when it ends. The compiler emits one copy and one release helper per type that
needs them; the copy happens at every ownership boundary — a `let`, an
assignment, a field write, a `match` binding and a `return` — and is not elided.

A value that owns an array may not be an **array element** (`E0426`): an element
is copied on every read and every write, so `[Bag]` would need those copies to be
deep, which is the storage work of N11f. A struct name is shared by
every module of the program (`E0380` for a duplicate), and a struct may not
contain itself directly or through another struct (`E0377`), because a struct is
stored inline and a recursive type would have no finite size. A variant payload
follows the same rules: a value, a `str` or an array, and an instance of it may
not be an array element either.

```nava
edition 2;

struct Person {
    name: str,
    age: i64
}

fn main() {
    let ada: Person = Person(name: "Ada", age: 36)
    let copy = ada
    copy.age = 37
    print(ada.age)
    print(copy.age)
    print(copy.name)
}
```

Output: `36`, `37`, `Ada`. The copy is a copy of the struct: writing `copy.age`
does not touch `ada`, and both names point at the same immutable string.

### Constructing a value

```
let p = Point(x: 3, y: 4)
```

Construction names every field exactly once, in any order (`E0376` for a missing
field, `E0375` for one given twice, `E0374` for an unknown one, `E0311` for a
wrong type). The syntax is a call with named fields rather than `Point { ... }`
on purpose: a `{` after an expression would be ambiguous with the block that
follows a condition or a range, so `if p { }` and `for i in 0..n { }` would stop
being readable — and a model writing code should not have to know which
positions are exceptions.

### Reading and writing fields

```
let dx = p.to.x - p.from.x
p.x = 0
```

`p.x` reads a field (`E0379` if the value is not a struct, `E0374` if the struct
has no such field), and `p.x = value` writes one; both are checked. A field of a
loop variable cannot be written, for the same reason the variable itself cannot
(`E0351`).

### Copying

A struct is copied by assignment, as a parameter and as a return value. The copy
is a value: writing to the copy is not visible through the original.

```
let a = Counter(value: 1)
let b = a
b.value = 99       # a.value is still 1
```

A struct that owns an array shares nothing with its copy, and the block that
declares it releases the storage. This is why a struct is not printable
(`print` accepts `i64`, `bool`, `f64` and `str`, so `E0323` applies) and cannot
be compared with `==` (`E0320`): both would need a definition of equality or a
format that edition 2 does not have. They are the next edition's business, and
the compiler says so instead of guessing.

Struct declarations are module-wide, not per-file with a visibility keyword: a
`pub`-style keyword and cross-module visibility are part of the later stages of
the independence plan, and the current rule is stated here rather than implied.

### Enums and `match`

```
edition 2;

enum Shape {
    Dot,
    Circle(i64),
    Rect(i64, i64)
}

fn area(s: Shape) -> i64 {
    match s {
        Shape::Dot => { return 0 }
        Shape::Circle(r) => { return 3 * r * r }
        Shape::Rect(w, h) => { return w * h }
    }
}
```

```text
enum_decl      = "enum" IDENT "{" { separator } variant { separator } "}" ;
variant        = IDENT [ "(" type { "," type } ")" ] ;
variant_value  = IDENT "::" IDENT "(" [ expression { "," expression } ] ")" ;
match_stmt     = "match" expression "{" { separator } arm { separator } "}" ;
arm            = "=>" block | pattern "=>" block ;
pattern        = "_" | IDENT "::" IDENT [ "(" IDENT { "," IDENT } ")" ] ;
```

An enum is a type whose values are exactly its variants. A variant carries a
fixed, positional list of fields, each `i64`, `bool`, `f64`, `str`, an array, a
struct or another enum (`E0203` for an unknown name). A payload that owns an
array is copied and released exactly as a struct field is. Variant names are unique inside their enum (`E0391`) and
`struct`, `enum` and builtin type names share one namespace (`E0390`).

A value is built with its variants, always qualified: `Shape::Circle(2)`. All
fields are given, positionally, with their declared types (`E0394`, `E0311`).

`match` is a **statement**. Every arm names a variant of the enum —

```
match cmd {
    Cmd::Add(a, b) => { total = total + a + b }
    Cmd::Neg(v) => { total = total - v }
    _ => { }                    # the wildcard: no bindings, matches anything
}
```

— and the checker requires the arms to cover every variant, either by naming
each one or by adding `_` (`E0396`). Naming a variant twice is `E0397`, an arm
after `_` is unreachable and is `E0398`, an unqualified pattern such as `Dot` is
`E0392` because a variant is only in scope through its enum, and a pattern must
bind exactly the variant's fields (`E0394`).

Semantics, stated because they are observable:

* the scrutinee is evaluated **once**, before the first arm, and the arms are
  tested in source order;
* a binding is a **copy** of the field, with the declared type, scoped to its
  arm's block; assigning to it does not change the matched value;
* a `match` whose arms all return counts as a return for `E0306`, and
  exhaustiveness is checked before that analysis, so `fn f(s: Shape) -> i64 {
  match s { ... } }` needs no trailing `return`;
* an arm that matches nothing observable is still an arm: `Shape::Dot => { }`
  leaves the program unchanged.

`match` is not an expression in edition 2. Guards, literal and range patterns
and matching on `i64` values do not exist yet; `Option`, `Result` and `?` are
described in the next section.

### `Option`, `Result` and `?`

Two enums are part of edition 2 without being declared: `Option(T)` with
variants `Some(T)` and `None`, and `Result(T, E)` with `Ok(T)` and `Err(E)`. They
are the only types that take type arguments, and the arguments are written in
parentheses — `Option(i64)`, `Result(i64, str)` — never in angle brackets,
because `<` is a comparison operator and `Option<i64>` would make the two
meanings depend on lookahead.

```
edition 2;

fn parse_positive(n: i64) -> Result(i64, str) {
    if n > 0 { return Result(i64, str)::Ok(n) }
    return Result(i64, str)::Err("not positive")
}

fn doubled(n: i64) -> Result(i64, str) {
    let value = parse_positive(n)?      # on Err: return it now
    return Result(i64, str)::Ok(value * 2)
}
```

`?` is available in one place: the initializer of a `let`. Three rules, and
nothing else:

1. the expression must produce an `Option(T)` or a `Result(T, E)` (`E0410`);
2. the enclosing function must return the **same** enum (`E0411`), instantiated
   with the **same failure type** — `Option` has no failure type, so any
   `Option` propagates into any `Option`; `Result` propagates only where the `E`
   is identical, because a failure is forwarded, never converted;
3. the success type may differ, and it is what the new local holds:
   `Result(bool, str)` inside a function returning `Result(i64, str)` is
   `let flag = check()?` with `flag: bool`.

Anything else is refused: `print(f()?)` is `E0413` (the early return has to be
a statement), `let x = 1?` is `E0410`, a `?` in `fn main()` is `E0411` because
`main` does not return a `Result`.

Values of these enums are built and matched like any other enum, with the
arguments written out: `Result(i64, str)::Ok(3)`, and in a pattern
`Result(i64, str)::Ok(v)`. A pattern must name the same instantiation as the
value (`E0395` otherwise).

`?` is not an expression, there is no `try`/`catch`, and a failing `Option` is
not an error: it propagates `None` like a `Result` propagates `Err`. Nothing is
converted; nothing is swallowed.

### Arrays of structs and slices

Edition 2 widens what an array may hold and adds a way to look at part of one.

```text
type           = ... | "[" type_name "]" ;      # edition 2: a struct element
slice          = IDENT "[" expression ".." expression "]" ;
```

An array element is a **value with no owner**: `i64`, `bool`, `str`, a struct or
an enum that owns nothing itself. `f64`, `void` and another array are refused
(`E0206` for the shapes no symbol table could allow, `E0203` for a name that is
not declared), and a struct or enum that holds an array is refused with `E0426`:
an element is copied on every read and every write, so an owning element would
need deep copies in the storage itself, which the storage work of N11f brings. An
enum is a value like a struct — its payload is stored inline — so `[Shape]` is
an array of plain values and the array stays the single owner.

`array_zeros(n)` gives every element its **zero value**, which exists only when
all-zero bits are a value of the element type: `0`, `false`, `0.0`, a struct
whose every field is zeroable, or an enum whose first variant is. An all-zero
`str` is a null pointer rather than an empty string and an all-zero function
value cannot be called, so `array_zeros` refuses those element types (`E0425`)
instead of handing the program a value that is not one.

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
    let first_end = points[0..1]
    print(len(first_end))
    print(first_end[0].x)
}
```

Output: `10`, `4`, `1`, `1`. An element is a **value**: `let p = points[0]`
copies it (writing `p.x` does not touch the array), and `points[i] = p` replaces
the whole element. A field of an element may be read (`points[i].x`); to change
one, copy the element, change the copy and write it back — a field write
straight into an element is not part of edition 2.

`a[i..j]` creates a **slice**: a read-only view of `j - i` elements of `a`,
starting at `i`. A slice is not a copy and owns nothing.

* the bounds are checked when the slice is created, not when it is used:
  `0 <= i <= j <= len` (`nava runtime error: slice start -1 is out of range for
  length 3`, `nava runtime error: slice end 4 is out of range for start 0 and
  length 3`, exit status 1). The two messages are distinct on purpose: a start
  that is too large and an end that crosses the length are different mistakes.
* a slice has the same type as the array it views, `[T]`, so it can be passed to
  a parameter of that type and measured with `len`. It cannot be passed to a
  `mut [T]` parameter (`E0423`): a borrowed view must not become a writable one.
* the elements cannot be written through a slice (`E0422`) — the array that owns
  the storage stays the only writer.
* a slice is never released by the block that binds it; the array it views
  releases the storage, at the end of the array's own block, as before;
* a view sees the owner's writes: `let view = a[0..3]` followed by `a[1] = x`
  makes `view[1]` read `x`. Reading through a view is a read of the array, which
  is what "view" means and why it cannot be written through.
* a slice of a slice is allowed and composes: `a[1..4][1..2]` cannot be written
  because a slice is not an expression, but `let inner = a[1..4]` followed by
  `let tail = inner[1..2]` is a view of two elements of the original array.
* a `for` loop may iterate a slice (`for p in points[1..3]`); the bounds are
  evaluated once, before the first turn, like every other iterable;
* the syntax names a **variable** after `[` and nothing else, so a slice of a
  temporary cannot be written down at all, and a view can never outlive the
  storage it points at: an array cannot be returned from a function, cannot be a
  struct field and cannot be reassigned.

Bounds are evaluated left to right, like every other expression in Nava, so a
program that prints inside the bounds sees the start printed before the end.

### Vectors: `Vec(T)`

An array has the length it was created with for the life of its block. A vector
is the type for the other case: it starts empty, grows as the program pushes, and
reports the length it has now.

```text
vector_type    = "Vec" "(" type_name ")" ;      # edition 2
vector_call    = ( "vec_new" | "vec_len" | "vec_capacity" | "vec_push"
                 | "vec_pop" | "vec_get" | "vec_set" ) "(" ... ")" ;
```

```nava
edition 2;

fn fill(xs: mut Vec(i64), n: i64) {
    let i = 0
    while i < n {
        vec_push(xs, i * i)
        i = i + 1
    }
}

fn total(xs: Vec(i64)) -> i64 {
    let sum = 0
    for x in xs { sum = sum + x }
    return sum
}

fn main() {
    let xs: Vec(i64) = vec_new()
    fill(xs, 5)
    print(vec_len(xs))
    print(vec_capacity(xs))
    print(total(xs))
    vec_set(xs, 0, 100)
    print(vec_get(xs, 0))
    match vec_pop(xs) {
        Option(i64)::Some(last) => { print(last) }
        Option(i64)::None => { print(-1) }
    }
}
```

The seven builtins are the whole interface:

| Call | Type | Meaning |
|---|---|---|
| `vec_new()` | `Vec(T)` | creates an empty vector; `T` comes from the annotation |
| `vec_len(v)` | `i64` | how many elements it holds now |
| `vec_capacity(v)` | `i64` | how many it can hold before the next growth |
| `vec_push(v, x)` | `void` | appends one element, growing if needed |
| `vec_pop(v)` | `Option(T)` | removes and returns the last element, or `None` |
| `vec_get(v, i)` | `T` | reads element `i`, checked |
| `vec_set(v, i, x)` | `void` | writes element `i`, checked |

The example prints `5`, `8`, `30`, `100`, `16`: five elements, room for eight,
their squares summed, the replaced first element, and the popped last one.

What the type is, and what it is not:

* `Vec(T)` is a **builtin type**, not a generic declaration. The element type is
  written out — `i64`, `bool`, `str`, `f64`, or a struct or enum type — and a
  name that declares nothing is `E0203`. An element may not own storage
  (`E0427`): a vector copies elements on every push and every read, and a
  shrinking vector would have to release elements it never copied, so an owning
  element waits for the storage work of N11f, exactly as an owning array
  element does.
* the block that writes the `let` owns the vector and releases it when the block
  ends, including on an early `return`. A vector is never **copied** — two
  holders would free one buffer — but it can be **moved**: out of a local into
  another binding, into an assignment, and out of a function as its return
  value (see [Moving an owning value](#moving-an-owning-value)). It still cannot
  be stored in a field, used as a payload or a type argument (`E0428`).
* `vec_new()` needs the annotation (`E0429`): an empty vector says nothing about
  what it holds, and there is no second guess.
* one handle, shared by borrowing: a `Vec(T)` parameter reads the vector, a
  `mut Vec(T)` parameter may grow it, and the caller sees the new length —
  growth replaces the **buffer inside the handle**, never the handle itself.
  Every mutating builtin therefore needs a writable target: a local or a
  `mut Vec(T)` parameter (`E0444`). A `mut Vec(T)` **argument** follows the array
  rule: it must be a vector created in the calling function, not a borrow
  (`E0333`), and one vector cannot be passed to two mutable parameters of the
  same call (`E0342`).
* growth doubles. Capacity starts at four and each reservation takes the
  smallest power of two that fits, so `N` pushes copy fewer than `2N` elements in
  total — amortised constant time per push — and `vec_capacity` reports the
  slack the program is paying for.
* indices are checked against the **current** length, and an out-of-range
  `vec_get` or `vec_set` ends the program like an array index does. `vec_pop` on
  an empty vector is not an error: it returns `Option(T)::None`, which is why its
  type is `Option(T)` and not `T`.
* `for x in v` reads the length once, before the first turn, like every other
  iterable, and binds the **element** type; the loop variable is read-only
  (`E0351`). A vector grown inside the loop body therefore does not extend the
  loop it is growing in.
* there is no `vec_insert`, no `vec_remove` and no shrink: a vector grows at the
  end and pops from the end, and its capacity is released only when the vector
  is. Sorting or filtering is a loop over two vectors.
* a vector is not an array for the purposes of the index operator: `v[i]` is
  `E0329` and `v[0..1]` is `E0421`. A vector index is a call — `vec_get` or
  `vec_set` — so the check is part of the language rather than of an operator.
* `print` does not take a vector (`E0323`); print `vec_len(v)` or the elements.
* the seven names are **reserved** (`E0301`): `fn vec_new()` or `let vec_len = 1`
  is refused, because the builtin is resolved before any declaration and a
  program that shadowed it would be writing dead code.

### Strings that grow: `String` and the text builtins

A `str` is immutable and lives for the whole run; a program cannot build one.
`String` is the owner of bytes a program assembles a piece at a time, and the
`str_*` builtins build a finished `str` from parts.

```text
string_type    = "String" ;                     # edition 2
string_call    = ( "string_new" | "string_push" | "string_push_byte"
                 | "string_len" | "string_capacity" | "string_clear"
                 | "string_text" ) "(" ... ")" ;
text_call      = ( "str_concat" | "str_from_i64" | "str_from_f64" | "str_slice"
                 | "str_index_of" | "str_starts_with" | "str_ends_with"
                 | "str_trim" | "str_replace" | "str_split" | "str_join" )
                 "(" ... ")" ;
```

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
    print(str_from_i64(-42))
    print(str_replace("a-b-c", "-", "+"))
}
```

The example prints `3`, `a-bb-ccc`, `a/bb/ccc`, `nava`, `-42`, `a+b+c`.

| Call | Type | Meaning |
|---|---|---|
| `string_new()` | `String` | an empty builder |
| `string_push(s, x)` | `void` | append a `str` |
| `string_push_byte(s, b)` | `void` | append one byte, `0..255`, never `0` |
| `string_len(s)` | `i64` | bytes written so far |
| `string_capacity(s)` | `i64` | bytes before the next growth |
| `string_clear(s)` | `void` | forget the bytes, keep the capacity |
| `string_text(s)` | `str` | the finished text, as an immutable `str` |
| `str_concat(a, b)` | `str` | the two strings joined |
| `str_from_i64(n)` / `str_from_f64(x)` | `str` | the same decimal text `print` writes |
| `str_slice(x, i, j)` | `str` | bytes `i .. j`, both checked |
| `str_index_of(x, needle)` | `i64` | first byte offset, or `-1` |
| `str_starts_with(x, p)` / `str_ends_with(x, p)` | `bool` | prefix and suffix tests |
| `str_trim(x)` | `str` | leading and trailing ASCII whitespace removed |
| `str_replace(x, from, to)` | `str` | every occurrence of `from` |
| `str_split(x, sep, out)` | `void` | appends each field to a `mut Vec(str)` |
| `str_join(parts, sep)` | `str` | the vector's strings with `sep` between them |

The rules, and the reasons for them:

* `String` has **exactly one owner**, like an array and like a vector. It is
  never copied, but it is **moved**: into another binding, into an assignment,
  and out of a function as its return value. It still cannot be stored in a
  field, used as a payload or a type argument (`E0428`), and an element of an
  array or a vector may not be a `String` (`E0426` / `E0427`). A `mut String`
  parameter is a borrow that may write, and a `String` parameter may not.
* the block that writes `let s: String = string_new()` releases the buffer when
  it ends, including on an early `return`. Growth doubles from 32 bytes; the
  buffer is allocated through the same checked allocator as an array, so the
  leak counter sees it.
* `str_split` writes into a vector instead of returning one, because a program
  cannot return an array or a vector: `let parts: Vec(str) = vec_new()` followed
  by `str_split(x, sep, parts)` is the shape, and `vec_len(parts)` is the count.
  An empty separator is a runtime error rather than an empty field list.
* **Built `str` values live in an arena** — a stack of chunks, bump-allocated.
  A call is given a **region**: a mark taken when it starts and released when it
  returns, which gives the text the frame built back in one step. It is only
  given one when nothing it allocates can outlive it, and the rule is
  conservative: a function whose return type can hold text — `str`, a `Vec` or
  an array of it, a struct or an enum containing one — or that writes through a
  `mut` parameter that can hold text keeps its chunks for the whole run, because
  the caller can still read that text afterwards. `main` has no region either:
  its frame ends with the process. Cycles and literals are unaffected: literals
  are static and are never copied. Under `-DNAVA_TRACK_ALLOCS` the arena prints
  its live accounting (`nava: arena bytes: N, chunks: M`) and frees whatever is
  left at exit, which is what the AddressSanitizer gate checks.
* `string_text` copies into the arena rather than handing out the builder's
  buffer: the builder may grow afterwards, and a `str` must not change.
* bytes are not characters. `string_len` and `str_slice` count bytes, like
  `bytes_len` and `byte_at`; `chars_len` remains the one place where UTF-8 is
  validated, and the `text` standard module (`is_valid_utf8`, `code_points`)
  adds classification without allocating.

### Moving an owning value

A `Vec(T)` and a `String` own one buffer each, so a program can hand the buffer
to somebody else but never duplicate it. Handing it on is a **move**: the value
is transferred, nothing is copied, and the binding it came from is empty from
then on.

```text
move_target    = let_binding | assignment | return_statement ;
move_source    = constructor | call | IDENT ;   # vec_new / string_new included
```

```nava
edition 2;

fn label(n: i64) -> String {
    let s: String = string_new()
    string_push(s, "n=")
    string_push(s, str_from_i64(n))
    return s
}

fn counted(n: i64) -> Vec(i64) {
    let v: Vec(i64) = vec_new()
    let i = 0
    while i < n {
        vec_push(v, i)
        i = i + 1
    }
    return v
}

fn main() {
    let a: String = label(7)
    print(string_text(a))
    let xs: Vec(i64) = counted(4)
    print(vec_len(xs))
    let more: Vec(i64) = xs
    print(vec_len(more))
    let other: String = string_new()
    other = label(9)
    print(string_text(other))
}
```

The example prints `n=7`, `4`, `4` and `n=9`.

The rules, and the reasons for them:

* three forms reach an owning binding: a **constructor** (`vec_new`,
  `string_new`), a **call** that returns the same owning type — the callee moved
  the value out and this function owns it now — and a **local** of that type,
  which is moved and then dead. A value that is not one of these is refused
  (`E0428`), because there is nothing to copy it from.
* a moved-from local cannot be read again: passing it, printing it, returning
  it, moving it a second time, even assigning to it is `E0445`. The buffer
  belongs to the new binding, and the frame that wrote the old `let` no longer
  frees anything.
* `return` of a `Vec(T)` or a `String` is a move **out of the frame**. The
  caller's binding becomes the owner and releases the buffer when its own block
  ends; the callee releases nothing. An early `return` from inside a loop or a
  nested block moves exactly the same way.
* only a value this function owns can be moved out. A parameter is a borrow:
  returning one, or assigning over one, is `E0446`, because the caller still
  owns what it passed and would free it as well.
* the value a call returns must be bound. A call to a function returning `Vec(T)`
  or `String` used as a statement is `E0428`: the buffer would be handed to
  nobody and never released.
* moving is not copying, and the type system still has no deep copy: an owning
  value cannot be stored in a field, used as a payload, used as a type argument
  (`E0428`) or used as an element of an array or a vector (`E0426` / `E0427`).
* the arena has regions, and moves are why the rule is written down rather than
  assumed: text a frame built is released when the frame returns unless it can
  be reached afterwards, and the conservative check for "reachable" is the
  return type and the `mut` parameters. Move a `str` out of a frame and the
  frame is not given a region — which costs memory, never correctness.

### `defer`, labelled loops and `pub`

Three statements about *where control goes*, all edition 2.

```text
defer          = "defer" block ;
loop_label     = IDENT ":" ( while_statement | for_statement ) ;
jump           = ( "break" | "continue" ) [ IDENT ] ;
public_fn      = "pub" "fn" IDENT "(" [ parameters ] ")" [ "->" type ] block ;
```

`defer { ... }` registers a block that runs when the **enclosing block is left**:
on the normal path, on `return`, and on a `break`/`continue` that leaves the
block. The rules, all observable:

* two `defer`s in one block run last-first, so they nest like scopes;
* a deferred block runs **before** the block releases its arrays, so it can
  still read them;
* it reads its variables when it **runs**, not when it was registered:
  `let i = 0; while i < 2 { defer { print(i) } ; i = i + 1 }` prints `1`, then
  `2` — the value of `i` at the moment the block left, not the value it had at
  registration;
* the block runs **once per iteration** when it is written inside a loop body;
* it cannot leave itself: `return` inside a `defer` is `E0430`, and so are
  `break`, `continue` and `?`, because the scope is being destroyed;
* the braces are required (`defer print(x)` is `E0201`): the two spellings
  differ in what they capture and a reader should not have to guess.

A loop may carry a label, and a jump may name it:

```
outer: for i in 0..rows {
    for j in 0..cols {
        if grid[i][j] == target { found = true  break outer }
    }
}
```

`break outer` leaves `outer` and every loop between, releasing the arrays of
each block it leaves; `continue outer` restarts `outer` and runs its increment.
A label that no enclosing loop carries is `E0431`; a label written on anything
that is not a loop is `E0432`. Labels are not variables and are not visible to
`let`. A nested loop may reuse a label name, and then the **innermost** loop
with that name is the target — the same rule as a variable lookup, so a jump
never silently leaves a loop the author did not name.

`pub fn name(...)` makes a function of a module callable from another module. A
module that declares `edition 2;` exports **only** its `pub` functions, and a
call to a private one is `E0433`; a module that does not declare edition 2 keeps
exporting every function, which is the edition-1 contract and does not change.
A public function may call the private functions of its own module. Only a
function can be `pub` (`E0434`).

### Generic declarations

A `struct`, an `enum` and a `fn` may take **type parameters**, and every use
instantiates them with concrete types. There is no runtime representation of a
type parameter: an instantiation is an ordinary struct, enum or function with
its parameters replaced, made once per combination of arguments
(*monomorphisation*).

```text
type_parameters = "<" IDENT { "," IDENT } ">" ;
struct_decl     = "struct" IDENT [ type_parameters ] "{" { field } "}" ;
enum_decl       = "enum" IDENT [ type_parameters ] "{" { variant } "}" ;
fn_decl         = [ "pub" ] "fn" IDENT [ type_parameters ] "(" [ parameters ] ")"
                  [ "->" type ] block ;
instance        = IDENT "(" type { "," type } ")" ;        (* Pair(i64), Box(str) *)
```

The **type** of an instantiation is written `Name(types)`, the same spelling
`Option` and `Result` already use, and it may appear wherever a type may:

```nava
edition 2;

struct Pair<T> {
    first: T,
    second: T
}

fn swap<T>(p: Pair(T)) -> Pair(T) {
    return Pair(T)(first: p.second, second: p.first)
}

fn main() {
    let p: Pair(i64) = Pair(i64)(first: 1, second: 2)
    let q = swap(p)
    print(q.first)
    print(q.second)
}
```

Note the two kinds of bracket: `fn swap<T>` declares a type parameter with
angle brackets, `swap(p)` calls it, and `Pair(T)(first: ...)` names an
instantiation and then builds one — two parentheses, one type. Angle brackets
are a *declaration* and round brackets an *instantiation*, so a type is always
written the same way wherever it appears: `Option(i64)` and `Pair(i64)` are
spelled alike. A **declaration with type parameters is not a type**: `let p: Pair` is `E0437`, and so is `Pair(first: 1, second: 2)`; write the
arguments.

**Call sites may infer the type arguments.** `identity(7)` binds `T` to `i64`
from the argument; `identity(i64)(7)` says it explicitly. A call writes its type
arguments *before* its arguments, so `identity(i64)(7)` and `identity(7)` are the
same call, while `double(i64)(21)` — a second argument list on a function that is
not generic — is `E0438`. Inference is a unification of the parameter types with
the argument types and nothing more: a type parameter that no parameter mentions
cannot be inferred, and that call is `E0436` until the arguments are written out.
A template struct or enum is always written with its arguments: there is nothing
at a construction site to infer from without guessing which field decides.

An instantiation **is an ordinary declaration**. `Pair(i64)` has the fields of
the template with `T` replaced, it is copied and assigned exactly as a
hand-written struct is, and its fields are reached with `.` in the same way. Two
instantiations with different arguments are different, unrelated types:
`Pair(i64)` and `Pair(f64)` cannot be assigned to each other (`E0311`). The same
rule makes a duplicate instantiation free — `identity(1)` and `identity(x)` where
`x: i64` share one generated function.

The **body of a template is checked per instantiation**, because its type
parameters have no meaning before then. A template that no call instantiates is
therefore never type-checked, and the compiler says so with `W0003` rather than
claiming to have verified it. An error inside a generic body is reported at the
call that instantiates it, once per distinct instance.

Restrictions, all edition 2 and all stated rather than implied:

* a type parameter is a type inside its own declaration only — the body may write
  `Pair(T)`, and nothing outside may write `T`;
* an instance may not be an array element type (`[Pair(i64)]` is `E0206`), and
  an instance that owns an array is refused as an element like any other
  (`E0426`);
* a template that would have to contain itself — `struct Node<T> { next:
  Node(Node(T)) }` instantiated at `Node(i64)` — is `E0377`, because a struct is
  stored inline and there is no indirection to break the cycle;
* there are no bounds on a type parameter: `fn add<T>(a: T, b: T) -> T { return
  a + b }` is rejected by the checker at every instantiation, because `+` is not
  defined for every `T`. Generic code in Nava is written for the instantiations
  it is called with;
* generic arrays (`[T]` where `T` is a parameter) are not supported yet;
* a struct or enum declared in one file is not visible by name in another, so
  `Pair(i64)` cannot be written outside the module that declares `Pair`. A
  generic **function** crosses the boundary normally: `pub fn pick<T>(...)` is
  callable as `module::pick(3, 4)` from any module that imports it.

### Function values

A function can be used as a value. The value is the address of a top-level
function; nothing is captured and no function is created while the program runs,
so a function type is a plain type with a fixed signature.

```text
function_type  = "fn" "(" [ type { "," type } ] ")" [ "->" type ] ;
function_value = IDENT ;                                   (* double *)
indirect_call  = IDENT "(" [ arguments ] ")" ;             (* f(2), f() *)
```

A function type is written `fn(i64) -> i64`, and `fn() -> void` when it takes no
arguments and returns nothing. The result is always written out, so two function
types are the same exactly when their spellings are equal: `fn(i64) -> i64` and
`fn(i64) -> f64` are different, unrelated types.

```nava
edition 2;

fn double(x: i64) -> i64 {
    return x * 2
}

fn twice(f: fn(i64) -> i64, x: i64) -> i64 {
    return f(f(x))
}

fn main() {
    let f: fn(i64) -> i64 = double
    print(f(21))
    print(twice(f, 5))
    print(twice(double, 5))
}
```

A declared function named in a value position has the type of its signature, so
`let f = double` infers `fn(i64) -> i64` and needs no annotation. A call through
a variable is checked against that signature before the program runs: the
number of arguments (`E0326`), each argument type (`E0327`) and the result are
all known, and a variable that does not hold a function type cannot be called at
all (`E0439`). Assigning a function value of another signature is `E0311`, like
any other type mismatch.

A function type may appear wherever a type may: a `let` annotation, a parameter,
a result, a struct field, a variant payload, a prelude instantiation such as
`Option(fn(i64) -> i64)`, and the type arguments of a generic call. It is a value
type, so a signature is copied like an `i64`: `let g = f` copies the address.

```nava
edition 2;

fn double(x: i64) -> i64 {
    return x * 2
}

struct Ops {
    op: fn(i64) -> i64,
    value: i64,
}

fn main() {
    let ops: Ops = Ops(op: double, value: 4)
    let f: fn(i64) -> i64 = ops.op
    print(f(ops.value))
}
```

The generated C keeps the type: one `typedef` per distinct signature and one
`&nv_fn_N` per function value, so no call passes through `void *` and the C
compiler checks the signature a second time.

Restrictions, all edition 2 and all stated rather than implied:

* there are no closures and no function literals: a value always names a
  declared function, so there is nothing to capture;
* a generic function has no single signature and is not a value (`E0437`); a
  wrapper such as `fn double_i64(x: i64) -> i64 { return double(x) }` is the way
  to pass one on;
* a module-qualified name has no value spelling yet: `math::abs` may be called
  but not bound, so pass it through a `pub fn` of its module instead;
* a call cannot go through a field or any other expression (`E0441`): bind it
  first, as in `let f: fn(i64) -> i64 = ops.op`;
* two function values cannot be compared (`E0442`) and a function value cannot
  be printed (`E0323`): an address is not a language-level value;
* a function type takes no `mut` parameter (`E0443`) — a function value may take
  `[i64]`, which is a read-only borrow, and only a declared function can ask for
  `mut`;
* function values cannot be stored in an array: an array element type is still
  `i64`, `bool`, `str` or a struct (`E0206`).

## Diagnostics

Errors are reported with a stable code, a source range and an optional hint.

```text
example.nv:2:9: error[E0311]: expected bool, got i64
  hint: there are no implicit conversions between i64, bool and str
```

`--format json` (before the subcommand) prints one JSON document to `stdout`
instead, which is what a tool or a repair loop should consume:

```json
{
  "status": "error",
  "path": "example.nv",
  "diagnostics": [
    {
      "code": "E0311",
      "severity": "error",
      "title": "annotation does not match the initializer",
      "message": "expected bool, got i64",
      "path": "example.nv",
      "range": {"start": {"line": 2, "column": 9}, "end": {"line": 2, "column": 10}},
      "hint": "there are no implicit conversions between i64, bool and str"
    }
  ]
}
```

`range.start` is 1-based and points at the first character of the offending
construct; `range.end` is exclusive. `range` is `null` for program-wide errors
such as a missing `main`. Codes are part of the interface: an existing code
never changes meaning; a new condition gets a new code.

### Diagnostic codes

**Lexer and source decoding**

| Code | Severity | Meaning | Triggered by |
|---|---|---|---|
| `E0101` | error | unexpected character | `tests/fixtures/negative/lex_unexpected_char.nv` |
| `E0102` | error | unterminated string | `tests/fixtures/negative/lex_unterminated_string.nv` |
| `E0103` | error | literal newline in string | `tests/fixtures/negative/lex_newline_in_string.nv` |
| `E0104` | error | invalid string escape | `tests/fixtures/negative/lex_bad_escape.nv` |
| `E0105` | error | NUL in string | `tests/fixtures/negative/lex_nul_in_string.nv` |
| `E0106` | error | invalid Unicode surrogate in string | `tests/fixtures/negative/lex_surrogate_in_string.nv` |
| `E0107` | error | invalid underscore in integer literal | `tests/fixtures/negative/lex_bad_underscore.nv` |
| `E0108` | error | integer literal outside the i64 range | `tests/fixtures/negative/lex_literal_range.nv` |
| `E0109` | error | unmatched ')' | `tests/fixtures/negative/lex_unmatched_paren.nv` |
| `E0110` | error | unclosed '(' | `tests/fixtures/negative/lex_unclosed_paren.nv` |
| `E0111` | error | source is not valid UTF-8 | — |
| `E0112` | error | unmatched ']' | `tests/fixtures/negative/lex_unmatched_bracket.nv` |
| `E0113` | error | unclosed '[' | `tests/fixtures/negative/lex_unclosed_bracket.nv` |

**Parser**

| Code | Severity | Meaning | Triggered by |
|---|---|---|---|
| `E0201` | error | unexpected token | `tests/fixtures/negative/parse_expected_token.nv` |
| `E0202` | error | missing statement separator | `tests/fixtures/negative/parse_missing_separator.nv` |
| `E0203` | error | unknown type | `tests/fixtures/negative/parse_unknown_type.nv` |
| `E0206` | error | an array element type is not allowed (`[void]`, `[f64]`, `[[i64]]`, `[Name]`) | `tests/fixtures/negative/array_nested_type.nv`, `array_of_void.nv`, `array_of_f64.nv` |
| `E0204` | error | unclosed block | `tests/fixtures/negative/parse_unclosed_block.nv` |
| `E0205` | error | expected an expression | `tests/fixtures/negative/parse_expected_expression.nv` |

| `E0370` | error | the edition declaration is not the first statement | `tests/fixtures/negative/edition_not_first.nv` |
| `E0371` | error | unknown edition | `tests/fixtures/negative/edition_unknown.nv` |
| `E0372` | error | an edition-2 construct in an edition-1 file | `tests/fixtures/negative/edition_one_struct.nv` |
| `E0373` | error | unknown struct | `tests/fixtures/negative/struct_unknown.nv` |
| `E0374` | error | unknown field | `tests/fixtures/negative/struct_unknown_field.nv` |
| `E0375` | error | duplicate field | `tests/fixtures/negative/struct_duplicate_field.nv` |
| `E0376` | error | missing field in a construction | `tests/fixtures/negative/struct_missing_field.nv` |
| `E0377` | error | a struct contains itself | `tests/fixtures/negative/struct_recursive.nv` |
| `E0379` | error | member access on a non-struct value | `tests/fixtures/negative/struct_member_scalar.nv` |
| `E0380` | error | duplicate struct | `tests/fixtures/negative/struct_duplicate.nv` |
| `E0381` | error | named fields where they are not allowed | `tests/fixtures/negative/struct_positional.nv`, `struct_named_function.nv` |
| `E0390` | error | duplicate enum | `tests/fixtures/negative/enum_duplicate.nv` |
| `E0391` | error | duplicate enum variant | `tests/fixtures/negative/enum_duplicate_variant.nv` |
| `E0392` | error | a variant pattern needs the enum name | `tests/fixtures/negative/match_pattern_unqualified.nv` |
| `E0393` | error | unknown enum variant | `tests/fixtures/negative/enum_unknown_variant.nv` |
| `E0394` | error | wrong number of variant fields (construction or pattern) | `tests/fixtures/negative/enum_variant_arity.nv`, `match_binding_arity.nv` |
| `E0395` | error | `match` expects an enum value | `tests/fixtures/negative/match_not_an_enum.nv` |
| `E0396` | error | `match` is not exhaustive, or has no arms | `tests/fixtures/negative/match_not_exhaustive.nv` |
| `E0397` | error | duplicate match arm | `tests/fixtures/negative/match_duplicate_arm.nv` |
| `E0398` | error | unreachable match arm after `_` | `tests/fixtures/negative/match_arm_after_wildcard.nv` |
| `E0410` | error | `?` expects an Option or a Result | `tests/fixtures/negative/propagate_not_option.nv` |
| `E0411` | error | `?` cannot propagate into this function's return type | `tests/fixtures/negative/propagate_in_void.nv`, `propagate_failure_mismatch.nv` |
| `E0412` | error | wrong number of type arguments | `tests/fixtures/negative/result_wrong_arity.nv` |
| `E0413` | error | `?` outside a `let` initializer | `tests/fixtures/negative/propagate_outside_let.nv` |
| `E0414` | error | a user type cannot take type arguments yet | `tests/fixtures/negative/generic_user_type.nv` |
| `E0415` | error | a type argument is not a type | `tests/fixtures/negative/result_not_a_type_arg.nv` |
| `E0420` | error | a slice bound is not `i64` | `tests/fixtures/negative/slice_bound_type.nv` |
| `E0421` | error | only an array can be sliced | `tests/fixtures/negative/slice_not_an_array.nv` |
| `E0422` | error | a slice is read-only | `tests/fixtures/negative/slice_read_only.nv` |
| `E0423` | error | a slice cannot be a `mut` argument | `tests/fixtures/negative/slice_mut_argument.nv` |
| `E0425` | error | `array_zeros` needs a zero value | `tests/fixtures/negative/array_zeros_str.nv` |
| `E0426` | error | an array element cannot own storage | `tests/fixtures/negative/array_of_owning_values.nv` |
| `E0427` | error | a vector element cannot own storage | `tests/fixtures/negative/vector_of_owning_values.nv` |
| `E0428` | error | an owning value has exactly one owner | `tests/fixtures/negative/string_field.nv`, `vector_of_owning_values.nv` |
| `E0429` | error | `vec_new` needs an annotated element type | `tests/fixtures/negative/vector_new_without_element_type.nv` |
| `E0430` | error | a deferred block cannot leave the function | `tests/fixtures/negative/defer_returns.nv` |
| `E0431` | error | no loop carries this label | `tests/fixtures/negative/label_unknown.nv` |
| `E0432` | error | a label must name the loop it is written on | `tests/fixtures/negative/label_not_a_loop.nv` |
| `E0433` | error | the function is not public | `tests/fixtures/modules/private_module` |
| `E0434` | error | only a function can be `pub` yet | `tests/fixtures/negative/pub_not_a_function.nv` |
| `E0435` | error | duplicate type parameter | `tests/fixtures/negative/duplicate_type_param.nv` |
| `E0436` | error | cannot infer a type argument | `tests/fixtures/negative/cannot_infer_type_argument.nv` |
| `E0437` | error | a generic declaration is not a type | `tests/fixtures/negative/generic_used_as_type.nv` |
| `E0438` | error | type arguments come before the arguments of a call | `tests/fixtures/negative/type_args_on_plain_function.nv` |
| `E0439` | error | the call target is not a function | `tests/fixtures/negative/call_through_non_function.nv` |
| `E0441` | error | a call cannot go through a field | `tests/fixtures/negative/call_through_field.nv` |
| `E0442` | error | function values cannot be compared | `tests/fixtures/negative/compare_function_values.nv` |
| `E0443` | error | a function type has no mutable parameter | `tests/fixtures/negative/mutable_in_function_type.nv` |
| `E0444` | error | a mutating vector call needs a writable target | `tests/fixtures/negative/vector_mutated_through_a_borrow.nv` |
| `E0445` | error | a moved value cannot be used again | `tests/fixtures/negative/vector_moved_into_another_binding.nv`, `string_moved_into_another_binding.nv` |
| `E0446` | error | only an owning local can be moved | `tests/fixtures/negative/string_returned_from_a_borrow.nv`, `vector_reassigned_in_a_borrow.nv` |

**Types and semantics**

| Code | Severity | Meaning | Triggered by |
|---|---|---|---|
| `E0301` | error | reserved builtin name | `tests/fixtures/negative/sem_reserved_variable.nv` |
| `E0302` | error | duplicate function | `tests/fixtures/negative/sem_duplicate_function.nv` |
| `E0303` | error | missing fn main | `tests/fixtures/negative/sem_missing_main.nv` |
| `E0304` | error | invalid main signature | `tests/fixtures/negative/sem_main_signature.nv` |
| `E0305` | error | void parameter type | `tests/fixtures/negative/sem_void_param.nv` |
| `E0306` | error | missing return on some path | `tests/fixtures/negative/sem_missing_return.nv` |
| `E0307` | error | duplicate variable in the same scope | `tests/fixtures/negative/sem_duplicate_variable.nv` |
| `E0308` | error | unknown variable | `tests/fixtures/negative/sem_unknown_variable.nv` |
| `E0309` | error | void value in a variable | `tests/fixtures/negative/sem_void_value.nv` |
| `E0310` | error | void variable type | `tests/fixtures/negative/sem_void_annotation.nv` |
| `E0311` | error | annotation does not match the initializer | `tests/fixtures/negative/sem_annotation_mismatch.nv` |
| `E0312` | error | assignment type mismatch | `tests/fixtures/negative/sem_assign_mismatch.nv` |
| `E0313` | error | expression statement must be a call | `tests/fixtures/negative/sem_expr_statement.nv` |
| `E0314` | error | void call used as a value | `tests/fixtures/negative/sem_void_return.nv` |
| `E0315` | error | return type mismatch | `tests/fixtures/negative/sem_return_mismatch.nv` |
| `E0316` | error | if condition must be bool | `tests/fixtures/negative/sem_if_condition.nv` |
| `E0317` | error | while condition must be bool | `tests/fixtures/negative/sem_while_condition.nv` |
| `E0318` | error | unary operator type mismatch | `tests/fixtures/negative/sem_unary_operand.nv` |
| `E0319` | error | operator requires two i64 values | `tests/fixtures/negative/sem_ordering_operand.nv` |
| `E0320` | error | equality requires two values of the same type | `tests/fixtures/negative/sem_equality_types.nv` |
| `E0321` | error | logical operator requires two bool | `tests/fixtures/negative/sem_logical_operands.nv` |
| `E0322` | error | wrong number of arguments to print | `tests/fixtures/negative/sem_print_arity.nv` |
| `E0323` | error | unsupported print argument type | `tests/fixtures/negative/sem_print_type.nv` |
| `E0324` | error | wrong number of arguments to read_i64 | `tests/fixtures/negative/sem_read_arity.nv` |
| `E0325` | error | unknown function | `tests/fixtures/negative/sem_unknown_function.nv` |
| `E0326` | error | wrong number of arguments | `tests/fixtures/negative/sem_argument_count.nv` |
| `E0327` | error | argument type mismatch | `tests/fixtures/negative/sem_argument_type.nv` |
| `E0328` | error | array index must be i64 | `tests/fixtures/negative/array_index_type.nv` |
| `E0329` | error | only an array can be indexed | `tests/fixtures/negative/array_index_scalar.nv` |
| `E0330` | error | array returned or passed by value | `tests/fixtures/negative/array_return.nv` |
| `E0331` | error | arrays cannot be copied | `tests/fixtures/negative/array_copy.nv` |
| `E0332` | error | array variable cannot be reassigned | `tests/fixtures/negative/array_reassign.nv` |
| `E0333` | error | mutable array argument must be an owned variable | `tests/fixtures/negative/array_mut_from_borrow.nv` |
| `E0334` | error | array literal element type mismatch | `tests/fixtures/negative/array_literal_mixed.nv` |
| `E0335` | error | empty array literal needs a type annotation | `tests/fixtures/negative/array_literal_empty.nv` |
| `E0336` | error | element assignment through a read-only borrow | `tests/fixtures/negative/array_readonly_write.nv` |
| `E0337` | error | wrong number of arguments to len | `tests/fixtures/negative/array_len_arity.nv` |
| `E0338` | error | len expects an array | `tests/fixtures/negative/array_len_type.nv` |
| `E0339` | error | wrong number of arguments to array_zeros | `tests/fixtures/negative/array_zeros_arity.nv` |
| `E0340` | error | array_zeros expects an i64 length | `tests/fixtures/negative/array_zeros_type.nv` |
| `E0341` | error | mut on a non-array parameter | `tests/fixtures/negative/array_mut_scalar.nv` |
| `E0342` | error | the same array borrowed mutably twice | `tests/fixtures/negative/array_double_mut.nv` |
| `E0343` | error | 'break' outside a loop | `tests/fixtures/negative/ctl_break_outside_loop.nv` |
| `E0344` | error | 'continue' outside a loop | `tests/fixtures/negative/ctl_continue_outside_loop.nv` |
| `E0345` | error | implicit conversion between i64 and f64 | `tests/fixtures/negative/float_mixed_arithmetic.nv` |
| `E0346` | error | invalid floating-point literal | `tests/fixtures/negative/float_literal_overflow.nv`, `float_malformed_exponent.nv` |
| `E0347` | error | invalid numeric conversion argument | `tests/fixtures/negative/float_conversion_argument.nv` |
| `E0348` | error | wrong number of arguments to read_f64 | `tests/fixtures/negative/float_read_arity.nv` |
| `E0349` | error | for-loop bound must be i64 | `tests/fixtures/negative/ctl_for_bound_type.nv` |
| `E0350` | error | for loop cannot iterate over this value | `tests/fixtures/negative/ctl_for_iterable_type.nv` |
| `E0351` | error | the loop variable cannot be assigned | `tests/fixtures/negative/ctl_loop_variable_assignment.nv` |
| `E0352` | error | module not found | `tests/test_modules.py: test_missing_module_lists_both_search_locations` |
| `E0353` | error | modules import each other in a cycle | `tests/fixtures/modules/cycle/` |
| `E0354` | error | import after a function definition | `tests/fixtures/negative/mod_import_after_function.nv` |
| `E0355` | error | module imported twice | `tests/fixtures/modules/duplicate_import/` |
| `E0356` | error | module escapes the project boundary | `tests/test_modules.py: test_an_import_cannot_escape_the_project_boundary` |
| `E0357` | error | module defines fn main | `tests/fixtures/modules/main_in_module/` |
| `E0358` | error | function belongs to another module | `tests/fixtures/modules/unqualified/` |
| `E0359` | error | module is not imported | `tests/fixtures/modules/not_imported/` |
| `E0360` | error | a local module shadows a standard module | `tests/fixtures/modules/shadow/` |
| `E0361` | error | standard module version mismatch | `tests/test_modules.py: test_the_standard_library_version_is_checked` |

**Resource limits**

| Code | Severity | Meaning | Triggered by |
|---|---|---|---|
| `E0401` | error | nesting too deep | `tests/fixtures/negative/limit_long_chain.nv` |
| `E0402` | error | compiler recursion limit reached | — |

**Command line and host**

| Code | Severity | Meaning | Triggered by |
|---|---|---|---|
| `E0501` | error | invalid C compiler command | — |
| `E0502` | error | empty C compiler command | — |
| `E0503` | error | C compiler not found | — |
| `E0504` | error | output path equals the source path | — |
| `E0505` | error | output directory does not exist | — |
| `E0506` | error | directory does not exist | `tests/test_files.py: test_a_missing_grant_directory_is_reported` |
| `E0507` | error | path is not a directory | `tests/test_files.py: test_a_grant_that_is_not_a_directory_is_reported` |

**Sandbox and isolation**

| Code | Severity | Meaning | Triggered by |
|---|---|---|---|
| `E0601` | error | requested isolation is unavailable | `--isolation required` on a host without Linux namespaces |
| `E0602` | error | sandboxed program was stopped | a child process survived termination of the program |

**Warnings**

| Code | Severity | Meaning | Triggered by |
|---|---|---|---|
| `W0001` | warning | unreachable statement | `tests/fixtures/warnings/unreachable.nv` |
| `W0002` | warning | unused import | `tests/fixtures/modules/unused_import/` |

## Warnings

Warnings are non-fatal: the program still compiles and runs, and the warning is
written to `stderr`. A statement that cannot be reached because the preceding
statement always returns, breaks or continues is reported as `W0001`; the
statement is still present in the generated C, so remove it rather than relying
on the warning. An import that no qualified call uses is `W0002`: the module is
loaded anyway, so the warning costs nothing to act on. A generic function that
no call instantiates is `W0003`: its body mentions type parameters, so it has
never been type-checked, and the warning says so instead of letting dead generic
code look verified.

```nava
fn f() -> i64 {
    return 1
}

fn main() {
    print(f())
}
```

See `tests/fixtures/warnings/unreachable.nv`.

## Implementation limits

| Limit | Value | Behaviour when exceeded |
|---|---|---|
| Nesting limit (`MAX_NESTING`) | 200 | `E0401` |
| Literal digit limit (`MAX_LITERAL_DIGITS`) | 19 | `E0108` |
| Array element limit (`MAX_ARRAY_ELEMENTS`) | 4194304 | runtime error, exit status 1 |
| Float literal digits (`MAX_FLOAT_DIGITS`) | 40 | `E0346` |
| File read limit (`MAX_FILE_BYTES`) | 1048576 | runtime error, exit status 1 |
| Total read limit (`MAX_READ_TOTAL_BYTES`) | 4194304 | runtime error, exit status 1 |
| Read call limit (`MAX_READ_CALLS`) | 64 | runtime error, exit status 1 |

The nesting counter is shared by function bodies and blocks, parenthesised
subexpressions, unary operator chains and the depth of the expression tree
(which also bounds long left-associative chains such as `1 + 1 + 1 + ...`).
These four numbers are also compiled into the generated runtime from
`nava_lang/limits.py`, so the documented value and the enforced value cannot
drift apart. The limit exists so that pathological input produces a diagnostic
instead of a Python traceback; it is not a tuning knob. If the guard is ever bypassed, the
`nava` command still reports `E0402` rather than crashing.

## Exit status

| Status | Meaning |
|---|---|
| `0` | success |
| `2` | front-end or command-line error (a diagnostic was printed) |
| `1` | the compiled program failed at run time (checked arithmetic, invalid input) |
| `124` | `nava run --timeout` expired |
| `130` | interrupted with Ctrl-C |

A program's own `main -> i64` result becomes the exit status modulo 256, so
`return 300` exits with 44 and `return -1` with 255. Those values are the
program's, not the compiler's.

## Not implemented

There is no `HashMap` or `HashSet` (they arrive with the standard library in
N12), no arrays of arrays, no array copies, no array results from a function,
no string indexing or string input, no floating-point `%`, no pointers, FFI,
networking, dynamic loading or package resolution. Module imports, a
capability-limited file API, `Vec(T)`, `String` and the `str_*` functions, the
`math` module and `pub` visibility are implemented, as described above; a
package manager is not.

Edition 2 adds structs, enums, `match`, `Option`/`Result`, `?`, arrays of
structs and of enums, slices, `defer`, labelled loops, `pub`, generic
declarations of your own, function values with the type `fn(i64) -> i64`, growing
vectors and growing strings, and moves of both. It does not add closures or
function literals, `match` as an expression, generic arrays, type-parameter
bounds, struct or enum equality, printing a struct, an enum, a function value, a
vector or a `String`, or a field, payload, type argument or element that owns a
`Vec` or a `String` — that is the storage work of N11f, and nothing here is a
promise that it exists today.

An array keeps the length it was created with; `Vec(T)` is the type that grows.
This version cannot express all ordinary C or C++ tasks. Save generated
C with `nava emit-c`; building requires C11 and the C standard library. GCC and
Clang can use built-in overflow checks; a portable C11 fallback is also
provided.
