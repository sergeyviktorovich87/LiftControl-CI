"""Read-only, same-origin release catalogue. The web process cannot publish or execute releases."""
import json
import os
import re
import hashlib
import logging
import stat
import struct
import zlib
import zipfile
from functools import lru_cache
from pathlib import Path
from django.http import FileResponse
from django.views.decorators.cache import never_cache
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from .access import MemberAccess, role
from .apk_metadata import read_apk

VERSION = '0.13.0'
logger = logging.getLogger('liftcontrol.updates')


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@lru_cache(maxsize=128)
def inspect_apk(path, expected):
    try:
        # Never follow links placed in the incoming directory.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode) or fingerprint(info) != expected:
                return None
            metadata = read_apk(file)
            file.seek(0)
            digest = hashlib.file_digest(file, 'sha256').hexdigest()
            if fingerprint(os.fstat(file.fileno())) != expected:
                return None
        return {**metadata, 'size': info.st_size, 'sha256': digest,
                'filename': f'LiftControl-Android-{metadata["version_code"]}.apk', 'notes': ''}
    except (OSError, ValueError, IndexError, UnicodeError, zipfile.BadZipFile, RuntimeError, EOFError, struct.error, zlib.error):
        logger.info('android_candidate_incomplete_or_invalid')
        return None


def folder_releases():
    root = Path(os.getenv('LIFTCONTROL_UPDATES_DIR', '/updates')).resolve()
    folder = root / 'android'
    if folder.is_symlink() or not folder.is_dir():
        return []
    result = []
    try:
        candidates = []
        for path in folder.iterdir():
            if path.suffix.lower() != '.apk':
                continue
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or not 1 <= info.st_size <= 200 * 1024**2:
                continue
            candidates.append((info.st_mtime_ns, path, info))
        # Bound parsing work; retain recent releases, not an unbounded file archive.
        for _, path, info in sorted(candidates, reverse=True)[:64]:
            item = inspect_apk(str(path), fingerprint(info))
            if item:
                result.append((dict(item), path, fingerprint(info)))
    except OSError:
        logger.warning('android_folder_unreadable')
    return result


def android_release(digest=None):
    candidates = folder_releases()
    try:
        legacy = release('android')
    except ValidationError:
        if not candidates:
            raise
        logger.warning('legacy_android_catalogue_invalid')
        legacy = None
    if legacy:
        path = Path(os.getenv('LIFTCONTROL_UPDATES_DIR', '/updates')).resolve() / legacy['filename']
        candidates.append((legacy, path, fingerprint(path.stat())))
    if digest is not None:
        candidates = [entry for entry in candidates if entry[0]['sha256'] == digest]
    return max(candidates, key=lambda entry: entry[0]['version_code'], default=None)


def release(kind):
    root = Path(os.getenv('LIFTCONTROL_UPDATES_DIR', '/updates')).resolve()
    try:
        manifest = json.loads((root / 'release.json').read_text())
        if not isinstance(manifest, dict):
            raise ValueError()
        item = manifest.get(kind)
        if not isinstance(item, dict):
            return None
        filename = item.get('filename', '')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,150}', filename) or '..' in filename:
            raise ValueError()
        path = root / filename
        if path.is_symlink() or path.resolve().parent != root or not path.is_file():
            raise ValueError()
        size = path.stat().st_size
        maximum = 200 * 1024**2 if kind == 'android' else 600 * 1024**2
        if not 1 <= size <= maximum or size != item['size']:
            raise ValueError()
        if not re.fullmatch(r'[a-f0-9]{64}', item.get('sha256', '')):
            raise ValueError()
        version = item['version']
        if not isinstance(version, str) or len(version) > 40:
            raise ValueError()
        if kind == 'android' and (type(item.get('version_code')) is not int or not 0 < item['version_code'] <= 2100000000):
            raise ValueError()
        return {'filename': filename, 'version': version, 'size': size,
                'sha256': item['sha256'], 'notes': str(item.get('notes', ''))[:4000],
                **({'version_code': item['version_code']} if kind == 'android' else {})}
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError):
        raise ValidationError('Файл обновления на сервере не прошёл проверку. Обратитесь к администратору.')


@never_cache
@api_view(['GET'])
@permission_classes([MemberAccess])
def catalogue(request):
    android = android_release()
    result = {'installed': {'panel': VERSION, 'api': VERSION, 'commit': os.getenv('BUILD_COMMIT', '')[:40]},
              'android': android[0] if android else None}
    if role(request.user) in ('ADMIN', 'DIRECTOR'):
        result['panel'] = release('panel')
    return Response(result)


@never_cache
@api_view(['GET'])
@permission_classes([MemberAccess])
def download_release(request, kind):
    if kind not in ('android', 'panel'):
        raise NotFound()
    if kind == 'panel' and role(request.user) not in ('ADMIN', 'DIRECTOR'):
        raise PermissionDenied()
    expected = None
    if kind == 'android':
        digest = request.query_params.get('sha256')
        if digest is not None and not re.fullmatch(r'[a-f0-9]{64}', digest):
            raise ValidationError('Неверный идентификатор обновления.')
        entry = android_release(digest)
        item, path, expected = entry if entry else (None, None, None)
    else:
        item = release(kind)
        path = Path(os.getenv('LIFTCONTROL_UPDATES_DIR', '/updates')).resolve() / item['filename'] if item else None
    if not item:
        raise NotFound('Обновление ещё не опубликовано.')
    try:
        file = os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb')
        if expected is not None and fingerprint(os.fstat(file.fileno())) != expected:
            file.close()
            raise NotFound('Файл обновляется. Повторите проверку.')
    except OSError:
        raise NotFound('Файл обновляется. Повторите проверку.')
    response = FileResponse(file, as_attachment=True, filename=item['filename'],
        content_type='application/vnd.android.package-archive' if kind == 'android' else 'application/zip')
    response['X-Content-SHA256'] = item['sha256']
    response['Cache-Control'] = 'private, no-store'
    return response
