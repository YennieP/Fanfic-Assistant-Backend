"""factory_boy 工厂：造测试数据（User / BaseCard）。

只填必要字段，其余走 model 默认值（JSONField 默认 list/dict、CharField 默认 blank）。
"""
import factory
from django.contrib.auth.models import User

from characters.models import BaseCard


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        django_get_or_create = ('username',)

    username = factory.Sequence(lambda n: f'user{n}')
    email = factory.LazyAttribute(lambda o: f'{o.username}@test.local')


class BaseCardFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = BaseCard

    owner = factory.SubFactory(UserFactory)
    name = factory.Sequence(lambda n: f'角色{n}')
    fandom = 'TestFandom'
