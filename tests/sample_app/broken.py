from django_deluxe.sandbox import sandboxed


@sandboxed
class Mistyped:
    """Return the length of `NAME`."""

    NAME = 'x'

    def __call__(self) -> int:
        ...
