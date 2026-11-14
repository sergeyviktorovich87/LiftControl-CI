import io
import json
import shutil
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock
from PIL import Image
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from .models import (Organization, Membership, Technician, Building, Elevator, Ticket, ChatMessage,
    Document, DocumentRevision, PersonalTask, PersonalTaskEvent, PersonalTaskPush,
    MobileSession, NativePushDevice, NativePushDelivery, PushSubscription)
from . import native_push, task_push
from .notification_preferences import allows, category


class FieldOperationsTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='liftcontrol-media-test-')
        self.addCleanup(self.temp.cleanup)
        config = override_settings(MEDIA_ROOT=self.temp.name)
        config.enable()
        self.addCleanup(config.disable)
        self.org = Organization.objects.first()
        self.other = Organization.objects.create(name='Другой работодатель')
        users = get_user_model()
        self.owner = users.objects.create_user('ops-owner',password='TestOwnerPassword-2026!')
        self.worker = users.objects.create_user('ops-worker',password='TestWorkerPassword-2026!')
        self.colleague = users.objects.create_user('ops-colleague',password='TestColleaguePassword-2026!')
        self.tech = Technician.objects.create(organization=self.org,full_name='Исполнитель')
        self.second = Technician.objects.create(organization=self.org,full_name='Другой механик')
        self.foreign = Technician.objects.create(organization=self.other,full_name='Другой работодатель')
        Membership.objects.create(organization=self.org,user=self.owner,role='ADMIN')
        Membership.objects.create(organization=self.other,user=self.owner,role='ADMIN')
        Membership.objects.create(organization=self.org,user=self.worker,role='MECHANIC',technician=self.tech)
        Membership.objects.create(organization=self.org,user=self.colleague,role='MECHANIC',technician=self.second)
        building = Building.objects.create(organization=self.org,address='Проверочная улица, 1')
        self.lift = Elevator.objects.create(organization=self.org,building=building,inventory_number='OPS-1')
        self.client = APIClient()
        self.login(self.owner)
    def login(self,user,organization=None):
        self.client.force_authenticate(user=user)
        self.client.credentials(HTTP_X_ORGANIZATION_ID=str((organization or self.org).pk))
    def photo(self):
        output = io.BytesIO()
        Image.new('RGB',(8,8),'green').save(output,format='PNG')
        return SimpleUploadedFile('image.png',output.getvalue(),content_type='image/png')
    def task(self):
        response = self.client.post('/api/tasks/',{'title':'Проверить инструмент','assigned_to':self.tech.pk,'description':'Личное задание'},format='json')
        self.assertEqual(response.status_code,201,response.data)
        return response.data
    def native_device(self,user):
        session = MobileSession.objects.create(user=user,token_hash='h'+str(user.pk),password_stamp=user.get_session_auth_hash(),
            device_name='CI',expires_at=timezone.now()+timedelta(days=1))
        return NativePushDevice.objects.create(session=session,token='CI-'+str(user.pk))
    def test_media_only_chat_and_authenticated_image(self):
        self.login(self.worker)
        response = self.client.post('/api/chat/',{'files':[self.photo()]},format='multipart')
        self.assertEqual(response.status_code,201,response.data)
        self.assertEqual(response.data['body'],'')
        attachment = response.data['attachments'][0]
        self.assertEqual(attachment['content_type'],'image/png')
        result = self.client.get(attachment['url'])
        self.assertEqual(result.status_code,200)
        self.assertEqual(result['Cache-Control'],'private, no-store')
        self.assertTrue(b''.join(result.streaming_content).startswith(b'\x89PNG'))
        self.login(self.owner,self.other)
        self.assertEqual(self.client.get(attachment['url']).status_code,404)
        self.client.force_authenticate(user=None)
        self.assertIn(self.client.get(attachment['url']).status_code,[401,403])
    def test_empty_chat_and_disguised_html_do_not_create_messages(self):
        self.assertEqual(self.client.post('/api/chat/',{'body':''},format='json').status_code,400)
        fake = SimpleUploadedFile('fake.mp4',b'<html><script>bad</script></html>',content_type='video/mp4')
        response = self.client.post('/api/chat/',{'body':'Should roll back','files':[self.photo(),fake]},format='multipart')
        self.assertEqual(response.status_code,400)
        self.assertFalse(ChatMessage.objects.exists())
        self.assertFalse(any(p.is_file() for p in Path(self.temp.name).rglob('*')))
    def test_chat_attachment_count_is_bounded(self):
        response = self.client.post('/api/chat/',{'files':[self.photo() for _ in range(6)]},format='multipart')
        self.assertEqual(response.status_code,400)
        self.assertFalse(ChatMessage.objects.exists())
    def test_real_video_upload_and_private_range(self):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('ffmpeg is installed in the release image')
        path = Path(self.temp.name)/'clip.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=green:s=16x16:d=1',
                        '-c:v','libx264','-pix_fmt','yuv420p','-movflags','+faststart',str(path)],check=True,timeout=15)
        blob = path.read_bytes()
        response = self.client.post('/api/chat/',{'body':'Видео проверки','files':[SimpleUploadedFile('clip.mp4',blob)]},format='multipart')
        self.assertEqual(response.status_code,201,response.data)
        url = response.data['attachments'][0]['url']
        result = self.client.get(url,HTTP_RANGE='bytes=0-15')
        self.assertEqual(result.status_code,206)
        self.assertEqual(b''.join(result.streaming_content),blob[:16])
        self.assertEqual(result['Content-Range'],f'bytes 0-15/{len(blob)}')
        self.assertEqual(self.client.get(url,HTTP_RANGE='bytes=9999999-').status_code,416)
    def test_video_probe_is_bounded_and_rejects_invalid_containers(self):
        fake = SimpleUploadedFile('fake.mp4',b'\0\0\0\x18ftypisom' + b'x'*32)
        with mock.patch('core.media.shutil.which',return_value='/usr/bin/ffprobe'), mock.patch('core.media.subprocess.run',return_value=subprocess.CompletedProcess([],0,b'{}')) as probe:
            response = self.client.post('/api/chat/',{'files':[fake]},format='multipart')
        self.assertEqual(response.status_code,400)
        self.assertEqual(probe.call_args.kwargs['timeout'],10)
        self.assertIn('file',probe.call_args.args[0])
        self.assertIn('-format_whitelist',probe.call_args.args[0])
    def test_task_is_private_and_never_a_shared_ticket(self):
        row = self.task()
        self.assertFalse(Ticket.objects.exists())
        self.login(self.colleague)
        self.assertEqual(self.client.get('/api/tasks/').data,[])
        for suffix in ('','accept/','comment/','attachments/'):
            response = self.client.get(f"/api/tasks/{row['id']}/{suffix}") if not suffix else self.client.post(f"/api/tasks/{row['id']}/{suffix}",{},format='json')
            self.assertEqual(response.status_code,404)
        self.assertEqual(self.client.post('/api/tasks/',{'title':'Illegal','assigned_to':self.second.pk},format='json').status_code,403)
        self.login(self.owner,self.other)
        self.assertEqual(self.client.get(f"/api/tasks/{row['id']}/").status_code,404)
    def test_personal_assignment_rejects_foreign_and_inactive_staff(self):
        for technician in (self.foreign,self.second):
            technician.active=False
            technician.save()
            response=self.client.post('/api/tasks/',{'title':'Задание','assigned_to':technician.pk},format='json')
            self.assertEqual(response.status_code,400)
    def test_personal_task_workflow_and_immutable_result(self):
        row = self.task()
        root = f"/api/tasks/{row['id']}/"
        self.login(self.worker)
        self.assertEqual(self.client.post(root+'complete/',{'body':'Too early'},format='json').status_code,403)
        self.assertEqual(self.client.post(root+'accept/',{},format='json').status_code,200)
        self.assertEqual(self.client.post(root+'comment/',{'body':'Инструмент найден'},format='json').status_code,200)
        self.assertEqual(self.client.post(root+'attachments/',{'file':self.photo()},format='multipart').status_code,201)
        self.assertEqual(self.client.post(root+'complete/',{'body':''},format='json').status_code,400)
        result = self.client.post(root+'complete/',{'body':'Проверено, инструмент исправен'},format='json')
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.data['status'],'DONE')
        self.assertEqual(len(result.data['attachments']),1)
        self.assertEqual(self.client.post(root+'comment/',{'body':'Rewrite'},format='json').status_code,400)
        self.assertEqual(self.client.patch(root,{'result':'overwrite'},format='json').status_code,403)
        self.assertEqual(self.client.delete(root).status_code,405)
        self.assertEqual(PersonalTaskEvent.objects.filter(task_id=row['id']).count(),5)
    def test_cancel_requires_manager_and_reason(self):
        row = self.task()
        root = f"/api/tasks/{row['id']}/cancel/"
        self.login(self.worker)
        self.assertEqual(self.client.post(root,{'body':'Передумал'},format='json').status_code,403)
        self.login(self.owner)
        self.assertEqual(self.client.post(root,{'body':''},format='json').status_code,400)
        self.assertEqual(self.client.post(root,{'body':'Работа больше не требуется'},format='json').status_code,200)
    def test_task_notifications_are_for_assignee_only_and_honor_revocation(self):
        owner_device=self.native_device(self.owner)
        worker_device=self.native_device(self.worker)
        self.native_device(self.colleague)
        row=self.task()
        delivery=PersonalTaskPush.objects.get(event__task_id=row['id'])
        self.assertEqual(delivery.device_id,worker_device.pk)
        self.assertNotEqual(delivery.device_id,owner_device.pk)
        Membership.objects.filter(user=self.worker,organization=self.org).update(active=False)
        self.assertEqual(task_push.send_one(delivery),'DISABLED')
    def test_notification_preferences_are_user_and_employer_scoped(self):
        self.assertTrue(self.client.get('/api/notification-preferences/').data['entrapment'])
        response=self.client.patch('/api/notification-preferences/',{'entrapment':False},format='json')
        self.assertEqual(response.status_code,200)
        self.assertFalse(allows(self.owner.pk,self.org.pk,'entrapment'))
        self.assertTrue(allows(self.owner.pk,self.other.pk,'entrapment'))
        self.assertTrue(allows(self.worker.pk,self.org.pk,'entrapment'))
        self.assertEqual(category('ENTRAPMENT'),'entrapment')
        self.assertEqual(category('ENTRAPMENT','completed'),'updates')
    def test_entrapment_is_a_valid_priority_and_is_present_in_private_push_payload(self):
        device=self.native_device(self.worker)
        response=self.client.post('/api/tickets/',{'elevator':self.lift.pk,'title':'Люди в кабине','priority':'ENTRAPMENT'},format='json')
        self.assertEqual(response.status_code,201,response.data)
        delivery=NativePushDelivery.objects.get(device=device,ticket_id=response.data['id'])
        with mock.patch('core.native_push.client_config',return_value={'projectId':'ci-test'}),mock.patch('core.native_push.access_token',return_value='ci'),mock.patch('requests.post') as post:
            post.return_value.status_code=200
            self.assertEqual(native_push.send_one(delivery),'SENT')
            data=post.call_args.kwargs['json']['message']['data']
            self.assertEqual(data['priority'],'ENTRAPMENT')
            self.assertNotIn('title',data)
            self.assertNotIn('address',data)
    def test_document_edit_keeps_revision_and_rejects_conflict_and_foreign_access(self):
        response=self.client.post('/api/documents/',{'title':'Схема','manufacturer':'AS380','file':SimpleUploadedFile('one.pdf',b'%PDF-1.7 old')},format='multipart')
        self.assertEqual(response.status_code,201,response.data)
        row=response.data
        old_file=Document.objects.get(pk=row['id']).file.name
        response=self.client.patch(f"/api/documents/{row['id']}/",{'title':'Схема задней двери','expected_updated_at':row['updated_at'],'file':SimpleUploadedFile('two.pdf',b'%PDF-1.7 new')},format='multipart')
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(DocumentRevision.objects.get().file.name,old_file)
        self.assertTrue((Path(self.temp.name)/old_file).exists())
        self.assertEqual(self.client.patch(f"/api/documents/{row['id']}/",{'title':'Stale edit','expected_updated_at':row['updated_at']},format='json').status_code,400)
        self.login(self.worker)
        self.assertEqual(self.client.patch(f"/api/documents/{row['id']}/",{'title':'Illegal'},format='json').status_code,403)
        self.login(self.owner,self.other)
        self.assertEqual(self.client.patch(f"/api/documents/{row['id']}/",{'title':'Illegal'},format='json').status_code,404)
