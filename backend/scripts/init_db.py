#!/usr/bin/env python3
"""发布流程第二步：数据库结构建立。

幂等执行：表已存在时跳过；可通过 REGISTRY_DB_PATH 指定档案库位置。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.archive import archive
from app.config import settings


def main() -> int:
    print(f"[schema] 档案库路径：{settings.registry_db_path}")
    archive.init_db()
    print("[schema] 注册表 archive_meta / registry_snapshot / conversion_failure 已就绪")
    print(f"[schema] 当前档案版本：v{archive.archive_version()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
