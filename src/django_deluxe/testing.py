"""
Testing generated code where it runs — in Monty, type-checked against the stubs — with fakes for the host side.

Running a generated function on CPython would let it pass on what Monty does not implement, so the tests of
generated code go through `run_generated()`. `check_generated()` is the contract test of a reviewed module: the
generated module exists, type-checks against the boundary and defines every entry function.
"""

from typing import Any

from django_deluxe.sandbox import get_definition


def run_generated(cls: type, function_name: str, *args, namespace: dict[str, Any] | None = None, **kwargs) -> Any:
    """
    Call `function_name` of the generated module of the sandboxed `cls`, its entry function or a helper, with
    `namespace` standing in for the instance: fake host functions and values by their public names. A name left
    out raises `NameError` in the sandbox if the code reaches for it.
    """

    return get_definition(cls).run(namespace or {}, function_name, args, kwargs)


def check_generated(cls: type) -> None:
    """Of the module of `cls`; see `SandboxedModule.check()`."""

    get_definition(cls).module.check()
