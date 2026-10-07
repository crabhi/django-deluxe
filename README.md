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

Monty does no IO of its own: no files, no network, no database. Whatever the generated code reads or writes, it does
through the host functions the class gives it — so the class decides exactly how far the code reaches.

Say a customer's orders are to be labelled by their amount. The code doing it is the LLM's; the database is not. It
gets two functions: one reading the customer's orders, as plain rows, and one saving labels — of that customer's
orders, and of two values only. No model instance, queryset or connection makes it to the sandbox.

```python
# myapp/models.py
class Customer(models.Model):
    name = models.CharField(max_length=200)


class Order(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE)
    created = models.DateTimeField()
    amount = models.IntegerField()
    label = models.CharField(max_length=20, blank=True)
```

```python
# myapp/tasks.py
import datetime
import typing

from django.utils import timezone

from django_deluxe.sandbox import sandboxed
from myapp.models import Customer, Order


class OrderRow(typing.NamedTuple):
    id: int
    created: datetime.datetime
    amount: int


@sandboxed
class LabelOrders:
    """
    Label the orders of `get_orders()` created in the last `days` days before `now`: `large` if their amount is over
    1 000, `small` if it is under 10. Save the labels with one call of `save_labels()` and return how many there are.
    """

    now: datetime.datetime  # a value: bound as a global in the sandbox

    def __init__(self, customer: Customer):
        self._orders = Order.objects.filter(customer=customer)  # private: never reaches the sandbox
        self.now = timezone.now()

    def __call__(self, days: int) -> int:  # the entry function's signature
        ...

    def get_orders(self, since: datetime.datetime) -> list[OrderRow]:  # a host function
        """The orders of the customer created since `since`, oldest first."""
        rows = self._orders.filter(created__gte=since).order_by('created')
        return [OrderRow(*row) for row in rows.values_list('id', 'created', 'amount')]

    def save_labels(self, labels: dict[int, typing.Literal['large', 'small']]) -> None:
        """Order id to its label. An order not of the customer is skipped."""
        for label in ('large', 'small'):
            ids = [order_id for order_id, order_label in labels.items() if order_label == label]
            self._orders.filter(id__in=ids).update(label=label)
```

The host functions enforce the scope themselves: `save_labels()` updates through the customer's queryset, so an id
the code makes up — or takes from elsewhere — changes nothing. Saving in one call rather than per order also keeps
the run within the host calls Monty allows (see [Resource limits](#resource-limits)).

Every public name must be typed with something that can cross into the sandbox — scalars, `datetime`s, containers,
`Literal`, `NamedTuple`, `TypedDict`, pydantic models and dataclasses. Anything else — a Django model, a queryset —
fails when the class is first used, so the ORM cannot leak into the boundary by accident.

## 2. Write the stubs

The generated module lives in `myapp/generated/tasks.py`. Create it empty and let `deluxe_stubs` write the stub
block into it: the names the sandbox binds, for the IDE and the linters, which would see them undefined otherwise.

```console
$ mkdir -p myapp/generated && touch myapp/generated/tasks.py
$ ./manage.py deluxe_stubs          # writes the block into every generated module
Updated .../myapp/generated/tasks.py
```

```python
# myapp/generated/tasks.py
### <django-deluxe-stubs>
# For the IDE and the linters only: the sandbox strips this block and binds these names itself.
# A pydantic model or a dataclass crosses as a dict of its JSON form, not as an instance: where a name below says
# "dict", the class is only there to look the dict's shape up in.
# isort: off
from myapp.tasks import LabelOrders
from myapp.tasks import OrderRow

# LabelOrders: `label_orders()`
get_orders = LabelOrders.get_orders
save_labels = LabelOrders.save_labels
now = LabelOrders.now
# isort: on
### </django-deluxe-stubs>
```

Run it again whenever the boundary changes; `./manage.py deluxe_stubs --check` fails in CI if a block is missing or
stale.

## 3. Generate the code

The prompt is rendered from the class — its docstring, the entry function's signature, the stubs of every public name
and the rules of Monty's Python subset — and tells the LLM which file to write. Pipe it to Claude Code, letting it
edit files:

```console
$ ./manage.py deluxe_sandbox_prompt myapp.tasks | claude -p --permission-mode acceptEdits
```

The prompt, for reference:

````console
$ ./manage.py deluxe_sandbox_prompt myapp.tasks
Write the Python module `.../myapp/generated/tasks.py`. It runs in Monty, a sandboxed interpreter of a subset of Python 3.14.

# The entry functions it defines

## `label_orders`

    def label_orders(days: int) -> int

It may use `get_orders`, `save_labels`, `now` — and nothing else of the stubs.

Label the orders of `get_orders()` created in the last `days` days before `now`: ...

# What the code can use
...
```python
import datetime
import typing

class OrderRow(typing.NamedTuple):
    id: int
    created: datetime.datetime
    amount: int

now: datetime.datetime

def get_orders(since: datetime.datetime) -> list[OrderRow]:
    """
    The orders of the customer created since `since`, oldest first.
    """

def save_labels(labels: dict[int, typing.Literal['large', 'small']]) -> None:
    """
    Order id to its label. An order not of the customer is skipped.
    """
```
...
````

Claude writes the module around the stub block; review the diff and commit it:

```python
# myapp/generated/tasks.py
import typing
from datetime import timedelta

### <django-deluxe-stubs>
...
### </django-deluxe-stubs>


def label_orders(days: int) -> int:
    labels: dict[int, typing.Literal['large', 'small']] = {}
    for order in get_orders(now - timedelta(days=days)):
        if order.amount > 1_000:
            labels[order.id] = 'large'
        elif order.amount < 10:
            labels[order.id] = 'small'
    save_labels(labels)
    return len(labels)
```

The module is type-checked against the stubs every time it runs, so code reaching for something that is not there —
or saving a label other than the two — fails before it does anything.

## 4. Call it

```python
label_orders = LabelOrders(customer)  # this week: orders of 5 000, 50 and 3
label_orders(days=7)                  # 2, computed in Monty; `large` and `small` are saved
```

`print()` in the generated code goes to the logger of the generated module, `myapp.generated.tasks`.

## 5. Test it

Tests of the generated code run it in Monty — not on CPython, where it would pass on things Monty lacks — with fakes
standing in for the instance, so they need no database:

```python
from django.utils import timezone

from django_deluxe.testing import check_generated, run_generated


def test_large_and_small_orders_are_labelled():
    now = timezone.now()
    saved = []

    result = run_generated(
        LabelOrders, 'label_orders', 7,
        namespace={
            'get_orders': lambda since: [OrderRow(1, now, 5_000), OrderRow(2, now, 50)],
            'save_labels': saved.append,
            'now': now,
        },
    )

    assert result == 1
    assert saved == [{1: 'large'}]


def test_contract():  # the one test on the reviewed side
    check_generated(LabelOrders)  # exists, type-checks against the boundary, defines every entry function
```

`run_generated()` calls helpers of the generated module too: `run_generated(LabelOrders, '_my_helper', ...)`.

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
