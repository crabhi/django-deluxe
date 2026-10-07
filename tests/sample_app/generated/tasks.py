from datetime import timedelta

### <django-deluxe-stubs>
# For the IDE and the linters only: the sandbox strips this block and binds these names itself.
# A pydantic model or a dataclass crosses as a dict of its JSON form, not as an instance: where a name below says
# "dict", the class is only there to look the dict's shape up in.
# isort: off
from tests.sample_app.tasks import Note
from tests.sample_app.tasks import TotalDuration
from tests.sample_app.tasks import Video
from tests.sample_app.tasks import VideoCount

# TotalDuration: `total_duration()`
add_note = TotalDuration.add_note
get_video_title = TotalDuration.get_video_title
get_videos = TotalDuration.get_videos
UNIT = TotalDuration.UNIT
factor = TotalDuration.factor

# VideoCount: `video_count()`
get_videos = VideoCount.get_videos
# isort: on
### </django-deluxe-stubs>


def total_duration(minimum: timedelta) -> float:
    total = 0.0
    for video in get_videos():
        try:
            get_video_title(video.article_id)
        except Exception:
            continue
        if video.duration < minimum:
            add_note({'article_id': video.article_id, 'text': 'too short'})
            continue
        total += _seconds(video)
    print(f'total {total} {UNIT}')
    return total * factor


def _seconds(video: Video) -> float:
    return video.duration.total_seconds()


def video_count() -> int:
    return len(get_videos())
