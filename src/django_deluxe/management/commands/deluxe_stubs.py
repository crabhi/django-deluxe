from django.core.management import BaseCommand, CommandError

from django_deluxe.sandbox import find_sandboxed_modules
from django_deluxe.stubs import update_stub_block


class Command(BaseCommand):
    help = (
        'Write the stub block — the names the host binds, for the IDE and the linters — into every generated module '
        'of the installed apps.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--check', action='store_true',
                            help='write nothing, fail if a stub block is missing or out of date')

    def handle(self, *args, **options):
        stale_paths = []
        for sandboxed_module in find_sandboxed_modules():
            path = sandboxed_module.generated_path
            if not sandboxed_module.definitions:
                raise CommandError(f'{path}: no @sandboxed class in {sandboxed_module.name}')
            source = path.read_text(encoding='utf-8')
            updated_source = update_stub_block(source, sandboxed_module.stub_block)
            if updated_source == source:
                continue
            stale_paths.append(path)
            if not options['check']:
                path.write_text(updated_source, encoding='utf-8')
                self.stdout.write(f'Updated {path}')
        if options['check'] and stale_paths:
            raise CommandError('Stub blocks out of date: ' + ', '.join(map(str, stale_paths)))
