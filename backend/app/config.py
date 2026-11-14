"""运行配置：端口、跨域、运行环境，以及注册档案（SQLite）相关开关。

读取入口 REGISTRY_READ_SOURCE：
- memory  ：沿用进程内内存清单（默认，回滚后的兜底入口）
- archive ：切换到 SQLite 注册档案，进程重启后注册时间、服务地址、健康状态仍可延续
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


@dataclass(frozen=True)
class Settings:
    app_name: str = "实时数据服务总线平台"
    env: str = field(default_factory=lambda: _env("APP_ENV", "local"))
    port: int = field(default_factory=lambda: int(_env("APP_PORT", "8000")))
    allowed_origins: list[str] = field(
        default_factory=lambda: [
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ]
    )
    page_size_default: int = 20
    page_size_max: int = 200
    # 注册档案 SQLite 文件位置，可用 REGISTRY_DB_PATH 覆盖。
    registry_db_path: Path = field(
        default_factory=lambda: Path(_env("REGISTRY_DB_PATH", str(BACKEND_DIR / "data" / "registry_archive.sqlite3")))
    )
    # 读取入口：memory | archive。
    registry_read_source: str = field(
        default_factory=lambda: _env("REGISTRY_READ_SOURCE", "memory").lower()
    )
    # 演练专用：转换成功提交多少条后注入中断，0 表示关闭。
    registry_fail_after: int = field(
        default_factory=lambda: int(_env("REGISTRY_FAIL_AFTER", "0"))
    )


settings = Settings()
