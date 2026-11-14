"""A person's choices are scoped to one employer and checked again before delivery."""
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from django.db.models import Q
from django.utils import timezone
from .access import MemberAccess, org
from .models import DutyShift, Membership, NotificationPreference

FIELDS = ('ordinary','urgent','entrapment','updates','tasks')
NEW_TICKET_EVENTS = ('created', 'reassigned')


def ticket_recipients(ticket, kind):
    members = Membership.objects.filter(
        organization=ticket.organization, active=True, user__is_active=True,
    ).exclude(role='ADJUSTER').exclude(
        role='MECHANIC', technician__isnull=True,
    ).exclude(role='MECHANIC', technician__active=False)
    responsible = ticket.assigned_to_id or ticket.elevator.assigned_technician_id
    eligible = Q(role__in=('ADMIN', 'DIRECTOR', 'DISPATCHER'))
    if responsible:
        eligible |= Q(role='MECHANIC', technician_id=responsible)
    if ticket.priority in ('HIGH', 'EMERGENCY', 'ENTRAPMENT') or not responsible:
        now = timezone.now()
        on_duty = DutyShift.objects.filter(
            organization=ticket.organization, start__lte=now, end__gt=now,
        ).values('technician_id')
        eligible |= Q(role='MECHANIC', technician_id__in=on_duty)
    if kind in NEW_TICKET_EVENTS and responsible and ticket.priority in ('LOW', 'NORMAL'):
        eligible = Q(role='MECHANIC', technician_id=responsible)
    return members.filter(eligible)


def category(priority, kind='created'):
    if kind not in ('created','reassigned'):
        return 'updates'
    return 'entrapment' if priority == 'ENTRAPMENT' else 'urgent' if priority in ('HIGH','EMERGENCY') else 'ordinary'


def allows(user_id, organization_id, group):
    row = NotificationPreference.objects.filter(user_id=user_id, organization_id=organization_id).first()
    return row is None or bool(getattr(row, group))


class PreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = FIELDS


@api_view(['GET','PATCH'])
@permission_classes([MemberAccess])
def preferences(request):
    organization = org(request.user)
    if request.method == 'GET':
        row = NotificationPreference.objects.filter(user=request.user, organization=organization).first()
        return Response(PreferenceSerializer(row).data if row else {k:True for k in FIELDS})
    serializer = PreferenceSerializer(data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    row, _ = NotificationPreference.objects.update_or_create(user=request.user, organization=organization,
        defaults=serializer.validated_data)
    return Response(PreferenceSerializer(row).data)
