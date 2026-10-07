### <django-deluxe-stubs>
# For the IDE and the linters only: the sandbox strips this block and binds these names itself.
# A pydantic model or a dataclass crosses as a dict of its JSON form, not as an instance: where a name below says
# "dict", the class is only there to look the dict's shape up in.
# isort: off
from tests.sample_app.broken import Mistyped

# Mistyped: `mistyped()`
NAME = Mistyped.NAME
# isort: on
### </django-deluxe-stubs>


def mistyped() -> int:
    return NAME + 1
