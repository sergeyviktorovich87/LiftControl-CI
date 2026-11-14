"""Small PostgreSQL-backed WebPush worker; run as an isolated container, never as an API thread."""
import time
from django.core.management.base import BaseCommand
from core.push_notifications import process_batch, push_config
from core.native_push import enabled, process_native_batch
from core.task_push import process_task_batch
from core.app_update_push import announce_latest, process_batch as process_update_batch


class Command(BaseCommand):
    help = 'Deliver pending LiftControl push notifications from durable queue.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--interval', type=int, default=5)

    def handle(self, *args, **options):
        if not push_config()['enabled'] and not enabled():
            self.stdout.write('WEBPUSH_DISABLED: VAPID key and subject are not configured')
            return
        interval = max(2, min(60, options['interval']))
        last_release_scan = None
        self.stdout.write('LIFTCONTROL_PUSH_WORKER_STARTED')
        while True:
            try:
                now = time.monotonic()
                if enabled() and (last_release_scan is None or now - last_release_scan >= 60):
                    last_release_scan = now
                    announce_latest()
                process_batch()
                process_native_batch()
                process_task_batch()
                process_update_batch()
            except Exception as exc:
                # No raw exception text, because push endpoints are secret capability URLs.
                self.stderr.write('PUSH_WORKER_ERROR: ' + type(exc).__name__)
            if options['once']:
                break
            time.sleep(interval)
