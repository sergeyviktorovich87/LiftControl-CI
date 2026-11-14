"""FCM HTTP v1: no private credentials in the API or APK, one audited event per device."""
import base64
import json
import logging
import os
import re
import time
from datetime import timedelta
from pathlib import Path
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from .access import MemberAccess, memberships
from .models import MobileSession, NativePushDevice, NativePushDelivery, Membership

logger = logging.getLogger('liftcontrol.push')
_credential_cache = {}
EVENT_STATUS = {'created': 'NEW', 'accepted': 'IN_PROGRESS', 'completed': 'DONE',
                'canceled': 'CANCELED', 'stopped': 'STOPPED', 'resumed': 'IN_PROGRESS'}


def client_config():
    try:
        data = json.loads(Path(os.environ.get('FCM_CLIENT_CONFIG_FILE', '/run/firebase-public.json')).read_text())
        if not isinstance(data, dict):
            return None
        fields = ('apiKey', 'appId', 'projectId', 'messagingSenderId')
        if any(not isinstance(data.get(k), str) or not data[k] or len(data[k]) > 256 for k in fields):
            return None
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{3,62}', data['projectId']):
            return None
        return {k: data[k] for k in fields}
    except (OSError, ValueError, TypeError):
        return None


def enabled():
    return os.getenv('FCM_ENABLED', '').lower() == 'true' and client_config() is not None


@api_view(['GET', 'POST', 'DELETE'])
@permission_classes([MemberAccess])
def device(request):
    if request.method == 'GET':
        return Response({'enabled': enabled(), 'config': client_config() if enabled() else None,
                         'provider': 'FCM', 'configured': enabled(),
                         'self_hosted': os.getenv('SELF_PUSH_ENABLED', '').lower() == 'true'})
    if not isinstance(request.auth, MobileSession):
        raise PermissionDenied('Подписка привязывается к отдельному сеансу Android.')
    if request.method == 'DELETE':
        NativePushDevice.objects.filter(session=request.auth).update(active=False)
        return Response({'ok': True})
    if not enabled():
        raise ValidationError('Системные push ещё не настроены на сервере.')
    token = request.data.get('token')
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_:.\-]{20,2048}', token):
        raise ValidationError('Неверный токен устройства.')
    with transaction.atomic():
        existing = NativePushDevice.objects.filter(token=token).first()
        if existing and existing.session_id != request.auth.pk:
            old = existing.session
            if existing.active and not old.revoked_at and old.expires_at > timezone.now():
                raise ValidationError('Сначала отключите уведомления предыдущего аккаунта на этом устройстве.')
            existing.delete()
        NativePushDevice.objects.update_or_create(session=request.auth, defaults={'token': token, 'active': True})
    return Response({'ok': True})


def enqueue_native(ticket, kind):
    event = ticket.events.order_by('-pk').first()
    eligible_users = Membership.objects.filter(organization=ticket.organization, active=True, user__is_active=True).exclude(role='ADJUSTER').exclude(
        role='MECHANIC', technician__isnull=True).exclude(role='MECHANIC', technician__active=False).values('user_id')
    devices = NativePushDevice.objects.filter(active=True, session__user_id__in=eligible_users,
        session__revoked_at__isnull=True, session__expires_at__gt=timezone.now())
    NativePushDelivery.objects.bulk_create([
        NativePushDelivery(device=d, ticket=ticket, event=event, kind=kind) for d in devices
    ], ignore_conflicts=True)


def valid_recipient(item):
    from .notification_preferences import allows, category
    session = item.device.session
    return (item.device.active and session.revoked_at is None and session.expires_at > timezone.now()
            and session.user.is_active and constant_time_compare(session.password_stamp, session.user.get_session_auth_hash())
            and memberships(session.user).filter(organization_id=item.ticket.organization_id).exclude(role='ADJUSTER').exists()
            and allows(session.user_id,item.ticket.organization_id,category(item.ticket.priority,item.kind)))


def stale(item):
    current = EVENT_STATUS.get(item.kind)
    return (current is not None and item.ticket.status != current
            or item.ticket.events.filter(pk__gt=item.event_id, action__in=list(EVENT_STATUS) + ['reassigned']).exists()
            or item.created_at < timezone.now() - timedelta(minutes=30))


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def access_token():
    import requests
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    config = client_config()
    credential_path = os.getenv('FCM_SERVICE_ACCOUNT_FILE', '')
    cache_key = (credential_path, config['projectId'])
    if _credential_cache.get('key') == cache_key and _credential_cache.get('expires', 0) > time.time() + 60:
        return _credential_cache['token']
    credentials = json.loads(Path(credential_path).read_text())
    if credentials.get('project_id') != config['projectId'] or credentials.get('token_uri') != 'https://oauth2.googleapis.com/token':
        raise ValueError('FCM credential mismatch')
    now = int(time.time())
    claims = {'iss': credentials['client_email'], 'scope': 'https://www.googleapis.com/auth/firebase.messaging',
              'aud': 'https://oauth2.googleapis.com/token', 'iat': now, 'exp': now + 3600}
    unsigned = _b64(b'{"alg":"RS256","typ":"JWT"}') + '.' + _b64(json.dumps(claims).encode())
    key = serialization.load_pem_private_key(credentials['private_key'].encode(), password=None)
    assertion = unsigned + '.' + _b64(key.sign(unsigned.encode(), padding.PKCS1v15(), hashes.SHA256()))
    result = requests.post('https://oauth2.googleapis.com/token', data={
        'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer', 'assertion': assertion}, timeout=8, allow_redirects=False)
    result.raise_for_status()
    data = result.json()
    _credential_cache.update(key=cache_key, token=data['access_token'], expires=time.time() + min(int(data['expires_in']), 3600))
    return data['access_token']


def send_one(item):
    import requests
    if not valid_recipient(item):
        return 'DISABLED'
    if stale(item):
        return 'SUPERSEDED'
    ticket = item.ticket
    config = client_config()
    # Addresses, names, photos and work text never leave our server through FCM.
    message = {'token': item.device.token, 'data': {
        'organization_id': str(ticket.organization_id), 'ticket_id': str(ticket.pk),
        'event_id': str(item.event_id), 'kind': item.kind, 'status': ticket.status,
        'priority': ticket.priority,
    }, 'android': {'priority': 'HIGH', 'ttl': '300s'}}
    response = requests.post(f"https://fcm.googleapis.com/v1/projects/{config['projectId']}/messages:send",
        headers={'Authorization': 'Bearer ' + access_token()}, json={'message': message}, timeout=8, allow_redirects=False)
    if response.status_code == 200:
        return 'SENT'
    if response.status_code == 404:
        NativePushDevice.objects.filter(pk=item.device_id).update(active=False)
        return 'DISABLED'
    if response.status_code in (401, 403):
        _credential_cache.clear()
    logger.warning('native push delivery HTTP %s', response.status_code)
    return 'RETRY'


def process_native_batch(limit=20):
    if not enabled() or not os.path.isfile(os.getenv('FCM_SERVICE_ACCOUNT_FILE', '')):
        return 0
    now = timezone.now()
    with transaction.atomic():
        rows = list(NativePushDelivery.objects.select_for_update(skip_locked=True).filter(
            Q(state='PENDING') | Q(state='PROCESSING'), next_attempt_at__lte=now).order_by('pk')[:limit])
        for row in rows:
            row.state, row.attempts = 'PROCESSING', row.attempts + 1
            row.next_attempt_at = now + timedelta(minutes=2)
        NativePushDelivery.objects.bulk_update(rows, ['state', 'attempts', 'next_attempt_at'])
    for row in rows:
        try:
            result = send_one(row)
        except Exception as exc:
            logger.warning('native push failure: %s', type(exc).__name__)
            result = 'RETRY'
        row.state = 'PENDING' if result == 'RETRY' and row.attempts < 4 else ('FAILED' if result == 'RETRY' else result)
        row.next_attempt_at = timezone.now() + timedelta(seconds=20 * 2 ** row.attempts)
        row.save(update_fields=['state', 'next_attempt_at'])
    return len(rows)
