"""0.13: align organization invite role choices with restricted ADJUSTER membership."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0016_notification_inbox')]

    operations = [
        migrations.AlterField(
            model_name='organizationinvite',
            name='role',
            field=models.CharField(max_length=20, choices=[
                ('ADMIN', 'Администратор'),
                ('DIRECTOR', 'Директор'),
                ('DISPATCHER', 'Диспетчер'),
                ('MECHANIC', 'Механик'),
                ('ADJUSTER', 'Приглашённый наладчик'),
            ]),
        ),
    ]
