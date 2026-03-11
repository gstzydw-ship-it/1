"""数据库 engine 构建工具。"""

from __future__ import annotations

from pathlib import Path

from sqlmodel import create_engine


def build_engine(database_url: str):
    """为给定数据库 URL 创建 SQLAlchemy engine。"""

    if database_url.startswith("sqlite:///"):
        db_path = Path(database_url.removeprefix("sqlite:///"))
        db_path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(database_url, echo=False)
