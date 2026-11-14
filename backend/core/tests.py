import io
import tempfile
from datetime import datetime, timedelta, timezone as dtz
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient
from .models import (Organization, Membership, Building, Elevator, Technician, Ticket,
                     DutyShift, PartRequest, ChatMessage, TicketPhoto, Document, AuditEvent, PushSubscription, PushDelivery)


class OperationalTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        setting = override_settings(MEDIA_ROOT=self.media.name)
        setting.enable()
        self.addCleanup(setting.disable)
        self.org = Organization.objects.get(pk=1)
        self.other = Organization.objects.create(name='Other organization')
        self.tech = Technician.objects.create(full_name='Mechanic One')
        self.tech2 = Technician.objects.create(full_name='Mechanic Two')
        self.othertech = Technician.objects.create(organization=self.other,full_name='Other')
        self.building = Building.objects.create(name='Test',address='Street, 1',street='Street',house='1')
        self.lift = Elevator.objects.create(building=self.building,factory_number='TEST-1',manufacturer='MEL',model='A',assigned_technician=self.tech)
        self.foreignbuilding = Building.objects.create(organization=self.other,name='Other',address='Other 2')
        self.foreignlift = Elevator.objects.create(organization=self.other,building=self.foreignbuilding,factory_number='OTHER-1')
        self.admin = self.user('admin','ADMIN')
        self.director = self.user('director','DIRECTOR')
        self.mechanic = self.user('mechanic','MECHANIC',self.tech)
        self.mechanic2 = self.user('mechanic2','MECHANIC',self.tech2)
        self.ticket = Ticket.objects.create(elevator=self.lift,title='Noise',assigned_to=self.tech,status='IN_PROGRESS',accepted_at=timezone.now())
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def user(self,name,role,tech=None):
        u = get_user_model().objects.create_user(username=name,password='test-password-long')
        Membership.objects.create(user=u,organization=self.org,role=role,technician=tech)
        return u

    def post(self,path,data):
        return self.client.post('/api/'+path,data,format='json')

    def photo(self,size=(12,12),exif=False):
        image = Image.new('RGB',size,'green')
        buf = io.BytesIO()
        meta = Image.Exif()
        if exif:
            meta[270] = 'private metadata'
        image.save(buf,format='JPEG',exif=meta)
        return SimpleUploadedFile('unsafe-name.jpg',buf.getvalue(),content_type='image/jpeg')

    def plan(self,**extra):
        options={'from':'2026-12-01','to':'2027-01-31','time':'08:00','hours':24,
                 'every':1,'kind':'daily','technicians':[self.tech.id,self.tech2.id],'replace':False}
        options.update(extra)
        return self.post('duty/preview/',options)

    def test_health_public_but_data_private(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/health/').status_code,200)
        for endpoint in ['me','elevators','tickets','parts','chat','documents','duty']:
            self.assertIn(self.client.get('/api/'+endpoint+'/').status_code,(401,403))
        response=self.client.get('/')
        self.assertEqual(response.status_code,302)
        self.assertIn('/login/',response['Location'])

    def test_membership_not_staff_controls_access(self):
        self.client.force_authenticate(self.mechanic)
        self.assertFalse(self.mechanic.is_staff)
        self.assertEqual(self.client.get('/api/me/').json()['role'],'MECHANIC')
        self.assertEqual(self.client.get('/api/elevators/').status_code,200)
        self.mechanic.memberships.update(active=False)
        self.assertEqual(self.client.get('/api/elevators/').status_code,403)

    def test_tenant_list_detail_and_relation_are_isolated(self):
        self.assertEqual([r['id'] for r in self.client.get('/api/elevators/').json()],[self.lift.id])
        self.assertEqual(self.client.get(f'/api/elevators/{self.foreignlift.id}/').status_code,404)
        self.assertEqual(self.post('parts/',{'elevator':self.foreignlift.id,'part':'test'}).status_code,400)
        result=self.post('parts/',{'elevator':self.lift.id,'part':'test','organization':self.other.id})
        self.assertEqual(result.status_code,201)
        self.assertEqual(PartRequest.objects.get(pk=result.json()['id']).organization_id,self.org.id)

    def test_director_and_mechanic_cannot_export_chat(self):
        for u in [self.director,self.mechanic]:
            self.client.force_authenticate(u)
            self.assertEqual(self.client.get('/api/chat/export/?from=2026-01-01&to=2026-12-31').status_code,403)
            self.assertFalse(self.client.get('/api/me/').json()['can_export_chat'])

    def test_chat_author_and_organization_are_server_assigned(self):
        self.client.force_authenticate(self.mechanic)
        r=self.post('chat/',{'body':'hello','author':self.admin.id,'organization':self.other.id})
        self.assertEqual(r.status_code,201)
        message=ChatMessage.objects.get(pk=r.json()['id'])
        self.assertEqual(message.author,self.mechanic)
        self.assertEqual(message.organization,self.org)
        ChatMessage.objects.create(organization=self.other,author=self.admin,body='private')
        self.assertEqual([m['body'] for m in self.client.get('/api/chat/').json()],['hello'])
        self.assertEqual(self.client.patch(f'/api/chat/{message.pk}/',{'body':'changed'},format='json').status_code,405)

    def test_admin_export_period_and_formula_escape(self):
        a=ChatMessage.objects.create(organization=self.org,author=self.mechanic,body='=1+1')
        old=ChatMessage.objects.create(organization=self.org,author=self.mechanic,body='old')
        ChatMessage.objects.filter(pk=old.pk).update(created_at=timezone.now()-timedelta(days=20))
        today=timezone.localdate().isoformat()
        r=self.client.get(f'/api/chat/export/?from={today}&to={today}')
        self.assertEqual(r.status_code,200)
        self.assertIn("'=1+1",r.content.decode())
        self.assertNotIn('old',r.content.decode())
        self.assertEqual(AuditEvent.objects.filter(action='chat.export').count(),1)
        self.assertNotIn(a.body,str(list(AuditEvent.objects.values())))

    def test_mechanic_parts_request_and_status_permissions(self):
        self.client.force_authenticate(self.mechanic)
        r=self.post('parts/',{'elevator':self.lift.id,'part':'Door roller','quantity':2,'requested_by':self.admin.id})
        self.assertEqual(r.status_code,201)
        url=f"/api/parts/{r.json()['id']}/"
        self.assertEqual(r.json()['requested_by'],self.mechanic.id)
        self.assertEqual(self.client.patch(url,{'status':'ORDERED'},format='json').status_code,400)
        self.assertEqual(self.post('parts/',{'elevator':self.lift.id,'part':'x','quantity':0}).status_code,400)
        self.client.force_authenticate(self.mechanic2)
        self.assertEqual(self.client.patch(url,{'part':'changed'},format='json').status_code,400)
        self.client.force_authenticate(self.director)
        self.assertEqual(self.client.patch(url,{'status':'ORDERED'},format='json').status_code,200)

    def test_mechanic_cannot_assign_roles_or_modify_assets(self):
        self.client.force_authenticate(self.mechanic)
        self.assertEqual(self.post('buildings/',{'name':'x','address':'x'}).status_code,403)
        self.assertEqual(self.client.patch(f'/api/technicians/{self.tech.pk}/',{'active':False},format='json').status_code,403)
        self.assertEqual(self.plan().status_code,403)

    def test_ticket_completion_requires_report_and_assignee(self):
        base=f'/api/tickets/{self.ticket.pk}/'
        self.assertEqual(self.client.patch(base,{'status':'DONE'},format='json').status_code,400)
        self.client.force_authenticate(self.mechanic)
        self.assertEqual(self.post(f'tickets/{self.ticket.pk}/complete/',{'work_description':'  '}).status_code,400)
        result=self.post(f'tickets/{self.ticket.pk}/complete/',{'work_description':'Adjusted door and checked'})
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()['status'],'DONE')
        self.assertEqual(self.ticket.comments.count(),1)
        self.assertEqual(self.post(f'tickets/{self.ticket.pk}/complete/',{'work_description':'other'}).status_code,403)
        self.assertEqual(self.client.patch(base,{'repair':'overwrite'},format='json').status_code,403)
        self.assertEqual(self.client.delete(base).status_code,403)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.repair,'Adjusted door and checked')

    def test_only_duty_mechanic_can_select_assignee_and_cannot_spoof_status_or_author(self):
        self.client.force_authenticate(self.mechanic)
        payload={'title':'New fault','elevator':self.lift.pk,'assigned_to':self.tech2.pk,
                 'status':'DONE','repair':'forged','created_by':self.admin.pk}
        self.assertEqual(self.post('tickets/',payload).status_code,403)
        now=timezone.now()
        DutyShift.objects.create(organization=self.org,technician=self.tech,
                                 start=now-timedelta(minutes=10),end=now+timedelta(hours=1))
        r=self.post('tickets/',payload)
        self.assertEqual(r.status_code,201)
        created=Ticket.objects.get(pk=r.json()['id'])
        self.assertEqual(created.status,'NEW')
        self.assertEqual(created.assigned_to_id,self.tech2.pk)
        self.assertEqual(created.created_by_id,self.mechanic.pk)
        self.assertEqual(created.repair,'')
        self.assertEqual(created.events.count(),1)

    def test_photo_private_original_preview_and_soft_delete(self):
        self.client.force_authenticate(self.mechanic)
        r=self.client.post(f'/api/tickets/{self.ticket.pk}/photos/',{'file':self.photo((2700,1),True)},format='multipart')
        self.assertEqual(r.status_code,201)
        url=r.json()['url']
        photo=TicketPhoto.objects.get(pk=r.json()['id'])
        self.assertEqual(self.ticket.events.filter(action='photo_added').count(), 1)
        self.assertEqual(self.client.get(f'/api/tickets/{self.ticket.pk}/').json()['photos'][0]['uploaded_by'],
                         self.mechanic.username)
        self.assertNotIn('unsafe-name',photo.file.name)
        with Image.open(photo.file.path) as image:
            self.assertEqual(image.width,2700)
            self.assertEqual(image.getexif().get(270),'private metadata')
        with Image.open(photo.asset.preview.path) as preview:
            self.assertLessEqual(preview.width,480)
            self.assertEqual(len(preview.getexif()),0)
        response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertTrue(b''.join(response.streaming_content).startswith(b'\xff\xd8'))
        self.assertEqual(self.client.get('/private-media-not-public/'+photo.file.name).status_code,404)
        foreign=self.user('foreign','ADMIN'); foreign.memberships.update(organization=self.other)
        self.client.force_authenticate(foreign)
        self.assertIn(self.client.get(url).status_code,(403,404))
        self.client.force_authenticate(self.mechanic)
        self.assertEqual(self.client.delete(url).status_code,403)
        self.assertEqual(self.client.get(url).status_code,200)
        photo.refresh_from_db();self.assertIsNone(photo.deleted_at)

    def test_photo_rejects_nonimage_large_upload_and_wrong_mechanic(self):
        self.client.force_authenticate(self.mechanic)
        url=f'/api/tickets/{self.ticket.pk}/photos/'
        self.assertEqual(self.client.post(url,{'file':SimpleUploadedFile('bad.jpg',b'<script>')},format='multipart').status_code,400)
        self.assertEqual(self.client.post(url,{'file':SimpleUploadedFile('huge.jpg',b'x'*(12*1024*1024))},format='multipart').status_code,400)
        self.client.force_authenticate(self.mechanic2)
        self.assertEqual(self.client.post(url,{'file':self.photo()},format='multipart').status_code,403)
        self.assertEqual(TicketPhoto.objects.count(),0)

    def test_photo_count_limit(self):
        self.client.force_authenticate(self.mechanic)
        for _ in range(8): TicketPhoto.objects.create(organization=self.org,ticket=self.ticket,file='test-unused')
        r=self.client.post(f'/api/tickets/{self.ticket.pk}/photos/',{'file':self.photo()},format='multipart')
        self.assertEqual(r.status_code,400)
        self.assertEqual(TicketPhoto.objects.count(),8)

    def test_document_shared_matching_and_private_download(self):
        r=self.client.post('/api/documents/',{'title':'Manual','manufacturer':'mel','model':'A','file':SimpleUploadedFile('manual.pdf',b'%PDF-1.4\n%%EOF')},format='multipart')
        self.assertEqual(r.status_code,201)
        self.assertEqual(len(self.client.get(f'/api/elevators/{self.lift.pk}/documents/').json()),1)
        self.client.force_authenticate(self.mechanic)
        self.assertEqual(self.client.post('/api/documents/',{'title':'x'},format='multipart').status_code,403)
        self.assertEqual(self.client.get(r.json()['url']).status_code,200)

    def test_single_24h_adjacency_and_overlap(self):
        payload={'technician':self.tech.id,'start':'2026-12-31T08:00:00+09:00','end':'2027-01-01T08:00:00+09:00','kind':'daily'}
        self.assertEqual(self.post('duty/',payload).status_code,201)
        self.assertEqual(self.post('duty/',payload).status_code,400)
        payload.update(start='2027-01-01T08:00:00+09:00',end='2027-01-02T08:00:00+09:00')
        self.assertEqual(self.post('duty/',payload).status_code,201)
        self.assertEqual(len(self.client.get('/api/duty/?from=2027-01-01&to=2027-01-01').json()),2)
        payload['end']='2027-01-03T08:00:00+09:00'
        self.assertEqual(self.post('duty/',payload).status_code,400)

    def test_rotation_62_days_across_year_preview_apply(self):
        r=self.plan();self.assertEqual(r.status_code,200)
        preview=r.json();self.assertEqual(len(preview['added']),62)
        self.assertEqual([s['technician_id'] for s in preview['added'][:3]],[self.tech.pk,self.tech2.pk,self.tech.pk])
        self.assertEqual(DutyShift.objects.count(),0)
        self.assertEqual(self.post('duty/apply/',{'token':preview['token']}).status_code,200)
        self.assertEqual(DutyShift.objects.count(),62)
        self.assertEqual(self.post('duty/apply/',{'token':preview['token']}).status_code,400)

    def test_stale_or_other_user_plan_never_deletes_shifts(self):
        preview=self.plan(replace=True).json()
        self.client.force_authenticate(self.director)
        self.assertEqual(self.post('duty/apply/',{'token':preview['token']}).status_code,403)
        self.client.force_authenticate(self.admin)
        DutyShift.objects.create(organization=self.org,technician=self.tech,start=datetime(2026,12,1,tzinfo=dtz.utc),end=datetime(2026,12,2,tzinfo=dtz.utc))
        self.assertEqual(self.post('duty/apply/',{'token':preview['token']}).status_code,400)
        self.assertEqual(DutyShift.objects.count(),1)

    def test_cross_boundary_shift_preserved_and_conflict_reported(self):
        DutyShift.objects.create(organization=self.org,technician=self.tech,start=datetime.fromisoformat('2026-11-30T12:00:00+09:00'),end=datetime.fromisoformat('2026-12-01T12:00:00+09:00'))
        self.assertEqual(self.plan(replace=True).status_code,400)
        r=self.plan(replace=False).json()
        self.assertEqual(r['skipped'],1)
        self.assertEqual(DutyShift.objects.count(),1)

    def test_invalid_plan_does_not_mutate(self):
        for changes in [{'to':'2028-12-01'},{'hours':25},{'technicians':[self.othertech.pk]},{'technicians':[]},{'every':0}]:
            self.assertEqual(self.plan(**changes).status_code,400)
        self.assertEqual(DutyShift.objects.count(),0)

    def test_session_csrf_required(self):
        client=APIClient(enforce_csrf_checks=True)
        client.force_login(self.mechanic)
        self.assertEqual(client.post('/api/chat/',{'body':'x'},format='json').status_code,403)
        token=client.get('/api/me/').json()['csrf']
        self.assertEqual(client.post('/api/chat/',{'body':'x'},format='json',HTTP_X_CSRFTOKEN=token).status_code,201)

    def test_operational_history_protected(self):
        with self.assertRaises(ProtectedError): self.lift.delete()
        with self.assertRaises(ProtectedError): self.building.delete()
        self.client.force_authenticate(self.mechanic)
        self.assertEqual(self.post('maintenance/',{'elevator':self.lift.id,'kind':'TO1','planned_date':'2026-11-06','completed_at':timezone.now().isoformat(),'notes':''}).status_code,400)

    def test_accept_only_responsible_mechanic_and_history_is_visible(self):
        ticket=Ticket.objects.create(elevator=self.lift,title='Doors stopped')
        path=f'tickets/{ticket.pk}/accept/'
        self.client.force_authenticate(self.mechanic2)
        self.assertEqual(self.post(path,{}).status_code,403)
        self.client.force_authenticate(self.mechanic)
        accepted=self.post(path,{})
        self.assertEqual(accepted.status_code,200)
        self.assertEqual(accepted.json()['status'],'IN_PROGRESS')
        self.assertEqual(self.post(path,{}).status_code,403)
        self.client.force_authenticate(self.mechanic2)
        history=self.client.get(f'/api/tickets/{ticket.pk}/history/')
        self.assertEqual(history.status_code,200)
        self.assertEqual(history.json()['events'][0]['action'],'accepted')
        self.assertEqual(self.post(f'tickets/{ticket.pk}/complete/',{'work_description':'Forged'}).status_code,403)

    def test_cancel_reason_is_mandatory_and_only_on_duty_or_manager(self):
        ticket=Ticket.objects.create(elevator=self.lift,title='False alarm')
        self.client.force_authenticate(self.mechanic)
        url=f'tickets/{ticket.pk}/cancel/'
        self.assertEqual(self.post(url,{'reason':'Cancelled'}).status_code,403)
        now=timezone.now()
        DutyShift.objects.create(organization=self.org,technician=self.tech,
                                 start=now-timedelta(minutes=1),end=now+timedelta(hours=6))
        self.assertEqual(self.post(url,{'reason':'   '}).status_code,400)
        self.assertEqual(self.post(url,{'reason':'bad'}).status_code,400)
        self.assertEqual(self.post(url,{'reason':'Repeated report, no fault'}).status_code,200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status,'CANCELED')
        self.assertTrue(ticket.canceled_at)
        self.assertEqual(ticket.canceled_by,self.mechanic)
        self.assertEqual(ticket.cancellation_reason,'Repeated report, no fault')
        self.assertEqual(ticket.events.last().reason,'Repeated report, no fault')
        self.assertFalse(self.client.get(f'/api/tickets/{ticket.pk}/').json()['can_cancel'])
        self.client.force_authenticate(self.mechanic2)
        self.assertEqual(self.client.get(f'/api/tickets/{ticket.pk}/').status_code,200)
        self.assertEqual(self.client.delete(f'/api/tickets/{ticket.pk}/').status_code,403)
        self.assertEqual(self.post(f'tickets/{ticket.pk}/comment/',{'body':'changed'}).status_code,403)

    def test_director_can_cancel_completed_but_duty_cannot(self):
        self.client.force_authenticate(self.mechanic)
        now=timezone.now()
        DutyShift.objects.create(organization=self.org,technician=self.tech,
                                 start=now-timedelta(minutes=1),end=now+timedelta(hours=1))
        self.assertEqual(self.post(f'tickets/{self.ticket.pk}/complete/',
                                   {'work_description':'Cleaned and tested'}).status_code,200)
        self.assertEqual(self.post(f'tickets/{self.ticket.pk}/cancel/',{'reason':'Audit correction'}).status_code,403)
        self.client.force_authenticate(self.director)
        self.assertEqual(self.post(f'tickets/{self.ticket.pk}/cancel/',{'reason':'Audit correction'}).status_code,200)
        self.assertEqual(self.ticket.comments.count(),1)

    def test_assign_replacement_requires_manager_reason_and_active_user(self):
        ticket=Ticket.objects.create(elevator=self.lift,title='Out of service')
        self.client.force_authenticate(self.mechanic)
        url=f'tickets/{ticket.pk}/assign/'
        self.assertEqual(self.post(url,{'assigned_to':self.tech2.pk,'reason':'Mechanic unavailable'}).status_code,403)
        self.client.force_authenticate(self.director)
        self.assertEqual(self.post(url,{'assigned_to':self.tech2.pk,'reason':'x'}).status_code,400)
        self.assertEqual(self.post(url,{'assigned_to':'not-a-number','reason':'Mechanic unavailable'}).status_code,400)
        self.assertEqual(self.post(url,{'assigned_to':self.tech2.pk,'reason':'Mechanic unavailable'}).status_code,200)
        self.client.force_authenticate(self.mechanic)
        self.assertEqual(self.post(f'tickets/{ticket.pk}/accept/',{}).status_code,403)
        self.client.force_authenticate(self.mechanic2)
        self.assertEqual(self.post(f'tickets/{ticket.pk}/accept/',{}).status_code,200)

    def test_append_only_comments_cannot_be_modified_or_deleted(self):
        self.client.force_authenticate(self.mechanic)
        r=self.post(f'tickets/{self.ticket.pk}/comment/',{'body':'Checked door contacts'})
        self.assertEqual(r.status_code,201)
        cid=r.json()['id']
        self.assertEqual(self.client.patch(f'/api/tickets/{self.ticket.pk}/comment/{cid}/',
                                           {'body':'forgery'},format='json').status_code,404)
        self.assertEqual(self.client.delete(f'/api/tickets/{self.ticket.pk}/comment/{cid}/').status_code,404)
        self.client.force_authenticate(self.mechanic2)
        history=self.client.get(f'/api/tickets/{self.ticket.pk}/history/').json()
        self.assertEqual(history['comments'][0]['body'],'Checked door contacts')

    def test_push_destination_validation_prevents_ssrf(self):
        from .push_notifications import valid_endpoint
        for endpoint in ['http://fcm.googleapis.com/x', 'http://127.0.0.1/',
                         'https://127.0.0.1/internal', 'https://fcm.googleapis.com.evil.test/p',
                         'https://x@fcm.googleapis.com/x', 'https://fcm.googleapis.com:444/x']:
            self.assertFalse(valid_endpoint(endpoint))
        self.assertTrue(valid_endpoint('https://fcm.googleapis.com/fcm/send/123'))
        self.assertTrue(valid_endpoint('https://updates.push.services.mozilla.com/wpush/v2/xyz'))

    def test_all_active_mechanics_receive_queued_push_without_network(self):
        for index, user in enumerate([self.mechanic, self.mechanic2]):
            PushSubscription.objects.create(
                organization=self.org, user=user,
                endpoint='https://fcm.googleapis.com/fcm/send/test'+str(index),
                p256dh='abcde1234567890', auth='abcdefghijklm',
            )
        r=self.post('tickets/',{'title':'Fault with notification','elevator':self.lift.pk})
        self.assertEqual(r.status_code,201)
        ticket=Ticket.objects.get(pk=r.json()['id'])
        self.assertEqual(list(PushDelivery.objects.filter(ticket=ticket).values_list('kind',flat=True)),
                         ['created','created'])
        # An inactive staff member must be omitted from future queue entries.
        self.mechanic2.memberships.update(active=False)
        r=self.post('tickets/',{'title':'Second notification','elevator':self.lift.pk})
        self.assertEqual(r.status_code,201)
        self.assertEqual(PushDelivery.objects.filter(ticket_id=r.json()['id']).count(),1)

    def test_inactive_technician_never_receives_push(self):
        sub=PushSubscription.objects.create(
            organization=self.org,user=self.mechanic,
            endpoint='https://fcm.googleapis.com/fcm/send/inactive-mechanic',
            p256dh='abcde1234567890',auth='abcdefghijklm',
        )
        item=PushDelivery.objects.create(
            organization=self.org,subscription=sub,ticket=self.ticket,kind='created',
        )
        self.tech.active=False
        self.tech.save(update_fields=['active'])
        from .push_notifications import deliver_one
        self.assertEqual(deliver_one(item,{'private_file':'not-used','subject':'mailto:test@example.com'}),
                         'DISABLED')

    def test_django_admin_cannot_bypass_ticket_events(self):
        from .admin import TicketAdmin
        from django.contrib.admin.sites import AdminSite
        from django.test import RequestFactory
        from .models import Ticket
        admin=TicketAdmin(Ticket, AdminSite())
        request=RequestFactory().get('/admin/core/ticket/')
        self.admin.is_superuser=True
        self.admin.save(update_fields=['is_superuser'])
        request.user=self.admin
        self.assertTrue(admin.has_view_permission(request, self.ticket))
        self.assertFalse(admin.has_change_permission(request, self.ticket))
        self.assertFalse(admin.has_delete_permission(request, self.ticket))
        self.assertFalse(admin.has_add_permission(request))

    def test_revoked_mechanic_never_receives_existing_queued_push(self):
        sub=PushSubscription.objects.create(
            organization=self.org,user=self.mechanic,
            endpoint='https://fcm.googleapis.com/fcm/send/old',
            p256dh='abcde1234567890',auth='abcdefghijklm',
        )
        item=PushDelivery.objects.create(
            organization=self.org,subscription=sub,ticket=self.ticket,kind='created',
        )
        self.mechanic.memberships.update(active=False)
        from .push_notifications import deliver_one
        self.assertEqual(deliver_one(item,{'private_file':'not-used','subject':'mailto:test@example.com'}),
                         'DISABLED')

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class LegacyMigrationTests(TransactionTestCase):
    def test_existing_addresses_history_and_old_writer_survive(self):
        executor=MigrationExecutor(connection)
        executor.migrate([('core','0001_initial')])
        old=executor.loader.project_state([('core','0001_initial')]).apps
        old_building=old.get_model('core','Building')
        b=old_building.objects.create(name='Existing house',address='Учебная улица, 265/2')
        unclear=old_building.objects.create(name='Unstructured',address='Учебный комплекс')
        e=old.get_model('core','Elevator').objects.create(building=b,factory_number='LEGACY')
        ticket=old.get_model('core','Ticket').objects.create(elevator=e,title='Existing ticket',description='Keep me')
        latest=executor.loader.graph.leaf_nodes('core')
        try:
            executor=MigrationExecutor(connection)
            executor.migrate(latest)
            b_new=Building.objects.get(pk=b.pk)
            self.assertEqual((b_new.address,b_new.street,b_new.house),('Учебная улица, 265/2','Учебная улица','265/2'))
            self.assertEqual(Building.objects.get(pk=unclear.pk).street,'')
            self.assertEqual(Ticket.objects.get(pk=ticket.pk).description,'Keep me')
            self.assertEqual(b_new.organization_id,1)
            # Prior application image can still insert rows using the old model definition.
            written=old_building.objects.create(name='Old app',address='Legacy 2')
            self.assertEqual(Building.objects.get(pk=written.pk).organization_id,1)
            old_ticket=old.get_model('core','Ticket').objects.create(elevator=e,title='Old writer')
            self.assertEqual(Ticket.objects.get(pk=old_ticket.pk).repair,'')
            self.assertGreater(Organization.objects.create(name='Next organization').pk,1)
        finally:
            MigrationExecutor(connection).migrate(latest)

