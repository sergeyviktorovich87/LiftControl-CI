from django.db import models
from django.conf import settings
from django.utils import timezone
from django.core.exceptions import ValidationError
import uuid


class MobileSession(models.Model):
    """Device-specific opaque credentials; the bearer secret is never stored."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='mobile_sessions')
    token_hash = models.CharField(max_length=64, unique=True)
    password_stamp = models.CharField(max_length=64)
    device_name = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    last_seen_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)


class MobileLoginLimit(models.Model):
    """Shared database rate limits also work across Gunicorn processes."""
    key = models.CharField(max_length=64, primary_key=True)
    attempts = models.PositiveIntegerField(default=0)
    resets_at = models.DateTimeField(db_index=True)


class Organization(models.Model):
    name = models.CharField(max_length=200)
    timezone = models.CharField(max_length=64, default="Asia/Yakutsk")
    duty_start = models.TimeField(default="09:00")
    def __str__(self):
        return self.name



class Building(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, default=1, db_default=1)
    name = models.CharField('Объект', max_length=200)
    street = models.CharField('Улица', max_length=200, blank=True, db_default='')
    house = models.CharField('Дом', max_length=50, blank=True, db_default='')
    address = models.CharField('Адрес', max_length=300, db_index=True)
    management_company = models.CharField('Управляющая организация', max_length=200, blank=True)
    contact_name = models.CharField('Контактное лицо', max_length=150, blank=True)
    contact_phone = models.CharField('Телефон', max_length=50, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['address']
        verbose_name = 'Объект'
        verbose_name_plural = 'Объекты'

    def __str__(self):
        return f'{self.address} — {self.name}'


class Technician(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, default=1, db_default=1)
    class Role(models.TextChoices):
        MECHANIC = 'MECHANIC', 'Механик'
        ADJUSTER = 'ADJUSTER', 'Наладчик'
        DISPATCHER = 'DISPATCHER', 'Диспетчер'
        ENGINEER = 'ENGINEER', 'Инженер'

    full_name = models.CharField('ФИО', max_length=200)
    phone = models.CharField('Телефон', max_length=50, blank=True)
    role = models.CharField('Роль', max_length=20, choices=Role.choices, default=Role.MECHANIC)
    active = models.BooleanField('Активен', default=True)
    last_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    last_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    last_location_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['full_name']
        verbose_name = 'Сотрудник'
        verbose_name_plural = 'Сотрудники'

    def __str__(self):
        return self.full_name


class Elevator(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, default=1, db_default=1)
    class ElevatorType(models.TextChoices):
        PASSENGER = 'PASSENGER', 'Пассажирский'
        FREIGHT = 'FREIGHT', 'Грузовой'
        HOSPITAL = 'HOSPITAL', 'Больничный'
        SERVICE = 'SERVICE', 'Малый грузовой'
        PLATFORM = 'PLATFORM', 'Подъёмник'
        UNKNOWN = 'UNKNOWN', 'Не уточнён'

    building = models.ForeignKey(Building, on_delete=models.PROTECT, related_name='elevators')
    factory_number = models.CharField('Заводской номер', max_length=100, null=True, blank=True)
    inventory_number = models.CharField('Учётный номер', max_length=100, blank=True)
    source_reference = models.CharField(max_length=200, blank=True)
    model = models.CharField('Модель', max_length=150, blank=True)
    manufacturer = models.CharField('Производитель', max_length=150, blank=True)
    entrance = models.CharField('Подъезд', max_length=50, blank=True)
    floors = models.PositiveSmallIntegerField('Остановок', null=True, blank=True)
    building_floors = models.PositiveSmallIntegerField('Этажность по исходной таблице', null=True, blank=True)
    capacity_kg = models.PositiveIntegerField('Грузоподъёмность, кг', null=True, blank=True)
    elevator_type = models.CharField('Тип', max_length=20, choices=ElevatorType.choices, default=ElevatorType.PASSENGER)
    assigned_technician = models.ForeignKey(Technician, on_delete=models.SET_NULL, null=True, blank=True, related_name='elevators')
    active = models.BooleanField('На обслуживании', default=True)
    notes = models.TextField('Примечание', blank=True)
    stopped_at = models.DateTimeField(null=True, blank=True)
    stop_reason = models.TextField(blank=True, default='', db_default='', max_length=1000)

    class Meta:
        ordering = ['building__address', 'entrance', 'factory_number']
        constraints = [models.UniqueConstraint(fields=['organization','factory_number'], name='organization_factory_number'),
            models.UniqueConstraint(fields=['organization','inventory_number'], condition=~models.Q(inventory_number=''), name='organization_inventory_number')]
        verbose_name = 'Лифт'
        verbose_name_plural = 'Лифты'

    @property
    def display_number(self):
        return self.factory_number or ('Учётный ' + self.inventory_number if self.inventory_number else f'Запись {self.pk}')

    def __str__(self):
        return f'{self.factory_number or self.inventory_number or self.pk} — {self.building.address}'


class Ticket(models.Model):
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_tickets')
    accepted_at = models.DateTimeField(null=True, blank=True)
    canceled_at = models.DateTimeField(null=True, blank=True)
    canceled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='canceled_tickets')
    cancellation_reason = models.TextField(blank=True, max_length=1000, default='', db_default='')
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, default=1, db_default=1)
    class Priority(models.TextChoices):
        LOW = 'LOW', 'Низкий'
        NORMAL = 'NORMAL', 'Обычный'
        HIGH = 'HIGH', 'Высокий'
        EMERGENCY = 'EMERGENCY', 'Аварийный'
        ENTRAPMENT = 'ENTRAPMENT', 'Застревание'

    class Status(models.TextChoices):
        NEW = 'NEW', 'Новая'
        ASSIGNED = 'ASSIGNED', 'Назначена'
        IN_PROGRESS = 'IN_PROGRESS', 'В работе'
        STOPPED = 'STOPPED', 'Лифт остановлен'
        LONG_REPAIR = 'LONG_REPAIR', 'Длительный ремонт'
        DONE = 'DONE', 'Выполнена'
        CANCELED = 'CANCELED', 'Отменена'

    elevator = models.ForeignKey(Elevator, on_delete=models.PROTECT, related_name='tickets')
    repair = models.TextField('Выполненные работы', blank=True, max_length=4000, db_default='')
    title = models.CharField('Кратко', max_length=200)
    description = models.TextField('Описание', blank=True)
    priority = models.CharField('Приоритет', max_length=20, choices=Priority.choices, default=Priority.NORMAL)
    status = models.CharField('Статус', max_length=20, choices=Status.choices, default=Status.NEW, db_index=True)
    assigned_to = models.ForeignKey(Technician, on_delete=models.SET_NULL, null=True, blank=True, related_name='tickets')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    stopped_at = models.DateTimeField(null=True, blank=True)
    stop_reason = models.TextField(blank=True, default='', db_default='', max_length=1000)
    long_repair_reason = models.TextField(blank=True, default='', db_default='', max_length=1000)
    expected_repair_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Заявка'
        verbose_name_plural = 'Заявки'

    def __str__(self):
        return f'#{self.pk or "—"} {self.title}'


class Maintenance(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, default=1, db_default=1)
    class Kind(models.TextChoices):
        TO1 = 'TO1', 'ТО-1'
        TO2 = 'TO2', 'ТО-2'
        REPAIR = 'REPAIR', 'Ремонт'
        INSPECTION = 'INSPECTION', 'Осмотр'

    elevator = models.ForeignKey(Elevator, on_delete=models.PROTECT, related_name='maintenance')
    technician = models.ForeignKey(Technician, on_delete=models.SET_NULL, null=True, blank=True, related_name='maintenance')
    kind = models.CharField('Вид работы', max_length=20, choices=Kind.choices, default=Kind.TO1)
    planned_date = models.DateField('Плановая дата', db_index=True)
    completed_at = models.DateTimeField('Выполнено', null=True, blank=True)
    notes = models.TextField('Результат/замечания', blank=True)

    class Meta:
        ordering = ['planned_date']
        verbose_name = 'ТО'
        verbose_name_plural = 'ТО'

    def __str__(self):
        return f'{self.get_kind_display()} {self.elevator} — {self.planned_date}'



class Membership(models.Model):
    class Role(models.TextChoices):
        ADMIN = 'ADMIN', 'Администратор'
        DIRECTOR = 'DIRECTOR', 'Директор'
        DISPATCHER = 'DISPATCHER', 'Диспетчер'
        MECHANIC = 'MECHANIC', 'Механик'
        ADJUSTER = 'ADJUSTER', 'Приглашённый наладчик'
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='memberships')
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MECHANIC)
    technician = models.OneToOneField(Technician, null=True, blank=True, on_delete=models.SET_NULL)
    active = models.BooleanField(default=True)
    can_commissioning = models.BooleanField(default=False)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'organization'], name='unique_user_organization')]

    def clean(self):
        if self.technician and self.technician.organization_id != self.organization_id:
            raise ValidationError('Сотрудник должен принадлежать той же организации.')
        if self.role == self.Role.MECHANIC and not self.technician:
            raise ValidationError('Для механика выберите сотрудника.')


def private_path(instance, filename):
    return f'{instance.organization_id}/{uuid.uuid4().hex}'


class TicketPhoto(models.Model):
    asset = models.ForeignKey('MediaAsset', null=True, blank=True, on_delete=models.PROTECT, related_name='legacy_photos')
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    ticket = models.ForeignKey(Ticket, on_delete=models.PROTECT, related_name='photos')
    file = models.FileField(upload_to=private_path)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    deleted_at = models.DateTimeField(null=True, blank=True)


class DutyShift(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    technician = models.ForeignKey(Technician, on_delete=models.PROTECT)
    start = models.DateTimeField(db_index=True)
    end = models.DateTimeField(db_index=True)
    kind = models.CharField(max_length=20, default='daily', choices=[('daily','Сутки'),('day','День'),('night','Ночь'),('custom','Своя смена')])
    updated_at = models.DateTimeField(auto_now=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    class Meta:
        ordering = ['start','id']
        constraints = [models.CheckConstraint(condition=models.Q(end__gt=models.F('start')), name='duty_end_after_start')]


class PartRequest(models.Model):
    class Status(models.TextChoices):
        NEW = 'NEW', 'Новая'
        APPROVED = 'APPROVED', 'Согласована'
        ORDERED = 'ORDERED', 'Заказана'
        RECEIVED = 'RECEIVED', 'Получена'
        ISSUED = 'ISSUED', 'Выдана'
        CANCELED = 'CANCELED', 'Отменена'
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    elevator = models.ForeignKey(Elevator, on_delete=models.PROTECT)
    part = models.CharField('Запчасть', max_length=250)
    quantity = models.PositiveSmallIntegerField('Количество', default=1)
    notes = models.TextField(blank=True, max_length=2000)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ['-created_at']


class PartPhoto(models.Model):
    """An immutable media asset attached to an individual spare-part request."""
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    part = models.ForeignKey(PartRequest, on_delete=models.PROTECT, related_name='photos')
    asset = models.ForeignKey('MediaAsset', on_delete=models.PROTECT, related_name='part_photos')
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['part', 'asset'], name='unique_part_photo_asset')]


class ChatMessage(models.Model):
    client_id = models.UUIDField(null=True, blank=True)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    body = models.TextField(max_length=4000, blank=True)
    linked_ticket = models.ForeignKey(Ticket, null=True, blank=True, on_delete=models.SET_NULL, related_name='chat_references')
    linked_part = models.ForeignKey(PartRequest, null=True, blank=True, on_delete=models.SET_NULL, related_name='chat_references')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    class Meta:
        ordering = ['created_at','id']
        constraints = [models.UniqueConstraint(fields=['organization','author','client_id'], name='chat_client_message_id')]


class ChatAttachment(models.Model):
    asset = models.ForeignKey('MediaAsset', null=True, blank=True, on_delete=models.PROTECT, related_name='legacy_chat')
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    message = models.ForeignKey(ChatMessage, on_delete=models.PROTECT, related_name='attachments')
    file = models.FileField(upload_to=private_path)
    content_type = models.CharField(max_length=40)
    size = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)


class Document(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    title = models.CharField(max_length=200)
    manufacturer = models.CharField(max_length=150)
    model = models.CharField(max_length=150, blank=True)
    file = models.FileField(upload_to=private_path)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(default=timezone.now)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deletion_reason = models.CharField(max_length=1000, blank=True)
    deleted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    class Meta:
        ordering = ['title']


class MobileSyncReceipt(models.Model):
    """Commit the business change and its receipt in the same transaction."""
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    operation_id = models.UUIDField()
    payload_hash = models.CharField(max_length=64)
    ticket = models.ForeignKey(Ticket, on_delete=models.PROTECT)
    result = models.JSONField(default=dict)
    occurred_at = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['organization', 'user', 'operation_id'], name='mobile_sync_unique_operation')]


class DocumentRevision(models.Model):
    document = models.ForeignKey(Document, on_delete=models.PROTECT, related_name='revisions')
    title = models.CharField(max_length=200)
    manufacturer = models.CharField(max_length=150)
    model = models.CharField(max_length=150, blank=True)
    file = models.FileField(upload_to=private_path)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)


class PersonalTask(models.Model):
    class Status(models.TextChoices):
        NEW = 'NEW', 'Новое'
        IN_PROGRESS = 'IN_PROGRESS', 'В работе'
        DONE = 'DONE', 'Выполнено'
        CANCELED = 'CANCELED', 'Отменено'
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    assigned_to = models.ForeignKey(Technician, on_delete=models.PROTECT, related_name='personal_tasks')
    elevator = models.ForeignKey(Elevator, null=True, blank=True, on_delete=models.PROTECT, related_name='personal_tasks')
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, max_length=4000)
    priority = models.CharField(max_length=20, default='NORMAL', choices=[('LOW','Низкий'),('NORMAL','Обычный'),('HIGH','Высокий')])
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    due_at = models.DateTimeField(null=True, blank=True)
    result = models.TextField(blank=True, max_length=4000)
    cancellation_reason = models.TextField(blank=True, max_length=1000)
    accepted_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ['-created_at', '-id']


class PersonalTaskEvent(models.Model):
    task = models.ForeignKey(PersonalTask, on_delete=models.PROTECT, related_name='events')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=32)
    reason = models.TextField(blank=True, max_length=4000)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['id']


class PersonalTaskAttachment(models.Model):
    asset = models.ForeignKey('MediaAsset', null=True, blank=True, on_delete=models.PROTECT, related_name='legacy_tasks')
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    task = models.ForeignKey(PersonalTask, on_delete=models.PROTECT, related_name='attachments')
    file = models.FileField(upload_to=private_path)
    content_type = models.CharField(max_length=40)
    size = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)


class NotificationPreference(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    ordinary = models.BooleanField(default=True)
    urgent = models.BooleanField(default=True)
    entrapment = models.BooleanField(default=True)
    updates = models.BooleanField(default=True)
    tasks = models.BooleanField(default=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['organization','user'], name='notification_preference_user_org')]


class PersonalTaskPush(models.Model):
    event = models.ForeignKey(PersonalTaskEvent, on_delete=models.PROTECT)
    device = models.ForeignKey('NativePushDevice', null=True, on_delete=models.CASCADE)
    subscription = models.ForeignKey('PushSubscription', null=True, on_delete=models.CASCADE)
    state = models.CharField(max_length=16, default='PENDING', db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [
            models.CheckConstraint(condition=(models.Q(device__isnull=False,subscription__isnull=True) | models.Q(device__isnull=True,subscription__isnull=False)), name='task_push_one_recipient'),
            models.UniqueConstraint(fields=['event','device'], name='task_event_native_device'),
            models.UniqueConstraint(fields=['event','subscription'], name='task_event_web_device'),
        ]


class AuditEvent(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=80)
    details = models.JSONField(default=dict, blank=True)
    object_id = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at']


def vault_path(instance, filename):
    return f'org_{instance.organization_id}/{instance.created_at:%Y/%m}/{instance.pk}/{filename}'


class MediaAsset(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    captured_at = models.DateTimeField(null=True, blank=True)
    original_name = models.CharField(max_length=180)
    file = models.FileField(upload_to=vault_path, max_length=250)
    preview = models.FileField(upload_to=vault_path, max_length=250, blank=True)
    preview_size = models.PositiveIntegerField(default=0)
    content_type = models.CharField(max_length=40)
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    state = models.CharField(max_length=16, default='READY')
    class Meta:
        constraints = [models.UniqueConstraint(fields=['organization','sha256'], name='media_tenant_digest')]
        ordering = ['-created_at','id']


class MediaLink(models.Model):
    asset = models.ForeignKey(MediaAsset, on_delete=models.PROTECT, related_name='links')
    ticket = models.ForeignKey(Ticket, null=True, blank=True, on_delete=models.PROTECT, related_name='media_links')
    elevator = models.ForeignKey(Elevator, null=True, blank=True, on_delete=models.PROTECT, related_name='media_links')
    message = models.ForeignKey(ChatMessage, null=True, blank=True, on_delete=models.PROTECT, related_name='media_links')
    task = models.ForeignKey(PersonalTask, null=True, blank=True, on_delete=models.PROTECT, related_name='media_links')
    category = models.CharField(max_length=16, default='OTHER', choices=[('DEFECT','Неисправность'),('PROGRESS','Ход работ'),('RESULT','Результат ремонта'),('OTHER','Прочее')])
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(default=timezone.now)
    class Meta:
        constraints = [
            models.CheckConstraint(condition=(models.Q(ticket__isnull=False,elevator__isnull=True,message__isnull=True,task__isnull=True)|models.Q(ticket__isnull=True,elevator__isnull=False,message__isnull=True,task__isnull=True)|models.Q(ticket__isnull=True,elevator__isnull=True,message__isnull=False,task__isnull=True)|models.Q(ticket__isnull=True,elevator__isnull=True,message__isnull=True,task__isnull=False)), name='media_one_link_target'),
            *[models.UniqueConstraint(fields=['asset',target],name=f'media_unique_{target}') for target in ('ticket','elevator','message','task')],
        ]


class MediaUploadReceipt(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    operation_id = models.UUIDField()
    fingerprint = models.CharField(max_length=64)
    result = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['organization','user','operation_id'], name='media_upload_operation')]


class TicketEvent(models.Model):
    """Append-only ticket timeline: never edit or delete an event via the API."""
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    ticket = models.ForeignKey(Ticket, on_delete=models.PROTECT, related_name='events')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=32)
    before = models.CharField(max_length=20, blank=True, default='')
    after = models.CharField(max_length=20, blank=True, default='')
    reason = models.TextField(blank=True, default='', max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['created_at', 'pk']


class TicketComment(models.Model):
    """Immutable work notes and repair reports, including author and timestamp."""
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    ticket = models.ForeignKey(Ticket, on_delete=models.PROTECT, related_name='comments')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    body = models.TextField(max_length=4000)
    kind = models.CharField(max_length=20, default='NOTE')
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['created_at', 'pk']


class PushSubscription(models.Model):
    """Treat endpoint and encryption keys as secrets; never expose via general API."""
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='push_subscriptions')
    endpoint = models.URLField(max_length=2048, unique=True)
    p256dh = models.CharField(max_length=255)
    auth = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)


class PushDelivery(models.Model):
    """Durable queue, one row per device. Worker retries temporary failures."""
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    subscription = models.ForeignKey(PushSubscription, on_delete=models.CASCADE, related_name='deliveries')
    ticket = models.ForeignKey(Ticket, on_delete=models.PROTECT)
    kind = models.CharField(max_length=32)
    state = models.CharField(max_length=16, default='PENDING')
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        indexes = [models.Index(fields=['state', 'next_attempt_at'], name='push_pending_idx')]


class NativePushDevice(models.Model):
    session = models.OneToOneField(MobileSession, on_delete=models.CASCADE, related_name='push_device')
    token = models.CharField(max_length=2048, unique=True)
    active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)


class NativePushDelivery(models.Model):
    device = models.ForeignKey(NativePushDevice, on_delete=models.CASCADE)
    ticket = models.ForeignKey(Ticket, on_delete=models.PROTECT)
    event = models.ForeignKey(TicketEvent, on_delete=models.PROTECT)
    kind = models.CharField(max_length=32)
    state = models.CharField(max_length=16, default='PENDING', db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['device', 'event'], name='native_push_device_event')]



class AndroidUpdateAnnouncement(models.Model):
    """One immutable, verified Android release announced to devices via FCM."""
    version_code = models.PositiveIntegerField(unique=True)
    version = models.CharField(max_length=40)
    sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)


class AndroidUpdatePushDelivery(models.Model):
    announcement = models.ForeignKey(AndroidUpdateAnnouncement, on_delete=models.PROTECT)
    device = models.ForeignKey(NativePushDevice, on_delete=models.CASCADE)
    state = models.CharField(max_length=16, default='PENDING', db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['announcement', 'device'], name='android_release_one_push_per_device')
        ]
        indexes = [models.Index(fields=['state', 'next_attempt_at'], name='android_push_pending_idx')]


class OrganizationInvite(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    role = models.CharField(max_length=20, choices=Membership.Role.choices)
    technician = models.ForeignKey(Technician, null=True, blank=True, on_delete=models.PROTECT)
    token_hash = models.CharField(max_length=64, unique=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    expires_at = models.DateTimeField()
    used_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)


class DutySwap(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    shift = models.ForeignKey(DutyShift, on_delete=models.CASCADE, related_name='swaps')
    source_technician = models.ForeignKey(Technician, on_delete=models.PROTECT, related_name='+')
    target_technician = models.ForeignKey(Technician, on_delete=models.PROTECT, related_name='+')
    shift_version = models.DateTimeField()
    reason = models.CharField(max_length=1000)
    status = models.CharField(max_length=16, default='PENDING', choices=[('PENDING','Ожидает'),('ACCEPTED','Принята'),('CANCELED','Отменена')])
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    class Meta:
        constraints = [models.UniqueConstraint(fields=['shift'], condition=models.Q(status='PENDING'), name='one_pending_swap_per_shift')]


# 0.13: Commissioning is separate from day-to-day maintenance.  No project
# object should bypass organization membership or an explicit lift assignment.
class CommissioningProject(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name='commissioning_projects')
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=300)
    contract_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    adjuster_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archive_reason = models.CharField(max_length=1000, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']


class CommissioningLift(models.Model):
    project = models.ForeignKey(CommissioningProject, on_delete=models.PROTECT, related_name='lifts')
    elevator = models.ForeignKey(Elevator, null=True, blank=True, on_delete=models.PROTECT, related_name='commissioning_lifts')
    label = models.CharField(max_length=150)
    factory_number = models.CharField(max_length=100, blank=True)
    progress = models.PositiveSmallIntegerField(default=0)
    revision = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['project', 'label'], name='unique_commissioning_lift_label')]
        ordering = ['id']


class CommissioningAssignment(models.Model):
    lift = models.ForeignKey(CommissioningLift, on_delete=models.PROTECT, related_name='assignments')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['lift', 'user'], name='unique_commissioning_assignee')]


class CommissioningProgress(models.Model):
    lift = models.ForeignKey(CommissioningLift, on_delete=models.PROTECT, related_name='history')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    before = models.PositiveSmallIntegerField()
    after = models.PositiveSmallIntegerField()
    description = models.CharField(max_length=4000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']


class CommissioningExpense(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    project = models.ForeignKey(CommissioningProject, on_delete=models.PROTECT, related_name='expenses')
    lift = models.ForeignKey(CommissioningLift, null=True, blank=True, on_delete=models.PROTECT)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    description = models.CharField(max_length=1000)
    receipt = models.FileField(upload_to=private_path, blank=True, max_length=250)
    created_at = models.DateTimeField(auto_now_add=True)
    spent_on = models.DateField(null=True, blank=True)
    reimbursed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']


class CommissioningNeed(models.Model):
    project = models.ForeignKey(CommissioningProject, on_delete=models.PROTECT, related_name='needs')
    lift = models.ForeignKey(CommissioningLift, null=True, blank=True, on_delete=models.PROTECT)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    category = models.CharField(max_length=30, default='OTHER')
    description = models.CharField(max_length=2000)
    blocking = models.BooleanField(default=False)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class CommissioningExtraWork(models.Model):
    project = models.ForeignKey(CommissioningProject, on_delete=models.PROTECT, related_name='extras')
    lift = models.ForeignKey(CommissioningLift, null=True, blank=True, on_delete=models.PROTECT)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    description = models.CharField(max_length=2000)
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=16, default='PENDING')
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class CommissioningMessage(models.Model):
    project = models.ForeignKey(CommissioningProject, on_delete=models.PROTECT, related_name='messages')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    text = models.CharField(max_length=4000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']


class CommissioningSyncReceipt(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    operation_id = models.UUIDField()
    payload_hash = models.CharField(max_length=64)
    result = models.JSONField()
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['organization', 'user', 'operation_id'],
            name='commissioning_sync_once',
        )]


class CommissioningLiftRequest(models.Model):
    project = models.ForeignKey(CommissioningProject, on_delete=models.PROTECT, related_name='lift_requests')
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    label = models.CharField(max_length=150)
    factory_number = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=16, default='PENDING')
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class NotificationInbox(models.Model):
    """Permanent per-user event history, distinct from OS push delivery and acceptance."""
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='lift_notifications')
    event_type = models.CharField(max_length=24)
    event_id = models.BigIntegerField()
    kind = models.CharField(max_length=24)
    title = models.CharField(max_length=200)
    target_type = models.CharField(max_length=24)
    target_id = models.BigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['user','event_type','event_id'], name='notification_user_event_once')]
        indexes = [models.Index(fields=['user','organization','read_at','created_at'],
                                name='notification_inbox_idx')]
        ordering = ['-created_at', '-id']
