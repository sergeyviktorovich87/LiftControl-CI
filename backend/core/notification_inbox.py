"""Server-owned notification history.

Inbox records are not delivery acknowledgments, and marking as read must
never accept a ticket or task. Push transports can fail independently of this
history; privacy is enforced at creation and again at read time.
"""
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .access import MemberAccess, org, role, technician_id
from .models import Membership, NotificationInbox, CommissioningAssignment, PersonalTask


def _enqueue(organization, user_ids, event_type, event_id, kind, title,
             target_type, target_id):
    unique={int(uid) for uid in user_ids if uid}
    NotificationInbox.objects.bulk_create([
        NotificationInbox(organization=organization, user_id=uid,
                          event_type=event_type, event_id=event_id,
                          kind=kind, title=title[:200],
                          target_type=target_type, target_id=target_id)
        for uid in unique
    ], ignore_conflicts=True)


def enqueue_ticket(event):
    ticket=event.ticket
    users=(Membership.objects.filter(
        organization=ticket.organization,active=True,user__is_active=True)
        .exclude(role='ADJUSTER')
        .exclude(role='MECHANIC',technician__isnull=True)
        .exclude(role='MECHANIC',technician__active=False)
        .values_list('user_id',flat=True).distinct())
    _enqueue(ticket.organization,users,'ticket',event.pk,event.action,
             f'Заявка №{ticket.pk}: {event.action}','ticket',ticket.pk)


def enqueue_task(event):
    task=event.task
    users=set()
    if task.created_by_id:
        users.add(task.created_by_id)
    assigned=Membership.objects.filter(
        organization=task.organization,active=True,user__is_active=True,
        role='MECHANIC',technician=task.assigned_to,
        technician__active=True).values_list('user_id',flat=True)
    users.update(assigned)
    _enqueue(task.organization,users,'task',event.pk,event.action,
             f'Личное задание №{task.pk}: {event.action}','task',task.pk)


def enqueue_commissioning_message(message):
    project=message.project
    users=set(Membership.objects.filter(
        organization=project.organization, active=True, user__is_active=True,
        role__in=['ADMIN','DIRECTOR']).values_list('user_id',flat=True))
    users.update(project.lifts.filter(assignments__active=True,
        assignments__user__is_active=True,
        assignments__user__memberships__organization=project.organization,
        assignments__user__memberships__active=True).values_list(
        'assignments__user_id',flat=True))
    users.discard(message.author_id)
    _enqueue(project.organization,users,'commissioning_message',
             message.pk,'message',f'Сообщение на объекте: {project.name}',
             'commissioning',project.pk)




def enqueue_team_chat(message):
    # Regular team chat is not visible to restricted external adjusters.
    recipients=(Membership.objects.filter(
        organization=message.organization,active=True,user__is_active=True)
        .exclude(role='ADJUSTER')
        .exclude(role='MECHANIC',technician__isnull=True)
        .exclude(role='MECHANIC',technician__active=False)
        .exclude(user_id=message.author_id)
        .values_list('user_id',flat=True).distinct())
    _enqueue(message.organization,recipients,'team_chat',message.pk,'message',
             'Новое сообщение в чате команды','chat',message.pk)

def _visible_inbox(user, company):
    # An already-created inbox row is not a permanent access grant.
    rows = NotificationInbox.objects.filter(user=user, organization=company)
    current_role = role(user)
    projects = CommissioningAssignment.objects.filter(
        user=user, active=True,
        lift__project__organization=company,
    ).values_list('lift__project_id', flat=True)
    if current_role == 'ADJUSTER':
        return rows.filter(target_type='commissioning', target_id__in=projects)
    if current_role in ('ADMIN', 'DIRECTOR', 'DISPATCHER'):
        if current_role == 'DISPATCHER':
            return rows.filter(~Q(target_type='commissioning') |
                               Q(target_type='commissioning', target_id__in=projects))
        return rows
    if current_role != 'MECHANIC':
        return rows.none()
    # Personal task events become invisible if the assignment was revoked.
    tasks = PersonalTask.objects.filter(organization=company,
                                        assigned_to_id=technician_id(user)).values_list('pk', flat=True)
    return rows.filter(
        (~Q(target_type='commissioning') | Q(target_id__in=projects)) &
        (~Q(target_type='task') | Q(target_id__in=tasks))
    )


@api_view(['GET'])
@permission_classes([MemberAccess])
def inbox(request):
    company=org(request.user)
    visible=_visible_inbox(request.user, company)
    unread_count=visible.filter(read_at__isnull=True).count()
    unread=request.query_params.get('unread')
    if unread not in (None,'0','1'):
        raise ValidationError({'unread':'Укажите 0 или 1.'})
    if unread == '1':
        visible=visible.filter(read_at__isnull=True)
    try:
        limit=int(request.query_params.get('limit','50'))
    except (ValueError,TypeError):
        raise ValidationError({'limit':'Неверное количество записей.'})
    if limit<1 or limit>100:
        raise ValidationError({'limit':'Допустимое количество: 1-100.'})
    rows=list(visible.order_by('-created_at','-id')[:limit])
    return Response({'unread':unread_count,'results':[
        {'id':x.pk,'kind':x.kind,'title':x.title,
         'target_type':x.target_type,'target_id':x.target_id,
         'created_at':x.created_at,'read_at':x.read_at}
        for x in rows]})


@api_view(['POST'])
@permission_classes([MemberAccess])
@transaction.atomic
def mark_read(request, notification_id):
    item=get_object_or_404(_visible_inbox(request.user, org(request.user)).select_for_update(),
                           pk=notification_id)
    if item.read_at is None:
        item.read_at=timezone.now()
        item.save(update_fields=['read_at'])
    return Response({'ok':True,'id':item.pk,'read_at':item.read_at})
