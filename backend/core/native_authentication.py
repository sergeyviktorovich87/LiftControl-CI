"""Credential verification only: importing this module must not load DRF views."""
import hashlib
import re
from datetime import timedelta
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed
from .models import MobileSession


def digest(token):
    return hashlib.sha256(token.encode('ascii')).hexdigest()


class MobileSessionAuthentication(BaseAuthentication):
    def authenticate_header(self, request):
        return 'Bearer'

    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if not parts or parts[0].lower() != b'bearer':
            return None
        if len(parts) != 2 or not re.fullmatch(rb'[A-Za-z0-9_-]{43}', parts[1]):
            raise AuthenticationFailed('Войдите в приложение заново.')
        now = timezone.now()
        row = MobileSession.objects.select_related('user').filter(
            token_hash=digest(parts[1].decode('ascii')), revoked_at__isnull=True, expires_at__gt=now,
        ).first()
        if not row or not row.user.is_active or not constant_time_compare(row.password_stamp, row.user.get_session_auth_hash()):
            raise AuthenticationFailed('Сеанс завершён. Войдите заново.')
        if row.last_seen_at < now - timedelta(minutes=5):
            MobileSession.objects.filter(pk=row.pk).update(last_seen_at=now)
        return row.user, row


