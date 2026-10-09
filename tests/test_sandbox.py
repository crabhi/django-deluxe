import decimal
import inspect
import logging
from datetime import timedelta
from pathlib import Path

import pytest
from django.core.management import call_command
from pydantic_monty import MontyRuntimeError, MontyTypingError

from django_deluxe.sandbox import Boundary, BoundaryError, SandboxedModule, get_definition
from django_deluxe.testing import check_generated, run_generated
from tests.sample_app.broken import Mistyped
from tests.sample_app.missing import NotGenerated
from tests.sample_app.tasks import TotalDuration, Video, VideoCount


VIDEOS = [
    Video(1, timedelta(seconds=30)),
    Video(2, timedelta(minutes=2)),
    Video(3, timedelta(minutes=3)),
]


def test_generated_path_mirrors_the_module_within_the_app():
    module = get_definition(TotalDuration).module

    assert get_definition(TotalDuration).entry_name == 'total_duration'
    assert module.generated_module == 'tests.sample_app.generated.tasks'
    assert module.generated_path == Path(__file__).parent / 'sample_app' / 'generated' / 'tasks.py'


def test_stubs_declare_the_public_names_of_every_class_of_the_module():
    assert get_definition(TotalDuration).module.stubs == inspect.cleandoc('''
        import datetime
        import typing

        class Note(typing.TypedDict):
            article_id: int
            text: str
            seconds: typing.NotRequired[float]

        class Video(typing.NamedTuple):
            """
            A recording to sum up.
            """
            article_id: int
            duration: datetime.timedelta

        UNIT: str
        factor: int

        def add_note(note: Note) -> None:
            ...

        def get_video_title(article_id: int) -> str:
            """
            :raises Exception: if the title cannot be had
            """

        def get_videos() -> list[Video]:
            """
            Every video, in no particular order.
            """
    ''') + '\n'


def test_calling_runs_the_entry_function_with_the_instance_bound():
    total_duration = TotalDuration(VIDEOS, factor=2, failing_article_ids=[3])

    assert total_duration(timedelta(minutes=1)) == 240.0
    assert total_duration._notes == [{'article_id': 1, 'text': 'too short'}]
    assert VideoCount(VIDEOS)() == 3


def test_print_goes_to_the_log_of_the_generated_module(caplog):
    with caplog.at_level(logging.INFO, logger='tests.sample_app.generated.tasks'):
        TotalDuration([], factor=1)(timedelta(0))

    assert caplog.messages == ['total 0.0 seconds']


def test_run_generated_with_fakes():
    notes = []

    result = run_generated(
        TotalDuration, 'total_duration', timedelta(minutes=1),
        namespace={
            'get_videos': lambda: VIDEOS,
            'get_video_title': lambda article_id: '',
            'add_note': notes.append,
            'factor': 1,
            'UNIT': 's',
        },
    )

    assert result == 300.0
    assert notes == [{'article_id': 1, 'text': 'too short'}]


def test_run_generated_helper():
    assert run_generated(TotalDuration, '_seconds', VIDEOS[0]) == 30.0


def test_host_calls_are_logged_at_debug(caplog):
    with caplog.at_level(logging.DEBUG, logger='django_deluxe.sandbox'):
        TotalDuration(VIDEOS[:1], factor=1)(timedelta(minutes=1))

    assert caplog.messages == [
        'tests.sample_app.generated.tasks.total_duration(datetime.timedelta(seconds=60))',
        'get_videos()',
        'get_videos returned [Video(article_id=1, duration=datetime.timedelta(seconds=30))]',
        'get_video_title(1)',
        "get_video_title returned 'Video 1'",
        "add_note({'article_id': 1, 'text': 'too short'})",
        'add_note returned None',
        'tests.sample_app.generated.tasks.total_duration returned 0.0',
    ]


def test_a_long_repr_is_logged_as_its_class(caplog):
    videos = [Video(article_id, timedelta(minutes=2)) for article_id in range(100)]

    with caplog.at_level(logging.DEBUG, logger='django_deluxe.sandbox'):
        run_generated(VideoCount, 'video_count', namespace={'get_videos': lambda: videos})

    assert caplog.messages == [
        'tests.sample_app.generated.tasks.video_count()',
        'get_videos()',
        'get_videos returned list(...)',
        'tests.sample_app.generated.tasks.video_count returned 100',
    ]


def test_host_calls_are_not_logged_above_debug(caplog):
    with caplog.at_level(logging.INFO, logger='django_deluxe.sandbox'):
        TotalDuration(VIDEOS, factor=1)(timedelta(minutes=1))

    assert caplog.messages == []


def test_only_the_names_of_the_class_called_are_bound():
    with pytest.raises(ValueError, match='add_note'):
        run_generated(VideoCount, 'video_count', namespace={'add_note': print})


def test_type_error_fails_before_running():
    with pytest.raises(MontyTypingError, match='unsupported-operator'):
        Mistyped()()


def test_check_generated():
    check_generated(TotalDuration)

    with pytest.raises(MontyRuntimeError, match="NameError: name 'not_generated' is not defined"):
        check_generated(NotGenerated)


def test_a_type_that_cannot_cross_is_refused():
    class Priced:
        def get_price(self) -> decimal.Decimal:
            ...

    with pytest.raises(BoundaryError, match='Decimal'):
        Boundary.of(Priced)


def test_an_unannotated_value_is_refused():
    class Configured:
        OPTIONS = ['a', 'b']

    with pytest.raises(BoundaryError, match='Configured.OPTIONS: annotate it'):
        Boundary.of(Configured)


def test_a_name_declared_differently_in_two_classes_is_refused():
    class First:
        def get_count(self) -> int:
            ...

    class Second:
        def get_count(self) -> str:
            ...

    with pytest.raises(BoundaryError, match='get_count is declared differently in .*Second'):
        Boundary.merge('module', [Boundary.of(First), Boundary.of(Second)])


def test_prompt_has_every_task_and_the_stubs(capsys):
    call_command('deluxe_sandbox_prompt', 'tests.sample_app.tasks')
    prompt = capsys.readouterr().out

    assert prompt == SandboxedModule.of('tests.sample_app.tasks').get_prompt()
    assert 'def total_duration(minimum: datetime.timedelta) -> float' in prompt
    assert 'It may use `add_note`, `get_video_title`, `get_videos`, `UNIT`, `factor`' in prompt
    assert 'The total duration of `get_videos()`' in prompt
    assert 'def video_count() -> int' in prompt
    assert 'def get_videos() -> list[Video]:' in prompt
