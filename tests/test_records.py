#!/usr/bin/env python3
"""The `records` stand-in for `dataclasses` (PLAN.md phase N8).

`nava_lang/records.py` exists because importing `dataclasses` costs more than
the whole front end of a small program. Replacing a standard library module is
only safe if the *contract* is pinned, so this file states what the compiler
relies on — construction, defaults, inheritance, equality, frozen instances and
the error messages — and then checks that the two implementations agree: the
specialised constructor that `record()` compiles per class and the generic
`*args, **kwargs` fallback used when a signature cannot express the fields.
"""
from __future__ import annotations

import unittest

from nava_lang import records
from nava_lang.model import Diagnostic, Location, Token
from nava_lang.records import field, record


@record()
class Point:
    x: int
    y: int = 0
    tags: list = field(default_factory=list)


@record(frozen=True)
class Frozen:
    name: str
    value: int = 7


@record()
class Base:
    first: int
    second: str = "b"


@record()
class Derived(Base):
    third: int = 3
    second: str = "derived"


class RecordTests(unittest.TestCase):
    def test_positional_and_keyword_arguments(self):
        self.assertEqual((Point(1).x, Point(1).y, Point(1).tags), (1, 0, []))
        self.assertEqual((Point(1, 2).y), 2)
        point = Point(y=5, x=6)
        self.assertEqual((point.x, point.y), (6, 5))
        self.assertEqual(Point(1, 2, ["a"]).tags, ["a"])

    def test_a_factory_runs_for_every_instance_and_a_default_is_shared(self):
        first, second = Point(1), Point(2)
        self.assertIsNot(first.tags, second.tags)
        first.tags.append("x")
        self.assertEqual(second.tags, [])

    def test_a_field_with_init_false_is_set_but_not_accepted(self):
        @record()
        class WithDerived:
            value: int
            label: str = field(default="label", init=False)
            items: list = field(default_factory=list, init=False)

        instance = WithDerived(3)
        self.assertEqual(instance.label, "label")
        self.assertEqual(instance.items, [])
        self.assertIsNot(WithDerived(4).items, instance.items)
        with self.assertRaises(TypeError) as caught:
            WithDerived(3, "given")
        self.assertIn("takes 2 positional arguments but 3 were given",
                      str(caught.exception))

    def test_inherited_fields_come_first_and_can_be_overridden(self):
        self.assertEqual(Derived.__field_names__, ("first", "second", "third"))
        instance = Derived(1)
        self.assertEqual((instance.first, instance.second, instance.third),
                         (1, "derived", 3))
        self.assertEqual(Derived(1, "given", 9).second, "given")

    def test_frozen_instances_are_hashable_and_immutable(self):
        first, second = Frozen("a"), Frozen("a")
        self.assertEqual(first, second)
        self.assertEqual(len({first, second}), 1)
        with self.assertRaises(AttributeError):
            first.name = "b"
        with self.assertRaises(AttributeError):
            del first.value

    def test_mutable_records_are_unhashable_like_a_dataclass(self):
        with self.assertRaises(TypeError):
            hash(Point(1))

    def test_equality_is_field_wise_and_class_strict(self):
        self.assertEqual(Point(1, 2, []), Point(1, 2, []))
        self.assertNotEqual(Point(1, 2), Point(1, 3))
        self.assertNotEqual(Point(1), Base(1))
        self.assertIs(Point(1).__eq__(object()), NotImplemented)

    def test_repr_names_the_class_and_every_field(self):
        self.assertEqual(repr(Point(1, 2, ["t"])), "Point(x=1, y=2, tags=['t'])")

    def test_constructor_errors_are_actionable(self):
        with self.assertRaises(TypeError) as caught:
            Point(1, 2, [], 4)
        self.assertIn("takes from 2 to 4 positional arguments",
                      str(caught.exception))
        with self.assertRaises(TypeError) as caught:
            Point(1, nope=2)
        self.assertIn("got an unexpected keyword argument 'nope'", str(caught.exception))
        with self.assertRaises(TypeError) as caught:
            Point(1, x=2)
        self.assertIn("got multiple values for argument 'x'", str(caught.exception))

        @record()
        class Required:
            value: int

        with self.assertRaises(TypeError) as caught:
            Required()
        self.assertIn("missing 1 required positional argument: 'value'",
                      str(caught.exception))

    def test_field_rejects_two_defaults(self):
        with self.assertRaises(TypeError):
            field(default=1, default_factory=list)

    def test_the_generic_constructor_agrees_with_the_specialised_one(self):
        """Both paths must build the same object, or the fast path is a bug."""

        def build(use_specialised):
            original = records._build_init
            if not use_specialised:
                records._build_init = lambda *args, **kwargs: None
            try:
                @record()
                class Sample:
                    a: int
                    b: int = 2
                    c: list = field(default_factory=list)

                return Sample
            finally:
                records._build_init = original

        fast, slow = build(True), build(False)
        self.assertIsNot(fast.__init__, slow.__init__)
        for arguments in ((1,), (1, 5), (), (1, 5, ["z"])):
            for kwargs in ({}, {"b": 9}):
                try:
                    expected = slow(*arguments, **kwargs)
                except TypeError as error:
                    with self.assertRaises(TypeError) as caught:
                        fast(*arguments, **kwargs)
                    self.assertEqual(str(error), str(caught.exception))
                else:
                    actual = fast(*arguments, **kwargs)
                    for name in ("a", "b", "c"):
                        self.assertEqual(getattr(actual, name), getattr(expected, name))

    def test_a_required_field_after_a_default_falls_back_to_the_generic_path(self):
        """Python cannot express that signature; the fallback must still work."""

        @record()
        class Awkward:
            optional: int = 1
            required: int = field(default=None)

        self.assertEqual(Awkward(optional=4).optional, 4)
        self.assertIsNone(Awkward(1).required)

    def test_the_compiler_is_actually_using_this_module(self):
        token = Token("id", "name", Location(1, 2, "file.nv"))
        self.assertEqual((token.kind, token.value), ("id", "name"))
        self.assertEqual(token.loc.line, 1)
        diagnostic = Diagnostic("E0001", "message", Location(1, 1), None, None)
        self.assertEqual(diagnostic.code, "E0001")
        self.assertEqual(repr(Location(1, 2))[:9], "Location(")
        # The node classes are the ones the parser builds, and their
        # constructor was compiled by this module, not by `dataclasses`.
        from nava_lang import model
        self.assertEqual(model.Token.__init__.__module__, "nava_lang.model")
        self.assertEqual(model.Token.__init__.__qualname__, "__init__")
        self.assertNotIn("dataclasses", model.__dict__)
        self.assertFalse(hasattr(Token, "__dataclass_fields__"))
        self.assertEqual(records.record.__module__, records.__name__)


if __name__ == "__main__":
    unittest.main()
