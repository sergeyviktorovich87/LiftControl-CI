import hashlib
import io
import os
import struct
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from .apk_metadata import ANDROID, manifest_metadata
from .models import Membership


def apk(code, version='0.5.0', package='ru.liftcontrol.app.beta', utf8=True):
    strings = ['manifest', 'package', package, ANDROID, 'versionCode', 'versionName', version]
    data = bytearray(); offsets = []
    for value in strings:
        offsets.append(len(data))
        encoded = value.encode('utf-8' if utf8 else 'utf-16-le')
        data.extend(bytes([len(value), len(encoded)]) + encoded + b'\0' if utf8 else struct.pack('<H', len(value)) + encoded + b'\0\0')
    start = 28 + len(strings) * 4
    data += b'\0' * ((-(start + len(data))) % 4)
    pool = struct.pack('<HHIIIIII', 1, 28, start + len(data), len(strings), 0, 0x100 if utf8 else 0, start, 0)
    pool += struct.pack('<' + 'I' * len(offsets), *offsets) + data
    attributes = b''.join(struct.pack('<IIIHBBI', ns, key, 0xffffffff, 8, 0, kind, value) for ns, key, kind, value in [
        (0xffffffff, 1, 3, 2), (3, 4, 0x10, code), (3, 5, 3, 6)])
    node = struct.pack('<HHIII', 0x102, 16, 36 + len(attributes), 1, 0xffffffff)
    node += struct.pack('<IIHHHHHH', 0xffffffff, 0, 20, 20, 3, 0, 0, 0) + attributes
    xml = struct.pack('<HHI', 3, 8, 8 + len(pool) + len(node)) + pool + node
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('AndroidManifest.xml', xml)
        archive.writestr('lib/arm64-v8a/libapp.so', b'fixture')
    return buffer.getvalue()


class APKFolderTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user('apk-owner')
        Membership.objects.create(user=user, organization_id=1, role='ADMIN')
        self.client = APIClient(); self.client.force_authenticate(user)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.folder = self.root / 'android'; self.folder.mkdir()
        self.env = patch.dict(os.environ, LIFTCONTROL_UPDATES_DIR=str(self.root)); self.env.start()
        self.addCleanup(self.env.stop)

    def catalogue(self):
        response = self.client.get('/api/updates/')
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()['android']

    def test_plain_apk_is_discovered_without_json_and_highest_version_wins(self):
        self.assertIsNone(self.catalogue())
        (self.folder / 'любое имя.apk').write_bytes(apk(2031))
        (self.folder / 'looks-newer.apk').write_bytes(apk(2020))
        result = self.catalogue()
        self.assertEqual((result['version_code'], result['version']), (2031, '0.5.0'))
        self.assertFalse((self.root / 'release.json').exists())
        self.assertEqual(self.client.get('/api/updates/')['Cache-Control'], 'max-age=0, no-cache, no-store, must-revalidate, private')

    def test_partial_foreign_and_symlink_apks_do_not_hide_a_valid_release(self):
        (self.folder / 'valid.apk').write_bytes(apk(2031))
        (self.folder / 'uploading.apk').write_bytes(b'PK partial transfer')
        (self.folder / 'next.apk.part').write_bytes(apk(9000))
        (self.folder / 'foreign.apk').write_bytes(apk(9000, package='com.other.app'))
        external = self.root / 'elsewhere.apk'; external.write_bytes(apk(9001))
        (self.folder / 'link.apk').symlink_to(external)
        self.assertEqual(self.catalogue()['version_code'], 2031)

    def test_replaced_file_is_reparsed_and_download_is_pinned_to_digest(self):
        first = apk(2031); latest = apk(2032)
        path = self.folder / 'release.apk'; path.write_bytes(first)
        digest = self.catalogue()['sha256']
        (self.folder / 'old.apk').write_bytes(first)
        path.write_bytes(latest)
        self.assertEqual(self.catalogue()['version_code'], 2032)
        response = self.client.get('/api/updates/android/download/?sha256=' + digest)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), first)
        self.assertEqual(response['X-Content-SHA256'], hashlib.sha256(first).hexdigest())
        (self.folder / 'old.apk').unlink()
        self.assertEqual(self.client.get('/api/updates/android/download/?sha256=' + digest).status_code, 404)

    def test_incomplete_file_becomes_available_after_transfer_finishes(self):
        path = self.folder / 'release.apk'; path.write_bytes(b'incomplete')
        self.assertIsNone(self.catalogue())
        path.write_bytes(apk(2033, version='0.6.0', utf8=False))
        self.assertEqual(self.catalogue()['version'], '0.6.0')
        path.unlink()
        self.assertIsNone(self.catalogue())

    def test_directory_symlink_and_unauthenticated_download_are_rejected(self):
        self.folder.rmdir(); self.folder.symlink_to(self.root, target_is_directory=True)
        (self.root / 'release.apk').write_bytes(apk(9000))
        self.assertIsNone(self.catalogue())
        self.client.force_authenticate(None)
        self.assertIn(self.client.get('/api/updates/android/download/').status_code, (401, 403))

    def test_corrupt_binary_xml_is_rejected(self):
        for data in (b'', b'\x03\x00\x08\x00' + b'\xff' * 16, b'<manifest/>'):
            with self.assertRaises((ValueError, struct.error)):
                manifest_metadata(data)
