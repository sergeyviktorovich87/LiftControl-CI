"""Allow isolated commissioning-only user memberships."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0014_commissioning')]

    operations = [
        migrations.AlterField(
            model_name='membership', name='role',
            field=models.CharField(max_length=20, default='MECHANIC', choices=[
                ('ADMIN', 'Администратор'), ('DIRECTOR', 'Директор'),
                ('DISPATCHER', 'Диспетчер'), ('MECHANIC', 'Механик'),
                ('ADJUSTER', 'Приглашённый наладчик'),
            ]),
        ),
    ]
