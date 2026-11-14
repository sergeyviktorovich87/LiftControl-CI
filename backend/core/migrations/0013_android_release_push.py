"""Durable Android release announcements, one FCM delivery per device and build."""
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [('core', '0012_part_photos_chat_links')]

    operations = [
        migrations.CreateModel(
            name='AndroidUpdateAnnouncement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version_code', models.PositiveIntegerField(unique=True)),
                ('version', models.CharField(max_length=40)),
                ('sha256', models.CharField(max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name='AndroidUpdatePushDelivery',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('state', models.CharField(db_index=True, default='PENDING', max_length=16)),
                ('attempts', models.PositiveSmallIntegerField(default=0)),
                ('next_attempt_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('announcement', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='core.androidupdateannouncement')),
                ('device', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='core.nativepushdevice')),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('announcement', 'device'), name='android_release_one_push_per_device')],
                'indexes': [models.Index(fields=['state', 'next_attempt_at'], name='android_push_pending_idx')],
            },
        ),
    ]
