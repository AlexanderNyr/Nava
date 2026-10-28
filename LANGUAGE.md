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

A struct is a value with named fields. In edition 2 a field type is `i64`,
`bool`, `f64`, or another struct; `str` and array fields are refused with
`E0378` because both carry ownership, which edition 2 does not yet define — they
arrive with the memory work, not by accident. A struct name is shared by every
module of the program (`E0380` for a duplicate), and a struct may not contain
itself directly or through another struct (`E0377`), because a struct is stored
inline and a recursive type would have no finite size.

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

Nothing is shared and nothing is freed: a struct field cannot own storage until
the owning types arrive (see `E0378`). This is why a struct is not printable
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
fixed, positional list of fields, each `i64`, `bool`, `f64`, a struct or another
enum (`E0399` for `str` and arrays, which wait for the memory work, and `E0203`
for an unknown name). Variant names are unique inside their enum (`E0391`) and
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

`match` is not an expression in edition 2. There is no `Option`/`Result` and no
`?`, no guards, no literal or range patterns and no matching on `i64` values:
those are N10c and later, and nothing above claims otherwise.

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
| `E0378` | error | unsupported struct field type | `tests/fixtures/negative/struct_field_str.nv` |
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
| `E0399` | error | unsupported enum variant field type | `tests/fixtures/negative/enum_variant_str.nv` |

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
loaded anyway, so the warning costs nothing to act on.

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

There are no resizable collections, arrays of arrays, `f64` arrays, array
copies, array results from functions, labelled `break`/`continue`, string
indexing, string concatenation, string input, mathematical functions such as
`sqrt`, floating-point `%`, pointers, FFI, networking, package resolution, a
module visibility keyword, or more than one file per module. Module imports and
a capability-limited file API are implemented, as described above; a package
manager is not.
Edition 2 adds structs, enums and `match`; it does not add `Option`/`Result`,
`?`, generics, generic containers, string fields, arrays of structs, struct or
enum equality, printing a struct or an enum, or module visibility keywords.
Those are N10c and later in `INDEPENDENCE.md`, and nothing here is a promise
that they exist today.

Array length is fixed at creation and the element types are exactly `i64`,
`bool` and `str`. This version cannot express all ordinary C or C++ tasks. Save generated
C with `nava emit-c`; building requires C11 and the C standard library. GCC and
Clang can use built-in overflow checks; a portable C11 fallback is also
provided.
