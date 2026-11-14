"""注册档案转换编排：把内存清单里的历史条目逐条转成注册版本快照。

规则：
- 实例编号是幂等键，已落档的编号再次转换时直接复用首版档案，不产生新版本；
- 每条一次独立提交，中断后重跑只处理尚未落档的编号（断点续转）；
- 校验失败不中断整体流程，编号与原因写入 conversion_failure 失败清单；
- 原始内存清单全程只读，回滚读取入口后仍可看到原内存清单。
"""
from __future__ import annotations

from typing import Any

from app.archive import SOURCE_CONVERSION, ArchiveConflict, archive
from app.config import settings
from app.store import store

MODULE = "registry"
FRAMEWORK_KEYS = {"id", "status", "pending", "abnormal"}
REQUIRED_CODE_FIELD = "实例编号"


class ConversionInterrupted(RuntimeError):
    """演练用：成功提交指定条数后主动中断，模拟转换半途失败。"""

    def __init__(self, converted: list[str], next_code: str, fail_after: int) -> None:
        super().__init__(
            f"转换演练在成功提交 {fail_after} 条后中断，下一条待转换编号：{next_code}"
        )
        self.converted = converted
        self.next_code = next_code
        self.fail_after = fail_after


def _entry_payload(row: dict[str, Any]) -> dict[str, Any]:
    """剥离框架字段，业务字段（含实例标签原值）整体保留。"""
    return {key: value for key, value in row.items() if key not in FRAMEWORK_KEYS}


def convert_memory_registry(*, fail_after: int | None = None) -> dict[str, Any]:
    """执行一次转换；fail_after 为演练注入点，表示本次成功提交 N 条后中断。"""
    if fail_after is None:
        fail_after = settings.registry_fail_after

    memory_rows = store.rows(MODULE)
    known = archive.known_instance_codes()
    pending_rows = [row for row in memory_rows if row.get(REQUIRED_CODE_FIELD) not in known]

    if not pending_rows:
        return {
            "reused": True,
            "message": "首版注册档案已存在，本次直接复用，未生成新版本",
            "archive_version": archive.archive_version(),
            "converted": [],
            "failures": archive.list_failures(),
            "remaining": [],
        }

    converted: list[str] = []
    remaining = [str(row.get(REQUIRED_CODE_FIELD) or f"内存id={row.get('id')}") for row in pending_rows]

    for row in pending_rows:
        code = str(row.get(REQUIRED_CODE_FIELD) or "").strip()

        if fail_after and len(converted) >= fail_after:
            reason = f"转换演练中断：成功提交 {fail_after} 条后进程停止，该编号尚未转换"
            if code:
                archive.add_failure(code, reason)
            raise ConversionInterrupted(converted, code or f"内存id={row.get('id')}", fail_after)

        if not code:
            reason = "缺少必填字段：实例编号"
            archive.add_failure(f"内存id={row.get('id')}", reason)
            continue

        try:
            archive.snapshot(
                instance_code=code,
                payload=_entry_payload(row),
                source=SOURCE_CONVERSION,
                status=str(row.get("status") or "草稿"),
                pending=bool(row.get("pending")),
                abnormal=bool(row.get("abnormal")),
            )
        except ArchiveConflict:
            # 并发场景下已被先成功提交的版本占用：裁决给先提交者，跳过本编号。
            continue
        except Exception as exc:  # 数据落档失败：保留编号与原因，继续后续条目
            archive.add_failure(code, f"档案提交失败：{exc}")
            continue

        converted.append(code)
        remaining.remove(code)

    return {
        "reused": False,
        "message": f"本次转换 {len(converted)} 条，档案当前版本 v{archive.archive_version()}",
        "archive_version": archive.archive_version(),
        "converted": converted,
        "failures": archive.list_failures(),
        "remaining": remaining,
    }


def archive_status() -> dict[str, Any]:
    """档案总览：版本、已落档编号、失败清单与读取入口。"""
    snapshots = archive.list_snapshots()
    return {
        "read_source": settings.registry_read_source,
        "db_path": str(settings.registry_db_path),
        "ready": archive.is_ready(),
        "archive_version": archive.archive_version(),
        "snapshot_count": len(snapshots),
        "instance_codes": [item["instance_code"] for item in snapshots],
        "failures": archive.list_failures(),
    }
