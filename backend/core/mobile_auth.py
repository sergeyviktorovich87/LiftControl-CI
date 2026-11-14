"""Native sessions are separate from browser cookies and legacy DRF tokens."""
import math
import secrets
from datetime import timedelta

from django.contrib.auth import authenticate
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework.exceptions import ValidationError, Throttled
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import MobileLoginLimit, MobileSession


from .native_authentication import MobileSessionAuthentication, digest


@transaction.atomic
def consume_login_attempt(request, username):
    now = timezone.now()
    # Never persist a password, a raw login, or an IP in the throttle table.
    keys = [('account:' + username.casefold(), 15),
            ('address:' + request.META.get('REMOTE_ADDR', ''), 120)]
    blocked_for = 0
    for value, maximum in keys:
        key = salted_hmac('mobile-login', value, algorithm='sha256').hexdigest()
        MobileLoginLimit.objects.get_or_create(key=key, defaults={'resets_at': now + timedelta(minutes=15)})
        row = MobileLoginLimit.objects.select_for_update().get(pk=key)
        if row.resets_at <= now:
            row.attempts, row.resets_at = 0, now + timedelta(minutes=15)
        if row.attempts >= maximum:
            blocked_for = max(blocked_for, math.ceil((row.resets_at - now).total_seconds()))
        else:
            row.attempts += 1
            row.save(update_fields=['attempts', 'resets_at'])
    # Bound stale rows without running an unbounded cleanup during sign-in.
    expired = list(MobileLoginLimit.objects.filter(resets_at__lte=now).values_list('pk', flat=True)[:100])
    MobileLoginLimit.objects.filter(pk__in=expired).delete()
    return blocked_for


@method_decorator(never_cache, name='dispatch')
class MobileLogin(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        username, password = request.data.get('username'), request.data.get('password')
        if not isinstance(username, str) or not isinstance(password, str) or not 1 <= len(username.strip()) <= 150 or not 1 <= len(password) <= 1024:
            raise ValidationError('Укажите логин и пароль.')
        username = username.strip()
        wait = consume_login_attempt(request, username)
        if wait:
            raise Throttled(wait=wait, detail='Слишком много попыток входа. Попробуйте позже.')
        user = authenticate(request, username=username, password=password)
        if not user or not user.is_active:
            return Response({'detail': 'Неверный логин или пароль.'}, status=401)
        name = request.data.get('device_name', 'Android')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or any(ord(c) < 32 for c in name):
            raise ValidationError('Некорректное имя устройства.')
        now = timezone.now()
        secret = secrets.token_urlsafe(32)
        with transaction.atomic():
            # Serialize concurrent sign-ins by this account; retain at most ten devices.
            type(user).objects.select_for_update().get(pk=user.pk)
            active = MobileSession.objects.filter(user=user, revoked_at__isnull=True, expires_at__gt=now)
            oldest = list(active.order_by('-created_at').values_list('pk', flat=True)[9:])
            MobileSession.objects.filter(pk__in=oldest).update(revoked_at=now)
            row = MobileSession.objects.create(user=user, token_hash=digest(secret), password_stamp=user.get_session_auth_hash(),
                device_name=name.strip(), expires_at=now + timedelta(days=30))
        from .organizations import available_organizations
        return Response({'token': secret, 'expires_at': row.expires_at,
            'user': {'id': user.pk, 'name': user.get_full_name() or user.username},
            'organizations': available_organizations(user)}, status=200)


@method_decorator(never_cache, name='dispatch')
class NativeView(APIView):
    authentication_classes = [MobileSessionAuthentication]
    permission_classes = [IsAuthenticated]


class MobileLogout(NativeView):
    def post(self, request):
        MobileSession.objects.filter(pk=request.auth.pk).update(revoked_at=timezone.now())
        return Response({'ok': True})


class MobileSessions(NativeView):
    def get(self, request):
        rows = MobileSession.objects.filter(user=request.user, revoked_at__isnull=True, expires_at__gt=timezone.now()).order_by('-created_at')
        return Response([{'id': r.pk, 'device_name': r.device_name, 'created_at': r.created_at,
            'expires_at': r.expires_at, 'last_seen_at': r.last_seen_at, 'current': r.pk == request.auth.pk} for r in rows])


class MobileRevoke(NativeView):
    def post(self, request, session_id):
        row = get_object_or_404(MobileSession, pk=session_id, user=request.user)
        MobileSession.objects.filter(pk=row.pk).update(revoked_at=timezone.now())
        return Response({'ok': True})
