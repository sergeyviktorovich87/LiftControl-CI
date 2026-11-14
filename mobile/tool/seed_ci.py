"""Only ephemeral SQLite fixtures for the Android emulator workflow."""
import os
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'liftcontrol.settings')
import django
django.setup()
from django.conf import settings
assert settings.DEBUG and settings.DATABASES['default']['ENGINE'].endswith('sqlite3')
assert Path(settings.DATABASES['default']['NAME']).name.startswith('mobile-ci-')
from django.contrib.auth import get_user_model
from django.utils import timezone
from core.models import Organization, Technician, Membership, Building, Elevator, DutyShift, Ticket
company = Organization.objects.get(pk=1)
company.name = 'Организация Альфа • CI'
company.save()
other = Organization.objects.create(name='Организация Бета • CI')
user = get_user_model().objects.create_user('ci-mechanic', password='ci-test-password-only', first_name='Тестовый механик')
tech = Technician.objects.create(organization=company, full_name='Тестовый механик')
Membership.objects.create(user=user, organization=company, technician=tech, role='MECHANIC')
Membership.objects.create(user=user, organization=other, role='DISPATCHER')
building = Building.objects.create(organization=company, name='Тестовый дом', street='Тестовая улица', house='1', address='Тестовая улица, 1')
elevator = Elevator.objects.create(organization=company, building=building, inventory_number='CI-001', assigned_technician=tech)
DutyShift.objects.create(organization=company, technician=tech, start=timezone.now()-timedelta(hours=1), end=timezone.now()+timedelta(hours=23))
Ticket.objects.create(organization=company, elevator=elevator, assigned_to=tech, title='Проверка дверей • CI')
owner = get_user_model().objects.create_user('ci-admin', password='ci-admin-password-only', first_name='Тестовый администратор')
Membership.objects.create(user=owner, organization=company, role='ADMIN')
print('EPHEMERAL ANDROID FIXTURE READY')
