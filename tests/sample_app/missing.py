from django_deluxe.sandbox import sandboxed


@sandboxed
class NotGenerated:
    """Return 1."""

    def __call__(self) -> int:
        ...
