"""Audited acknowledgment and consensual handover; no permanent duty role."""
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from .access import org, manager, role, technician_id, audit
from .models import DutyShift, DutySwap, Membership, Organization, Technician


def current_shift(user):
    now = timezone.now()
    return DutyShift.objects.filter(organization=org(user), start__lte=now, end__gt=now).select_related('technician').first()


def shift_data(shift):
    if not shift:
        return None
    return {'id':shift.pk,'technician':shift.technician_id,'technician_name':shift.technician.full_name,
            'start':shift.start,'end':shift.end,'acknowledged_at':shift.acknowledged_at}


def swap_data(row):
    return {'id':row.pk,'shift':row.shift_id,'start':row.shift.start,'end':row.shift.end,
            'source':row.source_technician_id,'source_name':row.source_technician.full_name,
            'target':row.target_technician_id,'target_name':row.target_technician.full_name,
            'reason':row.reason,'status':row.status,'created_at':row.created_at}


@transaction.atomic
def acknowledge(user, shift_id):
    Organization.objects.select_for_update().get(pk=org(user).pk)
    shift = get_object_or_404(DutyShift.objects.select_for_update(), pk=shift_id, organization=org(user))
    now = timezone.now()
    if role(user) != 'MECHANIC' or technician_id(user) != shift.technician_id or not shift.start <= now < shift.end:
        raise PermissionDenied('Подтвердить можно только своё текущее дежурство.')
    if not shift.acknowledged_at:
        shift.acknowledged_at, shift.acknowledged_by = now, user
        shift.save(update_fields=['acknowledged_at','acknowledged_by'])
        audit(user, 'duty.acknowledged', shift.pk)
    return shift_data(shift)


@transaction.atomic
def request_swap(user, shift_id, target_id, reason):
    organization = org(user)
    Organization.objects.select_for_update().get(pk=organization.pk)
    shift = get_object_or_404(DutyShift.objects.select_for_update(), pk=shift_id, organization=organization)
    if not manager(user) and (role(user) != 'MECHANIC' or technician_id(user) != shift.technician_id):
        raise PermissionDenied('Запросить подмену может назначенный механик или диспетчер.')
    text = str(reason or '').strip()
    if not 5 <= len(text) <= 1000:
        raise ValidationError('Укажите причину подмены (5–1000 символов).')
    from .organizations import technician_number
    target = get_object_or_404(Technician, pk=technician_number(target_id), organization=organization, active=True)
    if target.pk == shift.technician_id or shift.end <= timezone.now():
        raise ValidationError('Выберите другого механика для незавершённой смены.')
    if not Membership.objects.filter(organization=organization, technician=target, role='MECHANIC', active=True, user__is_active=True).exists():
        raise ValidationError('У сменщика должен быть действующий аккаунт в этой организации.')
    if shift.swaps.filter(status='PENDING').exists():
        raise ValidationError('Запрос подмены уже отправлен. Отмените его перед новым запросом.')
    row = DutySwap.objects.create(organization=organization, shift=shift, source_technician=shift.technician,
            target_technician=target, shift_version=shift.updated_at, reason=text, requested_by=user)
    audit(user, 'duty.swap_requested', row.pk, {'shift':shift.pk,'from':shift.technician_id,'to':target.pk,'reason':text})
    return row


@transaction.atomic
def resolve_swap(user, swap_id, accept):
    organization = org(user)
    Organization.objects.select_for_update().get(pk=organization.pk)
    row = get_object_or_404(DutySwap.objects.select_for_update(), pk=swap_id, organization=organization)
    shift = DutyShift.objects.select_for_update().get(pk=row.shift_id)
    if row.status != 'PENDING':
        raise ValidationError('Запрос уже обработан.')
    tid = technician_id(user)
    if accept:
        if role(user) != 'MECHANIC' or tid != row.target_technician_id:
            raise PermissionDenied('Подмену подтверждает только выбранный сменщик.')
        now = timezone.now()
        if shift.end <= now or shift.technician_id != row.source_technician_id or shift.updated_at != row.shift_version:
            raise ValidationError('График изменился или смена завершена. Создайте новый запрос.')
        old_end = shift.end
        if shift.start < now:
            shift.end = now
            shift.save(update_fields=['end','updated_at'])
            replacement = DutyShift.objects.create(organization=organization, technician_id=tid, start=now,
                end=old_end, kind='custom', acknowledged_at=now, acknowledged_by=user)
        else:
            shift.technician_id, shift.acknowledged_at, shift.acknowledged_by = tid, None, None
            shift.save(update_fields=['technician','acknowledged_at','acknowledged_by','updated_at'])
            replacement = shift
        row.status = 'ACCEPTED'
        audit(user, 'duty.swap_accepted', row.pk, {'shift':shift.pk,'replacement':replacement.pk,
              'from':row.source_technician_id,'to':tid,'reason':row.reason,'effective_at':replacement.start.isoformat()})
    else:
        if not manager(user) and user.pk != row.requested_by_id and tid != row.target_technician_id:
            raise PermissionDenied('Нет прав на отмену запроса.')
        row.status = 'CANCELED'
        audit(user, 'duty.swap_canceled', row.pk)
    row.resolved_at, row.resolved_by = timezone.now(), user
    row.save(update_fields=['status','resolved_at','resolved_by'])
    return row
