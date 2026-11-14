"""注册档案包：进程外持久化的服务实例注册版本快照。"""
from __future__ import annotations

from app.archive.db import (
    SOURCE_CONVERSION,
    SOURCE_RUNTIME,
    ArchiveConflict,
    ArchiveStore,
)
from app.config import settings

archive = ArchiveStore(settings.registry_db_path)

__all__ = [
    "archive",
    "ArchiveConflict",
    "ArchiveStore",
    "SOURCE_CONVERSION",
    "SOURCE_RUNTIME",
]
