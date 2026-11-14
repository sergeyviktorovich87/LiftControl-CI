import hashlib
import io
import tempfile
import uuid
from unittest.mock import patch
from pathlib import Path
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase,override_settings
from PIL import Image
from rest_framework.test import APIClient
from .models import Organization,Membership,Technician,Building,Elevator,Ticket,TicketPhoto,MediaAsset,MediaLink,ChatMessage,PersonalTask


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class MediaVaultTests(TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.settings=override_settings(MEDIA_ROOT=self.temp.name);self.settings.enable();self.addCleanup(self.settings.disable)
        self.org=Organization.objects.get(pk=1);self.other=Organization.objects.create(name='Другая')
        self.owner=get_user_model().objects.create_user('media-owner')
        self.worker=get_user_model().objects.create_user('media-worker')
        self.peer=get_user_model().objects.create_user('media-peer')
        self.tech=Technician.objects.create(organization=self.org,full_name='Работник')
        self.second=Technician.objects.create(organization=self.org,full_name='Коллега')
        Membership.objects.create(user=self.owner,organization=self.org,role='ADMIN')
        Membership.objects.create(user=self.owner,organization=self.other,role='ADMIN')
        Membership.objects.create(user=self.worker,organization=self.org,role='MECHANIC',technician=self.tech)
        Membership.objects.create(user=self.peer,organization=self.org,role='MECHANIC',technician=self.second)
        building=Building.objects.create(organization=self.org,name='Дом',address='Тестовая 1')
        self.lift=Elevator.objects.create(organization=self.org,building=building,assigned_technician=self.tech)
        self.ticket=Ticket.objects.create(organization=self.org,elevator=self.lift,title='Ремонт',status='IN_PROGRESS',assigned_to=self.tech)
        self.client=APIClient();self.client.force_authenticate(self.worker)
        output=io.BytesIO();Image.new('RGB',(700,400),'green').save(output,format='PNG');self.raw=output.getvalue()

    def send(self,**fields):
        fields={'upload_id':str(uuid.uuid4()),'kind':'ticket','target_id':str(self.ticket.pk),**fields}
        fields['file']=SimpleUploadedFile('original-name.png',self.raw,content_type='image/png')
        return self.client.post('/api/media/uploads/',fields,format='multipart',HTTP_X_ORGANIZATION_ID=str(fields.pop('_org',1)))

    def test_original_preview_and_private_download(self):
        result=self.send();self.assertEqual(result.status_code,201,result.content)
        asset=MediaAsset.objects.get();self.assertEqual(asset.sha256,hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(asset.file.read(),self.raw)
        self.assertTrue(asset.file.name.startswith(f'org_1/{asset.created_at:%Y/%m}/{asset.pk}/original.png'))
        self.assertEqual(self.client.get('/api/media/',{'elevator':self.lift.pk}).json()['count'],1)
        self.assertEqual(self.ticket.photos.get().asset_id,asset.pk)
        with Image.open(asset.preview) as preview:self.assertLessEqual(max(preview.size),480)
        response=self.client.get(f'/api/media/{asset.pk}/download/',HTTP_RANGE='bytes=0-7')
        self.assertEqual(response.status_code,206);self.assertEqual(b''.join(response.streaming_content),self.raw[:8])
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(f'/api/media/{asset.pk}/preview/').status_code,(401,403))

    def test_retry_is_idempotent_and_payload_reuse_conflicts(self):
        key=str(uuid.uuid4())
        first=self.send(upload_id=key);second=self.send(upload_id=key)
        self.assertEqual(second.status_code,200,second.content);self.assertTrue(second.json()['replayed'])
        self.assertEqual(first.json()['asset']['id'],second.json()['asset']['id'])
        self.assertEqual(self.send(upload_id=key,category='DEFECT').status_code,409)
        self.assertEqual(MediaAsset.objects.count(),1);self.assertEqual(MediaLink.objects.count(),1)

    def test_one_original_links_to_chat_lift_and_ticket(self):
        one=self.send().json()['asset']
        chat=self.send(kind='chat',body='Фото',message_key=str(uuid.uuid4()))
        self.assertEqual(chat.status_code,201,chat.content)
        linked=self.client.post(f"/api/media/{one['id']}/links/",{'kind':'elevator','target_id':self.lift.pk},format='json')
        self.assertEqual(linked.status_code,200,linked.content)
        self.assertEqual(MediaAsset.objects.count(),1);self.assertEqual(MediaLink.objects.count(),3)
        self.assertEqual(len(list(Path(self.temp.name).rglob('original.*'))),1)
        self.assertEqual(ChatMessage.objects.count(),1)
        listing=self.client.get('/api/media/',{'elevator':self.lift.pk}).json()
        self.assertEqual(listing['count'],1)

    def test_tenants_are_physically_and_logically_separate(self):
        asset=self.send().json()['asset']
        self.client.force_authenticate(self.owner)
        other=self.send(kind='library',target_id='',_org=self.other.pk)
        self.assertEqual(other.status_code,201,other.content)
        self.assertNotEqual(other.json()['asset']['id'],asset['id'])
        self.assertEqual(self.client.get(f"/api/media/{asset['id']}/download/",HTTP_X_ORGANIZATION_ID=str(self.other.pk)).status_code,404)
        self.assertEqual(MediaAsset.objects.count(),2)

    def test_private_task_does_not_leak_after_reassignment(self):
        task=PersonalTask.objects.create(organization=self.org,title='Личное',assigned_to=self.tech,created_by=self.owner,status='IN_PROGRESS')
        result=self.send(kind='task',target_id=str(task.pk));self.assertEqual(result.status_code,201,result.content)
        asset=result.json()['asset']['id'];task.assigned_to=self.second;task.save()
        self.assertEqual(self.client.get(f'/api/media/{asset}/download/').status_code,404)
        self.client.force_authenticate(self.peer)
        self.assertEqual(self.client.get(f'/api/media/{asset}/download/').status_code,200)

    def test_public_file_cannot_reveal_private_task_location_in_filters(self):
        task = PersonalTask.objects.create(
            organization=self.org, title='Личное', assigned_to=self.tech,
            created_by=self.owner, status='IN_PROGRESS', elevator=self.lift,
        )
        self.assertEqual(self.send(kind='task', target_id=str(task.pk)).status_code, 201)
        self.assertEqual(self.send(kind='chat', message_key=str(uuid.uuid4())).status_code, 201)
        self.assertEqual(self.client.get('/api/media/', {'elevator': self.lift.pk}).json()['count'], 1)

        self.client.force_authenticate(self.peer)
        result = self.client.get('/api/media/', {'source': 'chat'}).json()
        self.assertEqual(result['count'], 1)
        self.assertEqual([link['kind'] for link in result['results'][0]['links']], ['message'])
        self.assertEqual(self.client.get('/api/media/', {'elevator': self.lift.pk}).json()['count'], 0)
        self.assertEqual(self.client.get('/api/media/', {'address': 'Тестовая'}).json()['count'], 0)

    def test_checksum_rejection_publishes_nothing(self):
        self.assertEqual(self.send(sha256='0'*64).status_code,400)
        self.assertEqual(MediaAsset.objects.count(),0)

    def test_legacy_migration_retains_path_and_is_rerunnable(self):
        row=TicketPhoto(organization=self.org,ticket=self.ticket,uploaded_by=self.worker)
        row.file.save('legacy.png',ContentFile(self.raw));old=row.file.name
        call_command('migrate_media_vault',stdout=io.StringIO())
        row.refresh_from_db();self.assertIsNotNone(row.asset_id);self.assertEqual(row.file.name,old)
        self.assertTrue(row.file.storage.exists(old));self.assertEqual(row.asset.file.read(),self.raw)
        call_command('migrate_media_vault',stdout=io.StringIO())
        self.assertEqual(MediaAsset.objects.count(),1);self.assertEqual(MediaLink.objects.count(),1)

    def test_missing_legacy_file_stops_transition(self):
        row=TicketPhoto.objects.create(organization=self.org,ticket=self.ticket,uploaded_by=self.worker,file='missing')
        with self.assertRaises(CommandError):call_command('migrate_media_vault',stdout=io.StringIO(),stderr=io.StringIO())
        row.refresh_from_db();self.assertIsNone(row.asset_id)

    def test_statistics_and_immutable_history(self):
        asset=self.send().json()['asset']['id']
        self.assertEqual(self.client.get('/api/media/stats/').status_code,403)
        self.assertEqual(self.client.delete(f'/api/media/{asset}/download/').status_code,405)
        self.client.force_authenticate(self.owner)
        stats=self.client.get('/api/media/stats/').json()
        self.assertEqual(stats['originals'],len(self.raw));self.assertEqual(stats['count'],1)
        self.assertFalse(stats['automatic_deletion'])
        Membership.objects.filter(user=self.owner,organization=self.org).update(role='DIRECTOR')
        self.assertEqual(self.client.get('/api/media/stats/').status_code,200)

    def test_failed_receipt_rolls_back_database_and_uncommitted_files(self):
        from .media_vault import atomic_media,ingest,link_asset
        with self.assertRaises(RuntimeError):
            with atomic_media():
                asset=ingest(self.worker,SimpleUploadedFile('photo.png',self.raw))
                link_asset(self.worker,asset,'ticket',self.ticket)
                raise RuntimeError('Simulated lost database transaction')
        self.assertFalse(MediaAsset.objects.exists())
        self.assertFalse(MediaLink.objects.exists())
        self.assertFalse(any(p.is_file() for p in Path(self.temp.name).rglob('*')))

    def test_reusing_an_original_survives_another_failed_transaction(self):
        from .media_vault import atomic_media,ingest
        asset_id=self.send().json()['asset']['id']
        with self.assertRaises(RuntimeError):
            with atomic_media():
                ingest(self.worker,SimpleUploadedFile('again.png',self.raw))
                raise RuntimeError('Simulated rollback')
        self.assertEqual(MediaAsset.objects.get(pk=asset_id).file.read(),self.raw)
