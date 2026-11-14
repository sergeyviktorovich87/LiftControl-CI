from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from hashlib import sha256
from django.core import signing
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError, PermissionDenied
from .models import DutyShift, DutySwap, Organization, Technician
from .access import org, audit


def fingerprint(organization):
    rows = list(DutyShift.objects.filter(organization=organization).order_by('id').values_list('id','technician_id','start','end','updated_at'))
    return sha256(repr(rows).encode()).hexdigest()


def make_plan(organization, data):
    try:
        first = datetime.strptime(data['from'],'%Y-%m-%d').date()
        last = datetime.strptime(data['to'],'%Y-%m-%d').date()
        start_time = time.fromisoformat(data.get('time', organization.duty_start.strftime('%H:%M')))
        count = (last-first).days+1
        every = int(data.get('every',1))
        hours = float(data.get('hours',24))
        ids = list(dict.fromkeys(int(i) for i in data['technicians']))
        tz = ZoneInfo(organization.timezone)
        if not 1 <= count <= 366 or not 1 <= every <= 31 or not 0 < hours <= 24 or not ids:
            raise ValueError()
        if len(ids) != Technician.objects.filter(organization=organization,active=True,id__in=ids).count():
            raise ValueError()
        kind = data.get('kind','daily')
        if kind not in ('daily','day','night','custom'):
            raise ValueError()
    except (ValueError,KeyError,TypeError):
        raise ValidationError('Проверьте период (до 366 дней), сотрудников и длительность смены.')
    start_bound = datetime.combine(first,time.min,tzinfo=tz)
    end_bound = datetime.combine(last+timedelta(days=1),time.min,tzinfo=tz)
    existing = list(DutyShift.objects.filter(organization=organization,start__lt=end_bound+timedelta(days=1),end__gt=start_bound))
    replaced = [s for s in existing if data.get('replace') is True and start_bound <= s.start < end_bound]
    if any(s.start <= timezone.now() for s in replaced):
        raise ValidationError('Начавшиеся смены не заменяются. Для передачи используйте подмену.')
    if replaced and DutySwap.objects.filter(shift_id__in=[s.pk for s in replaced]).exists():
        raise ValidationError('Нельзя удалять историю запросов подмены. Измените такую смену отдельно.')
    remaining = [s for s in existing if s not in replaced]
    added, skipped = [], 0
    for i, offset in enumerate(range(0,count,every)):
        start = datetime.combine(first+timedelta(days=offset), start_time, tzinfo=tz)
        end = start+timedelta(hours=hours)
        collisions = [s for s in remaining if s.start < end and s.end > start]
        if collisions:
            if data.get('replace') is True:
                raise ValidationError('Переходящая смена за границей периода пересекает план. Уточните границы или начало.')
            skipped += 1
            continue
        added.append({'technician_id':ids[i % len(ids)],'start':start,'end':end,'kind':kind})
    return added, replaced, skipped


def preview_plan(user,data):
    organization = org(user)
    with transaction.atomic():
        Organization.objects.select_for_update().get(pk=organization.pk)
        added,replaced,skipped = make_plan(organization,data)
        token = signing.dumps({'organization':organization.pk,'user':user.pk,'data':data,'fingerprint':fingerprint(organization)}, salt='duty-plan')
    return {'token':token,'added':[dict(s, start=s['start'].isoformat(),end=s['end'].isoformat()) for s in added],'replaced':len(replaced),'skipped':skipped}


def apply_plan(user,token):
    try:
        payload = signing.loads(token, salt='duty-plan', max_age=600)
    except signing.BadSignature:
        raise ValidationError('Предпросмотр устарел. Сформируйте его заново.')
    organization = org(user)
    if payload['organization'] != organization.pk or payload['user'] != user.pk:
        raise PermissionDenied()
    with transaction.atomic():
        Organization.objects.select_for_update().get(pk=organization.pk)
        if fingerprint(organization) != payload['fingerprint']:
            raise ValidationError('График уже изменён другим пользователем. Обновите предпросмотр.')
        added,replaced,skipped = make_plan(organization,payload['data'])
        if not added:
            raise ValidationError('Нет новых назначений для применения.')
        previous = [{'id':s.pk,'technician':s.technician_id,'start':s.start.isoformat(),
                     'end':s.end.isoformat(),'kind':s.kind} for s in replaced]
        DutyShift.objects.filter(id__in=[s.id for s in replaced]).delete()
        DutyShift.objects.bulk_create([DutyShift(organization=organization,**s) for s in added])
        audit(user,'duty.plan.apply',len(added),{'added':len(added),'replaced':previous})
    return {'added':len(added),'replaced':len(replaced),'skipped':skipped}

