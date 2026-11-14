"""Commissioning security, tenant isolation, progress race and expense regressions."""
import io
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from .models import (Organization, Membership, Technician, Building, Elevator,
                     CommissioningProject, CommissioningLift, CommissioningAssignment,
                     CommissioningProgress, CommissioningExpense, CommissioningExtraWork)


class CommissioningTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        override = override_settings(MEDIA_ROOT=self.media.name)
        override.enable()
        self.addCleanup(override.disable)
        self.org = Organization.objects.get(pk=1)
        self.foreign = Organization.objects.create(name='Прочая организация')
        User = get_user_model()
        self.admin = User.objects.create_user(username='director-13', password='VeryStrong13Password!')
        self.a = User.objects.create_user(username='adjuster-a', password='VeryStrong13Password!')
        self.b = User.objects.create_user(username='adjuster-b', password='VeryStrong13Password!')
        self.stranger = User.objects.create_user(username='third-company', password='VeryStrong13Password!')
        Membership.objects.create(organization=self.org, user=self.admin, role='ADMIN')
        Membership.objects.create(organization=self.org, user=self.a, role='ADJUSTER')
        Membership.objects.create(organization=self.org, user=self.b, role='ADJUSTER')
        Membership.objects.create(organization=self.foreign, user=self.stranger, role='ADMIN')
        self.project = CommissioningProject.objects.create(
            organization=self.org, name='ЖК', address='Первый 1', created_by=self.admin)
        self.lift_a = CommissioningLift.objects.create(project=self.project, label='Лифт А')
        self.lift_b = CommissioningLift.objects.create(project=self.project, label='Лифт Б')
        CommissioningAssignment.objects.create(lift=self.lift_a, user=self.a)
        CommissioningAssignment.objects.create(lift=self.lift_b, user=self.b)
        self.client = APIClient()

    def request(self, user, method, path, body=None, tenant=None, format='json'):
        self.client.force_authenticate(user)
        headers = {'HTTP_X_ORGANIZATION_ID': str((tenant or self.org).id)}
        endpoint = '/api/' + path
        if method == 'get':
            return self.client.get(endpoint, **headers)
        return self.client.post(endpoint, body or {}, format=format, **headers)

    def test_project_shared_overview_but_strict_progress_authority(self):
        self.assertEqual(self.request(self.a, 'get', 'commissioning/').status_code, 200)
        response = self.request(self.a, 'get', f'commissioning/{self.project.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['lifts']), 2)
        self.assertTrue(response.data['lifts'][0]['can_edit'])
        self.assertFalse(response.data['lifts'][1]['can_edit'])
        path = f'commissioning/{self.project.pk}/lifts/{self.lift_b.pk}/progress/'
        self.assertEqual(self.request(self.a, 'post', path,
            {'revision': 0, 'progress': 20, 'description': 'Проверка дверей'}).status_code, 403)
        self.assertEqual(self.request(self.admin, 'post', path,
            {'revision': 0, 'progress': 20, 'description': 'Администратор'}).status_code, 403)
        self.assertEqual(self.request(self.b, 'post', path,
            {'revision': 0, 'progress': 20, 'description': 'Датчики выставлены'}).status_code, 200)
        self.assertEqual(self.request(self.b, 'post', path,
            {'revision': 0, 'progress': 80, 'description': 'Повтор'}).status_code, 400)
        self.lift_b.refresh_from_db()
        self.assertEqual((self.lift_b.progress, self.lift_b.revision), (20, 1))
        self.assertEqual(CommissioningProgress.objects.filter(lift=self.lift_b).count(), 1)

    def test_personal_expenses_and_receipts_private_even_on_common_project(self):
        path = f'commissioning/{self.project.pk}/expenses/'
        self.assertEqual(self.request(self.a, 'post', path,
            {'amount': '140.10', 'description': 'Болты'}).status_code, 201)
        self.assertEqual(self.request(self.b, 'post', path,
            {'amount': '70', 'description': 'Проезд'}).status_code, 201)
        expenses = CommissioningExpense.objects.filter(project=self.project).order_by('id')
        own, other = list(expenses)
        a = self.request(self.a, 'get', f'commissioning/{self.project.pk}/').data
        self.assertEqual([x['id'] for x in a['expenses']], [own.pk])
        b = self.request(self.b, 'get', f'commissioning/{self.project.pk}/').data
        self.assertEqual([x['id'] for x in b['expenses']], [other.pk])
        director = self.request(self.admin, 'get', f'commissioning/{self.project.pk}/').data
        self.assertEqual(len(director['expenses']), 2)
        im = Image.new('RGB', (4, 4), color='white')
        stream = io.BytesIO()
        im.save(stream, format='PNG')
        upload = SimpleUploadedFile('check.png', stream.getvalue(), content_type='image/png')
        uri = f'commissioning/{self.project.pk}/expenses/{own.pk}/upload/'
        self.assertEqual(self.request(self.a, 'post', uri, {'receipt': upload}, format='multipart').status_code, 201)
        duplicate = SimpleUploadedFile('another.png', stream.getvalue(), content_type='image/png')
        self.assertEqual(self.request(self.a, 'post', uri, {'receipt': duplicate}, format='multipart').status_code, 400)
        uri = f'commissioning/{self.project.pk}/expenses/{own.pk}/receipt/'
        self.assertEqual(self.request(self.b, 'get', uri).status_code, 403)
        self.assertEqual(self.request(self.a, 'get', uri).status_code, 200)
        self.assertEqual(self.request(self.admin, 'get', uri).status_code, 200)

    def test_foreign_tenant_and_unassigned_user_rejected(self):
        uri = f'commissioning/{self.project.pk}/'
        self.assertIn(self.request(self.stranger, 'get', uri).status_code, (403, 404))
        self.assertIn(self.request(self.a, 'get', uri, tenant=self.foreign).status_code, (403, 404))
        User = get_user_model()
        outsider = User.objects.create_user(username='unassigned-13', password='VeryStrong13Password!')
        Membership.objects.create(user=outsider, organization=self.org, role='ADJUSTER')
        self.assertEqual(self.request(outsider, 'get', 'commissioning/').data, [])
        self.assertEqual(self.request(outsider, 'get', uri).status_code, 403)

    def test_external_adjuster_cannot_read_regular_workflow_or_staff(self):
        for endpoint in ('tickets/', 'tasks/', 'chat/', 'parts/', 'media/', 'organizations/members/'):
            response = self.request(self.a, 'get', endpoint)
            self.assertEqual(response.status_code, 403, endpoint)
        profile = self.request(self.a, 'get', 'me/')
        self.assertEqual(profile.status_code, 200)
        self.assertTrue(profile.data['commissioning_only'])
        self.assertFalse(profile.data['can_create_ticket'])
        self.assertEqual(self.request(self.a, 'get', 'organizations/').status_code, 200)
        Membership.objects.filter(user=self.a, organization=self.org).update(active=False)
        self.assertEqual(self.request(self.a, 'get', f'commissioning/{self.project.pk}/').status_code, 403)

    def test_external_adjuster_account_can_be_created_without_technician(self):
        row = self.request(self.admin, 'post', 'organizations/members/create/', {
            'username': 'external-worker-13',
            'full_name': 'Наладчик Проверка',
            'password': 'UniqueStrongPassword13!',
            'role': 'ADJUSTER',
        })
        self.assertEqual(row.status_code, 201, row.data)
        self.assertIsNone(row.data['technician'])
        membership = Membership.objects.get(
            organization=self.org, user__username='external-worker-13')
        self.assertEqual(membership.role, 'ADJUSTER')
        self.assertIsNone(membership.technician)

    def test_approval_requests_and_archiving_are_audited(self):
        p = self.project.pk
        created = self.request(self.a, 'post', f'commissioning/{p}/extra-work/',
            {'price': '1200.00', 'description': 'Перенос станции'})
        self.assertEqual(created.status_code, 201)
        extra_id = created.data['id']
        self.assertEqual(CommissioningExtraWork.objects.get(pk=extra_id).status, 'PENDING')
        self.assertEqual(self.request(self.a, 'post', f'commissioning/{p}/extra-work/{extra_id}/decision/',
            {'approve': True}).status_code, 403)
        self.assertEqual(self.request(self.admin, 'post', f'commissioning/{p}/extra-work/{extra_id}/decision/',
            {'approve': True}).status_code, 200)
        requested = self.request(self.a, 'post', f'commissioning/{p}/lift-requests/',
            {'label': 'Лифт В'})
        self.assertEqual(requested.status_code, 201)
        self.assertEqual(self.request(self.admin, 'post',
            f'commissioning/{p}/lift-requests/{requested.data["id"]}/decision/',
            {'approve': True}).status_code, 200)
        self.assertEqual(CommissioningLift.objects.filter(project=self.project).count(), 3)
        self.assertEqual(self.request(self.admin, 'post', f'commissioning/{p}/archive/',
            {'reason': 'Объект закончен'}).status_code, 200)
        self.assertEqual(self.request(self.a, 'post', f'commissioning/{p}/messages/',
            {'text': 'После архива'}).status_code, 400)
        self.assertEqual(self.request(self.a, 'get', f'commissioning/{p}/').status_code, 200)
