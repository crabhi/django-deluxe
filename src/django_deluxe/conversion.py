"""
Pydantic models and dataclasses on the boundary: the host works with the objects, the sandbox with their JSON form.

Wherever such a type stands in an annotation of a host function or value — on its own, or inside a `list`,
`tuple`, `set`, `dict`, a union with `None`, a `TypedDict` or a `NamedTuple` — a value going into the sandbox is
dumped, `pydantic.TypeAdapter(type).dump_python(value, mode='json', exclude_none=True)`, and a value coming out is
validated back, `TypeAdapter(type).validate_python(value)`. So in the sandbox it is a `dict` of the model's field
names, without the fields that are `None`, enums as their values and dates, times and durations as ISO 8601 strings.
Everything else crosses as it is.
"""

import dataclasses
import functools
import types
import typing
from typing import Any

import pydantic


def is_converted(type_: Any) -> bool:
    """A pydantic model or a dataclass: crosses as its JSON form."""

    return isinstance(type_, type) and (dataclasses.is_dataclass(type_) or issubclass(type_, pydantic.BaseModel))


def to_sandbox(value: Any, annotation: Any) -> Any:
    return _convert(value, annotation, _dump) if _needs_conversion(annotation) else value


def from_sandbox(value: Any, annotation: Any) -> Any:
    return _convert(value, annotation, _validate) if _needs_conversion(annotation) else value


@functools.cache
def _needs_conversion(annotation: Any) -> bool:
    if is_converted(annotation):
        return True
    if typing.is_typeddict(annotation) or is_namedtuple(annotation):
        return any(_needs_conversion(hint) for hint in typing.get_type_hints(annotation).values())
    return any(_needs_conversion(argument) for argument in typing.get_args(annotation))


def _convert(value, annotation, convert_model):
    if value is None:
        return None
    if is_converted(annotation):
        return convert_model(value, annotation)

    origin = typing.get_origin(annotation)
    arguments = typing.get_args(annotation)
    if origin in (typing.Union, types.UnionType):
        (annotation,) = [argument for argument in arguments if argument is not types.NoneType]  # see `Boundary`
        return _convert(value, annotation, convert_model)
    if origin in (list, set, frozenset):
        return origin(_convert(item, arguments[0], convert_model) for item in value)
    if origin is tuple:
        if arguments[-1] is Ellipsis:
            return tuple(_convert(item, arguments[0], convert_model) for item in value)
        return tuple(_convert(item, argument, convert_model) for item, argument in zip(value, arguments, strict=True))
    if origin is dict:
        return {key: _convert(item, arguments[1], convert_model) for key, item in value.items()}
    if typing.is_typeddict(annotation):
        hints = typing.get_type_hints(annotation)
        return {key: _convert(item, hints[key], convert_model) for key, item in value.items()}
    if is_namedtuple(annotation):
        hints = typing.get_type_hints(annotation)
        return annotation(*(_convert(getattr(value, field), hints[field], convert_model) for field in annotation._fields))
    return value


def _dump(value, type_):
    return _get_type_adapter(type_).dump_python(value, mode='json', exclude_none=True)


def _validate(value, type_):
    return _get_type_adapter(type_).validate_python(value)


@functools.cache
def _get_type_adapter(type_) -> pydantic.TypeAdapter:
    return pydantic.TypeAdapter(type_)


def is_namedtuple(type_: Any) -> bool:
    return isinstance(type_, type) and issubclass(type_, tuple) and hasattr(type_, '_fields')
