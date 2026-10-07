import inspect

from django.core.management import call_command

from django_deluxe.sandbox import get_definition
from django_deluxe.stubs import strip_stub_block, update_stub_block
from tests.sample_app.billing import BillUsage
from tests.sample_app.tasks import TotalDuration


STUB_BLOCK = '### <django-deluxe-stubs>\nNAME: str\n### </django-deluxe-stubs>\n'


def test_stub_block_imports_the_classes_and_binds_the_names_from_an_instance():
    assert get_definition(BillUsage).module.stub_block == inspect.cleandoc('''
        ### <django-deluxe-stubs>
        # For the IDE and the linters only: the sandbox strips this block and binds these names itself.
        # A pydantic model or a dataclass crosses as a dict of its JSON form, not as an instance: where a name below says
        # "dict", the class is only there to look the dict's shape up in.
        # isort: off
        from tests.sample_app.billing import BillUsage
        from tests.sample_app.billing import Cost  # a dict in the sandbox
        from tests.sample_app.billing import Delivery
        from tests.sample_app.billing import Usage  # a dict in the sandbox

        # BillUsage: `bill_usage()`
        get_cost = BillUsage.get_cost  # dicts in the sandbox: Cost, Usage
        get_usage = BillUsage.get_usage  # dicts in the sandbox: Usage
        record = BillUsage.record  # dicts in the sandbox: Usage
        # isort: on
        ### </django-deluxe-stubs>
    ''') + '\n'


def test_every_class_binds_its_names_in_a_section_of_its_own():
    stub_block = get_definition(TotalDuration).module.stub_block

    assert '\n# TotalDuration: `total_duration()`\nadd_note = TotalDuration.add_note\n' in stub_block
    assert '\n# VideoCount: `video_count()`\nget_videos = VideoCount.get_videos\n' in stub_block
    assert 'get_videos = TotalDuration.get_videos\n' in stub_block


def test_block_goes_after_the_docstring_and_imports():
    source = '"""Doc."""\n\nimport json\nfrom datetime import timedelta\n\n\ndef f() -> int:\n    return 1\n'

    assert update_stub_block(source, STUB_BLOCK) == (
        '"""Doc."""\n\nimport json\nfrom datetime import timedelta\n\n'
        + STUB_BLOCK
        + '\n\ndef f() -> int:\n    return 1\n'
    )


def test_block_goes_first_without_imports():
    assert update_stub_block('def f() -> int:\n    return 1\n', STUB_BLOCK) == (
        STUB_BLOCK + '\n\ndef f() -> int:\n    return 1\n'
    )


def test_block_is_replaced_and_updating_is_idempotent():
    source = update_stub_block('import json\n\n\nx = 1\n', STUB_BLOCK)
    new_block = STUB_BLOCK.replace('NAME: str', 'OTHER: int')

    updated = update_stub_block(source, new_block)

    assert 'OTHER: int' in updated
    assert 'NAME: str' not in updated
    assert update_stub_block(updated, new_block) == updated


def test_monty_gets_blank_lines_in_place_of_the_block():
    source = update_stub_block('import json\n\n\nx = 1\n', STUB_BLOCK)

    stripped = strip_stub_block(source)

    assert 'NAME' not in stripped
    assert stripped.count('\n') == source.count('\n')


def test_sample_app_blocks_are_up_to_date():
    call_command('deluxe_stubs', '--check')
