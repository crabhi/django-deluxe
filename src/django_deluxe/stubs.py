"""
The stub block of a generated module: the names the host binds, for the IDE and the linters — which see the module
as Python and would report every one of them undefined. The block imports the real classes and binds every name to
the attribute of the sandboxed class — an unbound method for a host function, which is close enough for the IDE to
know the types and the docstrings; the block is never run. Monty never sees it: it
gets the stubs instead (`SandboxedModule.stubs`), and `strip_stub_block()` blanks the block out, keeping the line
numbers.
"""

import ast
import collections
import re
import typing


if typing.TYPE_CHECKING:
    from django_deluxe.sandbox import SandboxedModule


START_MARKER = '### <django-deluxe-stubs>'
END_MARKER = '### </django-deluxe-stubs>'
STUB_BLOCK_RE = re.compile(rf'^{re.escape(START_MARKER)}\n.*?^{re.escape(END_MARKER)}\n', re.MULTILINE | re.DOTALL)
HEADER = '''\
# For the IDE and the linters only: the sandbox strips this block and binds these names itself.
# A pydantic model or a dataclass crosses as a dict of its JSON form, not as an instance: where a name below says
# "dict", the class is only there to look the dict's shape up in.
# isort: off'''


def render_stub_block(sandboxed_module: 'SandboxedModule') -> str:
    boundary = sandboxed_module.boundary
    converted_names = set(boundary.converted_types)

    module_2_classes = collections.defaultdict(list)
    for type_ in [*boundary.converted_types.values(), *boundary.types.values()]:
        module_2_classes[type_.__module__].append(type_)
    for definition in sandboxed_module.definitions:
        module_2_classes[definition.cls.__module__].append(definition.cls)
    import_lines = []
    for module, classes in sorted(module_2_classes.items()):
        for cls in sorted(classes, key=lambda cls: cls.__qualname__):
            comment = '  # a dict in the sandbox' if cls.__name__ in converted_names else ''
            import_lines.append(f'from {module} import {cls.__qualname__}{comment}')

    binding_lines = []
    for definition in sandboxed_module.definitions:
        # a name several classes share is bound by each, the last one winning — they are declared alike
        binding_lines.extend(['', f'# {definition.cls.__qualname__}: `{definition.entry_name}()`'])
        for name in definition.boundary.names:
            if name in definition.boundary.functions:
                formatted = definition.boundary.format_signature(
                    definition.boundary.functions[name].signature, skip_self=True)
            else:
                formatted = definition.boundary.format_type(definition.boundary.values[name])
            dict_names = sorted(_get_converted_names(formatted, boundary))
            comment = f'  # dicts in the sandbox: {", ".join(dict_names)}' if dict_names else ''
            binding_lines.append(f'{name} = {definition.cls.__qualname__}.{name}{comment}')

    return '\n'.join([START_MARKER, HEADER, *import_lines, *binding_lines, '# isort: on', END_MARKER]) + '\n'


def update_stub_block(source: str, stub_block: str) -> str:
    """`source` with `stub_block` in place of its stub block, or after its leading imports if it has none."""

    if STUB_BLOCK_RE.search(source):
        return STUB_BLOCK_RE.sub(lambda match: stub_block, source, count=1)

    lines = source.splitlines(keepends=True)
    insert_at = 0
    for node in ast.parse(source).body:
        is_docstring = isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and insert_at == 0
        if not (isinstance(node, ast.Import | ast.ImportFrom) or is_docstring):
            break
        insert_at = node.end_lineno or 0
    return ''.join(lines[:insert_at]) + ('\n' if insert_at else '') + stub_block + '\n\n' + ''.join(lines[insert_at:]).lstrip('\n')


def _get_converted_names(formatted_type: str, boundary, seen: frozenset = frozenset()) -> set[str]:
    """The models and dataclasses in `formatted_type`, also those inside its NamedTuples and TypedDicts."""

    converted_names = set()
    for name in re.findall(r'\b\w+\b', formatted_type):
        if name in boundary.converted_types:
            converted_names.add(name)
        elif name in boundary.types and name not in seen:
            for field_type in typing.get_type_hints(boundary.types[name]).values():
                converted_names |= _get_converted_names(boundary.format_type(field_type), boundary, seen | {name})
    return converted_names


def strip_stub_block(source: str) -> str:
    return STUB_BLOCK_RE.sub(lambda match: '\n' * match.group().count('\n'), source)
