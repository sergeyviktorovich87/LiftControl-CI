import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

def module(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'deploy/offline'/f'{name}.py')
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value

copier=module('copy-media')
backup=module('backup-media')

class MediaDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'old';self.target=self.root/'new'
        self.source.mkdir();self.target.mkdir()
        (self.source/'private').mkdir();(self.source/'private/photo.png').write_bytes(b'original photo')

    def test_copy_is_verified_resumable_and_does_not_remove_old_paths(self):
        self.assertEqual(copier.copy(self.source,self.target),1)
        self.assertEqual(copier.copy(self.source,self.target),0)
        self.assertEqual((self.source/'private/photo.png').read_bytes(),b'original photo')
        self.assertEqual((self.target/'private/photo.png').read_bytes(),b'original photo')

    def test_conflicting_destination_is_never_overwritten(self):
        copier.copy(self.source,self.target)
        (self.target/'private/photo.png').write_bytes(b'other bytes')
        with self.assertRaisesRegex(ValueError,'CONFLICT'):copier.copy(self.source,self.target)
        self.assertEqual((self.target/'private/photo.png').read_bytes(),b'other bytes')

    def test_symlink_cannot_escape_the_destination(self):
        outside=self.root/'outside';outside.mkdir()
        (self.target/'private').symlink_to(outside,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'SYMLINK'):copier.copy(self.source,self.target)
        self.assertFalse((outside/'photo.png').exists())

    def test_database_and_media_archive_is_verified_and_can_be_restored(self):
        database=self.root/'database.dump';database.write_bytes(b'PGDMP fixture')
        archive=self.root/'backup.tar.gz';backup.archive_files(database,self.source,archive)
        self.assertEqual(backup.verify(archive),2)
        with tarfile.open(archive) as data:
            self.assertEqual(data.extractfile('database.dump').read(),database.read_bytes())
            self.assertEqual(data.extractfile('media/private/photo.png').read(),b'original photo')
            manifest=json.load(data.extractfile('manifest.json'))
            self.assertEqual(manifest['media/private/photo.png']['sha256'],hashlib.sha256(b'original photo').hexdigest())

    def test_tampered_archive_is_rejected(self):
        archive=self.root/'tampered.tar.gz'
        with tarfile.open(archive,'w:gz') as data:
            for name,raw in [('database.dump',b'bad'),('manifest.json',json.dumps({'database.dump':{'size':3,'sha256':'0'*64}}).encode())]:
                row=tarfile.TarInfo(name);row.size=len(raw);data.addfile(row,io.BytesIO(raw))
        with self.assertRaisesRegex(ValueError,'BACKUP_HASH'):backup.verify(archive)

if __name__=='__main__':unittest.main()
