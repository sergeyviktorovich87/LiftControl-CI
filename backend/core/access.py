from rest_framework.permissions import BasePermission, SAFE_METHODS
from rest_framework.exceptions import PermissionDenied
from .models import Organization, Membership, AuditEvent


def memberships(user):
    return Membership.objects.filter(user=user, active=True).select_related('organization', 'technician').exclude(
        role='MECHANIC', technician__isnull=True
    ).exclude(role='MECHANIC', technician__active=False)


def membership(user):
    selected = getattr(user, '_organization_id', None)
    rows = memberships(user)
    if selected is not None:
        rows = rows.filter(organization_id=selected)
    for m in rows.order_by('id'):
        if not m.technician_id or m.technician.organization_id == m.organization_id:
            return m
    return None


def select_organization(request):
    # Explicit request scope prevents cross-tab races when switching organizations.
    selected = request.headers.get('X-Organization-ID')
    if not selected and request.method in SAFE_METHODS:
        selected = request.query_params.get('organization')
    if not selected:
        selected = request.session.get('organization_id')
        if selected and not request.user.is_superuser and not memberships(request.user).filter(organization_id=selected).exists():
            selected = None  # Recover an old session after membership revocation.
    if selected:
        try:
            selected = int(selected)
            if selected <= 0:
                raise ValueError()
        except (TypeError, ValueError, OverflowError):
            raise PermissionDenied('Некорректная организация.')
    request.user._organization_id = selected
    if selected and not request.user.is_superuser and not membership(request.user):
        raise PermissionDenied('Нет доступа к выбранной организации.')


def org(user):
    m = membership(user)
    if m:
        return m.organization
    if user.is_superuser:
        selected = getattr(user, '_organization_id', None)
        organization = Organization.objects.filter(pk=selected).first() if selected else Organization.objects.order_by('id').first()
        if organization:
            return organization
    raise PermissionDenied('Нет действующего доступа к организации.')


def role(user):
    if user.is_superuser:
        return 'ADMIN'
    m = membership(user)
    return m.role if m else ''


def manager(user):
    return role(user) in ('ADMIN', 'DIRECTOR', 'DISPATCHER')


def admin_user(user):
    return role(user) == 'ADMIN'


def commissioning_access(user):
    if user.is_superuser:
        return True
    m = membership(user)
    return bool(m and (m.role in ('ADMIN', 'DIRECTOR') or m.can_commissioning))


def technician_id(user):
    m = membership(user)
    return m.technician_id if m else None


class MemberAccess(BasePermission):
    def has_permission(self, request, view):
        if not (request.user.is_authenticated and request.user.is_active):
            return False
        select_organization(request)
        current = membership(request.user)
        if current and current.role == 'ADJUSTER' and not request.user.is_superuser:
            # External adjusters must never enter maintenance, tickets, tasks,
            # chat, media library or staff APIs through a direct URL.
            route = request.path.removeprefix('/api/')
            exact = {'me/', 'organizations/', 'organizations/switch/',
                     'organizations/join/', 'logout/',
                     'mobile/push/', 'notification-preferences/'}
            limited_prefixes = ('commissioning/', 'notifications/', 'updates/', 'mobile/sessions/')
            if route not in exact and not route.startswith(limited_prefixes):
                return False
            if route == 'organizations/' and request.method != 'GET':
                return False
        return bool(request.user.is_superuser or current)


class ManagerWrite(MemberAccess):
    def has_permission(self, request, view):
        return super().has_permission(request, view) and (request.method in SAFE_METHODS or manager(request.user))


class OwnerWrite(MemberAccess):
    def has_permission(self,request,view):
        return super().has_permission(request,view) and (request.method in SAFE_METHODS or role(request.user) in ('ADMIN','DIRECTOR'))


def audit(user, action, obj_id='', details=None):
    AuditEvent.objects.create(organization=org(user), actor=user, action=action, object_id=str(obj_id), details=details or {})
