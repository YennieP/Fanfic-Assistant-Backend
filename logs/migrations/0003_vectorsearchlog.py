from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('logs', '0002_llmcalllog_generation_id_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='VectorSearchLog',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('request_id', models.UUIDField(db_index=True, null=True)),
                ('user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('generation_id', models.UUIDField(blank=True, db_index=True, null=True)),
                ('feature', models.CharField(max_length=64)),
                ('character_id', models.UUIDField(blank=True, null=True)),
                ('query_text', models.TextField()),
                ('top_k', models.IntegerField()),
                ('result_count', models.IntegerField()),
                ('top_similarity', models.FloatField(blank=True, null=True)),
                ('latency_ms', models.IntegerField()),
                ('style_injected', models.BooleanField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['created_at'], name='logs_vector_created_idx'),
                    models.Index(fields=['generation_id'], name='logs_vector_gen_id_idx'),
                    models.Index(fields=['feature', 'character_id'], name='logs_vector_feat_char_idx'),
                ],
            },
        ),
    ]