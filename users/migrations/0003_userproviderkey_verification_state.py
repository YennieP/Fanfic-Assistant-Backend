from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0002_add_user_provider_key'),
    ]

    operations = [
        migrations.AddField(
            model_name='userproviderkey',
            name='verification_checked_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='userproviderkey',
            name='verification_model',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='userproviderkey',
            name='verification_resolved_model',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='userproviderkey',
            name='verification_status',
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
    ]
