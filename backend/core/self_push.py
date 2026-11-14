"""Self-hosted LiftControl delivery feed (phase 1 of the dual push channel).

This is a cursor-based, authenticated HTTPS feed, not an Android background push
mechanism. It reuses immutable ticket/task events, avoiding a second message
database and preserving FCM. Never expose employee names, addresses, or repair
notes in this transport; clients fetch the protected object after opening.
"""
import os
import re
from datetime import timedelta

from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from .access import MemberAccess, membership, org
from .models import MobileSession, NotificationPreference, PersonalTaskEvent, TicketEvent
from .notification_preferences import ticket_recipients

TICKET_ACTIONS = ('created', 'accepted', 'completed', 'canceled', 'stopped', 'resumed', 'reassigned', 'long_repair')
TASK_ACTIONS = ('created', 'assigned', 'accepted', 'completed', 'canceled')
EXPECTED_TICKET_STATUS = {
    'created': 'NEW', 'accepted': 'IN_PROGRESS', 'completed': 'DONE',
    'canceled': 'CANCELED', 'stopped': 'STOPPED', 'resumed': 'IN_PROGRESS',
    'long_repair': 'LONG_REPAIR',
}
EXPECTED_TASK_STATUS = {
    'created': 'NEW', 'assigned': 'NEW', 'accepted': 'IN_PROGRESS',
    'completed': 'DONE', 'canceled': 'CANCELED',
}
# Old events remain in the business timeline; they are not re-announced as push.
MAX_AGE = timedelta(minutes=30)


def _cursor(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'(?:0|[1-9][0-9]{0,18})', value):
        raise ValidationError({label: 'Некорректный указатель уведомлений.'})
    cursor = int(value)
    if cursor > 9223372036854775807:
        raise ValidationError({label: 'Указатель слишком большой.'})
    return cursor


def _allowed(preferences, group):
    return preferences is None or bool(getattr(preferences, group))


def _category(priority, action):
    if action not in ('created', 'reassigned'):
        return 'updates'
    if priority == 'ENTRAPMENT':
        return 'entrapment'
    if priority in ('HIGH', 'EMERGENCY'):
        return 'urgent'
    return 'ordinary'


def _current_ticket(event, now):
    if event.created_at < now - MAX_AGE:
        return False
    ticket = event.ticket
    if event.action in EXPECTED_TICKET_STATUS and ticket.status != EXPECTED_TICKET_STATUS[event.action]:
        return False
    if ticket.events.filter(pk__gt=event.pk, action__in=TICKET_ACTIONS).exists():
        return False
    return True


def _current_task(event, now):
    if event.created_at < now - MAX_AGE:
        return False
    task = event.task
    if task.status != EXPECTED_TASK_STATUS.get(event.action):
        return False
    if task.events.filter(pk__gt=event.pk, action__in=TASK_ACTIONS).exists():
        return False
    return True


@api_view(['GET'])
@permission_classes([MemberAccess])
def feed(request):
    """Read up to 50 newer records per stream and return independent cursors.

    Android clients must retain cursors separately for each (server, user,
    organization). A successful HTTP response advances the scan cursor even
    when events were filtered by authorization/preferences/staleness. Local
    notification dedup uses (organization, target, object_id, event_id).
    """
    if not isinstance(request.auth, MobileSession):
        raise PermissionDenied('Доступно только авторизованному Android-сеансу.')
    if request.auth.revoked_at or request.auth.expires_at <= timezone.now():
        raise PermissionDenied('Сеанс устройства больше не действует.')

    if os.getenv('SELF_PUSH_ENABLED', '').lower() != 'true':
        raise PermissionDenied('Собственный канал уведомлений пока отключён.')

    organization = org(request.user)
    member = membership(request.user)
    # The organization is chosen by the existing X-Organization-ID permission.
    if not member or member.organization_id != organization.pk:
        raise PermissionDenied('Нет членства в организации.')

    tickets_after = _cursor(request.query_params.get('tickets_after', '0'), 'tickets_after')
    tasks_after = _cursor(request.query_params.get('tasks_after', '0'), 'tasks_after')
    now = timezone.now()
    preference = NotificationPreference.objects.filter(
        user=request.user, organization=organization
    ).first()
    recent = now - MAX_AGE
    ticket_events = TicketEvent.objects.filter(
        organization=organization, pk__gt=tickets_after, action__in=TICKET_ACTIONS
    )
    task_events = PersonalTaskEvent.objects.filter(
        task__organization=organization, pk__gt=tasks_after, action__in=TASK_ACTIONS
    )
    if member.role == 'MECHANIC':
        task_events = task_events.filter(
            task__assigned_to_id=member.technician_id,
            action__in=('created', 'assigned', 'canceled'),
        )
    else:
        task_events = task_events.filter(
            action__in=('accepted', 'completed'), task__created_by=request.user
        )
    if request.query_params.get('bootstrap') == '1':
        # Initial installation must not sound every historical notification.
        latest_tickets = ticket_events.order_by('-pk').values_list('pk', flat=True).first()
        latest_tasks = task_events.order_by('-pk').values_list('pk', flat=True).first()
        result = Response({
            'provider': 'LIFTCONTROL', 'events': [],
            'tickets_after': latest_tickets or tickets_after,
            'tasks_after': latest_tasks or tasks_after,
            'server_time': now.isoformat(),
        })
        result['Cache-Control'] = 'no-store, private'
        return result

    ticket_rows = list(ticket_events.select_related('ticket').order_by('pk')[:50])
    task_rows = list(task_events.select_related('task').order_by('pk')[:50])

    events = []
    for row in ticket_rows:
        if row.created_at < recent or not _current_ticket(row, now):
            continue
        if not ticket_recipients(row.ticket, row.action).filter(user=request.user).exists():
            continue
        group = _category(row.ticket.priority, row.action)
        if not _allowed(preference, group):
            continue
        events.append({
            'target': 'ticket', 'organization_id': organization.pk,
            'ticket_id': row.ticket_id, 'event_id': row.pk,
            'kind': row.action, 'priority': row.ticket.priority,
            'status': row.ticket.status, 'created_at': row.created_at.isoformat(),
        })

    for row in task_rows:
        if row.created_at < recent or not _current_task(row, now):
            continue
        if not _allowed(preference, 'tasks'):
            continue
        events.append({
            'target': 'task', 'organization_id': organization.pk,
            'task_id': row.task_id, 'event_id': row.pk,
            'kind': row.action, 'priority': row.task.priority,
            'status': row.task.status, 'created_at': row.created_at.isoformat(),
        })

    events.sort(key=lambda item: (item['created_at'], item['target'], item['event_id']))
    result = Response({
        'provider': 'LIFTCONTROL', 'events': events,
        'tickets_after': ticket_rows[-1].pk if ticket_rows else tickets_after,
        'tasks_after': task_rows[-1].pk if task_rows else tasks_after,
        'server_time': now.isoformat(), 'max_age_seconds': int(MAX_AGE.total_seconds()),
    })
    result['Cache-Control'] = 'no-store, private'
    return result
