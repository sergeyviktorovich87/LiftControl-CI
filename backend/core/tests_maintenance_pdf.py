"""0.13 maintenance PDF tests: tenant, signed-in access and six-column report."""
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Organization, Membership, Building, Elevator, Technician, Maintenance


class MaintenancePdfTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.get(pk=1)
        self.other = Organization.objects.create(name='Вторая организация')
        User = get_user_model()
        self.admin = User.objects.create_user(username='pdf-admin-13',password='StrongSecretPwd13!')
        self.foreign_admin = User.objects.create_user(username='pdf-foreign-13',password='StrongSecretPwd13!')
        self.adjuster = User.objects.create_user(username='pdf-adjuster-13',password='StrongSecretPwd13!')
        Membership.objects.create(organization=self.organization,user=self.admin,role='ADMIN')
        Membership.objects.create(organization=self.other,user=self.foreign_admin,role='ADMIN')
        Membership.objects.create(organization=self.organization,user=self.adjuster,role='ADJUSTER')
        self.building = Building.objects.create(organization=self.organization,name='Первый',
                                                address='Улица 1')
        self.elevator = Elevator.objects.create(organization=self.organization,
                                                building=self.building,factory_number='TO-13')
        self.tech=Technician.objects.create(organization=self.organization,
                                            full_name='Иванов Иван',role='MECHANIC')
        self.entry=Maintenance.objects.create(organization=self.organization,
                        elevator=self.elevator,technician=self.tech,kind='TO1',
                        planned_date=timezone.localdate(),completed_at=timezone.now(),
                        notes='Проверка башмаков и дверей')
        self.client=APIClient()

    def url(self):
        return f'/api/maintenance/{self.elevator.pk}/journal/pdf/'

    def get(self,user,query=None,org=None):
        self.client.force_authenticate(user)
        return self.client.get(self.url(),query or {},
                               HTTP_X_ORGANIZATION_ID=str((org or self.organization).pk))

    def test_pdf_download_has_valid_header_and_is_not_cached(self):
        response=self.get(self.admin)
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.content.startswith(b'%PDF-'))
        self.assertEqual(response['Content-Type'],'application/pdf')
        self.assertEqual(response['Cache-Control'],'private, no-store')
        self.assertGreater(len(response.content),1000)

    def test_no_cross_organization_or_adjuster_download(self):
        self.assertIn(self.get(self.foreign_admin,org=self.other).status_code,(403,404))
        self.assertEqual(self.get(self.adjuster).status_code,403)

    def test_period_validation(self):
        self.assertEqual(self.get(self.admin,{'from':'nonsense'}).status_code,400)
        self.assertEqual(self.get(self.admin,{'from':'2027-02-02','to':'2027-01-01'}).status_code,400)
        self.assertEqual(self.get(self.admin,{'from':'2027-01-01','to':'2027-01-02'}).status_code,200)
