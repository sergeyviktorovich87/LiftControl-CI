"""Release push: safe discovery, idempotent queue, revocation and metadata-only FCM."""
import os
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .app_update_push import announce_latest, send_one
from .models import (
    AndroidUpdateAnnouncement, AndroidUpdatePushDelivery,
    Membership, MobileSession, NativePushDevice, Organization,
)
from .tests_apk_updates import apk


class AndroidUpdatePushTests(TestCase):
    def setUp(self):
        org, _ = Organization.objects.get_or_create(pk=1, defaults={'name': 'Тестовая организация'})
        self.user = get_user_model().objects.create_user('update-user', password='test-password')
        Membership.objects.create(user=self.user, organization=org, role='ADMIN')
        self.session = MobileSession.objects.create(
            user=self.user, token_hash='a' * 64,
            password_stamp=self.user.get_session_auth_hash(),
            device_name='Тестовый Android',
            expires_at=timezone.now() + timedelta(days=7),
        )
        self.device = NativePushDevice.objects.create(
            session=self.session, token='F' * 100, active=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'android').mkdir()
        self.env = patch.dict(os.environ, LIFTCONTROL_UPDATES_DIR=str(self.root))
        self.env.start()
        self.addCleanup(self.env.stop)

    def write_apk(self, code=1201, version='0.12.1'):
        (self.root / 'android' / f'build-{code}.apk').write_bytes(apk(code, version=version))

    def test_announces_only_new_verified_build_once(self):
        self.write_apk()
        self.assertEqual(announce_latest(), 1)
        self.assertEqual(announce_latest(), 0)
        self.assertEqual(AndroidUpdateAnnouncement.objects.count(), 1)
        self.assertEqual(AndroidUpdatePushDelivery.objects.count(), 1)
        first = AndroidUpdatePushDelivery.objects.get()
        self.assertEqual((first.announcement.version, first.announcement.version_code),
                         ('0.12.1', 1201))
        self.write_apk(code=1202, version='0.12.2')
        self.assertEqual(announce_latest(), 1)
        self.assertEqual(AndroidUpdatePushDelivery.objects.count(), 2)

    def test_incomplete_apk_does_not_announce(self):
        (self.root / 'android' / 'broken.apk').write_bytes(b'not an apk')
        self.assertEqual(announce_latest(), 0)
        self.assertFalse(AndroidUpdateAnnouncement.objects.exists())

    def test_revoked_session_suppresses_release_push(self):
        self.write_apk()
        announce_latest()
        self.session.revoked_at = timezone.now()
        self.session.save(update_fields=['revoked_at'])
        delivery = AndroidUpdatePushDelivery.objects.select_related(
            'device__session__user', 'announcement').get()
        with patch('requests.post') as send:
            self.assertEqual(send_one(delivery), 'DISABLED')
            send.assert_not_called()

    def test_fcm_payload_contains_only_public_update_metadata(self):
        self.write_apk()
        announce_latest()
        delivery = AndroidUpdatePushDelivery.objects.select_related(
            'device__session__user', 'announcement').get()
        with patch('core.native_push.client_config', return_value={'projectId': 'lift-test'}), \
             patch('core.native_push.access_token', return_value='secret-token'), \
             patch('requests.post') as post:
            post.return_value.status_code = 200
            self.assertEqual(send_one(delivery), 'SENT')
            data = post.call_args.kwargs['json']['message']['data']
        self.assertEqual(data, {
            'target': 'app_update', 'version': '0.12.1', 'version_code': '1201'
        })
        self.assertNotIn('token', data)
        self.assertNotIn('organization', data)
        self.assertNotIn('address', data)

    def test_former_release_is_not_sent_after_newer_publication(self):
        self.write_apk()
        announce_latest()
        old = AndroidUpdatePushDelivery.objects.select_related(
            'device__session__user', 'announcement').get()
        self.write_apk(code=1202, version='0.12.2')
        announce_latest()
        with patch('requests.post') as post:
            self.assertEqual(send_one(old), 'SUPERSEDED')
            post.assert_not_called()
