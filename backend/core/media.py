"""Bounded private media, authenticated before opening files; uploads never become public URLs."""
import io
import json
import math
import re
import shutil
import subprocess
import tempfile
import warnings
from django.core.files.base import ContentFile
from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from rest_framework.exceptions import ValidationError

MAX_IMAGE = 10 * 1024**2
MAX_MEDIA = 20 * 1024**2


def normalize(upload, *, video=True):
    if not upload or not 0 < upload.size <= MAX_MEDIA:
        raise ValidationError('Фото — до 10 МБ, видео — до 20 МБ.')
    head = upload.read(4096)
    upload.seek(0)
    is_mp4 = len(head) >= 12 and head[4:8] == b'ftyp'
    is_webm = head.startswith(b'\x1aE\xdf\xa3') and b'webm' in head
    if is_mp4 or is_webm:
        if not video:
            raise ValidationError('В фотоотчёт можно добавить только фотографию.')
        probe = shutil.which('ffprobe')
        if not probe:
            raise ValidationError('На сервере ещё не установлена проверка видео. Обратитесь к администратору.')
        with tempfile.NamedTemporaryFile(suffix='.webm' if is_webm else '.mp4') as temp:
            size = 0
            for chunk in upload.chunks():
                size += len(chunk)
                if size > MAX_MEDIA:
                    raise ValidationError('Видео должно быть не больше 20 МБ.')
                temp.write(chunk)
            temp.flush()
            try:
                result = subprocess.run([probe, '-v', 'error', '-max_alloc', '67108864',
                    '-protocol_whitelist', 'file', '-format_whitelist', 'mov,matroska,webm',
                    '-probesize', '10485760', '-analyzeduration', '5000000',
                    '-show_entries', 'format=duration:stream=codec_type,codec_name,width,height',
                    '-of', 'json', temp.name], capture_output=True, timeout=10, check=True)
                data = json.loads(result.stdout)
                duration = float(data['format']['duration'])
                streams = data['streams']
                images = [s for s in streams if s.get('codec_type') == 'video']
                if not math.isfinite(duration) or not 0 < duration <= 180 or not images or len(streams) > 6:
                    raise ValueError()
                for stream in images:
                    if stream.get('codec_name') not in ('h264','hevc','vp8','vp9','av1') or not 0 < stream['width'] <= 4096 or not 0 < stream['height'] <= 4096:
                        raise ValueError()
            except (ValueError, KeyError, TypeError, subprocess.SubprocessError):
                raise ValidationError('Не удалось проверить видео. Поддерживаются MP4/MOV и WebM до 3 минут и 20 МБ.')
        upload.seek(0)
        mime = 'video/webm' if is_webm else ('video/quicktime' if b'qt  ' in head[8:32] else 'video/mp4')
        return upload, mime
    if upload.size > MAX_IMAGE:
        raise ValidationError('Фото должно быть не больше 10 МБ.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(upload) as original:
                if original.format not in ('JPEG','PNG','WEBP') or original.width * original.height > 20_000_000:
                    raise ValueError()
                image = ImageOps.exif_transpose(original).convert('RGB')
                image.thumbnail((4096,4096))
                output = io.BytesIO()
                image.save(output, format='JPEG', quality=90)
        return ContentFile(output.getvalue(), name='photo.jpg'), 'image/jpeg'
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise ValidationError('Выберите настоящую фотографию JPEG, PNG или WebP до 20 мегапикселей.')


def attachment_data(row, parent, target):
    return {'id':row.pk, 'content_type':row.content_type, 'size':row.size,
            'url':f'/api/{target}/{parent}/attachments/{row.pk}/?organization={row.organization_id}',
            'created_at':row.created_at,'asset_id':str(row.asset_id) if row.asset_id else None,
            'name':row.asset.original_name if row.asset_id else '',
            'preview_url':f'/api/media/{row.asset_id}/preview/?organization={row.organization_id}' if row.asset_id and row.asset.preview else None}


def serve(row, request):
    if getattr(row,'asset_id',None):
        row = row.asset
    size = row.size
    ranged = request.headers.get('Range')
    if ranged:
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', ranged)
        try:
            if not match or not any(match.groups()):
                raise ValueError()
            start, end = match.groups()
            if not start:
                length = int(end)
                if length <= 0:
                    raise ValueError()
                start, end = max(0,size-length), size-1
            else:
                start, end = int(start), min(int(end),size-1) if end else size-1
            if not 0 <= start <= end < size:
                raise ValueError()
        except ValueError:
            response = HttpResponse(status=416)
            response['Content-Range'] = f'bytes */{size}'
            response['Cache-Control'] = 'private, no-store'
            return response
        def chunks():
            with row.file.open('rb') as source:
                source.seek(start)
                remaining = end-start+1
                while remaining:
                    chunk = source.read(min(65536,remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk
        response = StreamingHttpResponse(chunks(), status=206, content_type=row.content_type)
        response['Content-Range'] = f'bytes {start}-{end}/{size}'
        response['Content-Length'] = str(end-start+1)
    else:
        response = FileResponse(row.file.open('rb'), content_type=row.content_type)
    response['Accept-Ranges'] = 'bytes'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'private, no-store'
    response['Content-Security-Policy'] = "default-src 'none'; sandbox"
    return response
