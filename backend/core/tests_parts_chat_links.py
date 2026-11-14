"""Regressions for spare part photo workflow and linked chat cards."""
import io
import tempfile
from PIL import Image
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from .models import Organization, Membership, Technician, Building, Elevator, PartRequest, Ticket, PartPhoto, AuditEvent, ChatMessage


class PartChatLinkTests(TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        setting = override_settings(MEDIA_ROOT=folder.name)
        setting.enable()
        self.addCleanup(setting.disable)
        self.a = Organization.objects.first()
        self.b = Organization.objects.create(name='Другой работодатель')
        u = get_user_model()
        self.owner = u.objects.create_user('part-owner-12', password='TestPass01-2026!')
        self.worker = u.objects.create_user('part-worker-12', password='TestPass02-2026!')
        self.foreign = u.objects.create_user('part-foreign-12', password='TestPass03-2026!')
        self.tech = Technician.objects.create(organization=self.a, full_name='Исполнитель А')
        self.btech = Technician.objects.create(organization=self.b, full_name='Исполнитель Б')
        Membership.objects.create(organization=self.a, user=self.owner, role='ADMIN')
        Membership.objects.create(organization=self.a, user=self.worker, role='MECHANIC', technician=self.tech)
        Membership.objects.create(organization=self.b, user=self.foreign, role='MECHANIC', technician=self.btech)
        house_a = Building.objects.create(organization=self.a, address='Тестовая 5')
        house_b = Building.objects.create(organization=self.b, address='Секретная 9')
        self.lift = Elevator.objects.create(organization=self.a, building=house_a)
        self.other_lift = Elevator.objects.create(organization=self.b, building=house_b)
        self.part = PartRequest.objects.create(organization=self.a, elevator=self.lift,
            part='Датчик ДТО', quantity=1, requested_by=self.worker)
        self.other_part = PartRequest.objects.create(organization=self.b, elevator=self.other_lift,
            part='Плата чужой организации', requested_by=self.foreign)
        self.ticket = Ticket.objects.create(organization=self.a, elevator=self.lift, title='Не закрываются двери')
        self.other_ticket = Ticket.objects.create(organization=self.b, elevator=self.other_lift,
            title='Неисправность чужой организации')
        self.client = APIClient()
        self.login(self.owner, self.a)

    def login(self, person, company):
        self.client.force_authenticate(person)
        self.client.credentials(HTTP_X_ORGANIZATION_ID=str(company.pk))

    def photo(self):
        output = io.BytesIO()
        Image.new('RGB', (16, 16), color='orange').save(output, 'PNG')
        return SimpleUploadedFile('sensor.png', output.getvalue(), content_type='image/png')

    def test_part_photos_are_scoped_soft_deleted_and_original_is_readable(self):
        self.login(self.worker, self.a)
        response = self.client.post(f'/api/parts/{self.part.pk}/photos/',
            {'file': self.photo()}, format='multipart')
        self.assertEqual(response.status_code, 201, response.data)
        self.part.refresh_from_db()
        self.assertEqual(self.part.photos.filter(deleted_at__isnull=True).count(), 1)
        image = response.data['url']
        received = self.client.get(image)
        self.assertEqual(received.status_code, 200)
        self.assertTrue(b''.join(received.streaming_content).startswith(b'\x89PNG'))
        self.assertIn('no-store', received['Cache-Control'])
        self.login(self.foreign, self.b)
        self.assertEqual(self.client.get(image).status_code, 404)
        self.login(self.owner, self.a)
        self.assertEqual(self.client.delete(image).status_code, 204)
        self.assertEqual(self.client.get(image).status_code, 404)
        self.assertEqual(self.part.photos.filter(deleted_at__isnull=False).count(), 1)
        self.assertEqual(self.client.delete(f'/api/parts/{self.part.pk}/').status_code, 405)
        self.assertTrue(AuditEvent.objects.filter(action='part.photo.remove').exists())

    def test_editing_approved_part_invalidates_approval_but_ordered_is_frozen(self):
        self.part.status = 'APPROVED'
        self.part.save(update_fields=['status'])
        response = self.client.patch(f'/api/parts/{self.part.pk}/',
            {'quantity': 2}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.part.refresh_from_db()
        self.assertEqual(self.part.status, 'NEW')
        self.assertEqual(self.part.quantity, 2)
        self.assertTrue(AuditEvent.objects.filter(action='part.edit').exists())
        self.part.status = 'ORDERED'
        self.part.save(update_fields=['status'])
        blocked = self.client.patch(f'/api/parts/{self.part.pk}/',
            {'quantity': 12}, format='json')
        self.assertEqual(blocked.status_code, 400, blocked.data)
        photo = self.client.post(f'/api/parts/{self.part.pk}/photos/',
            {'file': self.photo()}, format='multipart')
        self.assertEqual(photo.status_code, 400)
        self.login(self.worker, self.a)
        self.assertEqual(self.client.patch(f'/api/parts/{self.part.pk}/',
            {'part': 'Замена'}, format='json').status_code, 400)

    def test_approved_part_photo_edit_requires_new_approval(self):
        self.part.status = 'APPROVED'
        self.part.save(update_fields=['status'])
        response = self.client.post(f'/api/parts/{self.part.pk}/photos/',
            {'file': self.photo()}, format='multipart')
        self.assertEqual(response.status_code, 201, response.data)
        self.part.refresh_from_db()
        self.assertEqual(self.part.status, 'NEW')
        self.assertEqual(AuditEvent.objects.filter(action='part.approval.invalidate').count(), 1)

    def test_chat_reference_is_only_one_visible_record_and_not_a_permission_bypass(self):
        sent = self.client.post('/api/chat/', {'body': 'Посмотрите маркировку',
            'linked_part': self.part.pk}, format='json')
        self.assertEqual(sent.status_code, 201, sent.data)
        self.assertEqual(sent.data['reference']['kind'], 'part')
        self.assertEqual(sent.data['reference']['title'], 'Датчик ДТО')
        self.assertEqual(ChatMessage.objects.get(pk=sent.data['id']).linked_part_id, self.part.pk)
        linked_ticket = self.client.post('/api/chat/', {'linked_ticket': self.ticket.pk}, format='json')
        self.assertEqual(linked_ticket.status_code, 201, linked_ticket.data)
        self.assertEqual(linked_ticket.data['reference']['kind'], 'ticket')
        both = self.client.post('/api/chat/', {
            'linked_ticket': self.ticket.pk, 'linked_part': self.part.pk}, format='json')
        self.assertEqual(both.status_code, 400)
        cross_company = self.client.post('/api/chat/', {
            'body': 'Чужая', 'linked_part': self.other_part.pk}, format='json')
        self.assertEqual(cross_company.status_code, 400)
        self.login(self.foreign, self.b)
        self.assertEqual(self.client.get(f'/api/chat/{sent.data["id"]}/').status_code, 404)
        self.login(self.owner, self.a)
        self.part.part = 'Датчик уточнён'
        self.part.save(update_fields=['part'])
        updated = self.client.get(f'/api/chat/{sent.data["id"]}/')
        self.assertEqual(updated.data['reference']['title'], 'Датчик уточнён')
