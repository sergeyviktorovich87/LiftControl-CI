"""0.13 commissioning API.

No writable generic ModelViewSet: each mutation rechecks organization and the
exact lift assignment. All audit/progress records are append-only.
"""
import logging
from decimal import Decimal, InvalidOperation

from django.db import transaction, IntegrityError
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from PIL import Image, UnidentifiedImageError
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from .access import MemberAccess, audit, org, role
from .models import (Membership, Elevator, CommissioningProject as Project,
    CommissioningLift as Lift, CommissioningAssignment as Assignment,
    CommissioningProgress as Progress, CommissioningExpense as Expense,
    CommissioningNeed as Need, CommissioningExtraWork as Extra,
    CommissioningMessage as Message, CommissioningLiftRequest as LiftRequest)

log = logging.getLogger(__name__)


def _manager(user):
    return role(user) in ('ADMIN', 'DIRECTOR')


def _require_manager(user):
    if not _manager(user):
        raise PermissionDenied('Управление объектами наладки доступно руководителю.')


def _project(request, pk):
    p = get_object_or_404(Project, pk=pk, organization=org(request.user))
    if not _manager(request.user) and not Assignment.objects.filter(
        lift__project=p, user=request.user, active=True
    ).exists():
        raise PermissionDenied('Объект наладки не назначен вам.')
    return p


def _editable(project):
    if project.archived_at:
        raise ValidationError('Объект находится в архиве и доступен только для чтения.')


def _assigned(request, lift):
    if _manager(request.user) or not Assignment.objects.filter(
        lift=lift, user=request.user, active=True
    ).exists():
        raise PermissionDenied('Изменять прогресс может только назначенный наладчик.')


def _lift(project, pk):
    return get_object_or_404(Lift, pk=pk, project=project)


def _optional_lift(project, data):
    value = data.get('lift')
    return _lift(project, value) if value else None


def _string(data, key, limit, required=True):
    value = data.get(key, '')
    if not isinstance(value, str):
        raise ValidationError({key: 'Введите текст.'})
    value = value.strip()
    if len(value) > limit or (required and not value):
        raise ValidationError({key: f'Требуется текст до {limit} символов.'})
    return value


def _money(data, key):
    try:
        value = Decimal(str(data[key]))
    except (KeyError, TypeError, ValueError, InvalidOperation):
        raise ValidationError({key: 'Введите сумму.'})
    if not value.is_finite() or value < 0 or value >= Decimal('10000000000') or value.as_tuple().exponent < -2:
        raise ValidationError({key: 'Неверная сумма (не более двух знаков после запятой).'})
    return value


def _project_info(p, user):
    lifts = list(p.lifts.all().order_by('id'))
    result = {
        'id': p.pk, 'name': p.name, 'address': p.address,
        'archived': bool(p.archived_at),
        'lift_count': len(lifts),
        'progress': round(sum(l.progress for l in lifts) / len(lifts), 1) if lifts else 0,
    }
    if _manager(user):
        result.update(contract_amount=str(p.contract_amount), adjuster_fee=str(p.adjuster_fee))
    return result


def _details(p, user):
    result = _project_info(p, user)
    result['can_manage'] = _manager(user)
    result['lifts'] = [{
        'id': l.pk, 'label': l.label, 'factory_number': l.factory_number,
        'elevator': l.elevator_id, 'progress': l.progress, 'revision': l.revision,
        'can_edit': not _manager(user) and any(a.active and a.user_id == user.pk for a in l.assignments.all()),
        'assignees': [{
            'user': a.user_id,
            'name': a.user.get_full_name() or a.user.username,
        } for a in l.assignments.all() if a.active],
        'history': [{
            'id': h.pk, 'before': h.before, 'after': h.after,
            'description': h.description, 'author': h.actor.get_full_name() or h.actor.username,
            'created_at': h.created_at,
        } for h in l.history.all()],
    } for l in p.lifts.all().prefetch_related('assignments__user', 'history__actor').order_by('id')]
    visible_expenses = p.expenses.all() if _manager(user) else p.expenses.filter(author=user)
    result['expenses'] = [{
        'id': e.pk, 'lift': e.lift_id, 'author': e.author.get_full_name() or e.author.username,
        'amount': str(e.amount), 'description': e.description, 'created_at': e.created_at,
        'has_receipt': bool(e.receipt), 'reimbursed_at': e.reimbursed_at,
    } for e in visible_expenses.select_related('author').order_by('-id')]
    result['needs'] = [{
        'id': n.pk, 'lift': n.lift_id, 'category': n.category, 'description': n.description,
        'blocking': n.blocking, 'resolved': bool(n.resolved_at), 'created_at': n.created_at,
    } for n in p.needs.all().order_by('-id')]
    result['extras'] = [{
        'id': w.pk, 'lift': w.lift_id, 'description': w.description,
        'price': str(w.price), 'status': w.status, 'created_at': w.created_at,
    } for w in p.extras.all().order_by('-id')]
    result['messages'] = [{
        'id': m.pk, 'author': m.author.get_full_name() or m.author.username,
        'text': m.text, 'created_at': m.created_at,
    } for m in p.messages.select_related('author').order_by('-id')[:100]][::-1]
    result['lift_requests'] = [{
        'id': r.pk, 'label': r.label, 'factory_number': r.factory_number,
        'status': r.status,
    } for r in p.lift_requests.all().order_by('-id')]
    return result


@api_view(['GET', 'POST'])
@permission_classes([MemberAccess])
def projects(request):
    if request.method == 'GET':
        projects = Project.objects.filter(organization=org(request.user))
        if not _manager(request.user):
            projects = projects.filter(lifts__assignments__user=request.user,
                                       lifts__assignments__active=True).distinct()
        return Response([_project_info(p, request.user) for p in projects])
    _require_manager(request.user)
    name = _string(request.data, 'name', 200)
    address = _string(request.data, 'address', 300)
    with transaction.atomic():
        p = Project.objects.create(
            organization=org(request.user), name=name, address=address,
            contract_amount=_money(request.data, 'contract_amount') if 'contract_amount' in request.data else 0,
            adjuster_fee=_money(request.data, 'adjuster_fee') if 'adjuster_fee' in request.data else 0,
            created_by=request.user,
        )
        audit(request.user, 'commissioning.project_created', p.pk)
    log.info('commissioning_project_created project=%s', p.pk)
    return Response(_project_info(p, request.user), status=201)


@api_view(['GET'])
@permission_classes([MemberAccess])
def project(request, pk):
    return Response(_details(_project(request, pk), request.user))


@api_view(['POST'])
@permission_classes([MemberAccess])
@transaction.atomic
def add_lift(request, pk):
    _require_manager(request.user)
    p = _project(request, pk)
    _editable(p)
    label = _string(request.data, 'label', 150)
    factory = _string(request.data, 'factory_number', 100, required=False)
    related = None
    if request.data.get('elevator'):
        related = get_object_or_404(Elevator, organization=p.organization, pk=request.data['elevator'])
    if Lift.objects.filter(project=p, label=label).exists() or (
        factory and Lift.objects.filter(project=p, factory_number=factory).exists()
    ):
        raise ValidationError('Лифт с таким номером уже существует на объекте.')
    lift = Lift.objects.create(project=p, label=label, factory_number=factory, elevator=related)
    audit(request.user, 'commissioning.lift_created', lift.pk, {'project': p.pk})
    return Response({'id': lift.pk}, status=201)


@api_view(['POST'])
@permission_classes([MemberAccess])
@transaction.atomic
def assign(request, pk, lift_id):
    _require_manager(request.user)
    p = _project(request, pk)
    _editable(p)
    lift = _lift(p, lift_id)
    member = get_object_or_404(Membership, organization=p.organization,
                               user_id=request.data.get('user'), active=True, user__is_active=True)
    if member.role not in ('MECHANIC', 'ADJUSTER'):
        raise ValidationError('Назначить можно механика или наладчика.')
    active = request.data.get('active', True)
    if not isinstance(active, bool):
        raise ValidationError('active должен быть true/false.')
    assignment, _ = Assignment.objects.update_or_create(
        lift=lift, user=member.user, defaults={'active': active})
    audit(request.user, 'commissioning.assignment_changed', lift.pk,
          {'user': member.user_id, 'active': active})
    return Response({'id': assignment.pk, 'active': active})


@api_view(['POST'])
@permission_classes([MemberAccess])
@transaction.atomic
def update_progress(request, pk, lift_id):
    p = _project(request, pk)
    _editable(p)
    lift = get_object_or_404(Lift.objects.select_for_update(), pk=lift_id, project=p)
    _assigned(request, lift)
    if type(request.data.get('revision')) is not int or request.data['revision'] != lift.revision:
        raise ValidationError({'revision': 'Прогресс уже изменён. Обновите данные перед сохранением.'})
    value = request.data.get('progress')
    if type(value) is not int or not 0 <= value <= 100:
        raise ValidationError({'progress': 'Процент должен быть от 0 до 100.'})
    description = _string(request.data, 'description', 4000)
    if value == lift.progress:
        raise ValidationError('Процент не изменился.')
    before = lift.progress
    lift.progress = value
    lift.revision += 1
    lift.save(update_fields=['progress', 'revision'])
    event = Progress.objects.create(lift=lift, actor=request.user,
                                    before=before, after=value, description=description)
    audit(request.user, 'commissioning.progress_changed', lift.pk,
          {'before': before, 'after': value, 'revision': lift.revision})
    log.info('commissioning_progress lift=%s revision=%s', lift.pk, lift.revision)
    return Response({'progress': value, 'revision': lift.revision, 'event': event.pk})


@api_view(['POST'])
@permission_classes([MemberAccess])
@transaction.atomic
def request_lift(request, pk):
    p = _project(request, pk)
    _editable(p)
    if _manager(request.user):
        raise ValidationError('Руководитель добавляет лифт напрямую.')
    label = _string(request.data, 'label', 150)
    factory = _string(request.data, 'factory_number', 100, required=False)
    item = LiftRequest.objects.create(project=p, requested_by=request.user,
                                      label=label, factory_number=factory)
    audit(request.user, 'commissioning.lift_requested', item.pk, {'project': p.pk})
    return Response({'id': item.pk, 'status': item.status}, status=201)


@api_view(['POST'])
@permission_classes([MemberAccess])
@transaction.atomic
def decide_lift(request, pk, request_id):
    _require_manager(request.user)
    p = _project(request, pk)
    _editable(p)
    item = get_object_or_404(LiftRequest.objects.select_for_update(), pk=request_id, project=p)
    if item.status != 'PENDING':
        raise ValidationError('Заявка уже рассмотрена.')
    approve = request.data.get('approve')
    if not isinstance(approve, bool):
        raise ValidationError('Укажите approve: true или false.')
    if approve:
        if Lift.objects.filter(project=p, label=item.label).exists() or (
            item.factory_number and Lift.objects.filter(project=p, factory_number=item.factory_number).exists()
        ):
            raise ValidationError('Такой лифт уже есть.')
        lift = Lift.objects.create(project=p, label=item.label, factory_number=item.factory_number)
        # Approval does not grant edit rights to any other employee.
        if Membership.objects.filter(organization=p.organization, user=item.requested_by, active=True).exists():
            Assignment.objects.get_or_create(lift=lift, user=item.requested_by)
    item.status = 'APPROVED' if approve else 'REJECTED'
    item.reviewed_by = request.user
    item.save(update_fields=['status', 'reviewed_by'])
    audit(request.user, 'commissioning.lift_request_decided', item.pk, {'approved': approve})
    return Response({'id': item.pk, 'status': item.status})


@api_view(['POST'])
@permission_classes([MemberAccess])
@transaction.atomic
def add_expense(request, pk):
    p = _project(request, pk)
    _editable(p)
    if _manager(request.user):
        raise PermissionDenied('Личный расход вносит только его автор-наладчик.')
    lift = _optional_lift(p, request.data)
    if lift is not None:
        _assigned(request, lift)
    amount = _money(request.data, 'amount')
    if amount <= 0:
        raise ValidationError('Сумма должна быть положительной.')
    item = Expense.objects.create(organization=p.organization, project=p, lift=lift,
                                  author=request.user, amount=amount,
                                  description=_string(request.data, 'description', 1000))
    audit(request.user, 'commissioning.expense_created', item.pk, {'project': p.pk})
    return Response({'id': item.pk}, status=201)


@api_view(['POST'])
@permission_classes([MemberAccess])
@transaction.atomic
def upload_receipt(request, pk, expense_id):
    p = _project(request, pk)
    _editable(p)
    uploaded = request.FILES.get('receipt')
    if not uploaded or uploaded.size > 10 * 1024 * 1024:
        raise ValidationError('Требуется фото чека до 10 МБ.')
    try:
        with Image.open(uploaded) as picture:
            if picture.width * picture.height > 30_000_000:
                raise ValidationError('Слишком высокое разрешение изображения чека.')
            image_format = picture.format
            picture.verify()
        uploaded.seek(0)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise ValidationError('Файл не является безопасным изображением.')
    if image_format not in ('JPEG', 'PNG', 'WEBP'):
        raise ValidationError('Допустимы JPEG, PNG или WebP.')
    item = get_object_or_404(Expense.objects.select_for_update(),
                             pk=expense_id, project=p, author=request.user)
    if item.receipt:
        raise ValidationError('Чек уже прикреплён. Исходный файл не заменяется.')
    item.receipt.save('receipt.' + image_format.lower(), uploaded, save=True)
    audit(request.user, 'commissioning.receipt_uploaded', item.pk)
    return Response({'ok': True}, status=201)


@api_view(['GET'])
@permission_classes([MemberAccess])
def receipt(request, pk, expense_id):
    p = _project(request, pk)
    item = get_object_or_404(Expense, pk=expense_id, project=p)
    if not _manager(request.user) and item.author_id != request.user.id:
        raise PermissionDenied('Чужие чеки недоступны.')
    if not item.receipt:
        raise ValidationError('Фото чека отсутствует.')
    extension = item.receipt.name.rsplit('.', 1)[-1].lower()
    if extension not in ('jpeg', 'jpg', 'png', 'webp'):
        extension = 'jpg'
    response = FileResponse(item.receipt.open('rb'), as_attachment=True,
                            filename=f'receipt-{item.pk}.{extension}')
    response['Cache-Control'] = 'no-store, private'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@api_view(['POST'])
@permission_classes([MemberAccess])
@transaction.atomic
def reimburse(request, pk, expense_id):
    _require_manager(request.user)
    p = _project(request, pk)
    _editable(p)
    item = get_object_or_404(Expense.objects.select_for_update(), pk=expense_id, project=p)
    if item.reimbursed_at:
        raise ValidationError('Расход уже возмещён.')
    item.reimbursed_at = timezone.now()
    item.save(update_fields=['reimbursed_at'])
    audit(request.user, 'commissioning.expense_reimbursed', item.pk)
    return Response({'id': item.pk, 'reimbursed_at': item.reimbursed_at})


@api_view(['POST'])
@permission_classes([MemberAccess])
def add_need(request, pk):
    p = _project(request, pk)
    _editable(p)
    lift = _optional_lift(p, request.data)
    if lift is not None and not _manager(request.user):
        _assigned(request, lift)
    category = _string(request.data, 'category', 30, required=False) or 'OTHER'
    if category not in ('MATERIAL', 'TOOL', 'POWER', 'WATER', 'ACCESS', 'DOCUMENT', 'INSTALLATION', 'OTHER'):
        raise ValidationError('Неизвестная категория потребности.')
    blocking = request.data.get('blocking', False)
    if not isinstance(blocking, bool):
        raise ValidationError('Неверный признак блокировки.')
    item = Need.objects.create(project=p, lift=lift, author=request.user,
                               category=category, blocking=blocking,
                               description=_string(request.data, 'description', 2000))
    audit(request.user, 'commissioning.need_created', item.pk, {'project': p.pk})
    return Response({'id': item.pk}, status=201)


@api_view(['POST'])
@permission_classes([MemberAccess])
@transaction.atomic
def resolve_need(request, pk, need_id):
    _require_manager(request.user)
    p = _project(request, pk)
    _editable(p)
    item = get_object_or_404(Need.objects.select_for_update(), pk=need_id, project=p)
    if item.resolved_at:
        raise ValidationError('Потребность уже закрыта.')
    item.resolved_at = timezone.now()
    item.save(update_fields=['resolved_at'])
    audit(request.user, 'commissioning.need_resolved', item.pk)
    return Response({'id': item.pk, 'resolved': True})


@api_view(['POST'])
@permission_classes([MemberAccess])
def add_extra(request, pk):
    p = _project(request, pk)
    _editable(p)
    lift = _optional_lift(p, request.data)
    if lift is not None and not _manager(request.user):
        _assigned(request, lift)
    item = Extra.objects.create(project=p, lift=lift, author=request.user,
                                price=_money(request.data, 'price'),
                                description=_string(request.data, 'description', 2000),
                                status='APPROVED' if _manager(request.user) else 'PENDING',
                                reviewed_by=request.user if _manager(request.user) else None,
                                reviewed_at=timezone.now() if _manager(request.user) else None)
    audit(request.user, 'commissioning.extra_proposed', item.pk, {'project': p.pk})
    return Response({'id': item.pk, 'status': item.status}, status=201)


@api_view(['POST'])
@permission_classes([MemberAccess])
@transaction.atomic
def decide_extra(request, pk, extra_id):
    _require_manager(request.user)
    p = _project(request, pk)
    _editable(p)
    item = get_object_or_404(Extra.objects.select_for_update(), pk=extra_id, project=p)
    if item.status != 'PENDING':
        raise ValidationError('Решение уже принято.')
    approve = request.data.get('approve')
    if not isinstance(approve, bool):
        raise ValidationError('Укажите approve: true или false.')
    item.status = 'APPROVED' if approve else 'REJECTED'
    item.reviewed_by = request.user
    item.reviewed_at = timezone.now()
    item.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])
    audit(request.user, 'commissioning.extra_decided', item.pk, {'approved': approve})
    return Response({'id': item.pk, 'status': item.status})


@api_view(['POST'])
@permission_classes([MemberAccess])
def send_message(request, pk):
    p = _project(request, pk)
    _editable(p)
    item = Message.objects.create(project=p, author=request.user,
                                  text=_string(request.data, 'text', 4000))
    # A message is never a legal/financial approval.
    from .notification_inbox import enqueue_commissioning_message
    enqueue_commissioning_message(item)
    log.info('commissioning_message_created project=%s id=%s', p.pk, item.pk)
    return Response({'id': item.pk}, status=201)


@api_view(['POST'])
@permission_classes([MemberAccess])
@transaction.atomic
def archive_project(request, pk):
    _require_manager(request.user)
    p = get_object_or_404(Project.objects.select_for_update(), pk=pk, organization=org(request.user))
    _editable(p)
    p.archive_reason = _string(request.data, 'reason', 1000)
    p.archived_at = timezone.now()
    p.save(update_fields=['archive_reason', 'archived_at'])
    audit(request.user, 'commissioning.project_archived', p.pk, {'reason': p.archive_reason})
    return Response({'id': p.pk, 'archived': True})
