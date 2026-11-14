# LiftControl 0.5 ticket workflow and push delivery infrastructure.
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0002_organization_building_house_building_street_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AddField(
            model_name='ticket', name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='created_tickets', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='ticket', name='accepted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='ticket', name='canceled_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='ticket', name='canceled_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='canceled_tickets', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='ticket', name='cancellation_reason',
            field=models.TextField(blank=True, default='', db_default='', max_length=1000),
        ),
        migrations.CreateModel(
            name='TicketEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('action', models.CharField(max_length=32)),
                ('before', models.CharField(blank=True, default='', max_length=20)),
                ('after', models.CharField(blank=True, default='', max_length=20)),
                ('reason', models.TextField(blank=True, default='', max_length=1000)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('actor', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL,
                                            to=settings.AUTH_USER_MODEL)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='core.organization')),
                ('ticket', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                             related_name='events', to='core.ticket')),
            ],
            options={'ordering': ['created_at', 'pk']},
        ),
        migrations.CreateModel(
            name='TicketComment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('body', models.TextField(max_length=4000)),
                ('kind', models.CharField(default='NOTE', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('author', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL,
                                             to=settings.AUTH_USER_MODEL)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='core.organization')),
                ('ticket', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                             related_name='comments', to='core.ticket')),
            ],
            options={'ordering': ['created_at', 'pk']},
        ),
        migrations.CreateModel(
            name='PushSubscription',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('endpoint', models.URLField(max_length=2048, unique=True)),
                ('p256dh', models.CharField(max_length=255)),
                ('auth', models.CharField(max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='core.organization')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                           related_name='push_subscriptions', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='PushDelivery',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(max_length=32)),
                ('state', models.CharField(default='PENDING', max_length=16)),
                ('attempts', models.PositiveSmallIntegerField(default=0)),
                ('next_attempt_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='core.organization')),
                ('subscription', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                                    related_name='deliveries', to='core.pushsubscription')),
                ('ticket', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='core.ticket')),
            ],
        ),
        migrations.AddIndex(
            model_name='pushdelivery', index=models.Index(fields=['state','next_attempt_at'],name='push_pending_idx')
        ),
    ]
