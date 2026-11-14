import hashlib
import json
import os
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from .models import (Organization, Membership, Technician, Building, Elevator, Ticket,
    TicketEvent, DutyShift, MobileSession, NativePushDevice, NativePushDelivery,
    PushSubscription, PushDelivery)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class FieldFeedbackTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(pk=1)
        self.other = Organization.objects.create(name='Другая компания')
        User = get_user_model()
        self.admin = User.objects.create_user('owner')
        self.worker = User.objects.create_user('mechanic')
        self.peer = User.objects.create_user('replacement')
        self.tech = Technician.objects.create(organization=self.org, full_name='Механик')
        self.replacement = Technician.objects.create(organization=self.org, full_name='Замена')
        Membership.objects.create(user=self.admin, organization=self.org, role='ADMIN')
        Membership.objects.create(user=self.worker, organization=self.org, role='MECHANIC', technician=self.tech)
        Membership.objects.create(user=self.worker, organization=self.other, role='DISPATCHER')
        Membership.objects.create(user=self.peer, organization=self.org, role='MECHANIC', technician=self.replacement)
        building = Building.objects.create(organization=self.org, name='Дом', address='Тест 1')
        self.lift = Elevator.objects.create(organization=self.org, building=building, assigned_technician=self.tech)
        self.ticket = Ticket.objects.create(organization=self.org, elevator=self.lift, title='Двери')
        self.client = APIClient()
        self.client.force_authenticate(self.worker)

    def post(self, path, data=None):
        return self.client.post('/api/' + path, data or {}, format='json', HTTP_X_ORGANIZATION_ID=str(self.org.pk))

    def accept(self):
        self.assertEqual(self.post(f'tickets/{self.ticket.pk}/accept/').status_code, 200)

    def test_stop_requires_reason_and_assignee_preserves_open_incident(self):
        self.accept()
        self.assertEqual(self.post(f'tickets/{self.ticket.pk}/stop/', {'reason': ''}).status_code, 400)
        self.client.force_authenticate(self.peer)
        self.assertEqual(self.post(f'tickets/{self.ticket.pk}/stop/', {'reason': 'Нужна запчасть'}).status_code, 403)
        self.client.force_authenticate(self.worker)
        result = self.post(f'tickets/{self.ticket.pk}/stop/', {'reason': 'Нужна запчасть, привод дверей'})
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result.json()['status'], 'STOPPED')
        self.assertTrue(result.json()['can_resume'])
        self.assertIsNone(result.json()['completed_at'])
        self.lift.refresh_from_db()
        self.assertIsNotNone(self.lift.stopped_at)
        self.assertTrue(self.lift.active)
        self.assertEqual(self.post(f'tickets/{self.ticket.pk}/comment/', {'body': 'Деталь заказана'}).status_code, 201)
        self.assertEqual(self.post(f'tickets/{self.ticket.pk}/resume/').status_code, 200)
        self.lift.refresh_from_db()
        self.assertIsNotNone(self.lift.stopped_at)
        result = self.post(f'tickets/{self.ticket.pk}/complete/', {'work_description': 'Заменён привод, работа восстановлена'})
        self.assertEqual(result.status_code, 200)
        self.lift.refresh_from_db(); self.ticket.refresh_from_db()
        self.assertIsNone(self.lift.stopped_at)
        self.assertEqual(self.ticket.stop_reason, 'Нужна запчасть, привод дверей')
        self.assertEqual(list(self.ticket.events.values_list('action', flat=True)), ['accepted','stopped','commented','resumed','completed'])

    def test_long_repair_has_reason_and_expected_date_without_restarting_stopped_lift(self):
        self.accept()
        path = f'tickets/{self.ticket.pk}/'
        self.assertEqual(self.post(path + 'stop/', {'reason': 'Ожидаем запчасть'}).status_code, 200)
        self.assertEqual(self.post(path + 'long-repair/', {'reason': ''}).status_code, 400)
        paused = self.post(path + 'long-repair/', {
            'reason': 'Поставка привода', 'expected_date': '2099-10-08',
        })
        self.assertEqual(paused.status_code, 200, paused.data)
        self.assertEqual(paused.data['status'], 'LONG_REPAIR')
        self.assertEqual(paused.data['expected_repair_date'], '2099-10-08')
        self.lift.refresh_from_db()
        self.assertIsNotNone(self.lift.stopped_at)
        self.assertEqual(self.post(path + 'resume/', {}).status_code, 200)
        self.lift.refresh_from_db()
        self.assertIsNotNone(self.lift.stopped_at)

    def test_other_unresolved_stop_is_not_cleared_by_completion(self):
        self.accept()
        self.post(f'tickets/{self.ticket.pk}/stop/', {'reason': 'Ожидаем плату'})
        other = Ticket.objects.create(organization=self.org, elevator=self.lift, title='Вторая неисправность', assigned_to=self.tech, status='IN_PROGRESS')
        self.post(f'tickets/{other.pk}/stop/', {'reason': 'Ожидаем датчик'})
        self.post(f'tickets/{other.pk}/resume/')
        self.assertEqual(self.post(f'tickets/{self.ticket.pk}/complete/', {'work_description': 'Плата заменена'}).status_code, 200)
        self.lift.refresh_from_db(); self.assertIsNotNone(self.lift.stopped_at)
        self.post(f'tickets/{other.pk}/complete/', {'work_description': 'Датчик заменён'})
        self.lift.refresh_from_db(); self.assertIsNone(self.lift.stopped_at)

    def test_archive_lift_requires_owner_reason_and_no_open_ticket(self):
        path = f'elevators/{self.lift.pk}/archive/'
        self.assertEqual(self.post(path, {'reason':'Договор расторгнут'}).status_code, 403)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.patch(f'/api/elevators/{self.lift.pk}/', {'active':False}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(f'/api/technicians/{self.tech.pk}/', {'active':False}, format='json').status_code, 400)
        self.assertEqual(self.post(path).status_code, 400)
        self.assertEqual(self.post(path, {'reason':'Договор расторгнут'}).status_code, 400)
        self.ticket.status = 'DONE'; self.ticket.save()
        self.assertEqual(self.post(path, {'reason':'Договор расторгнут'}).status_code, 200)
        self.lift.refresh_from_db(); self.assertFalse(self.lift.active)
        self.assertTrue(Ticket.objects.filter(pk=self.ticket.pk).exists())
        self.assertEqual(self.post('tickets/', {'elevator':self.lift.pk, 'title':'Архив'}).status_code, 400)

    def test_archive_staff_transfers_future_assignments_keeps_other_employer(self):
        shift = DutyShift.objects.create(organization=self.org, technician=self.tech,
            start=timezone.now()+timedelta(days=2), end=timezone.now()+timedelta(days=3))
        self.client.force_authenticate(self.admin)
        path = f'technicians/{self.tech.pk}/archive/'
        self.assertEqual(self.post(path, {'reason':'Увольнение'}).status_code, 400)
        self.assertEqual(self.post(path, {'reason':'Увольнение', 'replacement':self.replacement.pk}).status_code, 200)
        self.tech.refresh_from_db(); shift.refresh_from_db(); self.lift.refresh_from_db()
        self.assertFalse(self.tech.active)
        self.assertEqual(shift.technician, self.replacement)
        self.assertEqual(self.lift.assigned_technician, self.replacement)
        self.assertFalse(Membership.objects.get(user=self.worker, organization=self.org).active)
        self.assertTrue(Membership.objects.get(user=self.worker, organization=self.other).active)

    def test_archive_staff_accepts_lift_by_lift_transfer_and_unassignment(self):
        another = Elevator.objects.create(
            organization=self.org, building=self.lift.building, inventory_number='SECOND',
            assigned_technician=self.tech,
        )
        self.client.force_authenticate(self.admin)
        invalid = self.post(f'technicians/{self.tech.pk}/archive/', {
            'reason': 'Увольнение', 'lift_assignments': {str(self.lift.pk): None},
        })
        self.assertEqual(invalid.status_code, 400)
        archived = self.post(f'technicians/{self.tech.pk}/archive/', {
            'reason': 'Увольнение',
            'lift_assignments': {str(self.lift.pk): self.replacement.pk, str(another.pk): None},
        })
        self.assertEqual(archived.status_code, 200, archived.data)
        self.lift.refresh_from_db()
        another.refresh_from_db()
        self.assertEqual(self.lift.assigned_technician_id, self.replacement.pk)
        self.assertIsNone(another.assigned_technician_id)

    def test_native_queue_rechecks_access_password_and_current_ticket_state(self):
        from .native_push import send_one
        session = MobileSession.objects.create(user=self.worker, token_hash='a'*64,
            password_stamp=self.worker.get_session_auth_hash(), device_name='Samsung', expires_at=timezone.now()+timedelta(days=1))
        device = NativePushDevice.objects.create(session=session, token='test-token-'+('a'*80))
        self.accept()
        pending = NativePushDelivery.objects.get(kind='accepted')
        self.post(f'tickets/{self.ticket.pk}/stop/', {'reason':'Нужна запчасть'})
        pending.ticket.refresh_from_db()
        with patch('requests.post') as outgoing:
            self.assertEqual(send_one(pending), 'SUPERSEDED')
            session.revoked_at = timezone.now(); session.save()
            pending = NativePushDelivery.objects.select_related('device__session__user').get(kind='stopped')
            self.assertEqual(send_one(pending), 'DISABLED')
            outgoing.assert_not_called()
        self.assertEqual(NativePushDelivery.objects.filter(device=device).count(), 2)

    def test_fcm_registration_is_bound_to_native_session_and_never_lists_token(self):
        session = MobileSession.objects.create(user=self.worker, token_hash='b'*64,
            password_stamp=self.worker.get_session_auth_hash(), device_name='Samsung', expires_at=timezone.now()+timedelta(days=1))
        token = 'device-token-'+'x'*80
        with patch('core.native_push.enabled', return_value=True):
            self.assertEqual(self.post('mobile/push/', {'token':token}).status_code, 403)
            self.client.force_authenticate(self.worker, token=session)
            self.assertEqual(self.post('mobile/push/', {'token':token}).status_code, 200)
            self.assertEqual(self.post('mobile/push/', {'token':token}).status_code, 200)
        self.assertEqual(NativePushDevice.objects.count(), 1)
        response = self.client.get('/api/mobile/sessions/')
        self.assertNotIn(token, response.content.decode())

    def test_webpush_never_delivers_historical_new_for_completed_ticket(self):
        from .push_notifications import deliver_one
        sub = PushSubscription.objects.create(organization=self.org,user=self.worker,
            endpoint='https://fcm.googleapis.com/fcm/send/test',p256dh='test',auth='test')
        queued = PushDelivery.objects.create(organization=self.org,subscription=sub,ticket=self.ticket,kind='created')
        self.ticket.status = 'DONE'; self.ticket.save()
        queued.ticket.refresh_from_db()
        with patch('pywebpush.webpush') as outgoing:
            self.assertEqual(deliver_one(queued, {}), 'SUPERSEDED'); outgoing.assert_not_called()

    def test_update_download_is_authorized_and_manifest_cannot_escape_directory(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {'LIFTCONTROL_UPDATES_DIR':temporary}):
            root = Path(temporary); payload = b'APK fixture'
            (root/'new.apk').write_bytes(payload)
            item = {'filename':'new.apk','version':'0.2.0-beta','version_code':2010,'size':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}
            (root/'release.json').write_text(json.dumps({'android':item}))
            result = self.client.get('/api/updates/')
            self.assertEqual(result.status_code, 200)
            self.assertNotIn('panel', result.json())
            response = self.client.get('/api/updates/android/download/')
            self.assertEqual(b''.join(response.streaming_content), payload)
            self.assertEqual(self.client.get('/api/updates/panel/download/').status_code, 403)
            (root/'release.json').write_text(json.dumps({'android':item|{'filename':'../new.apk'}}))
            self.assertEqual(self.client.get('/api/updates/').status_code, 400)
            self.client.force_authenticate(None)
            self.assertIn(self.client.get('/api/updates/android/download/').status_code, (401,403))
