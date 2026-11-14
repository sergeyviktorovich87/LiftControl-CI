"""Ticket state transitions and permissions; never trust client role or status."""
from datetime import date

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from .access import org, role, technician_id, audit
from .models import DutyShift, Membership, Ticket, TicketComment, TicketEvent, PushSubscription, PushDelivery


PRIVILEGED = frozenset(('ADMIN', 'DIRECTOR', 'DISPATCHER'))


def is_on_duty(user, at=None):
    """Evaluate the current shift on every request (no cross-request cached permissions)."""
    if role(user) != 'MECHANIC':
        return False
    tid = technician_id(user)
    now = at or timezone.now()
    return bool(tid and DutyShift.objects.filter(
        organization=org(user), technician_id=tid, start__lte=now, end__gt=now
    ).exists())

def can_create_ticket(user):
    return role(user) in PRIVILEGED or is_on_duty(user)


def can_cancel_ticket(user, ticket):
    if ticket.status == Ticket.Status.CANCELED:
        return False
    r = role(user)
    if ticket.status == Ticket.Status.DONE:
        return r in ('ADMIN', 'DIRECTOR')
    return r in PRIVILEGED or is_on_duty(user)


def can_accept_ticket(user, ticket):
    if role(user) != 'MECHANIC':
        return False
    tid = technician_id(user)
    responsible = ticket.assigned_to_id or ticket.elevator.assigned_technician_id
    return bool(tid and tid == responsible and ticket.status == Ticket.Status.NEW)


def can_finish_ticket(user, ticket):
    return bool(role(user) == 'MECHANIC' and ticket.status in (
                Ticket.Status.IN_PROGRESS, Ticket.Status.STOPPED, Ticket.Status.LONG_REPAIR)
                and technician_id(user)
                and technician_id(user) == ticket.assigned_to_id)


def can_stop_ticket(user, ticket):
    return can_finish_ticket(user, ticket) and ticket.status == Ticket.Status.IN_PROGRESS


def add_event(ticket, user, action, before='', after='', reason=''):
    row = TicketEvent.objects.create(
        organization=ticket.organization, ticket=ticket, actor=user,
        action=action, before=before, after=after, reason=reason,
    )
    audit(user, 'ticket.' + action, ticket.pk)
    # Only new tickets and state changes warrant delivery to their recipients.
    # Comments, corrections and photo uploads stay in the timeline without push spam.
    if action not in ('created', 'accepted', 'completed', 'canceled', 'reassigned', 'stopped', 'resumed', 'long_repair'):
        return
    from .notification_inbox import enqueue_ticket
    enqueue_ticket(row)
    # Durable per-device queue. Nothing is posted to an external endpoint from an HTTP request.
    from .notification_preferences import ticket_recipients
    recipients = ticket_recipients(ticket, action).filter(role='MECHANIC').values('user_id')
    subscriptions = PushSubscription.objects.filter(user_id__in=recipients)
    PushDelivery.objects.bulk_create([
        PushDelivery(organization=ticket.organization, ticket=ticket,
                     subscription=sub, kind=action)
        for sub in subscriptions
    ])
    from .native_push import enqueue_native
    enqueue_native(ticket, action)


def eligible_assignee(user, value):
    from .models import Technician
    try:
        value = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValidationError({'assigned_to': 'Выберите механика.'})
    person = Technician.objects.filter(pk=value, organization=org(user), active=True).first()
    if not person or not Membership.objects.filter(organization=org(user), technician=person,
            active=True, role='MECHANIC', user__is_active=True).exists():
        raise ValidationError({'assigned_to': 'Выберите механика с действующим аккаунтом организации.'})
    return person


def can_assign_ticket(user):
    return role(user) in PRIVILEGED or is_on_duty(user)


@transaction.atomic
def create_ticket(user, validated, *, offline_at=None):
    if not (can_create_ticket(user) or (offline_at is not None and is_on_duty(user, offline_at))):
        raise PermissionDenied('Создавать заявки может диспетчер или механик на действующем дежурстве.')
    from .models import Elevator
    lift = Elevator.objects.select_for_update().get(pk=validated['elevator'].pk, organization=org(user))
    if not lift.active:
        raise ValidationError('Нельзя создавать заявку на лифт из архива.')
    validated = dict(validated)
    selected = validated.pop('assigned_to', None)
    # The lift's responsible mechanic is the visible default, validated in this tenant.
    assignee = eligible_assignee(user, selected.pk if hasattr(selected, 'pk') else selected) if selected else None
    if assignee is None and lift.assigned_technician_id:
        try:
            assignee = eligible_assignee(user, lift.assigned_technician_id)
        except ValidationError:
            pass
    ticket = Ticket.objects.create(
        organization=org(user), created_by=user, status=Ticket.Status.NEW,
        assigned_to=assignee, repair='', **validated,
    )
    add_event(ticket, user, 'created', after=Ticket.Status.NEW)
    return ticket


@transaction.atomic
def accept_ticket(user, ticket_id):
    ticket = Ticket.objects.select_for_update().select_related('elevator').get(pk=ticket_id, organization=org(user))
    if not can_accept_ticket(user, ticket):
        raise PermissionDenied('Заявку может принять только закреплённый или назначенный механик, пока она новая.')
    ticket.assigned_to_id = technician_id(user)
    ticket.accepted_at = timezone.now()
    ticket.status = Ticket.Status.IN_PROGRESS
    ticket.save(update_fields=['assigned_to','accepted_at','status','updated_at'])
    add_event(ticket, user, 'accepted', before=Ticket.Status.NEW, after=Ticket.Status.IN_PROGRESS)
    return ticket


@transaction.atomic
def complete_ticket(user, ticket_id, work_description):
    ticket = Ticket.objects.select_for_update().get(pk=ticket_id, organization=org(user))
    # Serialize physical lift state across concurrent tickets for the same lift.
    from .models import Elevator
    elevator = Elevator.objects.select_for_update().get(pk=ticket.elevator_id)
    if not can_finish_ticket(user, ticket):
        raise PermissionDenied('Закрывать заявку может только принявший её механик.')
    text = str(work_description or '').strip()
    if not 3 <= len(text) <= 4000:
        raise ValidationError({'work_description': 'Укажите выполненные работы (3–4000 символов).'})
    previous = ticket.status
    ticket.repair = text
    ticket.completed_at = timezone.now()
    ticket.status = Ticket.Status.DONE
    ticket.save(update_fields=['repair','completed_at','status','updated_at'])
    if not Ticket.objects.filter(elevator=elevator, stopped_at__isnull=False).exclude(status__in=[Ticket.Status.DONE, Ticket.Status.CANCELED]).exists():
        elevator.stopped_at, elevator.stop_reason = None, ''
        elevator.save(update_fields=['stopped_at', 'stop_reason'])
    TicketComment.objects.create(
        organization=ticket.organization, ticket=ticket, author=user,
        kind='RESULT', body=text,
    )
    add_event(ticket, user, 'completed', before=previous, after=Ticket.Status.DONE)
    return ticket


@transaction.atomic
def stop_ticket(user, ticket_id, reason):
    ticket = Ticket.objects.select_for_update().get(pk=ticket_id, organization=org(user))
    if not can_stop_ticket(user, ticket):
        raise PermissionDenied('Остановку отмечает исполнитель заявки в работе.')
    text = str(reason or '').strip()
    if not 5 <= len(text) <= 1000:
        raise ValidationError({'reason': 'Укажите причину остановки (5–1000 символов).'})
    from .models import Elevator
    elevator = Elevator.objects.select_for_update().get(pk=ticket.elevator_id)
    now = timezone.now()
    ticket.status, ticket.stopped_at, ticket.stop_reason = Ticket.Status.STOPPED, now, text
    ticket.save(update_fields=['status', 'stopped_at', 'stop_reason', 'updated_at'])
    elevator.stopped_at, elevator.stop_reason = elevator.stopped_at or now, text
    elevator.save(update_fields=['stopped_at', 'stop_reason'])
    add_event(ticket, user, 'stopped', before=Ticket.Status.IN_PROGRESS,
              after=Ticket.Status.STOPPED, reason=text)
    return ticket


@transaction.atomic
def resume_ticket(user, ticket_id):
    ticket = Ticket.objects.select_for_update().get(pk=ticket_id, organization=org(user))
    if ticket.status not in (Ticket.Status.STOPPED, Ticket.Status.LONG_REPAIR) or not can_finish_ticket(user, ticket):
        raise PermissionDenied('Продолжить ремонт может исполнитель остановленной заявки.')
    previous = ticket.status
    ticket.status = Ticket.Status.IN_PROGRESS
    ticket.save(update_fields=['status', 'updated_at'])
    # Resuming repair does not certify that the physical lift works again.
    add_event(ticket, user, 'resumed', before=previous, after=Ticket.Status.IN_PROGRESS)
    return ticket


@transaction.atomic
def defer_repair(user, ticket_id, reason, expected_date=None):
    ticket = Ticket.objects.select_for_update().get(pk=ticket_id, organization=org(user))
    if not can_finish_ticket(user, ticket):
        raise PermissionDenied('Длительный ремонт отмечает исполнитель заявки.')
    text = reason.strip() if isinstance(reason, str) else ''
    if not 5 <= len(text) <= 1000:
        raise ValidationError({'reason': 'Укажите причину длительного ремонта (5–1000 символов).'})
    try:
        due = date.fromisoformat(expected_date) if expected_date not in (None, '') else None
    except (ValueError, TypeError):
        raise ValidationError({'expected_date': 'Укажите дату ГГГГ-ММ-ДД.'})
    previous = ticket.status
    ticket.status = Ticket.Status.LONG_REPAIR
    ticket.long_repair_reason = text
    ticket.expected_repair_date = due
    ticket.save(update_fields=['status', 'long_repair_reason', 'expected_repair_date', 'updated_at'])
    add_event(ticket, user, 'long_repair', before=previous, after=ticket.status,
              reason=(f'{text} · до {due.isoformat()}' if due else text)[:1000])
    return ticket


@transaction.atomic
def cancel_ticket(user, ticket_id, reason):
    ticket = Ticket.objects.select_for_update().get(pk=ticket_id, organization=org(user))
    if ticket.status == Ticket.Status.CANCELED:
        raise ValidationError('Заявка уже отменена.')
    if not can_cancel_ticket(user, ticket):
        raise PermissionDenied('Отменить заявку может только дежурный, диспетчер или руководитель.')
    text = str(reason or '').strip()
    if not 5 <= len(text) <= 1000:
        raise ValidationError({'reason': 'Укажите причину отмены (5–1000 символов).'})
    previous = ticket.status
    ticket.status = Ticket.Status.CANCELED
    ticket.canceled_by = user
    ticket.canceled_at = timezone.now()
    ticket.cancellation_reason = text
    ticket.save(update_fields=['status','canceled_by','canceled_at','cancellation_reason','updated_at'])
    add_event(ticket, user, 'canceled', before=previous, after=Ticket.Status.CANCELED, reason=text)
    return ticket


@transaction.atomic
def add_comment(user, ticket_id, body):
    ticket = Ticket.objects.select_for_update().get(pk=ticket_id, organization=org(user))
    if ticket.status not in (Ticket.Status.NEW, Ticket.Status.IN_PROGRESS, Ticket.Status.STOPPED, Ticket.Status.LONG_REPAIR):
        raise PermissionDenied('Завершённая или отменённая заявка доступна только для чтения.')
    if not (can_finish_ticket(user, ticket) or role(user) in PRIVILEGED or is_on_duty(user)):
        raise PermissionDenied('Добавлять комментарии вправе исполнитель или дежурный.')
    text = str(body or '').strip()
    if not 1 <= len(text) <= 4000:
        raise ValidationError({'body': 'Комментарий обязателен (до 4000 символов).'})
    comment = TicketComment.objects.create(
        organization=ticket.organization, ticket=ticket, author=user, body=text,
    )
    add_event(ticket, user, 'commented', before=ticket.status, after=ticket.status)
    return comment


@transaction.atomic
def reassign_ticket(user, ticket_id, assigned_to, reason):
    if not can_assign_ticket(user):
        raise PermissionDenied('Назначение исполнителя доступно диспетчеру, дежурному или руководителю.')
    ticket = Ticket.objects.select_for_update().get(pk=ticket_id, organization=org(user))
    if ticket.status != Ticket.Status.NEW:
        raise ValidationError('Переназначить можно только новую заявку.')
    text = str(reason or '').strip()
    if not 5 <= len(text) <= 1000:
        raise ValidationError({'reason': 'Укажите причину переназначения (5–1000 символов).'})
    from .models import Technician
    try:
        assignee_id = int(assigned_to)
    except (TypeError, ValueError, OverflowError):
        raise ValidationError({'assigned_to': 'Выберите корректного механика.'})
    if assignee_id <= 0:
        raise ValidationError({'assigned_to': 'Выберите корректного механика.'})
    person = Technician.objects.filter(
        pk=assignee_id, organization=org(user), active=True,
    ).first()
    if not person or not Membership.objects.filter(
        organization=org(user), technician=person, active=True,
        role='MECHANIC', user__is_active=True,
    ).exists():
        raise ValidationError({'assigned_to': 'Выберите механика с действующим аккаунтом организации.'})
    ticket.assigned_to = person
    ticket.save(update_fields=['assigned_to','updated_at'])
    add_event(ticket, user, 'reassigned', before=ticket.status,
              after=ticket.status, reason=text)
    return ticket

