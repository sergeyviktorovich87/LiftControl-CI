# LiftControl 0.12.0: linked chat records and photographs for spare-part requests.
from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0011_mediaasset_preview_size'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='chatmessage', name='linked_ticket',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='chat_references', to='core.ticket'),
        ),
        migrations.AddField(
            model_name='chatmessage', name='linked_part',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='chat_references', to='core.partrequest'),
        ),
        migrations.CreateModel(
            name='PartPhoto',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('asset', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='part_photos', to='core.mediaasset')),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='core.organization')),
                ('part', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='photos', to='core.partrequest')),
                ('deleted_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('uploaded_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('part', 'asset'), name='unique_part_photo_asset')],
            },
        ),
    ]
