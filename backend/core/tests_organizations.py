"""Security and workflow regressions for multiple employers, 09:00 shifts and real import."""
import io
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone as utc
from pathlib import Path
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from .models import (Organization, Membership, Technician, Building, Elevator, Ticket,
    DutyShift, DutySwap, AuditEvent, PushSubscription, PushDelivery, OrganizationInvite, ChatMessage)


class OrganizationDutyTests(TestCase):
    def setUp(self):
        self.a = Organization.objects.get(pk=1)
        self.b = Organization.objects.create(name='Вторая компания')
        self.third = Organization.objects.create(name='Недоступная компания')
        self.owner = get_user_model().objects.create_user('owner')
        self.worker = get_user_model().objects.create_user('worker')
        self.peer = get_user_model().objects.create_user('peer')
        self.ta = Technician.objects.create(organization=self.a, full_name='Основная работа')
        self.tb = Technician.objects.create(organization=self.b, full_name='Совместительство')
        self.tp = Technician.objects.create(organization=self.a, full_name='Сменщик')
        Membership.objects.create(user=self.owner,organization=self.a,role='ADMIN')
        Membership.objects.create(user=self.owner,organization=self.b,role='DIRECTOR')
        Membership.objects.create(user=self.worker,organization=self.a,role='MECHANIC',technician=self.ta)
        Membership.objects.create(user=self.worker,organization=self.b,role='MECHANIC',technician=self.tb)
        Membership.objects.create(user=self.peer,organization=self.a,role='MECHANIC',technician=self.tp)
        self.ba = Building.objects.create(organization=self.a,name='Первый',address='Первый 1')
        self.bb = Building.objects.create(organization=self.b,name='Второй',address='Второй 2')
        self.ea = Elevator.objects.create(organization=self.a,building=self.ba,assigned_technician=self.ta,factory_number='1')
        self.eb = Elevator.objects.create(organization=self.b,building=self.bb,assigned_technician=self.tb,factory_number='1')
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.now = timezone.now()

    def get(self,path,company=None):
        return self.client.get('/api/'+path,HTTP_X_ORGANIZATION_ID=str((company or self.a).pk))

    def post(self,path,data=None,company=None):
        return self.client.post('/api/'+path,data or {},format='json',HTTP_X_ORGANIZATION_ID=str((company or self.a).pk))

    def shift(self,tech=None,start=None,end=None):
        return DutyShift.objects.create(organization=self.a,technician=tech or self.ta,
            start=start or self.now-timedelta(hours=1),end=end or self.now+timedelta(hours=23))

    def test_create_employee_login_and_card_atomically(self):
        response = self.post('organizations/members/create/', {
            'username': 'newmechanic', 'password': 'LongUniqueTestPass-2026!safe',
            'full_name': 'Новый Механик', 'phone': '+79991112233', 'role': 'MECHANIC',
        })
        self.assertEqual(response.status_code, 201, response.content)
        account = get_user_model().objects.get(username='newmechanic')
        self.assertTrue(account.check_password('LongUniqueTestPass-2026!safe'))
        self.assertFalse(account.is_staff)
        self.assertFalse(account.is_superuser)
        member = Membership.objects.get(user=account, organization=self.a)
        self.assertEqual(member.technician.full_name, 'Новый Механик')
        self.assertEqual(member.technician.organization, self.a)
        self.assertEqual(member.role, 'MECHANIC')
        self.assertEqual(response.json()['technician'], member.technician_id)
        self.assertNotIn('password', response.json())
        self.assertEqual(AuditEvent.objects.filter(action='organization.employee_created').count(), 1)
        self.client.force_authenticate(account)
        self.assertEqual(self.get('me/').json()['technician'], member.technician_id)
        self.assertEqual(self.get('me/', self.b).status_code, 403)

    def test_create_login_with_imported_card_does_not_duplicate_it(self):
        orphan = Technician.objects.create(organization=self.a, full_name='Импортированный без логина')
        before = Technician.objects.count()
        response = self.post('organizations/members/create/', {
            'username': 'imported_user', 'password': 'UniqueImportedPassword-2026!',
            'full_name': '', 'technician': orphan.pk, 'role': 'MECHANIC',
        })
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Technician.objects.count(), before)
        self.assertEqual(Membership.objects.get(user__username='imported_user').technician, orphan)
        duplicated = self.post('organizations/members/create/', {
            'username': 'duplicate_staff', 'password': 'UniqueImportedPassword-2026!',
            'full_name': 'Другой', 'technician': orphan.pk, 'role': 'MECHANIC',
        })
        self.assertEqual(duplicated.status_code, 400)
        self.assertFalse(get_user_model().objects.filter(username='duplicate_staff').exists())

    def test_employee_creation_rejects_invalid_or_foreign_and_unprivileged(self):
        base = {'username': 'candidate', 'password': 'UniqueComplexPassword-2026!',
                'full_name': 'Кандидат Тест', 'role': 'MECHANIC'}
        self.assertEqual(self.post('organizations/members/create/', base | {'technician': self.tb.pk}).status_code, 404)
        self.assertEqual(self.post('organizations/members/create/', base | {'password': '123'}).status_code, 400)
        self.assertEqual(self.post('organizations/members/create/', base | {'role': 'UNKNOWN'}).status_code, 400)
        self.assertFalse(get_user_model().objects.filter(username='candidate').exists())
        self.client.force_authenticate(self.worker)
        self.assertEqual(self.post('organizations/members/create/', base).status_code, 403)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.post('organizations/members/create/', base | {'role': 'ADMIN'}, self.b).status_code, 403)
        self.assertEqual(self.post('organizations/members/create/', base).status_code, 201)
        self.assertEqual(self.post('organizations/members/create/', base).status_code, 400)

    @override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
    def test_django_admin_add_user_creates_company_staff_and_membership(self):
        orphan = Technician.objects.create(organization=self.a, full_name='Карточка без аккаунта')
        superuser = get_user_model().objects.create_superuser(
            username='su_onboard', password='StrongRootPassword-2026!')
        self.client.force_authenticate(None)
        self.client.force_login(superuser)
        response = self.client.post('/admin/auth/user/add/', {
            'username': 'admincreated', 'password1': 'TestAdminUserPassword-2026!',
            'password2': 'TestAdminUserPassword-2026!', 'first_name': 'Иван',
            'last_name': 'Петров', 'email': '', 'organization': self.a.pk,
            'role': 'MECHANIC', 'technician': orphan.pk, 'phone': '',
            'memberships-TOTAL_FORMS': '0', 'memberships-INITIAL_FORMS': '0',
            'memberships-MIN_NUM_FORMS': '0', 'memberships-MAX_NUM_FORMS': '1000',
            '_save': 'Сохранить',
        })
        errors = response.context_data['adminform'].form.errors if response.status_code == 200 else ''
        self.assertEqual(response.status_code, 302, errors)
        member = Membership.objects.get(user__username='admincreated', organization=self.a)
        self.assertEqual(member.technician, orphan)
        self.assertTrue(member.user.check_password('TestAdminUserPassword-2026!'))

    def test_one_login_independent_roles_and_tenant_data(self):
        self.assertEqual(self.get('me/').json()['role'],'ADMIN')
        self.assertEqual(self.get('me/',self.b).json()['role'],'DIRECTOR')
        self.assertEqual(len(self.get('me/').json()['organizations']),2)
        self.assertEqual([e['id'] for e in self.get('elevators/').json()],[self.ea.pk])
        self.assertEqual([e['id'] for e in self.get('elevators/',self.b).json()],[self.eb.pk])
        self.assertEqual(self.get('elevators/',self.third).status_code,403)
        self.assertEqual(self.post('tickets/',{'elevator':self.eb.pk,'title':'Чужой лифт'}).status_code,400)

    def test_switch_session_does_not_override_explicit_other_tab(self):
        self.assertEqual(self.post('organizations/switch/',{'organization':self.b.pk}).status_code,200)
        self.assertEqual(self.client.get('/api/me/').json()['organization_id'],self.b.pk)
        self.assertEqual(self.get('me/').json()['organization_id'],self.a.pk)
        self.assertEqual(self.post('organizations/switch/',{'organization':self.third.pk}).status_code,403)

    def test_privilege_bound_to_employer_not_user_or_submitted_role(self):
        Membership.objects.filter(user=self.worker,organization=self.a).update(role='DISPATCHER')
        self.client.force_authenticate(self.worker)
        self.assertTrue(self.get('me/').json()['can_create_ticket'])
        self.assertFalse(self.get('me/',self.b).json()['can_create_ticket'])
        self.assertEqual(self.post('organizations/',{'name':'Нельзя'}).status_code,403)
        self.assertEqual(self.post('organizations/invites/',{'role':'ADMIN'}).status_code,403)
        self.assertEqual(self.post('tickets/',{'elevator':self.eb.pk,'title':'Нельзя','role':'ADMIN'},self.b).status_code,403)

    def test_invite_new_account_second_employer_once_and_revocation(self):
        tech = Technician.objects.create(organization=self.b,full_name='Новый сотрудник')
        result = self.post('organizations/invites/',{'technician':tech.pk,'role':'MECHANIC'},self.b)
        self.assertEqual(result.status_code,201)
        code = result.json()['code']
        self.assertNotIn(code,self.get('organizations/invites/',self.b).content.decode())
        newcomer = get_user_model().objects.create_user('newcomer')
        self.client.force_authenticate(newcomer)
        self.assertIn('csrf',self.get('organizations/').json())
        joined = self.post('organizations/join/',{'code':code})
        self.assertEqual(joined.status_code,200)
        self.assertEqual(self.get('me/',self.b).json()['technician'],tech.pk)
        self.assertEqual(self.post('organizations/join/',{'code':code}).status_code,400)
        self.client.force_authenticate(self.owner)
        invite = self.post('organizations/invites/',{'role':'DISPATCHER'},self.b).json()
        self.assertEqual(self.post(f"organizations/invites/{invite['id']}/revoke/",company=self.b).status_code,200)
        self.client.force_authenticate(self.peer)
        self.assertEqual(self.post('organizations/join/',{'code':invite['code']}).status_code,400)

    def test_invite_rechecks_issuer_permissions_and_director_cannot_grant_admin(self):
        self.assertEqual(self.post('organizations/invites/',{'role':'ADMIN'},self.b).status_code,403)
        invite = self.post('organizations/invites/',{'role':'DISPATCHER'}).json()
        Membership.objects.filter(user=self.owner,organization=self.a).update(active=False)
        self.client.force_authenticate(self.peer)
        self.assertEqual(self.post('organizations/join/',{'code':invite['code']}).status_code,400)

    def test_revocation_independent_and_foreign_member_modification_denied(self):
        target = Membership.objects.get(user=self.worker,organization=self.a)
        response = self.client.patch(f'/api/organizations/members/{target.pk}/',{'active':False},format='json',HTTP_X_ORGANIZATION_ID=str(self.a.pk))
        self.assertEqual(response.status_code,200)
        self.client.force_authenticate(self.worker)
        self.assertEqual(self.get('me/').status_code,403)
        self.assertEqual(self.get('me/',self.b).status_code,200)
        self.client.force_authenticate(self.owner)
        response = self.client.patch(f'/api/organizations/members/{target.pk}/',{'active':True},format='json',HTTP_X_ORGANIZATION_ID=str(self.b.pk))
        self.assertEqual(response.status_code,404)

    def test_create_new_company_grants_only_creator_and_preserves_existing(self):
        created = self.post('organizations/',{'name':'Третья работа','timezone':'Asia/Yakutsk'})
        self.assertEqual(created.status_code,201)
        company = Organization.objects.get(pk=created.json()['id'])
        self.assertEqual(Membership.objects.get(organization=company).user,self.owner)
        self.assertFalse(Elevator.objects.filter(organization=company).exists())
        self.client.force_authenticate(self.worker)
        self.assertEqual(self.get('me/',company).status_code,403)

    def test_exact_0900_boundary_and_acknowledgment_not_privilege_gate(self):
        start = datetime(2026,11,8,0,0,tzinfo=utc.utc)  # 09:00 in Blagoveshchensk
        end = start+timedelta(days=1)
        shift = self.shift(start=start,end=end)
        self.client.force_authenticate(self.worker)
        with patch('django.utils.timezone.now',return_value=start-timedelta(microseconds=1)):
            self.assertFalse(self.get('me/').json()['on_duty'])
        with patch('django.utils.timezone.now',return_value=start):
            self.assertTrue(self.get('me/').json()['can_create_ticket'])
            self.assertIsNone(self.get('me/').json()['duty_shift']['acknowledged_at'])
            self.assertEqual(self.post(f'duty/{shift.pk}/acknowledge/').status_code,200)
            self.assertEqual(self.post(f'duty/{shift.pk}/acknowledge/').status_code,200)
            self.assertEqual(AuditEvent.objects.filter(action='duty.acknowledged').count(),1)
            self.assertFalse(self.get('me/',self.b).json()['on_duty'])
        with patch('django.utils.timezone.now',return_value=end):
            self.assertFalse(self.get('me/').json()['on_duty'])
            self.assertEqual(self.post('tickets/',{'elevator':self.ea.pk,'title':'После смены'}).status_code,403)

    def test_daily_plan_defaults_to_nine_and_single_duty_per_company(self):
        r = self.post('duty/preview/',{'from':'2027-01-01','to':'2027-01-02','technicians':[self.ta.pk,self.tp.pk]})
        self.assertEqual(r.status_code,200)
        self.assertIn('T09:00:00+09:00',r.json()['added'][0]['start'])
        self.assertEqual(self.post('duty/apply/',{'token':r.json()['token']}).status_code,200)
        first = DutyShift.objects.first()
        r = self.post('duty/',{'technician':self.tp.pk,'start':first.start.isoformat(),'end':first.end.isoformat()})
        self.assertEqual(r.status_code,400)

    def test_future_shift_can_be_edited_repeatedly_with_audit(self):
        start = self.now + timedelta(days=10)
        shift = self.shift(start=start,end=start+timedelta(days=1))
        path = f'/api/duty/{shift.pk}/'
        headers = {'HTTP_X_ORGANIZATION_ID':str(self.a.pk)}
        first = self.client.patch(path,{'technician':self.tp.pk},format='json',**headers)
        self.assertEqual(first.status_code,200,first.content)
        second = self.client.patch(path,{'technician':self.ta.pk},format='json',**headers)
        self.assertEqual(second.status_code,200,second.content)
        shift.refresh_from_db()
        self.assertEqual(shift.technician,self.ta)
        events = AuditEvent.objects.filter(action='duty.edit',organization=self.a).order_by('id')
        self.assertEqual(events.count(),2)
        self.assertEqual(events.first().details['before']['technician'],self.ta.pk)
        self.assertEqual(events.first().details['after']['technician'],self.tp.pk)
        self.client.force_authenticate(self.worker)
        denied = self.client.patch(path,{'technician':self.tp.pk},format='json',**headers)
        self.assertEqual(denied.status_code,403)
        self.client.force_authenticate(self.owner)
        other_company = self.client.patch(path,{'technician':self.tp.pk},format='json',
                                          HTTP_X_ORGANIZATION_ID=str(self.b.pk))
        self.assertEqual(other_company.status_code,404)

    def test_repeat_period_replaces_future_assignments_only_on_request(self):
        payload = {'from':'2099-01-01','to':'2099-01-03',
                   'technicians':[self.ta.pk,self.tp.pk]}
        first = self.post('duty/preview/',payload)
        self.assertEqual(first.status_code,200,first.content)
        result = self.post('duty/apply/',{'token':first.json()['token']})
        self.assertEqual(result.status_code,200,result.content)
        old = list(DutyShift.objects.filter(organization=self.a).values_list('pk',flat=True))
        self.assertEqual(len(old),3)
        no_replace = self.post('duty/preview/',payload)
        self.assertEqual(no_replace.status_code,200,no_replace.content)
        self.assertEqual(len(no_replace.json()['added']),0)
        self.assertEqual(no_replace.json()['skipped'],3)
        self.assertEqual(no_replace.json()['replaced'],0)
        revised = self.post('duty/preview/',{
            **payload,'technicians':[self.tp.pk],'replace':True,
        })
        self.assertEqual(revised.status_code,200,revised.content)
        self.assertEqual(revised.json()['replaced'],3)
        self.assertEqual(len(revised.json()['added']),3)
        applied = self.post('duty/apply/',{'token':revised.json()['token']})
        self.assertEqual(applied.status_code,200,applied.content)
        self.assertEqual(applied.json()['replaced'],3)
        self.assertEqual(DutyShift.objects.filter(organization=self.a).count(),3)
        self.assertFalse(DutyShift.objects.filter(pk__in=old).exists())
        self.assertEqual(set(DutyShift.objects.values_list('technician_id',flat=True)),{self.tp.pk})

    def test_swap_current_shift_requires_target_acceptance_and_preserves_past(self):
        shift = self.shift()
        self.client.force_authenticate(self.worker)
        r = self.post(f'duty/{shift.pk}/swap/',{'technician':self.tp.pk,'reason':'Срочная подмена по семейным причинам'})
        self.assertEqual(r.status_code,201)
        swap = r.json()['id']
        self.assertTrue(self.get('me/').json()['on_duty'])
        self.assertEqual(self.post(f'duty-swaps/{swap}/accept/').status_code,403)
        self.client.force_authenticate(self.peer)
        self.assertFalse(self.get('me/').json()['on_duty'])
        accepted = self.post(f'duty-swaps/{swap}/accept/')
        self.assertEqual(accepted.status_code,200)
        self.assertTrue(self.get('me/').json()['on_duty'])
        shift.refresh_from_db()
        next_shift = DutyShift.objects.exclude(pk=shift.pk).get()
        self.assertEqual(shift.end,next_shift.start)
        self.assertEqual(next_shift.technician,self.tp)
        self.assertIsNotNone(next_shift.acknowledged_at)
        self.assertEqual(self.post(f'duty-swaps/{swap}/accept/').status_code,400)
        self.client.force_authenticate(self.worker)
        self.assertFalse(self.get('me/').json()['on_duty'])
        self.assertEqual(AuditEvent.objects.filter(action='duty.swap_accepted').count(),1)

    def test_manager_can_arrange_handover_when_original_mechanic_is_inactive(self):
        shift=self.shift()
        self.ta.active=False
        self.ta.save(update_fields=['active'])
        request=self.post(f'duty/{shift.pk}/swap/',{'technician':self.tp.pk,'reason':'Назначенный механик недоступен'})
        self.assertEqual(request.status_code,201)
        self.client.force_authenticate(self.peer)
        self.assertEqual(self.post(f"duty-swaps/{request.json()['id']}/accept/").status_code,200)
        self.assertTrue(self.get('me/').json()['on_duty'])

    def test_swap_future_shift_stale_schedule_and_foreign_staff(self):
        shift = self.shift(start=self.now+timedelta(days=2),end=self.now+timedelta(days=3))
        self.client.force_authenticate(self.worker)
        self.assertEqual(self.post(f'duty/{shift.pk}/swap/',{'technician':self.tb.pk,'reason':'Другая организация'}).status_code,404)
        row = self.post(f'duty/{shift.pk}/swap/',{'technician':self.tp.pk,'reason':'Плановая замена'}).json()
        self.assertEqual(self.post(f'duty/{shift.pk}/swap/',{'technician':self.tp.pk,'reason':'Повторный запрос'}).status_code,400)
        DutyShift.objects.filter(pk=shift.pk).update(updated_at=self.now+timedelta(minutes=1))
        self.client.force_authenticate(self.peer)
        self.assertEqual(self.post(f"duty-swaps/{row['id']}/accept/").status_code,400)
        self.assertEqual(self.post(f"duty-swaps/{row['id']}/cancel/").status_code,200)

    def test_pending_swap_prevents_reassignment_until_resolved(self):
        start = self.now + timedelta(days=5)
        shift = self.shift(start=start, end=start+timedelta(days=1))
        self.client.force_authenticate(self.worker)
        requested = self.post(f'duty/{shift.pk}/swap/', {
            'technician': self.tp.pk, 'reason': 'Будущая подмена'
        })
        self.assertEqual(requested.status_code, 201, requested.content)
        self.client.force_authenticate(self.owner)
        denied = self.client.patch(f'/api/duty/{shift.pk}/', {
            'technician': self.tp.pk
        }, format='json', HTTP_X_ORGANIZATION_ID=str(self.a.pk))
        self.assertEqual(denied.status_code, 400, denied.content)
        shift.refresh_from_db()
        self.assertEqual(shift.technician_id, self.ta.pk)

    def test_repeat_plan_preserves_history_of_pending_swap(self):
        start = datetime(2099,1,1,0,tzinfo=utc.utc)
        shift = self.shift(start=start,end=start+timedelta(days=1))
        self.client.force_authenticate(self.worker)
        requested = self.post(f'duty/{shift.pk}/swap/',{
            'technician':self.tp.pk,'reason':'Плановая подмена сотрудника'
        })
        self.assertEqual(requested.status_code,201,requested.content)
        self.client.force_authenticate(self.owner)
        delete = self.client.delete(f'/api/duty/{shift.pk}/',
                                    HTTP_X_ORGANIZATION_ID=str(self.a.pk))
        self.assertEqual(delete.status_code,400,delete.content)
        planned = self.post('duty/preview/',{
            'from':'2099-01-01','to':'2099-01-01',
            'technicians':[self.tp.pk],'replace':True,
        })
        self.assertEqual(planned.status_code,400,planned.content)
        self.assertTrue(DutyShift.objects.filter(pk=shift.pk).exists())
        self.assertTrue(DutySwap.objects.filter(pk=requested.json()['id'],status='PENDING').exists())

    def test_started_shift_cannot_be_erased_or_rewritten(self):
        shift = self.shift()
        self.assertEqual(self.client.delete(f'/api/duty/{shift.pk}/').status_code,400)
        self.assertEqual(self.client.patch(f'/api/duty/{shift.pk}/',{'technician':self.tp.pk},format='json').status_code,400)
        self.client.force_authenticate(self.peer)
        self.assertEqual(self.post(f'duty/{shift.pk}/acknowledge/').status_code,403)

    def test_push_single_device_receives_both_authorized_employers_without_duplicates(self):
        sub=PushSubscription.objects.create(user=self.worker,organization=self.a,
            endpoint='https://fcm.googleapis.com/fcm/send/one-device',p256dh='0123456789abcdefgh',auth='0123456789abcdefgh')
        first=self.post('tickets/',{'elevator':self.ea.pk,'title':'Первая'}).json()
        second=self.post('tickets/',{'elevator':self.eb.pk,'title':'Вторая'},self.b).json()
        self.assertEqual(PushDelivery.objects.filter(subscription=sub,ticket_id=first['id']).count(),1)
        self.assertEqual(PushDelivery.objects.filter(subscription=sub,ticket_id=second['id']).count(),1)
        Membership.objects.filter(user=self.worker,organization=self.b).update(active=False)
        third=self.post('tickets/',{'elevator':self.eb.pk,'title':'Без доступа'},self.b).json()
        self.assertFalse(PushDelivery.objects.filter(ticket_id=third['id']).exists())
        from .push_notifications import deliver_one
        self.assertEqual(deliver_one(PushDelivery.objects.get(ticket_id=second['id']),{}),'DISABLED')


