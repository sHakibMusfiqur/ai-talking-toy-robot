import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('conversations', '0001_initial'),
        ('devices', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='conversation',
            name='robot',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='conversations', to='devices.robot'),
        ),
        migrations.AddField(
            model_name='message',
            name='conversation',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='messages', to='conversations.conversation'),
        ),
        migrations.AddField(
            model_name='aiinteraction',
            name='message',
            field=models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='ai_interaction', to='conversations.message'),
        ),
    ]
