"""Separate commissioning objects, progress history and private expenses (0.13)."""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import core.models


def user(name):
    return models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.PROTECT, related_name=name)


class Migration(migrations.Migration):
    dependencies = [('core', '0013_android_release_push'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(name='CommissioningProject', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('name', models.CharField(max_length=200)),
            ('address', models.CharField(max_length=300)),
            ('contract_amount', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
            ('adjuster_fee', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('archived_at', models.DateTimeField(blank=True, null=True)),
            ('archive_reason', models.CharField(blank=True, max_length=1000)),
            ('organization', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='commissioning_projects', to='core.organization')),
            ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
        ], options={'ordering': ['-created_at', '-id']}),
        migrations.CreateModel(name='CommissioningLift', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('label', models.CharField(max_length=150)),
            ('factory_number', models.CharField(blank=True, max_length=100)),
            ('progress', models.PositiveSmallIntegerField(default=0)),
            ('revision', models.PositiveIntegerField(default=0)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('elevator', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='commissioning_lifts', to='core.elevator')),
            ('project', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='lifts', to='core.commissioningproject')),
        ], options={'ordering': ['id']}),
        migrations.CreateModel(name='CommissioningAssignment', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('active', models.BooleanField(default=True)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('lift', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='assignments', to='core.commissioninglift')),
            ('user', user('+')),
        ]),
        migrations.CreateModel(name='CommissioningProgress', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('before', models.PositiveSmallIntegerField()),
            ('after', models.PositiveSmallIntegerField()),
            ('description', models.CharField(max_length=4000)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('actor', user('+')),
            ('lift', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='history', to='core.commissioninglift')),
        ], options={'ordering': ['id']}),
        migrations.CreateModel(name='CommissioningExpense', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('amount', models.DecimalField(decimal_places=2, max_digits=12)),
            ('description', models.CharField(max_length=1000)),
            ('receipt', models.FileField(blank=True, max_length=250, upload_to=core.models.private_path)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('reimbursed_at', models.DateTimeField(blank=True, null=True)),
            ('author', user('+')),
            ('organization', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='core.organization')),
            ('project', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='expenses', to='core.commissioningproject')),
            ('lift', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='core.commissioninglift')),
        ], options={'ordering': ['-created_at', '-id']}),
        migrations.CreateModel(name='CommissioningNeed', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('category', models.CharField(default='OTHER', max_length=30)),
            ('description', models.CharField(max_length=2000)),
            ('blocking', models.BooleanField(default=False)),
            ('resolved_at', models.DateTimeField(blank=True, null=True)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('author', user('+')),
            ('lift', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='core.commissioninglift')),
            ('project', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='needs', to='core.commissioningproject')),
        ], options={'ordering': ['-created_at']}),
        migrations.CreateModel(name='CommissioningExtraWork', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('description', models.CharField(max_length=2000)),
            ('price', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
            ('status', models.CharField(default='PENDING', max_length=16)),
            ('reviewed_at', models.DateTimeField(blank=True, null=True)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('author', user('+')),
            ('reviewed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ('lift', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='core.commissioninglift')),
            ('project', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='extras', to='core.commissioningproject')),
        ], options={'ordering': ['-created_at']}),
        migrations.CreateModel(name='CommissioningMessage', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('text', models.CharField(max_length=4000)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('author', user('+')),
            ('project', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='messages', to='core.commissioningproject')),
        ], options={'ordering': ['id']}),
        migrations.CreateModel(name='CommissioningLiftRequest', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('label', models.CharField(max_length=150)),
            ('factory_number', models.CharField(blank=True, max_length=100)),
            ('status', models.CharField(default='PENDING', max_length=16)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('requested_by', user('+')),
            ('reviewed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ('project', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='lift_requests', to='core.commissioningproject')),
        ], options={'ordering': ['-created_at']}),
        migrations.AddConstraint('commissioninglift', models.UniqueConstraint(fields=['project', 'label'], name='unique_commissioning_lift_label')),
        migrations.AddConstraint('commissioningassignment', models.UniqueConstraint(fields=['lift', 'user'], name='unique_commissioning_assignee')),
    ]
