from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from .models import Building, Elevator, Membership, MobileLoginLimit, MobileSession, Organization, Technician, Ticket


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class NativeSessionTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(pk=1)
        self.other = Organization.objects.create(name='Совместительство')
        self.user = get_user_model().objects.create_user('worker', password='long-test-password')
        self.tech = Technician.objects.create(organization=self.org, full_name='Механик')
        self.member = Membership.objects.create(user=self.user, organization=self.org, role='MECHANIC', technician=self.tech)
        Membership.objects.create(user=self.user, organization=self.other, role='DISPATCHER')
        building = Building.objects.create(organization=self.org, name='Дом', address='Тест 1')
        elevator = Elevator.objects.create(organization=self.org, building=building, assigned_technician=self.tech)
        self.ticket = Ticket.objects.create(organization=self.org, elevator=elevator, assigned_to=self.tech, title='Тест')
        self.client = APIClient()

    def login(self, device='Samsung'):
        result = self.client.post('/api/mobile/login/', {'username':'worker', 'password':'long-test-password', 'device_name':device}, format='json')
        self.assertEqual(result.status_code, 200, result.content)
        return result.json()

    def authorize(self, token):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

    def test_opaque_device_session_hashed_at_rest_no_cookie_or_password(self):
        data = self.login()
        self.assertEqual(len(data['organizations']), 2)
        self.assertEqual(len(data['token']), 43)
        row = MobileSession.objects.get()
        self.assertNotEqual(row.token_hash, data['token'])
        self.assertNotIn('password', str(data))
        self.assertFalse(self.client.cookies.get('sessionid'))
        self.authorize(data['token'])
        self.assertEqual(self.client.get('/api/me/').json()['role'], 'MECHANIC')
        self.assertEqual(self.client.get('/api/me/', HTTP_X_ORGANIZATION_ID=str(self.other.pk)).json()['role'], 'DISPATCHER')

    def test_native_token_cannot_escape_tenant_or_change_role(self):
        self.authorize(self.login()['token'])
        third = Organization.objects.create(name='Недоступно')
        self.assertEqual(self.client.get('/api/tickets/', HTTP_X_ORGANIZATION_ID=str(third.pk)).status_code, 403)
        self.assertEqual(self.client.post('/api/tickets/', {'elevator':self.ticket.elevator_id,'title':'Сам себе дежурный','role':'ADMIN'}, format='json').status_code, 403)
        result = self.client.post(f'/api/tickets/{self.ticket.pk}/accept/', {}, format='json')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['status'], 'IN_PROGRESS')

    def test_logout_revokes_only_this_device_even_after_membership_is_removed(self):
        first, second = self.login(), self.login('Другой телефон')
        self.authorize(first['token'])
        self.member.active = False
        self.member.save()
        self.assertEqual(self.client.get('/api/me/', HTTP_X_ORGANIZATION_ID=str(self.org.pk)).status_code, 403)
        self.assertEqual(self.client.post('/api/mobile/logout/').status_code, 200)
        self.assertEqual(self.client.get('/api/mobile/sessions/').status_code, 401)
        self.authorize(second['token'])
        self.assertEqual(self.client.get('/api/mobile/sessions/').status_code, 200)

    def test_expiry_password_change_and_disabled_account_invalidate_sessions(self):
        data = self.login()
        self.authorize(data['token'])
        MobileSession.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.client.get('/api/me/').status_code, 401)
        self.client.credentials()
        self.authorize(self.login()['token'])
        self.user.set_password('a-new-test-password')
        self.user.save()
        self.assertEqual(self.client.get('/api/me/').status_code, 401)
        self.user.set_password('long-test-password')
        self.user.save()
        self.client.credentials()
        self.authorize(self.login()['token'])
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.client.get('/api/me/').status_code, 401)

    def test_shared_database_login_limit_and_generic_error(self):
        for _ in range(15):
            r = self.client.post('/api/mobile/login/', {'username':'unknown','password':'bad'}, format='json')
            self.assertEqual(r.status_code, 401)
            self.assertEqual(r.json()['detail'], 'Неверный логин или пароль.')
        r = self.client.post('/api/mobile/login/', {'username':'UNKNOWN','password':'bad'}, format='json')
        self.assertEqual(r.status_code, 429)
        self.assertIn('Retry-After', r)
        self.assertNotIn('unknown', str(list(MobileLoginLimit.objects.values())))
        MobileLoginLimit.objects.update(resets_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.client.post('/api/mobile/login/', {'username':'unknown','password':'bad'}, format='json').status_code, 401)

    def test_ten_device_cap_and_no_secret_in_device_listing(self):
        for i in range(11):
            data = self.login(str(i))
        self.assertEqual(MobileSession.objects.filter(revoked_at__isnull=True).count(), 10)
        self.authorize(data['token'])
        result = self.client.get('/api/mobile/sessions/')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(sum(r['current'] for r in result.json()), 1)
        self.assertNotIn(data['token'], result.content.decode())
        self.assertNotIn('token_hash', result.content.decode())

    def test_remote_revocation_is_account_scoped(self):
        token = self.login()['token']
        another = get_user_model().objects.create_user('another')
        foreign = MobileSession.objects.create(user=another,token_hash='a'*64,password_stamp='',device_name='Чужой',expires_at=timezone.now()+timedelta(days=1))
        self.authorize(token)
        self.assertEqual(self.client.post(f'/api/mobile/sessions/{foreign.pk}/revoke/').status_code, 404)
        own = MobileSession.objects.get(user=self.user)
        self.assertEqual(self.client.post(f'/api/mobile/sessions/{own.pk}/revoke/').status_code, 200)
        self.assertEqual(self.client.get('/api/me/').status_code, 401)

    def test_invalid_header_no_cookie_fallback_and_no_cache(self):
        self.client.force_login(self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer not-valid')
        self.assertEqual(self.client.get('/api/me/').status_code, 401)
        self.client.credentials()
        self.assertEqual(self.client.get('/api/mobile/sessions/').status_code, 401)
        self.assertIn('no-store', self.client.post('/api/mobile/login/', {}, format='json')['Cache-Control'])
