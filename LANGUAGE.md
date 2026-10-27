# Nava 0.1: Language Reference

Nava 0.1 is a small, statically typed language. This document describes the
implemented behaviour; it is not a promise of future compatibility.

## Program structure

A program consists of functions. It must define `fn main()` with no
parameters and either `void` (the default) or `i64` as its return type.
Functions may be called before their definition and may be recursive.

```text
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
`-> type` returns `void`. Every path through a non-`void` function must
return a value. The return analysis is conservative: even `while true` is
not treated as a guaranteed return.

Statements are separated by a newline or `;`; a separator is not required
before `}`. Newlines inside parentheses are ignored. Outside parentheses, an
expression cannot be split across lines. Comments start with `#` or `//` and
continue to the end of the line. Identifiers contain ASCII letters, digits and
`_`, and cannot start with a digit.

## Types and values

| Type | Values |
|---|---|
| `i64` | Integers from -2^63 to 2^63-1. Decimal literals may use `_` as a separator, for example `1_000`. |
| `bool` | `true` and `false`. Conditions require `bool`; integers are not converted automatically. |
| `str` | Double-quoted UTF-8 strings using JSON escapes (`\"`, `\\`, `\n`, `\t`, `\uXXXX`, etc.). NUL is not allowed. |
| `void` | No function result; not a variable or parameter type. |

Strings can be passed to functions, returned, printed and compared for
equality. Equality compares UTF-8 bytes without Unicode normalization.
Concatenation, indexing and string input are not implemented.

```text
let count = 0                  # inferred from the value
let count2: i64 = 1            # optional type annotation
count = count + 1              # locals and parameters are mutable
```

Variables are block-scoped. Redeclaring a name in the same block is an error;
shadowing it in a nested block is permitted. The initializer is evaluated
before the new variable is declared: the right-hand `x` in `let x = x + 1`
inside a nested block refers to the outer variable. There are no global
variables or implicit type conversions.

## Control flow

```text
if x < 0 {
    print("negative")
} else if x == 0 {
    print("zero")
} else {
    print("positive")
}

while x > 0 {
    x = x - 1
}
```

Braces are required. Use `return value` in a function with a result. A
`void` function may use a bare `return` or reach the end of its body. Only
function calls may be used as expression statements. Nava 0.1 has no `for`,
`break`, `continue`, exceptions or first-class functions.

## Operators

From highest to lowest precedence: unary `-`, `!`; `*`, `/`, `%`; `+`, `-`;
`<`, `<=`, `>`, `>=`; `==`, `!=`; `&&`; `||`. Binary operators in the same
precedence group are left-associative. Parentheses change precedence.

- Arithmetic and ordering comparisons accept only `i64`.
- `==` and `!=` compare values of the same type (`i64`, `bool` or `str`).
- `!`, `&&` and `||` accept `bool`; `&&` and `||` evaluate their right-hand
  side only when needed.
- Arguments and subexpressions are evaluated left to right, including calls
  with side effects.
- `i64` arithmetic checks for overflow. Division by zero, remainder by zero,
  `-2^63 / -1`, `-2^63 % -1` and other overflows cause a runtime error.
  Division truncates toward zero; the remainder has the sign of the dividend.

A runtime error is written to `stderr` and exits with a nonzero status.
Recursion and loops are not limited automatically.

## Built-in functions

```text
let a = read_i64()
let b = read_i64()
print(a + b)
print(true)
print("ready")
```

- `read_i64() -> i64` reads one whitespace-delimited decimal integer from
  `stdin`, with an optional `+` or `-` sign. Invalid input, an out-of-range
  value and EOF are runtime errors.
- `print(value)` accepts one `i64`, `bool` or `str` and prints it followed by
  a newline. Boolean values are printed as `true` or `false`. The function
  does not return a value.

## Not implemented

There are no arrays, collections, floating-point values, string input, file
APIs, language-level dynamic allocation, modules, imports or FFI. This
version cannot express all ordinary C or C++ tasks. Save generated C with
`nava emit-c`; building requires C11 and the C standard library. GCC and Clang
can use built-in overflow checks; a portable C11 fallback is also provided.
