from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from mailboxes.campaigns import run_batch


class Command(BaseCommand):
    help = 'Deliver a bounded batch of queued Kappa seminar emails'

    def handle(self, *args, **options):
        import fcntl

        if not settings.MANAGED_KAPPA:
            raise CommandError('This worker is only used by the managed Kappa portal')
        with (settings.DATA_DIR / 'seminar-worker.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            run_batch()
