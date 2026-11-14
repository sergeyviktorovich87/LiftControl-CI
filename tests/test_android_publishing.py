import concurrent.futures
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def module(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'deploy/api'/f'{name}.py')
    value=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


publisher=module('publish-update')
packager=module('package-android-update')


class AndroidPublishingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.output=self.root/'updates'

    def source(self,code=2,body=b'verified test build',name='LiftControl.apk'):
        folder=self.root/f'build-{code}-{hashlib.sha256(body).hexdigest()[:8]}'
        folder.mkdir(exist_ok=True)
        (folder/name).write_bytes(body)
        item={'filename':name,'version':'0.4.0-beta','version_code':code,'size':len(body),'sha256':hashlib.sha256(body).hexdigest(),'notes':'Test'}
        (folder/'android-release.json').write_text(json.dumps({'android':item}))
        return folder

    def test_published_build_is_readable_and_retains_panel_release(self):
        self.output.mkdir()
        panel={'version':'0.9','filename':'panel.zip'}
        (self.output/'release.json').write_text(json.dumps({'panel':panel}))
        item=publisher.publish_android(self.source(),self.output)
        catalogue=json.loads((self.output/'release.json').read_text())
        self.assertEqual(catalogue['panel'],panel)
        self.assertEqual(catalogue['android'],item)
        self.assertEqual(hashlib.sha256((self.output/item['filename']).read_bytes()).hexdigest(),item['sha256'])
        self.assertEqual((self.output/item['filename']).stat().st_mode&0o777,0o644)

    def test_a_damaged_upload_keeps_the_previous_publication(self):
        folder=self.source()
        original=publisher.publish_android(folder,self.output)
        (folder/'LiftControl.apk').write_bytes(b'x'*len(b'verified test build'))
        with self.assertRaisesRegex(ValueError,'сумма'): publisher.publish_android(folder,self.output)
        self.assertEqual(json.loads((self.output/'release.json').read_text())['android'],original)

    def test_version_cannot_decrease_or_reuse_a_number_for_different_bytes(self):
        original=publisher.publish_android(self.source(3),self.output)
        for source in (self.source(2),self.source(3,b'other build')):
            with self.assertRaises(ValueError): publisher.publish_android(source,self.output)
        self.assertEqual(json.loads((self.output/'release.json').read_text())['android'],original)
        self.assertEqual(publisher.publish_android(self.source(3),self.output),original)

    def test_racing_publications_keep_the_highest_build(self):
        sources=[self.source(code) for code in range(2,7)]
        def publish(source):
            try: publisher.publish_android(source,self.output)
            except ValueError: pass  # A lower build may arrive after a higher one.
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(publish,sources))
        self.assertEqual(json.loads((self.output/'release.json').read_text())['android']['version_code'],6)

    def test_metadata_cannot_escape_its_directory(self):
        source=self.source()
        value=json.loads((source/'android-release.json').read_text())
        value['android']['filename']='../outside.apk'
        (source/'android-release.json').write_text(json.dumps(value))
        with self.assertRaises(ValueError): publisher.publish_android(source,self.output)
        self.assertFalse((self.output/'release.json').exists())

    def test_distribution_kit_has_apk_metadata_launcher_and_valid_hashes(self):
        source=self.source()
        release=self.root/'release.zip'
        with zipfile.ZipFile(release,'w') as archive:
            for path in source.iterdir(): archive.write(path,path.name)
            archive.writestr('../../untrusted-script.sh','never copy this')
        output=self.root/'LiftControl-Android-update.zip'
        packager.package(release,output)
        with zipfile.ZipFile(output) as archive:
            self.assertIsNone(archive.testzip())
            base='LiftControl-Android-update/'
            self.assertIn(base+'publish-android.sh',archive.namelist())
            self.assertFalse(any('..' in name for name in archive.namelist()))
            for line in archive.read(base+'PACKAGE.sha256').decode().splitlines():
                expected,name=line.split('  ',1)
                self.assertEqual(hashlib.sha256(archive.read(base+name)).hexdigest(),expected)
            self.assertEqual(archive.read(base+'LiftControl.apk'),b'verified test build')


class AndroidWorkflowSecurityTests(unittest.TestCase):
    def test_feature_branch_checks_do_not_request_signing_secret(self):
        ci=(ROOT/'.github/workflows/android.yml').read_text()
        self.assertIn('CI-only debug APK',ci)
        self.assertNotIn('secrets.LIFTCONTROL_ANDROID_KEYSTORE_BASE64',ci)
        self.assertNotIn('actions/cache@v4',ci)
        self.assertNotIn('flutter build apk --release',ci)

    def test_signed_release_requires_protected_main_and_preserved_signer(self):
        release=(ROOT/'.github/workflows/android-sign-release.yml').read_text()
        self.assertIn('workflow_dispatch:',release)
        self.assertIn("github.ref == 'refs/heads/main'",release)
        self.assertIn('environment: android-signing',release)
        self.assertIn('secrets.LIFTCONTROL_ANDROID_KEYSTORE_BASE64',release)
        self.assertIn('7692c3fedf4b66ac2673a29326c8fb7fbc73db91af1dcee889dacbe008e32683',release)
        self.assertIn('code>2061',release)
        self.assertNotIn('keytool -genkeypair',release)


    def test_api_web_and_flutter_have_one_release_version(self):
        import re
        import json
        version='0.13.0'
        backend=(ROOT/'backend/core/updates.py').read_text()
        web=(ROOT/'web/src/App.jsx').read_text()
        mobile=(ROOT/'mobile/pubspec.yaml').read_text()
        workflow=(ROOT/'.github/workflows/android-sign-release.yml').read_text()
        package=json.loads((ROOT/'web/package.json').read_text())
        lock=json.loads((ROOT/'web/package-lock.json').read_text())
        self.assertRegex(backend,r"VERSION = ['\"]"+re.escape(version)+r"['\"]")
        self.assertIn("Web "+version,web)
        self.assertEqual(package['version'],version)
        self.assertEqual(lock['version'],version)
        self.assertEqual(lock['packages']['']['version'],version)
        self.assertIn("version: "+version+"+3100",mobile)
        self.assertIn("--build-name="+version,workflow)
        self.assertIn("0.13.0-beta",workflow)
        self.assertIn("code>2061",workflow)

if __name__=='__main__': unittest.main()
