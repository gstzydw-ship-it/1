"""提示词缓存服务占位。

当前阶段使用进程内字典模拟缓存。
后续可切换为 SQLite 持久化或数据库表驱动。
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.prompt_cache.models import PromptCacheEntry


class PromptCacheService:
    """管理提示词缓存结果。"""

    def __init__(self) -> None:
        self._cache: dict[str, PromptCacheEntry] = {}

    def get(self, key: str) -> object | None:
        """按键读取缓存。"""

        entry = self._cache.get(key)
        return entry.value if entry else None

    def set(self, key: str, value: object) -> None:
        """写入缓存。"""

        self._cache[key] = PromptCacheEntry(
            key=key,
            value=value,
            created_at=datetime.now(timezone.utc),
        )
