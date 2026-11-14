"""Personal work is never exposed through the organization's shared ticket history."""
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet
from .access import MemberAccess, org, manager, role, technician_id, audit
from .models import PersonalTask, PersonalTaskEvent, PersonalTaskAttachment, Membership
from .serializers import TenantSerializer
from .media import attachment_data, normalize, serve


def assignee(user, task):
    return role(user) == 'MECHANIC' and technician_id(user) == task.assigned_to_id


def event(task, user, action, reason=''):
    row = PersonalTaskEvent.objects.create(task=task, actor=user, action=action, reason=reason)
    audit(user, 'task.'+action, task.pk)
    if action in ('created','assigned','accepted','completed','canceled'):
        from .notification_inbox import enqueue_task
        enqueue_task(row)
        from .task_push import enqueue
        enqueue(row)
    return row


class TaskSerializer(TenantSerializer):
    technician_name = serializers.CharField(source='assigned_to.full_name', read_only=True)
    address = serializers.CharField(source='elevator.building.address', default='', read_only=True)
    created_by_name = serializers.SerializerMethodField()
    can_accept = serializers.SerializerMethodField()
    can_complete = serializers.SerializerMethodField()
    can_cancel = serializers.SerializerMethodField()
    attachments = serializers.SerializerMethodField()
    history = serializers.SerializerMethodField()

    def get_created_by_name(self, obj):
        return (obj.created_by.get_full_name() or obj.created_by.username) if obj.created_by else 'Система'
    def get_can_accept(self, obj):
        return obj.status == 'NEW' and assignee(self.context['request'].user, obj)
    def get_can_complete(self, obj):
        return obj.status == 'IN_PROGRESS' and assignee(self.context['request'].user, obj)
    def get_can_cancel(self, obj):
        return manager(self.context['request'].user) and obj.status in ('NEW','IN_PROGRESS')
    def get_attachments(self, obj):
        return [attachment_data(a,obj.pk,'tasks') for a in obj.attachments.all()]
    def get_history(self, obj):
        if self.context['view'].action == 'list':
            return []
        return [{'id':e.pk,'action':e.action,'body':e.reason,'created_at':e.created_at,
                 'author_name':(e.actor.get_full_name() or e.actor.username) if e.actor else 'Система'}
                for e in obj.events.select_related('actor').order_by('-id')[:100]][::-1]
    def validate(self, attrs):
        attrs = super().validate(attrs)
        user = self.context['request'].user
        person = attrs.get('assigned_to')
        if person and (not person.active or not Membership.objects.filter(organization=org(user),
                technician=person,role='MECHANIC',active=True,user__is_active=True).exists()):
            raise ValidationError({'assigned_to':'Выберите механика с действующим доступом к организации.'})
        if attrs.get('elevator') and not attrs['elevator'].active:
            raise ValidationError({'elevator':'Лифт находится в архиве.'})
        return attrs
    class Meta:
        model = PersonalTask
        fields = ['id','title','description','priority','status','assigned_to','technician_name','elevator','address',
                  'created_by_name','due_at','result','cancellation_reason','accepted_at','completed_at','created_at',
                  'updated_at','can_accept','can_complete','can_cancel','attachments','history']
        read_only_fields = ['id','status','result','cancellation_reason','accepted_at','completed_at','created_at','updated_at']


class TaskViewSet(ModelViewSet):
    permission_classes = [MemberAccess]
    serializer_class = TaskSerializer
    queryset = PersonalTask.objects.select_related('assigned_to','created_by','elevator__building').prefetch_related('attachments__asset')
    http_method_names = ['get','post','patch','head','options']
    def create(self,request,*args,**kwargs):
        if not manager(request.user):
            raise PermissionDenied('Личное задание назначает руководитель или диспетчер.')
        return super().create(request,*args,**kwargs)
    def get_queryset(self):
        rows = super().get_queryset().filter(organization=org(self.request.user))
        if not manager(self.request.user):
            rows = rows.filter(assigned_to_id=technician_id(self.request.user)) if role(self.request.user) == 'MECHANIC' else rows.none()
        return rows
    def perform_create(self, serializer):
        if not manager(self.request.user):
            raise PermissionDenied('Личное задание назначает руководитель или диспетчер.')
        with transaction.atomic():
            task = serializer.save(organization=org(self.request.user),created_by=self.request.user)
            event(task,self.request.user,'created')
    def update(self, request, *args, **kwargs):
        if not manager(request.user):
            raise PermissionDenied()
        with transaction.atomic():
            task = get_object_or_404(self.get_queryset().select_for_update(of=('self',)),pk=kwargs['pk'])
            if task.status != 'NEW':
                raise ValidationError('Изменять можно только ещё не принятое задание.')
            serializer = self.get_serializer(task,data=request.data,partial=True)
            serializer.is_valid(raise_exception=True)
            previous = task.assigned_to_id
            task = serializer.save()
            event(task,request.user,'assigned' if previous != task.assigned_to_id else 'updated')
            return Response(self.get_serializer(task).data)
    def _transition(self, request, pk, action):
        with transaction.atomic():
            task = get_object_or_404(self.get_queryset().select_for_update(of=('self',)),pk=pk)
            text = request.data.get('body','')
            if not isinstance(text,str):
                raise ValidationError('Текст должен быть строкой.')
            text = text.strip()
            if action == 'accepted':
                if task.status != 'NEW' or not assignee(request.user,task):
                    raise PermissionDenied('Принять новое задание может только назначенный механик.')
                task.status, task.accepted_at = 'IN_PROGRESS', timezone.now()
            elif action == 'completed':
                if task.status != 'IN_PROGRESS' or not assignee(request.user,task):
                    raise PermissionDenied('Завершить задание может только его исполнитель после принятия.')
                if not 3 <= len(text) <= 4000:
                    raise ValidationError('Укажите выполненные работы (3–4000 символов).')
                task.status, task.result, task.completed_at = 'DONE', text, timezone.now()
            elif action == 'canceled':
                if not manager(request.user) or task.status not in ('NEW','IN_PROGRESS'):
                    raise PermissionDenied('Отменить незавершённое задание может руководитель или диспетчер.')
                if not 5 <= len(text) <= 1000:
                    raise ValidationError('Укажите причину отмены (5–1000 символов).')
                task.status, task.cancellation_reason = 'CANCELED', text
            elif action == 'commented':
                if task.status not in ('NEW','IN_PROGRESS') or not 1 <= len(text) <= 4000:
                    raise ValidationError('Комментарий до 4000 символов доступен в незавершённом задании.')
            task.save()
            event(task,request.user,action,text)
            return Response(self.get_serializer(task).data)
    @action(detail=True,methods=['post'])
    def accept(self,request,pk=None):
        return self._transition(request,pk,'accepted')
    @action(detail=True,methods=['post'])
    def complete(self,request,pk=None):
        return self._transition(request,pk,'completed')
    @action(detail=True,methods=['post'])
    def cancel(self,request,pk=None):
        return self._transition(request,pk,'canceled')
    @action(detail=True,methods=['post'])
    def comment(self,request,pk=None):
        return self._transition(request,pk,'commented')
    @action(detail=True,methods=['post'])
    def attachments(self,request,pk=None):
        task = self.get_object()
        if task.status not in ('NEW','IN_PROGRESS') or not (manager(request.user) or assignee(request.user,task)):
            raise PermissionDenied('Завершённое задание доступно только для чтения.')
        from .media_vault import atomic_media, ingest, link_asset
        with atomic_media():
            from .models import Organization
            Organization.objects.select_for_update(no_key=True).get(pk=task.organization_id)
            locked = PersonalTask.objects.select_for_update(no_key=True).get(pk=task.pk)
            if not (manager(request.user) or assignee(request.user,locked)):
                raise PermissionDenied('Исполнитель задания изменился. Обновите список.')
            if locked.status not in ('NEW','IN_PROGRESS') or locked.attachments.count()>=20:
                raise ValidationError('Задание уже завершено или достигнут предел 20 фотографий.')
            asset = ingest(request.user,request.FILES.get('file'),video=False)
            written = PersonalTaskAttachment.objects.create(organization=task.organization,task=task,asset=asset,
                file=asset.file.name,content_type=asset.content_type,size=asset.size)
            link_asset(request.user,asset,'task',locked,'RESULT',check=False)
            event(task,request.user,'photo_added')
        return Response(attachment_data(written,task.pk,'tasks'),status=201)
    @action(detail=True,methods=['get'],url_path=r'attachments/(?P<attachment_id>\d+)')
    def attachment(self,request,pk=None,attachment_id=None):
        task = self.get_object()
        row = get_object_or_404(PersonalTaskAttachment,pk=attachment_id,task=task,organization=org(request.user))
        return serve(row,request)
