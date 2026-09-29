import django.db.models.deletion
import django_mongodb_backend.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Robot',
            fields=[
                ('id', django_mongodb_backend.fields.ObjectIdAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('device_id', models.CharField(max_length=64, unique=True)),
                ('nickname', models.CharField(blank=True, default='', max_length=100)),
                ('pairing_status', models.CharField(choices=[('unpaired', 'Unpaired'), ('paired', 'Paired')], default='unpaired', max_length=16)),
                ('pairing_code_hash', models.CharField(blank=True, default='', max_length=64)),
                ('pairing_code_expires_at', models.DateTimeField(blank=True, null=True)),
                ('paired_at', models.DateTimeField(blank=True, null=True)),
                ('firmware_version', models.CharField(blank=True, default='', max_length=32)),
                ('last_seen', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('parent', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='robots', to='accounts.parent')),
            ],
        ),
        migrations.CreateModel(
            name='DeviceCredential',
            fields=[
                ('id', django_mongodb_backend.fields.ObjectIdAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('public_key', models.TextField()),
                ('token_hash', models.CharField(blank=True, default='', max_length=128)),
                ('token_expires_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('robot', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='credential', to='devices.robot')),
            ],
        ),
        migrations.CreateModel(
            name='RobotEvent',
            fields=[
                ('id', django_mongodb_backend.fields.ObjectIdAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event_type', models.CharField(max_length=64)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('timestamp', models.DateTimeField(auto_now_add=True)),
                ('robot', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='events', to='devices.robot')),
            ],
        ),
        migrations.CreateModel(
            name='RobotState',
            fields=[
                ('id', django_mongodb_backend.fields.ObjectIdAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('state', models.CharField(blank=True, default='', max_length=32)),
                ('battery_percent', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('online', models.BooleanField(default=False)),
                ('rssi', models.IntegerField(blank=True, null=True)),
                ('last_updated', models.DateTimeField(auto_now=True)),
                ('robot', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='state', to='devices.robot')),
            ],
        ),
        migrations.CreateModel(
            name='SensorData',
            fields=[
                ('id', django_mongodb_backend.fields.ObjectIdAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sensor_type', models.CharField(max_length=32)),
                ('value', models.FloatField()),
                ('timestamp', models.DateTimeField(auto_now_add=True)),
                ('robot', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sensor_data', to='devices.robot')),
            ],
        ),
    ]
