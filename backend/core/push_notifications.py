"""Web Push: strict endpoint allowlist, secret VAPID key, durable delivery outbox.

No HTTP request sends outbound push traffic. Background worker does so from an
isolated container with egress access. Do not log subscription endpoints or keys.
"""
import json
import logging
import os
import re
from datetime import timedelta
from urllib.parse import urlsplit
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError, PermissionDenied
from .access import org, role, memberships
from .models import PushSubscription, PushDelivery

logger = logging.getLogger('liftcontrol.push')


def push_config():
    private_file = os.getenv('WEBPUSH_VAPID_PRIVATE_KEY_FILE', '')
    public_key = os.getenv('WEBPUSH_VAPID_PUBLIC_KEY', '')
    subject = os.getenv('WEBPUSH_VAPID_SUBJECT', '')
    return {'enabled': bool(public_key and subject and (os.getenv('WEBPUSH_ENABLED','').lower() == 'true' or private_file and os.path.isfile(private_file))),
            'private_file': private_file, 'public_key': public_key, 'subject': subject}


def valid_endpoint(endpoint):
    if not isinstance(endpoint, str) or len(endpoint) > 2048:
        return False
    try:
        url = urlsplit(endpoint)
        host = (url.hostname or '').lower()
        allowed = (
            host in ('fcm.googleapis.com', 'updates.push.services.mozilla.com', 'web.push.apple.com')
            or host.endswith('.push.apple.com')
            or host.endswith('.notify.windows.com')
        )
        return (url.scheme == 'https' and allowed and url.port in (None, 443)
                and bool(url.path and url.path != '/') and not url.username
                and not url.password and not url.fragment)
    except ValueError:
        return False


def _key(value):
    return (isinstance(value, str) and 12 <= len(value) <= 255
            and re.fullmatch(r'[A-Za-z0-9_-]+={0,2}', value) is not None)


def register_subscription(user, data):
    if not memberships(user).filter(role='MECHANIC').exists():
        raise PermissionDenied('Push для заявок доступен авторизованным механикам.')
    if not push_config()['enabled']:
        raise ValidationError('Push пока не настроен администратором.')
    endpoint = data.get('endpoint', '')
    keys = data.get('keys', {})
    if not isinstance(keys, dict) or not valid_endpoint(endpoint) or not _key(keys.get('p256dh')) or not _key(keys.get('auth')):
        raise ValidationError('Неверные данные подписки браузера.')
    existing = PushSubscription.objects.filter(endpoint=endpoint).first()
    if existing and existing.user_id != user.pk:
        raise PermissionDenied('Этот браузер уже привязан к другому пользователю. Отпишитесь и войдите заново.')
    if existing:
        existing.p256dh, existing.auth = keys['p256dh'], keys['auth']
        existing.save(update_fields=['p256dh', 'auth'])
        return existing
    if PushSubscription.objects.filter(user=user).count() >= 5:
        raise ValidationError('На пользователя допускается не более 5 устройств.')
    return PushSubscription.objects.create(
        user=user, organization=org(user), endpoint=endpoint,
        p256dh=keys['p256dh'], auth=keys['auth'],
    )


def unregister_subscription(user, endpoint):
    return PushSubscription.objects.filter(user=user, endpoint=endpoint).delete()[0]


def deliver_one(item, config):
    from pywebpush import webpush, WebPushException
    from .models import PushSubscription
    sub = item.subscription
    # A former employee must not receive previously queued notifications.
    from .models import Membership
    if not sub.user.is_active or not Membership.objects.filter(
        user=sub.user, organization=item.organization, active=True, role='MECHANIC',
        technician__active=True, technician__organization=item.organization,
    ).exists():
        return 'DISABLED'
    ticket = item.ticket
    from .notification_preferences import allows, category
    if not allows(sub.user_id,item.organization_id,category(ticket.priority,item.kind)):
        return 'DISABLED'
    expected = {'created':'NEW','accepted':'IN_PROGRESS','completed':'DONE',
                'canceled':'CANCELED','stopped':'STOPPED','resumed':'IN_PROGRESS'}
    # An offline/retrying worker must not announce a historical NEW state.
    if (expected.get(item.kind, ticket.status) != ticket.status
            or ticket.events.filter(created_at__gt=item.created_at,
                action__in=list(expected)+['reassigned']).exists()
            or item.created_at < timezone.now() - timedelta(minutes=30)):
        return 'SUPERSEDED'
    event = ticket.events.filter(action=item.kind, created_at__lte=item.created_at).last()
    titles = {
        'created': 'Застревание: люди в кабине' if ticket.priority == 'ENTRAPMENT' else 'Новая заявка',
        'accepted': 'Заявка принята',
        'completed': 'Заявка выполнена',
        'canceled': 'Заявка отменена',
        'reassigned': 'Изменён исполнитель',
        'stopped': 'Лифт остановлен',
        'resumed': 'Ремонт продолжен',
    }
    payload = json.dumps({
        'title': (titles.get(item.kind) or 'Изменения в заявке') + f' #{ticket.pk}',
        'body': f'{item.organization.name} · {ticket.elevator.building.address} · {ticket.title}'[:220],
        'url': f'/?organization={item.organization_id}&ticket={ticket.pk}#tickets',
        'organization_id':item.organization_id, 'ticket_id':ticket.pk,
        'event_id':event.pk if event else item.pk, 'status':ticket.status, 'kind':item.kind, 'priority':ticket.priority,
        'tag': f'org-{item.organization_id}-ticket-{ticket.pk}',
    }, ensure_ascii=False)
    try:
        webpush(
            subscription_info={'endpoint': sub.endpoint,
                               'keys': {'p256dh': sub.p256dh, 'auth': sub.auth}},
            data=payload,
            vapid_private_key=config['private_file'],
            vapid_claims={'sub': config['subject']},
            ttl=300, timeout=5,
        )
    except WebPushException as exc:
        if exc.status_code in (404, 410):
            # Expired device subscription: never retry or leak endpoint into logs.
            sub.delete()
            return 'EXPIRED'
        logger.warning('push delivery failure: HTTP %s', exc.status_code)
        return 'RETRY'
    except Exception as exc:
        logger.warning('push delivery failure: %s', type(exc).__name__)
        return 'RETRY'
    return 'SENT'


def process_batch(limit=20):
    config = push_config()
    if not config['enabled'] or not config['private_file'] or not os.path.isfile(config['private_file']):
        return 0
    now = timezone.now()
    with transaction.atomic():
        rows = list(PushDelivery.objects.select_for_update(skip_locked=True)
                    .filter(Q(state='PENDING') | Q(state='PROCESSING'), next_attempt_at__lte=now)
                    .order_by('pk')[:limit])
        for row in rows:
            row.state = 'PROCESSING'
            row.attempts += 1
            row.next_attempt_at = now + timedelta(minutes=2)
        PushDelivery.objects.bulk_update(rows, ['state', 'attempts', 'next_attempt_at'])
    for row in rows:
        try:
            result = deliver_one(row, config)
        except PushSubscription.DoesNotExist:
            result = 'EXPIRED'
        if result == 'EXPIRED':
            # The subscription (and its cascading deliveries) is already gone.
            continue
        if result == 'RETRY' and row.attempts < 4:
            row.state = 'PENDING'
            row.next_attempt_at = timezone.now() + timedelta(seconds=20 * (2 ** row.attempts))
        else:
            row.state = result if result != 'RETRY' else 'FAILED'
            row.sent_at = timezone.now() if result == 'SENT' else None
        row.save(update_fields=['state', 'sent_at', 'next_attempt_at'])
    return len(rows)

