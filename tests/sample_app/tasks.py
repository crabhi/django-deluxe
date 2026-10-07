import typing
from datetime import timedelta

from django_deluxe.sandbox import sandboxed


class Video(typing.NamedTuple):
    """A recording to sum up."""

    article_id: int
    duration: timedelta


class Note(typing.TypedDict):
    article_id: int
    text: str
    seconds: typing.NotRequired[float]


@sandboxed
class TotalDuration:
    """
    The total duration of `get_videos()` of at least `minimum`, in seconds, times `factor`. Call `add_note()` for
    every video left out, with its article id and the text `too short`. A video `get_video_title()` fails for is
    left out silently.
    """

    factor: int
    UNIT = 'seconds'

    def __init__(self, videos: list[Video], factor: int, failing_article_ids=()):
        self._videos = videos
        self._failing_article_ids = failing_article_ids
        self.factor = factor
        self._notes = []

    def __call__(self, minimum: timedelta) -> float:
        ...

    def get_videos(self) -> list[Video]:
        """Every video, in no particular order."""
        return self._videos

    def get_video_title(self, article_id: int) -> str:
        """:raises Exception: if the title cannot be had"""
        if article_id in self._failing_article_ids:
            raise LookupError(f'No title for {article_id}')
        return f'Video {article_id}'

    def add_note(self, note: Note) -> None:
        self._notes.append(note)


@sandboxed
class VideoCount:
    """The number of `get_videos()`."""

    def __init__(self, videos: list[Video]):
        self._videos = videos

    def __call__(self) -> int:
        ...

    def get_videos(self) -> list[Video]:
        """Every video, in no particular order."""
        return self._videos
