"""Separate durable queue: a personal assignment is never broadcast to the team."""
import json
import logging
import os
from datetime import timedelta
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from .models import Membership, NativePushDevice, PushSubscription, PersonalTaskPush
from .notification_preferences import allows

logger = logging.getLogger('liftcontrol.push')
EXPECTED = {'created':'NEW','assigned':'NEW','accepted':'IN_PROGRESS','completed':'DONE','canceled':'CANCELED'}


def enqueue(event):
    task = event.task
    if event.action in ('created','assigned','canceled'):
        users = Membership.objects.filter(organization=task.organization,technician=task.assigned_to,
            role='MECHANIC',active=True,user__is_active=True,technician__active=True).values_list('user_id',flat=True)
    else:
        users = [task.created_by_id] if task.created_by_id else []
    rows = [PersonalTaskPush(event=event,device=d) for d in NativePushDevice.objects.filter(
        active=True,session__user_id__in=users,session__revoked_at__isnull=True,session__expires_at__gt=timezone.now())]
    rows += [PersonalTaskPush(event=event,subscription=s) for s in PushSubscription.objects.filter(user_id__in=users)]
    PersonalTaskPush.objects.bulk_create(rows,ignore_conflicts=True)


def send_one(row):
    from . import native_push
    from .push_notifications import push_config
    task = row.event.task
    if row.event.action not in EXPECTED or task.status != EXPECTED[row.event.action] or row.created_at < timezone.now()-timedelta(minutes=30) or task.events.filter(pk__gt=row.event_id,action__in=EXPECTED).exists():
        return 'SUPERSEDED'
    user = row.device.session.user if row.device_id else row.subscription.user
    members = Membership.objects.filter(user=user,organization=task.organization,active=True,user__is_active=True)
    visible = members.filter(role__in=['ADMIN','DIRECTOR','DISPATCHER']).exists() or members.filter(
        role='MECHANIC',technician_id=task.assigned_to_id,technician__active=True).exists()
    if not visible or not allows(user.pk,task.organization_id,'tasks'):
        return 'DISABLED'
    data = {'target':'task','task_id':str(task.pk),'organization_id':str(task.organization_id),
            'event_id':str(row.event_id),'kind':row.event.action,'status':task.status}
    if row.device_id:
        import requests
        session = row.device.session
        if not row.device.active or session.revoked_at or session.expires_at <= timezone.now() or not constant_time_compare(session.password_stamp,user.get_session_auth_hash()):
            return 'DISABLED'
        config = native_push.client_config()
        response = requests.post(f"https://fcm.googleapis.com/v1/projects/{config['projectId']}/messages:send",
            headers={'Authorization':'Bearer '+native_push.access_token()},
            json={'message':{'token':row.device.token,'data':data,'android':{'priority':'HIGH','ttl':'300s'}}},
            timeout=8,allow_redirects=False)
        if response.status_code == 200:
            return 'SENT'
        if response.status_code == 404:
            NativePushDevice.objects.filter(pk=row.device_id).update(active=False)
            return 'DISABLED'
        return 'RETRY'
    from pywebpush import webpush, WebPushException
    config = push_config()
    titles = {'created':'Новое личное задание','assigned':'Вам назначено задание','accepted':'Личное задание принято',
              'completed':'Личное задание выполнено','canceled':'Личное задание отменено'}
    payload = {**data,'event_id':row.event_id,'title':titles[row.event.action],'body':'Откройте личное задание для просмотра подробностей.'}
    sub = row.subscription
    try:
        webpush(subscription_info={'endpoint':sub.endpoint,'keys':{'p256dh':sub.p256dh,'auth':sub.auth}},
                data=json.dumps(payload,ensure_ascii=False),vapid_private_key=config['private_file'],
                vapid_claims={'sub':config['subject']},ttl=300,timeout=5)
    except WebPushException as exc:
        if exc.status_code in (404,410):
            sub.delete()
            return 'EXPIRED'
        return 'RETRY'
    return 'SENT'


def process_task_batch(limit=20):
    from .native_push import enabled
    from .push_notifications import push_config
    config = push_config()
    providers = Q(pk__in=[])
    if enabled() and os.path.isfile(os.getenv('FCM_SERVICE_ACCOUNT_FILE','')):
        providers |= Q(device__isnull=False)
    if config['enabled'] and config['private_file'] and os.path.isfile(config['private_file']):
        providers |= Q(subscription__isnull=False)
    with transaction.atomic():
        rows = list(PersonalTaskPush.objects.select_for_update(skip_locked=True).filter(providers,
            state__in=['PENDING','PROCESSING'],next_attempt_at__lte=timezone.now()).order_by('pk')[:limit])
        for row in rows:
            row.state, row.attempts = 'PROCESSING', row.attempts+1
            row.next_attempt_at = timezone.now()+timedelta(minutes=2)
        PersonalTaskPush.objects.bulk_update(rows,['state','attempts','next_attempt_at'])
    for row in rows:
        try:
            result = send_one(row)
        except Exception as exc:
            logger.warning('task push failure: %s',type(exc).__name__)
            result = 'RETRY'
        if result == 'EXPIRED':
            continue
        row.state = 'PENDING' if result == 'RETRY' and row.attempts < 4 else 'FAILED' if result == 'RETRY' else result
        row.next_attempt_at = timezone.now()+timedelta(seconds=20*2**row.attempts)
        row.save(update_fields=['state','next_attempt_at'])
    return len(rows)
