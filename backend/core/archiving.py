"""Audited removal from working lists, retaining incident and maintenance history."""
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from .access import org, role, audit
from .models import Organization, Membership, Technician, Elevator, Ticket, DutyShift


def reason(data):
    value = str(data.get('reason') or '').strip()
    if not 5 <= len(value) <= 1000:
        raise ValidationError({'reason': 'Укажите причину (5–1000 символов).'})
    return value


@transaction.atomic
def archive_elevator(user, pk, data):
    if role(user) not in ('ADMIN', 'DIRECTOR'):
        raise PermissionDenied('Удалить лифт из рабочих списков может администратор или директор.')
    lift = get_object_or_404(Elevator.objects.select_for_update(), pk=pk, organization=org(user))
    text = reason(data)
    if Ticket.objects.filter(elevator=lift).exclude(status__in=['DONE', 'CANCELED']).exists():
        raise ValidationError('Сначала завершите открытые заявки этого лифта.')
    lift.active = False
    lift.save(update_fields=['active'])
    audit(user, 'elevator.archived', lift.pk, {'reason': text})


@transaction.atomic
def archive_technician(user, pk, data):
    if role(user) not in ('ADMIN', 'DIRECTOR'):
        raise PermissionDenied('Удалить сотрудника из рабочих списков может администратор или директор.')
    organization = org(user)
    Organization.objects.select_for_update().get(pk=organization.pk)
    if role(user) not in ('ADMIN', 'DIRECTOR'):
        raise PermissionDenied('Доступ к управлению сотрудниками изменился.')
    tech = get_object_or_404(Technician.objects.select_for_update(), pk=pk, organization=organization)
    text = reason(data)
    member = Membership.objects.filter(technician=tech).first()
    if member and member.user_id == user.pk:
        raise ValidationError('Свой доступ изменяет другой администратор.')
    if member and member.role == 'ADMIN' and role(user) != 'ADMIN':
        raise PermissionDenied('Доступ администратора изменяет только администратор.')
    now = timezone.now()
    if DutyShift.objects.filter(technician=tech, start__lte=now, end__gt=now).exists():
        raise ValidationError('Сначала оформите подмену действующего дежурного.')
    if Ticket.objects.filter(assigned_to=tech).exclude(status__in=['DONE', 'CANCELED']).exists():
        raise ValidationError('У сотрудника остались незавершённые заявки.')
    lifts = Elevator.objects.filter(organization=organization, assigned_technician=tech, active=True)
    shifts = DutyShift.objects.filter(organization=organization, technician=tech, start__gt=now)
    replacement = None
    if data.get('replacement'):
        try:
            replacement = Technician.objects.get(pk=int(data['replacement']), organization=organization, active=True)
        except (TypeError, ValueError, Technician.DoesNotExist):
            raise ValidationError({'replacement': 'Выберите действующего механика организации.'})
        if replacement == tech or not Membership.objects.filter(technician=replacement, active=True, role='MECHANIC', user__is_active=True).exists():
            raise ValidationError({'replacement': 'У замены должен быть действующий доступ механика.'})
    if (lifts.exists() or shifts.exists()) and replacement is None:
        raise ValidationError({'replacement': 'Выберите, кому передать закреплённые лифты и будущие дежурства.'})
    transferred = {'lifts': list(lifts.values_list('pk', flat=True)), 'shifts': list(shifts.values_list('pk', flat=True))}
    if replacement:
        lifts.update(assigned_technician=replacement)
        shifts.update(technician=replacement, updated_at=now, acknowledged_at=None, acknowledged_by=None)
    if member:
        member.active = False
        member.save(update_fields=['active'])
    tech.active = False
    tech.save(update_fields=['active'])
    audit(user, 'technician.archived', tech.pk, {'reason': text, 'replacement': replacement.pk if replacement else None, **transferred})
