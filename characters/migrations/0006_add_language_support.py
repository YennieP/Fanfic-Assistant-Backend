"""
Migration: 为 BaseCard 添加多语言支持字段

新增：
  - canonical_id：UUID，同一角色的不同语言版本共享此 ID
  - language：语言代码（'zh' / 'en'），默认 'zh'

数据迁移：
  现有所有 BaseCard 的 canonical_id 设置为其自身的 id，
  确保每条存量记录保持独立的 canonical 组。

使用方法：
  1. 将本文件重命名为 XXXX_add_language_support.py（XXXX = 下一个迁移编号）
  2. 将 dependencies 中的 '0XXX_your_latest_migration' 替换为你当前最新的迁移文件名
  3. python manage.py migrate
"""
import uuid
from django.db import migrations, models


def populate_canonical_id(apps, schema_editor):
    """为所有现有 BaseCard 设置 canonical_id = id（各自独立）。"""
    BaseCard = apps.get_model('characters', 'BaseCard')
    for card in BaseCard.objects.all():
        card.canonical_id = card.id
        card.save(update_fields=['canonical_id'])


class Migration(migrations.Migration):

    dependencies = [
        ('characters', '0005_labelhistory'),
    ]

    operations = [
        # Step 1: 先加字段（nullable，允许数据迁移时为空）
        migrations.AddField(
            model_name='basecard',
            name='canonical_id',
            field=models.UUIDField(null=True, blank=True, db_index=True),
        ),
        migrations.AddField(
            model_name='basecard',
            name='language',
            field=models.CharField(
                max_length=8,
                default='zh',
                choices=[('zh', '中文'), ('en', 'English')],
            ),
        ),

        # Step 2: 数据迁移 —— 为存量记录设置 canonical_id = id
        migrations.RunPython(populate_canonical_id, migrations.RunPython.noop),

        # Step 3: 设为非空（现在所有行都有值了）
        migrations.AlterField(
            model_name='basecard',
            name='canonical_id',
            field=models.UUIDField(default=uuid.uuid4, db_index=True),
        ),

        # Step 4: 加唯一约束（同一 owner 下同一角色同一语言只能有一个版本）
        migrations.AlterUniqueTogether(
            name='basecard',
            unique_together={('owner', 'canonical_id', 'language')},
        ),
    ]