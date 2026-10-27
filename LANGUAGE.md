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
- **Keywords** — `fn`, `let`, `if`, `else`, `while`, `return`, `true`, `false`,
  `mut` (only in a parameter type, see [Arrays and ownership](#arrays-and-ownership)).
- **Identifiers** — ASCII letters, digits and `_`, not starting with a digit.
- **Types** — `i64`, `bool`, `str`, `void`, and the array types `[i64]`,
  `[bool]`, `[str]`. Any other name in a type position is `E0203`; so are
  `[void]` and a nested array such as `[[i64]]`.
- **Integer literals** — decimal digits, optionally separated by single
  underscores (`1_000_000`). A literal must fit in `i64`
  (-9223372036854775808 to 9223372036854775807). The single negation
  `-9223372036854775808` is accepted because its positive counterpart does not
  fit; `-` applied to any larger magnitude is `E0108` or an overflow at run
  time.
- **Strings** — double-quoted UTF-8 with JSON escapes (`\"`, `\\`, `\n`, `\t`,
  `\uXXXX`, and the other JSON escapes). A literal newline inside a string is
  `E0103`; an invalid escape is `E0104`; NUL is `E0105`; an unpaired surrogate
  is `E0106`.
- **Operators** — `( ) [ ] { } : , + - * / % = ! < > ;` and the two-character
  `->`, `<=`, `>=`, `==`, `!=`, `&&`, `||`. Delimiters must match: an unmatched
  `)` is `E0109`, an unmatched `]` is `E0112`, an unclosed `(` is `E0110` and
  an unclosed `[` is `E0113`.

## Grammar

The grammar below is the whole language. Statements are separated by a newline
or `;`; a separator is not required before `}`.

```text
program        = { function } end_of_file ;
function       = "fn" IDENT "(" [ parameters ] ")" [ "->" type ] block ;
parameters     = parameter { "," parameter } ;
parameter      = IDENT ":" [ "mut" ] type ;
type           = "i64" | "bool" | "str" | "void"
               | "[" ( "i64" | "bool" | "str" ) "]" ;
block          = "{" { separator } { statement { separator } } "}" ;
statement      = let | assignment | element_assignment | if_statement
               | while_statement | return_statement | call ;

let            = "let" IDENT [ ":" type ] "=" expression ;
assignment     = IDENT "=" expression ;
element_assignment = IDENT "[" expression "]" "=" expression ;
if_statement   = "if" expression block [ "else" ( if_statement | block ) ] ;
while_statement= "while" expression block ;
return_statement = "return" [ expression ] ;
separator      = NEWLINE | ";" ;

expression     = logical_or ;
logical_or     = logical_and { "||" logical_and } ;
logical_and    = equality { "&&" equality } ;
equality       = ordering { ( "==" | "!=" ) ordering } ;
ordering       = additive { ( "<" | "<=" | ">" | ">=" ) additive } ;
additive       = multiplicative { ( "+" | "-" ) multiplicative } ;
multiplicative = unary { ( "*" | "/" | "%" ) unary } ;
unary          = ( "-" | "!" ) unary | primary ;
primary        = NUMBER | STRING | "true" | "false" | "(" expression ")"
               | IDENT | call | element | array_literal ;
element        = IDENT "[" expression "]" ;
array_literal  = "[" [ expression { "," expression } ] "]" ;
call           = IDENT "(" [ arguments ] ")" ;
arguments      = expression { "," expression } ;
```

All binary operators are left-associative. Only a call may be used as a
statement (`E0313` otherwise).

## Types and values

| Type | Values |
|---|---|
| `i64` | Integers from -9223372036854775808 to 9223372036854775807. |
| `bool` | `true` and `false`. Conditions require `bool`; integers are not converted. |
| `str` | Double-quoted UTF-8 strings. NUL is not allowed. |
| `void` | No function result; not a variable or parameter type. |
| `[i64]`, `[bool]`, `[str]` | A fixed-length sequence of one element type. The length is set when the array is created and never changes. |

Strings can be passed to functions, returned, printed and compared for
equality. Equality compares UTF-8 bytes without Unicode normalization.
Concatenation, string indexing, string length and string input are not
implemented: `len` accepts an array, not a `str`. There are
no implicit conversions between `i64`, `bool` and `str`, and no conversions
between element types.

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
Nava 0.1 has no `for`, `break`, `continue`, exceptions or first-class
functions.

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
- `print(value)` accepts exactly one `i64`, `bool` or `str` and prints it
  followed by a newline. Booleans print as `true` or `false`. `print` returns
  `void` and cannot be used as a value (`E0309`, `E0314`).
- `len(a: [T]) -> i64` returns the number of elements (`E0337`, `E0338`).
- `array_zeros(n: i64) -> [i64]` allocates `n` zeroed elements and checks the
  length (`E0339`, `E0340`).
- All five names are reserved: they cannot be redefined (`E0301`).

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
| `E0203` | error | unknown type (also `[void]` and `[[i64]]`) | `tests/fixtures/negative/parse_unknown_type.nv`, `array_nested_type.nv`, `array_of_void.nv` |
| `E0204` | error | unclosed block | `tests/fixtures/negative/parse_unclosed_block.nv` |
| `E0205` | error | expected an expression | `tests/fixtures/negative/parse_expected_expression.nv` |

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

**Sandbox and isolation**

| Code | Severity | Meaning | Triggered by |
|---|---|---|---|
| `E0601` | error | requested isolation is unavailable | `--isolation required` on a host without Linux namespaces |
| `E0602` | error | sandboxed program was stopped | a child process survived termination of the program |

**Warnings**

| Code | Severity | Meaning | Triggered by |
|---|---|---|---|
| `W0001` | warning | unreachable statement | `tests/fixtures/warnings/unreachable.nv` |

## Warnings

Warnings are non-fatal: the program still compiles and runs, and the warning is
written to `stderr`. A statement that cannot be reached because the preceding
statement always returns is reported as `W0001`; the statement is still
present in the generated C, so remove it rather than relying on the warning.

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

The nesting counter is shared by function bodies and blocks, parenthesised
subexpressions, unary operator chains and the depth of the expression tree
(which also bounds long left-associative chains such as `1 + 1 + 1 + ...`).
The limit exists so that pathological input produces a diagnostic instead of a
Python traceback; it is not a tuning knob. If the guard is ever bypassed, the
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

There are no resizable collections, arrays of arrays, array copies, array
results from functions, floating-point values, string indexing, string
concatenation, string input, file APIs, pointers, modules, imports or FFI.
Array length is fixed at creation and the element types are exactly `i64`,
`bool` and `str`. This version cannot express all ordinary C or C++ tasks. Save generated
C with `nava emit-c`; building requires C11 and the C standard library. GCC and
Clang can use built-in overflow checks; a portable C11 fallback is also
provided.
