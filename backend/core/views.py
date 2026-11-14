import csv
import io
import warnings
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from django.contrib.auth import logout
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Prefetch
from django.http import FileResponse, HttpResponse
from django.middleware.csrf import get_token
from django.utils import timezone
from django.views.decorators.cache import never_cache
from PIL import Image, ImageOps, UnidentifiedImageError
from rest_framework import mixins
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet, GenericViewSet
from rest_framework.throttling import UserRateThrottle
from .models import Building, Elevator, Maintenance, Technician, Ticket, Organization, DutyShift, PartRequest, PartPhoto, ChatMessage, Document, TicketPhoto, AuditEvent, DutySwap, ChatAttachment, DocumentRevision
from .serializers import BuildingSerializer, ElevatorSerializer, MaintenanceSerializer, TechnicianSerializer, TicketSerializer, DutySerializer, PartSerializer, ChatSerializer, DocumentSerializer
from .access import MemberAccess, ManagerWrite, org, role, manager, admin_user, technician_id, audit, commissioning_access
from .scheduling import preview_plan, apply_plan
from .updates import VERSION


@api_view(['GET'])
@permission_classes([AllowAny])
def health(request):
    return Response({'status':'ok','service':'LiftControl API','version':VERSION,'android_api':1,'field_ops_api':1,'offline_sync_api':1,
                     'capabilities':['lift-stop','native-push','updates','staff-management','personal-tasks','chat-media','entrapment','document-edit','offline-sync','document-delete','media-vault','commissioning','notification-inbox','maintenance-pdf']})


@never_cache
@api_view(['GET'])
@permission_classes([MemberAccess])
def me(request):
    organization = org(request.user)
    from .organizations import available_organizations
    is_adjuster = role(request.user) == 'ADJUSTER'
    has_commissioning = commissioning_access(request.user)
    if is_adjuster:
        return Response({
            'id': request.user.pk, 'name': request.user.get_full_name() or request.user.username,
            'role': 'ADJUSTER', 'organization': organization.name, 'organization_id': organization.pk,
            'organizations': available_organizations(request.user), 'commissioning_only': True,
            'can_commissioning': has_commissioning, 'can_manage': False,
            'can_manage_access': False, 'can_create_ticket': False,
            'field_ops_api': 0, 'offline_sync_api': 0, 'media_api': 0, 'self_push_api': 0,
            'commissioning_sync_api': 1,
            'timezone': organization.timezone, 'utc_offset_minutes': int(timezone.now().astimezone(ZoneInfo(organization.timezone)).utcoffset().total_seconds()//60), 'server_time': timezone.now(),
            'csrf': get_token(request),
        })
    from .ticket_flow import is_on_duty, can_create_ticket
    from .duty_flow import current_shift, shift_data
    from .organizations import available_organizations
    from .mobile_sync import offline_grant
    return Response({'commissioning_only':False, 'can_commissioning':has_commissioning, 'commissioning_sync_api':1,
        'media_api':1,'offline_sync_api':1,'self_push_api':1, 'offline_grant':offline_grant(request.user),
        'on_duty':is_on_duty(request.user),'can_create_ticket':can_create_ticket(request.user),
        'id':request.user.id,'name':request.user.get_full_name() or request.user.username,'role':role(request.user),'field_ops_api':1,
        'organization':organization.name,'organization_id':organization.pk,'organizations':available_organizations(request.user),
        'timezone':organization.timezone,'utc_offset_minutes':int(timezone.now().astimezone(ZoneInfo(organization.timezone)).utcoffset().total_seconds()//60),
        'server_time':timezone.now(),'duty_start':organization.duty_start.strftime('%H:%M'),
        'duty_shift':shift_data(current_shift(request.user)),
        'next_duty_shift':shift_data(DutyShift.objects.filter(organization=organization,start__gt=timezone.now()).select_related('technician').order_by('start').first()), 'technician':technician_id(request.user),
        'can_manage_access':role(request.user) in ('ADMIN','DIRECTOR'),
        'can_manage':manager(request.user),'can_export_chat':admin_user(request.user),'is_superuser':request.user.is_superuser,
        'csrf':get_token(request)})


@api_view(['POST'])
@permission_classes([MemberAccess])
def sign_out(request):
    # Logging out from a shared phone must revoke its background push capability.
    endpoint = request.data.get('push_endpoint', '')
    if endpoint:
        from .push_notifications import valid_endpoint
        if valid_endpoint(endpoint):
            request.user.push_subscriptions.filter(endpoint=endpoint).delete()
        else:
            request.user.push_subscriptions.all().delete()
    else:
        request.user.push_subscriptions.all().delete()
    logout(request)
    return Response({'ok':True})


@api_view(['GET'])
@permission_classes([MemberAccess])
def dashboard(request):
    organization = org(request.user)
    today = timezone.localdate()
    return Response({'buildings':Building.objects.filter(organization=organization).count(),
        'elevators':Elevator.objects.filter(organization=organization,active=True).count(),
        'technicians':Technician.objects.filter(organization=organization,active=True).count(),
        'open_tickets':Ticket.objects.filter(organization=organization).exclude(status__in=['DONE','CANCELED']).count(),
        'maintenance_today':Maintenance.objects.filter(organization=organization,planned_date=today).count()})


class TenantViewSet(ModelViewSet):
    permission_classes = [ManagerWrite]
    http_method_names = ['get','post','patch','head','options']
    def get_queryset(self):
        return super().get_queryset().filter(organization=org(self.request.user))
    def perform_create(self,serializer):
        obj = serializer.save(organization=org(self.request.user))
        audit(self.request.user, self.basename+'.create', obj.pk)
    def perform_update(self,serializer):
        obj = serializer.save()
        audit(self.request.user, self.basename+'.update', obj.pk)


class BuildingViewSet(TenantViewSet):
    queryset = Building.objects.all()
    serializer_class = BuildingSerializer


class TechnicianViewSet(TenantViewSet):
    def get_permissions(self):
        from .access import OwnerWrite
        return [OwnerWrite()]
    queryset = Technician.objects.all()
    serializer_class = TechnicianSerializer

    @action(detail=True, methods=['post'])
    def archive(self, request, pk=None):
        from .archiving import archive_technician
        self.get_object()
        archive_technician(request.user, pk, request.data)
        return Response({'ok': True})


class ElevatorViewSet(TenantViewSet):
    queryset = Elevator.objects.select_related('building','assigned_technician').all()
    serializer_class = ElevatorSerializer

    @action(detail=True, methods=['post'])
    def archive(self, request, pk=None):
        from .archiving import archive_elevator
        self.get_object()
        archive_elevator(request.user, pk, request.data)
        return Response({'ok': True})
    @action(detail=True,methods=['get'])
    def documents(self,request,pk=None):
        elevator = self.get_object()
        # No manual attachments to individual lifts; match the shared library.
        docs = Document.objects.filter(organization=org(request.user),deleted_at__isnull=True,manufacturer__iexact=elevator.manufacturer)
        docs = [d for d in docs if not d.model or d.model.casefold() == elevator.model.casefold()]
        return Response(DocumentSerializer(docs,many=True).data)


class TicketViewSet(TenantViewSet):
    permission_classes = [MemberAccess]
    queryset = Ticket.objects.select_related(
        'elevator__building', 'elevator__assigned_technician', 'assigned_to', 'created_by'
    ).prefetch_related(
        Prefetch('photos', queryset=TicketPhoto.objects.filter(deleted_at__isnull=True).select_related('uploaded_by'),
                 to_attr='current_photos')
    )
    serializer_class = TicketSerializer
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def create(self, request, *args, **kwargs):
        from .ticket_flow import create_ticket
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # The server validates assignment; author and status remain server controlled.
        allowed = {k: v for k, v in serializer.validated_data.items()
                   if k in ('elevator', 'title', 'description', 'priority', 'assigned_to')}
        ticket = create_ticket(request.user, allowed)
        return Response(self.get_serializer(ticket).data, status=201)

    @transaction.atomic
    def partial_update(self, request, *args, **kwargs):
        from .ticket_flow import PRIVILEGED
        if role(request.user) not in PRIVILEGED:
            raise PermissionDenied('Изменить исходные сведения может только диспетчер или руководитель.')
        from django.shortcuts import get_object_or_404
        ticket = get_object_or_404(Ticket.objects.select_for_update(), pk=kwargs['pk'], organization=org(request.user))
        if ticket.status != Ticket.Status.NEW:
            raise ValidationError('Исходные сведения меняются только до принятия заявки.')
        allowed = ('title', 'description', 'priority')
        if not request.data or any(k not in allowed for k in request.data.keys()):
            raise ValidationError('Разрешено менять только название, описание и приоритет заявки.')
        serializer = self.get_serializer(ticket, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        from .ticket_flow import add_event
        fields = ', '.join(sorted(request.data.keys()))
        add_event(ticket, request.user, 'corrected', before=ticket.status,
                  after=ticket.status, reason='Изменены поля: ' + fields)
        return Response(serializer.data)

    def update(self, request, *args, **kwargs):
        raise PermissionDenied('Используйте предусмотренные операции заявки.')

    def destroy(self, request, *args, **kwargs):
        raise PermissionDenied('Заявки не удаляются. Дежурный может отменить заявку с причиной.')

    @action(detail=True, methods=['post'])
    def accept(self, request, pk=None):
        from .ticket_flow import accept_ticket
        self.get_object()
        return Response(self.get_serializer(accept_ticket(request.user, pk)).data)

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        from .ticket_flow import complete_ticket
        self.get_object()
        return Response(self.get_serializer(
            complete_ticket(request.user, pk, request.data.get('work_description', ''))
        ).data)

    @action(detail=True, methods=['post'])
    def stop(self, request, pk=None):
        from .ticket_flow import stop_ticket
        self.get_object()
        return Response(self.get_serializer(stop_ticket(request.user, pk, request.data.get('reason'))).data)

    @action(detail=True, methods=['post'])
    def resume(self, request, pk=None):
        from .ticket_flow import resume_ticket
        self.get_object()
        return Response(self.get_serializer(resume_ticket(request.user, pk)).data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        from .ticket_flow import cancel_ticket
        self.get_object()
        return Response(self.get_serializer(
            cancel_ticket(request.user, pk, request.data.get('reason', ''))
        ).data)

    @action(detail=True, methods=['post'])
    def assign(self, request, pk=None):
        from .ticket_flow import reassign_ticket
        self.get_object()
        return Response(self.get_serializer(
            reassign_ticket(request.user, pk, request.data.get('assigned_to'),
                            request.data.get('reason', ''))
        ).data)

    @action(detail=True, methods=['post'])
    def comment(self, request, pk=None):
        from .ticket_flow import add_comment
        self.get_object()
        note = add_comment(request.user, pk, request.data.get('body', ''))
        return Response({'id': note.pk, 'created_at': note.created_at, 'body': note.body}, status=201)

    @action(detail=True, methods=['get'])
    def history(self, request, pk=None):
        ticket = self.get_object()
        return Response({
            'events': [
                {'id': e.pk, 'action': e.action, 'before': e.before, 'after': e.after,
                 'reason': e.reason, 'created_at': e.created_at,
                 'actor': (e.actor.get_full_name() or e.actor.username) if e.actor else 'Система'}
                for e in ticket.events.select_related('actor')
            ],
            'comments': [
                {'id': c.pk, 'body': c.body, 'kind': c.kind, 'created_at': c.created_at,
                 'author': (c.author.get_full_name() or c.author.username) if c.author else 'Система'}
                for c in ticket.comments.select_related('author')
            ],
        })

    @action(detail=True, methods=['post'])
    def photos(self, request, pk=None):
        ticket = self.get_object()
        from .ticket_photos import save_photo
        photo = save_photo(request.user, ticket, request.FILES.get('file'))
        return Response({'id': photo.pk, 'url': f'/api/tickets/{ticket.pk}/photos/{photo.pk}/'}, status=201)

    @action(detail=True, methods=['get', 'delete'], url_path=r'photos/(?P<photo_id>\d+)')
    def photo(self, request, pk=None, photo_id=None):
        ticket = self.get_object()
        from django.shortcuts import get_object_or_404
        photo = get_object_or_404(ticket.photos, pk=photo_id, deleted_at__isnull=True)
        if request.method == 'DELETE':
            raise PermissionDenied('Фотографии являются частью истории заявки и не удаляются.')
        if photo.asset_id:
            from .media import serve
            return serve(photo.asset,request)
        response = FileResponse(photo.file.open('rb'), content_type='image/jpeg')
        response['Cache-Control'] = 'private, no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        return response


class MaintenanceViewSet(TenantViewSet):
    permission_classes = [MemberAccess]
    queryset = Maintenance.objects.select_related('elevator__building','technician').all()
    serializer_class = MaintenanceSerializer


class DutyViewSet(TenantViewSet):
    queryset = DutyShift.objects.select_related('technician').all()
    serializer_class = DutySerializer
    http_method_names = ['get','post','patch','delete','head','options']
    permission_classes = [MemberAccess]
    def get_permissions(self):
        if self.action in ('list','retrieve','acknowledge','swap'):
            return [MemberAccess()]
        return [ManagerWrite()]
    def get_queryset(self):
        q = super().get_queryset()
        start,end = self.request.query_params.get('from'),self.request.query_params.get('to')
        if start and end:
            try:
                tz = ZoneInfo(org(self.request.user).timezone)
                a = datetime.combine(datetime.strptime(start,'%Y-%m-%d').date(),time.min,tzinfo=tz)
                b = datetime.combine(datetime.strptime(end,'%Y-%m-%d').date()+timedelta(days=1),time.min,tzinfo=tz)
                if not 0 < (b-a).days <= 366:
                    raise ValueError()
                q = q.filter(start__lt=b,end__gt=a)
            except ValueError:
                raise ValidationError('Неверный период; максимум 366 дней.')
        return q
    @transaction.atomic
    def create(self,request,*args,**kwargs):
        Organization.objects.select_for_update().get(pk=org(request.user).pk)
        return super().create(request,*args,**kwargs)
    @transaction.atomic
    def update(self,request,*args,**kwargs):
        Organization.objects.select_for_update().get(pk=org(request.user).pk)
        shift = self.get_object()
        if shift.start <= timezone.now():
            raise ValidationError('Начавшуюся смену передают через запрос подмены.')
        if shift.swaps.filter(status='PENDING').exists():
            raise ValidationError('Есть ожидающая подтверждения подмена. Сначала отмените запрос подмены.')
        return super().update(request,*args,**kwargs)
    def perform_update(self,serializer):
        shift = serializer.instance
        before = {'technician':shift.technician_id,'start':shift.start.isoformat(),
                  'end':shift.end.isoformat(),'kind':shift.kind}
        updated = serializer.save()
        after = {'technician':updated.technician_id,'start':updated.start.isoformat(),
                 'end':updated.end.isoformat(),'kind':updated.kind}
        if before != after:
            audit(self.request.user,'duty.edit',updated.pk,{'before':before,'after':after})
    @transaction.atomic
    def destroy(self,request,*args,**kwargs):
        Organization.objects.select_for_update().get(pk=org(request.user).pk)
        shift = self.get_object()
        if shift.start <= timezone.now():
            raise ValidationError('Начавшиеся и завершённые смены сохраняются в истории.')
        if shift.swaps.exists():
            raise ValidationError('Смена содержит историю запросов подмены. Измените назначение без удаления.')
        audit(request.user,'duty.remove',shift.pk)
        shift.delete()
        return Response(status=204)
    @action(detail=True,methods=['post'])
    def acknowledge(self,request,pk=None):
        from .duty_flow import acknowledge
        self.get_object()
        return Response(acknowledge(request.user,pk))
    @action(detail=True,methods=['post'])
    def swap(self,request,pk=None):
        from .duty_flow import request_swap, swap_data
        self.get_object()
        return Response(swap_data(request_swap(request.user,pk,request.data.get('technician'),request.data.get('reason'))),status=201)
    @action(detail=False,methods=['post'])
    def preview(self,request):
        return Response(preview_plan(request.user,request.data))
    @action(detail=False,methods=['post'])
    def apply(self,request):
        return Response(apply_plan(request.user,request.data.get('token','')))


class PartViewSet(TenantViewSet):
    permission_classes = [MemberAccess]
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def destroy(self, request, *args, **kwargs):
        from rest_framework.exceptions import MethodNotAllowed
        raise MethodNotAllowed('DELETE', detail='Заказы запчастей сохраняются в истории.')
    queryset = PartRequest.objects.select_related('elevator__building', 'requested_by').prefetch_related('photos__asset')
    serializer_class = PartSerializer

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        Organization.objects.select_for_update().get(pk=org(request.user).pk)
        return super().update(request, *args, **kwargs)

    def perform_create(self, serializer):
        part = serializer.save(organization=org(self.request.user), requested_by=self.request.user)
        audit(self.request.user, 'part.create', part.pk)

    def perform_update(self, serializer):
        before = {k: getattr(serializer.instance, k) for k in ('elevator_id', 'part', 'quantity', 'notes', 'status')}
        part = serializer.save()
        after = {k: getattr(part, k) for k in before}
        if before != after:
            audit(self.request.user, 'part.edit', part.pk, {'before': before, 'after': after})

    @action(detail=True, methods=['post'], url_path='photos')
    def upload_photo(self, request, pk=None):
        from .media_vault import atomic_media, ingest
        part = self.get_object()
        if not (manager(request.user) or (part.requested_by_id == request.user.pk and part.status == 'NEW')):
            raise PermissionDenied('Добавить фото может автор новой заявки или руководитель.')
        if part.status in ('ORDERED', 'RECEIVED', 'ISSUED'):
            raise ValidationError('После заказа поставщику нельзя менять фотографии заявки.')
        upload = request.FILES.get('file')
        if upload is None or not 0 < upload.size <= 10 * 1024 * 1024:
            raise ValidationError('Выберите фото JPEG, PNG или WebP размером до 10 МБ.')
        with atomic_media():
            Organization.objects.select_for_update().get(pk=part.organization_id)
            part.refresh_from_db()
            if part.status in ('ORDERED', 'RECEIVED', 'ISSUED'):
                raise ValidationError('После заказа поставщику нельзя менять фотографии заявки.')
            if not (manager(request.user) or (part.requested_by_id == request.user.pk and part.status == 'NEW')):
                raise PermissionDenied('Добавить фото может автор новой заявки или руководитель.')
            if part.photos.filter(deleted_at__isnull=True).count() >= 8:
                raise ValidationError('Не более восьми фотографий к заявке.')
            asset = ingest(request.user, upload, video=False)
            photo, created = PartPhoto.objects.get_or_create(
                organization=org(request.user), part=part, asset=asset,
                defaults={'uploaded_by': request.user})
            if photo.deleted_at:
                photo.deleted_at = None
                photo.deleted_by = None
                photo.save(update_fields=['deleted_at', 'deleted_by'])
            if part.status == 'APPROVED':
                part.status = 'NEW'
                part.save(update_fields=['status', 'updated_at'])
                audit(request.user, 'part.approval.invalidate', part.pk, {'source': 'photo'})
            audit(request.user, 'part.photo.add', part.pk, {'photo': photo.pk})
        return Response({'id': photo.pk,
                         'url': f'/api/parts/{part.pk}/photos/{photo.pk}/?organization={part.organization_id}',
                         'content_type': asset.content_type}, status=201 if created else 200)

    @action(detail=True, methods=['get', 'delete'], url_path=r'photos/(?P<photo_id>\d+)')
    def photo(self, request, pk=None, photo_id=None):
        from django.shortcuts import get_object_or_404
        from .media import serve
        part = self.get_object()
        photo = get_object_or_404(PartPhoto.objects.select_related('asset'),
            pk=photo_id, part=part, organization=org(request.user), deleted_at__isnull=True)
        if request.method == 'GET':
            return serve(photo, request)
        if not (manager(request.user) or (part.requested_by_id == request.user.pk and part.status == 'NEW')):
            raise PermissionDenied('Удалить фото может автор новой заявки или руководитель.')
        if part.status in ('ORDERED', 'RECEIVED', 'ISSUED'):
            raise ValidationError('Фото заказанной запчасти сохраняются в истории.')
        with transaction.atomic():
            Organization.objects.select_for_update().get(pk=part.organization_id)
            part.refresh_from_db()
            if part.status in ('ORDERED', 'RECEIVED', 'ISSUED'):
                raise ValidationError('Фото заказанной запчасти сохраняются в истории.')
            photo.deleted_at = timezone.now()
            photo.deleted_by = request.user
            photo.save(update_fields=['deleted_at', 'deleted_by'])
            if part.status == 'APPROVED':
                part.status = 'NEW'
                part.save(update_fields=['status', 'updated_at'])
                audit(request.user, 'part.approval.invalidate', part.pk, {'source': 'photo'})
            audit(request.user, 'part.photo.remove', part.pk, {'photo': photo.pk})
        return Response(status=204)


class ChatThrottle(UserRateThrottle):
    rate = '30/minute'
    def allow_request(self, request, view):
        return request.method in ('GET','HEAD','OPTIONS') or super().allow_request(request,view)


class ChatViewSet(mixins.ListModelMixin,mixins.RetrieveModelMixin,mixins.CreateModelMixin,GenericViewSet):
    permission_classes = [MemberAccess]
    queryset = ChatMessage.objects.select_related('author', 'linked_ticket__elevator__building', 'linked_part__elevator__building').prefetch_related('attachments__asset').all()
    serializer_class = ChatSerializer
    throttle_classes = [ChatThrottle]
    def get_queryset(self):
        q = super().get_queryset().filter(organization=org(self.request.user))
        if self.action == 'export':
            start,end = self.request.query_params.get('from'),self.request.query_params.get('to')
            try:
                tz = ZoneInfo(org(self.request.user).timezone)
                a = datetime.combine(datetime.strptime(start,'%Y-%m-%d').date(),time.min,tzinfo=tz)
                b = datetime.combine(datetime.strptime(end,'%Y-%m-%d').date()+timedelta(days=1),time.min,tzinfo=tz)
                if not 0 < (b-a).days <= 366:
                    raise ValueError()
            except (ValueError,TypeError):
                raise ValidationError('Выберите период выгрузки до 366 дней.')
            return q.filter(created_at__gte=a,created_at__lt=b)
        if self.action == 'retrieve':
            return q
        # Newest 200 messages returned oldest-first; bounded UI without leaking other tenants.
        ids = q.order_by('-id').values_list('id',flat=True)[:200]
        return q.filter(id__in=ids).order_by('id')
    def perform_create(self,serializer):
        from .media import MAX_MEDIA
        from .media_vault import atomic_media, ingest, link_asset
        files = self.request.FILES.getlist('files') or self.request.FILES.getlist('file')
        if len(files)>5 or sum(f.size for f in files)>MAX_MEDIA:
            raise ValidationError('До 5 вложений и 20 МБ на сообщение.')
        if not files and not serializer.validated_data.get('body','').strip() and not (serializer.validated_data.get('linked_ticket') or serializer.validated_data.get('linked_part')):
            raise ValidationError('Добавьте текст, фото или видео.')
        with atomic_media():
            msg = serializer.save(organization=org(self.request.user),author=self.request.user)
            for file in files:
                asset = ingest(self.request.user,file)
                ChatAttachment.objects.create(organization=org(self.request.user),message=msg,asset=asset,
                    file=asset.file.name,content_type=asset.content_type,size=asset.size)
                link_asset(self.request.user,asset,'message',msg,check=False)
            audit(self.request.user,'chat.send',msg.pk)
            from .notification_inbox import enqueue_team_chat
            enqueue_team_chat(msg)
    @action(detail=True,methods=['get'],url_path=r'attachments/(?P<attachment_id>\d+)')
    def attachment(self,request,pk=None,attachment_id=None):
        from .media import serve
        from django.shortcuts import get_object_or_404
        row = get_object_or_404(ChatAttachment,pk=attachment_id,message_id=pk,organization=org(request.user))
        return serve(row,request)
    @action(detail=False,methods=['get'])
    def export(self,request):
        if not admin_user(request.user):
            raise PermissionDenied('Выгрузка чата доступна только администратору.')
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Дата','Автор','Сообщение'])
        def safe(text):
            text = str(text)
            return "'"+text if text.lstrip().startswith(('=','+','-','@')) or text.startswith(('\t','\r','\n')) else text
        for msg in self.get_queryset().iterator(chunk_size=100):
            writer.writerow([timezone.localtime(msg.created_at).isoformat(),safe((msg.author.get_full_name() or msg.author.username) if msg.author else 'Удалённый пользователь'),safe(msg.body)])
        audit(request.user,'chat.export')
        response = HttpResponse('\ufeff'+output.getvalue(),content_type='text/csv; charset=utf-8')
        response['Content-Disposition']='attachment; filename="LiftControl-chat.csv"'
        response['Cache-Control']='private, no-store'
        return response


class DocumentViewSet(TenantViewSet):
    queryset = Document.objects.filter(deleted_at__isnull=True)
    serializer_class = DocumentSerializer
    http_method_names = ['get','post','patch','put','delete','head','options']
    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        if role(request.user) not in ('ADMIN', 'DIRECTOR'):
            raise PermissionDenied('Удалять документы может администратор или руководитель.')
        reason = str(request.data.get('reason', '')).strip()
        if not 5 <= len(reason) <= 1000:
            raise ValidationError({'reason': 'Укажите причину удаления (5–1000 символов).'})
        from django.shortcuts import get_object_or_404
        document = get_object_or_404(self.get_queryset().select_for_update(), pk=kwargs['pk'])
        document.deleted_at, document.deleted_by, document.deletion_reason = timezone.now(), request.user, reason
        document.save(update_fields=['deleted_at', 'deleted_by', 'deletion_reason'])
        audit(request.user, 'document.delete', document.pk, {'reason': reason})
        return Response(status=204)
    def perform_create(self,serializer):
        upload = self.request.FILES.get('file')
        if not upload or upload.size > 20*1024*1024 or upload.read(5) != b'%PDF-':
            raise ValidationError('Выберите PDF до 20 МБ.')
        upload.seek(0)
        document = serializer.save(organization=org(self.request.user),file=upload)
        audit(self.request.user,'document.create',document.pk)
    def perform_update(self,serializer):
        from django.utils.dateparse import parse_datetime
        saved_file = None
        old_name = None
        try:
            with transaction.atomic():
                current = Document.objects.select_for_update().get(pk=serializer.instance.pk,organization=org(self.request.user))
                expected = self.request.data.get('expected_updated_at')
                if expected and parse_datetime(str(expected)) != current.updated_at:
                    raise ValidationError('Документ уже изменён другим сотрудником. Обновите список и откройте его заново.')
                old_name = current.file.name
                DocumentRevision.objects.create(document=current,title=current.title,manufacturer=current.manufacturer,
                    model=current.model,file=current.file.name,changed_by=self.request.user)
                serializer.instance = current
                fields = list(serializer.validated_data)
                replacement = serializer.validated_data.pop('file',None)
                if replacement:
                    current.file.save('document.pdf',replacement,save=False)
                    saved_file = current.file
                document = serializer.save(updated_at=timezone.now())
                saved_file = document.file
                audit(self.request.user,'document.update',document.pk,{'fields':fields,'file_replaced':replacement is not None})
        except Exception:
            if saved_file and saved_file.name != old_name:
                saved_file.delete(save=False)
            raise
    @action(detail=True,methods=['get'])
    def download(self,request,pk=None):
        doc = self.get_object()
        response = FileResponse(doc.file.open('rb'),as_attachment=True,filename=f'document-{doc.pk}.pdf',content_type='application/pdf')
        response['Cache-Control']='private, no-store'
        response['X-Content-Type-Options']='nosniff'
        return response


@api_view(['GET'])
@permission_classes([MemberAccess])
def audit_events(request):
    if not admin_user(request.user):
        raise PermissionDenied()
    return Response(list(AuditEvent.objects.filter(organization=org(request.user)).values('id','created_at','action','object_id','actor_id')[:200]))


@api_view(['GET'])
@permission_classes([MemberAccess])
def push_public_key(request):
    from .push_notifications import push_config
    config = push_config()
    return Response({'enabled': config['enabled'],
                     'public_key': config['public_key'] if config['enabled'] else ''})


@api_view(['POST', 'DELETE'])
@permission_classes([MemberAccess])
def push_subscription(request):
    from .push_notifications import register_subscription, unregister_subscription
    if request.method == 'DELETE':
        from .push_notifications import valid_endpoint
        endpoint = request.data.get('endpoint', '')
        if not valid_endpoint(endpoint):
            raise ValidationError('Некорректный адрес подписки.')
        unregister_subscription(request.user, endpoint)
        return Response(status=204)
    register_subscription(request.user, request.data)
    return Response({'ok': True}, status=201)



class DutySwapViewSet(mixins.ListModelMixin, GenericViewSet):
    permission_classes = [MemberAccess]
    queryset = DutySwap.objects.select_related('shift','source_technician','target_technician')
    def get_queryset(self):
        return self.queryset.filter(organization=org(self.request.user)).order_by('-pk')
    def list(self,request):
        from .duty_flow import swap_data
        return Response([swap_data(row) for row in self.get_queryset()[:200]])
    @action(detail=True,methods=['post'])
    def accept(self,request,pk=None):
        from .duty_flow import resolve_swap, swap_data
        self.get_object()
        return Response(swap_data(resolve_swap(request.user,pk,True)))
    @action(detail=True,methods=['post'])
    def cancel(self,request,pk=None):
        from .duty_flow import resolve_swap, swap_data
        self.get_object()
        return Response(swap_data(resolve_swap(request.user,pk,False)))
