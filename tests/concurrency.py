"""模拟「并发修改」时使用的 updated_at。

乐观锁拿 updated_at 当版本号：接口收到的 updated_at 与库里不一致才返回 409。
测试里模拟并发修改时，如果直接写 timezone.now() 或依赖 auto_now 自然推进，
版本是否真的变化就取决于系统时钟的分辨率：

- Linux / macOS：time.time() 走 clock_gettime，纳秒级，连续两次调用必然不同。
- Windows：time.time() 走 GetSystemTimeAsFileTime，分辨率 15.625ms，
  连续两次 timezone.now() 经常返回同一个值。

于是「创建片段 → 立刻模拟并发修改」这两步会落在同一个时钟 tick 里，
updated_at 没变，接口认为版本没过期，断言 409 的用例在 Windows 上就会失败。

这里显式把时间戳推到已知值和当前时间之后，让「版本已经变了」这件事
与时钟精度无关，三个平台上行为一致。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from django.utils import timezone


#: 推进步长。只要远大于任何平台的时钟分辨率即可，取 1 秒便于阅读。
CONCURRENT_WRITE_STEP = timedelta(seconds=1)


def concurrent_updated_at(*known: datetime | None) -> datetime:
    """返回一个严格晚于 ``known`` 中所有时间戳、且晚于当前时间的 updated_at。

    用法::

        Fragment.objects.filter(id=fragment.id).update(
            text='并发修改后的正文',
            updated_at=concurrent_updated_at(fragment.updated_at),
        )

    传入调用方手上的旧时间戳即可；当前时间也会参与比较，所以接口在回调之前
    已经写过一次库的情况同样安全。
    """
    candidates = [timestamp for timestamp in known if timestamp is not None]
    candidates.append(timezone.now())
    return max(candidates) + CONCURRENT_WRITE_STEP
