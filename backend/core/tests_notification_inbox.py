"""Tests for persistent notification feed, tenant isolation and read semantics."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from .models import (Organization, Membership, NotificationInbox,
                     CommissioningProject, CommissioningLift, CommissioningAssignment)


class NotificationInboxTests(TestCase):
    def setUp(self):
        self.org=Organization.objects.get(pk=1)
        self.foreign=Organization.objects.create(name='Вторая')
        User=get_user_model()
        self.user=User.objects.create_user(username='notice-a13',password='VeryStrongPass13!')
        self.other=User.objects.create_user(username='notice-b13',password='VeryStrongPass13!')
        self.adjuster=User.objects.create_user(username='notice-c13',password='VeryStrongPass13!')
        Membership.objects.create(organization=self.org,user=self.user,role='ADMIN')
        Membership.objects.create(organization=self.org,user=self.other,role='ADMIN')
        Membership.objects.create(organization=self.org,user=self.adjuster,role='ADJUSTER')
        self.client=APIClient()

    def request(self,user,method,path,body=None):
        self.client.force_authenticate(user)
        kwargs={'HTTP_X_ORGANIZATION_ID':str(self.org.pk)}
        if method=='GET':
            return self.client.get('/api/'+path,**kwargs)
        return self.client.post('/api/'+path,body or {},format='json',**kwargs)

    def test_read_own_and_filter_unread(self):
        own=NotificationInbox.objects.create(organization=self.org,user=self.user,
            event_type='ticket',event_id=100,kind='created',title='Заявка №100',
            target_type='ticket',target_id=100)
        NotificationInbox.objects.create(organization=self.org,user=self.other,
            event_type='ticket',event_id=100,kind='created',title='Заявка №100',
            target_type='ticket',target_id=100)
        rows=self.request(self.user,'GET','notifications/')
        self.assertEqual(rows.status_code,200)
        self.assertEqual(rows.data['unread'],1)
        self.assertEqual(len(rows.data['results']),1)
        forbidden=self.request(self.other,'POST',f'notifications/{own.pk}/read/')
        self.assertEqual(forbidden.status_code,404)
        seen=self.request(self.user,'POST',f'notifications/{own.pk}/read/')
        self.assertEqual(seen.status_code,200)
        self.assertEqual(self.request(self.user,'GET','notifications/?unread=1').data['unread'],0)
        self.assertEqual(self.request(self.user,'GET','notifications/?unread=1').data['results'],[])
        self.assertEqual(self.request(self.user,'POST',f'notifications/{own.pk}/read/').status_code,200)

    def test_role_change_hides_prior_regular_notifications_from_adjuster(self):
        old=NotificationInbox.objects.create(organization=self.org,user=self.adjuster,
            event_type='ticket',event_id=800,kind='created',
            title='Заявка №800',target_type='ticket',target_id=800)
        shown=NotificationInbox.objects.create(organization=self.org,user=self.adjuster,
            event_type='commissioning_message',event_id=801,kind='message',
            title='Объект наладки',target_type='commissioning',target_id=801)
        # Restricted roles cannot read historic operational notifications.
        response=self.request(self.adjuster,'GET','notifications/')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data['results'],[])
        self.assertEqual(self.request(self.adjuster,'POST',f'notifications/{old.pk}/read/').status_code,404)
        self.assertEqual(self.request(self.adjuster,'POST',f'notifications/{shown.pk}/read/').status_code,404)

    def test_foreign_org_not_visible_even_to_same_user(self):
        NotificationInbox.objects.create(organization=self.foreign,user=self.user,
            event_type='ticket',event_id=200,kind='created',
            title='Чужая',target_type='ticket',target_id=200)
        self.assertEqual(self.request(self.user,'GET','notifications/').data['results'],[])

    def test_commissioning_chat_scoped_to_assigned_adjuster_and_managers(self):
        from .notification_inbox import enqueue_commissioning_message
        from .models import CommissioningMessage
        project=CommissioningProject.objects.create(
            organization=self.org,name='Объект',address='Тестовая 1',created_by=self.user)
        lift=CommissioningLift.objects.create(project=project,label='№1')
        CommissioningAssignment.objects.create(lift=lift,user=self.adjuster)
        message=CommissioningMessage.objects.create(project=project,author=self.user,text='Открыл двери')
        enqueue_commissioning_message(message)
        # The sender does not need a notification about their own message.
        self.assertEqual(self.request(self.adjuster,'GET','notifications/').data['unread'],1)
        self.assertEqual(self.request(self.user,'GET','notifications/').data['unread'],0)
        self.assertEqual(self.request(self.other,'GET','notifications/').data['unread'],1)
        seen=self.request(self.adjuster,'GET','notifications/').data['results'][0]
        self.assertEqual(seen['target_type'],'commissioning')
        self.assertNotIn('Открыл двери',str(seen))
        # Removing the assignment revokes historical inbox access as well.
        CommissioningAssignment.objects.filter(lift=lift,user=self.adjuster).update(active=False)
        self.assertEqual(self.request(self.adjuster,'GET','notifications/').data['results'],[])
        self.assertEqual(self.request(self.adjuster,'POST',f'notifications/{seen["id"]}/read/').status_code,404)
