"""Organization membership is independent for each employer; invitations are one-use secrets."""
import hashlib
import re
import secrets
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django.db import transaction
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.middleware.csrf import get_token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .access import MemberAccess, audit, membership, memberships, org, role, select_organization
from .models import Membership, Organization, OrganizationInvite, Technician


def available_organizations(user):
    roles = {m.organization_id: m.role for m in memberships(user)}
    rows = Organization.objects.all() if user.is_superuser else Organization.objects.filter(pk__in=roles)
    return [{'id': o.pk, 'name': o.name, 'timezone': o.timezone,
             'role': 'ADMIN' if user.is_superuser else roles[o.pk],
             'duty_start': o.duty_start.strftime('%H:%M')} for o in rows.order_by('name', 'id')]


def require_owner(user):
    if role(user) not in ('ADMIN', 'DIRECTOR'):
        raise PermissionDenied('Управлять доступом может администратор или директор организации.')


def technician_number(value):
    try:
        number=int(value)
        if isinstance(value,bool) or number<=0:
            raise ValueError()
        return number
    except (TypeError,ValueError,OverflowError):
        raise ValidationError({'technician':'Выберите сотрудника из списка.'})


def member_data(m):
    return {'id': m.pk, 'user': m.user_id, 'name': m.user.get_full_name() or m.user.username,
            'role': m.role, 'technician': m.technician_id, 'active': m.active}


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def organizations(request):
    if request.method == 'GET':
        return Response({'organizations':available_organizations(request.user),'csrf':get_token(request)})
    select_organization(request)
    require_owner(request.user)
    name = str(request.data.get('name', '')).strip()
    zone = request.data.get('timezone', 'Asia/Yakutsk')
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise ValidationError({'timezone': 'Неизвестный часовой пояс.'})
    if not 2 <= len(name) <= 200:
        raise ValidationError({'name': 'Название организации: 2–200 символов.'})
    with transaction.atomic():
        organization = Organization.objects.create(name=name, timezone=zone)
        Membership.objects.create(user=request.user, organization=organization, role='ADMIN')
        previous = getattr(request.user, '_organization_id', None)
        request.user._organization_id = organization.pk
        audit(request.user, 'organization.created', organization.pk)
        request.user._organization_id = previous
    return Response({'id': organization.pk, 'name': organization.name}, status=201)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def switch_organization(request):
    try:
        selected = int(request.data.get('organization', 0))
    except (TypeError, ValueError, OverflowError):
        raise ValidationError('Выберите организацию.')
    if selected not in [o['id'] for o in available_organizations(request.user)]:
        raise PermissionDenied('Нет доступа к выбранной организации.')
    request.session['organization_id'] = selected
    return Response({'organization_id': selected})


@api_view(['GET', 'POST'])
@permission_classes([MemberAccess])
def organization_invites(request):
    require_owner(request.user)
    organization = org(request.user)
    if request.method == 'GET':
        # Never expose hashes or reuse a secret invitation code.
        return Response(list(OrganizationInvite.objects.filter(organization=organization).order_by('-pk').values(
            'id','role','technician_id','expires_at','used_at','revoked_at')[:100]))
    requested_role = request.data.get('role', 'MECHANIC')
    if requested_role not in Membership.Role.values or requested_role == 'ADMIN' and role(request.user) != 'ADMIN':
        raise PermissionDenied('Недоступная роль приглашения.')
    tech = None
    if request.data.get('technician'):
        tech = get_object_or_404(Technician, pk=technician_number(request.data['technician']), organization=organization, active=True)
        if Membership.objects.filter(technician=tech).exists():
            raise ValidationError('Сотрудник уже связан с учётной записью. Измените его доступ в списке сотрудников.')
    if requested_role == 'MECHANIC' and not tech:
        raise ValidationError('Выберите механика из списка организации.')
    if requested_role == 'ADJUSTER' and tech:
        raise ValidationError('Приглашённый наладчик не должен быть привязан к штатной карточке механика.')
    code = secrets.token_urlsafe(32)
    invite = OrganizationInvite.objects.create(
        organization=organization, role=requested_role, technician=tech,
        token_hash=hashlib.sha256(code.encode()).hexdigest(), created_by=request.user,
        expires_at=timezone.now() + timedelta(days=7))
    audit(request.user, 'organization.invited', invite.pk, {'role':requested_role,'technician':tech.pk if tech else None})
    return Response({'id':invite.pk, 'code':code, 'expires_at':invite.expires_at}, status=201)


@api_view(['POST'])
@permission_classes([MemberAccess])
@transaction.atomic
def revoke_invite(request, invite_id):
    require_owner(request.user)
    Organization.objects.select_for_update().get(pk=org(request.user).pk)
    invite = get_object_or_404(OrganizationInvite.objects.select_for_update(), pk=invite_id, organization=org(request.user))
    invite.revoked_at = timezone.now()
    invite.save(update_fields=['revoked_at'])
    audit(request.user, 'organization.invite_revoked', invite.pk)
    return Response({'ok':True})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@transaction.atomic
def join_organization(request):
    code = request.data.get('code', '')
    if not isinstance(code, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', code):
        raise ValidationError('Неверный код приглашения.')
    invite = get_object_or_404(OrganizationInvite, token_hash=hashlib.sha256(code.encode()).hexdigest())
    Organization.objects.select_for_update().get(pk=invite.organization_id)
    invite = OrganizationInvite.objects.select_for_update().get(pk=invite.pk)
    if invite.used_at or invite.revoked_at or invite.expires_at <= timezone.now():
        raise ValidationError('Приглашение уже использовано, отозвано или истекло.')
    if Membership.objects.filter(user=request.user, organization=invite.organization).exists():
        raise ValidationError('Учётная запись уже связана с организацией. Изменение доступа выполняет её администратор.')
    issuer = invite.created_by
    issuer._organization_id = invite.organization_id
    if not issuer.is_active or role(issuer) not in ('ADMIN','DIRECTOR') or invite.role == 'ADMIN' and role(issuer) != 'ADMIN':
        raise ValidationError('Автор приглашения больше не может предоставлять этот доступ.')
    if invite.technician_id:
        tech = invite.technician
        if not tech.active or tech.organization_id != invite.organization_id or Membership.objects.filter(technician=tech).exists():
            raise ValidationError('Сотрудник уже связан с аккаунтом или недоступен.')
    Membership.objects.create(user=request.user, organization=invite.organization, role=invite.role, technician=invite.technician)
    invite.used_at, invite.used_by = timezone.now(), request.user
    invite.save(update_fields=['used_at','used_by'])
    request.user._organization_id = invite.organization_id
    audit(request.user, 'organization.joined', invite.organization_id)
    return Response({'organization_id':invite.organization_id, 'name':invite.organization.name})


@api_view(['GET'])
@permission_classes([MemberAccess])
def organization_members(request):
    require_owner(request.user)
    return Response([member_data(m) for m in Membership.objects.filter(organization=org(request.user)).select_related('user')])


@api_view(['POST'])
@permission_classes([MemberAccess])
@transaction.atomic
def create_employee_account(request):
    """Create a login, a company-scoped staff card and membership together."""
    require_owner(request.user)
    organization = org(request.user)
    username = str(request.data.get('username', '')).strip()
    full_name = str(request.data.get('full_name', '')).strip()
    password = request.data.get('password')
    phone = str(request.data.get('phone', '')).strip()
    requested_role = request.data.get('role', 'MECHANIC')
    if not re.fullmatch(r'[\w.@+-]{3,150}', username):
        raise ValidationError({'username': 'Логин: 3–150 символов, буквы, цифры и . @ + - _.'})
    if requested_role not in Membership.Role.values:
        raise ValidationError({'role': 'Неизвестная роль.'})
    if requested_role == 'ADMIN' and role(request.user) != 'ADMIN':
        raise PermissionDenied('Назначить администратора может только администратор.')
    if len(phone) > 50:
        raise ValidationError({'phone': 'Телефон слишком длинный.'})
    tech = None
    if request.data.get('technician'):
        tech = get_object_or_404(
            Technician, pk=technician_number(request.data['technician']),
            organization=organization, active=True,
        )
        if Membership.objects.filter(technician=tech).exists():
            raise ValidationError({'technician': 'Эта карточка уже привязана к учётной записи.'})
        if not full_name:
            full_name = tech.full_name
    if not 2 <= len(full_name) <= 200:
        raise ValidationError({'full_name': 'Укажите ФИО сотрудника (2–200 символов).'})
    User = get_user_model()
    if User.objects.filter(username__iexact=username).exists():
        raise ValidationError({'username': 'Логин уже существует. Для совместительства используйте приглашение.'})
    if not isinstance(password, str):
        raise ValidationError({'password': 'Укажите пароль.'})
    names = full_name.split(' ', 1)
    account = User(username=username, first_name=names[0][:150],
                   last_name=names[1][:150] if len(names) > 1 else '')
    try:
        validate_password(password, user=account)
    except DjangoValidationError as exc:
        raise ValidationError({'password': exc.messages})
    if requested_role == 'ADJUSTER' and tech:
        raise ValidationError({'technician': 'Внешний наладчик работает без штатной карточки механика.'})
    if not tech and requested_role != 'ADJUSTER':
        staff_role = {
            'MECHANIC': Technician.Role.MECHANIC,
            'DISPATCHER': Technician.Role.DISPATCHER,
        }.get(requested_role, Technician.Role.ENGINEER)
        tech = Technician.objects.create(
            organization=organization, full_name=full_name, phone=phone, role=staff_role,
        )
    account.set_password(password)
    account.save()
    member = Membership(user=account, organization=organization,
                        role=requested_role, technician=tech)
    member.full_clean()
    member.save()
    audit(request.user, 'organization.employee_created', member.pk,
          {'user': account.pk, 'technician': tech.pk if tech else None, 'role': requested_role})
    return Response(member_data(member) | {'username': username}, status=201)


@api_view(['PATCH'])
@permission_classes([MemberAccess])
@transaction.atomic
def update_member(request, member_id):
    require_owner(request.user)
    organization = org(request.user)
    Organization.objects.select_for_update().get(pk=organization.pk)
    target = get_object_or_404(Membership.objects.select_for_update(), pk=member_id, organization=organization)
    new_role = request.data.get('role', target.role)
    active = request.data.get('active', target.active)
    if new_role not in Membership.Role.values or not isinstance(active, bool):
        raise ValidationError('Проверьте роль и активность доступа.')
    if role(request.user) != 'ADMIN' and (new_role == 'ADMIN' or target.role == 'ADMIN'):
        raise PermissionDenied('Доступ администратора изменяет только администратор.')
    if target.user_id == request.user.pk and (not active or new_role != target.role):
        raise ValidationError('Свой доступ изменяет другой администратор.')
    tech = target.technician
    if 'technician' in request.data:
        tech = get_object_or_404(Technician, pk=technician_number(request.data['technician']), organization=organization, active=True) if request.data['technician'] else None
    if new_role == 'MECHANIC' and not tech:
        raise ValidationError('Для механика выберите сотрудника.')
    if new_role == 'ADJUSTER' and tech:
        raise ValidationError('Для внешнего наладчика карточка штатного сотрудника не требуется. Уберите привязку.')
    if tech and Membership.objects.filter(technician=tech).exclude(pk=target.pk).exists():
        raise ValidationError('Сотрудник уже связан с другой учётной записью.')
    before = {'role':target.role, 'active':target.active, 'technician':target.technician_id}
    target.role, target.active, target.technician = new_role, active, tech
    target.full_clean()
    target.save()
    audit(request.user, 'organization.member_changed', target.pk, {'before':before,'after':{'role':new_role,'active':active,'technician':target.technician_id}})
    return Response(member_data(target))
