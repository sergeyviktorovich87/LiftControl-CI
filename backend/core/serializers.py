from django.utils import timezone
from rest_framework import serializers
from .models import Building, Elevator, Maintenance, Technician, Ticket, DutyShift, PartRequest, ChatMessage, Document
from .access import org, manager, technician_id


class TenantSerializer(serializers.ModelSerializer):
    def validate(self, attrs):
        organization = org(self.context['request'].user)
        for key in ('building','elevator','assigned_technician','assigned_to','technician'):
            item = attrs.get(key)
            if item is not None and item.organization_id != organization.id:
                raise serializers.ValidationError({key: 'Объект другой организации недоступен.'})
        return attrs


class BuildingSerializer(TenantSerializer):
    elevators_count = serializers.IntegerField(source='elevators.count', read_only=True)
    class Meta:
        model = Building
        fields = '__all__'
        read_only_fields = ['organization']


class TechnicianSerializer(TenantSerializer):
    role_display = serializers.CharField(source='get_role_display', read_only=True)
    can_receive_tasks = serializers.SerializerMethodField()
    def get_can_receive_tasks(self, obj):
        from .models import Membership
        return obj.active and Membership.objects.filter(technician=obj,organization=obj.organization,role='MECHANIC',active=True,user__is_active=True).exists()
    def validate(self, attrs):
        attrs = super().validate(attrs)
        if self.instance and self.instance.active and attrs.get('active') is False:
            raise serializers.ValidationError({'active': 'Используйте «Удалить из списка» с причиной и передачей назначений.'})
        return attrs
    class Meta:
        model = Technician
        exclude = ['last_latitude','last_longitude','last_location_at']
        read_only_fields = ['organization']


class ElevatorSerializer(TenantSerializer):
    address = serializers.CharField(source='building.address', read_only=True)
    building_name = serializers.CharField(source='building.name', read_only=True)
    technician_name = serializers.CharField(source='assigned_technician.full_name', read_only=True, default='')
    def validate(self, attrs):
        attrs=super().validate(attrs)
        if self.instance and self.instance.active and attrs.get('active') is False:
            raise serializers.ValidationError({'active': 'Используйте «Удалить из списка» с обязательной причиной.'})
        organization=org(self.context['request'].user)
        for key in ('factory_number','inventory_number'):
            value=attrs.get(key,getattr(self.instance,key,None))
            if value and Elevator.objects.filter(organization=organization,**{key:value}).exclude(pk=getattr(self.instance,'pk',None)).exists():
                raise serializers.ValidationError({key:'Этот номер уже занят в организации.'})
        return attrs

    def validate_factory_number(self, value):
        return value.strip() or None if value else None

    class Meta:
        model = Elevator
        fields = '__all__'
        read_only_fields = ['organization', 'stopped_at', 'stop_reason']


class TicketSerializer(TenantSerializer):
    assigned_to = serializers.PrimaryKeyRelatedField(queryset=Technician.objects.all(), required=False, allow_null=True)
    address = serializers.CharField(source='elevator.building.address', read_only=True)
    elevator_factory_number = serializers.CharField(source='elevator.factory_number', read_only=True)
    elevator_label = serializers.CharField(source='elevator.display_number', read_only=True)
    technician_name = serializers.CharField(source='assigned_to.full_name', read_only=True, default='')
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    created_by_name = serializers.SerializerMethodField()
    photos = serializers.SerializerMethodField()
    can_accept = serializers.SerializerMethodField()
    can_complete = serializers.SerializerMethodField()
    can_cancel = serializers.SerializerMethodField()
    can_stop = serializers.SerializerMethodField()
    can_resume = serializers.SerializerMethodField()
    can_assign = serializers.SerializerMethodField()

    def get_can_assign(self, obj):
        from .ticket_flow import can_assign_ticket
        return obj.status == Ticket.Status.NEW and can_assign_ticket(self.context['request'].user)

    def get_can_stop(self, obj):
        from .ticket_flow import can_stop_ticket
        return can_stop_ticket(self.context['request'].user, obj)

    def get_can_resume(self, obj):
        return obj.status in (Ticket.Status.STOPPED, Ticket.Status.LONG_REPAIR) and self.get_can_complete(obj)

    def get_created_by_name(self, obj):
        actor = obj.created_by
        return actor.get_full_name() or actor.username if actor else ''

    def get_photos(self, obj):
        photos = getattr(obj, 'current_photos', None)
        if photos is None:
            photos = obj.photos.filter(deleted_at__isnull=True)
        return [{'id': p.pk, 'asset_id': str(p.asset_id) if p.asset_id else None, 'url': f'/api/tickets/{obj.pk}/photos/{p.pk}/?organization={obj.organization_id}',
                 'created_at': p.created_at,
                 'uploaded_by': p.uploaded_by.get_full_name() or p.uploaded_by.username
                                if p.uploaded_by_id else 'Система'}
                for p in photos]

    def get_can_accept(self, obj):
        from .ticket_flow import can_accept_ticket
        return can_accept_ticket(self.context['request'].user, obj)

    def get_can_complete(self, obj):
        from .ticket_flow import can_finish_ticket
        return can_finish_ticket(self.context['request'].user, obj)

    def get_can_cancel(self, obj):
        from .access import role
        from .ticket_flow import is_on_duty
        user = self.context['request'].user
        current_role = role(user)
        # Match the server transition rule; no cancellation button on archived tickets.
        if obj.status == Ticket.Status.CANCELED:
            return False
        if obj.status == Ticket.Status.DONE:
            return current_role in ('ADMIN', 'DIRECTOR')
        if current_role in ('ADMIN', 'DIRECTOR', 'DISPATCHER'):
            return True
        if '_on_duty_for_ticket_list' not in self.context:
            self.context['_on_duty_for_ticket_list'] = is_on_duty(user)
        return self.context['_on_duty_for_ticket_list']

    class Meta:
        model = Ticket
        fields = '__all__'
        read_only_fields = [
            'organization', 'created_by', 'status',
            'repair', 'completed_at', 'accepted_at', 'canceled_at',
            'canceled_by', 'cancellation_reason', 'created_at', 'updated_at',
            'stopped_at', 'stop_reason', 'long_repair_reason', 'expected_repair_date',
        ]

    def validate(self, attrs):
        return super().validate(attrs)


class MaintenanceSerializer(TenantSerializer):
    address = serializers.CharField(source='elevator.building.address', read_only=True)
    elevator_factory_number = serializers.CharField(source='elevator.factory_number', read_only=True)
    elevator_label = serializers.CharField(source='elevator.display_number', read_only=True)
    technician_name = serializers.CharField(source='technician.full_name', read_only=True, default='')
    class Meta:
        model = Maintenance
        fields = '__all__'
        read_only_fields = ['organization']
    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs.get('completed_at',getattr(self.instance,'completed_at',None)) and not attrs.get('notes',getattr(self.instance,'notes','')).strip():
            raise serializers.ValidationError('Для выполненной работы нужен результат в журнале.')
        if not manager(self.context['request'].user):
            tid = technician_id(self.context['request'].user)
            if self.instance and self.instance.technician_id != tid:
                raise serializers.ValidationError('Недоступно редактирование чужой записи.')
            if self.instance and any(k not in ('notes','completed_at') for k in attrs):
                raise serializers.ValidationError('Можно изменить только результат и время выполнения.')
            if not self.instance:
                attrs.pop('technician',None)
                attrs['technician_id'] = tid
        return attrs


class DutySerializer(TenantSerializer):
    technician_name = serializers.CharField(source='technician.full_name', read_only=True)
    class Meta:
        model = DutyShift
        fields = '__all__'
        read_only_fields = ['organization','updated_at','acknowledged_at','acknowledged_by']
    def validate(self, attrs):
        attrs = super().validate(attrs)
        start = attrs.get('start',getattr(self.instance,'start',None))
        end = attrs.get('end',getattr(self.instance,'end',None))
        if not start or not end or not 0 < (end-start).total_seconds() <= 86400:
            raise serializers.ValidationError('Дежурство должно длиться от 1 минуты до 24 часов.')
        tech = attrs.get('technician',getattr(self.instance,'technician',None))
        if not tech or not tech.active:
            raise serializers.ValidationError('Выберите действующего механика.')
        q = DutyShift.objects.filter(organization=org(self.context['request'].user),start__lt=end,end__gt=start)
        if self.instance:
            q = q.exclude(pk=self.instance.pk)
        if q.exists():
            raise serializers.ValidationError('В организации уже назначен дежурный на это время.')
        return attrs


class PartSerializer(TenantSerializer):
    address = serializers.CharField(source='elevator.building.address', read_only=True)
    elevator_factory_number = serializers.CharField(source='elevator.factory_number', read_only=True)
    elevator_label = serializers.CharField(source='elevator.display_number', read_only=True)
    author_name = serializers.SerializerMethodField()
    photos = serializers.SerializerMethodField()
    can_edit = serializers.SerializerMethodField()

    def get_author_name(self, obj):
        return (obj.requested_by.get_full_name() or obj.requested_by.username) if obj.requested_by else 'Удалённый пользователь'

    def get_can_edit(self, obj):
        user = self.context['request'].user
        return (obj.status in ('NEW', 'APPROVED') and manager(user)) or (
            obj.status == 'NEW' and obj.requested_by_id == user.pk)

    def get_photos(self, obj):
        return [
            {'id': row.pk, 'url': f'/api/parts/{obj.pk}/photos/{row.pk}/?organization={obj.organization_id}',
             'name': row.asset.original_name, 'content_type': row.asset.content_type,
             'created_at': row.created_at}
            for row in obj.photos.all() if row.deleted_at is None
        ]

    class Meta:
        model = PartRequest
        fields = '__all__'
        read_only_fields = ['organization', 'requested_by', 'created_at', 'updated_at']

    def validate(self, attrs):
        attrs = super().validate(attrs)
        user = self.context['request'].user
        if not manager(user):
            if self.instance and (self.instance.requested_by_id != user.id or self.instance.status != 'NEW'):
                raise serializers.ValidationError('Можно редактировать только свою новую заявку.')
            if 'status' in attrs and attrs['status'] != 'NEW':
                raise serializers.ValidationError('Статус меняет руководитель или диспетчер.')
        if attrs.get('quantity', 1) < 1:
            raise serializers.ValidationError({'quantity': 'Количество должно быть больше нуля.'})
        if self.instance:
            changed = any(k in attrs and attrs[k] != getattr(self.instance, k)
                          for k in ('elevator', 'part', 'quantity', 'notes'))
            if changed and self.instance.status in ('ORDERED', 'RECEIVED', 'ISSUED'):
                raise serializers.ValidationError('Заказ уже отправлен поставщику. Создайте отдельную корректировку.')
            if changed and self.instance.status == 'APPROVED':
                attrs['status'] = 'NEW'  # Substantive edits invalidate prior approval.
        return attrs


class ChatSerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField()
    attachments = serializers.SerializerMethodField()
    linked_ticket = serializers.PrimaryKeyRelatedField(queryset=Ticket.objects.all(), required=False, allow_null=True)
    linked_part = serializers.PrimaryKeyRelatedField(queryset=PartRequest.objects.all(), required=False, allow_null=True)
    reference = serializers.SerializerMethodField()

    def get_attachments(self, obj):
        from .media import attachment_data
        return [attachment_data(row, obj.pk, 'chat') for row in obj.attachments.all()]

    def get_author_name(self, obj):
        return (obj.author.get_full_name() or obj.author.username) if obj.author else 'Удалённый пользователь'

    def get_reference(self, obj):
        if obj.linked_ticket_id and obj.linked_ticket and obj.linked_ticket.organization_id == obj.organization_id:
            ticket = obj.linked_ticket
            return {'kind': 'ticket', 'id': ticket.pk, 'title': ticket.title,
                    'status': ticket.status, 'address': ticket.elevator.building.address,
                    'elevator': ticket.elevator.display_number}
        if obj.linked_part_id and obj.linked_part and obj.linked_part.organization_id == obj.organization_id:
            part = obj.linked_part
            return {'kind': 'part', 'id': part.pk, 'title': part.part,
                    'status': part.status, 'address': part.elevator.building.address,
                    'elevator': part.elevator.display_number, 'quantity': part.quantity}
        return None

    def validate(self, attrs):
        ticket = attrs.get('linked_ticket')
        part = attrs.get('linked_part')
        if ticket is not None and part is not None:
            raise serializers.ValidationError('Укажите одну связанную запись.')
        company = org(self.context['request'].user)
        if (ticket is not None and ticket.organization_id != company.pk or
                part is not None and part.organization_id != company.pk):
            raise serializers.ValidationError('Связанная запись недоступна в выбранной организации.')
        return attrs

    class Meta:
        model = ChatMessage
        fields = ['id', 'body', 'author', 'author_name', 'created_at', 'attachments',
                  'linked_ticket', 'linked_part', 'reference']
        read_only_fields = ['id', 'author', 'created_at']


class DocumentSerializer(TenantSerializer):
    url = serializers.SerializerMethodField()
    file = serializers.FileField(write_only=True,required=False)
    def validate_file(self, value):
        if not 0 < value.size <= 20*1024*1024 or value.read(5) != b'%PDF-':
            raise serializers.ValidationError('Выберите PDF до 20 МБ.')
        value.seek(0)
        return value
    def get_url(self,obj):
        return f'/api/documents/{obj.pk}/download/?organization={obj.organization_id}'
    class Meta:
        model = Document
        fields = ['id','title','manufacturer','model','url','file','created_at','updated_at']
        read_only_fields = ['created_at','updated_at']

