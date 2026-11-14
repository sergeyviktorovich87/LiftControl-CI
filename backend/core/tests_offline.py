import base64
import io
import tempfile
import uuid
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from PIL import Image
from .models import (Organization, Membership, Technician, Building, Elevator, DutyShift,
    Ticket, MobileSyncReceipt, Document, DocumentRevision, AuditEvent)
from .mobile_sync import offline_grant


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class OfflineSynchronizationTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        settings = override_settings(MEDIA_ROOT=self.media.name)
        settings.enable(); self.addCleanup(settings.disable)
        self.org = Organization.objects.get(pk=1)
        self.other = Organization.objects.create(name='Other')
        self.admin = get_user_model().objects.create_user('admin')
        self.worker = get_user_model().objects.create_user('worker')
        self.peer = get_user_model().objects.create_user('peer')
        self.tech = Technician.objects.create(organization=self.org,full_name='Механик')
        self.replacement = Technician.objects.create(organization=self.org,full_name='Сменщик')
        Membership.objects.create(user=self.admin,organization=self.org,role='ADMIN')
        self.membership = Membership.objects.create(user=self.worker,organization=self.org,role='MECHANIC',technician=self.tech)
        Membership.objects.create(user=self.peer,organization=self.org,role='MECHANIC',technician=self.replacement)
        self.building = Building.objects.create(organization=self.org,name='Дом',address='Тест 1')
        self.lift = Elevator.objects.create(organization=self.org,building=self.building,manufacturer='MEL',assigned_technician=self.tech)
        self.client = APIClient(); self.client.force_authenticate(self.admin)

    def op(self, kind='create', body=None, ticket=None):
        return {'id':str(uuid.uuid4()),'type':kind,'occurred_at':timezone.now().isoformat(),
            **({'ticket':ticket} if ticket else {}),
            'body':body if body is not None else {'elevator':self.lift.pk,'title':'Двери не закрываются'}}

    def send(self, operation):
        return self.client.post('/api/mobile/sync/',operation,format='json',HTTP_X_ORGANIZATION_ID=str(self.org.pk))

    def test_response_loss_replay_creates_one_ticket_and_one_event(self):
        operation = self.op()
        first, repeated = self.send(operation), self.send(operation)
        self.assertEqual(first.status_code,201,first.content)
        self.assertEqual(repeated.status_code,200,repeated.content)
        self.assertTrue(repeated.json()['replayed'])
        self.assertEqual(first.json()['ticket']['id'],repeated.json()['ticket']['id'])
        self.assertEqual(Ticket.objects.count(),1)
        self.assertEqual(Ticket.objects.get().events.count(),1)
        self.assertEqual(MobileSyncReceipt.objects.count(),1)
        self.assertEqual(first.json()['ticket']['assigned_to'],self.tech.pk)

    def test_same_identifier_cannot_overwrite_payload(self):
        operation = self.op(); self.send(operation)
        operation['body']['title'] = 'Другой текст'
        self.assertEqual(self.send(operation).status_code,409)
        self.assertEqual(Ticket.objects.get().title,'Двери не закрываются')

    def test_ordinary_mechanic_cannot_create_offline_or_spoof_signed_grant(self):
        self.client.force_authenticate(self.worker)
        operation = self.op(); operation['grant'] = 'forged'
        self.assertEqual(self.send(operation).status_code,403)
        self.assertFalse(Ticket.objects.exists())
        self.assertFalse(MobileSyncReceipt.objects.exists())

    def test_signed_duty_operation_can_arrive_after_handover(self):
        self.client.force_authenticate(self.worker)
        recorded = timezone.now()-timedelta(hours=2)
        DutyShift.objects.create(organization=self.org,technician=self.tech,
            start=recorded-timedelta(hours=1),end=recorded+timedelta(hours=1))
        self.worker._organization_id = self.org.pk
        with patch('core.mobile_sync.timezone.now',return_value=recorded):
            grant = offline_grant(self.worker)
        operation = self.op()
        operation['occurred_at'] = (recorded+timedelta(minutes=1)).isoformat()
        operation['grant'] = grant
        response = self.send(operation)
        self.assertEqual(response.status_code,201,response.content)
        self.assertEqual(MobileSyncReceipt.objects.get().occurred_at.isoformat(),operation['occurred_at'])

    def test_grant_cannot_extend_shift_or_cross_account(self):
        recorded = timezone.now()-timedelta(hours=2)
        DutyShift.objects.create(organization=self.org,technician=self.tech,start=recorded-timedelta(hours=1),end=recorded+timedelta(minutes=30))
        self.worker._organization_id = self.org.pk
        with patch('core.mobile_sync.timezone.now',return_value=recorded): grant = offline_grant(self.worker)
        self.client.force_authenticate(self.worker)
        operation = self.op(); operation['grant'] = grant
        self.assertEqual(self.send(operation).status_code,403)
        operation['occurred_at'] = (recorded+timedelta(minutes=1)).isoformat()
        self.client.force_authenticate(self.peer)
        self.assertEqual(self.send(operation).status_code,403)

    def test_membership_revocation_blocks_receipt_replay(self):
        operation = self.op(); self.send(operation)
        Membership.objects.filter(user=self.admin).update(active=False)
        self.assertEqual(self.send(operation).status_code,403)

    def test_failed_transition_is_not_acknowledged_and_can_be_retried_after_correction(self):
        ticket = Ticket.objects.create(organization=self.org,elevator=self.lift,title='Шум',assigned_to=self.tech)
        self.client.force_authenticate(self.worker)
        operation = self.op('complete',{'work_description':'Заменён датчик'},ticket.pk)
        self.assertEqual(self.send(operation).status_code,403)
        self.assertFalse(MobileSyncReceipt.objects.exists())
        accepted = self.op('accept',{},ticket.pk)
        self.assertEqual(self.send(accepted).status_code,201)
        self.assertEqual(self.send(operation).status_code,201)
        self.assertEqual(self.send(operation).status_code,200)
        ticket.refresh_from_db(); self.assertEqual(ticket.status,'DONE')
        self.assertEqual(ticket.comments.count(),1)
        self.assertEqual(ticket.events.filter(action='completed').count(),1)

    def test_comment_and_photo_replay_do_not_duplicate_history(self):
        ticket = Ticket.objects.create(organization=self.org,elevator=self.lift,title='Шум',assigned_to=self.tech,status='IN_PROGRESS')
        self.client.force_authenticate(self.worker)
        comment = self.op('comment',{'body':'Нужна плата'},ticket.pk)
        for status in (201,200): self.assertEqual(self.send(comment).status_code,status)
        image = io.BytesIO(); Image.new('RGB',(12,12),'green').save(image,format='PNG')
        photo = self.op('photo',{'file':base64.b64encode(image.getvalue()).decode()},ticket.pk)
        for status in (201,200): self.assertEqual(self.send(photo).status_code,status)
        self.assertEqual(ticket.comments.count(),1)
        self.assertEqual(ticket.photos.count(),1)

    def test_assignee_and_lift_are_tenant_scoped(self):
        other = Technician.objects.create(organization=self.other,full_name='Чужой')
        operation = self.op(); operation['body']['assigned_to'] = other.pk
        self.assertEqual(self.send(operation).status_code,400)
        self.replacement.active=False; self.replacement.save()
        operation['body']['assigned_to'] = self.replacement.pk
        self.assertEqual(self.send(operation).status_code,400)
        self.assertFalse(Ticket.objects.exists())

    def test_duty_mechanic_can_choose_and_reassign_active_executor(self):
        now = timezone.now()
        DutyShift.objects.create(organization=self.org,technician=self.tech,start=now-timedelta(hours=1),end=now+timedelta(hours=1))
        self.client.force_authenticate(self.worker)
        operation = self.op(); operation['body']['assigned_to'] = self.replacement.pk
        result = self.send(operation)
        self.assertEqual(result.status_code,201,result.content)
        self.assertEqual(result.json()['ticket']['assigned_to'],self.replacement.pk)
        change = self.op('assign',{'assigned_to':self.tech.pk,'reason':'Сменщик занят'},result.json()['ticket']['id'])
        self.assertEqual(self.send(change).status_code,201)
        self.assertEqual(Ticket.objects.get().assigned_to_id,self.tech.pk)

    def test_invalid_envelopes_never_acknowledged(self):
        for changes in ({'type':'arbitrary'},{'id':'bad'},{'occurred_at':'2026-01-01'},{'occurred_at':(timezone.now()-timedelta(days=8)).isoformat()},{'occurred_at':(timezone.now()+timedelta(hours=2)).isoformat()},{'body':[]},{'url':'https://evil.example'}):
            operation = self.op(); operation.update(changes)
            self.assertEqual(self.send(operation).status_code,400,changes)
        self.assertFalse(MobileSyncReceipt.objects.exists())

    def test_document_deletion_is_scoped_audited_and_preserves_file_and_revision(self):
        doc = Document.objects.create(organization=self.org,title='Схема',manufacturer='MEL',file=SimpleUploadedFile('diagram.pdf',b'%PDF-test'))
        revision = DocumentRevision.objects.create(document=doc,title=doc.title,manufacturer=doc.manufacturer,file=doc.file.name)
        url = f'/api/documents/{doc.pk}/'
        self.assertEqual(self.client.delete(url,{},format='json').status_code,400)
        self.client.force_authenticate(self.worker)
        self.assertEqual(self.client.delete(url,{'reason':'Устаревшая схема'},format='json').status_code,403)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.delete(url,{'reason':'Устаревшая схема'},format='json').status_code,204)
        doc.refresh_from_db(); self.assertIsNotNone(doc.deleted_at)
        self.assertTrue(doc.file.storage.exists(doc.file.name)); self.assertTrue(DocumentRevision.objects.filter(pk=revision.pk).exists())
        self.assertEqual(self.client.get(url).status_code,404)
        self.assertEqual(self.client.get(f'{url}download/').status_code,404)
        self.assertEqual(self.client.get('/api/documents/').json(),[])
        self.assertEqual(self.client.get(f'/api/elevators/{self.lift.pk}/documents/').json(),[])
        self.assertEqual(AuditEvent.objects.filter(action='document.delete').count(),1)
