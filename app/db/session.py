"""数据库会话工厂。"""

from __future__ import annotations

from sqlalchemy.engine import Engine
from sqlmodel import Session


def create_session_factory(engine: Engine):
    """返回一个简单的 Session 工厂。"""

    def _factory() -> Session:
        return Session(engine)

    return _factory
