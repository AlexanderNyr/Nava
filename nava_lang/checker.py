from __future__ import annotations

import copy

from .diagnostics import make, severity_of
from .limits import MAX_NESTING
from .model import (
    Arm, ArrayLiteral, Assign, Binary, Boolean, Break, Call, Continue, Defer,
    Diagnostic, EnumDecl, Expr, ExprStmt, Field, Float, For, Function, If, Import,
    Index, IndexAssign, Let, Match, Member, MemberAssign, Number, Param, Program,
    Range, Return, Slice, String, Struct, StructLiteral, Symbol, Try, Unary,
    Variable, Variant, VariantValue, While, function_signature, function_type_parts,
    is_function_type,
)

#: Name of the entry module: its functions need no qualification.
ENTRY_MODULE = ""
BUILTINS = {
    "print", "read_i64", "read_f64", "len", "array_zeros", "to_f64", "to_i64",
    "bytes_len", "chars_len", "byte_at", "read_file", "write_file",
}
#: Types a value can have (everything except `void`).
VALUE_TYPES = {"i64", "bool", "str", "f64"}
#: Types `print` accepts: every value type.
PRINTABLE_TYPES = VALUE_TYPES
#: Types that support the checked-or-IEEE arithmetic operators.
ARITHMETIC_TYPES = ("i64", "f64")
#: Prelude enum templates: name -> (parameters, variants). They are the only
#: generic types in edition 2, and `?` is defined in terms of them.
PRELUDE = {
    "Option": (["T"], [("Some", ["T"]), ("None", [])]),
    "Result": (["T", "E"], [("Ok", ["T"]), ("Err", ["E"])]),
}
#: The failure variant of each prelude enum, by position.
FAILURE_VARIANT = {"Option": "None", "Result": "Err"}


def mangle(typ: str) -> str:
    """`Result(i64, str)` -> `Result_i64_str`: a C identifier for one instance."""
    return "".join(character if character.isalnum() else "_" for character in typ).replace("__", "_").strip("_")


#: The attributes of an AST node that hold a *type* rather than a value. A
#: template instantiation rewrites exactly these, and nothing else, so a string
#: literal in a body is never mistaken for a type.
TYPE_ATTRIBUTES = ("typ", "annotation", "instance", "returns")
TYPE_LISTS = ("type_args",)


def substitute_type(typ: str, substitution: dict[str, str]) -> str:
    """`Pair(T)` with `{T: i64}` -> `Pair(i64)`; an unknown name is unchanged."""
    if typ in substitution:
        return substitution[typ]
    if is_function_type(typ):
        # A signature mentions its parameters the same way a struct does:
        # `fn(T) -> T` with `{T: i64}` is `fn(i64) -> i64`.
        params, returns = function_type_parts(typ)
        inner = ", ".join(substitute_type(item, substitution) for item in params)
        return f"fn({inner}) -> {substitute_type(returns, substitution)}"
    split = split_instance(typ)
    if split is None:
        return typ
    name, arguments = split
    return f"{name}({', '.join(substitute_type(item, substitution) for item in arguments)})"


def substitute_types(node, substitution: dict[str, str]) -> None:
    """Rewrite every type written in a (already copied) subtree, in place."""
    if isinstance(node, str) or node is None or isinstance(node, (int, float, bool)):
        return
    if isinstance(node, (list, tuple)):
        for item in node:
            substitute_types(item, substitution)
        return
    fields = getattr(node, "__dict__", None)
    if fields is None:
        return
    for key, value in list(fields.items()):
        if key in TYPE_ATTRIBUTES and isinstance(value, str):
            setattr(node, key, substitute_type(value, substitution))
        elif key in TYPE_LISTS and isinstance(value, list):
            setattr(node, key, [substitute_type(item, substitution) for item in value])
        else:
            substitute_types(value, substitution)


def split_instance(typ: str | None) -> tuple[str, list[str]] | None:
    """`Result(i64, str)` -> ("Result", ["i64", "str"]); None for other types."""
    if not typ or "(" not in typ or not typ.endswith(")"):
        return None
    name, _, rest = typ.partition("(")
    arguments: list[str] = []
    depth = 0
    current = ""
    for character in rest[:-1]:
        if character == "," and depth == 0:
            arguments.append(current.strip())
            current = ""
            continue
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        current += character
    arguments.append(current.strip())
    return name, arguments


def is_array(typ: str | None) -> bool:
    """`[i64]`, `[bool]` and `[str]` are the only array types."""
    return bool(typ) and typ.startswith("[") and typ.endswith("]")


def element_of(typ: str) -> str:
    return typ[1:-1]


def is_value_type(typ: str | None) -> bool:
    """Everything that may be held, passed, returned and copied but not printed."""
    if not typ:
        return False
    return typ in VALUE_TYPES or is_array(typ) or is_function_type(typ)


#: Element types an array may hold. A struct is allowed in edition 2 because it
#: is a value with no owning fields; an enum is not, because its payloads have no
#: ownership story until N11.
SCALAR_ELEMENTS = {"i64", "bool", "str"}


#: Which expressions create a new owned array. Anything else with an array type
#: would be a copy or a borrow, and neither is allowed as an initializer.
def owns_new_array(value: Expr) -> bool:
    if isinstance(value, ArrayLiteral):
        return True
    return isinstance(value, Call) and value.name == "array_zeros"


class Checker:
    def __init__(self, program: Program):
        self.program = program
        #: Name resolution is per module: `(module, name)` -> Function.
        self.by_module: dict[str, dict[str, Function]] = {}
        #: Imports of each module, in source order.
        self.imports: dict[str, list[Import]] = dict(program.imports_by_module)
        if not self.imports and program.imports:
            self.imports = {ENTRY_MODULE: list(program.imports)}
        self.used_modules: set[tuple[str, str]] = set()
        self.scopes: list[dict[str, Symbol]] = []
        self.next_symbol = 0
        self.current_function: Function | None = None
        self.current_module = ENTRY_MODULE
        self.warnings: list[Diagnostic] = []
        self.depth = 0
        #: Nesting depth of `while`/`for` bodies; 0 means break/continue is an error.
        self.loop_depth = 0
        #: The label of every enclosing loop, innermost last; None for a loop
        #: written without one.
        self.loop_labels: list[str | None] = []
        #: How many `defer` blocks enclose the statement being checked. Inside
        #: one, no jump may leave it.
        self.defer_depth = 0
        #: Concrete structs built from a template, keyed by their written type.
        self.struct_instances: dict[str, Struct] = {}
        #: Concrete functions built from a template, keyed by their written
        #: instance type, `identity(i64)`.
        self.fn_instances: dict[str, Function] = {}
        #: Instances currently being built, so a recursive template is caught
        #: instead of exhausting the stack.
        self.instantiating: set[str] = set()

    def check(self) -> Program:
        # Every type an instantiation mentions must exist before an instance of
        # it is built, so the instances are created lazily, on demand.
        self.instances: dict[str, EnumDecl] = {}
        self.struct_instances = {}
        self.fn_instances = {}
        self.instantiating = set()
        # Names first, types second: a struct may hold an enum and an enum may
        # carry a struct, so neither table can be validated before both exist.
        self.collect_structs()
        self.collect_enums()
        self.validate_declaration_types()
        for index, function in enumerate(self.program.functions):
            table = self.by_module.setdefault(function.module, {})
            if function.name in BUILTINS:
                raise make("E0301", f"{function.name!r} is a reserved builtin", function.loc,
                           function.loc.advance(function.name),
                           f"{function.name}() is built in and cannot be redefined")
            if function.name in table:
                raise make("E0302", f"duplicate function {function.name!r}", function.loc,
                           function.loc.advance(function.name))
            function.cname = f"nv_fn_{index}"
            table[function.name] = function
        main = self.by_module.get(ENTRY_MODULE, {}).get("main")
        if main is None:
            raise make("E0303", "program must define fn main()")
        if main.params or main.returns not in ("void", "i64"):
            raise make("E0304", "main must have no parameters and return void or i64", main.loc,
                       main.loc.advance("fn " + main.name))

        # The worklist grows as calls instantiate templates: an instantiated
        # function is appended to `program.functions` and checked in turn, so the
        # code generator only ever sees concrete declarations.
        index = 0
        while index < len(self.program.functions):
            function = self.program.functions[index]
            index += 1
            if function.type_params:
                # A template is a recipe, not a function: its body is checked
                # once per instantiation, when its type parameters have a meaning.
                # Only the written signature is checked here.
                self.check_template_signature(function)
                continue
            self.current_function = function
            self.current_module = function.module
            self.scopes = [{}]
            if is_array(function.returns):
                raise make(
                    "E0330",
                    f"function {function.name!r} cannot return an array",
                    function.loc,
                    function.loc.advance("fn " + function.name),
                    "an array has exactly one owner; write into a mut [T] parameter instead",
                )
            for param in function.params:
                if self.is_template(param.typ):
                    raise make(
                        "E0437",
                        f"{param.typ!r} is a generic declaration, not a type",
                        param.loc, param.loc.advance(param.name),
                        hint=f"write `{param.typ}(i64)` with the type arguments",
                    )
                if is_function_type(param.typ):
                    self.check_function_type(param.typ, param.loc)
                if param.typ not in VALUE_TYPES and not is_array(param.typ) \
                        and not self.is_struct(param.typ) and not self.is_enum(param.typ) \
                        and not is_function_type(param.typ):
                    raise make("E0305", "function parameters cannot have type void", param.loc,
                               param.loc.advance(param.name))
                if param.mutable and not is_array(param.typ):
                    raise make(
                        "E0341",
                        f"'mut' is only allowed on an array parameter, not on {param.typ}",
                        param.loc, param.loc.advance("mut " + param.name),
                        "scalar parameters are already mutable locals inside the function",
                    )
                param.symbol = self.declare(param.name, param.typ, param.loc)
                if is_array(param.typ):
                    self.check_element_type(param.typ, param.loc)
                    # A parameter is a borrow: it owns no storage and its
                    # lifetime is the call, which no Nava program can extend.
                    param.symbol.owned = False
                    param.symbol.mutable = bool(param.mutable)
            self.check_statements(function.body, new_scope=False)
            if function.returns != "void" and not self.always_returns(function.body):
                raise make(
                    "E0306",
                    f"function {function.name!r} must return {function.returns} on every path",
                    function.loc,
                    function.loc.advance("fn " + function.name),
                    "the analysis is conservative: even `while true` does not count as a return",
                )
        self.program.instances = self.instances
        self.program.struct_instances = self.struct_instances
        self.unused_imports()
        self.uninstantiated_templates()
        self.program.warnings = self.warnings
        return self.program

    # --- enums (edition 2) --------------------------------------------------

    def validate_declaration_types(self) -> None:
        """Field and payload types, now that every declared name is known."""
        for struct in self.program.structs:
            if struct.type_params:
                continue
            self.check_field_types(struct)

    def check_field_types(self, struct: Struct) -> None:
        """One struct's fields: a scalar, a struct or an enum, nothing owning."""
        for item in struct.fields:
            if is_function_type(item.typ):
                self.check_function_type(item.typ, item.loc)
            if item.typ in ("str",) or is_array(item.typ):
                raise make(
                    "E0378",
                    f"a field of {struct.name} cannot have type {item.typ} yet",
                    item.loc, item.loc.advance(item.name),
                    "edition 2 struct fields are i64, bool, f64, a struct or an enum; "
                    "owning types such as str and arrays arrive with the memory work (N11)",
                )
            if self.is_template(item.typ):
                raise make(
                    "E0437",
                    f"{item.typ!r} is a generic struct, not a type",
                    item.loc, item.loc.advance(item.name),
                    hint=f"write `{item.typ}(i64)` with the type arguments",
                )
            if item.typ not in VALUE_TYPES and not self.is_struct(item.typ) \
                    and not self.is_enum(item.typ) and not is_function_type(item.typ):
                raise make(
                    "E0203",
                    f"unknown type {item.typ!r}; use i64, bool, f64, or a declared "
                    f"struct or enum",
                    item.loc, item.loc.advance(item.name),
                )

    def is_template(self, typ: str | None) -> bool:
        if not typ:
            return False
        declared = self.structs.get(typ) or self.enums.get(typ)
        return bool(declared is not None and declared.type_params)

    def is_enum(self, typ: str | None) -> bool:
        if typ is None:
            return False
        if typ in self.instances:
            return True
        declared = self.enums.get(typ)
        if declared is not None:
            # A template is a recipe: `Box` alone is not a type, `Box(i64)` is.
            return not declared.type_params
        return split_instance(typ) is not None

    def instantiate(self, typ: str) -> EnumDecl:
        """The declaration of `Result(i64, str)` or `Box(i64)`, once per type."""
        if typ in self.instances:
            return self.instances[typ]
        split = split_instance(typ)
        if split is None:
            return self.enums[typ]
        name, arguments = split
        template = PRELUDE.get(name)
        if template is None:
            # A user enum template: its parameters and variants are declared in
            # the source, so the same substitution applies to both spellings.
            declared = self.enums.get(name)
            if declared is None or not declared.type_params:
                raise make(
                    "E0414",
                    f"{name!r} cannot take type arguments",
                    hint=f"declare it as `enum {name}(T) {{ ... }}` to parameterise it",
                )
            parameters = list(declared.type_params)
            variants = [(variant.name, list(variant.types)) for variant in declared.variants]
            for argument in arguments:
                self.require_known_type(argument, f"in {typ}")
            if len(arguments) != len(parameters):
                raise make(
                    "E0412",
                    f"{name} takes {len(parameters)} type argument"
                    f"{'s' if len(parameters) != 1 else ''}, got {len(arguments)}",
                )
            substitution = dict(zip(parameters, arguments))
            declared_variants = []
            for tag, (variant_name, types) in enumerate(variants):
                resolved = [substitute_type(item, substitution) for item in types]
                choice = Variant(variant_name, resolved, None, tag)
                declared_variants.append(choice)
                self.check_variant_types(name, choice)
            decl = EnumDecl(name, declared_variants, None)
            decl.cname = "nv_enum_" + mangle(typ)
            self.instances[typ] = decl
            return decl
        parameters, variants = template
        if len(arguments) != len(parameters):
            raise make(
                "E0412",
                f"{name} takes {len(parameters)} type argument"
                f"{'s' if len(parameters) != 1 else ''}, got {len(arguments)}",
            )
        for argument in arguments:
            if not (is_value_type(argument) or self.is_struct(argument)
                    or self.is_enum(argument)):
                raise make("E0203", f"unknown type {argument!r} in {typ}")
            if is_function_type(argument):
                self.check_function_type(argument, None)
        substitution = dict(zip(parameters, arguments))
        declared_variants = []
        for tag, (variant_name, types) in enumerate(variants):
            resolved = [substitution.get(item, item) for item in types]
            declared_variants.append(Variant(variant_name, resolved, None, tag))
        decl = EnumDecl(name, declared_variants, None)
        decl.cname = "nv_enum_" + mangle(typ)
        self.instances[typ] = decl
        return decl

    def enum_decl_of(self, typ: str) -> EnumDecl:
        return self.instantiate(typ) if split_instance(typ) else self.enums[typ]

    def collect_enums(self) -> None:
        """Build the enum table and reject the declarations that cannot work.

        A variant carries a fixed list of positional fields, each of a scalar,
        struct or enum type. `str` and arrays again wait for N11: a variant that
        owns storage needs the memory model that structs also wait for.
        """
        self.enums: dict[str, EnumDecl] = {}
        for decl in self.program.enums:
            if decl.name in self.structs:
                raise make(
                    "E0390",
                    f"{decl.name!r} is already a struct; struct and enum names "
                    f"share one namespace",
                    decl.loc, decl.loc.advance("enum " + decl.name),
                    hint="rename one of them",
                )
            if decl.name in self.enums or decl.name in self.builtin_names:
                raise make(
                    "E0390",
                    f"duplicate enum {decl.name!r}",
                    decl.loc, decl.loc.advance("enum " + decl.name),
                    hint="struct, enum and builtin type names share one namespace",
                )
            if not decl.variants:
                raise make(
                    "E0396",
                    f"enum {decl.name} declares no variants",
                    decl.loc, decl.loc.advance("enum " + decl.name),
                    hint="an enum with no values has no value to construct or match",
                )
            decl.cname = f"nv_enum_{decl.name}"
            self.enums[decl.name] = decl
        for decl in self.program.enums:
            seen: set[str] = set()
            for tag, variant in enumerate(decl.variants):
                variant.tag = tag
                if variant.name in seen:
                    raise make(
                        "E0391",
                        f"enum {decl.name} declares the variant {variant.name!r} twice",
                        variant.loc, variant.loc.advance(variant.name),
                        hint="variant names are unique inside their enum",
                    )
                seen.add(variant.name)
                if not decl.type_params:
                    self.check_variant_types(decl.name, variant)

    def check_variant_types(self, enum_name: str, variant: Variant) -> None:
        """One variant's payload: a scalar, a struct or an enum, nothing owning.

        An instantiated variant has no location of its own — the type it came
        from was written at the instantiation — so the diagnostic carries none.
        """
        span = () if variant.loc is None else (variant.loc, variant.loc.advance(variant.name))
        for item in variant.types:
            if item in ("str",) or is_array(item):
                raise make(
                    "E0399",
                    f"a variant field of {enum_name}::{variant.name} cannot be {item} yet",
                    *span,
                    hint="edition 2 variant fields are i64, bool, f64, a struct or an enum; "
                         "owning types arrive with the memory work (N11)",
                )
            if self.is_template(item):
                raise make(
                    "E0437",
                    f"{item!r} is a generic declaration, not a type",
                    *span,
                    hint=f"write `{item}(i64)` with the type arguments",
                )
            if is_function_type(item):
                self.check_function_type(item, variant.loc)
                continue
            if item not in VALUE_TYPES and not self.is_struct(item) \
                    and not self.is_enum(item):
                raise make(
                    "E0203",
                    f"unknown type {item!r} in variant {enum_name}::{variant.name}",
                    *span,
                )

    def enum_of(self, typ: str | None) -> EnumDecl | None:
        if not typ:
            return None
        if typ in self.enums:
            return self.enums[typ]
        if split_instance(typ) is not None:
            return self.instantiate(typ)
        return None

    def variant_value(self, expr: VariantValue) -> str:
        """`Shape::Circle(3)`: a declared variant, with exactly its fields."""
        if expr.instance is not None:
            decl = self.instantiate(expr.instance)
            typ = expr.instance
        else:
            decl = self.enums[expr.enum]  # the parser only builds this for a declared enum
            typ = expr.enum
        expr.declared = decl
        choice = next((item for item in decl.variants if item.name == expr.variant), None)
        if choice is None:
            known = ", ".join(item.name for item in decl.variants)
            raise make(
                "E0393",
                f"enum {decl.name} has no variant {expr.variant!r}",
                expr.variant_loc or expr.loc,
                hint=f"variants are {known}",
            )
        expr.choice = choice
        if len(expr.args) != len(choice.types):
            raise make(
                "E0394",
                f"{decl.name}::{choice.name} takes {len(choice.types)} "
                f"field{'s' if len(choice.types) != 1 else ''}, got {len(expr.args)}",
                expr.loc, expr.loc.advance(expr.enum),
                hint="variant fields are positional",
            )
        for argument, expected in zip(expr.args, choice.types):
            actual = self.expression(argument)
            if actual != expected:
                raise make(
                    "E0311",
                    f"field of {decl.name}::{choice.name} is {expected}, got {actual}",
                    argument.loc,
                    hint="there are no implicit conversions between i64, bool, f64 and str",
                )
        return typ

    def match_statement(self, statement: Match) -> None:
        """One arm per variant, or a wildcard; bindings are copies of the fields."""
        subject = self.expression(statement.value)
        decl = self.enum_of(subject)
        if decl is None:
            raise make(
                "E0395",
                f"match expects an enum value, got {subject}",
                statement.value.loc,
                hint="an enum type is declared with `enum Name { Variant(type, ...) }`",
            )
        covered: dict[str, Arm] = {}
        wildcard = False
        for arm in statement.arms:
            if wildcard:
                raise make(
                    "E0398",
                    "this arm can never be reached: an earlier `_` arm matches every value",
                    arm.loc, hint="move the wildcard arm last, or delete this arm",
                )
            if arm.variant is None:
                wildcard = True
                self.check_statements(arm.body, new_scope=True)
                continue
            if arm.instance is not None and arm.instance != subject:
                raise make(
                    "E0395",
                    f"this arm matches {arm.instance}, but the value is {subject}",
                    arm.loc, arm.loc.advance(arm.instance.split("(", 1)[0]),
                    hint="instantiate the pattern with the same type arguments "
                         "as the value",
                )
            choice = next((item for item in decl.variants if item.name == arm.variant), None)
            if choice is None:
                known = ", ".join(item.name for item in decl.variants)
                raise make(
                    "E0393",
                    f"enum {decl.name} has no variant {arm.variant!r}",
                    arm.loc, arm.loc.advance(decl.name + "::" + arm.variant),
                    hint=f"variants are {known}",
                )
            if choice.name in covered:
                raise make(
                    "E0397",
                    f"variant {decl.name}::{choice.name} already has an arm",
                    arm.loc, arm.loc.advance(decl.name + "::" + choice.name),
                )
            if len(arm.bindings) != len(choice.types):
                raise make(
                    "E0394",
                    f"{decl.name}::{choice.name} carries {len(choice.types)} "
                    f"field{'s' if len(choice.types) != 1 else ''}, "
                    f"the pattern binds {len(arm.bindings)}",
                    arm.loc, arm.loc.advance(decl.name + "::" + choice.name),
                    hint="bind every field, positionally",
                )
            arm.choice = choice
            covered[choice.name] = arm
            self.scopes.append({})
            try:
                arm.symbols = [self.declare(name, typ, arm.loc)
                               for name, typ in zip(arm.bindings, choice.types)]
                self.check_statements(arm.body, new_scope=False)
            finally:
                self.scopes.pop()
        missing = [item.name for item in decl.variants if item.name not in covered]
        if missing and not wildcard:
            raise make(
                "E0396",
                f"match on {subject} does not cover "
                + ", ".join(f"{subject}::{name}" for name in missing),
                statement.loc, statement.loc.advance("match"),
                hint="cover every variant, or add `_ => { ... }` as the last arm",
            )

    # --- structs (edition 2) ------------------------------------------------

    def is_struct(self, typ: str | None) -> bool:
        return self.struct_of(typ) is not None

    def struct_of(self, typ: str | None) -> Struct | None:
        """The declared struct behind a type, instantiating a template if needed.

        Unknown names and type-parameter names return None: this answers "is this
        a struct", not "is this legal here", so the caller keeps its own message.
        """
        if not typ:
            return None
        declared = self.structs.get(typ)
        if declared is not None:
            return None if declared.type_params else declared
        split = split_instance(typ)
        if split is None:
            return None
        name, arguments = split
        template = self.structs.get(name)
        if template is None or not template.type_params:
            return None
        if len(arguments) != len(template.type_params):
            return None
        try:
            return self.instantiate_struct(typ)
        except NavaError:
            # A probe, not a validation: `struct_of` answers "is this a struct".
            # A bad argument type is reported where the type is written.
            return None

    def collect_structs(self) -> None:
        """Build the struct table and reject the declarations that cannot work.

        A struct is a value with named fields. Edition 2 restricts a field to a
        scalar (`i64`, `bool`, `f64`) or another struct: `str` and arrays carry
        ownership, which is N11's work, and accepting them here would mean
        copying a pointer and calling it a copy.
        """
        self.structs: dict[str, Struct] = {}
        for struct in self.program.structs:
            if struct.name in self.structs or struct.name in self.builtin_names:
                raise make(
                    "E0380",
                    f"duplicate struct {struct.name!r}",
                    struct.loc, struct.loc.advance("struct " + struct.name),
                    hint="struct names are shared by every module of the program",
                )
            struct.cname = f"nv_struct_{struct.name}"
            self.structs[struct.name] = struct
        for struct in self.program.structs:
            seen: set[str] = set()
            for item in struct.fields:
                if item.name in seen:
                    raise make(
                        "E0375",
                        f"struct {struct.name} declares the field {item.name!r} twice",
                        item.loc, item.loc.advance(item.name),
                        hint="each field name appears exactly once",
                    )
                seen.add(item.name)
        for struct in self.program.structs:
            self.check_struct_cycle(struct)

    def require_known_type(self, typ: str, where: str) -> None:
        """Refuse a written type that names nothing: `Box(Nope)` names nothing."""
        if typ in VALUE_TYPES or self.is_struct(typ) or self.is_enum(typ):
            return
        split = split_instance(typ)
        if split is not None:
            # A missing template is reported by the instantiation that asked.
            return
        raise make("E0203", f"unknown type {typ!r} {where}")

    def instantiate_struct(self, typ: str) -> Struct:
        """The declaration of `Pair(i64)`, created once per type.

        A struct is stored inline and copied by value, so an instance is an
        ordinary struct whose fields have concrete types. Nothing downstream —
        the checker's later passes or the code generator — needs to know that it
        came from a template.
        """
        if typ in self.struct_instances:
            return self.struct_instances[typ]
        split = split_instance(typ)
        name, arguments = split
        template = self.structs.get(name)
        if template is None or not template.type_params:
            raise make("E0414", f"{name!r} cannot take type arguments",
                       hint=f"declare it as `struct {name}(T) {{ ... }}` to parameterise it")
        parameters = list(template.type_params)
        if len(arguments) != len(parameters):
            raise make(
                "E0412",
                f"{name} takes {len(parameters)} type argument"
                f"{'s' if len(parameters) != 1 else ''}, got {len(arguments)}",
            )
        for argument in arguments:
            self.require_known_type(argument, f"in {typ}")
        if typ in self.instantiating:
            raise make(
                "E0377",
                f"struct {typ} would contain itself: instantiating it needs itself",
                template.loc, template.loc.advance("struct " + template.name),
                hint="a struct is stored inline; edition 2 has no indirection to break the cycle",
            )
        substitution = dict(zip(parameters, arguments))
        fields = [Field(item.name, substitute_type(item.typ, substitution), item.loc)
                  for item in template.fields]
        decl = Struct(typ, fields, template.loc)
        decl.cname = "nv_struct_" + mangle(typ)
        self.struct_instances[typ] = decl
        self.instantiating.add(typ)
        try:
            self.check_field_types(decl)
        finally:
            self.instantiating.discard(typ)
        return decl

    def check_struct_cycle(self, struct: Struct) -> None:
        """A struct may not contain itself, directly or through another struct."""
        def walk(current: Struct, path: list[str]) -> None:
            for item in current.fields:
                if item.typ == struct.name:
                    chain = " -> ".join(path + [struct.name])
                    raise make(
                        "E0377",
                        f"struct {struct.name} contains itself: {chain}",
                        item.loc, item.loc.advance(item.name),
                        "a struct is stored inline, so a recursive type has no finite size; "
                        "edition 2 has no indirection to put it behind",
                    )
                nested = self.structs.get(item.typ)
                if nested is not None and nested.name not in path:
                    walk(nested, path + [nested.name])
        walk(struct, [struct.name])

    @property
    def builtin_names(self) -> set[str]:
        return set(BUILTINS)

    def struct_literal(self, expr: StructLiteral) -> str:
        """`Point(x: 1, y: 2)` or `Pair(i64)(first: 1, second: 2)`: every field once."""
        if expr.instance is not None:
            if expr.name not in self.structs:
                raise make("E0373", f"unknown struct {expr.name!r}", expr.loc,
                           expr.loc.advance(expr.name))
            struct = self.instantiate_struct(expr.instance)
        else:
            struct = self.struct_of(expr.name)
        if struct is None and expr.instance is None and self.is_template(expr.name):
            raise make(
                "E0437",
                f"{expr.name!r} is a generic struct: a construction names its type",
                expr.loc, expr.loc.advance(expr.name),
                hint=f"write {expr.name}(i64)(field: value, ...)",
            )
        if struct is None:
            if expr.name in BUILTINS or any(
                    function.name == expr.name for function in self.program.functions):
                raise make(
                    "E0381",
                    f"{expr.name!r} is a function, not a struct: it takes positional arguments",
                    expr.loc, expr.loc.advance(expr.name),
                    hint=f"write {expr.name}(value, ...) without field names",
                )
            raise make(
                "E0373",
                f"unknown struct {expr.name!r}",
                expr.loc, expr.loc.advance(expr.name),
                hint="declare it with `struct Name { field: type }` in edition 2, "
                     "or call a function with positional arguments",
            )
        expr.struct = struct
        declared = {item.name: item for item in struct.fields}
        seen: dict[str, int] = {}
        for name, value in expr.fields:
            if name not in declared:
                raise make(
                    "E0374",
                    f"struct {struct.name} has no field {name!r}",
                    value.loc,
                    hint="fields are " + ", ".join(declared) if declared else "the struct has no fields",
                )
            if name in seen:
                raise make(
                    "E0375",
                    f"field {name!r} is given twice when constructing {struct.name}",
                    value.loc,
                )
            seen[name] = 1
            actual = self.expression(value)
            if actual != declared[name].typ:
                raise make(
                    "E0311",
                    f"field {name!r} of {struct.name} is {declared[name].typ}, got {actual}",
                    value.loc,
                    hint="there are no implicit conversions between i64, bool, f64 and str",
                )
        missing = [item.name for item in struct.fields if item.name not in seen]
        if missing:
            listy = ", ".join(missing)
            raise make(
                "E0376",
                f"missing field{'s' if len(missing) > 1 else ''} {listy} "
                f"when constructing {struct.name}",
                expr.loc, expr.loc.advance(expr.name),
                hint="every field is named at the construction site",
            )
        return struct.name

    def member(self, expr: Member) -> str:
        base = self.expression(expr.base)
        if not self.is_struct(base):
            raise make(
                "E0379",
                f"only a struct has fields, but this value is {base}",
                expr.loc, expr.loc.advance("."),
                hint="a field access needs a value of a declared struct type",
            )
        struct = self.struct_of(base)
        for item in struct.fields:
            if item.name == expr.field:
                expr.owner = item
                return item.typ
        raise make(
            "E0374",
            f"struct {struct.name} has no field {expr.field!r}",
            expr.field_loc or expr.loc,
            hint="fields are " + ", ".join(item.name for item in struct.fields)
            if struct.fields else "the struct has no fields",
        )

    def root_variable(self, expr: Expr) -> Variable | None:
        while isinstance(expr, Member):
            expr = expr.base
        return expr if isinstance(expr, Variable) else None

    def member_assign(self, statement: MemberAssign) -> None:
        target_type = self.member(statement.target)
        root = self.root_variable(statement.target)
        if root is not None and root.symbol is not None and root.symbol.readonly:
            raise make(
                "E0351",
                f"the loop variable {root.name!r} cannot be assigned",
                statement.loc, statement.loc.advance(root.name),
                "the loop rebinds it; use a separate `let` for a mutated copy",
            )
        value_type = self.expression(statement.value)
        if value_type != target_type:
            raise make(
                "E0312",
                f"cannot assign {value_type} to field {statement.target.field!r} "
                f"of type {target_type}",
                statement.value.loc,
            )

    def check_element_type(self, typ: str, loc) -> None:
        """Only a scalar or a struct may be an array element (E0424)."""
        if not is_array(typ):
            return
        element = element_of(typ)
        if element in SCALAR_ELEMENTS or self.is_struct(element):
            return
        if not self.is_enum(element) and element not in VALUE_TYPES:
            raise make(
                "E0203",
                f"unknown type {element!r}; use i64, bool, str, or a declared struct",
                loc,
            )
        raise make(
            "E0424",
            f"an array element type must be i64, bool, str or a struct, got {element}",
            loc,
            hint="an enum has no storage of its own until the memory work (N11)",
        )

    def slice(self, expr: Slice) -> str:
        """`a[i..j]`: a read-only view of `a`, bounds checked at run time.

        The bounds are not required to be constants, so the checker proves only
        that they are `i64`; the range itself is checked when the slice is
        created (`0 <= i <= j <= len`), which is why a slice cannot be used
        without running the program.
        """
        base = self.expression(expr.array)
        if not is_array(base):
            raise make(
                "E0421",
                f"only an array can be sliced, but {expr.array.name!r} is {base}",
                expr.loc, expr.loc.advance(expr.array.name),
            )
        self.check_element_type(base, expr.loc)
        for bound, label in ((expr.start, "start"), (expr.end, "end")):
            actual = self.expression(bound)
            if actual != "i64":
                raise make(
                    "E0420",
                    f"the {label} of a slice must be i64, got {actual}",
                    bound.loc,
                    hint="integers are not converted; use i64 arithmetic",
                )
        return base

    def uninstantiated_templates(self) -> None:
        """W0003: a template nobody uses has never been type-checked.

        Its body mentions type parameters, so it cannot be checked before a call
        fixes them. Warning about it is the honest alternative to pretending that
        dead generic code was verified.
        """
        templates = {id(function) for function in self.program.functions
                     if function.type_params}
        for function in self.program.functions:
            if id(function) not in templates:
                continue
            used = any(clone.name == function.name and clone.module == function.module
                       for clone in self.fn_instances.values())
            if used:
                continue
            self.warn(function.loc, "W0003",
                      f"generic function {function.name!r} is never instantiated, "
                      f"so its body is not type-checked")

    def unused_imports(self) -> None:
        """W0002: an import that no qualified call in its module uses."""
        for module, imports in self.imports.items():
            for item in imports:
                if (module, item.name) in self.used_modules:
                    continue
                self.warn(item.name_loc, "W0002",
                          f"unused import {item.name!r}: no function of this module is called")

    # --- generics (edition 2) ----------------------------------------------

    def check_template_signature(self, function: Function) -> None:
        """The written signature of a template, before any instantiation.

        The body is not checked here: it mentions type parameters, which have no
        meaning until a call fixes them. Every instantiation is checked in full,
        so an error inside a body is reported at each use, not once.
        """
        allowed = set(function.type_params)
        for param in function.params:
            if not self.type_is_known(param.typ, allowed):
                raise make(
                    "E0203",
                    f"unknown type {param.typ!r} in the parameters of {function.name}",
                    param.loc, param.loc.advance(param.name),
                    hint="a type parameter must be listed in the declaration, as in "
                         f"`fn {function.name}(T)`",
                )
        if not self.type_is_known(function.returns, allowed):
            raise make(
                "E0203",
                f"unknown type {function.returns!r} in the result of {function.name}",
                function.loc, function.loc.advance("fn " + function.name),
            )

    def type_is_known(self, typ: str, allowed: set[str]) -> bool:
        """Is `typ` a type once `allowed` (the declaration's parameters) is fixed?

        It answers for the *written* signature, where `T` has no value yet, so an
        instantiation is resolved by looking at its template rather than by
        building the instance: `Pair(T)` is known exactly when `Pair` is a
        template and `T` is a parameter.
        """
        if typ in allowed or typ in VALUE_TYPES or typ == "void":
            return True
        if is_function_type(typ):
            parts = function_type_parts(typ)
            if parts is None:
                return False
            params, returns = parts
            return all(self.type_is_known(item, allowed) for item in params + [returns])
        split = split_instance(typ)
        if split is not None:
            name, arguments = split
            if name in allowed:
                # `T(i64)` is not a type: a type parameter takes no arguments.
                return False
            template = self.structs.get(name) or self.enums.get(name)
            if name in PRELUDE:
                expected = PRELUDE[name][0]
                return len(arguments) == len(expected) and all(
                    self.type_is_known(item, allowed) for item in arguments)
            if template is None or not template.type_params:
                return False
            if len(arguments) != len(template.type_params):
                return False
            return all(self.type_is_known(item, allowed) for item in arguments)
        if self.is_template(typ):
            return False
        return self.is_struct(typ) or self.is_enum(typ)

    def function_instance_name(self, template: Function, substitution: dict[str, str]) -> str:
        arguments = ", ".join(substitution[name] for name in template.type_params)
        return f"{template.name}({arguments})"

    def instantiate_function(self, template: Function,
                             substitution: dict[str, str]) -> Function:
        """A concrete copy of a template, appended to the program's functions.

        Monomorphisation is an AST copy with the type parameters replaced: the
        copy is an ordinary function, so the rest of the checker and the whole
        code generator need no knowledge of generics.
        """
        typ = self.function_instance_name(template, substitution)
        if typ in self.fn_instances:
            return self.fn_instances[typ]
        clone = Function(
            template.name,
            [Param(param.name, substitute_type(param.typ, substitution), param.loc,
                   param.mutable) for param in template.params],
            substitute_type(template.returns, substitution),
            copy.deepcopy(template.body),
            template.loc,
            module=template.module,
            public=template.public,
        )
        substitute_types(clone.body, substitution)
        clone.cname = f"nv_fn_{len(self.program.functions)}"
        self.fn_instances[typ] = clone
        # The worklist in `check()` will type-check this copy, and the code
        # generator emits it because it is now one of the program's functions.
        self.program.functions.append(clone)
        return clone

    def type_argument_binding(self, expr: Call, template: Function) -> dict[str, str]:
        """`identity(i64)(7)`: the written type arguments of a call."""
        written = list(expr.type_args or [])
        parameters = list(template.type_params)
        if len(written) != len(parameters):
            raise make(
                "E0412",
                f"{expr.name} takes {len(parameters)} type argument"
                f"{'s' if len(parameters) != 1 else ''}, got {len(written)}",
                expr.loc, expr.loc.advance(expr.name),
            )
        for typ in written:
            self.require_known_type(typ, f"in the type arguments of {expr.name}")
        return dict(zip(parameters, written))

    def infer_binding(self, expr: Call, template: Function,
                      actual: list[str]) -> dict[str, str]:
        """Bind the type parameters from the argument types, or refuse (E0436)."""
        substitution: dict[str, str] = {}

        def unify(written: str, real: str) -> None:
            if written in template.type_params:
                # The first binding wins; a second, different one is a genuine
                # argument mismatch, reported below as E0327.
                substitution.setdefault(written, real)
                return
            split = split_instance(written)
            if split is None:
                return
            name, arguments = split
            real_split = split_instance(real)
            if real_split is None or real_split[0] != name or len(real_split[1]) != len(arguments):
                return
            for inner, other in zip(arguments, real_split[1]):
                unify(inner, other)

        for param, real in zip(template.params, actual):
            unify(param.typ, real)
        missing = [name for name in template.type_params if name not in substitution]
        if missing:
            listed = ", ".join(missing)
            raise make(
                "E0436",
                f"cannot infer the type argument{'s' if len(missing) != 1 else ''} "
                f"{listed} of {expr.name}",
                expr.loc, expr.loc.advance(expr.name),
                hint=f"write the call as {expr.name}("
                     + ", ".join(substitution.get(name, "i64") for name in template.type_params)
                     + ")(...)",
            )
        return substitution

    def resolve(self, expr: Call) -> Function:
        """Resolve a call to a function of the current or an imported module."""
        if expr.module is None:
            local = self.by_module.get(self.current_module, {})
            if expr.name in local:
                return local[expr.name]
            # A function that exists in another module is a qualification
            # error, not an unknown name: say which module to name.
            elsewhere = sorted(module for module, table in self.by_module.items()
                               if expr.name in table)
            if elsewhere:
                where = elsewhere[0]
                hint = f"call it as {where}::{expr.name}"
                if not any(item.name == where for item in self.imports.get(self.current_module, [])):
                    hint += f" after adding `import {where}`"
                raise make("E0358", f"function {expr.name!r} is defined in module {where!r}",
                           expr.loc, expr.loc.advance(expr.name), hint)
            raise make("E0325", f"unknown function {expr.name!r}", expr.loc,
                       expr.loc.advance(expr.name),
                       "define it with `fn`; functions may be called before their definition")
        table = self.by_module.get(expr.module)
        imported = {item.name: item for item in self.imports.get(self.current_module, [])}
        if expr.module not in imported or table is None:
            raise make(
                "E0359",
                f"module {expr.module!r} is not imported",
                expr.loc, expr.loc.advance(f"{expr.module}::{expr.name}"),
                f"add `import {expr.module}` at the top of the file",
            )
        if expr.name not in table:
            raise make("E0325", f"module {expr.module!r} has no function {expr.name!r}",
                       expr.loc, expr.loc.advance(f"{expr.module}::{expr.name}"),
                       "modules expose functions only; check the name in LANGUAGE.md")
        target = table[expr.name]
        # An edition-2 module decides what it exports; an edition-1 module keeps
        # the behaviour it was released with, where every function is visible.
        if self.program.module_editions.get(expr.module, 1) >= 2 and not target.public \
                and expr.module != self.current_module:
            raise make(
                "E0433",
                f"{expr.module}::{expr.name} is not public",
                expr.loc, expr.loc.advance(f"{expr.module}::{expr.name}"),
                hint=f"declare it as `pub fn {expr.name}` in {expr.module}.nv",
            )
        self.used_modules.add((self.current_module, expr.module))
        return target

    def declare(self, name, typ, loc) -> Symbol:
        if name in BUILTINS:
            raise make("E0301", f"{name!r} is a reserved builtin", loc, loc.advance(name))
        if name in self.scopes[-1]:
            raise make("E0307", f"duplicate variable {name!r} in the same scope", loc,
                       loc.advance(name),
                       "shadowing is allowed only in a nested block, not in the same block")
        symbol = Symbol(name, typ, f"nv_v{self.next_symbol}")
        self.next_symbol += 1
        self.scopes[-1][name] = symbol
        return symbol

    def check_function_type(self, typ: str, loc) -> None:
        """Every part of a `fn(...) -> T` must itself be a value type."""
        span = () if loc is None else (loc, loc)
        parts = function_type_parts(typ)
        if parts is None:
            raise make("E0203", f"unknown type {typ!r}", *span)
        params, returns = parts
        if returns != "void" and not is_value_type(returns) \
                and not self.is_struct(returns) and not self.is_enum(returns):
            raise make("E0203", f"unknown return type {returns!r} in {typ}", *span)
        for item in params:
            if not is_value_type(item) and not self.is_struct(item) \
                    and not self.is_enum(item):
                raise make("E0203", f"unknown parameter type {item!r} in {typ}", *span)

    def find_symbol(self, name) -> Symbol | None:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return None

    def function_named(self, name) -> Function | None:
        """A function of the current module by name, or None."""
        return self.by_module.get(self.current_module, {}).get(name)

    def lookup(self, name, loc) -> Symbol:
        symbol = self.find_symbol(name)
        if symbol is not None:
            return symbol
        raise make("E0308", f"unknown variable {name!r}", loc, loc.advance(name),
                   "declare it with `let` in this or an enclosing block before use")

    @staticmethod
    def always_returns(statements) -> bool:
        for statement in statements:
            if Checker.guarantees_return(statement):
                return True
        return False

    def enter(self, loc) -> None:
        """Bound the recursion the checker performs over one expression tree."""
        self.depth += 1
        if self.depth > MAX_NESTING:
            raise make(
                "E0401",
                f"expression nesting is deeper than the supported limit of {MAX_NESTING}",
                loc,
                hint="name intermediate results with `let` or split the work into functions",
            )

    def leave(self) -> None:
        self.depth -= 1

    def warn(self, loc, code: str, message: str, path=None) -> None:
        self.warnings.append(Diagnostic(code=code, severity=severity_of(code),
                                        message=message, loc=loc))

    def check_statements(self, statements, *, new_scope: bool) -> None:
        if new_scope:
            self.scopes.append({})
        terminated = False
        for statement in statements:
            if terminated:
                self.warn(statement.loc, "W0001",
                          "unreachable statement: the preceding statement never completes normally")
            self.check_statement(statement)
            if not terminated and (self.guarantees_return(statement)
                                   or isinstance(statement, (Break, Continue))):
                terminated = True
        if new_scope:
            self.scopes.pop()

    @staticmethod
    @staticmethod
    def guarantees_return(statement) -> bool:
        """True when this single statement always returns.

        A `match` counts when the checker has already proved it exhaustive —
        which happens before this analysis runs, because `check_statement`
        records every arm's variant on the arm — and every arm returns.
        """
        if isinstance(statement, Return):
            return True
        if isinstance(statement, If):
            return (statement.else_body is not None
                    and Checker.always_returns(statement.then_body)
                    and Checker.always_returns(statement.else_body))
        if isinstance(statement, Match):
            # Exhaustiveness is already proved: an incomplete match is `E0396`
            # and never reaches this analysis.
            return bool(statement.arms) and all(
                Checker.always_returns(arm.body) for arm in statement.arms)
        return False

    def propagate(self, statement) -> str:
        """`let value = call()?`: check the operator and return the bound type.

        The rule, in full, because it is the whole semantics of `?`:

        * the call must produce an `Option(T)` or a `Result(T, E)`;
        * the enclosing function must return the same enum, instantiated so that
          its *failure* type is identical — a failure is forwarded, never
          converted;
        * the success type may differ (`Result(bool, str)` inside a function
          returning `Result(i64, str)` is fine), and it is what the new local is
          bound to;
        * anything else is an error, because a silent alternative would be a
          silent failure.
        """
        inner_type = self.expression(statement.value.inner)
        decl = self.enum_of(inner_type)
        if decl is None or split_instance(inner_type) is None:
            raise make(
                "E0410",
                f"`?` expects an Option or a Result, got {inner_type}",
                statement.value.loc, statement.value.loc.advance("?"),
                hint="`?` forwards the failure variant of Option(T) or Result(T, E)",
            )
        success, failure = self.instance_parts(inner_type)
        function = self.current_function
        returns = function.returns if function is not None else "void"
        outer = self.enum_of(returns)
        outer_split = split_instance(returns)
        inner_split = split_instance(inner_type)
        assert inner_split is not None
        if outer is None or outer_split is None or outer_split[0] != inner_split[0]:
            raise make(
                "E0411",
                f"`?` cannot propagate into this function, which returns {returns}",
                statement.value.loc, statement.value.loc.advance("?"),
                hint="the enclosing function must return "
                     f"{inner_type.split('(', 1)[0]} with the same failure type",
            )
        outer_success, outer_failure = self.instance_parts(returns)
        if outer_failure != failure:
            raise make(
                "E0411",
                f"`?` forwards {failure}, but this function fails with {outer_failure}",
                statement.value.loc, statement.value.loc.advance("?"),
                hint="a failure is forwarded unchanged; convert it before forwarding",
            )
        return success

    @staticmethod
    def instance_parts(typ: str) -> tuple[str, str]:
        """The success and failure types of `Option(T)` or `Result(T, E)`."""
        split = split_instance(typ)
        assert split is not None
        name, arguments = split
        if name == "Option":
            return arguments[0], "none"
        return arguments[0], arguments[1]

    def check_statement(self, statement) -> None:
        if isinstance(statement, Let):
            name_loc = statement.name_loc or statement.loc
            if isinstance(statement.value, Try):
                if self.defer_depth:
                    raise make(
                        "E0430",
                        "a deferred block cannot propagate a failure out of itself",
                        statement.value.loc, statement.value.loc.advance("?"),
                        hint="the block runs while its scope is being left; handle the "
                             "failure inside it with match",
                    )
                # `?` decides the type of the binding from the callee, so the
                # annotation is checked against that type and never drives it.
                typ = self.propagate(statement)
            else:
                expected = statement.annotation if is_array(statement.annotation) else None
                typ = self.expression(statement.value, expected=expected)
            if typ == "void":
                raise make("E0309", "a variable cannot contain a void value", name_loc,
                           name_loc.advance(statement.name))
            if statement.annotation is not None:
                if self.is_template(statement.annotation):
                    raise make(
                        "E0437",
                        f"{statement.annotation!r} is a generic declaration, not a type",
                        name_loc, name_loc.advance(statement.name),
                        hint=f"write `{statement.annotation}(i64)` with the type arguments",
                    )
                if statement.annotation == "void":
                    raise make("E0310", "a variable cannot have type void", name_loc,
                               name_loc.advance(statement.name))
                if statement.annotation != typ:
                    raise make("E0311", f"expected {statement.annotation}, got {typ}",
                               name_loc, name_loc.advance(statement.name),
                               "there are no implicit conversions between i64, bool, f64 and str")
            self.check_element_type(typ, name_loc)
            borrowed = isinstance(statement.value, Slice)
            if is_array(typ) and not owns_new_array(statement.value) and not borrowed:
                raise make(
                    "E0331",
                    f"arrays cannot be copied; {statement.name!r} would share storage with another array",
                    name_loc, name_loc.advance(statement.name),
                    "create a new array with a literal or array_zeros(n), or pass the existing "
                    "one to a parameter",
                )
            statement.symbol = self.declare(statement.name, typ, name_loc)
            if borrowed:
                # A slice owns nothing: the array it views stays the owner, and
                # the block that declared that array releases the storage.
                statement.symbol.borrowed = True
        elif isinstance(statement, Defer):
            self.check_defer(statement)
        elif isinstance(statement, Match):
            self.match_statement(statement)
        elif isinstance(statement, MemberAssign):
            self.member_assign(statement)
        elif isinstance(statement, IndexAssign):
            statement.symbol = self.lookup(statement.name, statement.loc)
            if not is_array(statement.symbol.typ):
                raise make(
                    "E0329",
                    f"only an array can be indexed, but {statement.name!r} is {statement.symbol.typ}",
                    statement.loc, statement.loc.advance(statement.name),
                )
            if statement.symbol.borrowed:
                raise make(
                    "E0422",
                    f"{statement.name!r} is a slice: a view of an array is read-only",
                    statement.loc, statement.loc.advance(statement.name),
                    "write through the array that owns the storage, or copy the "
                    "elements into a new array",
                )
            if not statement.symbol.mutable:
                raise make(
                    "E0336",
                    f"cannot assign to an element of the read-only parameter {statement.name!r}",
                    statement.loc, statement.loc.advance(statement.name),
                    "declare the parameter as `mut [T]` to allow writes",
                )
            index_type = self.expression(statement.index)
            if index_type != "i64":
                raise make("E0328", f"an array index must be i64, got {index_type}",
                           statement.index.loc,
                           hint="integers are not converted; use i64 arithmetic")
            element = element_of(statement.symbol.typ)
            value_type = self.expression(statement.value)
            if value_type != element:
                raise make("E0312",
                           f"cannot assign {value_type} to an element of "
                           f"{statement.symbol.typ} (element type {element})",
                           statement.value.loc)
        elif isinstance(statement, Assign):
            statement.symbol = self.lookup(statement.name, statement.loc)
            if statement.symbol.readonly:
                raise make(
                    "E0351",
                    f"the loop variable {statement.name!r} cannot be assigned",
                    statement.loc, statement.loc.advance(statement.name),
                    "the loop rebinds it; use a separate `let` for a mutated copy",
                )
            if is_array(statement.symbol.typ):
                raise make(
                    "E0332",
                    f"an array variable cannot be reassigned ({statement.name!r} is "
                    f"{statement.symbol.typ})",
                    statement.loc, statement.loc.advance(statement.name),
                    "an array owns its storage for the life of its block; "
                    "write elements with a[i] = value",
                )
            typ = self.expression(statement.value)
            if typ != statement.symbol.typ:
                raise make("E0312", f"cannot assign {typ} to {statement.symbol.typ}",
                           statement.loc, statement.loc.advance(statement.name),
                           "there are no implicit conversions between i64, bool and str")
        elif isinstance(statement, ExprStmt):
            if not isinstance(statement.expr, Call):
                raise make("E0313", "only function calls can be used as statements",
                           statement.loc,
                           hint="remove the expression or use its value, as in `print(x)`")
            self.expression(statement.expr)
        elif isinstance(statement, Return):
            assert self.current_function is not None
            if self.defer_depth:
                raise make(
                    "E0430",
                    "a deferred block cannot return from the function",
                    statement.loc, statement.loc.advance("return"),
                    hint="the block runs while its scope is being left; do the work "
                         "in the function body instead",
                )
            expected = self.current_function.returns
            actual = "void" if statement.value is None else self.expression(statement.value)
            if statement.value is not None and actual == "void":
                raise make("E0314", "a void call cannot be returned as a value", statement.loc)
            if is_array(actual):
                raise make(
                    "E0330",
                    "a function cannot return an array",
                    statement.loc,
                    hint="write into a mut [T] parameter, or return an element",
                )
            if actual != expected:
                raise make("E0315", f"return expects {expected}, got {actual}", statement.loc)
        elif isinstance(statement, If):
            typ = self.expression(statement.condition)
            if typ != "bool":
                raise make("E0316", f"if condition must be bool, got {typ}",
                           statement.condition.loc,
                           hint="integers are not converted to bool; compare explicitly, as in x != 0")
            self.check_statements(statement.then_body, new_scope=True)
            if statement.else_body is not None:
                self.check_statements(statement.else_body, new_scope=True)
        elif isinstance(statement, While):
            typ = self.expression(statement.condition)
            if typ != "bool":
                raise make("E0317", f"while condition must be bool, got {typ}",
                           statement.condition.loc,
                           hint="integers are not converted to bool; compare explicitly, as in x > 0")
            self.loop_depth += 1
            self.loop_labels.append(getattr(statement, "label", None))
            try:
                self.check_statements(statement.body, new_scope=True)
            finally:
                self.loop_labels.pop()
                self.loop_depth -= 1
        elif isinstance(statement, For):
            self.check_for(statement)
        elif isinstance(statement, (Break, Continue)):
            name = "break" if isinstance(statement, Break) else "continue"
            if not self.loop_depth:
                if self.defer_depth:
                    raise make(
                        "E0430",
                        f"a deferred block cannot `{name}` out of itself",
                        statement.loc, statement.loc.advance(name),
                        hint="the block runs while its scope is being left",
                    )
                code = "E0343" if isinstance(statement, Break) else "E0344"
                raise make(code, f"'{name}' is only allowed inside a loop", statement.loc,
                           hint="a loop is `while` or `for`")
            if statement.label is not None and statement.label not in self.loop_labels:
                raise make(
                    "E0431",
                    f"no enclosing loop carries the label {statement.label!r}",
                    statement.loc, statement.loc.advance(f"{name} {statement.label}"),
                    hint="label the loop itself, as in `outer: for i in 0..n { ... }`",
                )
        else:
            raise AssertionError(f"unknown statement: {type(statement)}")

    def arithmetic(self, op: str, left: str, right: str, loc) -> str:
        """i64 is checked; f64 is IEEE-754. Mixing them is never implicit."""
        if left == "i64" and right == "i64":
            return "i64"
        if left == "f64" and right == "f64":
            if op == "%":
                raise make("E0319", "operator '%' is defined for i64 only", loc,
                           hint="use to_i64 or a formula such as a - b * to_f64(to_i64(a / b))")
            return "f64"
        if left in ARITHMETIC_TYPES and right in ARITHMETIC_TYPES:
            raise make(
                "E0345",
                f"operator {op!r} cannot mix {left} and {right}",
                loc,
                hint="convert explicitly with to_f64(x) or to_i64(x)"
                     "; there is no implicit integer/float conversion",
            )
        raise make("E0319", f"operator {op!r} expects two i64 or two f64 values", loc)

    def comparable(self, op: str, left: str, right: str, loc) -> None:
        if left == right and left in ARITHMETIC_TYPES:
            return
        if left in ARITHMETIC_TYPES and right in ARITHMETIC_TYPES:
            raise make(
                "E0345",
                f"operator {op!r} cannot compare {left} and {right}",
                loc,
                hint="convert explicitly with to_f64(x) or to_i64(x)",
            )
        raise make("E0319", f"operator {op!r} expects two i64 or two f64 values", loc)

    def check_defer(self, statement: Defer) -> None:
        """A deferred block runs when its block is left, so it cannot leave it.

        `return`, `break`, `continue` and `?` all jump somewhere else; inside a
        `defer` the enclosing scope is being destroyed, so each of them is
        refused. Loop depth is reset while the body is checked, which is what
        makes every jump visible as an error rather than as a jump over the
        deferred work.
        """
        saved_depth, saved_labels = self.loop_depth, self.loop_labels
        self.loop_depth, self.loop_labels = 0, []
        self.defer_depth += 1
        self.scopes.append({})
        try:
            self.check_statements(statement.body, new_scope=False)
        finally:
            self.scopes.pop()
            self.defer_depth -= 1
            self.loop_depth, self.loop_labels = saved_depth, saved_labels

    def check_for(self, statement: For) -> None:
        """A `for` iterates over a half-open i64 range or over one array."""
        iterable = statement.iterable
        element: str
        if isinstance(iterable, Range):
            for bound in (iterable.start, iterable.end):
                bound_type = self.expression(bound)
                if bound_type != "i64":
                    raise make("E0349", f"a for-loop bound must be i64, got {bound_type}",
                               bound.loc,
                               hint="ranges are i64 only; use to_i64(x) to convert an f64")
            iterable.typ = "range"
            element = "i64"
        else:
            iterable_type = self.expression(iterable)
            if not is_array(iterable_type):
                raise make(
                    "E0350",
                    f"a for loop iterates over an array or a range a..b, not over {iterable_type}",
                    iterable.loc,
                    hint="write `for x in values` for an array or `for i in 0..n` for a count",
                )
            if not isinstance(iterable, (Variable, ArrayLiteral, Slice)):
                # Array literals and slices are allowed: both are values whose
                # storage outlives the loop (a literal is a statement temporary,
                # a slice borrows an array that is still in scope).
                raise make("E0350", "a for loop iterates over an array, a slice or a literal",
                           iterable.loc)
            element = element_of(iterable_type)
        name_loc = statement.name_loc or statement.loc
        self.scopes.append({})
        try:
            statement.symbol = self.declare(statement.name, element, name_loc)
            # The loop rebinds the variable, so a write inside the body would
            # silently desynchronise the iteration.
            statement.symbol.readonly = True
            self.loop_depth += 1
            self.loop_labels.append(statement.label)
            try:
                self.check_statements(statement.body, new_scope=False)
            finally:
                self.loop_labels.pop()
                self.loop_depth -= 1
        finally:
            self.scopes.pop()

    def expression(self, expr: Expr, expected: str | None = None) -> str:
        self.enter(expr.loc)
        try:
            return self.expression_inner(expr, expected)
        finally:
            self.leave()

    def expression_inner(self, expr: Expr, expected: str | None = None) -> str:
        if isinstance(expr, Number):
            typ = "i64"
        elif isinstance(expr, Float):
            typ = "f64"
        elif isinstance(expr, Range):
            raise make("E0350", "a range a..b is only allowed directly after `in`",
                       expr.loc)
        elif isinstance(expr, String):
            typ = "str"
        elif isinstance(expr, Boolean):
            typ = "bool"
        elif isinstance(expr, Variable):
            symbol = self.find_symbol(expr.name)
            if symbol is not None:
                expr.symbol = symbol
                typ = symbol.typ
            else:
                # Not a variable: a declared function used as a value (edition 2)
                # is the only other thing a bare name can be.
                function = self.function_named(expr.name)
                if function is None:
                    raise make("E0308", f"unknown variable {expr.name!r}", expr.loc,
                               expr.loc.advance(expr.name),
                               "declare it with `let` in this or an enclosing block before use")
                if function.type_params:
                    raise make(
                        "E0437",
                        f"{expr.name!r} is a generic function, so it has no single type",
                        expr.loc, expr.loc.advance(expr.name),
                        hint="a template is instantiated by a call; write a non-generic "
                             "wrapper to pass it as a value",
                    )
                expr.function = function
                expr.typ = function_signature(function)
                typ = expr.typ
        elif isinstance(expr, Try):
            raise make(
                "E0413",
                "`?` is only allowed in a `let` initializer",
                expr.loc, expr.loc.advance("?"),
                hint="write `let value = call()?`; the early return has to be a statement",
            )
        elif isinstance(expr, VariantValue):
            typ = self.variant_value(expr)
        elif isinstance(expr, StructLiteral):
            typ = self.struct_literal(expr)
        elif isinstance(expr, Member):
            typ = self.member(expr)
        elif isinstance(expr, ArrayLiteral):
            typ = self.array_literal(expr, expected)
        elif isinstance(expr, Index):
            base = self.expression(expr.array)
            if not is_array(base):
                raise make("E0329", f"only an array can be indexed, but {expr.array.name!r} "
                                    f"is {base}", expr.loc, expr.loc.advance(expr.array.name))
            index_type = self.expression(expr.index)
            if index_type != "i64":
                raise make("E0328", f"an array index must be i64, got {index_type}",
                           expr.index.loc,
                           hint="integers are not converted; use i64 arithmetic")
            typ = element_of(base)
        elif isinstance(expr, Slice):
            typ = self.slice(expr)
        elif isinstance(expr, Call):
            typ = self.call(expr, expected)
        elif isinstance(expr, Unary):
            actual = self.expression(expr.operand)
            if expr.op == "-":
                if actual not in ARITHMETIC_TYPES:
                    raise make("E0318", f"unary '-' expects i64 or f64, got {actual}", expr.loc)
                expr.typ = actual
                return actual
            expected = "bool"
            if actual != expected:
                raise make("E0318", f"operator {expr.op!r} expects {expected}, got {actual}",
                           expr.loc)
            typ = expected
        elif isinstance(expr, Binary):
            left = self.expression(expr.left)
            right = self.expression(expr.right)
            if expr.op in {"+", "-", "*", "/", "%"}:
                typ = self.arithmetic(expr.op, left, right, expr.loc)
            elif expr.op in {"<", "<=", ">", ">="}:
                self.comparable(expr.op, left, right, expr.loc)
                typ = "bool"
            elif expr.op in {"==", "!="}:
                if left == right and is_function_type(left):
                    raise make(
                        "E0442",
                        f"operator {expr.op!r} cannot compare function values",
                        expr.loc,
                        hint="a function value is the address of its code; compare the "
                             "results of calls instead",
                    )
                if left != right or left not in VALUE_TYPES:
                    raise make("E0320",
                               f"operator {expr.op!r} requires two values of the same type",
                               expr.loc)
                typ = "bool"
            elif expr.op in {"&&", "||"}:
                if left != "bool" or right != "bool":
                    raise make("E0321", f"operator {expr.op!r} expects two bool values", expr.loc)
                typ = "bool"
            else:
                raise AssertionError(f"unknown binary operator: {expr.op}")
        else:
            raise AssertionError(f"unknown expression: {type(expr)}")
        expr.typ = typ
        return typ

    def array_literal(self, expr: ArrayLiteral, expected: str | None) -> str:
        """A literal creates a new array; all items must share one element type."""
        if not expr.items:
            if expected is None or not is_array(expected):
                raise make(
                    "E0335",
                    "an empty array literal needs a type annotation",
                    expr.loc,
                    hint="write `let a: [i64] = []` so the element type is known",
                )
            return expected
        element = None
        for item in expr.items:
            actual = self.expression(item)
            # A struct is an element type in edition 2 either way; an array, a
            # str-owning value or an enum is still refused, and says so with the
            # element-type rule rather than as a generic mismatch.
            if actual not in VALUE_TYPES and not self.is_struct(actual) \
                    and not self.is_enum(actual):
                raise make("E0334",
                           f"array literal elements must be i64, bool, str or a struct, "
                           f"got {actual}",
                           item.loc)
            if element is None:
                element = actual
            elif actual != element:
                raise make("E0334",
                           f"array literal elements must all have the same type; "
                           f"expected {element}, got {actual}",
                           item.loc,
                           hint="there are no implicit conversions between i64, bool and str")
        assert element is not None
        result = f"[{element}]"
        if expected is not None and expected != result:
            raise make("E0311", f"expected {expected}, got {result}", expr.loc,
                       hint="annotate the literal or make every element the same type")
        return result

    def call(self, expr: Call, expected: str | None = None) -> str:
        if expr.module is None:
            indirect = self.indirect_call(expr)
            if indirect is not None:
                return indirect
        if expr.module is None and self.is_template(expr.name) \
                and expr.name not in self.by_module.get(self.current_module, {}):
            raise make(
                "E0437",
                f"{expr.name!r} is a generic declaration, not a value",
                expr.loc, expr.loc.advance(expr.name),
                hint=f"write {expr.name}(i64)(...) with the type arguments",
            )
        if expr.module is None and self.is_struct(expr.name):
            raise make(
                "E0381",
                f"{expr.name!r} is a struct: its fields are named at the construction site",
                expr.loc, expr.loc.advance(expr.name),
                hint=f"write {expr.name}(field: value, ...) with every field of the struct",
            )
        if expr.module is None and expr.name == "print":
            if len(expr.args) != 1:
                raise make("E0322", "print expects exactly one argument", expr.loc)
            if self.expression(expr.args[0]) not in PRINTABLE_TYPES:
                raise make("E0323", "print expects an i64, bool, f64, or str value", expr.loc)
            return "void"
        if expr.module is None and expr.name == "read_i64":
            if expr.args:
                raise make("E0324", "read_i64 expects no arguments", expr.loc)
            return "i64"
        if expr.module is None and expr.name == "read_f64":
            if expr.args:
                raise make("E0348", "read_f64 expects no arguments", expr.loc)
            return "f64"
        if expr.module is None and expr.name in ("to_f64", "to_i64"):
            source_type, target_type = ("i64", "f64") if expr.name == "to_f64" else ("f64", "i64")
            if len(expr.args) != 1:
                raise make("E0347", f"{expr.name} expects exactly one argument", expr.loc)
            actual = self.expression(expr.args[0])
            if actual != source_type:
                raise make("E0347", f"{expr.name} expects {source_type}, got {actual}",
                           expr.args[0].loc,
                           hint=f"{expr.name} converts {source_type} to {target_type}")
            return target_type
        if expr.module is None and expr.name == "len":
            if len(expr.args) != 1:
                raise make("E0337", "len expects exactly one argument", expr.loc)
            actual = self.expression(expr.args[0])
            if not is_array(actual):
                raise make("E0338", f"len expects an array, got {actual}", expr.loc)
            return "i64"
        if expr.module is None and expr.name == "array_zeros":
            if len(expr.args) != 1:
                raise make("E0339", "array_zeros expects exactly one argument", expr.loc)
            actual = self.expression(expr.args[0])
            if actual != "i64":
                raise make("E0340", f"array_zeros expects an i64 length, got {actual}",
                           expr.args[0].loc)
            # Zeroed storage is defined for every element type: `0`, `false`, an
            # empty string, or a struct whose every field is zeroed.
            return expected if is_array(expected) else "[i64]"
        if expr.module is None and expr.name in ("bytes_len", "chars_len"):
            if len(expr.args) != 1:
                raise make("E0326", f"{expr.name} expects exactly one argument", expr.loc)
            actual = self.expression(expr.args[0])
            if actual != "str":
                raise make("E0327", f"argument 1 of {expr.name} expects str, got {actual}",
                           expr.args[0].loc)
            return "i64"
        if expr.module is None and expr.name == "byte_at":
            if len(expr.args) != 2:
                raise make("E0326", f"byte_at expects 2 arguments, got {len(expr.args)}",
                           expr.loc)
            actual = self.expression(expr.args[0])
            if actual != "str":
                raise make("E0327", f"argument 1 of byte_at expects str, got {actual}",
                           expr.args[0].loc)
            index_type = self.expression(expr.args[1])
            if index_type != "i64":
                raise make("E0327", f"argument 2 of byte_at expects i64, got {index_type}",
                           expr.args[1].loc)
            return "i64"
        if expr.module is None and expr.name == "read_file":
            if len(expr.args) != 1:
                raise make("E0326", "read_file expects exactly one argument", expr.loc)
            actual = self.expression(expr.args[0])
            if actual != "str":
                raise make("E0327", f"argument 1 of read_file expects str, got {actual}",
                           expr.args[0].loc)
            return "str"
        if expr.module is None and expr.name == "write_file":
            if len(expr.args) != 2:
                raise make("E0326", f"write_file expects 2 arguments, got {len(expr.args)}",
                           expr.loc)
            actual = self.expression(expr.args[0])
            if actual != "str":
                raise make("E0327", f"argument 1 of write_file expects str, got {actual}",
                           expr.args[0].loc)
            text_type = self.expression(expr.args[1])
            if text_type != "str":
                raise make("E0327", f"argument 2 of write_file expects str, got {text_type}",
                           expr.args[1].loc)
            return "void"
        function = self.resolve(expr)
        if len(expr.args) != len(function.params):
            plural = "" if len(function.params) == 1 else "s"
            raise make(
                "E0326",
                f"{expr.name} expects {len(function.params)} argument{plural}, got {len(expr.args)}",
                expr.loc, expr.loc.advance(expr.name),
            )
        if function.type_params:
            return self.generic_call(expr, function)
        if expr.type_args is not None:
            raise make(
                "E0438",
                f"{expr.name} is not generic, so it takes no type arguments",
                expr.loc, expr.loc.advance(expr.name),
                hint=f"call it as {expr.name}(...)",
            )
        self.check_arguments(expr, function.params)
        expr.target = function
        return function.returns

    def indirect_call(self, expr: Call) -> str | None:
        """`f(3)` where `f` is a variable: a call through a function value.

        A declared function wins over a variable of the same name, so every
        edition-1 program keeps meaning what it meant. The parameters of a
        function type are immutable: a `fn([i64]) -> void` may read an array it
        is handed, and only a declared function can ask for `mut`.
        """
        symbol = self.find_symbol(expr.name)
        if symbol is None or self.function_named(expr.name) is not None:
            return None
        if not is_function_type(symbol.typ):
            raise make(
                "E0439",
                f"{expr.name!r} is {symbol.typ}, so it cannot be called",
                expr.loc, expr.loc.advance(expr.name),
                hint="only a variable of a function type can be called; declare it as "
                     f"`let {expr.name}: fn(i64) -> i64 = ...`",
            )
        params, returns = function_type_parts(symbol.typ)
        if len(expr.args) != len(params):
            plural = "" if len(params) == 1 else "s"
            raise make(
                "E0326",
                f"{expr.name} expects {len(params)} argument{plural}, got {len(expr.args)}",
                expr.loc, expr.loc.advance(expr.name),
                hint=f"its type is {symbol.typ}",
            )
        self.check_arguments(expr, [Param("argument", item, expr.loc) for item in params])
        expr.callee = symbol
        return returns

    def generic_call(self, expr: Call, template: Function) -> str:
        """A call of a template: one type per instantiation, checked once."""
        if expr.type_args is not None:
            substitution = self.type_argument_binding(expr, template)
            concrete = self.instantiate_function(template, substitution)
            self.check_arguments(expr, concrete.params)
            expr.target = concrete
            return concrete.returns
        actual: list[str] = []
        for arg, param in zip(expr.args, template.params):
            # A parameter whose type is fully written can already be the context
            # of an empty array literal; one that mentions `T` cannot.
            expected = self.argument_context(arg, param.typ) \
                if not self.mentions_type_parameter(param.typ, template.type_params) else None
            actual.append(self.expression(arg, expected=expected))
        substitution = self.infer_binding(expr, template, actual)
        concrete = self.instantiate_function(template, substitution)
        self.check_arguments(expr, concrete.params, actual)
        expr.target = concrete
        return concrete.returns

    @staticmethod
    def mentions_type_parameter(typ: str, parameters: list[str]) -> bool:
        if typ in parameters:
            return True
        if is_function_type(typ):
            params, returns = function_type_parts(typ)
            return any(Checker.mentions_type_parameter(item, parameters)
                       for item in params + [returns])
        split = split_instance(typ)
        if split is None:
            return False
        return any(Checker.mentions_type_parameter(item, parameters) for item in split[1])

    @staticmethod
    def argument_context(arg, typ: str) -> str | None:
        """An empty array literal takes its element type from the parameter."""
        return typ if isinstance(arg, ArrayLiteral) and not arg.items else None

    def check_arguments(self, expr: Call, params, actual: list[str] | None = None) -> None:
        """Argument types, and the exclusivity of every mutable borrow."""
        borrowed: dict[str, int] = {}
        for index, (arg, param) in enumerate(zip(expr.args, params), start=1):
            if actual is None:
                real = self.expression(arg, expected=self.argument_context(arg, param.typ))
            else:
                real = actual[index - 1]
            if real != param.typ:
                raise make("E0327",
                           f"argument {index} of {expr.name} expects {param.typ}, got {real}",
                           arg.loc)
            if not is_array(param.typ) or not param.mutable:
                continue
            # A mutable borrow must be exclusive and must come from an owner:
            # a borrow of a borrow could alias another live mutable view.
            if isinstance(arg, Variable) and arg.symbol.borrowed:
                raise make(
                    "E0423",
                    f"argument {index} of {expr.name} is `mut {param.typ}`, but {arg.name!r} "
                    f"is a slice: a borrow cannot be handed on as a mutable borrow",
                    arg.loc,
                    hint="pass the array that owns the storage",
                )
            if not isinstance(arg, Variable) or not arg.symbol.owned:
                raise make(
                    "E0333",
                    f"argument {index} of {expr.name} is `mut {param.typ}`, so it must be an "
                    f"owned array variable, not a borrow",
                    arg.loc,
                    hint="element writes are only allowed on an array created in this function",
                )
            if arg.name in borrowed:
                raise make(
                    "E0342",
                    f"{arg.name!r} is passed to two mutable parameters of {expr.name} "
                    f"(arguments {borrowed[arg.name]} and {index})",
                    arg.loc,
                    hint="a mutable borrow must be exclusive: copy into a second array first",
                )
            borrowed[arg.name] = index


def check(program: Program) -> Program:
    return Checker(program).check()
