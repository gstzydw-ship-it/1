"""提示词缓存数据模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class PromptCacheEntry:
    """单条提示词缓存。"""

    key: str
    value: object
    created_at: datetime
