"""Durable, per-organization and per-user notification inbox."""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('core','0015_adjuster_role'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name='NotificationInbox',fields=[
            ('id', models.BigAutoField(primary_key=True, serialize=False, auto_created=True, verbose_name='ID')),
            ('event_type', models.CharField(max_length=24)),
            ('event_id', models.BigIntegerField()),
            ('kind', models.CharField(max_length=24)),
            ('title', models.CharField(max_length=200)),
            ('target_type', models.CharField(max_length=24)),
            ('target_id', models.BigIntegerField()),
            ('created_at',models.DateTimeField(auto_now_add=True)),
            ('read_at',models.DateTimeField(blank=True,null=True)),
            ('organization', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,to='core.organization')),
            ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='lift_notifications',to=settings.AUTH_USER_MODEL)),
        ], options={'ordering':['-created_at','-id']}),
        migrations.AddConstraint('notificationinbox', models.UniqueConstraint(
            fields=['user','event_type','event_id'],name='notification_user_event_once')),
        migrations.AddIndex('notificationinbox',models.Index(
            fields=['user','organization','read_at','created_at'],name='notification_inbox_idx')),
    ]
