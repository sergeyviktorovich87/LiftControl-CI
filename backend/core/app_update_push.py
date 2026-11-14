"""Announce a verified APK exactly once; FCM is a notification, not an installer.

The watcher runs only in the isolated native push worker with a read-only APK
mount. It never exposes Firebase credentials, release files or device tokens to
the Android client. The app still checks the authenticated update catalogue.
"""
import hashlib
import logging
import os
import re
import stat
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from .access import memberships
from .apk_metadata import read_apk
from .models import AndroidUpdateAnnouncement, AndroidUpdatePushDelivery, NativePushDevice
from . import native_push
from .updates import android_release

logger = logging.getLogger('liftcontrol.update_push')


def announce_latest():
    """Discover folder and published releases without a manual send button.

    A release announcement is written only after the APK metadata and SHA-256
    match the API catalogue. Repeated polls and multiple workers are safe.
    """
    entry = android_release()
    if entry is None:
        return 0
    item, path, _ = entry
    code, version, digest = item['version_code'], item['version'], item['sha256']
    if (type(code) is not int or code <= 0
            or not isinstance(version, str) or not 0 < len(version) <= 40
            or not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest)):
        logger.warning('android_announcement_invalid_metadata')
        return 0
    current = AndroidUpdateAnnouncement.objects.filter(version_code=code).first()
    if current:
        if current.sha256 != digest:
            logger.error('android_announcement_version_conflict')
        return 0
    if AndroidUpdateAnnouncement.objects.filter(version_code__gt=code).exists():
        return 0

    # Validate only a new release. Do not hash a large APK on every worker poll.
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as file:
            before = os.fstat(file.fileno())
            if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= 200 * 1024**2:
                return 0
            actual = read_apk(file)
            file.seek(0)
            actual_digest = hashlib.file_digest(file, 'sha256').hexdigest()
            after = os.fstat(file.fileno())
        if (actual != {'version': version, 'version_code': code}
                or actual_digest != digest or
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            logger.warning('android_announcement_verification_failed')
            return 0
    except (OSError, ValueError, RuntimeError):
        logger.warning('android_announcement_apk_unavailable')
        return 0

    with transaction.atomic():
        announcement, created = AndroidUpdateAnnouncement.objects.get_or_create(
            version_code=code, defaults={'version': version, 'sha256': digest})
        if not created:
            if announcement.sha256 != digest:
                logger.error('android_announcement_version_conflict')
            return 0
        now = timezone.now()
        device_ids = list(NativePushDevice.objects.filter(
            active=True, session__revoked_at__isnull=True,
            session__expires_at__gt=now, session__user__is_active=True,
            session__user__memberships__active=True,
        ).distinct().values_list('pk', flat=True))
        AndroidUpdatePushDelivery.objects.bulk_create(
            [AndroidUpdatePushDelivery(announcement=announcement, device_id=device_id)
             for device_id in device_ids],
            ignore_conflicts=True, batch_size=500)
    logger.info('android_release_announcement_queued: build=%s devices=%s', code, len(device_ids))
    return len(device_ids)


def valid_recipient(delivery):
    device = delivery.device
    session = device.session
    return (
        device.active and session.revoked_at is None
        and session.expires_at > timezone.now()
        and session.user.is_active
        and constant_time_compare(session.password_stamp, session.user.get_session_auth_hash())
        and memberships(session.user).exists()
    )


def send_one(delivery):
    import requests

    if not valid_recipient(delivery):
        return 'DISABLED'
    announcement = delivery.announcement
    if AndroidUpdateAnnouncement.objects.filter(version_code__gt=announcement.version_code).exists():
        return 'SUPERSEDED'
    config = native_push.client_config()
    if config is None:
        return 'RETRY'
    message = {
        'token': delivery.device.token,
        'data': {
            'target': 'app_update',
            'version': announcement.version,
            'version_code': str(announcement.version_code),
        },
        'android': {'priority': 'NORMAL', 'ttl': '86400s'},
    }
    response = requests.post(
        f"https://fcm.googleapis.com/v1/projects/{config['projectId']}/messages:send",
        headers={'Authorization': 'Bearer ' + native_push.access_token()},
        json={'message': message}, timeout=8, allow_redirects=False,
    )
    if response.status_code == 200:
        return 'SENT'
    if response.status_code == 404:
        NativePushDevice.objects.filter(pk=delivery.device_id).update(active=False)
        return 'DISABLED'
    if response.status_code in (401, 403):
        native_push._credential_cache.clear()
    logger.warning('android_release_push_http_%s', response.status_code)
    return 'RETRY'


def process_batch(limit=20):
    if not native_push.enabled() or not os.path.isfile(os.getenv('FCM_SERVICE_ACCOUNT_FILE', '')):
        return 0
    now = timezone.now()
    with transaction.atomic():
        rows = list(AndroidUpdatePushDelivery.objects.select_for_update(skip_locked=True)
                    .select_related('device__session__user', 'announcement')
                    .filter(Q(state='PENDING') | Q(state='PROCESSING'),
                            next_attempt_at__lte=now)
                    .order_by('pk')[:limit])
        for row in rows:
            row.state, row.attempts = 'PROCESSING', row.attempts + 1
            row.next_attempt_at = now + timedelta(minutes=2)
        AndroidUpdatePushDelivery.objects.bulk_update(
            rows, ['state', 'attempts', 'next_attempt_at'])
    for row in rows:
        try:
            result = send_one(row)
        except Exception as exc:
            logger.warning('android_release_push_failed: %s', type(exc).__name__)
            result = 'RETRY'
        row.state = 'PENDING' if result == 'RETRY' and row.attempts < 4 else (
            'FAILED' if result == 'RETRY' else result)
        row.next_attempt_at = timezone.now() + timedelta(seconds=20 * 2 ** row.attempts)
        row.sent_at = timezone.now() if result == 'SENT' else None
        row.save(update_fields=['state', 'next_attempt_at', 'sent_at'])
    return len(rows)
