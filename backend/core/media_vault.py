"""Private immutable originals, organization-local deduplication and audited links."""
import hashlib
import io
import logging
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import PurePath
from zoneinfo import ZoneInfo
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageOps
from rest_framework.exceptions import PermissionDenied, ValidationError
from .access import org, manager, technician_id, audit
from .models import MediaAsset, MediaLink, Organization, Ticket, Elevator, PersonalTask, TicketPhoto, PersonalTaskAttachment
from .media import normalize

logger = logging.getLogger('liftcontrol.media')
_written = ContextVar('media_written', default=None)


@contextmanager
def atomic_media():
    parent = _written.get()
    if parent is not None:
        with transaction.atomic():
            yield
        return
    paths = []
    token = _written.set(paths)
    try:
        with transaction.atomic():
            yield
    except BaseException:
        # Each path belongs to a newly created UUID, never to a reused object.
        for path in reversed(paths):
            try:
                from django.db.models import Q
                # A lost COMMIT acknowledgement must never delete a committed
                # original. If the database is unavailable, retain the file.
                if MediaAsset.objects.filter(Q(file=path)|Q(preview=path)).exists():
                    continue
                default_storage.delete(path)
            except Exception:
                logger.warning('media_rollback_cleanup_failed')
        raise
    finally:
        _written.reset(token)


def ingest(user, upload, *, video=True, organization=None, created_at=None, validate=True):
    if _written.get() is None:
        raise RuntimeError('Use atomic_media for filesystem compensation')
    organization = organization or org(user)
    name = re.sub(r'[\x00-\x1f\x7f]', '', PurePath(str(upload.name).replace('\\','/')).name)[:180] or 'media'
    if validate:
        _, mime = normalize(upload, video=video)
    else:
        mime = getattr(upload, 'content_type', 'image/jpeg')
    upload.seek(0)
    raw = upload.read(20 * 1024**2 + 1)
    if not 0 < len(raw) <= 20 * 1024**2:
        raise ValidationError('Файл пуст или превышает 20 МБ.')
    preview, captured = None, None
    if not mime.startswith('video/'):
        with Image.open(io.BytesIO(raw)) as image:
            mime = {'JPEG':'image/jpeg','PNG':'image/png','WEBP':'image/webp'}[image.format]
            try:
                value = image.getexif().get(36867) or image.getexif().get(306)
                if value:
                    captured = datetime.strptime(str(value), '%Y:%m:%d %H:%M:%S').replace(tzinfo=ZoneInfo(organization.timezone))
            except (ValueError, TypeError):
                pass
            picture = ImageOps.exif_transpose(image).convert('RGB')
            picture.thumbnail((480,480))
            buffer = io.BytesIO(); picture.save(buffer,format='WEBP',quality=75)
            preview = buffer.getvalue()
    elif shutil.which('ffmpeg'):
        # One bounded frame; the original remains untouched and playable with
        # authenticated range requests. Failure does not discard the video.
        with tempfile.NamedTemporaryFile(suffix='.webm' if mime=='video/webm' else '.mp4') as temp:
            temp.write(raw);temp.flush()
            try:
                frame=subprocess.run([shutil.which('ffmpeg'),'-v','error','-nostdin','-threads','1','-filter_threads','1',
                    '-max_alloc','67108864','-protocol_whitelist','file,pipe','-i',temp.name,'-an','-frames:v','1',
                    '-vf','scale=480:480:force_original_aspect_ratio=decrease','-c:v','libwebp','-f','image2pipe','pipe:1'],
                    capture_output=True,timeout=10,check=True).stdout
                if 0<len(frame)<=1024*1024:preview=frame
            except (OSError,subprocess.SubprocessError):
                logger.warning('media_video_preview_unavailable')
    digest = hashlib.sha256(raw).hexdigest()
    # Serialize publication of equal bytes across users and API workers.
    Organization.objects.select_for_update(no_key=True).get(pk=organization.pk)
    asset = MediaAsset.objects.filter(organization=organization,sha256=digest).first()
    if asset:
        logger.info('media_reused')
        return asset
    asset = MediaAsset(organization=organization,uploaded_by=user,original_name=name,
        content_type=mime,size=len(raw),sha256=digest,captured_at=captured,created_at=created_at or timezone.now())
    extension = {'image/jpeg':'jpg','image/png':'png','image/webp':'webp','video/mp4':'mp4','video/quicktime':'mov','video/webm':'webm'}[mime]
    asset.file.save('original.'+extension,ContentFile(raw),save=False)
    _written.get().append(asset.file.name)
    if preview:
        asset.preview_size = len(preview)
        asset.preview.save('preview.webp',ContentFile(preview),save=False)
        _written.get().append(asset.preview.name)
    asset.save()
    logger.info('media_original_saved')
    return asset


def may_link(user, kind, target):
    if target.organization_id != org(user).pk:
        raise PermissionDenied('Нет доступа к этой организации.')
    from . import ticket_flow
    if kind == 'ticket':
        allowed = manager(user) or ticket_flow.can_finish_ticket(user,target)
    elif kind == 'elevator':
        allowed = manager(user) or target.assigned_technician_id == technician_id(user) or ticket_flow.is_on_duty(user)
    elif kind == 'task':
        allowed = manager(user) or target.assigned_to_id == technician_id(user)
        allowed = allowed and target.status in ('NEW','IN_PROGRESS')
    else:
        allowed = kind == 'message' and target.author_id == user.pk
    if not allowed:
        raise PermissionDenied('Связать файл может исполнитель, дежурный или руководитель.')


def link_asset(user, asset, kind, target, category='OTHER', *, check=True):
    if asset.organization_id != target.organization_id:
        raise PermissionDenied('Файлы организаций изолированы.')
    if category not in dict(MediaLink._meta.get_field('category').choices):
        raise ValidationError('Выберите категорию вложения.')
    if check:
        may_link(user,kind,target)
    Organization.objects.select_for_update(no_key=True).get(pk=asset.organization_id)
    link, made = MediaLink.objects.get_or_create(asset=asset,**{kind:target},defaults={'created_by':user,'category':category})
    # Older clients still read their ticket/task attachment endpoints. These
    # rows point to the same immutable asset; they never create another file.
    if asset.content_type.startswith('image/') and kind == 'ticket':
        if not TicketPhoto.objects.filter(ticket=target,asset=asset).exists():
            TicketPhoto.objects.create(organization=asset.organization,ticket=target,asset=asset,
                uploaded_by=user,file=asset.file.name)
    elif kind == 'task':
        if not PersonalTaskAttachment.objects.filter(task=target,asset=asset).exists():
            PersonalTaskAttachment.objects.create(organization=asset.organization,task=target,asset=asset,
                file=asset.file.name,content_type=asset.content_type,size=asset.size)
    if made:
        audit(user,'media.link',str(asset.pk),{'target':kind,'id':target.pk,'category':category})
    return link


def visible_assets(user):
    from django.db.models import Q
    rows = MediaAsset.objects.filter(organization=org(user),state='READY')
    if manager(user):
        return rows
    # A personal task may be private; another public relationship grants access.
    return rows.filter(Q(uploaded_by=user,links__isnull=True)|Q(links__ticket__isnull=False)|Q(links__elevator__isnull=False)|Q(links__message__isnull=False)|Q(links__task__assigned_to_id=technician_id(user))).distinct()


def asset_data(asset, user):
    from .work_orders import assignee
    links = []
    for link in asset.links.select_related('ticket__elevator__building','elevator__building','message','task').all():
        kind = next(k for k in ('ticket','elevator','message','task') if getattr(link,k+'_id'))
        target = getattr(link,kind)
        if kind == 'task' and not (manager(user) or assignee(user,target)):
            continue
        lift = target.elevator if kind in ('ticket','task') else target if kind == 'elevator' else None
        links.append({'kind':kind,'id':target.pk,'category':link.category,'created_at':link.created_at,
            'elevator':lift.pk if lift else None,'address':lift.building.address if lift else '',
            'entrance':lift.entrance if lift else '', 'title':getattr(target,'title','')})
    base = f'/api/media/{asset.pk}/'
    return {'id':str(asset.pk),'name':asset.original_name,'content_type':asset.content_type,'size':asset.size,
        'sha256':asset.sha256,'created_at':asset.created_at,'captured_at':asset.captured_at,
        'author':asset.uploaded_by_id,'author_name':asset.uploaded_by.get_full_name() or asset.uploaded_by.username if asset.uploaded_by_id else 'Система',
        'state':asset.state,'url':base+'download/?organization='+str(asset.organization_id),
        'preview_url':base+'preview/?organization='+str(asset.organization_id) if asset.preview else None,'links':links}
