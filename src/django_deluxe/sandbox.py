"""
A sandboxed function is a class decorated with `@sandboxed`. The class is what a human writes and reviews:

- its docstring is the task, the prompt the code is generated from;
- `__call__`'s signature is the signature of the generated entry function, `snake_case` of the class name;
- its public names — those not starting with an underscore — are everything the generated code can reach: a
  method becomes a host function, any other attribute a value. Each is declared, typed, in the stubs the
  generated code is type-checked against, so the stubs are exactly the boundary to review.

The code is generated ahead of time, committed, and run by Monty in a worker subprocess. It lives in the same
module path within the Django app, under `generated`: `<app>/generated/<module>.py` for `<app>/<module>.py` — one
generated module for every `@sandboxed` class of a module, plus whatever helpers the code needs. Calling an
instance of the class feeds the whole generated module to Monty with the instance's public names bound, then
calls the entry function and returns what it returns. The module is type-checked against the boundaries of all its
classes together; at runtime only the called class's names are bound.

Only plain data crosses the boundary: `None`, `bool`, `int`, `float`, `str`, `bytes`, `datetime.date`,
`datetime.datetime`, `datetime.timedelta`, `list`, `tuple`, `dict`, `set`, `frozenset`, `typing.Literal`,
`typing.NamedTuple` and `typing.TypedDict` classes, and unions of these. A NamedTuple comes back from the sandbox as
the host class; a TypedDict is a plain `dict` at runtime and only the type checker enforces its keys. A pydantic
model or a dataclass crosses as its JSON form, a `dict` the stubs name after the class but do not describe — see
`django_deluxe.conversion`.

An exception a host function raises reaches the sandbox as an `Exception` with its message, unless it is one of
the builtin exception types Monty implements.
"""

import atexit
import collections.abc
import contextlib
import dataclasses
import datetime
import functools
import importlib
import inspect
import logging
import re
import types
import typing
from pathlib import Path
from typing import Any

from django.apps import apps
from pydantic_monty import Monty, ResourceLimits

from django_deluxe.conversion import from_sandbox, is_converted, is_namedtuple, to_sandbox
from django_deluxe.stubs import render_stub_block, strip_stub_block


GENERATED_PACKAGE = 'generated'
# Monty itself has no memory or time limit by default; the time counts only while the sandbox executes, not while
# a host function runs.
DEFAULT_LIMITS: ResourceLimits = {
    'max_memory': 512 * 2 ** 20,
    'max_feed_duration_secs': 60.0,
}
SCALAR_TYPES = (bool, int, float, str, bytes)
DATETIME_TYPES = (datetime.date, datetime.datetime, datetime.timedelta)
CONTAINER_TYPES = (list, tuple, dict, set, frozenset)
ARGUMENTS_NAME = '__deluxe_arguments'
KEYWORD_ARGUMENTS_NAME = '__deluxe_keyword_arguments'

_definitions: list['SandboxDefinition'] = []


class BoundaryError(TypeError):
    """A public name of a sandboxed class is of a type that cannot cross into the sandbox, or has no type at all."""


@typing.overload
def sandboxed(cls: type, /) -> type: ...


@typing.overload
def sandboxed(*, limits: ResourceLimits | None = None) -> collections.abc.Callable[[type], type]: ...


def sandboxed(cls=None, /, *, limits=None):
    """
    Make the class a sandboxed function: calling an instance runs the generated entry function in Monty.

    :param limits: Monty's resource limits, over `DEFAULT_LIMITS`; `max_suspensions` (host calls, 1000 by default)
        is the one a long run needs raised
    """

    def decorate(cls):
        definition = SandboxDefinition(cls, DEFAULT_LIMITS | (limits or {}))
        cls._sandbox_definition = definition
        cls.__call__ = _make_call(definition)
        _definitions.append(definition)
        return cls

    return decorate(cls) if cls else decorate


def get_definition(cls: type) -> 'SandboxDefinition':
    return cls._sandbox_definition


@dataclasses.dataclass
class SandboxDefinition:
    """One `@sandboxed` class: its entry function and its boundary."""

    cls: type
    limits: ResourceLimits

    def __post_init__(self):
        # `__call__` is replaced by the decorator; the original is the contract of the entry function
        self.call_signature = inspect.signature(self.cls.__call__)

    @functools.cached_property
    def entry_name(self) -> str:
        return re.sub(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])', '_', self.cls.__name__).lower()

    @functools.cached_property
    def boundary(self) -> 'Boundary':
        return Boundary.of(self.cls)

    @property
    def module(self) -> 'SandboxedModule':
        return SandboxedModule.of(self.cls.__module__)

    def get_task(self) -> str:
        """The entry function and what it does, the class docstring."""

        entry_signature = self.boundary.format_signature(self.call_signature, skip_self=True)
        return TASK_TEMPLATE.format(
            entry_name=self.entry_name,
            entry_signature=entry_signature,
            task=inspect.cleandoc(self.cls.__doc__ or ''),
            names=', '.join(f'`{name}`' for name in self.boundary.names) or 'nothing',
        )

    def run(self, namespace: dict[str, Any], function_name: str, arguments: tuple, keyword_arguments: dict) -> Any:
        """
        Feed the generated module to Monty with `namespace` bound — callables as host functions, anything else as
        values — then call `function_name` in it.

        :raises pydantic_monty.MontyTypingError: if the module does not type-check against the stubs
        :raises pydantic_monty.MontyRuntimeError: if the code raised; `display()` is its traceback
        """

        unknown_names = set(namespace) - set(self.boundary.names)
        if unknown_names:
            raise ValueError(f'Not on the boundary of {self.cls.__qualname__}: {", ".join(sorted(unknown_names))}')

        if function_name == self.entry_name:  # a helper's types are the generated code's own
            bound_arguments = self.call_signature.bind(None, *arguments, **keyword_arguments)
            _convert_arguments(bound_arguments, self.call_signature, to_sandbox)
            arguments, keyword_arguments = bound_arguments.args[1:], bound_arguments.kwargs
        with self.module.feed(self._wrap_namespace(namespace), self.limits) as feed_run:
            result = feed_run(
                f'{function_name}(*{ARGUMENTS_NAME}, **{KEYWORD_ARGUMENTS_NAME})',
                inputs={ARGUMENTS_NAME: list(arguments), KEYWORD_ARGUMENTS_NAME: keyword_arguments},
            )
        if function_name == self.entry_name:
            return from_sandbox(result, self.call_signature.return_annotation)
        return result

    def _wrap_namespace(self, namespace: dict[str, Any]) -> dict[str, Any]:
        """Host functions and values converted where their annotations have a model or a dataclass."""

        wrapped_namespace = {}
        for name, value in namespace.items():
            if name in self.boundary.functions:
                wrapped_namespace[name] = _wrap_host_function(value, self.boundary.functions[name].signature)
            else:
                wrapped_namespace[name] = to_sandbox(value, self.boundary.values[name])
        return wrapped_namespace


def _wrap_host_function(function, signature: inspect.Signature):
    """`signature` is of the method, `self` first; `function` may be the bound method or a fake standing in."""

    @functools.wraps(function)
    def host_function(*args, **kwargs):
        bound_arguments = signature.bind(None, *args, **kwargs)
        _convert_arguments(bound_arguments, signature, from_sandbox)
        return to_sandbox(function(*bound_arguments.args[1:], **bound_arguments.kwargs), signature.return_annotation)

    return host_function


def _convert_arguments(bound_arguments: inspect.BoundArguments, signature: inspect.Signature, convert) -> None:
    for name, value in bound_arguments.arguments.items():
        parameter = signature.parameters[name]
        if parameter.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
            bound_arguments.arguments[name] = convert(value, parameter.annotation)


def _make_call(definition: SandboxDefinition):
    def __call__(self, *args, **kwargs):
        namespace = {name: getattr(self, name) for name in definition.boundary.names}
        return definition.run(namespace, definition.entry_name, args, kwargs)

    __call__.__signature__ = definition.call_signature
    __call__.__doc__ = definition.cls.__call__.__doc__
    return __call__


def find_sandboxed_modules() -> list['SandboxedModule']:
    """
    Of every generated module of the installed apps, importing the module it is generated from — which registers
    its `@sandboxed` classes.
    """

    sandboxed_modules = []
    for app_config in apps.get_app_configs():
        generated_directory = Path(app_config.path, GENERATED_PACKAGE)
        for path in sorted(generated_directory.rglob('*.py')):
            relative_path = path.relative_to(generated_directory).with_suffix('')
            if relative_path.name == '__init__' or 'tests' in relative_path.parts:
                continue
            name = '.'.join([app_config.name, *relative_path.parts])
            importlib.import_module(name)
            sandboxed_modules.append(SandboxedModule.of(name))
    return sandboxed_modules


@dataclasses.dataclass(frozen=True)
class SandboxedModule:
    """A module with `@sandboxed` classes, and the one generated module for all of them."""

    name: str

    @classmethod
    @functools.cache
    def of(cls, name: str) -> 'SandboxedModule':
        return cls(name)

    @property
    def definitions(self) -> list[SandboxDefinition]:
        return [definition for definition in _definitions if definition.cls.__module__ == self.name]

    @functools.cached_property
    def generated_module(self) -> str:
        app_config = apps.get_containing_app_config(self.name)
        if app_config is None:
            raise ValueError(f'{self.name} is in no installed Django app')
        return f'{app_config.name}.{GENERATED_PACKAGE}.{self.name.removeprefix(f"{app_config.name}.")}'

    @functools.cached_property
    def generated_path(self) -> Path:
        app_config = apps.get_containing_app_config(self.name)
        relative_module = self.generated_module.removeprefix(f'{app_config.name}.')
        return Path(app_config.path, *relative_module.split('.')).with_suffix('.py')

    @functools.cached_property
    def boundary(self) -> 'Boundary':
        """Of every class together: what the generated module is type-checked against."""

        return Boundary.merge(self.name, [definition.boundary for definition in self.definitions])

    @functools.cached_property
    def stubs(self) -> str:
        return self.boundary.render_stubs()

    @functools.cached_property
    def stub_block(self) -> str:
        """For the generated module itself; see `django_deluxe.stubs`."""

        return render_stub_block(self)

    def get_prompt(self) -> str:
        """What the module is generated from: the tasks, the stubs and the rules of the sandbox."""

        return PROMPT_TEMPLATE.format(
            path=self.generated_path,
            tasks='\n\n'.join(definition.get_task() for definition in self.definitions),
            stubs=self.stubs,
        )

    def check(self) -> None:
        """
        :raises FileNotFoundError: if there is no generated module
        :raises pydantic_monty.MontyTypingError: if it does not type-check against the stubs
        :raises pydantic_monty.MontyRuntimeError: if it lacks an entry function — a `NameError`
        """

        with self.feed({}, DEFAULT_LIMITS) as feed_run:
            for definition in self.definitions:
                feed_run(definition.entry_name)

    @contextlib.contextmanager
    def feed(self, namespace: dict[str, Any], limits: ResourceLimits):
        """
        A session that has run the generated module with `namespace` bound, and a function feeding it more code,
        not type-checked.
        """

        inputs = {name: value for name, value in namespace.items() if not callable(value)}
        external_lookup = self.boundary.get_constructors() | {
            name: value for name, value in namespace.items() if callable(value)
        }
        logger = logging.getLogger(self.generated_module)

        def print_callback(stream, text):
            logger.info('%s', text.rstrip('\n'))

        source = strip_stub_block(self.generated_path.read_text())
        with get_pool().checkout(
            script_name=str(self.generated_path),
            limits=limits,
            type_check=True,
            type_check_stubs=self.stubs,
        ) as session:
            session.feed_run(source, inputs=inputs, external_lookup=external_lookup, print_callback=print_callback)

            def feed_run(code, *, inputs=None):
                return session.feed_run(
                    code,
                    inputs=inputs,
                    external_lookup=external_lookup,
                    print_callback=print_callback,
                    skip_type_check=True,
                )

            yield feed_run


@functools.cache
def get_pool() -> Monty:
    """One pool of Monty workers per process, closed when it exits."""

    pool = Monty()
    pool.__enter__()
    atexit.register(pool.__exit__, None, None, None)
    return pool


@dataclasses.dataclass
class HostFunction:
    signature: inspect.Signature
    docstring: str | None


@dataclasses.dataclass
class Boundary:
    """Public names and their types: the host functions, the values and the NamedTuple and TypedDict types they use."""

    owner: str  # what the boundary is of, for the errors
    functions: dict[str, HostFunction]
    values: dict[str, Any]  # name to type
    types: dict[str, type] = dataclasses.field(default_factory=dict)  # by name, as `format_type()` finds them
    converted_types: dict[str, type] = dataclasses.field(default_factory=dict)  # models and dataclasses, by name

    @classmethod
    def of(cls, sandboxed_cls: type) -> 'Boundary':
        annotations = typing.get_type_hints(sandboxed_cls)
        names = sorted({name for name in dir(sandboxed_cls) if not name.startswith('_')} |
                       {name for name in annotations if not name.startswith('_')})
        functions = {}
        values = {}
        for name in names:
            attribute = inspect.getattr_static(sandboxed_cls, name, None)
            if inspect.isfunction(attribute):
                functions[name] = HostFunction(inspect.signature(attribute), attribute.__doc__)
            elif name in annotations:
                values[name] = annotations[name]
            elif isinstance(attribute, SCALAR_TYPES):
                values[name] = type(attribute)
            else:
                raise BoundaryError(
                    f'{sandboxed_cls.__qualname__}.{name}: annotate it on the class, or make it a method')
        boundary = cls(sandboxed_cls.__qualname__, functions, values)
        boundary.render_stubs()  # every type checked now, so that a wrong one fails when the class is first used
        return boundary

    @classmethod
    def merge(cls, owner: str, boundaries: list['Boundary']) -> 'Boundary':
        """A name several boundaries share must be declared alike in each."""

        merged = cls(owner, {}, {})
        for boundary in boundaries:
            for name, host_function in boundary.functions.items():
                known = merged.functions.setdefault(name, host_function)
                if known.signature != host_function.signature:
                    raise BoundaryError(f'{owner}: {name} is declared differently in {boundary.owner}')
            for name, type_ in boundary.values.items():
                if merged.values.setdefault(name, type_) != type_:
                    raise BoundaryError(f'{owner}: {name} is declared differently in {boundary.owner}')
        merged.render_stubs()
        return merged

    @property
    def names(self) -> list[str]:
        return [*self.functions, *self.values]

    def get_constructors(self) -> dict[str, type]:
        """The NamedTuple classes, so that the sandbox can make one; a TypedDict, called, makes a plain dict."""

        return dict(self.types)

    def render_stubs(self) -> str:
        """`.pyi` source of the boundary: the types, the values and the host functions with their docstrings."""

        value_lines = [f'{name}: {self.format_type(type_)}' for name, type_ in self.values.items()]
        function_blocks = [
            self._render_function(name, host_function)
            for name, host_function in self.functions.items()
        ]
        type_blocks = []
        rendered = set()
        while pending := [name for name in self.types if name not in rendered]:  # a type may use further ones
            for name in pending:
                type_blocks.append(self._render_type(self.types[name]))
                rendered.add(name)
        converted_lines = [
            f'{name} = dict[str, typing.Any]  # {type_.__module__}.{type_.__qualname__}'
            for name, type_ in sorted(self.converted_types.items())
        ]
        return '\n\n'.join(block for block in [
            'import datetime\nimport typing',
            '\n'.join(converted_lines),
            *type_blocks,
            '\n'.join(value_lines),
            *function_blocks,
        ] if block) + '\n'

    def format_signature(self, signature: inspect.Signature, *, skip_self=False) -> str:
        parameters = []
        for parameter in list(signature.parameters.values())[1 if skip_self else 0:]:
            text = parameter.name
            if parameter.annotation is not inspect.Parameter.empty:
                text += f': {self.format_type(parameter.annotation)}'
            if parameter.default is not inspect.Parameter.empty:
                text += ' = ...'
            if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
                text = f'*{text}'
            elif parameter.kind is inspect.Parameter.VAR_KEYWORD:
                text = f'**{text}'
            parameters.append(text)
        returns = signature.return_annotation
        returns = 'None' if returns is inspect.Signature.empty else self.format_type(returns)
        return f'({", ".join(parameters)}) -> {returns}'

    def format_type(self, type_: Any) -> str:
        """The annotation as stub source; a NamedTuple or TypedDict it uses is added to `types`."""

        origin = typing.get_origin(type_)
        arguments = typing.get_args(type_)
        if type_ is None or type_ is types.NoneType:
            return 'None'
        if type_ is typing.Any:
            return 'typing.Any'
        if isinstance(type_, str):
            raise BoundaryError(f'{self.owner}: unresolved annotation {type_!r}')
        if origin is typing.Literal:
            return f'typing.Literal[{", ".join(repr(argument) for argument in arguments)}]'
        if origin in (typing.Union, types.UnionType):
            members = [argument for argument in arguments if argument is not types.NoneType]
            if len(members) > 1 and any(is_converted(member) for member in members):
                # which one a dict coming back is would be a guess
                raise BoundaryError(f'{self.owner}: {type_} — a model or a dataclass can be in a union with None only')
            return ' | '.join(self.format_type(argument) for argument in arguments)
        if origin in CONTAINER_TYPES:
            formatted = [
                '...' if argument is Ellipsis else self.format_type(argument)
                for argument in arguments
            ]
            return f'{origin.__name__}[{", ".join(formatted)}]'
        if type_ in SCALAR_TYPES or type_ in CONTAINER_TYPES:
            return type_.__name__
        if type_ in DATETIME_TYPES:
            return f'datetime.{type_.__name__}'
        if is_converted(type_):
            known = self.converted_types.setdefault(type_.__name__, type_)
            if known is not type_:
                raise BoundaryError(f'{self.owner}: two types named {type_.__name__}')
            return type_.__name__
        if typing.is_typeddict(type_) or is_namedtuple(type_):
            known = self.types.setdefault(type_.__name__, type_)
            if known is not type_:
                raise BoundaryError(f'{self.owner}: two types named {type_.__name__}')
            return type_.__name__
        raise BoundaryError(f'{self.owner}: {type_!r} cannot cross into the sandbox')

    def _render_function(self, name: str, host_function: HostFunction) -> str:
        docstring = host_function.docstring
        body = f'    """\n{_indent(inspect.cleandoc(docstring))}\n    """' if docstring else '    ...'
        return f'def {name}{self.format_signature(host_function.signature, skip_self=True)}:\n{body}'

    def _render_type(self, type_: type) -> str:
        hints = typing.get_type_hints(type_)
        lines = []
        if type_.__doc__ and not type_.__doc__.startswith(f'{type_.__name__}('):  # not NamedTuple's own
            lines.append(f'    """\n{_indent(inspect.cleandoc(type_.__doc__))}\n    """')
        if typing.is_typeddict(type_):
            base = 'typing.TypedDict'
            for field, field_type in hints.items():
                formatted = self.format_type(field_type)
                if field in type_.__optional_keys__:
                    formatted = f'typing.NotRequired[{formatted}]'
                lines.append(f'    {field}: {formatted}')
        else:
            base = 'typing.NamedTuple'
            for field in type_._fields:
                default = ' = ...' if field in type_._field_defaults else ''
                lines.append(f'    {field}: {self.format_type(hints[field])}{default}')
        return f'class {type_.__name__}({base}):\n' + '\n'.join(lines or ['    pass'])


def _indent(text: str) -> str:
    return '\n'.join(f'    {line}' if line else '' for line in text.splitlines())


TASK_TEMPLATE = '''\
## `{entry_name}`

    def {entry_name}{entry_signature}

It may use {names} — and nothing else of the stubs.

{task}'''

PROMPT_TEMPLATE = '''\
Write the Python module `{path}`. It runs in Monty, a sandboxed interpreter of a subset of Python 3.14.

# The entry functions it defines

{tasks}

# What the code can use

The host functions and values below are bound as globals; the module does not import or define them. They are
declared as stubs, and the module is type-checked against them before it runs: a type error fails the run.

```python
{stubs}```

A type declared as `<Name> = dict[str, typing.Any]  # <module>.<Name>` is a pydantic model or a dataclass of the host,
crossing as its JSON form: a `dict` of its field names (not their aliases) without the fields that are `None`, enums
as their values, and dates, times and durations as ISO 8601 strings. A `dict` the code passes back where one is
expected is validated into the class, so it may also use the class's aliases and give dates, times and durations as
`datetime` objects. Look the shape up in the class itself; where the code holds such a value, name the class in a
comment, e.g. `# GenerateContentResponse`.

# Rules

- Structure the module as you see fit: helper functions and constants are welcome. Its top level only defines
  them — calling a host function there is not allowed.
- Monty is not CPython. There are no third-party packages, and of the standard library only `asyncio`, `base64`,
  `collections`, `copy`, `dataclasses`, `datetime`, `functools`, `itertools`, `json`, `math`, `re`, `typing`
  and parts of them. Classes cannot inherit and methods cannot be decorated; there is no `yield`, `match`, `del`,
  `enum`, `logging` and no user-defined exception classes. `print()` goes to the host's log.
- Raise only builtin exceptions. A host function that fails raises `Exception` with a message: catch it only where
  its docstring says it raises and the task says what to do then; anything else must propagate.
- Annotate every function; values passed to host functions must match the stubs.
- Leave the block between `### <django-deluxe-stubs>` and `### </django-deluxe-stubs>` alone, or leave it out:
  `manage.py deluxe_stubs` writes it, for the IDE; the sandbox never sees it. The classes it imports are there to
  look shapes up in: a pydantic model or a dataclass reaches the code as a dict, never as an instance.
'''
