"""Small record classes without importing `dataclasses` (PLAN.md phase N8).

`nava` is a command-line compiler, so the interpreter start-up and the imports
of the compiler itself are part of every build. `bench/profile.py` shows that
importing `dataclasses` costs more than the whole front end of a small program:
it pulls in `inspect`, `dis`, `linecache` and `typing` before a single token is
lexed. The AST nodes in `model.py` need only four things from a dataclass —
a positional-or-keyword constructor, defaults, field-wise equality and a
readable `repr` — so this module provides exactly those, in about thirty lines,
and nothing else.

Usage mirrors the decorator it replaces:

    @record()
    class Token:
        kind: str
        loc: Location
        value: str = ""

    @record(frozen=True)
    class Location:
        line: int
        column: int
        path: str | None = None

`field(default=..., default_factory=..., init=False)` works like the dataclass
helper it looks like. There is no `order=`, no `slots=` and no validation
beyond "unknown keyword argument" and "too many positional arguments": keeping
the surface small is the point, and `tests/test_records.py` pins the behaviour
that the compiler relies on.
"""
from __future__ import annotations

_MISSING = object()


class _Field:
    """A field declaration: a default, a factory, or neither."""

    __slots__ = ("default", "factory", "init")

    def __init__(self, default=_MISSING, factory=None, init=True):
        self.default = default
        self.factory = factory
        self.init = init


def field(*, default=_MISSING, default_factory=None, init=True):
    if default is not _MISSING and default_factory is not None:
        raise TypeError("a field takes a default or a default_factory, not both")
    return _Field(default=default, factory=default_factory, init=init)


def _collect(target) -> list[tuple[str, _Field]]:
    """Fields of `target`, base classes first, like a dataclass."""
    order: list[str] = []
    specs: dict[str, _Field] = {}
    for klass in reversed(target.__mro__):
        for name in klass.__dict__.get("__annotations__", {}):
            declared = klass.__dict__.get(name, _MISSING)
            if name not in specs:
                order.append(name)
            specs[name] = declared if isinstance(declared, _Field) else _Field(
                default=declared)
    return [(name, specs[name]) for name in order]


def _build_init(name: str, module: str, initialised, specs, frozen: bool):
    """A constructor specialised for one class.

    The generic `*args, **kwargs` version allocated a dictionary per node and
    rebuilt the field lookup on every call; `bench/profile.py` charged it with
    a third of the front end on a large module. Compiling a signature per class
    puts that work back at decoration time, where it happens once.
    """
    parameters: list[str] = ["self"]
    body: list[str] = []
    namespace = {"_MISSING": _MISSING, "__name__": module}
    counter = 0
    seen_default = False
    for field_name, spec in initialised:
        if spec.factory is not None:
            seen_default = True
            # A factory must run per instance, so the argument needs a sentinel
            # and a guard; a plain default can sit in the signature and cost
            # nothing at call time.
            parameters.append(f"{field_name}=_MISSING")
        elif spec.default is not _MISSING:
            seen_default = True
            namespace[f"_default{counter}"] = spec.default
            parameters.append(f"{field_name}=_default{counter}")
            counter += 1
        elif seen_default:
            return None  # not expressible as a signature; keep the generic one
        else:
            parameters.append(field_name)
    for field_name, spec in initialised:
        if spec.factory is not None:
            namespace[f"_factory{counter}"] = spec.factory
            body.append(f"    if {field_name} is _MISSING:")
            body.append(f"        {field_name} = _factory{counter}()")
            counter += 1
    for field_name, spec in specs:
        if spec.init:
            value = field_name
        elif spec.factory is not None:
            namespace[f"_factory{counter}"] = spec.factory
            body.append(f"    {field_name} = _factory{counter}()")
            value = field_name
            counter += 1
        elif spec.default is not _MISSING:
            namespace[f"_default{counter}"] = spec.default
            body.append(f"    {field_name} = _default{counter}")
            value = field_name
            counter += 1
        else:
            body.append(f"    {field_name} = None")
            value = field_name
        if frozen:
            body.append(f"    object.__setattr__(self, {field_name!r}, {value})")
        else:
            body.append(f"    self.{field_name} = {value}")
    source = f"def __init__({', '.join(parameters)}):\n" + "\n".join(body) + "\n"
    try:
        exec(compile(source, f"<record {name}>", "exec"), namespace)
    except SyntaxError:  # pragma: no cover - guarded by the ordering check above
        return None
    return namespace["__init__"]


def record(cls=None, *, frozen: bool = False):
    """Give `cls` a constructor, equality and a repr built from its annotations."""

    def decorate(target):
        specs = _collect(target)
        initialised = [(name, spec) for name, spec in specs if spec.init]
        names = tuple(name for name, _ in specs)

        def __init__(self, *args, **kwargs):
            """The fallback for a class whose fields cannot form a signature.

            The wording of every error mirrors the specialised constructor, so
            the two paths cannot be told apart by a caller.
            """
            if len(args) > len(initialised):
                raise TypeError(
                    f"__init__() takes {len(initialised)} positional arguments "
                    f"but {len(args)} were given")
            values = {}
            for (name, _), value in zip(initialised, args):
                values[name] = value
            for name, value in kwargs.items():
                if name not in known:
                    raise TypeError("__init__() got an unexpected keyword "
                                    f"argument {name!r}")
                if name in values:
                    raise TypeError(f"__init__() got multiple values for "
                                    f"argument {name!r}")
                values[name] = value
            missing = [name for name, spec in initialised if name not in values
                       and spec.factory is None and spec.default is _MISSING]
            if missing:
                plural = "s" if len(missing) > 1 else ""
                listed = ", ".join(repr(name) for name in missing)
                raise TypeError(f"__init__() missing {len(missing)} required "
                                f"positional argument{plural}: {listed}")
            for name, spec in specs:
                if name in values:
                    continue
                if spec.factory is not None:
                    values[name] = spec.factory()
                elif spec.default is not _MISSING:
                    values[name] = spec.default
                else:
                    values[name] = None
            for name in names:
                object.__setattr__(self, name, values[name])

        known = frozenset(name for name, _ in initialised)
        fast = _build_init(target.__name__, target.__module__, initialised, specs,
                           frozen)

        def __repr__(self):
            rendered = ", ".join(f"{name}={getattr(self, name)!r}" for name in names)
            return f"{target.__name__}({rendered})"

        def __eq__(self, other):
            if other.__class__ is not target:
                return NotImplemented
            return all(getattr(self, name) == getattr(other, name) for name in names)

        target.__init__ = fast if fast is not None else __init__
        target.__repr__ = __repr__
        target.__eq__ = __eq__
        target.__field_names__ = names
        if frozen:
            def __hash__(self):
                return hash((target, ) + tuple(
                    getattr(self, name) if not isinstance(getattr(self, name), list)
                    else tuple(getattr(self, name)) for name in names))

            def __setattr__(self, name, value):
                raise AttributeError(f"{target.__name__} is frozen")

            def __delattr__(self, name):
                raise AttributeError(f"{target.__name__} is frozen")

            target.__hash__ = __hash__
            target.__setattr__ = __setattr__
            target.__delattr__ = __delattr__
        else:
            # A dataclass with `eq=True` and no `frozen=True` is unhashable;
            # assigning `__eq__` after class creation does not clear `__hash__`
            # the way defining it in the body would, so do it explicitly.
            target.__hash__ = None
        return target

    return decorate(cls) if cls is not None else decorate
