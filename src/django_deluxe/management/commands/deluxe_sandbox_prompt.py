import importlib

from django.core.management import BaseCommand

from django_deluxe.sandbox import SandboxedModule


class Command(BaseCommand):
    help = 'Print what the generated module of a module with `@sandboxed` classes is generated from, and where it goes.'

    def add_arguments(self, parser):
        parser.add_argument('module', help='dotted path, e.g. myapp.tasks')

    def handle(self, *args, **options):
        importlib.import_module(options['module'])
        self.stdout.write(SandboxedModule.of(options['module']).get_prompt())
