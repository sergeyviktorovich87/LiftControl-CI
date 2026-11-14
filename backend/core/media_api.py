import hashlib
import json
import uuid
from datetime import datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from django.db.models import Q, Sum, Count
from django.shortcuts import get_object_or_404
from django.utils.http import content_disposition_header
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError, NotFound
from rest_framework.response import Response
from rest_framework.decorators import throttle_classes
from rest_framework.throttling import UserRateThrottle
from .access import MemberAccess, org, manager, role, technician_id, audit
from .models import MediaAsset, MediaLink, MediaUploadReceipt, Organization, ChatMessage, ChatAttachment, Ticket, PartRequest, Elevator, PersonalTask
from .media_vault import atomic_media, ingest, link_asset, may_link, visible_assets, asset_data
from .media import serve
from .mobile_sync import SyncConflict

class MediaThrottle(UserRateThrottle):
    rate = '30/min'
    scope = 'media_upload'


def number(value):
    try:
        value = int(value)
        if value <= 0:
            raise ValueError()
        return value
    except (ValueError,TypeError,OverflowError):
        raise ValidationError('Укажите существующую запись.')


def target(user, kind, value):
    model = {'ticket':Ticket,'elevator':Elevator,'task':PersonalTask}.get(kind)
    if model is None:
        raise ValidationError('Связать можно с заявкой, лифтом или заданием.')
    row = get_object_or_404(model.objects.select_for_update(no_key=True),pk=number(value),organization=org(user))
    may_link(user,kind,row)
    return row


@api_view(['GET'])
@permission_classes([MemberAccess])
def catalogue(request):
    rows = visible_assets(request.user).select_related('uploaded_by').prefetch_related('links')
    query = request.query_params
    manages_media = manager(request.user)
    task_scope = Q(links__task__isnull=False)
    if not manages_media:
        task_scope &= Q(links__task__assigned_to_id=technician_id(request.user))
    if query.get('task'):
        from .work_orders import assignee
        task=get_object_or_404(PersonalTask,pk=number(query['task']),organization=org(request.user))
        if not (manages_media or assignee(request.user,task)):
            raise NotFound()
    for field, lookup in [('ticket','links__ticket_id'),('task','links__task_id'),('author','uploaded_by_id')]:
        if query.get(field):
            rows = rows.filter(**{lookup:number(query[field])})
    if query.get('elevator'):
        lift = number(query['elevator'])
        rows = rows.filter(
            Q(links__elevator_id=lift)
            | Q(links__ticket__elevator_id=lift)
            | (task_scope & Q(links__task__elevator_id=lift))
        )
    if query.get('source') == 'chat':
        rows = rows.filter(links__message__isnull=False)
    if query.get('message'):
        rows = rows.filter(links__message_id=number(query['message']))
    if query.get('type') in ('image','video'):
        rows = rows.filter(content_type__startswith=query['type']+'/')
    if query.get('address'):
        text = query['address'][:200]
        rows = rows.filter(
            Q(links__ticket__elevator__building__address__icontains=text)
            | Q(links__elevator__building__address__icontains=text)
            | (task_scope & Q(links__task__elevator__building__address__icontains=text))
        )
    tz = ZoneInfo(org(request.user).timezone)
    for key, lookup in [('from','created_at__gte'),('to','created_at__lt')]:
        if query.get(key):
            try:
                date = datetime.strptime(query[key],'%Y-%m-%d').date()
                if key == 'to': date += timedelta(days=1)
                rows = rows.filter(**{lookup:datetime.combine(date,time.min,tzinfo=tz)})
            except ValueError:
                raise ValidationError('Дата должна быть в формате ГГГГ-ММ-ДД.')
    try:
        offset = max(0,min(int(query.get('offset',0)),1000000))
    except ValueError:
        raise ValidationError('Неверный номер страницы.')
    rows = rows.distinct().order_by('-created_at','id')
    count = rows.count()
    return Response({'count':count,'next':offset+40 if offset+40<count else None,
        'results':[asset_data(row,request.user) for row in rows[offset:offset+40]]})


@api_view(['GET'])
@permission_classes([MemberAccess])
def statistics(request):
    if role(request.user) not in ('ADMIN','DIRECTOR'):
        raise PermissionDenied('Объём хранения доступен администратору.')
    rows = MediaAsset.objects.filter(organization=org(request.user),state='READY')
    data = rows.aggregate(count=Count('id'),originals=Sum('size'),previews=Sum('preview_size'),
        photos=Sum('size',filter=Q(content_type__startswith='image/')),videos=Sum('size',filter=Q(content_type__startswith='video/')))
    data = {k:v or 0 for k,v in data.items()}
    return Response({**data,'total':data['originals']+data['previews'],'automatic_deletion':False})


@api_view(['GET'])
@permission_classes([MemberAccess])
def download(request, asset_id, variant):
    if variant not in ('preview','download'):
        raise NotFound()
    asset = get_object_or_404(visible_assets(request.user),pk=asset_id)
    if variant == 'preview':
        if not asset.preview:
            raise NotFound('Миниатюра отсутствует.')
        row = SimpleNamespace(file=asset.preview,size=asset.preview_size,content_type='image/webp')
    else:
        row = asset
    try:
        response = serve(row,request)
    except FileNotFoundError:
        raise NotFound('Файл недоступен. Обратитесь к администратору.')
    if request.query_params.get('download') == '1':
        response['Content-Disposition'] = content_disposition_header(True,asset.original_name)
    return response


@api_view(['POST'])
@permission_classes([MemberAccess])
@throttle_classes([MediaThrottle])
def attach(request, asset_id):
    asset = get_object_or_404(visible_assets(request.user),pk=asset_id)
    kind = request.data.get('kind')
    with atomic_media():
        Organization.objects.select_for_update(no_key=True).get(pk=org(request.user).pk)
        row = target(request.user,kind,request.data.get('target_id'))
        link_asset(request.user,asset,kind,row,request.data.get('category','OTHER'))
    return Response(asset_data(asset,request.user))


@api_view(['POST'])
@permission_classes([MemberAccess])
@throttle_classes([MediaThrottle])
def upload(request):
    try:
        operation = uuid.UUID(str(request.data['upload_id']))
    except (KeyError,TypeError,ValueError):
        raise ValidationError('Для загрузки требуется постоянный UUID.')
    kind = request.data.get('kind','library')
    if kind not in ('chat','ticket','elevator','task','library'):
        raise ValidationError('Неподдерживаемая связь файла.')
    fields = {key:str(request.data.get(key,'')) for key in ('kind','target_id','body','message_key','category','sha256','name','linked_ticket','linked_part')}
    fields['kind'] = kind
    if len(fields['body']) > 4000:
        raise ValidationError('Сообщение — до 4000 символов.')
    file = request.FILES.get('file')
    if not file and (kind != 'chat' or not (fields['body'].strip() or fields['linked_ticket'] or fields['linked_part'])):
        raise ValidationError('Добавьте файл или текст сообщения.')
    digest = ''
    if file:
        if file.size > 20 * 1024**2:
            raise ValidationError('Файл — до 20 МБ.')
        digest = hashlib.file_digest(file,'sha256').hexdigest(); file.seek(0)
        if fields['sha256'] and fields['sha256'] != digest:
            raise ValidationError('Контрольная сумма файла не совпала. Повторите загрузку.')
    fingerprint = hashlib.sha256((json.dumps(fields,sort_keys=True)+digest).encode()).hexdigest()
    with atomic_media():
        organization = org(request.user)
        Organization.objects.select_for_update(no_key=True).get(pk=organization.pk)
        saved = MediaUploadReceipt.objects.filter(organization=organization,user=request.user,operation_id=operation).first()
        if saved:
            if saved.fingerprint != fingerprint:
                raise SyncConflict()
            if saved.result.get('asset'):
                get_object_or_404(visible_assets(request.user),pk=saved.result['asset']['id'])
            return Response({**saved.result,'replayed':True})
        context = None
        if kind == 'chat':
            try:
                key = uuid.UUID(fields['message_key'])
            except ValueError:
                raise ValidationError('У сообщения должен быть постоянный UUID.')
            if fields['linked_ticket'] and fields['linked_part']:
                raise ValidationError('К сообщению можно прикрепить только одну связанную запись.')
            linked = {}
            if fields['linked_ticket']:
                linked['linked_ticket'] = get_object_or_404(
                    Ticket, pk=number(fields['linked_ticket']), organization=organization)
            if fields['linked_part']:
                linked['linked_part'] = get_object_or_404(
                    PartRequest, pk=number(fields['linked_part']), organization=organization)
            context,made = ChatMessage.objects.get_or_create(
                organization=organization,author=request.user,client_id=key,
                defaults={'body':fields['body'], **linked})
            if not made and (context.body != fields['body'] or
                    context.linked_ticket_id != getattr(linked.get('linked_ticket'), 'pk', None) or
                    context.linked_part_id != getattr(linked.get('linked_part'), 'pk', None)):
                raise SyncConflict()
            if file and context.attachments.count() >= 5:
                raise ValidationError('До 5 вложений в сообщении.')
        elif kind != 'library':
            context = target(request.user,kind,fields['target_id'])
        if file and fields['name']:
            file.name = fields['name'][:180]
        asset = ingest(request.user,file,video=kind != 'task') if file else None
        if asset and context:
            if kind == 'chat' and not context.attachments.filter(asset=asset).exists() and (context.attachments.aggregate(total=Sum('size'))['total'] or 0)+asset.size > 20*1024**2:
                raise ValidationError('Всего до 20 МБ на сообщение.')
            link_asset(request.user,asset,'message' if kind=='chat' else kind,context,fields['category'] or 'OTHER')
            if kind == 'chat':
                ChatAttachment.objects.get_or_create(organization=organization,message=context,asset=asset,
                    defaults={'file':asset.file.name,'content_type':asset.content_type,'size':asset.size})
        from rest_framework.renderers import JSONRenderer
        result = {'operation_id':str(operation),'asset':asset_data(asset,request.user) if asset else None,
            'message':context.pk if kind=='chat' else None,'state':'READY'}
        result = json.loads(JSONRenderer().render(result))
        MediaUploadReceipt.objects.create(organization=organization,user=request.user,operation_id=operation,fingerprint=fingerprint,result=result)
        audit(request.user,'media.upload',str(asset.pk) if asset else context.pk)
    return Response({**result,'replayed':False},status=201)
