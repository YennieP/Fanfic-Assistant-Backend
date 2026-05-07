"""
Data migration: Fragment.tags.emotion.shared  string → array

将所有 Fragment 记录中 tags.emotion.shared 为字符串的值迁移为单元素数组。
例：{"shared": "愤怒"} → {"shared": ["愤怒"]}

已是数组的记录不做任何改动（幂等）。
"""
from django.db import migrations


def migrate_emotion_shared_to_array(apps, schema_editor):
    Fragment = apps.get_model('examples', 'Fragment')
    updated = 0
    for fragment in Fragment.objects.exclude(tags=None):
        tags = fragment.tags
        if not isinstance(tags, dict):
            continue
        emotion = tags.get('emotion')
        if not isinstance(emotion, dict):
            continue
        shared = emotion.get('shared')
        # 已是数组则跳过，为字符串则转换
        if isinstance(shared, list):
            continue
        if isinstance(shared, str) and shared:
            emotion['shared'] = [shared]
            fragment.tags = tags
            fragment.save(update_fields=['tags'])
            updated += 1

    print(f'\n  Migrated {updated} fragments: emotion.shared str → list')


def reverse_emotion_shared_to_string(apps, schema_editor):
    """
    回滚：将数组还原为字符串（取第一个元素）。
    多值数组回滚时会丢失除第一个以外的值，属于有损回滚，仅供紧急回退使用。
    """
    Fragment = apps.get_model('examples', 'Fragment')
    for fragment in Fragment.objects.exclude(tags=None):
        tags = fragment.tags
        if not isinstance(tags, dict):
            continue
        emotion = tags.get('emotion')
        if not isinstance(emotion, dict):
            continue
        shared = emotion.get('shared')
        if isinstance(shared, list):
            emotion['shared'] = shared[0] if shared else ''
            fragment.tags = tags
            fragment.save(update_fields=['tags'])


class Migration(migrations.Migration):

    dependencies = [
        ('examples', '0003_fragment_end_line_fragment_fragment_type_and_more'),
    ]

    operations = [
        migrations.RunPython(
            migrate_emotion_shared_to_array,
            reverse_emotion_shared_to_string,
        ),
    ]