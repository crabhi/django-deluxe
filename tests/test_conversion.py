import datetime
import inspect

import pydantic
import pytest

from django_deluxe.conversion import from_sandbox, to_sandbox
from django_deluxe.sandbox import Boundary, BoundaryError, get_definition
from django_deluxe.testing import run_generated
from tests.sample_app.billing import BillUsage, Cost, Delivery, Modality, TokenCount, Usage


USAGE = Usage(
    details=[TokenCount(modality=Modality.TEXT, token_count=100), TokenCount(modality=Modality.VIDEO, token_count=900)],
    created=datetime.datetime(2026, 10, 7, 12, 30, tzinfo=datetime.UTC),
)


def test_models_and_dataclasses_are_named_in_the_stubs_but_not_described():
    assert get_definition(BillUsage).module.stubs == inspect.cleandoc('''
        import datetime
        import typing

        Cost = dict[str, typing.Any]  # tests.sample_app.billing.Cost
        Usage = dict[str, typing.Any]  # tests.sample_app.billing.Usage

        class Delivery(typing.TypedDict):
            usage: Usage
            label: str

        def get_cost(usage: Usage) -> Cost:
            ...

        def get_usage() -> Usage:
            ...

        def record(delivery: Delivery) -> None:
            ...
    ''') + '\n'


def test_the_host_gets_objects_the_sandbox_dicts():
    bill_usage = BillUsage(USAGE)

    assert bill_usage(2.0) == Cost(tokens=900, usd=1.8)
    assert bill_usage._deliveries == [Delivery(
        usage=Usage(details=[TokenCount(modality=Modality.VIDEO, token_count=900)], created=USAGE.created),
        label='video',
    )]


def test_the_sandbox_sees_the_json_form():
    seen = []

    run_generated(BillUsage, 'bill_usage', 1.0, namespace={
        'get_usage': lambda: USAGE,
        'get_cost': lambda usage: seen.append(usage) or Cost(),
        'record': seen.append,
    })

    delivery, usage = seen
    assert isinstance(usage, Usage)  # validated back on the way out
    assert delivery['usage'] == usage


def test_the_json_form_has_no_nones_and_strings_for_enums_and_datetimes():
    assert to_sandbox({'usage': USAGE, 'label': 'all'}, Delivery) == {
        'usage': {
            'details': [{'modality': 'TEXT', 'token_count': 100}, {'modality': 'VIDEO', 'token_count': 900}],
            'created': '2026-10-07T12:30:00Z',
        },
        'label': 'all',
    }
    assert to_sandbox(None, Usage | None) is None
    assert to_sandbox([Cost(1, 0.5)], list[Cost]) == [{'tokens': 1, 'usd': 0.5}]


def test_a_dict_not_valid_for_its_class_is_refused():
    with pytest.raises(pydantic.ValidationError, match='modality'):
        from_sandbox({'details': [{'modality': 'AUDIO', 'token_count': 1}], 'created': USAGE.created}, Usage)


def test_a_model_in_a_union_with_anything_but_none_is_refused():
    class Ambiguous:
        def get(self) -> Usage | str:
            ...

    with pytest.raises(BoundaryError, match='in a union with None only'):
        Boundary.of(Ambiguous)
