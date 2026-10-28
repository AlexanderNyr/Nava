from __future__ import annotations

from .diagnostics import make
from .lexer import lex
from .limits import I64_MAX, MAX_NESTING
from .model import (
    Arm, ArrayLiteral, Assign, Binary, Boolean, Break, Call, Continue, EnumDecl,
    Expr, ExprStmt, Field, Float, For, Function, If, Import, Index, IndexAssign,
    Let, Match, Member, MemberAssign, Number, Param, Program, Range, Return,
    String, Struct, StructLiteral, Token, Try, Unary, Variable, Variant,
    VariantValue, While,
)
from .model import NavaError

PRECEDENCE = {
    "||": 1, "&&": 2, "==": 3, "!=": 3,
    "<": 4, "<=": 4, ">": 4, ">=": 4,
    "+": 5, "-": 5, "*": 6, "/": 6, "%": 6,
}
TYPES = {"i64", "bool", "str", "void", "f64"}
#: Editions this compiler implements. An edition is frozen when it is released;
#: a new construct belongs to the next one.
EDITIONS = {1, 2}
#: The edition that introduced structs, named construction and member access.
STRUCT_EDITION = 2
#: Types that take type arguments, and how many. They are prelude enums: written
#: `Option(i64)` with parentheses, never `Option<i64>`, because `<` is already a
#: comparison operator and a model writing code should not have to know where the
#: parser switches personalities.
GENERIC_PRELUDE = {"Option": 1, "Result": 2}
#: Types an array may contain. There are no arrays of arrays and no arrays of
#: `void`; both are rejected in the parser because they are syntax-level shapes.
ELEMENT_TYPES = {"i64", "bool", "str"}


class Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.i = 0
        self.depth = 0
        #: The edition the file declares; 1 when it declares none.
        self.edition = 1
        #: Struct names declared anywhere in the file. The parser needs them to
        #: accept a struct name in a type position; the checker owns the rest.
        self.struct_names: set[str] = set()
        #: Enum names, for the same reason: `Shape` is a type, and `Shape::Circle`
        #: is a value rather than a module call.
        self.enum_names: set[str] = set()
        #: The instance the last `instantiation()` lookahead consumed.
        self.last_instantiation: str | None = None

    def enter(self, loc) -> None:
        self.depth += 1
        if self.depth > MAX_NESTING:
            raise make(
                "E0401",
                f"nesting is deeper than the supported limit of {MAX_NESTING}",
                loc,
                hint="simplify the expression or split it into named functions",
            )

    def leave(self) -> None:
        self.depth -= 1

    @property
    def current(self) -> Token:
        return self.tokens[self.i]

    def at(self, kind: str) -> bool:
        return self.current.kind == kind

    def take(self, kind: str) -> Token | None:
        if self.at(kind):
            token = self.current
            self.i += 1
            return token
        return None

    def expect(self, kind: str, description: str | None = None) -> Token:
        if not self.at(kind):
            actual = "end of file" if self.at("EOF") else repr(self.current.value)
            text = "" if self.at("EOF") else str(self.current.value)
            raise make(
                "E0201",
                f"expected {description or repr(kind)}, got {actual}",
                self.current.loc,
                self.current.loc.advance(text),
            )
        token = self.current
        self.i += 1
        return token

    def require_edition(self, loc, feature: str, code: str = "E0372") -> None:
        """An edition-2 construct in a file that did not declare edition 2."""
        if self.edition >= STRUCT_EDITION:
            return
        raise make(
            code,
            f"{feature} needs edition 2, but this file is edition {self.edition}",
            loc,
            hint="add `edition 2;` as the first line of the file",
        )

    def separators(self) -> None:
        while self.at("NL") or self.at(";"):
            self.i += 1

    def end_statement(self) -> None:
        if self.at(".."):
            raise make("E0350", "a range a..b is only allowed directly after `in`",
                       self.current.loc,
                       hint="write `for i in a..b`, or use a while loop with `i < b`")
        if self.at("NL") or self.at(";"):
            self.separators()
        elif not self.at("}") and not self.at("EOF"):
            raise make("E0202", "expected a newline, ';', or '}' after statement",
                       self.current.loc, hint="end the statement with a newline or ';'")

    def parse_type(self) -> str:
        if self.at("["):
            opening = self.expect("[")
            if self.at("["):
                raise make("E0206", "an array cannot contain another array",
                           self.current.loc, self.current.loc.advance("["),
                           "nested collections are not part of Nava 0.1")
            element = self.expect("IDENT", "an element type (i64, bool, or str)")
            if element.value not in ELEMENT_TYPES:
                raise make("E0206",
                           f"an array element type must be i64, bool, or str, got {element.value!r}",
                           element.loc, element.loc.advance(str(element.value)))
            self.expect("]", "']'")
            return f"[{element.value}]"
        token = self.expect("IDENT", "a type (i64, bool, str, void, or f64)")
        if self.at("(") and self.edition >= STRUCT_EDITION:
            return self.generic_type(token)
        if self.edition >= STRUCT_EDITION and (token.value in self.struct_names
                                               or token.value in self.enum_names):
            # A declared struct or enum is a type in edition 2. The checker
            # decides whether it may be a field, a parameter or a local.
            return str(token.value)
        if token.value not in TYPES:
            # The message text is part of the fixture contract: array types are
            # documented in LANGUAGE.md and the hint names the scalar set only.
            raise make("E0203", f"unknown type {token.value!r}; use i64, bool, str, or void",
                       token.loc, token.loc.advance(str(token.value)))
        return str(token.value)

    def generic_type(self, name: Token) -> str:
        """`Option(i64)` or `Result(i64, str)`: a prelude enum with arguments."""
        expected = GENERIC_PRELUDE.get(str(name.value))
        if expected is None:
            raise make(
                "E0414",
                f"{name.value!r} cannot take type arguments yet",
                name.loc, name.loc.advance(str(name.value)),
                hint="edition 2 parameterises only Option and Result; generic "
                     "declarations are a later increment",
            )
        self.expect("(", "'(' after a type name")
        arguments: list[str] = []
        while not self.at(")"):
            arguments.append(self.parse_type())
            if not self.take(","):
                break
        self.expect(")", "')'")
        if len(arguments) != expected:
            plural = "s" if expected != 1 else ""
            raise make(
                "E0412",
                f"{name.value} takes {expected} type argument{plural}, "
                f"got {len(arguments)}",
                name.loc, name.loc.advance(str(name.value)),
                hint="write " + ", ".join([f"Option(i64)", f"Result(i64, str)"][expected - 1:expected])
                if expected in (1, 2) else None,
            )
        return f"{name.value}({', '.join(arguments)})"

    def instantiation(self) -> str | None:
        """Read `Name(T, ...)` if the tokens ahead are exactly that, else None.

        Used where a type argument list could be confused with a call: `f(x)` is
        a call, `Option(i64)` is a type. The lookahead is bounded — the tokens
        must be IDENT '(', types, ')', '::' — so no backtracking is needed.
        """
        if not (self.at("IDENT") and self.tokens[self.i + 1].kind == "("
                and self.current.value in GENERIC_PRELUDE):
            return None
        saved = self.i
        name = self.current
        self.i += 2
        try:
            arguments: list[str] = []
            while not self.at(")"):
                if self.at("EOF") or self.at("NL"):
                    self.i = saved
                    return None
                arguments.append(self.parse_type())
                if not self.take(","):
                    break
            if not self.at(")") or self.tokens[self.i + 1].kind != "::":
                self.i = saved
                return None
            self.i += 1
        except NavaError as error:
            # A missing token means "this was not a type argument list" — a value
            # where a type belongs. A *named* error (an unknown type, a forbidden
            # element type) is real and must reach the author.
            if error.code != "E0201":
                raise
            self.i = saved
            return None
        expected = GENERIC_PRELUDE[str(name.value)]
        if len(arguments) != expected:
            raise make(
                "E0412",
                f"{name.value} takes {expected} type argument"
                f"{'s' if expected != 1 else ''}, got {len(arguments)}",
                name.loc, name.loc.advance(str(name.value)),
            )
        self.last_instantiation = f"{name.value}({', '.join(arguments)})"
        return self.last_instantiation

    def edition_declaration(self) -> None:
        """`edition 2;` — the first statement of the file, when it has one."""
        start = self.expect("EDITION")
        number = self.expect("NUMBER", "an edition number")
        value = int(number.value)
        if value not in EDITIONS:
            raise make(
                "E0371",
                f"unknown edition {value}; this compiler implements editions 1 and 2",
                number.loc,
                number.loc.advance(str(number.value)),
                hint="an edition is a version of the language, not a compiler flag",
            )
        self.edition = value
        self.end_statement()
        # Struct names are needed while parsing types, and a struct may be
        # declared after the function that uses it.
        if self.edition >= STRUCT_EDITION:
            self.struct_names = {
                str(self.tokens[index + 1].value)
                for index, token in enumerate(self.tokens[:-1])
                if token.kind == "STRUCT" and self.tokens[index + 1].kind == "IDENT"
            }
            self.enum_names = {
                str(self.tokens[index + 1].value)
                for index, token in enumerate(self.tokens[:-1])
                if token.kind == "ENUM" and self.tokens[index + 1].kind == "IDENT"
            }

    def struct_declaration(self) -> Struct:
        start = self.expect("STRUCT")
        self.require_edition(start.loc, "a struct declaration")
        name = self.expect("IDENT", "a struct name")
        if name.value in TYPES:
            raise make("E0203", f"{name.value!r} is a built-in type name",
                       name.loc, name.loc.advance(str(name.value)),
                       "pick a name that is not one of i64, bool, str, f64 or void")
        self.separators()
        self.expect("{", "'{'")
        fields: list[Field] = []
        self.separators()
        while not self.at("}"):
            if self.at("EOF"):
                raise make("E0204", "expected '}' before end of file", self.current.loc,
                           hint=f"the struct opened at line {start.loc.line} is not closed")
            field_name = self.expect("IDENT", "a field name")
            self.expect(":", "':' after a field name")
            typ = self.parse_type()
            fields.append(Field(str(field_name.value), typ, field_name.loc))
            if not self.take(","):
                self.separators()
                if not self.at("}"):
                    raise make("E0202", "expected ',' or '}' after a struct field",
                               self.current.loc,
                               hint="separate fields with ',' or a newline")
            self.separators()
        self.expect("}")
        return Struct(str(name.value), fields, start.loc)

    def enum_declaration(self) -> EnumDecl:
        start = self.expect("ENUM")
        self.require_edition(start.loc, "an enum declaration")
        name = self.expect("IDENT", "an enum name")
        if name.value in TYPES:
            raise make("E0203", f"{name.value!r} is a built-in type name",
                       name.loc, name.loc.advance(str(name.value)))
        self.separators()
        self.expect("{", "'{'")
        variants: list[Variant] = []
        self.separators()
        while not self.at("}"):
            if self.at("EOF"):
                raise make("E0204", "expected '}' before end of file", self.current.loc,
                           hint=f"the enum opened at line {start.loc.line} is not closed")
            variant = self.expect("IDENT", "a variant name")
            types: list[str] = []
            if self.take("("):
                while not self.at(")"):
                    types.append(self.parse_type())
                    if not self.take(","):
                        break
                self.expect(")")
            variants.append(Variant(str(variant.value), types, variant.loc))
            if not self.take(","):
                self.separators()
                if not self.at("}"):
                    raise make("E0202", "expected ',' or '}' after an enum variant",
                               self.current.loc,
                               hint="separate variants with ',' or a newline")
            self.separators()
        self.expect("}")
        return EnumDecl(str(name.value), variants, start.loc)

    def parse(self) -> Program:
        functions: list[Function] = []
        imports: list[Import] = []
        structs: list[Struct] = []
        enums: list[EnumDecl] = []
        self.separators()
        if self.at("EDITION"):
            self.edition_declaration()
            self.separators()
        while not self.at("EOF"):
            if self.at("EDITION"):
                token = self.current
                raise make(
                    "E0370",
                    "the edition declaration must be the first statement of the file",
                    token.loc,
                    token.loc.advance("edition"),
                    hint="move `edition 2;` above every import, struct and function",
                )
            if self.at("STRUCT"):
                structs.append(self.struct_declaration())
            elif self.at("ENUM"):
                enums.append(self.enum_declaration())
            elif self.at("IMPORT"):
                if functions:
                    raise make(
                        "E0354",
                        "an import must come before every function definition",
                        self.current.loc,
                        self.current.loc.advance("import"),
                        "move the import statements to the top of the file",
                    )
                imports.append(self.import_statement())
            else:
                functions.append(self.function())
            self.separators()
        program = Program(functions, imports=imports, structs=structs, enums=enums)
        program.edition = self.edition
        return program

    def import_statement(self) -> Import:
        start = self.expect("IMPORT")
        name = self.expect("IDENT", "a module name")
        if self.current.kind == "::":
            raise make(
                "E0201",
                "expected a module name, got '::'",
                self.current.loc,
                self.current.loc.advance("::"),
                "an import names one module: write `import math`, then call math::abs(x)",
            )
        self.end_statement()
        return Import(str(name.value), start.loc, name.loc)

    def function(self) -> Function:
        start = self.expect("FN", "'fn'")
        name = self.expect("IDENT", "function name")
        self.expect("(")
        params: list[Param] = []
        while not self.at(")"):
            p = self.expect("IDENT", "parameter name")
            self.expect(":")
            mutable = self.take("MUT") is not None
            typ = self.parse_type()
            params.append(Param(str(p.value), typ, p.loc, mutable))
            if not self.take(","):
                break
        self.expect(")")
        returns = "void"
        if self.take("->"):
            returns = self.parse_type()
        body = self.block()
        return Function(str(name.value), params, returns, body, start.loc)

    def block(self) -> list:
        self.separators()
        opening = self.expect("{")
        self.enter(opening.loc)
        try:
            body = []
            self.separators()
            while not self.at("}"):
                if self.at("EOF"):
                    raise make("E0204", "expected '}' before end of file", self.current.loc,
                               hint=f"the block opened at line {opening.loc.line} is not closed")
                body.append(self.statement())
                self.separators()
            self.expect("}")
        finally:
            self.leave()
        return body

    def statement(self):
        token = self.current
        if self.take("LET"):
            name = self.expect("IDENT", "variable name")
            annotation = self.parse_type() if self.take(":") else None
            self.expect("=")
            value = self.expression()
            self.end_statement()
            return Let(token.loc, str(name.value), annotation, value, name.loc)
        if self.at("MATCH"):
            return self.match_statement()
        if self.at("IF"):
            return self.if_statement()
        if self.take("WHILE"):
            condition = self.expression()
            return While(token.loc, condition, self.block())
        if self.take("FOR"):
            name = self.expect("IDENT", "loop variable name")
            self.expect("IN", "'in'")
            iterable = self.expression()
            if self.take(".."):
                # `a..b` is half-open; both bounds are evaluated once, before
                # the first iteration.
                iterable = Range(iterable.loc, iterable, self.expression())
            return For(token.loc, str(name.value), iterable, self.block(), name.loc)
        if self.take("BREAK"):
            self.end_statement()
            return Break(token.loc)
        if self.take("CONTINUE"):
            self.end_statement()
            return Continue(token.loc)
        if self.take("RETURN"):
            value = None if self.current.kind in ("NL", ";", "}", "EOF") else self.expression()
            self.end_statement()
            return Return(token.loc, value)
        if self.at("IDENT") and self.tokens[self.i + 1].kind == ".":
            target = self.postfix()
            if not isinstance(target, Member) or not self.take("="):
                raise make(
                    "E0313",
                    "only function calls can be used as statements",
                    target.loc,
                    hint="assign to the field, as in p.x = value",
                )
            self.require_edition(target.loc, "a field assignment")
            value = self.expression()
            self.end_statement()
            return MemberAssign(target.loc, target, value)
        if self.at("IDENT") and self.tokens[self.i + 1].kind == "[":
            # `a[i] = value` is an element assignment; `a[i]` alone is not a
            # statement, and the message names the rule instead of the token.
            name = self.expect("IDENT")
            self.expect("[")
            index = self.expression()
            self.expect("]")
            if not self.take("="):
                raise make("E0313", "only function calls can be used as statements",
                           token.loc,
                           hint=f"assign to the element, as in {name.value}[i] = value")
            value = self.expression()
            self.end_statement()
            return IndexAssign(token.loc, str(name.value), index, value, name.loc)
        if self.at("IDENT") and self.tokens[self.i + 1].kind == "=":
            name = self.expect("IDENT")
            self.expect("=")
            value = self.expression()
            self.end_statement()
            return Assign(name.loc, str(name.value), value)
        expr = self.expression()
        self.end_statement()
        return ExprStmt(token.loc, expr)

    def match_statement(self) -> Match:
        """`match value { Enum::Variant(a, b) => { ... } _ => { ... } }`."""
        start = self.expect("MATCH")
        self.require_edition(start.loc, "a match statement")
        value = self.expression()
        self.separators()
        self.expect("{", "'{'")
        self.separators()
        arms: list[Arm] = []
        while not self.at("}"):
            if self.at("EOF"):
                raise make("E0204", "expected '}' before end of file", self.current.loc,
                           hint=f"the match opened at line {start.loc.line} is not closed")
            arms.append(self.match_arm())
            self.separators()
        self.expect("}")
        if not arms:
            raise make("E0396", "a match needs at least one arm", start.loc,
                       start.loc.advance("match"),
                       hint="list every variant of the enum, or add `_ => { ... }`")
        return Match(start.loc, value, arms)

    def match_arm(self) -> Arm:
        token = self.current
        bindings: list[str] = []
        variant: str | None = None
        instance: str | None = None
        if self.at("IDENT") and token.value == "_":
            self.i += 1
        elif self.at("IDENT") and self.edition >= STRUCT_EDITION \
                and self.instantiation() is not None:
            instance = self.last_instantiation
            # `Result(i64, str)::Ok(v)`: the enum is the whole instantiated type,
            # so an arm cannot be read against a different instantiation.
            self.expect("::")
            name = self.expect("IDENT", "a variant name after '::'")
            variant = str(name.value)
            if self.take("("):
                while not self.at(")"):
                    binding = self.expect("IDENT", "a binding name for a variant field")
                    bindings.append(str(binding.value))
                    if not self.take(","):
                        break
                self.expect(")")
        elif self.at("IDENT") and self.tokens[self.i + 1].kind == "::":
            self.i += 2
            name = self.expect("IDENT", "a variant name after '::'")
            variant = str(name.value)
            if self.take("("):
                while not self.at(")"):
                    binding = self.expect("IDENT", "a binding name for a variant field")
                    bindings.append(str(binding.value))
                    if not self.take(","):
                        break
                self.expect(")")
        elif self.at("IDENT"):
            raise make(
                "E0392",
                f"a variant pattern must name its enum, as in Shape::{token.value}(...)",
                token.loc, token.loc.advance(str(token.value)),
                hint="`_` is the wildcard arm; every other pattern is Enum::Variant",
            )
        else:
            raise make("E0205", "expected a pattern or `_`", token.loc)
        self.expect("=>", "'=>' after a pattern")
        return Arm(token.loc, variant, bindings, self.block(), instance=instance)

    def if_statement(self) -> If:
        start = self.expect("IF")
        condition = self.expression()
        then_body = self.block()
        self.separators()
        else_body = None
        if self.take("ELSE"):
            if self.at("IF"):
                else_body = [self.if_statement()]
            else:
                else_body = self.block()
        return If(start.loc, condition, then_body, else_body)

    def expression(self, min_prec: int = 1) -> Expr:
        left = self.unary()
        while self.current.kind in PRECEDENCE and PRECEDENCE[self.current.kind] >= min_prec:
            op = self.current
            self.i += 1
            right = self.expression(PRECEDENCE[op.kind] + 1)
            left = Binary(op.loc, op.kind, left, right)
        return left

    def unary(self) -> Expr:
        token = self.current
        if self.take("-"):
            # Permit the one negative value whose positive counterpart does not fit i64.
            if self.at("NUMBER") and self.current.value == 2**63:
                self.i += 1
                return Number(token.loc, -(2**63))
            self.enter(token.loc)
            try:
                return Unary(token.loc, "-", self.unary())
            finally:
                self.leave()
        if self.take("!"):
            self.enter(token.loc)
            try:
                return Unary(token.loc, "!", self.unary())
            finally:
                self.leave()
        return self.postfix()

    def postfix(self) -> Expr:
        """`primary`, then any number of `.field` reads, then an optional `?`."""
        expr = self.primary()
        while self.at("."):
            dot = self.current
            self.require_edition(dot.loc, "a field access")
            self.i += 1
            field = self.expect("IDENT", "a field name after '.'")
            expr = Member(dot.loc, expr, str(field.value), field.loc)
        if self.at("?"):
            mark = self.current
            self.require_edition(mark.loc, "the `?` operator")
            self.i += 1
            expr = Try(mark.loc, expr)
        return expr

    def arguments(self) -> tuple[list[tuple[str, Expr]] | None, list[Expr]]:
        """Arguments of `name(...)`.

        Returns `(fields, args)`: `fields` is not None when every argument is
        written as `name: value`, which is a struct construction; `args` is the
        positional list otherwise. Mixing the two is rejected here, because the
        diagnostics for it would have to guess which one the author meant.
        """
        if self.at(")"):
            self.i += 1
            return None, []
        labelled = self.at("IDENT") and self.tokens[self.i + 1].kind == ":"
        if labelled:
            self.require_edition(self.current.loc, "named construction")
        fields: list[tuple[str, Expr]] = []
        args: list[Expr] = []
        while not self.at(")"):
            if labelled:
                key = self.expect("IDENT", "a field name")
                self.expect(":", "':' after a field name")
                fields.append((str(key.value), self.expression()))
            else:
                if self.at("IDENT") and self.tokens[self.i + 1].kind == ":":
                    raise make(
                        "E0381",
                        "positional and named arguments cannot be mixed",
                        self.current.loc,
                        hint="name every field, as in Point(x: 1, y: 2), or name none",
                    )
                args.append(self.expression())
            if not self.take(","):
                break
        self.expect(")")
        return (fields if labelled else None), args

    def primary(self) -> Expr:
        token = self.current
        if self.take("NUMBER"):
            value = int(token.value)
            if value > I64_MAX:
                raise make("E0108", "integer literal is outside the i64 range", token.loc,
                           token.loc.advance(str(token.value)),
                           "i64 holds -9223372036854775808 to 9223372036854775807")
            return Number(token.loc, value)
        if self.take("FLOAT"):
            return Float(token.loc, float(token.value))
        if self.take("STRING"):
            return String(token.loc, str(token.value))
        if self.take("TRUE"):
            return Boolean(token.loc, True)
        if self.take("FALSE"):
            return Boolean(token.loc, False)
        if self.take("["):
            self.enter(token.loc)
            try:
                items: list[Expr] = []
                while not self.at("]"):
                    if self.at("EOF"):
                        raise make("E0113", "unclosed '['", token.loc,
                                   hint="close the array literal with ']'")
                    item = self.expression()
                    if isinstance(item, ArrayLiteral):
                        raise make("E0203", "an array cannot contain another array",
                                   item.loc)
                    items.append(item)
                    if not self.take(","):
                        break
                self.expect("]", "']'")
            finally:
                self.leave()
            return ArrayLiteral(token.loc, items)
        if self.at("IDENT") and self.edition >= STRUCT_EDITION:
            instance = self.instantiation()
            if instance is not None:
                base = instance.split("(", 1)[0]
                self.expect("::")
                variant = self.expect("IDENT", "a variant name after '::'")
                args: list[Expr] = []
                if self.take("("):
                    while not self.at(")"):
                        args.append(self.expression())
                        if not self.take(","):
                            break
                    self.expect(")")
                return VariantValue(token.loc, base, str(variant.value), args,
                                    token.loc, variant.loc, instance=instance)
        if self.take("IDENT"):
            name = str(token.value)
            if self.take("::"):
                target = self.expect("IDENT", "a function name after '::'")
                if name in self.enum_names and self.edition >= STRUCT_EDITION:
                    # A variant of a declared enum, not a call into a module.
                    if not self.take("("):
                        return VariantValue(token.loc, name, str(target.value), [],
                                            token.loc, target.loc)
                    args: list[Expr] = []
                    while not self.at(")"):
                        args.append(self.expression())
                        if not self.take(","):
                            break
                    self.expect(")")
                    return VariantValue(token.loc, name, str(target.value), args,
                                        token.loc, target.loc)
                # `module::function(...)`: a qualified call into another module.
                if not self.take("("):
                    raise make(
                        "E0201",
                        "expected '(' after a qualified function name",
                        self.current.loc,
                        hint="a module exposes functions only: write math::abs(x)",
                    )
                args = []
                while not self.at(")"):
                    args.append(self.expression())
                    if not self.take(","):
                        break
                self.expect(")")
                return Call(token.loc, str(target.value), args, module=name)
            if self.take("["):
                index = self.expression()
                self.expect("]", "']'")
                return Index(token.loc, Variable(token.loc, name), index)
            if self.take("("):
                fields, args = self.arguments()
                if self.at("::"):
                    # `Option(1, 2)::Some(1)` or `P(i64)::Some(1)`: the type
                    # arguments were not types, so the lookahead declined and the
                    # call was parsed as a call. Say what is actually wrong.
                    if name in GENERIC_PRELUDE:
                        count = GENERIC_PRELUDE[name]
                        raise make(
                            "E0415",
                            f"{name} takes {count} type argument"
                            f"{'s' if count != 1 else ''}, and they must be types",
                            token.loc, token.loc.advance(name),
                            hint=f"write {name}(i64" + (", str)" if count == 2 else ")"),
                        )
                    raise make(
                        "E0414",
                        f"{name!r} cannot take type arguments",
                        token.loc, token.loc.advance(name),
                        hint="edition 2 parameterises only Option and Result",
                    )
                if fields is not None:
                    return StructLiteral(token.loc, name, fields)
                return Call(token.loc, name, args)
            return Variable(token.loc, name)
        if self.take("("):
            self.enter(token.loc)
            try:
                result = self.expression()
                self.expect(")")
                return result
            finally:
                self.leave()
        raise make("E0205", "expected an expression", token.loc)


def parse(source: str, path: str | None = None) -> Program:
    """Parse one file. `path` is recorded in every location for multi-file use."""
    return Parser(lex(source, path)).parse()
