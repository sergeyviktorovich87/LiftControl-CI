"""Regression tests for the independent HTTPS delivery channel.

Tests use DRF's authenticated mobile-session fixture; token verification itself
is tested by the existing mobile-auth test suite.
"""
import os
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import (
    Building, Elevator, Membership, MobileSession, NotificationPreference,
    Organization, PersonalTask, PersonalTaskEvent, Technician, Ticket, TicketEvent,
)


class SelfPushFeedTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Test provider')
        self.other = Organization.objects.create(name='Other provider')
        self.mechanic = Technician.objects.create(organization=self.organization, full_name='Worker A')
        self.colleague = Technician.objects.create(organization=self.organization, full_name='Worker B')
        self.user = get_user_model().objects.create_user(username='self-push-worker', password='long-test-password')
        Membership.objects.create(
            user=self.user, organization=self.organization, technician=self.mechanic, role='MECHANIC'
        )
        self.client = APIClient()
        self.session = MobileSession.objects.create(
            user=self.user, token_hash='b' * 64, password_stamp=self.user.get_session_auth_hash(),
            device_name='Android test', expires_at=timezone.now() + timedelta(days=7),
        )
        self.client.force_authenticate(user=self.user, token=self.session)
        self.path = '/api/mobile/push/self/'
        self.headers = {'HTTP_X_ORGANIZATION_ID': str(self.organization.pk)}
        self.building = Building.objects.create(
            organization=self.organization, name='One', address='Some street'
        )
        self.elevator = Elevator.objects.create(
            organization=self.organization, building=self.building, factory_number='LC-PUSH-1'
        )

    def get(self, params=None, client=None):
        with patch.dict(os.environ, {'SELF_PUSH_ENABLED': 'true'}):
            return (client or self.client).get(self.path, params or {}, **self.headers)

    def ticket(self, org=None):
        ticket = Ticket.objects.create(
            organization=org or self.organization, elevator=self.elevator, title='Test', status='NEW',
            assigned_to=self.mechanic,
        )
        event = TicketEvent.objects.create(
            organization=org or self.organization, ticket=ticket, action='created', after='NEW'
        )
        return ticket, event

    def test_only_android_session_can_connect(self):
        browser = APIClient()
        browser.force_authenticate(user=self.user)
        self.assertEqual(self.get(client=browser).status_code, 403)
        anon = APIClient()
        self.assertIn(self.get(client=anon).status_code, (401, 403))

    def test_disabled_by_default(self):
        with patch.dict(os.environ, {'SELF_PUSH_ENABLED': 'false'}):
            response = self.client.get(self.path, **self.headers)
        self.assertEqual(response.status_code, 403)

    def test_bootstrap_then_new_event_without_private_details(self):
        _, first = self.ticket()
        bootstrap = self.get({'bootstrap': '1'})
        self.assertEqual(bootstrap.status_code, 200)
        self.assertEqual(bootstrap.data['events'], [])
        self.assertEqual(bootstrap.data['tickets_after'], first.pk)
        ticket, latest = self.ticket()
        response = self.get({'tickets_after': first.pk, 'tasks_after': 0})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['events'][0]['ticket_id'], ticket.pk)
        self.assertEqual(response.data['events'][0]['event_id'], latest.pk)
        self.assertNotIn('address', str(response.data))
        self.assertNotIn('title', str(response.data))
        self.assertEqual(response['Cache-Control'], 'no-store, private')

    def test_no_other_organization_events(self):
        other_building = Building.objects.create(
            organization=self.other, name='Two', address='Other'
        )
        other_lift = Elevator.objects.create(
            organization=self.other, building=other_building, factory_number='LC-OTHER-1'
        )
        ticket = Ticket.objects.create(
            organization=self.other, elevator=other_lift, title='Private', status='NEW'
        )
        TicketEvent.objects.create(organization=self.other, ticket=ticket, action='created')
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['events'], [])
        denied = self.get({'tickets_after': 0})
        self.assertEqual(denied.data['events'], [])

    def test_personal_tasks_only_for_assigned_mechanic(self):
        task_other = PersonalTask.objects.create(
            organization=self.organization, assigned_to=self.colleague, title='Not for me'
        )
        PersonalTaskEvent.objects.create(task=task_other, action='created')
        task_mine = PersonalTask.objects.create(
            organization=self.organization, assigned_to=self.mechanic, title='For me'
        )
        PersonalTaskEvent.objects.create(task=task_mine, action='created')
        response = self.get()
        tasks = [e for e in response.data['events'] if e['target'] == 'task']
        self.assertEqual([e['task_id'] for e in tasks], [task_mine.pk])

    def test_preferences_and_stale_events(self):
        ticket, _ = self.ticket()
        NotificationPreference.objects.create(
            organization=self.organization, user=self.user, ordinary=False
        )
        response = self.get()
        self.assertEqual(response.data['events'], [])
        NotificationPreference.objects.filter(user=self.user).update(ordinary=True)
        ticket.status = 'DONE'
        ticket.save(update_fields=['status'])
        response = self.get()
        self.assertEqual(response.data['events'], [])

    def test_rejects_invalid_cursors(self):
        for value in ('-1', '1;DROP TABLE', 'x', '9223372036854775808'):
            response = self.get({'tickets_after': value})
            self.assertEqual(response.status_code, 400)
