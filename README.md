<p align="center">
  <img src="docs/logo-black-on-white.png" alt="django deluxe" width="480">
</p>

Run LLM-generated code in the [Monty](https://github.com/pydantic/monty) sandbox, behind a boundary a human reviews.

A sandboxed function is a class decorated with `@sandboxed`: its docstring is the task, `__call__` the signature of the
generated entry function, and its public methods and attributes everything the generated code can reach. The code
lives in `<app>/generated/<module>.py`, is generated ahead of time and committed; calling an instance runs it in Monty.

## Setup

```python
# settings.py
INSTALLED_APPS = [
    ...,
    'django_deluxe',
]
```

## 1. Write the boundary — the part you review

```python
# myapp/tasks.py
import typing
from datetime import timedelta

from django_deluxe.sandbox import sandboxed


class Video(typing.NamedTuple):
    article_id: int
    duration: timedelta


@sandboxed
class TotalDuration:
    """
    The total duration of `get_videos()` of at least `minimum`, in seconds, times `factor`. Call `add_note()` for
    every video left out, with its article id and the text `too short`.
    """

    factor: int  # a value: bound as a global in the sandbox

    def __init__(self, videos: list[Video], factor: int):
        self._videos = videos  # private: never reaches the sandbox
        self.factor = factor
        self.notes = []

    def __call__(self, minimum: timedelta) -> float:  # the entry function's signature
        ...

    def get_videos(self) -> list[Video]:  # a host function
        """Every video, in no particular order."""
        return self._videos

    def add_note(self, note: dict[str, typing.Any]) -> None:
        self.notes.append(note)
```

Every public name must be typed with something that can cross into the sandbox — scalars, `datetime`s, containers,
`Literal`, `NamedTuple`, `TypedDict`, pydantic models and dataclasses. Anything else fails when the class is first used.

## 2. Generate the code

The prompt is rendered from the class — its docstring, the entry function's signature, the stubs of every public name
and the rules of Monty's Python subset:

```console
$ ./manage.py deluxe_sandbox_prompt myapp.tasks
Write the Python module `.../myapp/generated/tasks.py`. It runs in Monty, a sandboxed interpreter of a subset of Python 3.14.

# The entry functions it defines

## `total_duration`

    def total_duration(minimum: datetime.timedelta) -> float

It may use `add_note`, `get_videos`, `factor` — and nothing else of the stubs.
...
```

Hand it to the LLM of your choice; commit what it writes to `myapp/generated/tasks.py`. The module is type-checked
against the stubs every time it runs, so code reaching for something that is not there fails before it does anything.

## 3. Let the IDE know the injected names

```console
$ ./manage.py deluxe_stubs          # writes the block into every generated module
$ ./manage.py deluxe_stubs --check  # for CI: fails if a block is missing or stale
```

```python
# myapp/generated/tasks.py
from datetime import timedelta

### <django-deluxe-stubs>
# For the IDE and the linters only: the sandbox strips this block and binds these names itself.
# A pydantic model or a dataclass crosses as a dict of its JSON form, not as an instance: where a name below says
# "dict", the class is only there to look the dict's shape up in.
# isort: off
from myapp.tasks import TotalDuration
from myapp.tasks import Video

# TotalDuration: `total_duration()`
add_note = TotalDuration.add_note
get_videos = TotalDuration.get_videos
factor = TotalDuration.factor
# isort: on
### </django-deluxe-stubs>


def total_duration(minimum: timedelta) -> float:
    total = 0.0
    for video in get_videos():
        if video.duration < minimum:
            add_note({'article_id': video.article_id, 'text': 'too short'})
            continue
        total += video.duration.total_seconds()
    return total * factor
```

## 4. Call it

```python
total_duration = TotalDuration([Video(1, timedelta(seconds=30)), Video(2, timedelta(minutes=2))], factor=2)
total_duration(timedelta(minutes=1))  # 240.0, computed in Monty
total_duration.notes                  # [{'article_id': 1, 'text': 'too short'}]
```

`print()` in the generated code goes to the logger of the generated module, `myapp.generated.tasks`.

## 5. Test it

Tests of the generated code run it in Monty — not on CPython, where it would pass on things Monty lacks — with fakes
standing in for the instance:

```python
from django_deluxe.testing import check_generated, run_generated


def test_short_videos_are_noted():
    notes = []

    result = run_generated(
        TotalDuration, 'total_duration', timedelta(minutes=1),
        namespace={'get_videos': lambda: [Video(1, timedelta(seconds=30))], 'add_note': notes.append, 'factor': 1},
    )

    assert result == 0.0
    assert notes == [{'article_id': 1, 'text': 'too short'}]


def test_contract():  # the one test on the reviewed side
    check_generated(TotalDuration)  # exists, type-checks against the boundary, defines every entry function
```

`run_generated()` calls helpers of the generated module too: `run_generated(TotalDuration, '_my_helper', ...)`.

## Pydantic models and dataclasses

Host functions keep their real types; the sandbox gets dicts. A model or a dataclass anywhere in an annotation is
dumped on the way in — `TypeAdapter(type).dump_python(value, mode='json', exclude_none=True)` — and validated back on
the way out:

```python
class Usage(pydantic.BaseModel):
    token_count: int
    created: datetime.datetime


@dataclasses.dataclass
class Cost:
    usd: float = 0


@sandboxed
class BillUsage:
    """Return the cost of `get_usage()`, by `get_cost()`."""

    def __call__(self) -> Cost:
        ...

    def get_usage(self) -> Usage:
        return Usage(token_count=1000, created=timezone.now())

    def get_cost(self, usage: Usage) -> Cost:  # receives a `Usage`, validated
        return Cost(usd=usage.token_count / 1000)
```

```python
# myapp/generated/billing.py
def bill_usage() -> Cost:
    usage = get_usage()  # Usage: {'token_count': 1000, 'created': '2026-10-07T12:30:00Z'}
    return get_cost(usage)  # Cost: {'usd': 1.0}
```

```python
BillUsage()()  # Cost(usd=1.0)
```

The stubs name such a type — `Usage = dict[str, typing.Any]  # myapp.billing.Usage` — but do not describe it; the
generated code looks the shape up in the class.

## Resource limits

```python
@sandboxed(limits={'max_suspensions': 100_000, 'max_feed_duration_secs': 300.0})
class ProcessBatch:
    ...
```

The limits go over `django_deluxe.sandbox.DEFAULT_LIMITS` (512 MiB, 60 s of execution, not counting the time host
functions take). `max_suspensions`, the host calls of one run, defaults to Monty's 1000.

See the docstring of `django_deluxe.sandbox` for the details.
