import dataclasses
import datetime
import enum
import typing

import pydantic

from django_deluxe.sandbox import sandboxed


class Modality(enum.Enum):
    TEXT = 'TEXT'
    VIDEO = 'VIDEO'


class TokenCount(pydantic.BaseModel):
    modality: Modality
    token_count: int
    note: str | None = None


class Usage(pydantic.BaseModel):
    details: list[TokenCount]
    created: datetime.datetime


@dataclasses.dataclass
class Cost:
    tokens: int = 0
    usd: float = 0


class Delivery(typing.TypedDict):
    usage: Usage
    label: str


@sandboxed
class BillUsage:
    """
    `record()` the video part of `get_usage()`, labelled `video`, and return what it cost by `get_cost()`, its price
    times `factor`.
    """

    def __init__(self, usage: Usage):
        self._usage = usage
        self._deliveries = []

    def __call__(self, factor: float) -> Cost:
        ...

    def get_usage(self) -> Usage:
        return self._usage

    def get_cost(self, usage: Usage) -> Cost:
        tokens = sum(token_count.token_count for token_count in usage.details)
        return Cost(tokens=tokens, usd=tokens / 1000)

    def record(self, delivery: Delivery) -> None:
        self._deliveries.append(delivery)
